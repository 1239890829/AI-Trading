"""Durable source facts for non-buy-point notifications.

The source owns its decision or action.  AlertEvent is the shared projection;
brief JSON is derived after the event exists.  Only kinds allowed by both the
push matrix and the persisted system rule may create a Feishu outbox intent.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import timezone

from sqlalchemy import select

from app.core.bjtime import BJ_TZ, beijing_now
from app.core.db import get_session_factory
from app.models.alert import AlertEvent, AlertRule
from app.review.models import ReviewReportRow
from app.notifiers import get_notifier_registry
from app.repositories.alert_repo import AlertRepository, PLACEHOLDER_SYMBOL
from app.services.push_policy import PolicyKind, feishu_allowed

log = logging.getLogger(__name__)

SOURCE_POLICIES: dict[str, PolicyKind] = {
    "board_reopen": PolicyKind.SILENT,
    "position_open": PolicyKind.SILENT,
    "position_wave": PolicyKind.SILENT,
    "position_exit": PolicyKind.SILENT,
    "position_exit_pending": PolicyKind.SILENT,
    "position_monitor_degraded": PolicyKind.SILENT,
    "take_profit": PolicyKind.SILENT,
    "real_exit_alert": PolicyKind.CRITICAL,
    "review_report": PolicyKind.REPORT,
}


def event_key(kind: str, source_key: str) -> str:
    if kind not in SOURCE_POLICIES or not source_key:
        raise ValueError("unknown source event kind or missing source key")
    return hashlib.sha256(f"source-event:v1:{kind}:{source_key}".encode()).hexdigest()


def _rule(session_factory, kind: str) -> AlertRule:
    name = f"__source_{kind}__"
    with session_factory() as db:
        row = db.scalar(select(AlertRule).where(AlertRule.name == name).order_by(AlertRule.id).limit(1))
        if row is None:
            channels = ["in_app", "log"]
            if feishu_allowed(SOURCE_POLICIES[kind]):
                channels.append("feishu")
            row = AlertRule(
                name=name, enabled=1, condition_type="source_event", scope="all",
                threshold=0.0, cooldown_seconds=0,
                channels=json.dumps(channels, ensure_ascii=False),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        db.expunge(row)
        return row


def _scope_allows(rule: AlertRule, symbol: str) -> bool:
    if rule.scope == "all":
        return True
    if rule.scope != "symbols":
        return False
    try:
        symbols = json.loads(rule.symbols or "[]")
    except (TypeError, ValueError):
        return False
    return isinstance(symbols, list) and symbol in symbols


def record_source_event(
    kind: str, source_key: str, *, symbol: str, name: str, text: str,
    source_id: str, source_version: str | None, source_as_of: str,
    trade_date: str, direction: str, brief_alert: dict | None = None,
    session_factory=None, allow_external: bool = True,
) -> tuple[int, bool, bool]:
    """Create one event and optional intent, then refresh its brief projection.

    Repeating the same source key also retries a failed brief projection, while
    the unique event and outbox facts remain unchanged.  Backfilled references
    carry their original source time and a separate recording time.
    """
    if kind not in SOURCE_POLICIES or not source_id or not source_as_of or not trade_date:
        raise ValueError("source event identity incomplete")
    session_factory = session_factory or get_session_factory()
    rule = _rule(session_factory, kind)
    policy = SOURCE_POLICIES[kind]
    now_ms = int(time.time() * 1000)
    snapshot = {
        "kind": kind, "name": name, "direction": direction, "text": text[:500],
        "source_id": source_id, "source_version": source_version,
        "source_as_of": source_as_of, "recorded_at": beijing_now().isoformat(),
        "trade_date": trade_date, "push_policy": policy.value,
    }
    if brief_alert is not None:
        snapshot["brief_projection"] = "pending"
    try:
        channels = json.loads(rule.channels or "[]")
    except (TypeError, ValueError):
        channels = []
    external = (
        allow_external and bool(rule.enabled) and isinstance(channels, list)
        and "feishu" in channels and feishu_allowed(policy)
        and _scope_allows(rule, symbol)
    )
    target = None
    if external:
        notifier = get_notifier_registry().get("feishu")
        target = notifier.delivery_target() if notifier is not None else ""
    lifetime_ms = 4 * 60 * 60 * 1000 if kind == "review_report" else 5 * 60 * 1000
    repo = AlertRepository(session_factory)
    event, created = repo.record_trigger_once(
        rule.id, symbol or PLACEHOLDER_SYMBOL, 0.0, 0.0,
        dedup_key=event_key(kind, source_key), snapshot=snapshot,
        delivered_channels=["in_app"], outbox_target=target,
        now_ms=now_ms, expires_at_ms=now_ms + lifetime_ms,
        outbox_intent={
            "kind": "source_event", "source_kind": kind,
            "source_id": source_id, "source_version": source_version,
            "trade_date": trade_date, "policy": policy.value,
        } if external else None,
    )
    projected = True
    if brief_alert is not None:
        try:
            from app.picks.morning_brief import append_alert, load_brief

            stored = json.loads(event.snapshot or "{}")
            projection = dict(brief_alert)
            projection["text"] = stored.get("text") or projection.get("text")
            if isinstance(projection.get("seal_state"), dict):
                projection["seal_state"] = {**projection["seal_state"],
                                            "version": stored.get("source_version")}
            meta = dict(projection.get("meta") or {})
            if "snapshot_as_of" in meta:
                meta["snapshot_as_of"] = stored.get("source_as_of")
            meta.update(event_id=event.id, source_id=stored.get("source_id"),
                        source_version=stored.get("source_version"),
                        source_as_of=stored.get("source_as_of"),
                        recorded_at=stored.get("recorded_at"))
            projection["meta"] = meta
            brief_date = trade_date.replace("-", "")
            if not append_alert(brief_date, projection):
                current = load_brief(brief_date)
                projected = bool(current and any(
                    a.get("key") == projection.get("key") for a in current.get("alerts", [])
                ))
        except Exception:
            projected = False
            log.exception("source event %s brief projection failed; durable event retained", event.id)
        if projected:
            try:
                with session_factory() as db:
                    stored_event = db.get(AlertEvent, event.id)
                    if stored_event is None:
                        raise RuntimeError("source event disappeared before projection receipt")
                    stored_snapshot = json.loads(stored_event.snapshot or "{}")
                    if stored_snapshot.get("brief_projection") != "completed":
                        stored_snapshot["brief_projection"] = "completed"
                        stored_event.snapshot = json.dumps(stored_snapshot, ensure_ascii=False)
                        db.commit()
            except Exception:
                projected = False
                log.exception("source event %s brief projection receipt failed; retry required", event.id)
    return event.id, created, projected


def record_review_report(session_factory, trade_date: str) -> tuple[int, bool, bool] | None:
    """Reconcile a saved report to one event. Historical reports stay reference-only."""
    with session_factory() as db:
        row = db.scalar(select(ReviewReportRow).where(ReviewReportRow.trade_date == trade_date))
        if row is None:
            return None
        review_id = row.review_id
        generated_at = row.generated_at
    generated_bj = None
    if generated_at is not None:
        as_utc = generated_at if generated_at.tzinfo is not None else generated_at.replace(tzinfo=timezone.utc)
        generated_bj = as_utc.astimezone(BJ_TZ)
    source_as_of = generated_bj.isoformat() if generated_bj is not None else "unknown"
    now = beijing_now()
    today = now.strftime("%Y%m%d")
    return record_source_event(
        "review_report", f"review:{trade_date}:{review_id}", symbol=PLACEHOLDER_SYMBOL,
        name="", text=f"{trade_date} 复盘报告已生成；请在复盘页查看完整依据。",
        source_id=review_id, source_version=review_id,
        source_as_of=source_as_of, trade_date=trade_date,
        direction="盘后复盘", session_factory=session_factory,
        allow_external=(trade_date == today and (now.hour, now.minute) >= (15, 0)
                        and generated_bj is not None
                        and generated_bj.strftime("%Y%m%d") == today
                        and (generated_bj.hour, generated_bj.minute) >= (15, 0)),
    )
