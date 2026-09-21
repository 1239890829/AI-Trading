"""Replayable evidence and outcome labels for the stock-opportunity funnel.

Snapshots are append-only: later closes are written to a separate outcome table.
The archived evidence is sufficient to replay each deterministic stage without
calling a market-data provider or consulting today's mutable configuration.

**两种收益口径，勿混用（2026-09-16 `RSH-026` 第二批）**

- `return_pct` = **毛市场结果**：决策时点 reference → 指定 horizon 的收盘，**不含成本**。
  `d0_close` 受 A 股 T+1 约束不可实现；`d1/d3/d5_close` 虽满足时间约束，仍只是 reference 轨，
  不是实际 shadow fill 的交易净收益。
- `net_return_pct` 是历史列名；现在统一表示**成本调整 reference 代理**：
  决策时点 reference → horizon 目标交易日收盘，再扣双边费用。只有决策时点可成交
  (`fill_state == "ok"`) 才给值；不可成交/非动作分母一律 `None`，缺价不造 0。
- 成本费率**不在此另立**：取自 `app.paper.engine.calc_fee`（与 `paper/reconcile.py` 同源）；
  涨停幅度取自 `app.market.price_rules.limit_pct`（全仓单一实现）。
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Iterable

from sqlalchemy import and_, exists, func, or_, select

from app.core.bjtime import beijing_now, to_beijing_naive
from app.core.db import get_session_factory, utcnow
from app.market import price_rules
from app.models.opportunity_learning import (
    OpportunityDecisionSnapshot,
    OpportunityDecisionRun,
    OpportunityOutcomeLabel,
    OpportunityOutcomeRevision,
)
from app.paper.engine import calc_fee
from app.picks.kb_routing import snapshot_citations
from app.services.theme_service import parse_hhmmss

STRATEGY_VERSION = "stock-opportunity-funnel-v2"
FEATURE_VERSION = "pit-evidence-v2"
OUTCOME_HORIZON = "d0_close"
OUTCOME_HORIZONS = {
    "d0_close": 0,
    "d1_close": 1,
    "d3_close": 3,
    "d5_close": 5,
}
FUTURE_OUTCOME_HORIZONS = tuple(h for h in OUTCOME_HORIZONS if h != OUTCOME_HORIZON)
PATH_VERSION = "d0-path-v1.tencent1m.zt-zb"
CROSS_DAY_PATH_VERSION = "xday-v1.d0m1+qfq1d.raw-anchor"
PRICE_BASIS_VERSION = "qfq-ref-v1.raw-anchor"
STAGES = ("candidate", "hard_gate", "rank", "notification")

#: 成本口径版本。**变更费率假设或可成交判据时必须递增**——结果标签是 append-only 的，
#: 没有版本号就无法区分「策略变好」与「口径变松」。
COST_MODEL_VERSION = "cost-v1.notional-100k"
OUTCOME_REVISION_VERSION = f"close-v1.{PRICE_BASIS_VERSION}.{COST_MODEL_VERSION}"

#: IMP-006：页面/提醒/模拟执行共享的决策事实契约版本。只改结构语义时递增；
#: 策略阈值变化仍由 STRATEGY_VERSION / FEATURE_VERSION 单独版本化。
EXECUTION_CONTRACT_VERSION = "execution-facts-v1"

#: 净收益按**每笔 10 万元名义本金**折算整手股数（口径假设）。
#: 取 10 万是为了让佣金脱离 `commission_min`(5 元) 的主导区、反映真实费率结构；
#: 贵价股（>1000 元）按保底 1 手计 ⇒ 名义本金会高于 10 万，属**已知近似**。
#: 故 `net_return_pct` 只用于**同口径横向比较**，不得当作真实账户绝对盈亏。
COST_NOTIONAL_CNY = 100_000.0

#: A 股整手（100 股），买卖委托必须为其整数倍。
COST_LOT = 100

#: 距涨停不足此幅度（**百分点**）即视为封板买不到。与 `sentiment/engine.py` 的
#: 涨停容差同源（该处作 `pct >= limit_pct(...) - 0.15`），避免同一现象两套阈值。
FILL_SEAL_GAP_PCT = 0.15


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _finite_number(value: Any) -> bool:
    """Only finite numeric observations are eligible for return metrics."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _positive_finite(value: Any) -> bool:
    return _finite_number(value) and float(value) > 0


def _has_current_cost_model(outcome: Any) -> bool:
    """One identity rule for both metric admission and audit accounting."""
    structured = str(getattr(outcome, "cost_model_version", "") or "")
    if structured:
        return structured == COST_MODEL_VERSION
    return COST_MODEL_VERSION in str(getattr(outcome, "reason", "") or "")


def _load_json(raw: str | None, default: Any) -> Any:
    """归档 JSON 列 → 对象；坏值/空值回落到 `default`（**读取侧不因单行坏值整批失败**）。"""
    try:
        value = json.loads(raw or "")
    except Exception:  # noqa: BLE001
        return default
    return value if isinstance(value, type(default)) else default


def _kb_ref_state(raw: str | None) -> str:
    """快照的 KB 引用状态（蓝图 §5）。

    `legacy` = 该列引入（迁移 `c5d2f8a3b7e1`）之前的行 —— **不猜**成 `not_consulted`：
    「历史上确实没记」与「记了、就是没引」是两件事，混起来会让迁移前的老数据
    被读成新口径的证据。`unparsed` = 列坏值（应报警，不该静默并档）。
    """
    payload = _load_json(raw, {})
    if not payload:
        return "legacy"
    state = payload.get("state")
    return str(state) if state else "unparsed"


def _hash(value: Any, length: int = 32) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()[:length]


def build_execution_contract(
    *, trade_date: str, symbol: str, item: dict, executable_snapshot: dict | None,
    gate_decision: str, gate_reason: str | None, pick_generated_at: str | None,
) -> dict:
    """统一一只股票的一版执行事实（IMP-006）。

    reference_entry 是组合生成/首见时的反事实参考，只回答“当时若按参考价计会怎样”；
    executable_snapshot 是动作时复核行情，只回答“这一拍拿什么事实做判定”。
    二者都不是成交；真实成交只来自 paper order 的 filled_price。

    decision_id 在同一交易日/场景/股票内稳定；decision_version 只由会改变动作判定
    的事实生成，不含派发成功/去重结果，因此通知状态不会伪造一个新交易判断版本。
    """
    ref_price = item.get("price")
    if not _positive_finite(ref_price):
        ref_price = None
    reference_entry = {
        "price": float(ref_price) if ref_price is not None else None,
        "as_of": pick_generated_at,
        "source": "daily_pick_set",
        "semantics": "reference_only_not_fill",
    }
    snap = dict(executable_snapshot or {})
    snap.setdefault("state", "unknown")
    snap.setdefault("price", None)
    snap.setdefault("change_pct", None)
    snap.setdefault("source", None)
    snap["semantics"] = "action_time_quote_not_fill"

    decision_id = "OD-" + _hash({
        "scenario": "buy_point", "trade_date": trade_date, "symbol": symbol,
    }, 24)
    # decision_version 只吃会改变动作结论的物质事实。单纯刷新 received_at/ticktime/as_of
    # 仍完整归档在 executable_snapshot，但不能让“价格没动、判据没动”的轮询每分钟造新版本。
    material_snapshot = {
        k: snap.get(k) for k in ("state", "price", "change_pct", "prev_close", "source")
    }
    material = {
        "contract_version": EXECUTION_CONTRACT_VERSION,
        "decision_id": decision_id,
        "strategy_version": STRATEGY_VERSION,
        "feature_version": FEATURE_VERSION,
        "reference_entry": reference_entry,
        "executable_snapshot": material_snapshot,
        "gate_decision": gate_decision,
        "gate_reason": gate_reason,
        "gate_inputs": {
            "confidence_tier": (item.get("confidence") or {}).get("tier"),
            "vetoes": item.get("vetoes") or [],
            "follow_state": item.get("follow_state"),
            "observation_only": bool(item.get("observation_only")),
            "buy_range": item.get("buy_range"),
        },
    }
    return {
        **material,
        # 版本哈希只吃会改变判断的物质事实；对外证据保留完整 freshness/checked_at/语义。
        "executable_snapshot": snap,
        "decision_version": "ODV-" + _hash(material, 32),
    }


def _data_state(payload: dict) -> str:
    if payload.get("linkage_note"):
        return "degraded"
    stats = payload.get("linkage_stats") or {}
    # Quote fields being present is not enough: a stale/degraded snapshot is known-bad
    # point-in-time input and must never masquerade as ``ready`` evidence.
    if stats.get("snapshot_state") != "ready" or not stats.get("snapshot_as_of"):
        return "degraded"
    if int(stats.get("missing_quote") or 0) > 0 or payload.get("hot_available") is False:
        return "degraded"
    return "ready"


def _round_lots(price: float) -> int:
    """按名义本金折算整手股数；贵价股保底 1 手（见 `COST_NOTIONAL_CNY`）。"""
    if not _positive_finite(price):
        return COST_LOT
    lots = int(COST_NOTIONAL_CNY / float(price) / COST_LOT)
    return max(1, lots) * COST_LOT


def round_trip_net_pct(reference: float, exit_price: float) -> tuple[float, float, int]:
    """一次假设完整买卖的净收益率 / 成本率 / 折算股数（算术工具）。

    本函数本身不判断 horizon 是否可交易；调用 D0 时只能作为成本调整代理。
    净收益取**定义式**：`(卖出净得 − 买入实付) / 买入实付`，
    其中买入实付含费用、卖出净得已扣费用 ⇒ 它**恒严格小于**同口径毛收益，
    差额即 `calc_fee` 口径的双边成本。费率与最低佣金不在此另立。
    """
    if not _positive_finite(reference) or not _positive_finite(exit_price):
        raise ValueError("reference/exit_price 必须是有限正数")
    qty = _round_lots(reference)
    buy_fee = calc_fee("buy", float(reference), qty)
    sell_fee = calc_fee("sell", float(exit_price), qty)
    paid = float(reference) * qty + buy_fee
    if paid <= 0:
        return 0.0, 0.0, qty
    proceeds = float(exit_price) * qty - sell_fee
    return (
        round((proceeds - paid) / paid * 100, 2),
        round((buy_fee + sell_fee) / paid * 100, 4),
        qty,
    )


def assess_fill_state(symbol: str, change_pct: float | None) -> tuple[str, str]:
    """决策时点能否按归档价买入 → `(fill_state, basis)`。

    - `ok`：距涨停尚有余量，按现价可买 ⇒ 净收益可计
    - `sealed`：已贴涨停（容差内）⇒ 封板买不到 ⇒ 净收益记 `None`
    - `no_quote`：决策时点无涨幅事实 ⇒ 可成交性未知 ⇒ 净收益记 `None`

    判据**只用决策时点已归档的事实**（涨幅），不查实时行情、不读当前配置
    —— 与快照的 point-in-time 契约一致，否则重放会因行情变化而漂移。
    ⚠️ `price_rules.limit_pct` 只服务**个股** symbol；本函数不得接收板块/大盘指数。
    """
    if not _finite_number(change_pct):
        return "no_quote", "决策时点涨幅缺失或非有限，可成交性未知"
    pct = float(change_pct)
    limit = price_rules.limit_pct(symbol)
    if pct >= limit - FILL_SEAL_GAP_PCT:
        return "sealed", (
            f"决策时点涨幅 {pct:.2f}% 已贴 {limit:.0f}% 涨停（容差 {FILL_SEAL_GAP_PCT}）"
            "⇒ 封板买不到，净收益不可计"
        )
    return "ok", f"决策时点涨幅 {pct:.2f}%，距 {limit:.0f}% 涨停有余量"


def _archived_change_pct(evidence: dict) -> float | None:
    """从归档证据里取决策时点涨幅（各阶段键位不同，事实同源）。"""
    for key in ("change_pct", "chg"):
        value = evidence.get(key)
        if _finite_number(value):
            return float(value)
    facts = evidence.get("facts") or {}
    value = facts.get("change_pct")
    return float(value) if _finite_number(value) else None


def _selected_outcome_snapshot(stage: str, decision: str) -> bool:
    """Whether this snapshot belongs to the realtime selected/actionable outcome lane."""
    return (
        (stage == "rank" and decision == "ranked")
        or (stage == "notification" and decision in {"eligible", "notified", "suppressed"})
    )


def _selected_outcome_sql():
    """SQL equivalent of :func:`_selected_outcome_snapshot` for large-day queries."""
    return or_(
        and_(
            OpportunityDecisionSnapshot.stage == "rank",
            OpportunityDecisionSnapshot.decision == "ranked",
        ),
        and_(
            OpportunityDecisionSnapshot.stage == "notification",
            OpportunityDecisionSnapshot.decision.in_(("eligible", "notified", "suppressed")),
        ),
    )


def build_intraday_records(
    payload: dict, *, trade_date: str, as_of: datetime, kb_ids: Iterable[str] = (),
) -> tuple[str, list[dict]]:
    """Turn one opportunity tree into candidate → hard-gate → rank evidence.

    `kb_ids` = 本阶段决策**实际引用**的 KB 条目（蓝图 §5）。缺省空 ⇒ 快照记
    `state="not_consulted"`（这是现状，不是异常——蓝图 §5 自述选股运行时尚未接 KB）。
    引用一律经 `kb_routing.snapshot_citations()` 校验后落 `kb_ids` / `kb_refs` 两列，
    **本函数不自行拼这两个字段**（避免出现第二份引用口径）。
    """
    from app.picks.intraday_opportunity import top_watch_stocks

    as_of = as_of.replace(tzinfo=None)
    run_id = _hash({"scenario": "intraday", "trade_date": trade_date, "as_of": as_of.isoformat()})
    kb_ids_json, kb_refs_json = snapshot_citations("intraday_opportunity", kb_ids)
    ranked = top_watch_stocks(payload, limit=10_000).get("items") or []
    rank_by_key = {
        (str(row.get("symbol") or ""), str(row.get("theme") or "")): n
        for n, row in enumerate(ranked, 1)
    }
    state = _data_state(payload)
    records: list[dict] = []
    for theme in payload.get("themes") or []:
        theme_name = str(theme.get("theme") or "")
        participants = theme.get("participants") or []
        participant_by = {str(s.get("symbol") or ""): s for s in participants}
        audit_rows = theme.get("_candidate_audit") or []
        if not audit_rows:
            audit_rows = [
                {
                    "symbol": s.get("symbol"), "name": s.get("name"),
                    "candidate_decision": "included",
                    "hard_gate_decision": (
                        "passed" if (s.get("tradability") or {}).get("level") == "可参与"
                        else "unknown" if (s.get("tradability") or {}).get("level") in (None, "unknown")
                        else "rejected"
                    ),
                    "reason": (s.get("tradability") or {}).get("basis"),
                    "price": s.get("price"), "change_pct": s.get("change_pct"),
                    "amount": s.get("amount"), "board": s.get("board"),
                    "facts": {
                        "quote_present": s.get("price") is not None,
                        "board_tradable": (s.get("tradability") or {}).get("level") == "可参与",
                        "change_pct": s.get("change_pct"),
                        "amount": s.get("amount"),
                        **(s.get("seal_state") or {}),
                    },
                }
                for s in participants
            ]
        for audit in audit_rows:
            symbol = str(audit.get("symbol") or "")
            if not symbol:
                continue
            stock = participant_by.get(symbol) or audit
            price = audit.get("price", stock.get("price"))
            common = {
                "run_id": run_id, "trade_date": trade_date, "as_of": as_of,
                "scenario": "intraday_opportunity", "symbol": symbol,
                "name": str(audit.get("name") or stock.get("name") or ""),
                "source_theme": theme_name, "strategy_version": STRATEGY_VERSION,
                "feature_version": FEATURE_VERSION, "data_state": state,
                "kb_ids": kb_ids_json, "kb_refs": kb_refs_json,
                "entry_price": float(price) if isinstance(price, (int, float)) and price > 0 else None,
            }
            candidate_evidence = {
                "theme_stage": theme.get("stage"),
                "theme_strength": theme.get("strength_tier"),
                "change_pct": audit.get("change_pct"), "amount": audit.get("amount"),
                "filter_reason": audit.get("reason"), "facts": audit.get("facts") or {},
            }
            records.append({
                **common, "stage": "candidate",
                "decision": str(audit.get("candidate_decision") or "unknown"),
                "rank": None, "evidence": candidate_evidence,
            })
            facts = audit.get("facts") or {}
            gate_evidence = {
                "filter_reason": audit.get("reason"), "board": audit.get("board"), "facts": facts,
                "tradability_level": (
                    "可参与" if audit.get("hard_gate_decision") == "passed"
                    else "unknown" if audit.get("hard_gate_decision") == "unknown"
                    else "不可参与"
                ),
            }
            records.append({
                **common, "stage": "hard_gate",
                "decision": str(audit.get("hard_gate_decision") or "unknown"),
                "rank": None, "evidence": gate_evidence,
            })

        for stock in participants:
            symbol = str(stock.get("symbol") or "")
            if not symbol:
                continue
            name = str(stock.get("name") or "")
            price = stock.get("price")
            tradability = stock.get("tradability") or {}
            linkage = stock.get("linkage") or {}
            common = {
                "run_id": run_id,
                "trade_date": trade_date,
                "as_of": as_of,
                "scenario": "intraday_opportunity",
                "symbol": symbol,
                "name": name,
                "source_theme": theme_name,
                "strategy_version": STRATEGY_VERSION,
                "feature_version": FEATURE_VERSION,
                "data_state": state,
                "kb_ids": kb_ids_json, "kb_refs": kb_refs_json,
                "entry_price": float(price) if isinstance(price, (int, float)) and price > 0 else None,
            }
            tradability_level = tradability.get("level")
            if tradability_level == "可参与":
                gate_decision = "passed"
            elif tradability_level == "unknown" or not tradability_level:
                gate_decision = "unknown"
            else:
                gate_decision = "rejected"
            rank = rank_by_key.get((symbol, theme_name))
            linkage_level = linkage.get("level")
            if gate_decision == "unknown" or linkage_level == "unknown" or not linkage_level:
                rank_decision = "unknown"
            elif gate_decision != "passed" or linkage_level not in ("高", "中"):
                rank_decision = "rejected"
            else:
                rank_decision = "ranked"
            rank_evidence = {
                "gate_decision": gate_decision,
                "linkage_level": linkage_level,
                "linkage_basis": linkage.get("basis"),
                "change_pct": stock.get("change_pct"),
                "seal_state": stock.get("seal_state"),
                "expected_rank": rank,
            }
            records.append({**common, "stage": "rank", "decision": rank_decision,
                            "rank": rank, "evidence": rank_evidence})
    return run_id, records


def build_notification_records(
    items: list[dict], *, trade_date: str, as_of: datetime,
    hits: list[dict], skips: list[dict], dispatch_by_symbol: dict[str, str],
    kb_ids: Iterable[str] = (), pick_generated_at: str | None = None,
    execution_by_symbol: dict[str, dict] | None = None,
) -> tuple[str, list[dict]]:
    """Archive every notification-gate input, including negative decisions.

    `kb_ids` 语义同 `build_intraday_records`：仅记录**实际引用**的 KB 条目；
    本阶段属 `intraday_pick` 场景（别名 `buy_point`）。
    """
    as_of = as_of.replace(tzinfo=None)
    run_id = _hash({"scenario": "notification", "trade_date": trade_date, "as_of": as_of.isoformat()})
    kb_ids_json, kb_refs_json = snapshot_citations("buy_point", kb_ids)
    hit_by = {str(h.get("item", {}).get("symbol") or ""): h for h in hits}
    skip_by = {str(s.get("symbol") or ""): str(s.get("reason") or "") for s in skips}
    records: list[dict] = []
    for item in items or []:
        symbol = str(item.get("symbol") or "")
        if not symbol:
            continue
        hit = hit_by.get(symbol)
        dispatch = dispatch_by_symbol.get(symbol)
        if hit is None:
            decision = "rejected"
        elif dispatch == "notified":
            decision = "notified"
        elif dispatch == "suppressed":
            decision = "suppressed"
        else:
            decision = "eligible"
        price = (hit or {}).get("price")
        gate_decision = "passed" if hit is not None else "rejected"
        gate_reason = skip_by.get(symbol)
        execution_contract = build_execution_contract(
            trade_date=trade_date, symbol=symbol, item=item,
            executable_snapshot=(execution_by_symbol or {}).get(symbol),
            gate_decision=gate_decision, gate_reason=gate_reason,
            pick_generated_at=pick_generated_at,
        )
        evidence = {
            "gate_decision": gate_decision,
            "gate_reason": gate_reason,
            "dispatch": dispatch,
            "execution_contract": execution_contract,
            "confidence_tier": (item.get("confidence") or {}).get("tier"),
            "vetoes": item.get("vetoes") or [],
            "follow_state": item.get("follow_state"),
            "observation_only": bool(item.get("observation_only")),
            "buy_range": item.get("buy_range"),
            "change_pct": (hit or {}).get("chg"),
        }
        records.append({
            "run_id": run_id, "trade_date": trade_date, "as_of": as_of,
            "scenario": "buy_point", "stage": "notification", "symbol": symbol,
            "name": str(item.get("name") or ""), "source_theme": "",
            "decision": decision, "rank": None, "strategy_version": STRATEGY_VERSION,
            "feature_version": FEATURE_VERSION,
            "data_state": str(execution_contract["executable_snapshot"].get("state") or "unknown"),
            "kb_ids": kb_ids_json, "kb_refs": kb_refs_json,
            "entry_price": float(price) if isinstance(price, (int, float)) and price > 0 else None,
            "evidence": evidence,
        })
    return run_id, records


def outcome_target_dates(trade_date: str, trading_days: list[date]) -> dict[str, str]:
    """Map D0/D1/D3/D5 to exact trading-session dates; never guess calendar days.

    If the injected calendar does not contain the anchor or does not extend far
    enough, unavailable future horizons are omitted. Callers may retry later when
    the authoritative calendar covers more sessions.
    """
    anchor = date.fromisoformat(trade_date)
    normalized = sorted({d for d in trading_days if isinstance(d, date)})
    targets = {OUTCOME_HORIZON: trade_date}
    if anchor not in normalized:
        return targets
    index = normalized.index(anchor)
    for horizon, offset in OUTCOME_HORIZONS.items():
        if offset == 0:
            continue
        target_index = index + offset
        if target_index < len(normalized):
            targets[horizon] = normalized[target_index].isoformat()
    return targets


def _new_horizon_outcome(
    snapshot: OpportunityDecisionSnapshot, horizon: str, target_date: str,
) -> OpportunityOutcomeLabel:
    selected = _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
    return OpportunityOutcomeLabel(
        snapshot_id=snapshot.snapshot_id, horizon=horizon, target_date=target_date,
        state="pending" if selected else "deferred", label="unknown",
        reference_price=snapshot.entry_price,
        fill_state="pending" if selected else "not_actionable",
        price_basis_version=PRICE_BASIS_VERSION,
        path_state="not_started", path_version="",
        reason=(
            f"等待 {horizon}@{target_date} 收盘价" if selected
            else (
                f"全漏斗分母已登记；{snapshot.stage}:{snapshot.decision} 非实时动作样本；"
                f"等待离线 {horizon}@{target_date} 结果回填"
            )
        ),
    )


def ensure_outcome_horizons(
    trade_date: str, trading_days: list[date], session_factory=None, *,
    include_deferred: bool = True,
) -> dict:
    """Ensure D1/D3/D5 identities without rewriting decision snapshots.

    Production EOD uses ``include_deferred=False`` so rejected/unknown full-funnel
    research rows cannot multiply the online database by three horizons per refresh.
    Explicit offline research may still request the full funnel.
    """
    targets = outcome_target_dates(trade_date, trading_days)
    future_targets = {h: d for h, d in targets.items() if h != OUTCOME_HORIZON}
    sf = session_factory or get_session_factory()
    inserted = 0
    total_snapshots = 0
    with sf() as db:
        base_where = OpportunityDecisionSnapshot.trade_date == trade_date
        if include_deferred:
            snapshots = db.execute(
                select(OpportunityDecisionSnapshot).where(base_where)
            ).scalars().all()
            total_snapshots = len(snapshots)
        else:
            total_snapshots = int(db.scalar(
                select(func.count()).select_from(OpportunityDecisionSnapshot).where(base_where)
            ) or 0)
            snapshots = db.execute(
                select(OpportunityDecisionSnapshot).where(
                    base_where, _selected_outcome_sql()
                )
            ).scalars().all()
        if snapshots and future_targets:
            # Never expand tens of thousands of snapshot IDs into one SQLite IN (...).
            # Join through the decision date instead; this stays valid for large legacy days.
            existing = set(db.execute(
                select(OpportunityOutcomeLabel.snapshot_id, OpportunityOutcomeLabel.horizon)
                .join(
                    OpportunityDecisionSnapshot,
                    OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
                )
                .where(
                    OpportunityDecisionSnapshot.trade_date == trade_date,
                    OpportunityOutcomeLabel.horizon.in_(tuple(future_targets)),
                )
            ).all())
            for snapshot in snapshots:
                for horizon, target_date in future_targets.items():
                    key = (snapshot.snapshot_id, horizon)
                    if key in existing:
                        continue
                    db.add(_new_horizon_outcome(snapshot, horizon, target_date))
                    existing.add(key)
                    inserted += 1
            db.commit()
    return {
        "trade_date": trade_date,
        "targets": targets,
        "missing_horizons": sorted(set(FUTURE_OUTCOME_HORIZONS) - set(future_targets)),
        "scope": "full_funnel" if include_deferred else "selected_only",
        "source_snapshots": total_snapshots,
        "snapshots": len(snapshots),
        "inserted": inserted,
    }


def _new_outcome_identity(
    *, snapshot_id: str, trade_date: str, stage: str, decision: str,
    reference_price: float | None,
) -> OpportunityOutcomeLabel:
    """Build one D0 outcome identity from immutable decision facts.

    New online writes and legacy recovery must share this constructor; otherwise
    historical rows can silently acquire different pending/deferred semantics.
    """
    selected = _selected_outcome_snapshot(stage, decision)
    return OpportunityOutcomeLabel(
        snapshot_id=snapshot_id, horizon=OUTCOME_HORIZON, target_date=trade_date,
        state="pending" if selected else "deferred", label="unknown",
        reference_price=reference_price,
        fill_state="pending" if selected else "not_actionable",
        price_basis_version=PRICE_BASIS_VERSION,
        path_state="pending" if selected else "deferred", path_version=PATH_VERSION,
        reason=(
            "等待收盘价" if selected
            else f"全漏斗分母已登记；{stage}:{decision} 非实时动作样本，等待离线结果回填"
        ),
    )


def _new_outcome_label(snapshot_id: str, record: dict) -> OpportunityOutcomeLabel:
    return _new_outcome_identity(
        snapshot_id=snapshot_id, trade_date=record["trade_date"],
        stage=record["stage"], decision=record["decision"],
        reference_price=record.get("entry_price"),
    )


def backfill_missing_outcome_identities(
    session_factory=None, *, trade_dates: Iterable[str] | None = None, batch_size: int = 2000,
) -> dict[str, Any]:
    """Recover missing legacy D0 identities without rewriting decision snapshots.

    This is deliberately an explicit offline operation.  It only inserts rows whose
    ``(snapshot_id, d0_close)`` identity is absent and repairs the pre-RSH-026 false
    ``pending + fill_state=ok`` default.  Labeled outcomes are never rewritten.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    sf = session_factory or get_session_factory()
    dates = tuple(sorted({str(d) for d in (trade_dates or ()) if str(d)}))
    inserted = selected_pending = deferred = repaired_pending_fill = 0
    last_id = 0

    while True:
        with sf() as db:
            missing = ~exists(
                select(OpportunityOutcomeLabel.id).where(
                    OpportunityOutcomeLabel.snapshot_id == OpportunityDecisionSnapshot.snapshot_id,
                    OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
                )
            )
            stmt = (
                select(OpportunityDecisionSnapshot)
                .where(OpportunityDecisionSnapshot.id > last_id, missing)
                .order_by(OpportunityDecisionSnapshot.id)
                .limit(batch_size)
            )
            if dates:
                stmt = stmt.where(OpportunityDecisionSnapshot.trade_date.in_(dates))
            rows = db.execute(stmt).scalars().all()
            if not rows:
                break
            for snapshot in rows:
                selected = _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
                db.add(_new_outcome_identity(
                    snapshot_id=snapshot.snapshot_id, trade_date=snapshot.trade_date,
                    stage=snapshot.stage, decision=snapshot.decision,
                    reference_price=snapshot.entry_price,
                ))
                inserted += 1
                if selected:
                    selected_pending += 1
                else:
                    deferred += 1
            last_id = rows[-1].id
            db.commit()

    with sf() as db:
        stmt = (
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            )
            .where(
                OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
                OpportunityOutcomeLabel.state == "pending",
                OpportunityOutcomeLabel.labeled_at.is_(None),
                OpportunityOutcomeLabel.fill_state == "ok",
                OpportunityOutcomeLabel.reason == "等待收盘价",
            )
        )
        if dates:
            stmt = stmt.where(OpportunityDecisionSnapshot.trade_date.in_(dates))
        for outcome, snapshot in db.execute(stmt).all():
            if not _selected_outcome_snapshot(snapshot.stage, snapshot.decision):
                continue
            outcome.fill_state = "pending"
            repaired_pending_fill += 1
        db.commit()

    return {
        "inserted": inserted,
        "selected_pending": selected_pending,
        "deferred": deferred,
        "repaired_pending_fill_state": repaired_pending_fill,
        "trade_dates": list(dates),
    }


def build_intraday_run_meta(
    payload: dict, *, run_id: str, trade_date: str, as_of: datetime, records: list[dict],
) -> dict:
    """Build immutable run-level evidence, including a legitimate zero-record run."""
    as_of = to_beijing_naive(as_of)
    themes = payload.get("themes") or []
    stage_counts = Counter(str(record.get("stage") or "unknown") for record in records)
    decision_counts = Counter(
        f"{record.get('stage') or 'unknown'}:{record.get('decision') or 'unknown'}"
        for record in records
    )
    linkage_stats = payload.get("linkage_stats") or {}
    summary = {
        **(payload.get("summary") or {}),
        "hot_available": payload.get("hot_available"),
        "linkage_note": payload.get("linkage_note"),
        "board_excluded_reference": payload.get("board_excluded_reference"),
        "tradable_boards": payload.get("tradable_boards"),
    }
    base = {
        "run_id": run_id,
        "trade_date": trade_date,
        "as_of": as_of,
        "scenario": "intraday_opportunity",
        "strategy_version": STRATEGY_VERSION,
        "feature_version": FEATURE_VERSION,
        "data_state": _data_state(payload),
        "snapshot_state": str(linkage_stats.get("snapshot_state") or "unknown"),
        "snapshot_as_of": str(linkage_stats.get("snapshot_as_of") or ""),
        "theme_count": len(themes),
        "participant_count": sum(len(theme.get("participants") or []) for theme in themes),
        "candidate_audit_count": sum(len(theme.get("_candidate_audit") or []) for theme in themes),
        "records_total": len(records),
        "candidate_rows": int(stage_counts.get("candidate", 0)),
        "hard_gate_rows": int(stage_counts.get("hard_gate", 0)),
        "rank_rows": int(stage_counts.get("rank", 0)),
        "notification_rows": int(stage_counts.get("notification", 0)),
        "stage_counts": _json(dict(sorted(stage_counts.items()))),
        "decision_counts": _json(dict(sorted(decision_counts.items()))),
        "linkage_stats": _json(linkage_stats),
        "summary": _json(summary),
        "caveats": _json(payload.get("caveats") or []),
    }
    digest_payload = {
        key: (value.isoformat() if isinstance(value, datetime) else value)
        for key, value in base.items()
    }
    return {**base, "evidence_digest": _hash(digest_payload, 64)}


def build_notification_run_meta(
    *, run_id: str, trade_date: str, as_of: datetime, records: list[dict],
) -> dict:
    """Build the run header for one buy-point notification beat.

    Notification rows already carry a ``run_id``; without the matching header those
    rows become orphaned point-in-time evidence and zero-record beats disappear entirely.
    This run type stays separate from ``intraday_opportunity`` so learning/scorecard
    queries keep their existing denominator.
    """
    as_of = as_of.replace(tzinfo=None)
    stage_counts = Counter(str(record.get("stage") or "unknown") for record in records)
    decision_counts = Counter(
        f"{record.get('stage') or 'unknown'}:{record.get('decision') or 'unknown'}"
        for record in records
    )
    states = {str(record.get("data_state") or "unknown") for record in records}
    snapshot_state = next(iter(states)) if len(states) == 1 else ("degraded" if states else "unknown")
    snapshot_times = {
        str((((record.get("evidence") or {}).get("execution_contract") or {})
             .get("executable_snapshot") or {}).get("as_of") or "")
        for record in records
    }
    snapshot_times.discard("")
    snapshot_as_of = next(iter(snapshot_times)) if len(snapshot_times) == 1 else ""
    caveats = ["notification execution snapshots have mixed as_of"] if len(snapshot_times) > 1 else []
    base = {
        "run_id": run_id, "trade_date": trade_date, "as_of": as_of,
        "scenario": "buy_point", "strategy_version": STRATEGY_VERSION,
        "feature_version": FEATURE_VERSION, "data_state": snapshot_state,
        "snapshot_state": snapshot_state, "snapshot_as_of": snapshot_as_of,
        "theme_count": 0, "participant_count": 0, "candidate_audit_count": 0,
        "records_total": len(records), "candidate_rows": 0, "hard_gate_rows": 0,
        "rank_rows": 0, "notification_rows": int(stage_counts.get("notification", 0)),
        "stage_counts": _json(dict(sorted(stage_counts.items()))),
        "decision_counts": _json(dict(sorted(decision_counts.items()))),
        "linkage_stats": _json({}), "summary": _json({"notification_items": len(records)}),
        "caveats": _json(caveats),
    }
    digest_payload = {
        key: (value.isoformat() if isinstance(value, datetime) else value)
        for key, value in base.items()
    }
    return {**base, "evidence_digest": _hash(digest_payload, 64)}


def archive_records(
    run_id: str, records: list[dict], session_factory=None, *, run_meta: dict | None = None,
) -> dict:
    """Persist a whole run atomically; rerunning the same run is idempotent."""
    sf = session_factory or get_session_factory()
    inserted = 0
    outcomes_inserted = 0
    run_inserted = 0
    with sf() as db:
        if run_meta is not None:
            if str(run_meta.get("run_id") or "") != run_id:
                raise ValueError("run_meta.run_id must match run_id")
            existing_run = db.get(OpportunityDecisionRun, run_id)
            if existing_run is None:
                db.add(OpportunityDecisionRun(**run_meta))
                run_inserted = 1
            elif existing_run.evidence_digest != run_meta.get("evidence_digest"):
                raise RuntimeError(
                    "opportunity run digest mismatch for existing run_id; "
                    "refusing to merge two point-in-time truths"
                )
        existing = set(db.execute(
            select(OpportunityDecisionSnapshot.snapshot_id).where(
                OpportunityDecisionSnapshot.run_id == run_id
            )
        ).scalars().all())
        existing_outcomes = {
            row.snapshot_id: row for row in db.execute(
                select(OpportunityOutcomeLabel)
                .join(OpportunityDecisionSnapshot,
                      OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
                .where(OpportunityDecisionSnapshot.run_id == run_id,
                       OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON)
            ).scalars().all()
        }
        outcomes_repaired = 0
        for record in records:
            # 旧拍/补录仍须 append-only 保留；“当前最新”只在读侧按 as_of 决定，
            # 不能为防倒退而删除历史证据，否则离线回放与晚到数据会失真。
            evidence = record.get("evidence") or {}
            snapshot_id = _hash({
                "run_id": run_id, "stage": record["stage"], "symbol": record["symbol"],
                "theme": record.get("source_theme") or "", "decision": record["decision"],
                "evidence": evidence,
            }, 40)
            if snapshot_id in existing:
                # Cross-version self-heal: legacy snapshots may predate full-funnel
                # outcome identities. Replaying the exact run may attach the missing
                # outcome row, but never rewrites or duplicates the snapshot itself.
                existing_outcome = existing_outcomes.get(snapshot_id)
                if existing_outcome is None:
                    outcome = _new_outcome_label(snapshot_id, record)
                    db.add(outcome)
                    existing_outcomes[snapshot_id] = outcome
                    outcomes_inserted += 1
                elif (
                    existing_outcome.state == "pending"
                    and existing_outcome.labeled_at is None
                    and existing_outcome.fill_state == "ok"
                    and (existing_outcome.reason or "").strip() == "等待收盘价"
                ):
                    # Legacy ORM default claimed fillability before assessment. Pending
                    # rows are mutable workflow state, so correct that false claim on
                    # exact-run replay without touching snapshot evidence or labeled rows.
                    existing_outcome.fill_state = "pending"
                    outcomes_repaired += 1
                continue
            row = OpportunityDecisionSnapshot(
                snapshot_id=snapshot_id,
                evidence=_json(evidence),
                **{k: v for k, v in record.items() if k != "evidence"},
            )
            db.add(row)
            # RSH-026: every funnel snapshot owns an outcome identity so rejected/unknown
            # rows cannot disappear from the denominator. Only selected/actionable rows
            # enter the realtime close-fetch lane; denominator-only rows stay deferred
            # until an explicit offline backfill supplies the same market-close fact.
            db.add(_new_outcome_label(snapshot_id, record))
            existing.add(snapshot_id)
            existing_outcomes[snapshot_id] = None
            inserted += 1
            outcomes_inserted += 1
        db.commit()
    return {
        "run_id": run_id, "run_inserted": run_inserted,
        "inserted": inserted, "outcomes_inserted": outcomes_inserted,
        "outcomes_repaired": outcomes_repaired, "records": len(records),
    }


def archive_intraday_pipeline(payload: dict, *, trade_date: str, as_of: datetime | None = None,
                              kb_ids: Iterable[str] = (), session_factory=None) -> dict:
    effective_as_of = as_of or beijing_now()
    run_id, records = build_intraday_records(
        payload, trade_date=trade_date, as_of=effective_as_of, kb_ids=kb_ids,
    )
    run_meta = build_intraday_run_meta(
        payload, run_id=run_id, trade_date=trade_date, as_of=effective_as_of, records=records,
    )
    return archive_records(
        run_id, records, session_factory, run_meta=run_meta
    )


def archive_notification_pipeline(
    items: list[dict], *, trade_date: str, hits: list[dict], skips: list[dict],
    dispatch_by_symbol: dict[str, str], kb_ids: Iterable[str] = (),
    as_of: datetime | None = None, session_factory=None, pick_generated_at: str | None = None,
    execution_by_symbol: dict[str, dict] | None = None,
) -> dict:
    effective_as_of = as_of or beijing_now()
    run_id, records = build_notification_records(
        items, trade_date=trade_date, as_of=effective_as_of, hits=hits, skips=skips,
        dispatch_by_symbol=dispatch_by_symbol, kb_ids=kb_ids,
        pick_generated_at=pick_generated_at, execution_by_symbol=execution_by_symbol,
    )
    run_meta = build_notification_run_meta(
        run_id=run_id, trade_date=trade_date, as_of=effective_as_of, records=records,
    )
    return archive_records(run_id, records, session_factory, run_meta=run_meta)


def latest_notification_execution(trade_date: str, session_factory=None) -> dict[str, dict]:
    """读取每只股票最新一版买点执行事实；只读，不生成样本（IMP-006）。"""
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(OpportunityDecisionSnapshot)
            .where(
                OpportunityDecisionSnapshot.trade_date == trade_date,
                OpportunityDecisionSnapshot.stage == "notification",
            )
            .order_by(OpportunityDecisionSnapshot.as_of, OpportunityDecisionSnapshot.id)
        ).scalars().all()
    out: dict[str, dict] = {}
    for row in rows:
        evidence = _load_json(row.evidence, {})
        contract = evidence.get("execution_contract") if isinstance(evidence, dict) else None
        if isinstance(contract, dict) and contract.get("decision_version"):
            out[row.symbol] = {
                **contract,
                "dispatch": evidence.get("dispatch"),
                "archived_decision": row.decision,
                "archived_as_of": row.as_of.isoformat() if row.as_of else None,
                "snapshot_id": row.snapshot_id,
            }
    return out


def replay_decision(stage: str, evidence: dict) -> str:
    """Re-evaluate one archived stage using archived facts only.

    Legacy v1 rows used ``sealed_pool`` as a permanent reject fact; keep that historical replay
    semantics.  v2 rows instead carry ``ever_sealed/current_sealed/snapshot_state/version`` so an
    ever-sealed stock can reopen without rewriting old evidence.
    """
    def seal_gate(facts: dict) -> str | None:
        if facts.get("sealed_pool") is True:  # legacy v1 evidence
            return "rejected"
        if facts.get("current_sealed") is True:
            return "rejected"
        if facts.get("ever_sealed") is True:
            if facts.get("current_sealed") is None:
                return "unknown"
            if facts.get("snapshot_state") != "ready" or not facts.get("version"):
                return "unknown"
        return None

    if stage == "candidate":
        facts = evidence.get("facts") or {}
        if facts.get("board_tradable") is False:
            return "rejected"
        sealed = seal_gate(facts)
        if sealed is not None:
            return sealed
        if facts.get("quote_present") is False or (
            "change_pct" in facts and facts.get("change_pct") is None
        ):
            return "unknown"
        pct = facts.get("change_pct")
        if pct is not None and facts.get("linkage_min_pct") is not None:
            if pct < facts["linkage_min_pct"]:
                return "rejected"
        if facts.get("amount_min") is not None and (facts.get("amount") or 0) < facts["amount_min"]:
            return "rejected"
        if facts.get("seal_line") is not None and pct is not None and pct >= facts["seal_line"]:
            return "rejected"
        return "included"
    if stage == "hard_gate":
        facts = evidence.get("facts") or {}
        if facts.get("board_tradable") is False:
            return "rejected"
        sealed = seal_gate(facts)
        if sealed is not None:
            return sealed
        if facts.get("quote_present") is False or (
            "change_pct" in facts and facts.get("change_pct") is None
        ):
            return "unknown"
        pct = facts.get("change_pct")
        if facts.get("seal_line") is not None and pct is not None and pct >= facts["seal_line"]:
            return "rejected"
        level = evidence.get("tradability_level")
        return "unknown" if level in (None, "unknown") else ("passed" if level == "可参与" else "rejected")
    if stage == "rank":
        gate = evidence.get("gate_decision")
        linkage = evidence.get("linkage_level")
        if gate == "unknown" or linkage == "unknown" or not linkage:
            return "unknown"
        return "ranked" if gate == "passed" and linkage in ("高", "中") else "rejected"
    if stage == "notification":
        if evidence.get("gate_decision") != "passed":
            return "rejected"
        dispatch = evidence.get("dispatch")
        return dispatch if dispatch in ("notified", "suppressed") else "eligible"
    raise ValueError(f"unknown opportunity stage: {stage}")


def replay_run(run_id: str, session_factory=None) -> dict:
    sf = session_factory or get_session_factory()
    with sf() as db:
        run = db.get(OpportunityDecisionRun, run_id)
        rows = db.execute(
            select(OpportunityDecisionSnapshot)
            .where(OpportunityDecisionSnapshot.run_id == run_id)
            .order_by(OpportunityDecisionSnapshot.id)
        ).scalars().all()
    items = []
    mismatches = 0
    for row in rows:
        try:
            evidence = json.loads(row.evidence or "{}")
        except Exception:
            evidence = {}
        replayed = replay_decision(row.stage, evidence)
        matches = replayed == row.decision
        mismatches += 0 if matches else 1
        items.append({
            "snapshot_id": row.snapshot_id, "stage": row.stage, "symbol": row.symbol,
            "theme": row.source_theme, "archived": row.decision, "replayed": replayed,
            "matches": matches, "evidence": evidence,
            # 蓝图 §5 的 KB 引用项（重放时要能看到「当时引了什么」）
            "kb_ids": _load_json(row.kb_ids, []),
            "kb_refs": _load_json(row.kb_refs, {}),
        })
    run_evidence = None
    if run is not None:
        run_evidence = {
            "trade_date": run.trade_date,
            "as_of": run.as_of.isoformat() if run.as_of else None,
            "scenario": run.scenario,
            "data_state": run.data_state,
            "snapshot_state": run.snapshot_state,
            "snapshot_as_of": run.snapshot_as_of,
            "theme_count": run.theme_count,
            "participant_count": run.participant_count,
            "candidate_audit_count": run.candidate_audit_count,
            "records_total": run.records_total,
            "stage_counts": _load_json(run.stage_counts, {}),
            "decision_counts": _load_json(run.decision_counts, {}),
            "linkage_stats": _load_json(run.linkage_stats, {}),
            "summary": _load_json(run.summary, {}),
            "caveats": _load_json(run.caveats, []),
            "evidence_digest": run.evidence_digest,
        }
    return {
        "run_id": run_id, "records": len(items), "mismatches": mismatches,
        "run_evidence": run_evidence, "items": items,
    }


def _continuous_trading_minutes(start: datetime, end: datetime) -> float:
    """A-share continuous-session elapsed minutes, excluding the lunch break."""
    if end <= start:
        return 0.0
    day = start.date()
    sessions = (
        (
            datetime.combine(day, datetime.min.time()).replace(hour=9, minute=30),
            datetime.combine(day, datetime.min.time()).replace(hour=11, minute=30),
        ),
        (
            datetime.combine(day, datetime.min.time()).replace(hour=13),
            datetime.combine(day, datetime.min.time()).replace(hour=15),
        ),
    )
    seconds = 0.0
    for session_start, session_end in sessions:
        left = max(start, session_start)
        right = min(end, session_end)
        if right > left:
            seconds += (right - left).total_seconds()
    return round(seconds / 60.0, 2)


def _limit_timing(
    trade_date: str, decision_at: datetime, *, limit_member: bool | None,
    first_seal_time: str | None,
) -> dict[str, Any]:
    if limit_member is None:
        return {"limit_state": "unknown", "first_limit_time": None, "time_to_limit_minutes": None}
    if not limit_member:
        return {"limit_state": "not_hit", "first_limit_time": None, "time_to_limit_minutes": None}
    parsed = parse_hhmmss(first_seal_time)
    if parsed is None:
        return {"limit_state": "hit_time_unknown", "first_limit_time": None, "time_to_limit_minutes": None}
    hh, mm, ss = parsed // 10000, (parsed // 100) % 100, parsed % 100
    if hh > 23 or mm > 59 or ss > 59:
        return {"limit_state": "hit_time_unknown", "first_limit_time": None, "time_to_limit_minutes": None}
    first_text = f"{hh:02d}:{mm:02d}:{ss:02d}"
    seal_at = datetime.combine(date.fromisoformat(trade_date), datetime.min.time()).replace(
        hour=hh, minute=mm, second=ss
    )
    delta = (seal_at - decision_at).total_seconds() / 60.0
    if delta <= 0:
        return {
            "limit_state": "preexisting", "first_limit_time": first_text,
            "time_to_limit_minutes": None,
        }
    return {
        "limit_state": "hit", "first_limit_time": first_text,
        # Continuous-session lead time is the actionable clock. Natural elapsed
        # time would count the 11:30-13:00 lunch break and overstate opportunity.
        "time_to_limit_minutes": _continuous_trading_minutes(decision_at, seal_at),
    }


def d0_path_metrics(
    snapshot: OpportunityDecisionSnapshot, minute_bars: Iterable[Any], *,
    limit_member: bool | None = None, first_seal_time: str | None = None,
) -> dict[str, Any]:
    """Decision-time-safe D0 path metrics from **Tencent 1m only**.

    Complete minute bars must be strictly later than ``snapshot.as_of``.  This
    deliberately drops the decision minute so a bar containing pre-decision ticks
    cannot leak into MFE/MAE.  Cross-provider minute fallback is forbidden here:
    current Eastmoney minute timestamps do not share Tencent's UTC normalization.
    """
    decision_at = to_beijing_naive(snapshot.as_of) if snapshot.as_of else None
    timing = (
        _limit_timing(
            snapshot.trade_date, decision_at,
            limit_member=limit_member, first_seal_time=first_seal_time,
        )
        if decision_at is not None
        else {"limit_state": "unknown", "first_limit_time": None, "time_to_limit_minutes": None}
    )
    source = "tencent_1m+zt_zb_pools" if limit_member is not None else "tencent_1m"
    base = {
        **timing,
        "path_version": PATH_VERSION,
        "path_source": source,
        "path_high_price": None,
        "path_low_price": None,
        "mfe_pct": None,
        "mae_pct": None,
        "path_bar_count": 0,
    }
    reference = snapshot.entry_price if _positive_finite(snapshot.entry_price) else None
    if decision_at is None or decision_at.date().isoformat() != snapshot.trade_date:
        return {**base, "path_state": "unknown", "path_reason": "决策 as_of 缺失或与 trade_date 不一致"}
    if reference is None:
        return {**base, "path_state": "unknown", "path_reason": "决策 reference 价格缺失，无法计算路径收益"}

    valid: list[tuple[datetime, float, float]] = []
    for bar in minute_bars or []:
        if getattr(bar, "source", None) != "tencent":
            continue
        ts = getattr(bar, "ts", None)
        if not isinstance(ts, datetime):
            continue
        local_ts = to_beijing_naive(ts)
        # Strictly later than the decision timestamp: no partial decision-minute leakage.
        if local_ts.date().isoformat() != snapshot.trade_date or local_ts <= decision_at:
            continue
        high, low = getattr(bar, "high", None), getattr(bar, "low", None)
        if not (_positive_finite(high) and _positive_finite(low)) or float(high) < float(low):
            continue
        valid.append((local_ts, float(high), float(low)))
    if not valid:
        return {**base, "path_state": "pending", "path_reason": "腾讯1m尚无决策后完整分钟 bar；保留待补"}

    # D0 MFE/MAE must cover the rest of the trading day, not merely a valid prefix.
    # Tencent m1 is rolling/capped, so an early or truncated fetch can otherwise
    # understate afternoon excursion while still looking syntactically valid.
    last_bar_at = max(v[0] for v in valid)
    if (last_bar_at.hour, last_bar_at.minute) < (15, 0):
        return {
            **base,
            "path_state": "pending",
            "path_bar_count": len(valid),
            "path_reason": (
                "腾讯1m决策后序列尚未覆盖收盘；"
                f"last_bar={last_bar_at.strftime('%H:%M')}，不得把截断前缀冒充D0完整路径"
            ),
        }

    high = max(v[1] for v in valid)
    low = min(v[2] for v in valid)
    mfe = round(max(0.0, (high / float(reference) - 1.0) * 100.0), 2)
    mae = round(min(0.0, (low / float(reference) - 1.0) * 100.0), 2)
    return {
        **base,
        "path_state": "labeled",
        "path_reason": "严格使用决策时点之后的腾讯1m完整 bar；不含决策当分钟",
        "path_high_price": high,
        "path_low_price": low,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "path_bar_count": len(valid),
    }


def pending_d0_path_symbols(
    trade_date: str, session_factory=None, *, include_deferred: bool = False,
) -> set[str]:
    """D0 symbols needing path evidence; realtime mode remains selected-only."""
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(
                OpportunityDecisionSnapshot.trade_date == trade_date,
                OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
                OpportunityOutcomeLabel.path_state.in_(("unknown", "pending", "deferred")),
            )
        ).all()
    symbols = set()
    for outcome, snapshot in rows:
        selected = _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
        if not selected and not include_deferred:
            continue
        # Legacy rows are `unknown` with no path version and are eligible for self-heal.
        # Current-version `unknown` is terminal (e.g. reference missing) and must not
        # trigger the same minute request on every EOD run.
        if outcome.path_state == "unknown" and outcome.path_version == PATH_VERSION:
            continue
        symbols.add(snapshot.symbol)
    return symbols


def label_d0_paths(
    trade_date: str, minute_by_symbol: dict[str, list[Any]], *,
    first_seal_by_symbol: dict[str, str | None] | None = None,
    limit_pool_known: bool = False, session_factory=None, include_deferred: bool = False,
) -> dict[str, Any]:
    """Attach D0 post-decision path facts without changing close labels or snapshots."""
    sf = session_factory or get_session_factory()
    first_seal_by_symbol = first_seal_by_symbol or {}
    labeled = pending = unknown = skipped_deferred = 0
    with sf() as db:
        rows = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(
                OpportunityDecisionSnapshot.trade_date == trade_date,
                OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
                OpportunityOutcomeLabel.path_state.in_(("unknown", "pending", "deferred")),
            )
        ).all()
        for outcome, snapshot in rows:
            selected = _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
            if not selected and not include_deferred:
                skipped_deferred += 1
                continue
            if outcome.path_state == "unknown" and outcome.path_version == PATH_VERSION:
                continue
            member = (
                True if snapshot.symbol in first_seal_by_symbol
                else False if limit_pool_known else None
            )
            metrics = d0_path_metrics(
                snapshot, minute_by_symbol.get(snapshot.symbol) or [],
                limit_member=member,
                first_seal_time=first_seal_by_symbol.get(snapshot.symbol),
            )
            for key, value in metrics.items():
                setattr(outcome, key, value)
            if metrics["path_state"] == "labeled":
                outcome.path_resolved_at = utcnow()
                labeled += 1
            elif metrics["path_state"] == "unknown":
                outcome.path_resolved_at = utcnow()
                unknown += 1
            else:
                pending += 1
        db.commit()
    return {
        "trade_date": trade_date, "labeled": labeled, "pending": pending,
        "unknown": unknown, "skipped_deferred": skipped_deferred,
        "limit_pool_known": limit_pool_known,
    }


def _basis_qfq_source(source: str | None) -> str:
    for part in str(source or "").split("|"):
        if part.startswith("qfq:"):
            return part[4:]
    return ""


def cross_day_path_metrics(
    snapshot: OpportunityDecisionSnapshot,
    d0_outcome: OpportunityOutcomeLabel | None,
    future_outcome: OpportunityOutcomeLabel,
    qfq_daily_path: dict[date, tuple[float, float, str]],
    trading_days: list[date],
    price_basis_by_key: dict[tuple[str, str], tuple[float, str]],
) -> dict[str, Any]:
    """Cumulative post-decision MFE/MAE through one future trading horizon.

    D0 extrema come only from the already captured decision-safe Tencent 1m path.
    Later sessions use qfq daily high/low.  Missing intermediate market sessions
    fail closed because this layer cannot prove suspension vs data loss.
    """
    base = {
        "path_version": CROSS_DAY_PATH_VERSION,
        "path_source": "",
        "path_high_price": None,
        "path_low_price": None,
        "mfe_pct": None,
        "mae_pct": None,
        "path_bar_count": 0,
        "limit_state": "unknown",
        "first_limit_time": None,
        "time_to_limit_minutes": None,
    }
    horizon = future_outcome.horizon
    offset = OUTCOME_HORIZONS.get(horizon)
    if not offset:
        return {**base, "path_state": "unknown", "path_reason": f"{horizon} 不是跨日路径 horizon"}
    try:
        anchor = date.fromisoformat(snapshot.trade_date)
        target = date.fromisoformat(future_outcome.target_date)
    except Exception:
        return {**base, "path_state": "unknown", "path_reason": "trade_date/target_date 非法"}
    calendar = sorted({d for d in trading_days if isinstance(d, date)})
    if anchor not in calendar or target not in calendar:
        return {**base, "path_state": "pending", "path_reason": "交易日历尚未覆盖决策日与目标日"}
    anchor_index = calendar.index(anchor)
    target_index = calendar.index(target)
    if target_index != anchor_index + offset:
        return {
            **base, "path_state": "unknown",
            "path_reason": (
                f"{horizon} 目标交易日身份不一致：{snapshot.trade_date}->{future_outcome.target_date}，"
                f"expected_offset={offset}, actual_offset={target_index-anchor_index}"
            ),
        }
    expected_days = calendar[anchor_index + 1: target_index + 1]

    reference = snapshot.entry_price if _positive_finite(snapshot.entry_price) else None
    if reference is None:
        return {**base, "path_state": "unknown", "path_reason": "决策 reference 价格缺失，无法计算跨日路径"}
    basis = price_basis_by_key.get((snapshot.trade_date, snapshot.symbol))
    factor = basis[0] if basis else None
    basis_source = str(basis[1] or "") if basis else ""
    qfq_source = _basis_qfq_source(basis_source)
    if not _positive_finite(factor) or not qfq_source:
        return {
            **base, "path_state": "pending",
            "path_reason": f"等待 {snapshot.trade_date}/{snapshot.symbol} {PRICE_BASIS_VERSION} 同源价格基准",
        }
    basis_reference = float(reference) * float(factor)
    if not _positive_finite(basis_reference):
        return {**base, "path_state": "pending", "path_reason": "价格基准换算后 reference 非有限正数"}

    if d0_outcome is None:
        return {**base, "path_state": "pending", "path_reason": "缺 D0 outcome，不能定义决策后起点路径"}
    if d0_outcome.path_state == "unknown" and d0_outcome.path_version == PATH_VERSION:
        return {
            **base, "path_state": "unknown",
            "path_reason": "D0 当前版本路径已终态 unknown，跨日路径无法无前视重建",
        }
    if not (
        d0_outcome.path_state == "labeled"
        and d0_outcome.path_version == PATH_VERSION
        and _positive_finite(d0_outcome.path_high_price)
        and _positive_finite(d0_outcome.path_low_price)
        and float(d0_outcome.path_high_price) >= float(d0_outcome.path_low_price)
    ):
        return {
            **base, "path_state": "pending",
            "path_reason": f"等待当前 D0 路径 {PATH_VERSION}；不得用整日 D0 high/low 回填",
        }

    missing = [d for d in expected_days if d not in qfq_daily_path]
    if missing:
        return {
            **base, "path_state": "pending",
            "path_reason": (
                "跨日路径缺完整市场交易日 bar；停牌与数据缺口不可在本层同形处理："
                + ",".join(d.isoformat() for d in missing)
            ),
        }
    future_highs: list[float] = []
    future_lows: list[float] = []
    for d in expected_days:
        high, low, source = qfq_daily_path[d]
        if source != qfq_source:
            return {
                **base, "path_state": "pending",
                "path_reason": f"{d.isoformat()} qfq path source={source or 'unknown'} 与 basis source={qfq_source} 不一致",
            }
        if not (_positive_finite(high) and _positive_finite(low)) or float(high) < float(low):
            return {**base, "path_state": "pending", "path_reason": f"{d.isoformat()} qfq high/low 非有限正数或 high<low"}
        future_highs.append(float(high))
        future_lows.append(float(low))

    # Keep `path_high_price/path_low_price` on the same raw-reference-equivalent
    # scale as D0.  Future qfq bars are divided by the decision-day qfq/raw factor;
    # this preserves total-return economics across corporate actions without making
    # one schema column change price scales by horizon.
    factor_f = float(factor)
    future_high_raw_equiv = [value / factor_f for value in future_highs]
    future_low_raw_equiv = [value / factor_f for value in future_lows]
    high = max([float(d0_outcome.path_high_price), *future_high_raw_equiv])
    low = min([float(d0_outcome.path_low_price), *future_low_raw_equiv])
    ref = float(reference)
    mfe = round(max(0.0, (high / ref - 1.0) * 100.0), 2)
    mae = round(min(0.0, (low / ref - 1.0) * 100.0), 2)
    return {
        **base,
        "path_state": "labeled",
        "path_source": f"d0:{d0_outcome.path_source or PATH_VERSION}|qfq_daily:{qfq_source}",
        "path_reason": (
            f"D0仅用决策后 {PATH_VERSION}；随后 {len(expected_days)} 个市场交易日用完整同源 qfq high/low；"
            f"future qfq 以决策日因子 {factor_f:.8f} 映射回 raw-reference 等价尺度；"
            "缺交易日不跳过，不把 reference 路径冒充 shadow fill P&L"
        ),
        "path_high_price": high,
        "path_low_price": low,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "path_bar_count": int(d0_outcome.path_bar_count or 0) + len(expected_days),
    }


def pending_cross_day_path_targets(
    as_of_date: str, session_factory=None, *, lookback_days: int = 30,
    horizons: Iterable[str] = FUTURE_OUTCOME_HORIZONS,
) -> dict[str, set[str]]:
    """Selected future rows whose cross-day path is due and retryable."""
    as_of = date.fromisoformat(as_of_date)
    lower = (as_of - timedelta(days=max(1, int(lookback_days)))).isoformat()
    wanted = tuple(horizons)
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(
                OpportunityOutcomeLabel.target_date, OpportunityDecisionSnapshot.symbol,
                OpportunityOutcomeLabel.path_state, OpportunityOutcomeLabel.path_version,
            )
            .join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            )
            .where(
                OpportunityOutcomeLabel.target_date >= lower,
                OpportunityOutcomeLabel.target_date <= as_of_date,
                OpportunityOutcomeLabel.horizon.in_(wanted),
                _selected_outcome_sql(),
                OpportunityOutcomeLabel.path_state.in_(("not_started", "unknown", "pending")),
            )
        ).all()
    out: dict[str, set[str]] = {}
    for target_date, symbol, state, version in rows:
        if state == "unknown" and version == CROSS_DAY_PATH_VERSION:
            continue
        out.setdefault(target_date, set()).add(symbol)
    return dict(sorted(out.items()))


def label_cross_day_paths(
    target_date: str,
    qfq_daily_path_by_symbol: dict[str, dict[date, tuple[float, float, str]]],
    trading_days: list[date],
    price_basis_by_key: dict[tuple[str, str], tuple[float, str]],
    session_factory=None, *,
    horizons: Iterable[str] = FUTURE_OUTCOME_HORIZONS,
) -> dict[str, Any]:
    """Attach selected-only D1/D3/D5 cumulative path facts for one exact target date."""
    sf = session_factory or get_session_factory()
    wanted = tuple(horizons)
    labeled = pending = unknown = 0
    with sf() as db:
        rows = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            )
            .where(
                OpportunityOutcomeLabel.target_date == target_date,
                OpportunityOutcomeLabel.horizon.in_(wanted),
                _selected_outcome_sql(),
                OpportunityOutcomeLabel.path_state.in_(("not_started", "unknown", "pending")),
            )
        ).all()
        snapshot_ids = [snapshot.snapshot_id for _outcome, snapshot in rows]
        d0_by_snapshot: dict[str, OpportunityOutcomeLabel] = {}
        if snapshot_ids:
            d0_rows = db.execute(
                select(OpportunityOutcomeLabel).where(
                    OpportunityOutcomeLabel.snapshot_id.in_(snapshot_ids),
                    OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
                )
            ).scalars().all()
            d0_by_snapshot = {outcome.snapshot_id: outcome for outcome in d0_rows}
        for outcome, snapshot in rows:
            if outcome.path_state == "unknown" and outcome.path_version == CROSS_DAY_PATH_VERSION:
                continue
            metrics = cross_day_path_metrics(
                snapshot, d0_by_snapshot.get(snapshot.snapshot_id), outcome,
                qfq_daily_path_by_symbol.get(snapshot.symbol) or {},
                trading_days, price_basis_by_key,
            )
            for key, value in metrics.items():
                setattr(outcome, key, value)
            if metrics["path_state"] == "labeled":
                outcome.path_resolved_at = utcnow()
                labeled += 1
            elif metrics["path_state"] == "unknown":
                outcome.path_resolved_at = utcnow()
                unknown += 1
            else:
                pending += 1
        db.commit()
    return {
        "target_date": target_date, "horizons": list(wanted),
        "labeled": labeled, "pending": pending, "unknown": unknown,
        "path_version": CROSS_DAY_PATH_VERSION,
    }


def pending_outcome_targets(
    as_of_date: str, session_factory=None, *, lookback_days: int = 30,
    horizons: Iterable[str] = FUTURE_OUTCOME_HORIZONS,
) -> dict[str, set[str]]:
    """Selected/actionable future-horizon rows due on or before ``as_of_date``.

    This is the bounded crash-recovery surface for the after-close loop. Deferred
    denominator-only rows are intentionally excluded so research completeness can
    never multiply realtime provider requests.
    """
    as_of = date.fromisoformat(as_of_date)
    lower = (as_of - timedelta(days=max(1, int(lookback_days)))).isoformat()
    wanted = tuple(horizons)
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(OpportunityOutcomeLabel.target_date, OpportunityDecisionSnapshot.symbol)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(
                OpportunityOutcomeLabel.target_date >= lower,
                OpportunityOutcomeLabel.target_date <= as_of_date,
                OpportunityOutcomeLabel.horizon.in_(wanted),
                OpportunityOutcomeLabel.state == "pending",
            )
        ).all()
    out: dict[str, set[str]] = {}
    for target_date, symbol in rows:
        out.setdefault(target_date, set()).add(symbol)
    return dict(sorted(out.items()))


def due_outcome_symbols(
    target_date: str, session_factory=None, *, include_deferred: bool = False,
    horizons: Iterable[str] | None = None,
) -> set[str]:
    """Symbols with outcome rows due on one target trading date."""
    sf = session_factory or get_session_factory()
    states = ("pending", "deferred") if include_deferred else ("pending",)
    wanted = tuple(horizons or OUTCOME_HORIZONS)
    with sf() as db:
        rows = db.execute(
            select(OpportunityDecisionSnapshot.symbol)
            .join(OpportunityOutcomeLabel,
                  OpportunityOutcomeLabel.snapshot_id == OpportunityDecisionSnapshot.snapshot_id)
            .where(
                OpportunityOutcomeLabel.target_date == target_date,
                OpportunityOutcomeLabel.horizon.in_(wanted),
                OpportunityOutcomeLabel.state.in_(states),
            )
        ).scalars().all()
    return set(rows)


def _label_outcome_rows(
    rows: list[tuple[OpportunityOutcomeLabel, OpportunityDecisionSnapshot]],
    close_by_symbol: dict[str, float],
    price_basis_by_key: dict[tuple[str, str], tuple[float, str]],
) -> dict[str, int]:
    """Label rows on one explicit qfq price basis.

    ``reference_price`` is immutable raw realtime evidence. ``close_by_symbol`` is
    target-date qfq close. ``price_basis_by_key`` converts the raw reference onto
    decision-date qfq basis via ``qfq_daily_close / raw_daily_close``.
    """
    labeled = unknown = pending = 0
    for outcome, snapshot in rows:
        denominator_only = not _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
        reference = (
            outcome.reference_price if _positive_finite(outcome.reference_price)
            else snapshot.entry_price if _positive_finite(snapshot.entry_price)
            else None
        )
        try:
            evidence = json.loads(snapshot.evidence or "{}")
        except Exception:
            evidence = {}
        if denominator_only:
            fill_state = "not_actionable"
            fill_basis = (
                f"{snapshot.stage}:{snapshot.decision} 为全漏斗分母样本；"
                "只记录市场结果，不计可执行净收益"
            )
        else:
            fill_state, fill_basis = assess_fill_state(
                snapshot.symbol, _archived_change_pct(evidence)
            )
        outcome.fill_state = fill_state
        if not _positive_finite(reference):
            outcome.state = "unknown"
            outcome.label = "unknown"
            outcome.reason = f"{outcome.horizon} 决策时点价格缺失，不能计算收益"
            outcome.labeled_at = utcnow()
            unknown += 1
            continue

        basis = price_basis_by_key.get((snapshot.trade_date, snapshot.symbol))
        factor = basis[0] if basis else None
        basis_source = str(basis[1] or "") if basis else ""
        if not _positive_finite(factor):
            outcome.reason = (
                f"{fill_basis}；等待 {snapshot.trade_date}/{snapshot.symbol} "
                f"{PRICE_BASIS_VERSION} 价格基准（qfq日收/raw日收）"
            )
            pending += 1
            continue
        basis_reference = float(reference) * float(factor)
        if not _positive_finite(basis_reference):
            outcome.reason = f"{fill_basis}；价格基准换算后 reference 非有限正数"
            pending += 1
            continue

        close = close_by_symbol.get(snapshot.symbol)
        if not _positive_finite(close):
            outcome.reason = (
                f"{fill_basis}；等待 {outcome.horizon}@{outcome.target_date} "
                "有限且为正的 qfq 收盘价"
            )
            pending += 1
            continue

        ratio = float(close) / basis_reference
        ret = round((ratio - 1.0) * 100, 2)
        outcome.state = "labeled"
        outcome.reference_price = float(reference)
        outcome.basis_reference_price = basis_reference
        outcome.reference_adjustment_factor = float(factor)
        outcome.price_basis_version = PRICE_BASIS_VERSION
        outcome.price_basis_source = basis_source
        outcome.outcome_price = float(close)
        outcome.source = "qfq_close"
        outcome.return_pct = ret
        outcome.label = "positive" if ret >= 0 else ("flat" if ret >= -2.0 else "negative")
        if fill_state == "ok":
            # Convert the qfq total-return ratio back to raw-reference price scale for
            # fee/lot sizing; qfq nominal level itself must not change fee mechanics.
            economic_exit = float(reference) * ratio
            net, cost, _qty = round_trip_net_pct(float(reference), economic_exit)
            outcome.cost_pct = cost
            outcome.net_return_pct = net
            identity = "D0 同日成本调整代理" if outcome.horizon == OUTCOME_HORIZON else "跨日 reference 成本调整代理"
            outcome.reason = (
                f"{outcome.horizon}@{outcome.target_date}：raw reference {float(reference):.4f} "
                f"× qfq因子 {float(factor):.8f} → basis {basis_reference:.4f}；"
                f"qfq 收盘 {float(close):.4f}，毛 {ret:+.2f}%，{identity} {net:+.2f}%"
                f"（{cost:.4f}%，{COST_MODEL_VERSION}；{PRICE_BASIS_VERSION}；非 shadow fill）"
            )
        else:
            outcome.cost_pct = None
            outcome.net_return_pct = None
            outcome.reason = (
                f"{outcome.horizon}@{outcome.target_date}：raw reference {float(reference):.4f} "
                f"× qfq因子 {float(factor):.8f} → basis {basis_reference:.4f}；"
                f"qfq 收盘 {float(close):.4f}，毛 {ret:+.2f}%；{fill_basis}；"
                f"{PRICE_BASIS_VERSION}"
            )
        outcome.labeled_at = utcnow()
        labeled += 1
    return {"labeled": labeled, "unknown": unknown, "pending": pending}


_REVISION_CLOSE_FIELDS = (
    "target_date", "state", "label", "reference_price", "outcome_price",
    "return_pct", "fill_state", "cost_pct", "net_return_pct", "reason",
    "source", "basis_reference_price", "reference_adjustment_factor",
    "price_basis_version", "price_basis_source", "labeled_at",
)


def _effective_outcome(base: OpportunityOutcomeLabel, revision: OpportunityOutcomeRevision | None):
    """Read-only base outcome with current close revision overlaid; path evidence stays base-owned."""
    if revision is None:
        return base
    values = {column.name: getattr(base, column.name) for column in OpportunityOutcomeLabel.__table__.columns}
    for field in _REVISION_CLOSE_FIELDS:
        values[field] = getattr(revision, field)
    values["cost_model_version"] = revision.cost_model_version
    values["effective_revision_version"] = revision.revision_version
    return SimpleNamespace(**values)


def _effective_outcome_pairs(db, trade_date: str):
    """Return effective close rows plus immutable base rows and applied current revisions."""
    base_pairs = db.execute(
        select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
        .join(
            OpportunityDecisionSnapshot,
            OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
        )
        .where(OpportunityDecisionSnapshot.trade_date == trade_date)
    ).all()
    revisions = db.execute(
        select(OpportunityOutcomeRevision)
        .join(
            OpportunityOutcomeLabel,
            OpportunityOutcomeLabel.id == OpportunityOutcomeRevision.base_outcome_id,
        )
        .join(
            OpportunityDecisionSnapshot,
            OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
        )
        .where(
            OpportunityDecisionSnapshot.trade_date == trade_date,
            OpportunityOutcomeRevision.revision_version == OUTCOME_REVISION_VERSION,
        )
    ).scalars().all()
    by_base = {revision.base_outcome_id: revision for revision in revisions}
    effective = [
        (_effective_outcome(base, by_base.get(base.id)), snapshot)
        for base, snapshot in base_pairs
    ]
    return effective, base_pairs, revisions


def backfill_outcome_revisions(
    trade_date: str,
    close_by_symbol: dict[str, float],
    price_basis_by_key: dict[tuple[str, str], tuple[float, str]],
    session_factory=None, *,
    horizons: Iterable[str] = (OUTCOME_HORIZON,),
    batch_size: int = 2000,
) -> dict:
    """Append current D0 close revisions without mutating base rows.

    Cross-day horizons intentionally require an explicit ``horizons`` override and
    a target-date-specific close map; callers must never reuse one day's closes
    across D1/D3/D5.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    sf = session_factory or get_session_factory()
    wanted = tuple(horizons)
    inserted = labeled = unknown = pending = 0
    last_id = 0
    while True:
        with sf() as db:
            already = exists(
                select(1).where(
                    OpportunityOutcomeRevision.base_outcome_id == OpportunityOutcomeLabel.id,
                    OpportunityOutcomeRevision.revision_version == OUTCOME_REVISION_VERSION,
                )
            )
            rows = db.execute(
                select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
                .join(
                    OpportunityDecisionSnapshot,
                    OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
                )
                .where(
                    OpportunityOutcomeLabel.id > last_id,
                    OpportunityDecisionSnapshot.trade_date == trade_date,
                    OpportunityOutcomeLabel.horizon.in_(wanted),
                    OpportunityOutcomeLabel.state == "labeled",
                    or_(
                        OpportunityOutcomeLabel.price_basis_version != PRICE_BASIS_VERSION,
                        and_(
                            OpportunityOutcomeLabel.fill_state == "ok",
                            ~OpportunityOutcomeLabel.reason.contains(COST_MODEL_VERSION),
                        ),
                    ),
                    ~already,
                )
                .order_by(OpportunityOutcomeLabel.id)
                .limit(batch_size)
            ).all()
            if not rows:
                break
            last_id = rows[-1][0].id
            for base, snapshot in rows:
                revision = OpportunityOutcomeRevision(
                    base_outcome_id=base.id,
                    snapshot_id=base.snapshot_id,
                    horizon=base.horizon,
                    target_date=base.target_date,
                    revision_version=OUTCOME_REVISION_VERSION,
                    state="pending",
                    label="unknown",
                    reference_price=base.reference_price,
                    fill_state="unknown",
                    reason="",
                    source="daily_close",
                )
                counts = _label_outcome_rows(
                    [(revision, snapshot)], close_by_symbol, price_basis_by_key
                )
                if revision.state == "pending":
                    pending += counts["pending"]
                    continue
                revision.cost_model_version = COST_MODEL_VERSION
                db.add(revision)
                inserted += 1
                labeled += counts["labeled"]
                unknown += counts["unknown"]
            db.commit()
    return {
        "trade_date": trade_date,
        "revision_version": OUTCOME_REVISION_VERSION,
        "inserted": inserted,
        "labeled": labeled,
        "unknown": unknown,
        "pending": pending,
    }


def label_due_outcomes(
    target_date: str, close_by_symbol: dict[str, float], session_factory=None, *,
    price_basis_by_key: dict[tuple[str, str], tuple[float, str]],
    include_deferred: bool = False, horizons: Iterable[str] = FUTURE_OUTCOME_HORIZONS,
) -> dict:
    """Label D1/D3/D5 rows due on an exact target trading date."""
    sf = session_factory or get_session_factory()
    states = ("pending", "deferred") if include_deferred else ("pending",)
    wanted = tuple(horizons)
    with sf() as db:
        rows = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(
                OpportunityOutcomeLabel.target_date == target_date,
                OpportunityOutcomeLabel.horizon.in_(wanted),
                OpportunityOutcomeLabel.state.in_(states),
            )
        ).all()
        counts = _label_outcome_rows(rows, close_by_symbol, price_basis_by_key)
        db.commit()
    return {"target_date": target_date, "horizons": list(wanted), **counts}


def pending_symbols(
    trade_date: str, session_factory=None, *, include_deferred: bool = False,
) -> set[str]:
    """Symbols awaiting outcomes; realtime callers exclude denominator-only deferred rows."""
    sf = session_factory or get_session_factory()
    states = ("pending", "deferred") if include_deferred else ("pending",)
    with sf() as db:
        rows = db.execute(
            select(OpportunityDecisionSnapshot.symbol)
            .join(OpportunityOutcomeLabel,
                  OpportunityOutcomeLabel.snapshot_id == OpportunityDecisionSnapshot.snapshot_id)
            .where(OpportunityDecisionSnapshot.trade_date == trade_date,
                   OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
                   OpportunityOutcomeLabel.state.in_(states))
        ).scalars().all()
    return set(rows)


def label_trade_date(
    trade_date: str, close_by_symbol: dict[str, float], session_factory=None, *,
    price_basis_by_key: dict[tuple[str, str], tuple[float, str]],
    include_deferred: bool = False, batch_size: int | None = None,
) -> dict:
    """Attach D0 qfq-basis labels; missing close/basis stays pending and retryable."""
    if batch_size is not None and batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    sf = session_factory or get_session_factory()
    states = ("pending", "deferred") if include_deferred else ("pending",)
    totals = {"labeled": 0, "unknown": 0, "pending": 0}
    last_id = 0
    while True:
        with sf() as db:
            stmt = (
                select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
                .join(
                    OpportunityDecisionSnapshot,
                    OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
                )
                .where(
                    OpportunityDecisionSnapshot.trade_date == trade_date,
                    OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
                    OpportunityOutcomeLabel.state.in_(states),
                )
            )
            if batch_size is not None:
                stmt = stmt.where(OpportunityOutcomeLabel.id > last_id).order_by(OpportunityOutcomeLabel.id).limit(batch_size)
            rows = db.execute(stmt).all()
            if not rows:
                break
            counts = _label_outcome_rows(rows, close_by_symbol, price_basis_by_key)
            for key in totals:
                totals[key] += counts[key]
            if batch_size is not None:
                last_id = rows[-1][0].id
            db.commit()
        if batch_size is None:
            break
    return {"trade_date": trade_date, **totals}


def learning_summary(trade_date: str, session_factory=None) -> dict:
    sf = session_factory or get_session_factory()
    with sf() as db:
        snapshots = db.execute(
            select(OpportunityDecisionSnapshot).where(
                OpportunityDecisionSnapshot.trade_date == trade_date
            )
        ).scalars().all()
        all_outcomes, base_outcomes, applied_revisions = _effective_outcome_pairs(db, trade_date)
        decision_runs = db.execute(
            select(OpportunityDecisionRun).where(
                OpportunityDecisionRun.trade_date == trade_date,
                OpportunityDecisionRun.scenario == "intraday_opportunity",
            ).order_by(OpportunityDecisionRun.as_of, OpportunityDecisionRun.run_id)
        ).scalars().all()
    outcomes = [pair for pair in all_outcomes if pair[0].horizon == OUTCOME_HORIZON]
    stage_counts = Counter(s.stage for s in snapshots)
    decision_counts = Counter(f"{s.stage}:{s.decision}" for s in snapshots)
    data_state_counts = Counter(s.data_state or "unknown" for s in snapshots)
    run_data_state_counts = Counter(run.data_state or "unknown" for run in decision_runs)
    unready_runs = [run for run in decision_runs if run.data_state != "ready"]
    state_counts = Counter(o.state for o, _snapshot in outcomes)
    fill_counts = Counter(o.fill_state for o, _snapshot in outcomes)
    price_basis_counts = Counter(
        o.price_basis_version or "legacy_unversioned" for o, _snapshot in outcomes
    )
    # KB 引用状态分布（蓝图 §5）：把「KB 尚未接入选股运行时」这件事**变成可读出的数**，
    # 而不是靠读代码推断——现状应全为 `not_consulted`（+ 迁移前的 `legacy`）。
    kb_ref_counts = Counter(_kb_ref_state(s.kb_refs) for s in snapshots)
    selected_outcomes = [
        (outcome, snapshot) for outcome, snapshot in outcomes
        if _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
    ]
    eligible = len(selected_outcomes)
    labeled = sum(1 for outcome, _snapshot in selected_outcomes if outcome.state == "labeled")
    funnel_symbols = {row.symbol for row in snapshots}
    outcome_symbols = {snapshot.symbol for _outcome, snapshot in outcomes}
    labeled_symbols = {
        snapshot.symbol for outcome, snapshot in outcomes
        if outcome.state == "labeled" and _finite_number(outcome.return_pct)
    }
    missing_outcome_symbols = sorted(funnel_symbols - outcome_symbols)
    unlabeled_symbols = sorted(funnel_symbols - labeled_symbols)
    funnel_opportunities = {(row.run_id, row.symbol) for row in snapshots}
    unready_funnel_opportunities = {
        (row.run_id, row.symbol) for row in snapshots if row.data_state != "ready"
    }
    ready_funnel_opportunities = funnel_opportunities - unready_funnel_opportunities
    outcome_opportunities = {(snapshot.run_id, snapshot.symbol) for _outcome, snapshot in outcomes}
    labeled_opportunities = {
        (snapshot.run_id, snapshot.symbol) for outcome, snapshot in outcomes
        if outcome.state == "labeled" and _finite_number(outcome.return_pct)
    }
    current_basis_labeled_opportunities = {
        (snapshot.run_id, snapshot.symbol) for outcome, snapshot in outcomes
        if outcome.state == "labeled"
        and outcome.price_basis_version == PRICE_BASIS_VERSION
        and _finite_number(outcome.return_pct)
    }
    # Path denominator comes from immutable selected snapshots, not the outcome
    # join, so missing outcome attachment remains visible as missing coverage.
    selected_path_opportunities = {
        (snapshot.run_id, snapshot.symbol) for snapshot in snapshots
        if _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
    }
    labeled_path_opportunities = {
        (snapshot.run_id, snapshot.symbol) for outcome, snapshot in outcomes
        if _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
        and outcome.path_state == "labeled"
        and outcome.path_version == PATH_VERSION
    }
    path_state_counts = Counter(outcome.path_state for outcome, _snapshot in outcomes)
    current_path_state_counts = Counter(
        outcome.path_state for outcome, _snapshot in outcomes
        if outcome.path_version == PATH_VERSION
    )
    path_version_counts = Counter(
        outcome.path_version or "legacy_unversioned" for outcome, _snapshot in outcomes
    )
    limit_state_counts = Counter(outcome.limit_state for outcome, _snapshot in outcomes)
    horizon_coverage: dict[str, dict] = {}
    for horizon in OUTCOME_HORIZONS:
        horizon_rows = [pair for pair in all_outcomes if pair[0].horizon == horizon]
        horizon_labeled = {
            (snapshot.run_id, snapshot.symbol) for outcome, snapshot in horizon_rows
            if outcome.state == "labeled" and _finite_number(outcome.return_pct)
        }
        horizon_current_basis = {
            (snapshot.run_id, snapshot.symbol) for outcome, snapshot in horizon_rows
            if outcome.state == "labeled"
            and outcome.price_basis_version == PRICE_BASIS_VERSION
            and _finite_number(outcome.return_pct)
        }
        horizon_path_version = (
            PATH_VERSION if horizon == OUTCOME_HORIZON else CROSS_DAY_PATH_VERSION
        )
        # Path denominator must come from immutable selected snapshots, not the
        # horizon outcome join; otherwise a missing D1/D3/D5 identity disappears
        # from both numerator and denominator and inflates coverage.
        horizon_selected = set(selected_path_opportunities)
        horizon_path_attached = {
            (snapshot.run_id, snapshot.symbol) for _outcome, snapshot in horizon_rows
            if _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
        }
        horizon_path_labeled = {
            (snapshot.run_id, snapshot.symbol) for outcome, snapshot in horizon_rows
            if _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
            and outcome.path_state == "labeled"
            and outcome.path_version == horizon_path_version
            and _finite_number(outcome.mfe_pct)
            and _finite_number(outcome.mae_pct)
        }
        horizon_coverage[horizon] = {
            "rows": len(horizon_rows),
            "states": dict(sorted(Counter(outcome.state for outcome, _snapshot in horizon_rows).items())),
            "price_basis_versions": dict(sorted(Counter(
                outcome.price_basis_version or "legacy_unversioned"
                for outcome, _snapshot in horizon_rows
            ).items())),
            "target_dates": sorted({outcome.target_date for outcome, _snapshot in horizon_rows}),
            "labeled_opportunities": len(horizon_labeled),
            "current_basis_labeled_opportunities": len(horizon_current_basis),
            "opportunity_label_coverage": (
                round(len(horizon_labeled) / len(funnel_opportunities), 4)
                if funnel_opportunities else None
            ),
            "path": {
                "version": horizon_path_version,
                "states": dict(sorted(Counter(
                    outcome.path_state for outcome, snapshot in horizon_rows
                    if _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
                ).items())),
                "selected_opportunities": len(horizon_selected),
                "outcome_attached": len(horizon_path_attached),
                "labeled_selected_opportunities": len(horizon_path_labeled),
                "coverage": (
                    round(len(horizon_path_labeled) / len(horizon_selected), 4)
                    if horizon_selected else None
                ),
            },
        }
    return {
        "trade_date": trade_date,
        "snapshots": len(snapshots),
        "runs": len({s.run_id for s in snapshots}),
        "stages": {stage: stage_counts.get(stage, 0) for stage in STAGES},
        "decisions": dict(sorted(decision_counts.items())),
        "outcomes": dict(sorted(state_counts.items())),
        # 可成交性分布：`sealed` / `no_quote` 都不进 D0 成本调整代理样本；必须可见，
        # 否则「代理样本变少」会被误读成「机会变少」。
        "fill_states": dict(sorted(fill_counts.items())),
        "kb_ref_states": dict(sorted(kb_ref_counts.items())),
        "cost_model": COST_MODEL_VERSION,
        "run_ledger": {
            "runs": len(decision_runs),
            "zero_record_runs": sum(1 for run in decision_runs if run.records_total == 0),
            "data_states": dict(sorted(run_data_state_counts.items())),
            "latest_as_of": (
                decision_runs[-1].as_of.isoformat() if decision_runs and decision_runs[-1].as_of else None
            ),
            "latest_run_id": decision_runs[-1].run_id if decision_runs else None,
        },
        "evidence_quality": {
            "required_state_for_effect": "ready",
            "states": dict(sorted(data_state_counts.items())),
            "snapshot_rows": len(snapshots),
            "decision_runs": len(decision_runs),
            "zero_record_runs": sum(1 for run in decision_runs if run.records_total == 0),
            "run_states": dict(sorted(run_data_state_counts.items())),
            "unready_runs": len(unready_runs),
            "run_symbol_opportunities": len(funnel_opportunities),
            "ready_run_symbol_opportunities": len(ready_funnel_opportunities),
            "unready_run_symbol_opportunities": (
                len(funnel_opportunities) - len(ready_funnel_opportunities)
            ),
            "ready_opportunity_coverage": (
                round(len(ready_funnel_opportunities) / len(funnel_opportunities), 4)
                if funnel_opportunities else None
            ),
            "complete": (
                bool(funnel_opportunities or decision_runs)
                and len(ready_funnel_opportunities) == len(funnel_opportunities)
                and not unready_runs
            ),
        },
        "price_basis": {
            "current_version": PRICE_BASIS_VERSION,
            "revision_version": OUTCOME_REVISION_VERSION,
            "revisions_applied": sum(
                1 for revision in applied_revisions if revision.horizon == OUTCOME_HORIZON
            ),
            "versions": dict(sorted(price_basis_counts.items())),
            "base_versions": dict(sorted(Counter(
                base.price_basis_version or "legacy_unversioned"
                for base, _snapshot in base_outcomes
                if base.horizon == OUTCOME_HORIZON
            ).items())),
            "current_basis_labeled_opportunities": len(current_basis_labeled_opportunities),
            "current_basis_opportunity_coverage": (
                round(len(current_basis_labeled_opportunities) / len(funnel_opportunities), 4)
                if funnel_opportunities else None
            ),
        },
        # compatibility: historical field is row-level coverage among rows that
        # already have an outcome identity, not full-funnel denominator coverage.
        "label_coverage": round(labeled / eligible, 4) if eligible else None,
        "label_coverage_scope": "selected_outcome_rows_legacy",
        "d0_path": {
            "states": dict(sorted(path_state_counts.items())),
            "current_version_states": dict(sorted(current_path_state_counts.items())),
            "versions": dict(sorted(path_version_counts.items())),
            "limit_states": dict(sorted(limit_state_counts.items())),
            "selected_opportunities": len(selected_path_opportunities),
            "labeled_selected_opportunities": len(labeled_path_opportunities),
            "selected_path_coverage": (
                round(len(labeled_path_opportunities) / len(selected_path_opportunities), 4)
                if selected_path_opportunities else None
            ),
            "version": PATH_VERSION,
        },
        "horizon_coverage": horizon_coverage,
        "funnel_denominator": {
            "snapshot_rows": len(snapshots),
            "symbols": len(funnel_symbols),
            "outcome_rows": len(outcomes),
            "outcome_symbols": len(outcome_symbols),
            "labeled_symbols": len(labeled_symbols),
            "outcome_attachment_coverage": (
                round(len(outcome_symbols) / len(funnel_symbols), 4) if funnel_symbols else None
            ),
            "label_coverage": (
                round(len(labeled_symbols) / len(funnel_symbols), 4) if funnel_symbols else None
            ),
            "missing_outcome_symbols": missing_outcome_symbols,
            "unlabeled_symbols": unlabeled_symbols,
            "run_symbol_opportunities": len(funnel_opportunities),
            "outcome_opportunities": len(outcome_opportunities),
            "labeled_opportunities": len(labeled_opportunities),
            "current_basis_labeled_opportunities": len(current_basis_labeled_opportunities),
            "current_basis_version": PRICE_BASIS_VERSION,
            "current_basis_opportunity_coverage": (
                round(len(current_basis_labeled_opportunities) / len(funnel_opportunities), 4)
                if funnel_opportunities else None
            ),
            "opportunity_label_coverage": (
                round(len(labeled_opportunities) / len(funnel_opportunities), 4)
                if funnel_opportunities else None
            ),
        },
        "note": (
            "label_coverage 为兼容旧接口的 outcome-row 行级覆盖率；全漏斗必须看 funnel_denominator。"
            "样本不足、全漏斗标签未完整或 evidence_quality 非完整时只报告覆盖率与事实分布，不据此晋级策略；"
            "degraded/unavailable/unknown 决策事实保留审计但不进入效果样本；D0 成本调整代理口径见 cost_model；"
            "kb_ref_states 记录本次决策的 KB 引用状态（not_consulted=未引用，现状如此；"
            "KB 进入个股收益打分须先过有/无 KB 消融，见蓝图 §5）"
        ),
    }


#: 判定策略是否可讨论的**样本下限**。低于此值一律只报事实、不出结论——
#: 把账本「样本不足不得转正」从注释文案**落成代码门禁**（此前该纪律无任何强制力）。
MIN_LABELS_FOR_VERDICT = 30


def opportunity_scorecard(
    trade_date: str, top_k: int = 5, session_factory=None, *,
    run_id: str | None = None, horizon: str = OUTCOME_HORIZON,
    strategy_version: str = STRATEGY_VERSION, feature_version: str = FEATURE_VERSION,
) -> dict:
    """Return a denominator-safe scorecard for one declared metric identity.

    Raw outcome rows remain append-only audit evidence.  Statistical samples are
    de-duplicated to one **symbol × trade_date** observation using the earliest
    labeled observation (then best rank as a deterministic tie-break).  This
    prevents refreshes/stages/themes from inflating independent sample counts.

    Precision@K is a different denominator: one rank run/as-of only, de-duplicated
    by symbol *before* K is cut.  If ``run_id`` is omitted, the latest eligible
    rank run is selected and returned explicitly.

    Every horizon remains reference-price proxy evidence rather than actual shadow-fill return.
    ``d0_close`` additionally violates A-share T+1 same-day exit.
    """
    sf = session_factory or get_session_factory()
    with sf() as db:
        all_snapshots = db.execute(
            select(OpportunityDecisionSnapshot).where(
                OpportunityDecisionSnapshot.trade_date == trade_date
            )
        ).scalars().all()
        all_rows, base_rows, applied_revisions = _effective_outcome_pairs(db, trade_date)
        scorecard_runs = db.execute(
            select(OpportunityDecisionRun).where(
                OpportunityDecisionRun.trade_date == trade_date,
                OpportunityDecisionRun.scenario == "intraday_opportunity",
                OpportunityDecisionRun.strategy_version == strategy_version,
                OpportunityDecisionRun.feature_version == feature_version,
            ).order_by(OpportunityDecisionRun.as_of, OpportunityDecisionRun.run_id)
        ).scalars().all()
    funnel_snapshots = [
        snapshot for snapshot in all_snapshots
        if snapshot.strategy_version == strategy_version
        and snapshot.feature_version == feature_version
    ]
    rows = [
        pair for pair in all_rows
        if pair[0].horizon == horizon
        and pair[1].strategy_version == strategy_version
        and pair[1].feature_version == feature_version
    ]

    stage_order = {"rank": 0, "notification": 1, "hard_gate": 2, "candidate": 3}

    def snapshot_key(snapshot):
        return (
            snapshot.as_of or datetime.max,
            stage_order.get(snapshot.stage, 99),
            snapshot.rank if snapshot.rank is not None else 10**9,
            snapshot.id or 0,
        )

    def row_key(pair):
        return snapshot_key(pair[1])

    def cost_proxy(pair) -> float | None:
        outcome, snapshot = pair
        if snapshot.data_state != "ready":
            return None
        if outcome.price_basis_version != PRICE_BASIS_VERSION:
            return None
        if outcome.state != "labeled" or not _finite_number(outcome.return_pct):
            return None
        if outcome.fill_state != "ok" or not _finite_number(outcome.net_return_pct):
            return None
        # The schema predates a dedicated row-level cost-version column.  New labels
        # persist the version token in ``reason``; rows that cannot prove the current
        # version are excluded rather than silently reinterpreted under today's fees.
        if not _has_current_cost_model(outcome):
            return None
        return float(outcome.net_return_pct)

    state_counts = Counter(outcome.state for outcome, _snapshot in rows)
    raw_stage_counts = Counter(snapshot.stage for _outcome, snapshot in rows)
    data_state_counts = Counter(snapshot.data_state or "unknown" for snapshot in funnel_snapshots)
    price_basis_counts = Counter(
        outcome.price_basis_version or "legacy_unversioned" for outcome, _snapshot in rows
    )
    labeled_rows = [pair for pair in rows if pair[0].state == "labeled"]
    current_basis_labeled_rows = [
        pair for pair in labeled_rows
        if pair[0].price_basis_version == PRICE_BASIS_VERSION
    ]
    metric_eligible_labeled_rows = [
        pair for pair in current_basis_labeled_rows
        if pair[1].data_state == "ready"
    ]
    invalid_metric_rows = sum(
        1 for outcome, _snapshot in labeled_rows
        if not _finite_number(outcome.return_pct)
        or (outcome.net_return_pct is not None and not _finite_number(outcome.net_return_pct))
    )
    # Performance metrics remain selection-conditioned; denominator-only rejected/unknown
    # rows are labeled for missed-opportunity analysis but never silently enter fill/net metrics.
    selected_labeled_rows = [
        pair for pair in metric_eligible_labeled_rows
        if _selected_outcome_snapshot(pair[1].stage, pair[1].decision)
    ]
    valid_signal_rows = [
        pair for pair in selected_labeled_rows if _finite_number(pair[0].return_pct)
    ]

    funnel_symbols = {snapshot.symbol for snapshot in funnel_snapshots}
    outcome_symbols = {snapshot.symbol for _outcome, snapshot in rows}
    labeled_funnel_symbols = {
        snapshot.symbol for outcome, snapshot in rows
        if outcome.state == "labeled"
        and outcome.price_basis_version == PRICE_BASIS_VERSION
        and _finite_number(outcome.return_pct)
    }
    missing_outcome_symbols = sorted(funnel_symbols - outcome_symbols)
    unlabeled_funnel_symbols = sorted(funnel_symbols - labeled_funnel_symbols)
    funnel_opportunities = {(snapshot.run_id, snapshot.symbol) for snapshot in funnel_snapshots}
    unready_funnel_opportunities = {
        (snapshot.run_id, snapshot.symbol) for snapshot in funnel_snapshots
        if snapshot.data_state != "ready"
    }
    ready_funnel_opportunities = funnel_opportunities - unready_funnel_opportunities
    run_data_state_counts = Counter(run.data_state or "unknown" for run in scorecard_runs)
    unready_runs = [run for run in scorecard_runs if run.data_state != "ready"]
    evidence_quality_complete = (
        bool(funnel_opportunities or scorecard_runs)
        and not unready_funnel_opportunities
        and not unready_runs
    )
    outcome_opportunities = {(snapshot.run_id, snapshot.symbol) for _outcome, snapshot in rows}
    labeled_opportunities = {
        (snapshot.run_id, snapshot.symbol) for outcome, snapshot in rows
        if outcome.state == "labeled"
        and outcome.price_basis_version == PRICE_BASIS_VERSION
        and _finite_number(outcome.return_pct)
    }
    denominator_complete = bool(funnel_opportunities) and not (funnel_opportunities - labeled_opportunities)
    denominator_state = (
        "empty" if not funnel_opportunities
        else "complete" if denominator_complete
        else "incomplete"
    )

    # Independent day-level sample: one stock cannot become N samples merely because
    # it crossed stages/themes or the endpoint refreshed repeatedly.  Keep every raw
    # row in ``audit``; only the metric denominator is de-duplicated.
    sample_by_symbol: dict[str, tuple] = {}
    for pair in sorted(valid_signal_rows, key=row_key):
        sample_by_symbol.setdefault(pair[1].symbol, pair)
    samples = list(sample_by_symbol.values())

    # Path evidence has its own denominator. Define the sample from immutable
    # selected snapshots *before* joining outcomes so a missing outcome row cannot
    # disappear from coverage. The identity is the earliest selected decision for
    # each symbol/trade_date; later refreshes must never substitute for a missing
    # earliest outcome and make coverage look better.
    path_snapshot_by_symbol: dict[str, OpportunityDecisionSnapshot] = {}
    for snapshot in sorted(
        [
            snapshot for snapshot in funnel_snapshots
            if _selected_outcome_snapshot(snapshot.stage, snapshot.decision)
        ],
        key=snapshot_key,
    ):
        path_snapshot_by_symbol.setdefault(snapshot.symbol, snapshot)
    path_audit_candidate_snapshots = list(path_snapshot_by_symbol.values())
    path_candidate_snapshots = [
        snapshot for snapshot in path_audit_candidate_snapshots
        if snapshot.data_state == "ready"
    ]
    path_outcome_by_snapshot_id = {
        snapshot.snapshot_id: outcome for outcome, snapshot in rows
    }
    path_candidates = [
        (path_outcome_by_snapshot_id[snapshot.snapshot_id], snapshot)
        for snapshot in path_candidate_snapshots
        if snapshot.snapshot_id in path_outcome_by_snapshot_id
    ]
    path_version = PATH_VERSION if horizon == OUTCOME_HORIZON else CROSS_DAY_PATH_VERSION
    path_samples = [
        outcome for outcome, _snapshot in path_candidates
        if outcome.path_state == "labeled"
        and outcome.path_version == path_version
        and _finite_number(outcome.mfe_pct) and _finite_number(outcome.mae_pct)
    ]
    path_limit_states = Counter(outcome.limit_state for outcome in path_samples)
    limit_hits = [
        outcome for outcome in path_samples
        if outcome.limit_state == "hit" and _finite_number(outcome.time_to_limit_minutes)
    ]
    ever_limit_hits = sum(
        path_limit_states.get(state, 0)
        for state in ("hit", "preexisting", "hit_time_unknown")
    )
    limit_membership_evaluable = len(path_samples) - path_limit_states.get("unknown", 0)
    gross = [float(outcome.return_pct) for outcome, _snapshot in samples]
    proxy_pairs = [(pair, cost_proxy(pair)) for pair in samples]
    proxy_pairs = [(pair, value) for pair, value in proxy_pairs if value is not None]
    proxy = [value for _pair, value in proxy_pairs]
    gross_on_proxy = [float(pair[0].return_pct) for pair, _value in proxy_pairs]
    not_fillable = sum(1 for outcome, _snapshot in samples if outcome.fill_state != "ok")
    price_basis_excluded_rows = sum(
        1 for outcome, _snapshot in labeled_rows
        if outcome.price_basis_version != PRICE_BASIS_VERSION
    )
    cost_version_excluded = sum(
        1 for outcome, _snapshot in samples
        if outcome.fill_state == "ok" and _finite_number(outcome.net_return_pct)
        and not _has_current_cost_model(outcome)
    )

    # Stage diagnostics use a different, layered denominator: one symbol per stage/day.
    # This preserves funnel-stage visibility while preventing refresh/theme duplicates inside a stage.
    stage_sample_by_key: dict[tuple[str, str], tuple] = {}
    for pair in sorted(valid_signal_rows, key=row_key):
        snapshot = pair[1]
        stage_sample_by_key.setdefault((snapshot.stage, snapshot.symbol), pair)

    by_stage: dict[str, dict] = {}
    for pair in stage_sample_by_key.values():
        outcome, snapshot = pair
        value = cost_proxy(pair)
        bucket = by_stage.setdefault(
            snapshot.stage,
            {"labels": 0, "fillable": 0, "net_sum": 0.0, "net_positive": 0},
        )
        bucket["labels"] += 1
        if value is not None:
            bucket["fillable"] += 1
            bucket["net_sum"] += value
            bucket["net_positive"] += 1 if value > 0 else 0
    for bucket in by_stage.values():
        value = (
            round(bucket.pop("net_sum") / bucket["fillable"], 2)
            if bucket["fillable"] else None
        )
        metric_key = (
            "cost_adjusted_d0_proxy_expectancy_pct"
            if horizon == OUTCOME_HORIZON else "cost_adjusted_reference_proxy_expectancy_pct"
        )
        bucket[metric_key] = value
        bucket["net_expectancy_pct"] = value  # compatibility alias; see metric_identity

    # Precision@K: first choose exactly one run, then unique symbols, then cut K.
    rank_rows = [
        pair for pair in rows
        if pair[1].stage == "rank" and pair[1].decision == "ranked"
        and pair[1].rank is not None
    ]
    selected_run_id = run_id
    if selected_run_id is None and rank_rows:
        selected_run_id = max(
            rank_rows, key=lambda pair: (pair[1].as_of or datetime.min, pair[1].run_id)
        )[1].run_id
    selected_run_rows = [pair for pair in rank_rows if pair[1].run_id == selected_run_id]
    unique_ranked: dict[str, tuple] = {}
    for pair in sorted(
        selected_run_rows,
        key=lambda pair: (pair[1].rank, pair[1].symbol, pair[1].id or 0),
    ):
        unique_ranked.setdefault(pair[1].symbol, pair)
    selected = list(unique_ranked.values())[:max(1, int(top_k))]
    evaluable = [value for pair in selected if (value := cost_proxy(pair)) is not None]
    run_as_of = max(
        (snapshot.as_of for _outcome, snapshot in selected_run_rows if snapshot.as_of is not None),
        default=None,
    )

    verdict = (
        "no_matching_denominator" if denominator_state == "empty"
        else "incomplete_denominator" if denominator_state == "incomplete"
        else "degraded_input" if not evidence_quality_complete
        else "insufficient_sample" if len(proxy) < MIN_LABELS_FOR_VERDICT
        else "cost_proxy_positive_observed"
        if sum(proxy) / len(proxy) > 0 else "cost_proxy_nonpositive_observed"
    )
    proxy_mean = round(sum(proxy) / len(proxy), 2) if proxy else None
    gross_proxy_mean = round(sum(gross_on_proxy) / len(gross_on_proxy), 2) if gross_on_proxy else None
    identity_note = (
        "D0 收盘衡量信号方向；成本调整值仍是同日收盘代理。A 股 T+1 禁止当日买入当日卖出，"
        "因此不是可实现净收益；合法 D1/D3/D5 等交易标签由 RSH-026 后续版本化补齐。"
        if horizon == OUTCOME_HORIZON else
        f"{horizon} 使用决策时点 reference 到目标交易日收盘的结果；虽满足 T+1 时间约束，"
        "仍不是实际 shadow fill 净收益，只能称成本调整 reference 代理。"
    )

    return {
        "trade_date": trade_date,
        "filters": {
            "horizon": horizon,
            "strategy_version": strategy_version,
            "feature_version": feature_version,
        },
        "cost_model": COST_MODEL_VERSION,
        "metric_identity": {
            "horizon": horizon,
            "sample_unit": "symbol_trade_date",
            "gross_metric": "signal_direction_return_pct",
            "cost_adjusted_metric": (
                "cost_adjusted_d0_proxy_pct" if horizon == OUTCOME_HORIZON
                else "cost_adjusted_reference_proxy_pct"
            ),
            "realizable_return": False,
            "cost_model_version": COST_MODEL_VERSION,
            "price_basis_version": PRICE_BASIS_VERSION,
            "note": identity_note,
        },
        "audit": {
            "date_outcome_rows": len(all_rows),
            "outcome_rows": len(rows),
            "filter_excluded_rows": len(all_rows) - len(rows),
            "labeled_rows": len(labeled_rows),
            "states": dict(sorted(state_counts.items())),
            "runs": len({snapshot.run_id for _outcome, snapshot in rows}),
            "opportunities": len({(snapshot.run_id, snapshot.symbol) for _outcome, snapshot in rows}),
            "symbols": len({snapshot.symbol for _outcome, snapshot in rows}),
            "by_stage_rows": dict(sorted(raw_stage_counts.items())),
            "data_states": dict(sorted(data_state_counts.items())),
            "metric_eligible_labeled_rows": len(metric_eligible_labeled_rows),
            "data_state_excluded_labeled_rows": (
                len(current_basis_labeled_rows) - len(metric_eligible_labeled_rows)
            ),
            "repeated_labeled_rows": max(0, len(valid_signal_rows) - len(samples)),
            "invalid_metric_rows": invalid_metric_rows,
            "price_basis_versions": dict(sorted(price_basis_counts.items())),
            "base_price_basis_versions": dict(sorted(Counter(
                base.price_basis_version or "legacy_unversioned"
                for base, snapshot in base_rows
                if base.horizon == horizon
                and snapshot.strategy_version == strategy_version
                and snapshot.feature_version == feature_version
            ).items())),
            "outcome_revision_version": OUTCOME_REVISION_VERSION,
            "outcome_revisions_applied": len([
                revision for revision in applied_revisions if revision.horizon == horizon
            ]),
            "price_basis_excluded_labeled_rows": price_basis_excluded_rows,
            "cost_version_excluded_samples": cost_version_excluded,
        },
        "sample": {
            "unit": "symbol_trade_date",
            "policy": "earliest_labeled_observation_then_best_rank",
            "data_state_required": "ready",
            "count": len(samples),
            "symbols": sorted(sample_by_symbol),
        },
        "denominators": {
            "audit_rows": len(rows),
            "runs": len({snapshot.run_id for _outcome, snapshot in rows}),
            "run_symbol_opportunities": len({
                (snapshot.run_id, snapshot.symbol) for _outcome, snapshot in rows
            }),
            "symbols": len({snapshot.symbol for _outcome, snapshot in rows}),
            "symbol_trade_date_samples": len(samples),
        },
        "evidence_quality": {
            "required_state": "ready",
            "states": dict(sorted(data_state_counts.items())),
            "snapshot_rows": len(funnel_snapshots),
            "decision_runs": len(scorecard_runs),
            "zero_record_runs": sum(1 for run in scorecard_runs if run.records_total == 0),
            "run_states": dict(sorted(run_data_state_counts.items())),
            "unready_runs": len(unready_runs),
            "run_symbol_opportunities": len(funnel_opportunities),
            "ready_run_symbol_opportunities": len(ready_funnel_opportunities),
            "unready_run_symbol_opportunities": len(unready_funnel_opportunities),
            "ready_opportunity_coverage": (
                round(len(ready_funnel_opportunities) / len(funnel_opportunities), 4)
                if funnel_opportunities else None
            ),
            "complete": evidence_quality_complete,
        },
        "funnel_denominator": {
            "snapshot_rows": len(funnel_snapshots),
            "symbols": len(funnel_symbols),
            "outcome_symbols": len(outcome_symbols),
            "labeled_symbols": len(labeled_funnel_symbols),
            "outcome_attachment_coverage": (
                round(len(outcome_symbols) / len(funnel_symbols), 4) if funnel_symbols else None
            ),
            "label_coverage": (
                round(len(labeled_funnel_symbols) / len(funnel_symbols), 4) if funnel_symbols else None
            ),
            "complete": denominator_complete,
            "state": denominator_state,
            "price_basis_version": PRICE_BASIS_VERSION,
            "data_state_required_for_effect": "ready",
            "data_state_counts": dict(sorted(data_state_counts.items())),
            "ready_opportunities": len(ready_funnel_opportunities),
            "unready_opportunities": len(unready_funnel_opportunities),
            "evidence_quality_complete": evidence_quality_complete,
            "decision_runs": len(scorecard_runs),
            "zero_record_runs": sum(1 for run in scorecard_runs if run.records_total == 0),
            "run_data_state_counts": dict(sorted(run_data_state_counts.items())),
            "missing_outcome_symbols": missing_outcome_symbols,
            "unlabeled_symbols": unlabeled_funnel_symbols,
            "run_symbol_opportunities": len(funnel_opportunities),
            "outcome_opportunities": len(outcome_opportunities),
            "labeled_opportunities": len(labeled_opportunities),
            "opportunity_label_coverage": (
                round(len(labeled_opportunities) / len(funnel_opportunities), 4)
                if funnel_opportunities else None
            ),
        },
        "min_labels_for_verdict": MIN_LABELS_FOR_VERDICT,
        "labeled": len(samples),
        "fillable": len(proxy),
        "not_fillable": not_fillable,
        "expectancy": {
            "gross_pct": round(sum(gross) / len(gross), 2) if gross else None,
            "gross_on_fillable_pct": gross_proxy_mean,  # compatibility alias
            "net_pct": proxy_mean,  # compatibility alias; explicitly non-realizable above
            "gross_signal_pct": round(sum(gross) / len(gross), 2) if gross else None,
            "gross_on_cost_proxy_sample_pct": gross_proxy_mean,
            (
                "cost_adjusted_d0_proxy_pct"
                if horizon == OUTCOME_HORIZON else "cost_adjusted_reference_proxy_pct"
            ): proxy_mean,
            "gross_labels": len(gross),
            "fillable_labels": len(proxy),
            "net_pct_identity": "deprecated alias of cost_adjusted_d0_proxy_pct; not realizable",
        },
        "path_metrics": (
            {
                "available": True,
                "identity": (
                    "D0 post-decision Tencent 1m through 15:00; decision minute excluded; "
                    "independent of close-label availability; not shadow-fill P&L"
                ),
                "version": PATH_VERSION,
                "sample_unit": "symbol_trade_date_earliest_selected_decision",
                "data_state_required": "ready",
                "audit_denominator": len(path_audit_candidate_snapshots),
                "data_state_excluded": (
                    len(path_audit_candidate_snapshots) - len(path_candidate_snapshots)
                ),
                "denominator": len(path_candidate_snapshots),
                "outcome_attached": len(path_candidates),
                "evaluable": len(path_samples),
                "coverage": (
                    round(len(path_samples) / len(path_candidate_snapshots), 4)
                    if path_candidate_snapshots else None
                ),
                "avg_mfe_pct": (
                    round(sum(float(o.mfe_pct) for o in path_samples) / len(path_samples), 2)
                    if path_samples else None
                ),
                "avg_mae_pct": (
                    round(sum(float(o.mae_pct) for o in path_samples) / len(path_samples), 2)
                    if path_samples else None
                ),
                "limit_states": dict(sorted(path_limit_states.items())),
                "limit_membership_evaluable": limit_membership_evaluable,
                "ever_limit_hits": ever_limit_hits,
                # Compatibility name retained for this unreleased slice; this is
                # specifically the subset with known post-decision first-seal time.
                "limit_hits_after_decision": len(limit_hits),
                "post_decision_timed_hits": len(limit_hits),
                "avg_time_to_limit_minutes": (
                    round(sum(float(o.time_to_limit_minutes) for o in limit_hits) / len(limit_hits), 2)
                    if limit_hits else None
                ),
            }
            if horizon == OUTCOME_HORIZON
            else {
                "available": True,
                "identity": (
                    f"{horizon} cumulative reference path: decision-safe D0 Tencent 1m + "
                    "complete same-source qfq daily high/low through exact target trading date; "
                    "missing intermediate market sessions fail closed; not shadow-fill P&L"
                ),
                "version": CROSS_DAY_PATH_VERSION,
                "sample_unit": "symbol_trade_date_earliest_selected_decision",
                "data_state_required": "ready",
                "audit_denominator": len(path_audit_candidate_snapshots),
                "data_state_excluded": (
                    len(path_audit_candidate_snapshots) - len(path_candidate_snapshots)
                ),
                "denominator": len(path_candidate_snapshots),
                "outcome_attached": len(path_candidates),
                "evaluable": len(path_samples),
                "coverage": (
                    round(len(path_samples) / len(path_candidate_snapshots), 4)
                    if path_candidate_snapshots else None
                ),
                "avg_mfe_pct": (
                    round(sum(float(o.mfe_pct) for o in path_samples) / len(path_samples), 2)
                    if path_samples else None
                ),
                "avg_mae_pct": (
                    round(sum(float(o.mae_pct) for o in path_samples) / len(path_samples), 2)
                    if path_samples else None
                ),
                "limit_metrics_available": False,
                "limit_hits_after_decision": None,
                "avg_time_to_limit_minutes": None,
            }
        ),
        "precision_at_k": {
            "run_id": selected_run_id,
            "as_of": run_as_of.isoformat() if run_as_of else None,
            "k_requested": max(1, int(top_k)),
            "k": len(selected),
            "selected": len(selected),
            "evaluable": len(evaluable),
            "data_state_counts": dict(sorted(Counter(
                snapshot.data_state or "unknown" for _outcome, snapshot in selected
            ).items())),
            "symbols": [snapshot.symbol for _outcome, snapshot in selected],
            "coverage": round(len(evaluable) / len(selected), 4) if selected else None,
            "observed": (
                round(sum(1 for value in evaluable if value > 0) / len(evaluable), 4)
                if evaluable else None
            ),
        },
        "by_stage": dict(sorted(by_stage.items())),
        "verdict": verdict,
        "note": (
            "只描述已归档事实，不构成买卖建议；独立样本按 symbol×trade_date 去重，原始行数保留在 audit。"
            "指定版本没有任何漏斗样本时 verdict=no_matching_denominator；有分母但尚有未标结果时 "
            "verdict=incomplete_denominator；分母完整但存在非 ready 决策事实时 verdict=degraded_input；"
            "只有输入质量完整后，可执行样本低于下限才为 insufficient_sample。"
            "degraded/unavailable/unknown 决策事实保留在 audit/分母，但不进入效果样本；"
            "legacy/unversioned 结果仅作审计，不进入当前 price-basis 指标；"
            "D0 成本调整值仅为同日收盘代理，不是 A 股 T+1 下可实现净收益；"
            "deferred/not_actionable 只服务漏选/失败分母，不进入可执行净收益。"
        ),
    }
