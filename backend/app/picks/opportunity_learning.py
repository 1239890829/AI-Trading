"""Replayable evidence and outcome labels for the stock-opportunity funnel.

Snapshots are append-only: later closes are written to a separate outcome table.
The archived evidence is sufficient to replay each deterministic stage without
calling a market-data provider or consulting today's mutable configuration.

**两种收益口径，勿混用（2026-09-16 `RSH-026` 第二批）**

- `return_pct` = **毛收益**：决策时点价 → D0 收盘，**不含成本**。它衡量的是
  「信号方向对不对」，**不是**可实现盈亏；且 A 股 T+1 ⇒ 当日买入当日不可卖，
  该收益在规则上**不可实现**。
- `net_return_pct` 是历史列名；对当前 `d0_close` 标签，它实际表示**成本调整 D0 代理**：
  决策时点价 → 同日收盘，再扣双边费用。A 股 T+1 禁止当日买入当日卖出，故它**不是可实现净收益**。
  仅在决策时点可成交（`fill_state == "ok"`）时给值；不可成交一律记 `None`，缺价不造 0。
- 成本费率**不在此另立**：取自 `app.paper.engine.calc_fee`（与 `paper/reconcile.py` 同源）；
  涨停幅度取自 `app.market.price_rules.limit_pct`（全仓单一实现）。
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select

from app.core.bjtime import beijing_now
from app.core.db import get_session_factory, utcnow
from app.market import price_rules
from app.models.opportunity_learning import OpportunityDecisionSnapshot, OpportunityOutcomeLabel
from app.paper.engine import calc_fee
from app.picks.kb_routing import snapshot_citations

STRATEGY_VERSION = "stock-opportunity-funnel-v2"
FEATURE_VERSION = "pit-evidence-v2"
OUTCOME_HORIZON = "d0_close"
STAGES = ("candidate", "hard_gate", "rank", "notification")

#: 成本口径版本。**变更费率假设或可成交判据时必须递增**——结果标签是 append-only 的，
#: 没有版本号就无法区分「策略变好」与「口径变松」。
COST_MODEL_VERSION = "cost-v1.notional-100k"

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


def archive_records(run_id: str, records: list[dict], session_factory=None) -> dict:
    """Persist a whole run atomically; rerunning the same run is idempotent."""
    sf = session_factory or get_session_factory()
    inserted = 0
    with sf() as db:
        existing = set(db.execute(
            select(OpportunityDecisionSnapshot.snapshot_id).where(
                OpportunityDecisionSnapshot.run_id == run_id
            )
        ).scalars().all())
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
                continue
            row = OpportunityDecisionSnapshot(
                snapshot_id=snapshot_id,
                evidence=_json(evidence),
                **{k: v for k, v in record.items() if k != "evidence"},
            )
            db.add(row)
            if record["stage"] in ("rank", "notification") and record["decision"] in (
                "ranked", "eligible", "notified", "suppressed",
            ):
                db.add(OpportunityOutcomeLabel(
                    snapshot_id=snapshot_id, horizon=OUTCOME_HORIZON,
                    target_date=record["trade_date"], state="pending", label="unknown",
                    reference_price=record.get("entry_price"), reason="等待收盘价",
                ))
            existing.add(snapshot_id)
            inserted += 1
        db.commit()
    return {"run_id": run_id, "inserted": inserted, "records": len(records)}


def archive_intraday_pipeline(payload: dict, *, trade_date: str, as_of: datetime | None = None,
                              kb_ids: Iterable[str] = (), session_factory=None) -> dict:
    run_id, records = build_intraday_records(
        payload, trade_date=trade_date, as_of=as_of or beijing_now(), kb_ids=kb_ids,
    )
    return archive_records(run_id, records, session_factory)


def archive_notification_pipeline(
    items: list[dict], *, trade_date: str, hits: list[dict], skips: list[dict],
    dispatch_by_symbol: dict[str, str], kb_ids: Iterable[str] = (),
    as_of: datetime | None = None, session_factory=None, pick_generated_at: str | None = None,
    execution_by_symbol: dict[str, dict] | None = None,
) -> dict:
    run_id, records = build_notification_records(
        items, trade_date=trade_date, as_of=as_of or beijing_now(), hits=hits, skips=skips,
        dispatch_by_symbol=dispatch_by_symbol, kb_ids=kb_ids,
        pick_generated_at=pick_generated_at, execution_by_symbol=execution_by_symbol,
    )
    return archive_records(run_id, records, session_factory)


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
    return {"run_id": run_id, "records": len(items), "mismatches": mismatches, "items": items}


def pending_symbols(trade_date: str, session_factory=None) -> set[str]:
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(OpportunityDecisionSnapshot.symbol)
            .join(OpportunityOutcomeLabel,
                  OpportunityOutcomeLabel.snapshot_id == OpportunityDecisionSnapshot.snapshot_id)
            .where(OpportunityDecisionSnapshot.trade_date == trade_date,
                   OpportunityOutcomeLabel.state == "pending")
        ).scalars().all()
    return set(rows)


def label_trade_date(trade_date: str, close_by_symbol: dict[str, float], session_factory=None) -> dict:
    """Attach D0 close labels; missing closes stay pending and can be retried.

    `return_pct` 是 D0 信号方向毛变化；历史列 `net_return_pct` 是同一 D0 窗口的
    成本调整代理，只在决策时点可成交（`fill_state == "ok"`）时给值。A 股 T+1 下
    两者都不是可实现交易收益；封板/无现价留 `None`，不造 0。
    """
    sf = session_factory or get_session_factory()
    labeled = unknown = pending = 0
    with sf() as db:
        rows = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.trade_date == trade_date,
                   OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
                   OpportunityOutcomeLabel.state == "pending")
        ).all()
        for outcome, snapshot in rows:
            reference = (
                outcome.reference_price if _positive_finite(outcome.reference_price)
                else snapshot.entry_price if _positive_finite(snapshot.entry_price)
                else None
            )
            try:
                evidence = json.loads(snapshot.evidence or "{}")
            except Exception:
                evidence = {}
            fill_state, fill_basis = assess_fill_state(
                snapshot.symbol, _archived_change_pct(evidence)
            )
            outcome.fill_state = fill_state
            if not _positive_finite(reference):
                outcome.state = "unknown"
                outcome.label = "unknown"
                outcome.reason = "决策时点价格缺失，不能计算收益"
                outcome.labeled_at = utcnow()
                unknown += 1
                continue
            close = close_by_symbol.get(snapshot.symbol)
            if not _positive_finite(close):
                # 缺失/NaN/Inf/非正收盘价都不是有效结果；保持 pending，允许后续用正确数据重试。
                outcome.reason = f"{fill_basis}；等待有限且为正的收盘价"
                pending += 1
                continue
            ret = round((float(close) / float(reference) - 1) * 100, 2)
            outcome.state = "labeled"
            outcome.reference_price = float(reference)
            outcome.outcome_price = float(close)
            outcome.return_pct = ret
            outcome.label = "positive" if ret >= 0 else ("flat" if ret >= -2.0 else "negative")
            if fill_state == "ok":
                net, cost, _qty = round_trip_net_pct(float(reference), float(close))
                outcome.cost_pct = cost
                outcome.net_return_pct = net
                outcome.reason = (
                    f"决策时点价至收盘毛 {ret:+.2f}%，扣双边成本净 {net:+.2f}%"
                    f"（{cost:.4f}%，{COST_MODEL_VERSION}）"
                )
            else:
                outcome.cost_pct = None
                outcome.net_return_pct = None
                outcome.reason = f"决策时点价至收盘毛 {ret:+.2f}%；{fill_basis}"
            outcome.labeled_at = utcnow()
            labeled += 1
        db.commit()
    return {"trade_date": trade_date, "labeled": labeled, "unknown": unknown, "pending": pending}


def learning_summary(trade_date: str, session_factory=None) -> dict:
    sf = session_factory or get_session_factory()
    with sf() as db:
        snapshots = db.execute(
            select(OpportunityDecisionSnapshot).where(
                OpportunityDecisionSnapshot.trade_date == trade_date
            )
        ).scalars().all()
        outcomes = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot.stage)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.trade_date == trade_date)
        ).all()
    stage_counts = Counter(s.stage for s in snapshots)
    decision_counts = Counter(f"{s.stage}:{s.decision}" for s in snapshots)
    state_counts = Counter(o.state for o, _stage in outcomes)
    fill_counts = Counter(o.fill_state for o, _stage in outcomes)
    # KB 引用状态分布（蓝图 §5）：把「KB 尚未接入选股运行时」这件事**变成可读出的数**，
    # 而不是靠读代码推断——现状应全为 `not_consulted`（+ 迁移前的 `legacy`）。
    kb_ref_counts = Counter(_kb_ref_state(s.kb_refs) for s in snapshots)
    eligible = len(outcomes)
    labeled = state_counts.get("labeled", 0)
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
        "label_coverage": round(labeled / eligible, 4) if eligible else None,
        "note": (
            "样本不足时仅报告覆盖率与事实分布，不据此晋级策略；D0 成本调整代理口径见 cost_model；"
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

    ``d0_close`` is signal-direction / cost-adjusted **proxy evidence**, not a
    realizable A-share return: T+1 forbids same-day exit.
    """
    sf = session_factory or get_session_factory()
    with sf() as db:
        all_rows = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            )
            .where(OpportunityDecisionSnapshot.trade_date == trade_date)
        ).all()
    rows = [
        pair for pair in all_rows
        if pair[0].horizon == horizon
        and pair[1].strategy_version == strategy_version
        and pair[1].feature_version == feature_version
    ]

    stage_order = {"rank": 0, "notification": 1, "hard_gate": 2, "candidate": 3}

    def row_key(pair):
        _outcome, snapshot = pair
        return (
            snapshot.as_of or datetime.max,
            stage_order.get(snapshot.stage, 99),
            snapshot.rank if snapshot.rank is not None else 10**9,
            snapshot.id or 0,
        )

    def cost_proxy(pair) -> float | None:
        outcome, _snapshot = pair
        if outcome.state != "labeled" or not _finite_number(outcome.return_pct):
            return None
        if outcome.fill_state != "ok" or not _finite_number(outcome.net_return_pct):
            return None
        # The schema predates a dedicated row-level cost-version column.  New labels
        # persist the version token in ``reason``; rows that cannot prove the current
        # version are excluded rather than silently reinterpreted under today's fees.
        if COST_MODEL_VERSION not in (outcome.reason or ""):
            return None
        return float(outcome.net_return_pct)

    state_counts = Counter(outcome.state for outcome, _snapshot in rows)
    raw_stage_counts = Counter(snapshot.stage for _outcome, snapshot in rows)
    labeled_rows = [pair for pair in rows if pair[0].state == "labeled"]
    invalid_metric_rows = sum(
        1 for outcome, _snapshot in labeled_rows
        if not _finite_number(outcome.return_pct)
        or (outcome.net_return_pct is not None and not _finite_number(outcome.net_return_pct))
    )
    valid_signal_rows = [
        pair for pair in labeled_rows if _finite_number(pair[0].return_pct)
    ]

    # Independent day-level sample: one stock cannot become N samples merely because
    # it crossed stages/themes or the endpoint refreshed repeatedly.  Keep every raw
    # row in ``audit``; only the metric denominator is de-duplicated.
    sample_by_symbol: dict[str, tuple] = {}
    for pair in sorted(valid_signal_rows, key=row_key):
        sample_by_symbol.setdefault(pair[1].symbol, pair)
    samples = list(sample_by_symbol.values())

    gross = [float(outcome.return_pct) for outcome, _snapshot in samples]
    proxy_pairs = [(pair, cost_proxy(pair)) for pair in samples]
    proxy_pairs = [(pair, value) for pair, value in proxy_pairs if value is not None]
    proxy = [value for _pair, value in proxy_pairs]
    gross_on_proxy = [float(pair[0].return_pct) for pair, _value in proxy_pairs]
    not_fillable = sum(1 for outcome, _snapshot in samples if outcome.fill_state != "ok")
    cost_version_excluded = sum(
        1 for outcome, _snapshot in samples
        if outcome.fill_state == "ok" and _finite_number(outcome.net_return_pct)
        and COST_MODEL_VERSION not in (outcome.reason or "")
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
        bucket["cost_adjusted_d0_proxy_expectancy_pct"] = value
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

    verdict = "insufficient_sample" if len(proxy) < MIN_LABELS_FOR_VERDICT else (
        "cost_proxy_positive_observed"
        if sum(proxy) / len(proxy) > 0 else "cost_proxy_nonpositive_observed"
    )
    proxy_mean = round(sum(proxy) / len(proxy), 2) if proxy else None
    gross_proxy_mean = round(sum(gross_on_proxy) / len(gross_on_proxy), 2) if gross_on_proxy else None
    identity_note = (
        "D0 收盘衡量信号方向；成本调整值仍是同日收盘代理。A 股 T+1 禁止当日买入当日卖出，"
        "因此不是可实现净收益；合法 D1/D3/D5 等交易标签由 RSH-026 后续版本化补齐。"
        if horizon == OUTCOME_HORIZON else
        "该 horizon 仅按归档标签身份统计；BUG-026 不把它自动认定为可实现交易收益。"
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
                else "cost_adjusted_proxy_pct"
            ),
            "realizable_return": False if horizon == OUTCOME_HORIZON else None,
            "cost_model_version": COST_MODEL_VERSION,
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
            "repeated_labeled_rows": max(0, len(valid_signal_rows) - len(samples)),
            "invalid_metric_rows": invalid_metric_rows,
            "cost_version_excluded_samples": cost_version_excluded,
        },
        "sample": {
            "unit": "symbol_trade_date",
            "policy": "earliest_labeled_observation_then_best_rank",
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
            "cost_adjusted_d0_proxy_pct": proxy_mean,
            "gross_labels": len(gross),
            "fillable_labels": len(proxy),
            "net_pct_identity": "deprecated alias of cost_adjusted_d0_proxy_pct; not realizable",
        },
        "precision_at_k": {
            "run_id": selected_run_id,
            "as_of": run_as_of.isoformat() if run_as_of else None,
            "k_requested": max(1, int(top_k)),
            "k": len(selected),
            "selected": len(selected),
            "evaluable": len(evaluable),
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
            "样本低于下限时 verdict 恒为 insufficient_sample。D0 成本调整值仅为同日收盘代理，"
            "不是 A 股 T+1 下可实现净收益；可成交代理仍存在选择性偏差。"
        ),
    }
