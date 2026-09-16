"""Replayable evidence and outcome labels for the stock-opportunity funnel.

Snapshots are append-only: later closes are written to a separate outcome table.
The archived evidence is sufficient to replay each deterministic stage without
calling a market-data provider or consulting today's mutable configuration.

**两种收益口径，勿混用（2026-09-16 `RSH-026` 第二批）**

- `return_pct` = **毛收益**：决策时点价 → D0 收盘，**不含成本**。它衡量的是
  「信号方向对不对」，**不是**可实现盈亏；且 A 股 T+1 ⇒ 当日买入当日不可卖，
  该收益在规则上**不可实现**。
- `net_return_pct` = **成本后净收益**：扣双边佣金/印花税/过户费，
  **仅在决策时点可成交（`fill_state == "ok"`）时给值**。不可成交一律记 `None`
  —— 缺价/不可成交**不造 0**（把"买不到"记成"零收益"会系统性高估策略）。
- 成本费率**不在此另立**：取自 `app.paper.engine.calc_fee`（与 `paper/reconcile.py` 同源）；
  涨停幅度取自 `app.market.price_rules.limit_pct`（全仓单一实现）。
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.bjtime import beijing_now
from app.core.db import get_session_factory, utcnow
from app.market import price_rules
from app.models.opportunity_learning import OpportunityDecisionSnapshot, OpportunityOutcomeLabel
from app.paper.engine import calc_fee

STRATEGY_VERSION = "stock-opportunity-funnel-v1"
FEATURE_VERSION = "pit-evidence-v1"
OUTCOME_HORIZON = "d0_close"
STAGES = ("candidate", "hard_gate", "rank", "notification")

#: 成本口径版本。**变更费率假设或可成交判据时必须递增**——结果标签是 append-only 的，
#: 没有版本号就无法区分「策略变好」与「口径变松」。
COST_MODEL_VERSION = "cost-v1.notional-100k"

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


def _hash(value: Any, length: int = 32) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()[:length]


def _data_state(payload: dict) -> str:
    if payload.get("linkage_note"):
        return "degraded"
    stats = payload.get("linkage_stats") or {}
    if int(stats.get("missing_quote") or 0) > 0 or payload.get("hot_available") is False:
        return "degraded"
    return "ready"


def _round_lots(price: float) -> int:
    """按名义本金折算整手股数；贵价股保底 1 手（见 `COST_NOTIONAL_CNY`）。"""
    if price is None or float(price) <= 0:
        return COST_LOT
    lots = int(COST_NOTIONAL_CNY / float(price) / COST_LOT)
    return max(1, lots) * COST_LOT


def round_trip_net_pct(reference: float, exit_price: float) -> tuple[float, float, int]:
    """一次完整买卖的**净收益率 / 成本率 / 折算股数**（前两者为百分数）。

    净收益取**定义式**：`(卖出净得 − 买入实付) / 买入实付`，
    其中买入实付含费用、卖出净得已扣费用 ⇒ 它**恒严格小于**同口径毛收益，
    差额即 `calc_fee` 口径的双边成本。费率与最低佣金不在此另立。
    """
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
    if change_pct is None:
        return "no_quote", "决策时点无涨幅事实，可成交性未知"
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
        if isinstance(value, (int, float)):
            return float(value)
    facts = evidence.get("facts") or {}
    value = facts.get("change_pct")
    return float(value) if isinstance(value, (int, float)) else None


def build_intraday_records(payload: dict, *, trade_date: str, as_of: datetime) -> tuple[str, list[dict]]:
    """Turn one opportunity tree into candidate → hard-gate → rank evidence."""
    from app.picks.intraday_opportunity import top_watch_stocks

    as_of = as_of.replace(tzinfo=None)
    run_id = _hash({"scenario": "intraday", "trade_date": trade_date, "as_of": as_of.isoformat()})
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
                "expected_rank": rank,
            }
            records.append({**common, "stage": "rank", "decision": rank_decision,
                            "rank": rank, "evidence": rank_evidence})
    return run_id, records


def build_notification_records(
    items: list[dict], *, trade_date: str, as_of: datetime,
    hits: list[dict], skips: list[dict], dispatch_by_symbol: dict[str, str],
) -> tuple[str, list[dict]]:
    """Archive every notification-gate input, including negative decisions."""
    as_of = as_of.replace(tzinfo=None)
    run_id = _hash({"scenario": "notification", "trade_date": trade_date, "as_of": as_of.isoformat()})
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
        evidence = {
            "gate_decision": "passed" if hit is not None else "rejected",
            "gate_reason": skip_by.get(symbol),
            "dispatch": dispatch,
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
            "data_state": "unknown" if "快照无现价" in skip_by.get(symbol, "") else "ready",
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
                "ranked", "notified", "suppressed",
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
                              session_factory=None) -> dict:
    run_id, records = build_intraday_records(
        payload, trade_date=trade_date, as_of=as_of or beijing_now()
    )
    return archive_records(run_id, records, session_factory)


def archive_notification_pipeline(
    items: list[dict], *, trade_date: str, hits: list[dict], skips: list[dict],
    dispatch_by_symbol: dict[str, str], as_of: datetime | None = None, session_factory=None,
) -> dict:
    run_id, records = build_notification_records(
        items, trade_date=trade_date, as_of=as_of or beijing_now(), hits=hits, skips=skips,
        dispatch_by_symbol=dispatch_by_symbol,
    )
    return archive_records(run_id, records, session_factory)


def replay_decision(stage: str, evidence: dict) -> str:
    """Re-evaluate one archived stage using archived facts only."""
    if stage == "candidate":
        facts = evidence.get("facts") or {}
        if facts.get("sealed_pool") is True or facts.get("board_tradable") is False:
            return "rejected"
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
        if facts.get("sealed_pool") is True or facts.get("board_tradable") is False:
            return "rejected"
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

    `return_pct` 恒为**毛收益**（信号方向，不含成本）；`net_return_pct` 只在
    决策时点**可成交**（`fill_state == "ok"`）时给值，封板/无现价一律留 `None`
    —— **不造 0**。返回结构保持既有四键（分布见 `learning_summary`）。
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
            reference = outcome.reference_price or snapshot.entry_price
            try:
                evidence = json.loads(snapshot.evidence or "{}")
            except Exception:
                evidence = {}
            fill_state, fill_basis = assess_fill_state(
                snapshot.symbol, _archived_change_pct(evidence)
            )
            outcome.fill_state = fill_state
            if reference is None or reference <= 0:
                outcome.state = "unknown"
                outcome.label = "unknown"
                outcome.reason = "决策时点价格缺失，不能计算收益"
                outcome.labeled_at = utcnow()
                unknown += 1
                continue
            close = close_by_symbol.get(snapshot.symbol)
            if close is None or close <= 0:
                # 缺收盘价：可成交性已判定并可追溯，但结果仍未定 ⇒ 保持 pending 待重试
                outcome.reason = f"{fill_basis}；等待收盘价"
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
    eligible = len(outcomes)
    labeled = state_counts.get("labeled", 0)
    return {
        "trade_date": trade_date,
        "snapshots": len(snapshots),
        "runs": len({s.run_id for s in snapshots}),
        "stages": {stage: stage_counts.get(stage, 0) for stage in STAGES},
        "decisions": dict(sorted(decision_counts.items())),
        "outcomes": dict(sorted(state_counts.items())),
        # 可成交性分布：`sealed`（封板买不到）与 `no_quote`（无现价）都**不进净期望**，
        # 但必须在此可见——否则「净收益样本变少」会被误读成「机会变少」。
        "fill_states": dict(sorted(fill_counts.items())),
        "cost_model": COST_MODEL_VERSION,
        "label_coverage": round(labeled / eligible, 4) if eligible else None,
        "note": "样本不足时仅报告覆盖率与事实分布，不据此晋级策略；净收益口径见 cost_model",
    }


#: 判定策略是否可讨论的**样本下限**。低于此值一律只报事实、不出结论——
#: 把账本「样本不足不得转正」从注释文案**落成代码门禁**（此前该纪律无任何强制力）。
MIN_LABELS_FOR_VERDICT = 30


def opportunity_scorecard(trade_date: str, top_k: int = 5, session_factory=None) -> dict:
    """当日机会决策的**成本后**记分卡（只描述已发生的事实，不构成买卖建议）。

    - `precision_at_k`：精排队列前 K 名里净收益为正的占比
    - `expectancy`：毛/净期望收益**并列报告**——差额即可见的交易成本
    - `by_stage`：各阶段的净期望与样本数（用于定位漏斗哪一层在漏）

    ⚠️ **样本门禁**：可成交样本数 < `MIN_LABELS_FOR_VERDICT` 时 `verdict` 恒为
    `insufficient_sample`，`expectancy` 仍报告但**不足以支撑任何比较或晋级**。
    ⚠️ 净收益样本只含 `fill_state == "ok"` 的标签：封板/无现价的机会**天然缺席**，
    故本卡存在**选择性偏差**——它衡量的是「能买到的那些机会」，不是「全部机会」。
    """
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.trade_date == trade_date,
                   OpportunityOutcomeLabel.state == "labeled")
        ).all()
    gross = [float(o.return_pct) for o, _s in rows if o.return_pct is not None]
    net = [float(o.net_return_pct) for o, _s in rows if o.net_return_pct is not None]
    # 与 `net` **同分母**的毛收益子集：只有它在减法上与净期望可比（差额 = 交易成本）。
    gross_on_fillable = [
        float(o.return_pct) for o, _s in rows
        if o.net_return_pct is not None and o.return_pct is not None
    ]
    not_fillable = sum(1 for o, _s in rows if o.fill_state != "ok")
    ranked = sorted(
        ((s.rank, float(o.net_return_pct))
         for o, s in rows
         if o.net_return_pct is not None and s.stage == "rank"
         and s.decision == "ranked" and s.rank is not None),
        key=lambda pair: pair[0],
    )[:max(1, int(top_k))]
    by_stage: dict[str, dict] = {}
    for outcome, snapshot in rows:
        bucket = by_stage.setdefault(
            snapshot.stage, {"labels": 0, "fillable": 0, "net_sum": 0.0, "net_positive": 0}
        )
        bucket["labels"] += 1
        if outcome.net_return_pct is not None:
            bucket["fillable"] += 1
            bucket["net_sum"] += float(outcome.net_return_pct)
            bucket["net_positive"] += 1 if float(outcome.net_return_pct) > 0 else 0
    for bucket in by_stage.values():
        bucket["net_expectancy_pct"] = (
            round(bucket.pop("net_sum") / bucket["fillable"], 2) if bucket["fillable"] else None
        )
    verdict = "insufficient_sample" if len(net) < MIN_LABELS_FOR_VERDICT else (
        "net_positive_observed" if sum(net) / len(net) > 0 else "net_nonpositive_observed"
    )
    return {
        "trade_date": trade_date,
        "cost_model": COST_MODEL_VERSION,
        "min_labels_for_verdict": MIN_LABELS_FOR_VERDICT,
        "labeled": len(rows),
        "fillable": len(net),
        "not_fillable": not_fillable,
        "expectancy": {
            # ⚠️ 三个数**分母不同、不可互减**：`gross_pct` 的分母是全部已标样本，
            # 后两者的分母是可成交子集 —— 只有 `gross_on_fillable_pct − net_pct` 才等于成本。
            "gross_pct": round(sum(gross) / len(gross), 2) if gross else None,
            "gross_on_fillable_pct": (
                round(sum(gross_on_fillable) / len(gross_on_fillable), 2)
                if gross_on_fillable else None
            ),
            "net_pct": round(sum(net) / len(net), 2) if net else None,
            "gross_labels": len(gross),
            "fillable_labels": len(net),
        },
        "precision_at_k": {
            "k": len(ranked),
            "observed": (
                round(sum(1 for _r, v in ranked if v > 0) / len(ranked), 4) if ranked else None
            ),
        },
        "by_stage": dict(sorted(by_stage.items())),
        "verdict": verdict,
        "note": (
            "只描述已归档事实，不构成买卖建议；样本低于下限时 verdict 恒为 insufficient_sample，"
            "不得据此晋级策略。净期望仅覆盖可成交样本（封板买不到者天然缺席），存在选择性偏差。"
        ),
    }
