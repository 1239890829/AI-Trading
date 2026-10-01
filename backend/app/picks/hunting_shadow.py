"""IMP-053: existing buy-point decisions -> independent, execution-owned shadow facts.

No notifications, rankings, or research-made fills. An outer SQLite transaction
commits attempt + order + cash together, including rejection/no-fill denominators.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections import Counter
from datetime import datetime, timedelta
from statistics import mean, median

from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ, beijing_now
from app.core.db import utcnow
from app.models.hunting_shadow import HuntingShadowAttempt as Attempt
from app.models.opportunity_learning import OpportunityDecisionSnapshot
from app.models.paper import PaperOrder, PaperPosition, SCOPE_HUNTING_SHADOW
from app.paper.engine import PaperTradingEngine, calc_fee
from app.picks.opportunity_learning import latest_notification_execution

log = logging.getLogger(__name__)
SCOPE = SCOPE_HUNTING_SHADOW
EXECUTION_VERSION = "hunting-execution-v1"
COST_MODEL_VERSION = "paper-fees-v1.slippage-zero"
EXIT_RULE_VERSION = "next-confirmed-trading-day-v1"
CAPITAL_VERSION = "standard-1m.10pct.2slots-v1"
VERSIONS = ("strategy_version", "feature_version", "execution_version", "cost_model_version", "exit_rule_version")


def _json(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=str)


def _digest(data):
    return hashlib.sha256(_json(data).encode()).hexdigest()


def _time(raw):
    try:
        dt = datetime.fromisoformat(str(raw))
        return dt if dt.tzinfo is not None else None
    except ValueError:
        return None


def _number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _fresh_quote(quote):
    return (quote is not None and _number(quote.price)
            and quote.quality.value in {"high", "medium"} and quote.freshness().state == "ready")


def _days(raw):
    result = set()
    for day in raw or []:
        value = day.isoformat() if hasattr(day, "isoformat") else str(day)
        try:
            result.add(datetime.strptime(value.replace("-", ""), "%Y%m%d").date().isoformat())
        except ValueError:
            return []
    return sorted(result)


def _entry_issue(contract, quote, now, days):
    from app.market.trade_calendar import in_trading_window
    checked = _time((contract.get("executable_snapshot") or {}).get("checked_at"))
    if checked is None or checked.astimezone(BJ_TZ).date() != now.date():
        return "expired", "decision_time_unknown_or_other_day"
    age = (now - checked).total_seconds()
    if age < 0 or age > 60:
        return "expired", "decision_window_expired"
    if now.date().isoformat() not in days or not in_trading_window(now):
        return "rejected", "calendar_or_session_unconfirmed"
    snap = contract.get("executable_snapshot") or {}
    gates = contract.get("gate_inputs") or {}
    if (contract.get("gate_decision") != "passed" or snap.get("state") != "ready"
            or gates.get("confidence_tier") not in {"executable", "strong"}
            or gates.get("vetoes") or gates.get("observation_only")):
        return "rejected", "decision_not_actionable"
    if not _fresh_quote(quote):
        return "rejected", "recheck_quote_not_ready"
    low, high = (gates.get("buy_range") or {}).get("low"), (gates.get("buy_range") or {}).get("high")
    if not (_number(low) and _number(high) and low <= quote.price <= high and _number(snap.get("price"))):
        return "rejected", "recheck_outside_entry_range"
    from app.market.price_rules import limit_pct
    if quote.change_pct is None or quote.change_pct >= .95 * limit_pct(quote.symbol, quote.name):
        return "rejected", "recheck_limit_zone_or_unknown"
    if not all(str(contract.get(field) or "").strip() for field in ("decision_id", "decision_version", "strategy_version", "feature_version")):
        return "rejected", "decision_version_missing"
    return None


class HuntingShadowRunner:
    def __init__(self, engine: PaperTradingEngine, sf):
        if engine.scope != SCOPE:
            raise ValueError("hunting runner requires independent scope")
        self.engine, self.sf = engine, sf
        self.health = {"state": "not_started", "as_of": None, "reason": None}

    async def tick(self):
        now = beijing_now()
        try:
            days = _days(await self.engine._tdays_fn()) if self.engine._tdays_fn else []
        except Exception:
            days = []
            log.exception("hunting calendar unavailable; entries/exits fail closed")
        await self.close_due(now, days)
        contracts = latest_notification_execution(now.date().isoformat(), self.sf)
        for symbol, contract in contracts.items():
            # Waiting/rejected research/reference decisions never allocate capital.
            if contract.get("archived_decision") not in {"eligible", "notified", "suppressed"} or contract.get("gate_decision") != "passed":
                continue
            await self.submit(symbol, contract, now, days)
        return {"state": "ready" if days else "degraded", "as_of": beijing_now().isoformat(),
                "reason": None if days else "calendar_unconfirmed"}

    async def submit(self, symbol, contract, now, days):
        key = _digest({"decision_id": contract.get("decision_id"), "decision_version": contract.get("decision_version")})
        with self.sf() as db:
            if db.get(Attempt, key):
                return
        try:
            quote = await self.engine._quote_fn(symbol)
        except Exception:
            quote = None
            log.exception("hunting entry quote unavailable %s", symbol)
        async def captured_quote(requested):
            return quote if requested == symbol else None
        async def captured_days():
            return days
        with self.sf() as probe:
            bind = probe.get_bind()
        with bind.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                sf = sessionmaker(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
                with sf() as db:
                    if db.get(Attempt, key):
                        conn.rollback()
                        return
                    current = latest_notification_execution(now.date().isoformat(), sf).get(symbol) or {}
                    if current.get("decision_id") != contract.get("decision_id") or current.get("decision_version") != contract.get("decision_version"):
                        conn.rollback()
                        return  # superseded reference; no attempted action on the old version
                    prior = db.scalar(select(Attempt).where(Attempt.decision_id == contract.get("decision_id"), Attempt.state.in_(("pending", "filled", "exited"))).limit(1))
                    if prior:
                        conn.rollback()
                        return  # same day episode already occupies/occupied a position
                    first = db.scalar(select(OpportunityDecisionSnapshot).where(
                        OpportunityDecisionSnapshot.trade_date == now.date().isoformat(),
                        OpportunityDecisionSnapshot.symbol == symbol,
                        OpportunityDecisionSnapshot.stage == "notification",
                    ).order_by(OpportunityDecisionSnapshot.as_of, OpportunityDecisionSnapshot.id).limit(1))
                    evidence = {
                        "strategy_key": "daily_picks", "strategy_version": current.get("strategy_version"),
                        "experiment_id": None, "candidate": None, "cost_bps": None,
                        "admission_binding": "existing_daily_picks_only_no_challenger",
                        "feature_version": current.get("feature_version"), "execution_version": EXECUTION_VERSION,
                        "cost_model_version": COST_MODEL_VERSION, "exit_rule_version": EXIT_RULE_VERSION,
                        "capital_version": CAPITAL_VERSION, "initial_cash": 1_000_000, "weight": .1, "slots": 2,
                        "first_seen": first.as_of.replace(tzinfo=BJ_TZ).isoformat() if first else None,
                        "first_seen_snapshot_id": first.snapshot_id if first else None,
                        "first_triggered": (current.get("executable_snapshot") or {}).get("checked_at"),
                        "reference": current.get("reference_entry"), "reference_semantics": "reference_only_not_fill",
                        "decision_snapshot": current.get("snapshot_id"), "recheck_at": now.isoformat(),
                        "recheck_quote": quote.model_dump(mode="json") if quote else None,
                        "expires_at": ((_time((current.get("executable_snapshot") or {}).get("checked_at")) or now) + timedelta(seconds=60)).isoformat(),
                        "slippage_bps": 0, "timeline": [],
                    }
                    row = Attempt(id=key, trade_date=now.date().isoformat(), symbol=symbol,
                                  decision_id=current["decision_id"], decision_version=current["decision_version"],
                                  snapshot_id=current["snapshot_id"], state="rejected", evidence=_json(evidence))
                    issue = _entry_issue(current, quote, beijing_now(), days)
                    if quote and quote.symbol != symbol:
                        issue = ("rejected", "quote_symbol_mismatch")
                    if issue:
                        row.state, row.reason = issue
                    else:
                        executor = PaperTradingEngine(sf, captured_quote, captured_days, scope=SCOPE, risk_engine=self.engine._risk_engine)
                        occupied = db.scalar(select(PaperPosition.id).where(PaperPosition.scope == SCOPE, PaperPosition.symbol == symbol, PaperPosition.quantity > 0))
                        positions = list(db.scalars(select(PaperPosition).where(PaperPosition.scope == SCOPE, PaperPosition.quantity > 0)))
                        pending = executor.pending_orders("buy")
                        acc = executor.ensure_account()
                        if occupied or any(o["symbol"] == symbol for o in pending):
                            row.reason = "symbol_already_occupied"
                        elif len(positions) + len(pending) >= 2:
                            row.reason = "capital_slots_full"
                        elif acc.initial_cash != 1_000_000:
                            row.reason = "standard_capital_drift"
                        else:
                            price = current["executable_snapshot"]["price"]
                            qty = int(acc.initial_cash * .1 / price / 100) * 100
                            while qty >= 100 and qty * price + calc_fee("buy", price, qty) > acc.initial_cash * .1:
                                qty -= 100
                            if qty < 100:
                                row.reason = "budget_below_one_lot"
                            else:
                                order = await executor.place_order(symbol, "buy", price, qty)
                                # Market/risk rejections may not have been persisted by the engine.
                                db.add(order)
                                db.flush()
                                row.entry_order_id = order.id
                                row.state = order.status if order.status in {"filled", "pending", "rejected"} else "rejected"
                                row.reason = order.reason or "accepted"
                    evidence["timeline"].append({"at": now.isoformat(), "state": row.state, "reason": row.reason, "order_id": row.entry_order_id})
                    row.evidence = _json(evidence)
                    db.add(row)
                    db.commit()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    async def close_due(self, now, days):
        from app.market.trade_calendar import in_trading_window
        with self.sf() as db:
            keys = list(db.scalars(select(Attempt.id).where(Attempt.state.in_(("pending", "filled")))))
        for key in keys:
            with self.sf() as db:
                detached = db.get(Attempt, key)
                symbol, day = detached.symbol, detached.trade_date
            # Pending entries never fill by hindsight; cancel at their fixed deadline.
            try:
                quote = await self.engine._quote_fn(symbol) if in_trading_window(now) else None
            except Exception:
                quote = None
                log.exception("hunting exit quote unavailable %s", symbol)
            now = beijing_now()  # a slow source must not freeze the execution/session clock
            async def captured_quote(requested):
                return quote if requested == symbol else None
            async def captured_days():
                return days
            with self.sf() as probe:
                bind = probe.get_bind()
            with bind.connect() as conn:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    sf = sessionmaker(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
                    executor = PaperTradingEngine(sf, captured_quote, captured_days, scope=SCOPE, risk_engine=self.engine._risk_engine)
                    with sf() as db:
                        row = db.get(Attempt, key)
                        if row.state not in {"pending", "filled"}:
                            conn.rollback()
                            continue
                        evidence = json.loads(row.evidence)
                        entry = db.get(PaperOrder, row.entry_order_id)
                        if entry is None or entry.scope != SCOPE:
                            raise ValueError("entry order missing or wrong scope")
                        if row.state == "pending":
                            deadline = _time(evidence["expires_at"])
                            if deadline is None or now >= deadline:
                                cancelled = executor.cancel(entry.id)
                                if cancelled is None:
                                    raise ValueError("pending order drift; requires reconciliation")
                                row.state, row.reason = "no_fill", "limit_order_expired_unfilled"
                            else:
                                current = latest_notification_execution(row.trade_date, sf).get(symbol) or {}
                                if current.get("decision_version") != row.decision_version or _entry_issue(current, quote, now, days) or quote.symbol != symbol:
                                    conn.rollback()
                                    continue
                                risk_issue = await executor._risk_block_reason(symbol, "buy", entry.price, entry.quantity, quote)
                                if risk_issue:
                                    row.reason = risk_issue
                                    evidence["timeline"].append({"at": now.isoformat(), "state": "pending", "reason": risk_issue})
                                    row.evidence = _json(evidence)
                                    row.updated_at = utcnow()
                                    db.commit()
                                    conn.commit()
                                    continue
                                await executor.match_pending()
                                db.refresh(entry)
                                if entry.status != "filled":
                                    conn.rollback()
                                    continue
                                row.state, row.reason = "filled", "bounded_limit_fill"
                        else:
                            next_day = next((d for d in days if d > day), None) if day in days else None
                            if next_day is None or now.date().isoformat() not in days or now.date().isoformat() < next_day or not in_trading_window(now):
                                conn.rollback()
                                continue
                            if not _fresh_quote(quote) or quote.symbol != symbol:
                                conn.rollback()
                                continue
                            order = await executor.place_order(symbol, "sell", quote.price, entry.quantity)
                            db.add(order)
                            db.flush()
                            row.exit_order_id = order.id
                            row.reason = order.reason or order.status
                            if order.status == "filled":
                                row.state = "exited"
                        evidence["timeline"].append({"at": now.isoformat(), "state": row.state, "reason": row.reason, "exit_order_id": row.exit_order_id,
                                                     "quote": quote.model_dump(mode="json") if quote else None})
                        row.evidence = _json(evidence)
                        row.updated_at = utcnow()
                        db.commit()
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise


def read_summary(sf, day=None):
    """Pure read: no account creation, settlement, labels, model calls, or orders."""
    with sf() as db:
        day = day or db.scalar(select(func.max(Attempt.trade_date)))
        query = select(Attempt).where(Attempt.trade_date == day).order_by(Attempt.created_at, Attempt.id)
        rows = list(db.scalars(query))
        ids = {r.entry_order_id for r in rows} | {r.exit_order_id for r in rows}
        orders = {o.id: o for o in db.scalars(select(PaperOrder).where(PaperOrder.id.in_(ids - {None})))}
        records, returns, issues = [], [], []
        for row in rows:
            evidence = json.loads(row.evidence)
            entry, exit_order = orders.get(row.entry_order_id), orders.get(row.exit_order_id)
            fill = entry if entry and entry.scope == SCOPE and entry.symbol == row.symbol and entry.side == "buy" and entry.status == "filled" and _number(entry.filled_price) else None
            closed = exit_order if fill and exit_order and exit_order.scope == SCOPE and exit_order.symbol == row.symbol and exit_order.side == "sell" and exit_order.status == "filled" and exit_order.quantity == fill.quantity and _number(exit_order.filled_price) else None
            net = None
            if row.state not in {"pending", "filled", "exited", "rejected", "no_fill", "expired"}:
                issues.append(f"{row.id}:execution_state_unknown")
            if fill and row.state not in {"filled", "exited"}:
                issues.append(f"{row.id}:fill_state_disagrees_with_order")
            if closed and row.state != "exited":
                issues.append(f"{row.id}:exit_state_disagrees_with_order")
            if row.state in {"filled", "exited"} and not fill:
                issues.append(f"{row.id}:actual_entry_fill_missing")
            if row.state == "exited" and not closed:
                issues.append(f"{row.id}:actual_exit_fill_missing")
            if closed:
                if not (math.isfinite(fill.fee) and math.isfinite(closed.fee) and fill.fee >= 0 and closed.fee >= 0):
                    issues.append(f"{row.id}:fee_invalid")
                else:
                    entry_cost = fill.filled_price * fill.quantity + fill.fee
                    net = ((closed.filled_price * closed.quantity - closed.fee) / entry_cost - 1) * 100
                    returns.append(net)
            records.append({"id": row.id, "symbol": row.symbol, "trade_date": row.trade_date, "state": row.state,
                            "decision_id": row.decision_id, "decision_version": row.decision_version,
                            "reference": evidence.get("reference"), "entry_order_id": row.entry_order_id,
                            "exit_order_id": row.exit_order_id, "filled_price": fill.filled_price if fill else None,
                            "entry_fee": fill.fee if fill else None, "exit_fee": closed.fee if closed else None,
                            "net_return_pct": net, "reason": row.reason, "evidence": evidence})
    counts = Counter(r.state for r in rows)
    counts_out = {"filled": counts["filled"] + counts["exited"], "rejected": counts["rejected"], "no_fill": counts["no_fill"], "expired": counts["expired"], "pending": counts["pending"]}
    mature = bool(rows) and not issues and counts_out["pending"] == 0 and counts["exited"] == counts_out["filled"] and bool(returns)
    payload = {"evidence_version": 1, "source_owner": "IMP-053", "execution_scope": SCOPE,
               "return_identity": "shadow_fill_net", "filled_price_identity": "PaperOrder.filled_price",
               "costs_included": True, "slippage_bps": 0,
               "opportunities": len(rows), "independent_decisions": len({r.decision_id for r in rows}),
               "counts": counts_out, "fills_total": counts_out["filled"], "closed_fills": counts["exited"],
               "net_return_pct": mean(returns) if mature else None,
               "net_median_pct": median(returns) if mature else None,
               "win_rate": mean(r > 0 for r in returns) if mature else None,
               "status": "complete" if mature else "incomplete", "issues": issues,
               "records": records, "disclaimer": "仅独立模拟成交，不构成买卖建议。参考轨不计成交。"}
    identities = {tuple(r["evidence"].get(v) for v in VERSIONS) for r in records}
    if len(identities) == 1:
        payload.update(dict(zip(VERSIONS, next(iter(identities)))))
    elif rows:
        payload["issues"].append("mixed_execution_versions")
        payload.update(status="incomplete", net_return_pct=None, net_median_pct=None, win_rate=None)
    payload.update(strategy_key="daily_picks", experiment_id=None, candidate=None, cost_bps=None,
                   trade_date=day, promotion_binding="unbound_existing_rule_no_challenger",
                   exit_policy_id=EXIT_RULE_VERSION, exited=counts["exited"])
    return {**payload, "evidence_digest": _digest(payload)}


def validate_execution_evidence(evidence, sf=None):
    """Reject self-sealed synthetic fills: recompute from the authoritative database."""
    from app.core.db import _session_factory
    sf = sf or _session_factory
    if sf is None:
        return False  # no implicit database opening/migration from a read-only verifier
    try:
        current = read_summary(sf, evidence.get("trade_date"))
        return evidence.get("evidence_digest") == current["evidence_digest"] and evidence == current
    except (ValueError, TypeError, KeyError, SQLAlchemyError):
        return False


async def hunting_shadow_loop(app, stop):
    from app.core.config import settings
    while not stop.is_set():
        try:
            if settings.hunting_shadow_enabled and getattr(app.state, "hunting_shadow", None) is not None:
                app.state.hunting_shadow.health = await app.state.hunting_shadow.tick()
        except Exception as exc:
            app.state.hunting_shadow.health = {"state": "degraded", "as_of": beijing_now().isoformat(), "reason": type(exc).__name__}
            log.exception("hunting shadow tick failed; durable transaction preserved")
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
