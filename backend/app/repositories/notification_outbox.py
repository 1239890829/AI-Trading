"""Same-database outbox with compare-and-set leases and conservative recovery.

Only pre-send leases are reclaimable. Once sending may have begun, a lost result
is unknown and is never automatically sent again without channel idempotency.
"""
from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import and_, or_, select, update

from app.models.alert import AlertEvent
from app.models.notification_outbox import NotificationAttempt, NotificationOutbox


def rule_snapshot(rule) -> dict:
    return {key: getattr(rule, key) for key in (
        "id", "name", "condition_type", "threshold", "scope", "symbols", "channels",
        "created_at",
    )}


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def enqueue_feishu(
    db, event, rule, *, target: str, now_ms: int, expires_at_ms: int,
    intent: dict | None = None,
) -> None:
    try:
        channels = json.loads(rule.channels or "[]")
    except (ValueError, TypeError):
        channels = []
    if not isinstance(channels, list) or "feishu" not in channels:
        return
    db.flush()  # event ID and timestamp, inside the caller's transaction
    payload = {"rule": rule_snapshot(rule), "event": {
        key: getattr(event, key) for key in (
            "id", "rule_id", "symbol", "trigger_value", "threshold", "triggered_at", "snapshot",
        )
    }}
    if intent:
        payload["intent"] = intent
    db.add(NotificationOutbox(
        event_id=event.id, channel="feishu", idempotency_key=uuid4().hex,
        target=target, payload=encode(payload),
        created_at_ms=now_ms, expires_at_ms=expires_at_ms,
    ))


class NotificationOutboxRepository:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def reconcile(self, now_ms: int) -> None:
        with self._session_factory() as db:
            due = db.scalar(select(NotificationOutbox.id).where(or_(
                and_(NotificationOutbox.state == "pending", NotificationOutbox.expires_at_ms <= now_ms),
                and_(NotificationOutbox.state == "leased", NotificationOutbox.lease_until_ms <= now_ms),
            )).limit(1))
            if due is None:
                return  # Idle polling must not compete for SQLite's write lock.
            # Take SQLite's write lock before reading leases; concurrent finish
            # cannot be overwritten using a stale ORM object.
            db.execute(update(NotificationOutbox).where(
                NotificationOutbox.state == "pending", NotificationOutbox.expires_at_ms <= now_ms,
            ).values(state="expired", reason="intent_expired"))
            rows = db.scalars(select(NotificationOutbox).where(
                NotificationOutbox.state == "leased", NotificationOutbox.lease_until_ms <= now_ms,
            )).all()
            for row in rows:
                started = row.send_started_at_ms is not None
                row.state = "unknown" if started else "pending"
                row.reason = "lease_lost_after_send_started" if started else "lease_reclaimed_before_send"
                db.execute(update(NotificationAttempt).where(
                    NotificationAttempt.lease_token == row.lease_token,
                ).values(state="unknown" if started else "interrupted", reason=row.reason, finished_at_ms=now_ms))
                row.lease_token = None
                row.lease_until_ms = None
                if not started and row.expires_at_ms <= now_ms:
                    row.state, row.reason = "expired", "intent_expired"
            db.commit()

    def pending_ids(self, limit: int = 100) -> list[int]:
        with self._session_factory() as db:
            return list(db.scalars(select(NotificationOutbox.id).where(
                NotificationOutbox.state == "pending",
            ).order_by(NotificationOutbox.id).limit(limit)))

    def claim(self, outbox_id: int, now_ms: int) -> NotificationOutbox | None:
        token = uuid4().hex
        with self._session_factory() as db:
            changed = db.execute(update(NotificationOutbox).where(
                NotificationOutbox.id == outbox_id, NotificationOutbox.state == "pending",
                NotificationOutbox.expires_at_ms > now_ms,
            ).values(state="leased", reason="claimed", lease_token=token, lease_until_ms=now_ms + 90_000))
            if changed.rowcount != 1:
                return None
            db.add(NotificationAttempt(outbox_id=outbox_id, lease_token=token, started_at_ms=now_ms))
            db.commit()
            row = db.get(NotificationOutbox, outbox_id)
            db.expunge(row)
            return row

    def begin_send(self, row: NotificationOutbox, now_ms: int) -> bool:
        with self._session_factory() as db:
            changed = db.execute(update(NotificationOutbox).where(
                NotificationOutbox.id == row.id, NotificationOutbox.state == "leased",
                NotificationOutbox.lease_token == row.lease_token,
                NotificationOutbox.send_started_at_ms.is_(None),
                NotificationOutbox.lease_until_ms > now_ms,
                NotificationOutbox.expires_at_ms > now_ms,
                NotificationOutbox.event_id.is_not(None),
            ).values(send_started_at_ms=now_ms))
            db.commit()
            return changed.rowcount == 1

    def finish(self, row: NotificationOutbox, state: str, reason: str, now_ms: int) -> bool:
        if state not in {"accepted", "unknown", "expired", "suppressed", "permanent_failed"}:
            raise ValueError("invalid outbox terminal state")
        with self._session_factory() as db:
            changed = db.execute(update(NotificationOutbox).where(
                NotificationOutbox.id == row.id, NotificationOutbox.state == "leased",
                NotificationOutbox.lease_token == row.lease_token,
            ).values(state=state, reason=reason, lease_token=None, lease_until_ms=None,
                     accepted_at_ms=now_ms if state == "accepted" else None))
            if changed.rowcount != 1:
                return False
            db.execute(update(NotificationAttempt).where(NotificationAttempt.lease_token == row.lease_token).values(
                state=state, reason=reason, finished_at_ms=now_ms,
            ))
            event_id = db.scalar(select(NotificationOutbox.event_id).where(NotificationOutbox.id == row.id))
            if state == "accepted" and event_id is not None:
                event = db.get(AlertEvent, event_id)
                if event is not None:
                    channels = json.loads(event.delivered_channels or "[]")
                    event.delivered_channels = encode(list(dict.fromkeys([*channels, row.channel])))
            db.commit()
            return True

    def states_for_events(self, event_ids: list[int]) -> dict[int, list[dict]]:
        with self._session_factory() as db:
            rows = db.scalars(select(NotificationOutbox).where(NotificationOutbox.event_id.in_(event_ids))).all()
            out: dict[int, list[dict]] = {}
            for row in rows:
                out.setdefault(row.event_id, []).append({
                    "outbox_id": row.id, "channel": row.channel, "state": row.state,
                    "reason": row.reason, "created_at_ms": row.created_at_ms,
                    "expires_at_ms": row.expires_at_ms, "accepted_at_ms": row.accepted_at_ms,
                })
            return out
