"""The registered event collector uses one Beijing session on every host."""
import asyncio
import datetime as dt
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from app.bootstrap.schedulers import register_schedulers
from app.core import bjtime
from app.core.scheduler import SchedulerRegistry


@pytest.mark.parametrize("host_zone", [dt.timezone.utc, bjtime.BJ_TZ])
@pytest.mark.parametrize("beijing_time,expected", [
    ("2026-10-29T09:14:59+08:00", True),
    ("2026-10-29T09:15:00+08:00", False),
    ("2026-10-29T10:00:00+08:00", False),
    ("2026-10-29T15:04:59+08:00", False),
    ("2026-10-29T15:05:00+08:00", True),
])
def test_registered_event_collector_scope_is_host_timezone_independent(monkeypatch, host_zone, beijing_time, expected):
    from app.api.routes import events

    instant = dt.datetime.fromisoformat(beijing_time)
    real_datetime = dt.datetime

    class Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz is not None else instant.astimezone(host_zone).replace(tzinfo=None)

    recorded = []

    async def collect(state, *, include_limit_up=False):
        recorded.append(include_limit_up)
        return {}

    ticks = {}
    original_add = SchedulerRegistry.add_periodic

    def capture_tick(self, name, tick, **kwargs):
        ticks[name] = tick
        return original_add(self, name, tick, **kwargs)

    monkeypatch.setattr(events, "collect_news_events", collect)
    monkeypatch.setattr(SchedulerRegistry, "add_periodic", capture_tick)
    services = SimpleNamespace(hub=SimpleNamespace(run=None), alert_engine=SimpleNamespace(run=None),
                               risk_engine=None, paper=None, snapshot_service=None, review=None)
    registry = SchedulerRegistry()
    register_schedulers(registry, FastAPI(), services)
    # Freeze only datetime reads, never the event-loop/monotonic clock.
    monkeypatch.setattr(dt, "datetime", Clock)
    monkeypatch.setattr(bjtime, "datetime", Clock)
    asyncio.run(ticks["event-collector"]())
    assert recorded == [expected]
    assert all(row["state"] != "running" for row in registry.snapshot())
