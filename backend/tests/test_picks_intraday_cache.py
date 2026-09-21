"""猎场机会缓存的并发一致性与请求隔离。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone



def test_opportunity_endpoints_share_one_build_and_isolate_overlays(monkeypatch):
    """并发刷新只能装配一次，且请求级字段不得污染缓存或另一份响应。"""
    from app.picks import intraday_opportunity_runtime as runtime

    state = SimpleNamespace()
    app = SimpleNamespace(state=state)
    calls = 0
    release = asyncio.Event()

    async def fake_uncached(_app, trade_date, top_themes, stocks_per_theme, *, snapshot_bundle=None):
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
    bundles=[]
    async def fake_uncached(_request, trade_date, top_themes, stocks_per_theme, *, snapshot_bundle=None):
        nonlocal calls
        calls += 1
        assert snapshot_bundle is not None
        bundles.append(snapshot_bundle)
        return {"data":{"trade_date":str(trade_date),"themes":[]},"meta":{}}
    monkeypatch.setattr(runtime,"_build_opportunities_uncached",fake_uncached)
    monkeypatch.setattr(runtime,"attach_risk_to_themes",lambda *_:None)
    asyncio.run(runtime.build_opportunities(app,"2026-09-21",5,8))
    svc.last_success += timedelta(seconds=60)
    asyncio.run(runtime.build_opportunities(app,"2026-09-21",5,8))
    assert calls == 2
    assert [bundle[2] for bundle in bundles] == [
        "2026-09-21T01:30:00+00:00", "2026-09-21T01:31:00+00:00"
    ]


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
    async def fake_uncached(_request, trade_date, top_themes, stocks_per_theme, *, snapshot_bundle=None):
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


def _durable_bundle_utc(hour: int = 2, minute: int = 10):
    return (
        {"600001": {"symbol": "600001", "price": 10.0, "change_pct": 1.0}},
        "ready",
        datetime(2026, 9, 21, hour, minute, tzinfo=timezone.utc).isoformat(),
    )


def test_evidence_tick_archives_each_durable_snapshot_once(monkeypatch):
    from app.picks import intraday_opportunity_runtime as runtime
    import app.market.trade_calendar as tc
    import app.services.market_snapshot as market_snapshot

    svc = SimpleNamespace(saved_files=2, snapshot=[])
    state = SimpleNamespace(snapshot_service=svc, hub=object())
    app = SimpleNamespace(state=state)
    calls = 0

    async def fake_build(_app, trade_date, top_themes, stocks_per_theme, *, snapshot_bundle=None):
        nonlocal calls
        calls += 1
        assert str(trade_date) == "2026-09-21"
        assert (top_themes, stocks_per_theme) == (5, 8)
        assert snapshot_bundle == _durable_bundle_utc()
        return {"data": {"decision_evidence": {"state": "ready", "run_id": "r1", "records": 9}}}

    async def fake_trade_date(_hub):
        return datetime(2026, 9, 21).date()

    monkeypatch.setattr(runtime, "build_opportunities", fake_build)
    monkeypatch.setattr(runtime, "durable_snapshot_context", lambda _app: _durable_bundle_utc())
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

    svc = SimpleNamespace(saved_files=3, snapshot=[])
    state = SimpleNamespace(snapshot_service=svc, hub=object())
    app = SimpleNamespace(state=state)
    attempts = 0

    async def fake_build(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic archive failure")
        return {"data": {"decision_evidence": {"state": "ready", "run_id": "r2", "records": 3}}}

    async def fake_trade_date(_hub):
        return datetime(2026, 9, 21).date()

    monkeypatch.setattr(runtime, "build_opportunities", fake_build)
    monkeypatch.setattr(runtime, "durable_snapshot_context", lambda _app: _durable_bundle_utc())
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
    monkeypatch.setattr(runtime, "durable_snapshot_context", lambda _app: _durable_bundle_utc(4, 0))
    monkeypatch.setattr(tc, "in_trading_window", lambda _now=None: False)

    got = asyncio.run(runtime.archive_intraday_evidence_tick(app))
    assert got["state"] == "skipped"
    assert got["reason"] == "outside_trading_window"
    assert state.opportunity_evidence_saved_files == 4


def test_durable_snapshot_context_reads_exact_saved_a_not_current_b(tmp_path):
    import polars as pl
    from app.picks import intraday_opportunity_runtime as runtime

    path = tmp_path / "A.parquet"
    pl.DataFrame([{
        "symbol": "600001", "price": 10.0, "change_pct": 1.0, "amount": 1e8,
    }]).write_parquet(path)
    svc = SimpleNamespace(
        last_saved_path=path,
        last_saved_as_of=datetime(2026, 9, 21, 2, 10, tzinfo=timezone.utc),
        last_saved_state="ready",
        snapshot=[{"symbol": "600001", "price": 10.2, "change_pct": 2.0}],
        last_success=datetime(2026, 9, 21, 2, 11, tzinfo=timezone.utc),
    )
    app = SimpleNamespace(state=SimpleNamespace(snapshot_service=svc))
    snap_by, state, version = runtime.durable_snapshot_context(app)
    assert state == "ready"
    assert version == "2026-09-21T02:10:00+00:00"
    assert snap_by["600001"]["change_pct"] == 1.0
    assert svc.snapshot[0]["change_pct"] == 2.0


def test_durable_snapshot_context_preserves_saved_degraded_state(tmp_path):
    import polars as pl
    from app.picks import intraday_opportunity_runtime as runtime

    path = tmp_path / "fallback.parquet"
    pl.DataFrame([{
        "symbol": "600001", "price": 10.0, "change_pct": 1.0, "amount": 1e8,
    }]).write_parquet(path)
    svc = SimpleNamespace(
        last_saved_path=path,
        last_saved_as_of=datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc),
        last_saved_state="degraded",
    )
    app = SimpleNamespace(state=SimpleNamespace(snapshot_service=svc))
    _snap, state, _version = runtime.durable_snapshot_context(app)
    assert state == "degraded"


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


def test_evidence_tick_missing_durable_metadata_is_visible_failure_and_retryable():
    from app.picks import intraday_opportunity_runtime as runtime

    svc = SimpleNamespace(saved_files=5, snapshot=[])
    state = SimpleNamespace(snapshot_service=svc, hub=object())
    app = SimpleNamespace(state=state)
    try:
        asyncio.run(runtime.archive_intraday_evidence_tick(app))
    except RuntimeError as exc:
        assert "no exact saved path/as_of metadata" in str(exc)
    else:
        raise AssertionError("missing durable identity must be a visible scheduler failure")
    assert not hasattr(state, "opportunity_evidence_saved_files")


def test_evidence_tick_uses_saved_fact_time_not_late_consumer_clock(monkeypatch):
    from app.picks import intraday_opportunity_runtime as runtime
    import app.market.trade_calendar as tc
    import app.services.market_snapshot as market_snapshot

    svc = SimpleNamespace(saved_files=6, snapshot=[])
    state = SimpleNamespace(snapshot_service=svc, hub=object())
    app = SimpleNamespace(state=state)
    bundle = _durable_bundle_utc(3, 29)  # 11:29 Beijing
    seen = {}

    async def fake_build(_app, trade_date, _top, _stocks, *, snapshot_bundle=None):
        seen["bundle"] = snapshot_bundle
        return {"data": {"decision_evidence": {"state": "ready", "run_id": "r1129", "records": 1}}}

    async def fake_trade_date(_hub):
        return datetime(2026, 9, 21).date()

    def fake_window(at):
        seen["window_time"] = at
        return at.hour == 11 and at.minute == 29

    monkeypatch.setattr(runtime, "durable_snapshot_context", lambda _app: bundle)
    monkeypatch.setattr(runtime, "build_opportunities", fake_build)
    monkeypatch.setattr(tc, "in_trading_window", fake_window)
    monkeypatch.setattr(market_snapshot, "default_trade_date", fake_trade_date)

    got = asyncio.run(runtime.archive_intraday_evidence_tick(app))
    assert got["state"] == "archived"
    assert seen["bundle"] == bundle
    assert (seen["window_time"].hour, seen["window_time"].minute) == (11, 29)
    assert state.opportunity_evidence_saved_files == 6


def test_uncached_runtime_archives_with_snapshot_fact_time_without_name_shadow(monkeypatch):
    """真实 shared builder 必须走到 archive；防局部 snapshot_as_of 遮蔽同名函数。"""
    from app.picks import intraday_opportunity_runtime as runtime
    import app.picks.board_surge as board_surge
    import app.picks.intraday_opportunity as opportunity
    import app.picks.opportunity_learning as learning
    import app.picks.tradability as tradability
    import app.services.theme_service as theme_service

    class Svc:
        def __init__(self):
            self.snapshot = [{
                "symbol": "600001", "name": "甲", "price": 10.0,
                "change_pct": 1.0, "amount": 2e8,
            }]
            self.last_success = datetime(2026, 9, 21, 3, 10, tzinfo=timezone.utc)
            self.parquet_dir = ""

        def freshness(self):
            return SimpleNamespace(state="ready", as_of=self.last_success)

    svc = Svc()
    app = SimpleNamespace(state=SimpleNamespace(snapshot_service=svc, hub=SimpleNamespace(provider=object())))
    seen = {}
    linkage_seen = {}

    async def fake_board(_provider, _trade_date, snapshot_map=None):
        # The board/premium lane must see the same A snapshot captured at builder entry.
        assert snapshot_map["600001"]["change_pct"] == 1.0
        # Simulate a real MarketSnapshotService refresh while expensive provider IO runs.
        svc.snapshot = [{
            "symbol": "600001", "name": "甲", "price": 10.2,
            "change_pct": 2.0, "amount": 3e8,
        }]
        svc.last_success = datetime(2026, 9, 21, 3, 11, tzinfo=timezone.utc)
        return {"themes": [{"theme": "测试题材", "ladder": []}]}

    def fake_assemble(_board, _hot, _hot_available, **_kwargs):
        return {
            "themes": [{
                "theme": "测试题材", "stocks": [],
                "participants": [{"symbol": "600001", "name": "甲", "change_pct": 1.0}],
            }],
            "summary": {"limit_up_total": 0},
        }

    class FakeIndexCache:
        def get(self):
            return {}, {}

    def fake_archive(payload, *, trade_date, as_of, **_kwargs):
        seen.update(payload=payload, trade_date=trade_date, as_of=as_of)
        return {"run_id": "real-builder-run", "records": 0}

    def _unexpected_parquet(*_args, **_kwargs):
        raise AssertionError("live frozen snapshot should prevent Parquet fallback")

    monkeypatch.setattr(runtime, "load_snapshot_map", _unexpected_parquet)
    monkeypatch.setattr(theme_service, "build_theme_board", fake_board)
    monkeypatch.setattr(theme_service, "_pick_provider", lambda *_args: None)
    monkeypatch.setattr(opportunity, "assemble", fake_assemble)
    monkeypatch.setattr(board_surge, "get_index_cache", lambda _app: FakeIndexCache())
    monkeypatch.setattr(tradability, "index_views", lambda _index: ({}, {}))
    monkeypatch.setattr(tradability, "attach_tradability", lambda *_args, **_kwargs: None)

    def fake_attach_participants(_themes, **kwargs):
        linkage_seen.update(kwargs)
        assert kwargs["snapshot_by"]["600001"]["change_pct"] == 1.0
        assert kwargs["snapshot_as_of"] == "2026-09-21T03:10:00+00:00"
        return {"snapshot_state": "ready", "snapshot_as_of": kwargs["snapshot_as_of"], "missing_quote": 0}

    monkeypatch.setattr(opportunity, "attach_participants", fake_attach_participants)
    monkeypatch.setattr(learning, "archive_intraday_pipeline", fake_archive)
    import app.market.trade_calendar as trade_calendar
    monkeypatch.setattr(trade_calendar, "in_trading_window", lambda *_args, **_kwargs: False)

    got = asyncio.run(
        runtime._build_opportunities_uncached(
            app, datetime(2026, 9, 21).date(), top_themes=5, stocks_per_theme=8
        )
    )
    assert got["data"]["decision_evidence"] == {
        "state": "ready", "run_id": "real-builder-run", "records": 0
    }
    assert seen["trade_date"] == "2026-09-21"
    assert seen["as_of"] == datetime(2026, 9, 21, 11, 10)
    assert linkage_seen["snapshot_as_of"] == "2026-09-21T03:10:00+00:00"
    assert svc.last_success == datetime(2026, 9, 21, 3, 11, tzinfo=timezone.utc)
    assert svc.snapshot[0]["change_pct"] == 2.0
