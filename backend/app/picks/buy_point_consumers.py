"""Recover local consumers independently of external delivery.

Watch insertion is idempotent. A started paper action with an unknown outcome
is retained for reconciliation and is never retried blindly.
"""
import json
import logging
import time
from datetime import datetime

from sqlalchemy import select, update

from app.core.bjtime import beijing_now
from app.models.notification_outbox import BuyPointConsumption as Progress

log = logging.getLogger(__name__)


async def consume_pending(app, sf, *, event_id=None):
    now_ms = int(time.time() * 1000)
    with sf() as db:
        if db.scalar(select(Progress.event_id).where(Progress.state.in_(("pending", "running"))).limit(1)) is None:
            return
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
        keys = [(r.event_id, r.consumer) for r in db.scalars(query.limit(20))]
        db.commit()
    for key in keys:
        with sf() as db:
            claimed = db.execute(update(Progress).where(
                Progress.event_id == key[0], Progress.consumer == key[1], Progress.state == "pending",
            ).values(state="running", updated_at_ms=now_ms, attempts=Progress.attempts + 1))
            db.commit()
            if not claimed.rowcount:
                continue
            row = db.get(Progress, key)
            raw, expiry, attempts = row.payload, row.expires_at_ms, row.attempts
        state, reason = "done", "consumed"
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
                    record_sighting(
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

                latest = latest_notification_execution(trade_date, sf).get(alert["symbol"]) or {}
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
                                              decision_context=ref)
                    state = "unknown" if result.get("execution_unknown") else "done"
                    reason = str(result.get("reason") or "consumed")[:256]
        except Exception as exc:
            state = "pending" if key[1] == "watch" and attempts < 3 else "unknown"
            reason = f"consumer_error:{type(exc).__name__}"
            log.warning("buy-point consumer %s/%s: %s", *key, reason, exc_info=True)
        with sf() as db:
            db.execute(update(Progress).where(
                Progress.event_id == key[0], Progress.consumer == key[1], Progress.state == "running",
                Progress.updated_at_ms == now_ms,
            ).values(state=state, reason=reason, updated_at_ms=int(time.time() * 1000)))
            db.commit()
