"""One automatic review waits for its existing daily owner, with a deadline."""
import asyncio
from datetime import date, datetime
import json
import logging
from types import SimpleNamespace

import pytest

from app.core.bjtime import BJ_TZ
from app.review import daily_market as daily
from app.review import service as review


DAY = date(2026, 10, 26)


def clock(hour, minute=0):
    return datetime(2026, 10, 26, hour, minute, tzinfo=BJ_TZ)


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / "market.duckdb"
    path.write_bytes(b"not opened by readiness")
    monkeypatch.setattr(daily, "DEFAULT_DB_PATH", path)
    return path


def publish(path):
    (path.parent / "sync_state.json").write_text(json.dumps({
        "last_sync_date": "20261026", "at": "2026-10-26T16:31:32+08:00"}))
    (path.parent / "quality_report.json").write_text(json.dumps({
        "available": True, "status": "warn", "error_count": 0, "issues": [],
        "synced_at": "2026-10-26T16:31:32+0800",
        "sync": {"daily": {"dmax": int(clock(0).timestamp() * 1000)}},
    }))


def reason(now, **extra):
    return daily.daily_source_wait_reason(now, enabled=True, **extra)


def test_wait_does_not_hash_or_open_the_warehouse_and_reuses_owner_metadata(source, monkeypatch):
    monkeypatch.setattr(daily, "_digest", lambda *a: pytest.fail("readiness must not hash files"))
    monkeypatch.setattr(daily, "_read_rows", lambda *a: pytest.fail("readiness must not open DuckDB"))
    assert "16:40" in reason(clock(15, 30))
    assert reason(clock(16, 30))
    publish(source)
    assert reason(clock(16, 32)) is None


def test_default_deadline_and_changed_owner_schedule_are_finite(source):
    assert reason(clock(16, 39))
    assert reason(clock(16, 40)) is None
    assert reason(clock(17, 3), sync_hour=17, sync_minute=0, sync_interval_seconds=120)
    assert reason(clock(17, 4), sync_hour=17, sync_minute=0, sync_interval_seconds=120) is None
    assert reason(clock(15, 59), sync_hour=15, sync_minute=0, sync_interval_seconds=60)
    assert reason(clock(16), sync_hour=15, sync_minute=0, sync_interval_seconds=60) is None


def test_disabled_missing_db_and_deadline_skip_all_dependency_reads(source, monkeypatch):
    monkeypatch.setattr(daily, "_document", lambda *a: pytest.fail("dependency must not be read"))
    assert daily.daily_source_wait_reason(clock(15, 30), enabled=False) is None
    assert reason(clock(16, 40)) is None
    source.unlink()
    assert reason(clock(15, 30)) is None


@pytest.mark.parametrize("settings", [
    {"sync_interval_seconds": 0}, {"sync_interval_seconds": float("nan")},
    {"sync_interval_seconds": 1e308}, {"sync_hour": 25},
    {"sync_hour": 23, "sync_minute": 59},
])
def test_unusable_schedule_cannot_silently_wait_forever(source, settings):
    assert reason(clock(15, 30), **settings) is None


@pytest.mark.parametrize("change", ["yesterday", "failed", "future", "bad_json", "wrong_content_day"])
def test_metadata_does_not_turn_bad_or_undated_source_into_ready(source, change):
    publish(source)
    if change == "bad_json":
        (source.parent / "quality_report.json").write_text("bad")
    else:
        name = "sync_state.json" if change == "yesterday" else "quality_report.json"
        path = source.parent / name
        doc = json.loads(path.read_text())
        if change == "yesterday":
            doc["last_sync_date"] = "20261023"
        elif change == "failed":
            doc["status"] = "fail"
        elif change == "future":
            doc["synced_at"] = "2026-10-27T16:30:00+08:00"
        else:
            doc["sync"]["daily"]["dmax"] -= 86400_000
        path.write_text(json.dumps(doc))
    assert reason(clock(16, 32))
    assert reason(clock(16, 40)) is None


def run_ticks(monkeypatch, *, moments, exists=False, day_state=True, pending=None):
    remaining = iter(moments)
    calls, dependency_calls, delays = [], [], []
    current = [True]

    def now():
        try:
            return next(remaining)
        except StopIteration:
            current.clear()
            return moments[-1]

    class Stop:
        def is_set(self):
            return not current

        async def wait(self):
            delays.append(True)
            raise TimeoutError

    async def calendar(provider):
        return [DAY]

    async def run(day):
        calls.append(day)

    def waiting(*args, **kwargs):
        dependency_calls.append(args[0])
        return pending(args[0]) if pending else None

    from app.review import storage
    from app.picks import source_events

    monkeypatch.setattr(review, "beijing_now", now)
    monkeypatch.setattr(review.tc, "trading_days", calendar)
    monkeypatch.setattr(review.tc, "is_trade_day_on", lambda *a: day_state)
    monkeypatch.setattr(storage, "report_exists", lambda *a: exists or bool(calls))
    monkeypatch.setattr(daily, "daily_source_wait_reason", waiting)
    monkeypatch.setattr(source_events, "record_review_report", lambda *a: None)
    svc = SimpleNamespace(hub=SimpleNamespace(provider=None), session_factory=None, run=run)
    asyncio.run(review.review_scheduler(svc, stop=Stop(), daily_sync_enabled=True,
                                       check_interval_seconds=60))
    return calls, dependency_calls, delays


def test_scheduler_yields_each_wait_tick_and_generates_once_when_source_ready(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    calls, dependencies, delays = run_ticks(monkeypatch,
        moments=[clock(15, 30), clock(15, 31), clock(16, 32)],
        pending=lambda now: "等待同一来源" if now.hour < 16 else None)
    assert calls == [DAY]
    assert len(dependencies) == 3 and len(delays) == 4
    assert caplog.text.count("等待同一来源") == 1


def test_scheduler_deadline_generates_once_with_truthful_missing_source(source, monkeypatch):
    actual = daily.daily_source_wait_reason
    calls, dependencies, delays = run_ticks(monkeypatch,
        moments=[clock(15, 30), clock(16, 39), clock(16, 40)],
        pending=lambda now: actual(now, enabled=True))
    assert calls == [DAY] and len(dependencies) == 3 and len(delays) == 4


@pytest.mark.parametrize("exists,day_state", [(True, True), (False, False), (False, None)])
def test_existing_report_nontrade_or_unknown_calendar_never_read_dependencies(monkeypatch, exists, day_state):
    calls, dependencies, _ = run_ticks(monkeypatch, moments=[clock(15, 30)],
                                     exists=exists, day_state=day_state,
                                     pending=lambda now: pytest.fail("dependency was read"))
    assert calls == [] and dependencies == []
