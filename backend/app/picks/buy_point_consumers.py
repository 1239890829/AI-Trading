"""Recover local consumers independently of external delivery.

Watch insertion is idempotent. A started paper action with an unknown outcome
is retained for reconciliation and is never retried blindly.
"""
import asyncio
import json
import logging
import time
from datetime import datetime

from sqlalchemy import case, func, select, update

from app.core.bjtime import beijing_now
from app.models.notification_outbox import BuyPointConsumption as Progress

log = logging.getLogger(__name__)


def _has_pending(sf):
    with sf() as db:
        return db.scalar(select(Progress.event_id).where(Progress.state.in_(("pending", "running"))).limit(1)) is not None


def _pending_keys(sf, now_ms, event_id):
    with sf() as db:
        # Reclaim only idempotent watch work. Interrupted paper work is uncertain.
        for consumer, state in (("watch", "pending"), ("position", "unknown")):
            db.execute(update(Progress).where(
                Progress.state == "running", Progress.consumer == consumer,
                Progress.updated_at_ms < now_ms - 120_000,
            ).values(state=state, reason="interrupted_consumer"))
        query = select(Progress).where(Progress.state == "pending")
        if event_id is not None:
            query = query.where(Progress.event_id == event_id)
        keys = [(r.event_key, r.consumer) for r in db.scalars(query.limit(20))]
        db.commit()
        return keys


def _claim(sf, key, now_ms):
    with sf() as db:
        claimed = db.execute(update(Progress).where(
            Progress.event_key == key[0], Progress.consumer == key[1], Progress.state == "pending",
        ).values(state="running", updated_at_ms=now_ms, attempts=Progress.attempts + 1))
        db.commit()
        if not claimed.rowcount:
            return None
        row = db.get(Progress, key)
        return row.payload, row.expires_at_ms, row.attempts


def _finish(sf, key, now_ms, state, reason, execution_result, payload, meta):
    with sf() as db:
        values = {"state": state, "reason": reason, "updated_at_ms": int(time.time() * 1000)}
        if execution_result is not None:
            current = db.get(Progress, key)
            saved_payload = json.loads(current.payload) if current else payload
            atomic_result = saved_payload.get("execution_result") or {}
            if type(atomic_result.get("order_id")) is int and atomic_result["order_id"] > 0:
                # The financial transaction knows this order even if the
                # wrapper/refresh failed before returning its identity.
                execution_result = {**execution_result, **atomic_result}
            saved_payload = {**saved_payload, "execution_result": execution_result}
            order_id = execution_result.get("order_id")
            if (state == "done" and type(order_id) is int and order_id > 0
                    and meta.get("selection_ref") and meta.get("selection_source_version")):
                saved_payload["paper_projection"] = {"state": "pending"}
            values["payload"] = json.dumps(saved_payload, ensure_ascii=False)
        saved = db.execute(update(Progress).where(
            Progress.event_key == key[0], Progress.consumer == key[1], Progress.state == "running",
            Progress.updated_at_ms == now_ms,
        ).values(**values))
        db.commit()
        return saved.rowcount


async def consume_pending(app, sf, *, event_id=None):
    await _project_positions(sf)
    now_ms = int(time.time() * 1000)
    if not await asyncio.to_thread(_has_pending, sf):
        return
    keys = await asyncio.to_thread(_pending_keys, sf, now_ms, event_id)
    for key in keys:
        claimed = await asyncio.to_thread(_claim, sf, key, now_ms)
        if claimed is None:
            continue
        raw, expiry, attempts = claimed
        state, reason, execution_result = "done", "consumed", None
        payload, meta = None, None
        try:
            payload = json.loads(raw)
            alert, snapshot = payload["alert"], payload["snapshot"]
            meta = alert.get("meta") or {}
            ref = meta.get("execution_ref") or {}
            trade_date = str(meta.get("trade_date") or "")
            if key[1] == "watch":
                from app.picks.pre_limit_radar import board_limit_pct, is_sealed
                from app.picks.watch_ledger import record_sighting
                from app.picks.watcher import _alert_price

                pct = snapshot.get("change_pct")
                if pct is None or is_sealed(float(pct), board_limit_pct(alert["symbol"], alert["name"])):
                    state, reason = "skipped", "original_quote_sealed_or_unknown"
                else:
                    # Original observed time/price, never recovery-time hindsight.
                    at = datetime.fromisoformat(alert["at"])
                    if at.tzinfo is None or at.date().isoformat() != trade_date:
                        raise ValueError("original observation time invalid")
                    await asyncio.to_thread(record_sighting,
                        trade_date=trade_date, symbol=alert["symbol"], name=alert["name"],
                        layer="today_strongest", source_theme=alert.get("direction") or "",
                        reason={"kind": "buy_point", "text": (alert.get("text") or "")[:300],
                                "decision_id": ref.get("decision_id"),
                                "decision_version": ref.get("decision_version")},
                        entry_price=_alert_price(alert, snapshot.get("price")),
                        entry_time=at.strftime("%H:%M:%S"), session_factory=sf,
                    )
            elif expiry <= int(time.time() * 1000) or trade_date != beijing_now().date().isoformat():
                state, reason = "expired", "original_action_expired"
            else:
                from app.market.trade_calendar import in_trading_window
                if not in_trading_window(beijing_now()):
                    raise ValueError("outside trading window")
                from app.picks.opportunity_learning import latest_notification_execution
                from app.picks.position_engine import maybe_open

                executions = await asyncio.to_thread(latest_notification_execution, trade_date, sf,
                    selection_ref=meta.get("selection_ref"), source_version=meta.get("selection_source_version"))
                latest = executions.get(alert["symbol"]) or {}
                executable = latest.get("executable_snapshot") or {}
                if (latest.get("decision_id") != ref.get("decision_id")
                        or latest.get("decision_version") != ref.get("decision_version")
                        or latest.get("gate_decision") != "passed"
                        or latest.get("archived_decision") != "eligible"
                        or executable.get("state") != "ready"):
                    state, reason = "skipped", "decision_superseded_or_ineligible"
                else:
                    result = await maybe_open(app, symbol=alert["symbol"], name=alert["name"],
                                              trigger="buy_point", price=executable.get("price"),
                                              decision_context=ref,
                                              selection_context={
                                                  "selection_ref": meta.get("selection_ref"),
                                                  "source_version": meta.get("selection_source_version"),
                                                  "decision_id": ref.get("decision_id"),
                                                  "decision_version": ref.get("decision_version"),
                                                  "consumer_event_key": key[0], "expires_at_ms": expiry,
                                              })
                    execution_result = {field: result[field] for field in (
                        "opened", "accepted", "pending", "execution_unknown", "order_id",
                        "decision_id", "decision_version", "reason", "projection_unknown") if field in result}
                    state = "unknown" if result.get("execution_unknown") else "done"
                    reason = str(result.get("reason") or "consumed")[:256]
        except Exception as exc:
            state = "pending" if key[1] == "watch" and attempts < 3 else "unknown"
            reason = f"consumer_error:{type(exc).__name__}"
            log.warning("buy-point consumer %s/%s: %s", *key, reason, exc_info=True)
        saved = await asyncio.to_thread(_finish, sf, key, now_ms, state, reason, execution_result, payload, meta)
        if saved and key[1] == "position" and state == "done":
            await _project_positions(sf, event_key=key[0])


def _pending_position_projections(sf, event_key):
    safe = case((func.json_valid(Progress.payload), Progress.payload), else_="{}")
    with sf() as db:
        query = select(Progress.event_key, Progress.payload).where(Progress.consumer == "position",
            Progress.state == "done", func.json_extract(safe, "$.paper_projection.state") == "pending")
        if event_key is not None:
            query = query.where(Progress.event_key == event_key)
        return list(db.execute(query.limit(20)))


def _finish_projection(sf, event_key, original, receipt):
    payload = json.loads(original)
    payload["paper_projection"] = {key: receipt[key] for key in ("state", "event_id", "reason") if key in receipt}
    with sf() as db:
        db.execute(update(Progress).where(Progress.event_key == event_key, Progress.consumer == "position",
            Progress.state == "done", Progress.payload == original).values(payload=json.dumps(payload, ensure_ascii=False)))
        db.commit()


async def _project_positions(sf, event_key=None):
    """Retry only a committed notification projection, never its paper action."""
    from app.services.selection_workflow import record_automatic_paper_status
    for key, original in await asyncio.to_thread(_pending_position_projections, sf, event_key):
        try:
            receipt = await asyncio.to_thread(record_automatic_paper_status, event_key=key, session_factory=sf)
            if receipt.get("state") in {"recorded", "rejected"}:
                await asyncio.to_thread(_finish_projection, sf, key, original, receipt)
        except Exception:
            log.warning("saved paper projection unavailable; original action retained", exc_info=True)
