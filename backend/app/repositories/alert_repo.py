from __future__ import annotations

import json
from datetime import datetime

from app.models.alert import AlertEvent, AlertRule
from app.core.bjtime import beijing_now_naive

#: 事件**不指向具体个股**时的占位代码（板块级 / 方向级 / 信号健康级统一用它）。
#:
#: 生产侧约定见 `picks/watcher.py`、`picks/board_surge.py`、`sentiment/intraday_monitor.py`、
#: `picks/signal_health.py` 等。⚠️ 判断含义要说准：它是"这条事件**没有**具体标的"，
#: **不是**"代码非法" —— 真实个股代码永远不会是 `000000`，故可用于二元分流。
PLACEHOLDER_SYMBOL = "000000"


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
                    acknowledged: bool | None = None,
                    real_symbol_only: bool = False) -> list[AlertEvent]:
        """按 `triggered_at` 倒序取前 `limit` 条。

        `real_symbol_only=True` 时**只取带真实标的的事件**（排除空串与
        `PLACEHOLDER_SYMBOL`）——留给"只关心个股级事件"的消费方（如通知中心）。

        ⚠️ 这个过滤**必须在 DB 侧**，不能"先取 N 条再在内存里筛掉板块级"：
        板块级事件实测占单日总量的 **83%**（2026-09-16：703 条里 582 条无代码），
        它会把**按条数计的读取窗口**几乎吃光。当日实测的后果是——窗口取 500 条时，
        当天 121 条临板预警只出来 **89 条**，更早的 32 条被**静默截断**
        （改完"看起来生效了"，但用户在盘中看到的仍不是全部机会）。
        """
        with self._session_factory() as db:
            q = db.query(AlertEvent).order_by(AlertEvent.triggered_at.desc())
            if rule_id is not None:
                q = q.filter(AlertEvent.rule_id == rule_id)
            if acknowledged is not None:
                q = q.filter(AlertEvent.acknowledged == (1 if acknowledged else 0))
            if real_symbol_only:
                q = q.filter(
                    AlertEvent.symbol.isnot(None),
                    AlertEvent.symbol != "",
                    AlertEvent.symbol != PLACEHOLDER_SYMBOL,
                )
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
