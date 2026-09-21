"""猎场机会缓存的并发一致性与请求隔离。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from app.core.bjtime import BJ_TZ


def test_opportunity_endpoints_share_one_build_and_isolate_overlays(monkeypatch):
    """并发刷新只能装配一次，且请求级字段不得污染缓存或另一份响应。"""
    from app.picks import intraday_opportunity_runtime as runtime

    state = SimpleNamespace()
    app = SimpleNamespace(state=state)
    calls = 0
    release = asyncio.Event()

    async def fake_uncached(_app, trade_date, top_themes, stocks_per_theme):
        nonlocal calls
        calls += 1
        await release.wait()
        return {
            "data": {
                "trade_date": str(trade_date),
                "themes": [{
                    "theme": "算力",
                    "participants": [{"symbol": f"60000{i}"} for i in range(8)],
                    "stocks": [],
                }],
            },
            "meta": {"top_themes": top_themes, "stocks_per_theme": stocks_per_theme},
        }

    overlay_calls = 0

    def fake_overlay(data, _snapshot):
        nonlocal overlay_calls
        overlay_calls += 1
        data["request_overlay"] = overlay_calls

    monkeypatch.setattr(runtime, "_build_opportunities_uncached", fake_uncached)
    monkeypatch.setattr(runtime, "attach_risk_to_themes", fake_overlay)
    monkeypatch.setattr(runtime, "snapshot_by", lambda _app: {})

    async def run():
        one = asyncio.create_task(runtime.build_opportunities(app, "2026-09-15", 5, 8))
        two = asyncio.create_task(runtime.build_opportunities(app, "2026-09-15", 5, 8))
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(one, two)
        third = await runtime.build_opportunities(app, "2026-09-15", 5, 8)
        return first, second, third

    first, second, third = asyncio.run(run())

    assert calls == 1, "同键并发未命中必须单飞，禁止 1/8 只结果互相覆盖缓存"
    assert [len(x["data"]["themes"][0]["participants"]) for x in (first, second, third)] == [8, 8, 8]
    assert {first["data"]["request_overlay"], second["data"]["request_overlay"], third["data"]["request_overlay"]} == {1, 2, 3}
    first["data"]["themes"][0]["participants"].clear()
    assert len(second["data"]["themes"][0]["participants"]) == 8
    assert len(third["data"]["themes"][0]["participants"]) == 8


def test_opportunity_cache_key_tracks_snapshot_version(monkeypatch):
    from app.picks import intraday_opportunity_runtime as runtime
    svc=SimpleNamespace(last_success=datetime(2026,9,21,1,30,tzinfo=timezone.utc), snapshot=[])
    state=SimpleNamespace(snapshot_service=svc)
    app=SimpleNamespace(state=state)
    calls=0
    async def fake_uncached(_request, trade_date, top_themes, stocks_per_theme):
        nonlocal calls
        calls += 1
        return {"data":{"trade_date":str(trade_date),"themes":[]},"meta":{}}
    monkeypatch.setattr(runtime,"_build_opportunities_uncached",fake_uncached)
    monkeypatch.setattr(runtime,"attach_risk_to_themes",lambda *_:None)
    asyncio.run(runtime.build_opportunities(app,"2026-09-21",5,8))
    svc.last_success += timedelta(seconds=60)
    asyncio.run(runtime.build_opportunities(app,"2026-09-21",5,8))
    assert calls == 2


def test_opportunity_cache_key_tracks_snapshot_freshness_state(monkeypatch):
    from app.picks import intraday_opportunity_runtime as runtime
    class Svc:
        snapshot=[]
        last_success=datetime(2026,9,21,1,30,tzinfo=timezone.utc)
        state="ready"
        def freshness(self):
            return SimpleNamespace(state=self.state, as_of=self.last_success)
    svc=Svc(); state=SimpleNamespace(snapshot_service=svc)
    app=SimpleNamespace(state=state)
    calls=0
    async def fake_uncached(_request, trade_date, top_themes, stocks_per_theme):
        nonlocal calls; calls += 1
        return {"data":{"trade_date":str(trade_date),"themes":[]},"meta":{}}
    monkeypatch.setattr(runtime,"_build_opportunities_uncached",fake_uncached)
    monkeypatch.setattr(runtime,"attach_risk_to_themes",lambda *_:None)
    asyncio.run(runtime.build_opportunities(app,"2026-09-21",5,8))
    svc.state="stale"  # as_of 不变，仅 freshness 状态变；也必须失效缓存
    asyncio.run(runtime.build_opportunities(app,"2026-09-21",5,8))
    assert calls == 2


def test_ever_sealed_identity_comes_from_full_board_not_display_quota():
    from app.picks.intraday_opportunity_runtime import _ever_sealed_symbols
    board={"themes":[
        {"ladder":[{"symbol":"600001"},{"symbol":"600002"}]},
        {"ladder":[{"symbol":"600003"}]},
    ]}
    assert _ever_sealed_symbols(board) == {"600001","600002","600003"}


def test_snapshot_as_of_uses_market_fact_time_not_consumer_clock():
    from app.picks.intraday_opportunity_runtime import snapshot_as_of

    svc = SimpleNamespace(
        snapshot=[],
        last_success=datetime(2026, 9, 21, 2, 5, tzinfo=timezone.utc),
    )
    app = SimpleNamespace(state=SimpleNamespace(snapshot_service=svc))
    assert snapshot_as_of(app) == datetime(2026, 9, 21, 10, 5)


def test_evidence_tick_archives_each_durable_snapshot_once(monkeypatch):
    from app.picks import intraday_opportunity_runtime as runtime
    import app.market.trade_calendar as tc
    import app.services.market_snapshot as market_snapshot

    class Fresh:
        state = "ready"
        as_of = datetime(2026, 9, 21, 2, 10, tzinfo=timezone.utc)
        def is_usable(self):
            return True

    svc = SimpleNamespace(saved_files=2, freshness=lambda: Fresh(), snapshot=[])
    state = SimpleNamespace(snapshot_service=svc, hub=object())
    app = SimpleNamespace(state=state)
    calls = 0

    async def fake_build(_app, trade_date, top_themes, stocks_per_theme):
        nonlocal calls
        calls += 1
        assert str(trade_date) == "2026-09-21"
        assert (top_themes, stocks_per_theme) == (5, 8)
        return {"data": {"decision_evidence": {"state": "ready", "run_id": "r1", "records": 9}}}

    async def fake_trade_date(_hub):
        return datetime(2026, 9, 21).date()

    monkeypatch.setattr(runtime, "build_opportunities", fake_build)
    monkeypatch.setattr(runtime, "beijing_now", lambda: datetime(2026, 9, 21, 10, 10, tzinfo=BJ_TZ))
    monkeypatch.setattr(tc, "in_trading_window", lambda _now=None: True)
    monkeypatch.setattr(market_snapshot, "default_trade_date", fake_trade_date)

    first = asyncio.run(runtime.archive_intraday_evidence_tick(app))
    second = asyncio.run(runtime.archive_intraday_evidence_tick(app))
    assert first == {"state": "archived", "saved_files": 2, "run_id": "r1", "records": 9}
    assert second["state"] == "idle"
    assert calls == 1
    assert state.opportunity_evidence_saved_files == 2


def test_evidence_tick_failure_does_not_advance_cursor(monkeypatch):
    from app.picks import intraday_opportunity_runtime as runtime
    import app.market.trade_calendar as tc
    import app.services.market_snapshot as market_snapshot

    class Fresh:
        state = "ready"
        as_of = datetime(2026, 9, 21, 2, 10, tzinfo=timezone.utc)
        def is_usable(self):
            return True

    svc = SimpleNamespace(saved_files=3, freshness=lambda: Fresh(), snapshot=[])
    state = SimpleNamespace(snapshot_service=svc, hub=object())
    app = SimpleNamespace(state=state)
    attempts = 0

    async def fake_build(*_args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic archive failure")
        return {"data": {"decision_evidence": {"state": "ready", "run_id": "r2", "records": 3}}}

    async def fake_trade_date(_hub):
        return datetime(2026, 9, 21).date()

    monkeypatch.setattr(runtime, "build_opportunities", fake_build)
    monkeypatch.setattr(runtime, "beijing_now", lambda: datetime(2026, 9, 21, 10, 10, tzinfo=BJ_TZ))
    monkeypatch.setattr(tc, "in_trading_window", lambda _now=None: True)
    monkeypatch.setattr(market_snapshot, "default_trade_date", fake_trade_date)

    try:
        asyncio.run(runtime.archive_intraday_evidence_tick(app))
    except RuntimeError as exc:
        assert "synthetic archive failure" in str(exc)
    else:
        raise AssertionError("first failed archive must propagate to scheduler registry")
    assert not hasattr(state, "opportunity_evidence_saved_files")

    second = asyncio.run(runtime.archive_intraday_evidence_tick(app))
    assert second["state"] == "archived"
    assert attempts == 2
    assert state.opportunity_evidence_saved_files == 3


def test_evidence_tick_consumes_outside_session_snapshot_without_replay(monkeypatch):
    from app.picks import intraday_opportunity_runtime as runtime
    import app.market.trade_calendar as tc

    svc = SimpleNamespace(saved_files=4, snapshot=[])
    state = SimpleNamespace(snapshot_service=svc, hub=object())
    app = SimpleNamespace(state=state)
    monkeypatch.setattr(runtime, "beijing_now", lambda: datetime(2026, 9, 21, 12, 0, tzinfo=BJ_TZ))
    monkeypatch.setattr(tc, "in_trading_window", lambda _now=None: False)

    got = asyncio.run(runtime.archive_intraday_evidence_tick(app))
    assert got["state"] == "skipped"
    assert got["reason"] == "outside_trading_window"
    assert state.opportunity_evidence_saved_files == 4


def test_runtime_normalizes_http_request_to_app_state():
    from app.picks.intraday_opportunity_runtime import _state

    state = SimpleNamespace(marker="state")
    app = SimpleNamespace(state=state)
    request = SimpleNamespace(app=app)
    assert _state(request) is state
    assert _state(app) is state
    assert _state(state) is state


def test_intraday_route_is_thin_wrapper_over_shared_runtime(monkeypatch):
    from app.api.routes import picks_intraday as route

    app = SimpleNamespace(state=SimpleNamespace(hub=object()))
    request = SimpleNamespace(app=app)
    seen = {}

    async def fake_trade_date(hub):
        assert hub is app.state.hub
        return datetime(2026, 9, 21).date()

    async def fake_build(holder, trade_date, top_themes, stocks_per_theme):
        seen.update(
            holder=holder, trade_date=trade_date,
            top_themes=top_themes, stocks_per_theme=stocks_per_theme,
        )
        return {"data": {"themes": []}, "meta": {}}

    monkeypatch.setattr(route, "default_trade_date", fake_trade_date)
    monkeypatch.setattr(route, "_build_opportunities", fake_build)
    got = asyncio.run(route.intraday_opportunities(request, top_themes=5, stocks_per_theme=8))
    assert got == {"data": {"themes": []}, "meta": {}}
    assert seen == {
        "holder": request,
        "trade_date": datetime(2026, 9, 21).date(),
        "top_themes": 5, "stocks_per_theme": 8,
    }


def test_evidence_tick_unusable_snapshot_is_visible_failure_and_retryable(monkeypatch):
    from app.picks import intraday_opportunity_runtime as runtime
    import app.market.trade_calendar as tc

    class Fresh:
        state = "stale"
        as_of = datetime(2026, 9, 21, 2, 10, tzinfo=timezone.utc)
        def is_usable(self):
            return False

    svc = SimpleNamespace(saved_files=5, freshness=lambda: Fresh(), snapshot=[])
    state = SimpleNamespace(snapshot_service=svc, hub=object())
    app = SimpleNamespace(state=state)
    monkeypatch.setattr(
        runtime, "beijing_now",
        lambda: datetime(2026, 9, 21, 10, 10, tzinfo=BJ_TZ),
    )
    monkeypatch.setattr(tc, "in_trading_window", lambda _now=None: True)

    try:
        asyncio.run(runtime.archive_intraday_evidence_tick(app))
    except RuntimeError as exc:
        assert "not usable" in str(exc)
    else:
        raise AssertionError("unusable new snapshot must be a visible scheduler tick failure")
    assert not hasattr(state, "opportunity_evidence_saved_files")
