"""信号健康度预警接线测试：规则分立 / 当日同状态去重 / 状态升级可再发。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.alert import AlertEvent, AlertRule
from app.models.watchlist import Base
from app.picks import signal_health
from app.picks.signal_health import maybe_alert_signal_health

_REGISTERED = (AlertEvent, AlertRule)


def _health(status: str, win_rate=0.3, mean_excess=-1.8, s_max=1.9) -> dict:
    return {
        "status": status,
        "window": {"groups": 20, "total_picks": 100, "win_rate": win_rate,
                   "good": 30, "bad": 60, "flat": 10, "mean_excess": mean_excess},
        "cusum": ({"mu0": 1.2, "s_max": s_max, "threshold": 1.5, "delta": 0.1,
                   "drift": status == "drift"} if status == "drift" else None),
        "counts": {"groups": 20},
    }


@pytest.fixture()
def sf(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    # 预警函数内部经模块级 get_session_factory 取全局库——测试环境钉到内存库，
    # 绝不写真实 ashare.db
    monkeypatch.setattr(signal_health, "get_session_factory", lambda: factory)
    yield factory
    engine.dispose()


@pytest.fixture()
def fake_registry(monkeypatch):
    """截获 NotifierRegistry.dispatch：测试绝不触发真实飞书。"""
    calls: list[dict] = []

    class FakeRegistry:
        async def dispatch(self, event, rule):
            calls.append({"event_id": event.id, "rule": rule.name})
            return ["in_app"]

    import app.notifiers as notifiers_mod

    monkeypatch.setattr(notifiers_mod, "get_notifier_registry", lambda: FakeRegistry())
    return calls


class TestMaybeAlert:
    def test_ok_and_insufficient_skip(self, sf, fake_registry):
        state = SimpleNamespace()
        for status in ("ok", "insufficient", "error"):
            out = asyncio.run(maybe_alert_signal_health(state, _health(status)))
            assert out["dispatched"] is False
        assert fake_registry == []

    def test_warning_dispatches_with_dedicated_rule(self, sf, fake_registry):
        state = SimpleNamespace()
        out = asyncio.run(maybe_alert_signal_health(state, _health("warning")))
        assert out["dispatched"] is True
        assert fake_registry and fake_registry[0]["rule"] == "__signal_health__"
        with sf() as db:
            rule = db.execute(
                select(AlertRule).where(AlertRule.name == "__signal_health__")
            ).scalars().one()
            ev = db.execute(select(AlertEvent)).scalars().one()
        assert ev.rule_id == rule.id
        snap = json.loads(ev.snapshot) if isinstance(ev.snapshot, str) else ev.snapshot
        assert snap["kind"] == "signal_health" and snap["status"] == "warning"

    def test_same_status_deduped_same_day(self, sf, fake_registry):
        state = SimpleNamespace()
        asyncio.run(maybe_alert_signal_health(state, _health("warning")))
        out = asyncio.run(maybe_alert_signal_health(state, _health("warning")))
        assert out == {"dispatched": False, "reason": "今日同状态已告警"}
        assert len(fake_registry) == 1

    def test_upgrade_to_drift_redispatches(self, sf, fake_registry):
        """warning → drift 状态升级：同日也允许再发（升级本身是增量信息）。"""
        state = SimpleNamespace()
        asyncio.run(maybe_alert_signal_health(state, _health("warning")))
        out = asyncio.run(maybe_alert_signal_health(state, _health("drift")))
        assert out["dispatched"] is True
        assert len(fake_registry) == 2

    def test_drift_priority_item(self, sf, fake_registry):
        """drift 的告警文本必须携带 CUSUM 证据（可解释，不是干喊）。"""
        state = SimpleNamespace()
        asyncio.run(maybe_alert_signal_health(state, _health("drift")))
        with sf() as db:
            ev = db.execute(select(AlertEvent)).scalars().one()
        text = json.loads(ev.snapshot)["text"] if isinstance(ev.snapshot, str) else ev.snapshot["text"]
        assert "CUSUM" in text
        assert "s_max=1.9" in text
