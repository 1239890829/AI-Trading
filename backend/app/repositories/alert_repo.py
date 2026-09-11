from __future__ import annotations

import json
from datetime import datetime

from app.core.db import beijing_now_naive
from app.models.alert import AlertEvent, AlertRule


class AlertRepository:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def list_rules(self, enabled_only: bool = False) -> list[AlertRule]:
        with self._session_factory() as db:
            q = db.query(AlertRule).order_by(AlertRule.id.desc())
            if enabled_only:
                q = q.filter(AlertRule.enabled == True)  # noqa: E712
            return q.all()

    def get_rule(self, rule_id: int) -> AlertRule | None:
        with self._session_factory() as db:
            return db.query(AlertRule).filter(AlertRule.id == rule_id).one_or_none()

    def create_rule(self, **kwargs) -> AlertRule:
        if "symbols" in kwargs and isinstance(kwargs["symbols"], list):
            kwargs["symbols"] = json.dumps(kwargs["symbols"], ensure_ascii=False)
        if "channels" in kwargs and isinstance(kwargs["channels"], list):
            kwargs["channels"] = json.dumps(kwargs["channels"], ensure_ascii=False)
        with self._session_factory() as db:
            rule = AlertRule(**kwargs)
            db.add(rule)
            db.commit()
            db.refresh(rule)
            return rule

    def update_rule(self, rule_id: int, **kwargs) -> AlertRule | None:
        if "symbols" in kwargs and isinstance(kwargs["symbols"], list):
            kwargs["symbols"] = json.dumps(kwargs["symbols"], ensure_ascii=False)
        if "channels" in kwargs and isinstance(kwargs["channels"], list):
            kwargs["channels"] = json.dumps(kwargs["channels"], ensure_ascii=False)
        with self._session_factory() as db:
            rule = db.query(AlertRule).filter(AlertRule.id == rule_id).one_or_none()
            if not rule:
                return None
            for k, v in kwargs.items():
                setattr(rule, k, v)
            rule.updated_at = beijing_now_naive()
            db.commit()
            db.refresh(rule)
            return rule

    def delete_rule(self, rule_id: int) -> bool:
        with self._session_factory() as db:
            rule = db.query(AlertRule).filter(AlertRule.id == rule_id).one_or_none()
            if not rule:
                return False
            db.delete(rule)
            db.commit()
            return True

    def record_trigger(self, rule_id: int, symbol: str, trigger_value: float, threshold: float,
                       snapshot: dict | None = None, delivered_channels: list[str] | None = None) -> AlertEvent:
        with self._session_factory() as db:
            event = AlertEvent(
                rule_id=rule_id,
                symbol=symbol,
                trigger_value=trigger_value,
                threshold=threshold,
                snapshot=json.dumps(snapshot, ensure_ascii=False, default=str) if snapshot else None,
                delivered_channels=json.dumps(delivered_channels, ensure_ascii=False) if delivered_channels else None,
            )
            db.add(event)
            rule = db.query(AlertRule).filter(AlertRule.id == rule_id).one()
            rule.last_triggered_at = beijing_now_naive()
            db.commit()
            db.refresh(event)
            return event

    def list_events(self, limit: int = 50, rule_id: int | None = None,
                    acknowledged: bool | None = None) -> list[AlertEvent]:
        with self._session_factory() as db:
            q = db.query(AlertEvent).order_by(AlertEvent.triggered_at.desc())
            if rule_id is not None:
                q = q.filter(AlertEvent.rule_id == rule_id)
            if acknowledged is not None:
                q = q.filter(AlertEvent.acknowledged == (1 if acknowledged else 0))
            return q.limit(limit).all()

    def acknowledge_event(self, event_id: int) -> bool:
        with self._session_factory() as db:
            event = db.query(AlertEvent).filter(AlertEvent.id == event_id).one_or_none()
            if not event:
                return False
            event.acknowledged = 1
            db.commit()
            return True

    def update_event_channels(self, event_id: int, channels: list[str]) -> bool:
        with self._session_factory() as db:
            event = db.query(AlertEvent).filter(AlertEvent.id == event_id).one_or_none()
            if not event:
                return False
            event.delivered_channels = json.dumps(channels, ensure_ascii=False)
            db.commit()
            return True

    def update_last_triggered(self, rule_id: int, triggered_at: datetime | None = None) -> None:
        with self._session_factory() as db:
            rule = db.query(AlertRule).filter(AlertRule.id == rule_id).one_or_none()
            if rule:
                rule.last_triggered_at = triggered_at or beijing_now_naive()
                db.commit()
