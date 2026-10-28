"""A shared constituent or fuzzy board name is not an official theme identity."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date
import threading
from types import SimpleNamespace

import pytest

from app.api.routes import market_themes
from app.core.ttl_cache import cache_on


def _identity(code="885517.TI", name="机器人概念"):
    return {"code": code, "name": name, "source": "ths_official", "basis": "exact_name"}


def _request(catalog, fetch):
    svc = SimpleNamespace(get_catalog=lambda **_: [SimpleNamespace(**{**r, "catalog_active": True}) for r in catalog],
                          fetch_board_bars=fetch, revision=0)
    async def directory(kind):
        rows = (await asyncio.to_thread(svc.get_catalog, limit=1000) if kind == "concept" else
                [SimpleNamespace(code="881126.TI", name="汽车零部件", source="ths_official")])
        return {"kind": kind, "source": "ths_official", "catalog_state": "ready", "total": len(rows),
                "synced_at": "2026-10-27T08:00:00+00:00", "items": [
                    {"code": row.code, "name": row.name, "source": row.source, "kind": kind} for row in rows]}
    svc.read_board_catalog = directory
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(theme_catalog=svc)))


def _verify(request, payload, trade_date=date(2026, 10, 12)):
    return market_themes._verify_board_multi_day(request, payload, trade_date)


def _bars():
    return [{"date": f"2026-10-{day:02d}", "close": 100 + day} for day in range(1, 13)]


def test_fuzzy_board_name_cannot_upgrade_source_reason_or_persistence():
    calls = []
    async def fetch(code):
        calls.append(code)
        return _bars()
    request = _request([_identity()], fetch)
    persistence = {"position_risk": False, "checks": [], "grade": "原判据"}
    card = {"theme": "机器人", "board": {"name": "机器人概念", "chg_5d": 88.0},
            "persistence": deepcopy(persistence)}
    asyncio.run(_verify(request, {"themes": [card]}))
    assert calls == [] and card["persistence"] == persistence
    assert card["board"] is None and card["candidate_board"]["chg_5d"] == 88.0
    interval = card["official_interval"]
    assert interval["code"] is None and interval["state"] == "unmapped" and interval["verified"] is False



def test_exact_name_uses_its_source_code_and_reports_kline_date():
    calls = []
    async def fetch(code):
        calls.append(code)
        return _bars()
    request = _request([_identity()], fetch)
    original = {"name": "机器人概念", "chg_3d": 88.0}
    card = {"theme": "机器人概念", "board": deepcopy(original)}
    payload = {"themes": [card]}
    asyncio.run(_verify(request, payload))
    interval = card["official_interval"]
    assert calls == ["885517.TI"] and interval["verified"] is True
    assert (interval["code"], interval["name"], interval["source"]) == ("885517.TI", "机器人概念", "ths_official_kline")
    assert interval["source_as_of"] == "2026-10-12"
    assert card["candidate_board"]["chg_3d"] == 88.0 and card["board"] is None
    asyncio.run(_verify(request, payload))
    assert calls == ["885517.TI"], "repeated source identity reuses the completed read"



def test_source_identity_official_interval_never_rewrites_an_em_candidate():
    async def fetch(_):
        return _bars()

    request = _request([_identity()], fetch)
    candidate = {"board_code": "BK0001", "name": "机器人概念", "chg_5d": 88.0,
                 "source": "eastmoney", "same_concept_verified": False}
    card = {"theme": "机器人概念", "candidate_board": deepcopy(candidate), "board": None}
    asyncio.run(_verify(request, {"themes": [card]}))
    assert card["candidate_board"] == candidate
    assert card["board"] is None
    assert card["official_interval"]["code"] == "885517.TI"
    assert card["official_interval"]["state"] == "verified"


def test_one_verified_card_does_not_hide_another_unverified_identity():
    calls = []
    async def fetch(code):
        calls.append(code)
        return _bars()
    request = _request([_identity(), _identity("886069.TI", "人形机器人")], fetch)
    cards = [{"theme": "机器人概念", "board": {"name": "机器人概念", "multi_day_verified": True}},
             {"theme": "人形机器人", "board": {"name": "人形机器人"}}]
    asyncio.run(_verify(request, {"themes": cards}))
    assert calls == ["885517.TI", "886069.TI"], "old board booleans do not authenticate either interval"
    assert all(c["board"] is None and c["official_interval"]["verified"] for c in cards)



def test_ambiguous_same_name_does_not_choose_a_source_code():
    async def fetch(_):
        raise AssertionError("ambiguous identity must not fetch")
    request = _request([_identity(), _identity("886069.TI")], fetch)
    card = {"theme": "机器人概念", "board": {"name": "机器人概念", "chg_5d": 3.0}}
    asyncio.run(_verify(request, {"themes": [card]}))
    assert card["candidate_board"]["chg_5d"] == 3.0
    assert card["official_interval"]["state"] == "ambiguous" and card["official_interval"]["code"] is None



def test_legacy_fuzzy_upgrade_is_revoked_and_available_inference_restored():
    async def fetch(_):
        raise AssertionError("source label has no exact identity")
    request = _request([_identity()], fetch)
    card = {"theme": "机器人", "board": {"name": "机器人概念", "chg_5d": 12.0, "chg_5d_inferred": 3.0,
        "multi_day_verified": True, "multi_day_source": "ths_official_kline"}}
    asyncio.run(_verify(request, {"themes": [card]}))
    assert card["board"] is None and card["candidate_board"]["chg_5d"] == 3.0
    assert "multi_day_source" not in card["candidate_board"]
    assert card["official_interval"]["state"] == "unmapped" and card["official_interval"]["chg_5d"] is None



def test_missing_catalog_cannot_preserve_a_previous_verification():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(theme_catalog=None)))
    card = {"theme": "机器人概念", "board": {"name": "机器人概念", "chg_5d": 12.0, "chg_5d_inferred": 3.0,
        "multi_day_verified": True, "multi_day_source": "ths_official_kline"}}
    asyncio.run(_verify(request, {"themes": [card]}))
    assert card["board"] is None and card["candidate_board"]["chg_5d"] == 3.0
    assert card["official_interval"]["state"] == "catalog_unavailable"
    assert card["official_interval"]["code"] is None and card["official_interval"]["verified"] is False



def test_changed_catalog_identity_cannot_keep_an_old_verification_on_source_failure():
    calls = []
    async def fetch(code):
        calls.append(code)
        raise RuntimeError("official kline unavailable")
    request = _request([_identity("886100.TI")], fetch)
    card = {"theme": "机器人概念", "board": {"name": "机器人概念", "chg_5d": 12.0, "chg_5d_inferred": 3.0,
        "multi_day_verified": True, "multi_day_source": "ths_official_kline", "multi_day_catalog": _identity(),
        "multi_day_source_code": "885517.TI"}}
    asyncio.run(_verify(request, {"themes": [card]}))
    interval = card["official_interval"]
    assert calls == ["886100.TI"] and interval["code"] == "886100.TI"
    assert interval["state"] == "source_unavailable" and interval["chg_5d"] is None
    assert card["candidate_board"]["chg_5d"] == 3.0 and "multi_day_source_code" not in card["candidate_board"]



def test_theme_board_cache_excludes_old_policy_and_catalog_revision(monkeypatch):
    from app.services import theme_service

    async def fetch(_):
        raise AssertionError("cache test must not use an external source")

    request = _request([], fetch)
    request.app.state.snapshot_service = object()
    provider = SimpleNamespace()
    hub = SimpleNamespace(provider=provider)
    calls = []

    async def build(*_, **__):
        calls.append(1)
        return {"themes": [], "version": len(calls)}

    async def meta(*_):
        return {}

    monkeypatch.setattr(theme_service, "build_theme_board", build)
    monkeypatch.setattr(market_themes, "load_snapshot_map", lambda *_: {})
    monkeypatch.setattr(market_themes, "dated_meta", meta)
    day = date(2026, 10, 23)
    cache_on(request.app.state, "market.themes", 60, maxsize=16).set(
        day, {"data": {"themes": [{"theme": "旧模糊挂靠"}]}, "meta": {}})
    cache_on(request.app.state, "market.themes", 60, maxsize=16).set(
        ("exact-catalog-v2", day, 0), {"data": {"themes": [{"theme": "旧同名资金"}]}, "meta": {}})

    async def run():
        first = await market_themes._theme_board_cached(request, hub, day)
        second = await market_themes._theme_board_cached(request, hub, day)
        request.app.state.theme_catalog.revision += 1
        third = await market_themes._theme_board_cached(request, hub, day)
        return first, second, third

    first, second, third = asyncio.run(run())
    assert first == second and first["data"]["version"] == 1
    assert third["data"]["version"] == 2 and len(calls) == 2


def test_deferred_optional_read_drains_and_is_reused_by_next_payload(monkeypatch):
    monkeypatch.setattr(market_themes, "THEME_KLINE_VIEW_BUDGET_SECONDS", 0.01)
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def fetch(code):
        calls.append(code)
        entered.set()
        await release.wait()
        finished.set()
        return _bars()

    request = _request([_identity()], fetch)
    original = {"themes": [{"theme": "机器人概念", "board": {"name": "机器人概念", "chg_5d": 88.0}}]}

    async def run():
        # This case tests the K-line owner's deadline after the directory is
        # ready. A cold directory has its own bounded-wait coverage below.
        resolution = await market_themes._official_identities_cached(request)
        assert resolution["state"] == "available"
        one, two = deepcopy(original), deepcopy(original)
        reads = [asyncio.create_task(_verify(request, payload)) for payload in (one, two)]
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(asyncio.gather(*reads), 0.5)
            assert not finished.is_set(), "returning a base list does not claim that its source finished"
            assert all(p["themes"][0]["official_interval"]["state"] == "pending" for p in (one, two))
            assert all(p["themes"][0]["candidate_board"]["chg_5d"] == 88.0 for p in (one, two))
        finally:
            release.set()
            await asyncio.gather(*reads, return_exceptions=True)
        await asyncio.wait_for(finished.wait(), 1)
        third = deepcopy(original)
        await _verify(request, third)
        assert third["themes"][0]["official_interval"]["verified"] is True
        assert one["themes"][0]["official_interval"]["state"] == "pending", "background owner must not mutate a returned payload"
        return third

    asyncio.run(run())
    assert calls == ["885517.TI"], "independent cold payload copies share one source owner and its saved result"


def test_canceled_waiter_does_not_cancel_other_reader_or_the_source(monkeypatch):
    monkeypatch.setattr(market_themes, "THEME_KLINE_VIEW_BUDGET_SECONDS", 1)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    canceled_source = []

    async def fetch(code):
        calls.append(code)
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            canceled_source.append(True)
            raise
        return _bars()

    request = _request([_identity()], fetch)
    original = {"themes": [{"theme": "机器人概念", "board": {"name": "机器人概念"}}]}

    async def run():
        abandoned, survivor = deepcopy(original), deepcopy(original)
        one = asyncio.create_task(_verify(request, abandoned))
        two = asyncio.create_task(_verify(request, survivor))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            one.cancel()
            with pytest.raises(asyncio.CancelledError):
                await one
            assert not canceled_source
        finally:
            release.set()
            await asyncio.gather(one, two, return_exceptions=True)
        assert survivor["themes"][0]["official_interval"]["verified"] is True
        await _verify(request, deepcopy(original))

    asyncio.run(run())
    assert calls == ["885517.TI"] and not canceled_source


def test_failure_is_briefly_shared_then_recovers_without_a_new_identity(monkeypatch):
    import app.core.ttl_cache as tc

    clock = {"now": 1000.0}
    monkeypatch.setattr(tc, "monotonic", lambda: clock["now"])
    calls = []

    async def fetch(code):
        calls.append(code)
        if len(calls) == 1:
            raise RuntimeError("source unavailable")
        return _bars()

    request = _request([_identity()], fetch)

    async def run():
        day = date(2026, 10, 12)
        first = await market_themes._official_bars_cached(request, "885517.TI", day)
        second = await market_themes._official_bars_cached(request, "885517.TI", day)
        assert first == second and first["state"] == "unavailable" and first["bars"] == []
        assert len(calls) == 1
        clock["now"] += 5
        third = await market_themes._official_bars_cached(request, "885517.TI", day)
        assert third["state"] == "available"
        request.app.state.theme_catalog.revision += 1
        await market_themes._official_bars_cached(request, "885517.TI", day)

    asyncio.run(run())
    assert calls == ["885517.TI"] * 3


def test_historical_cutoff_and_cache_scope_never_use_future_bars_or_mutate_raw_rows():
    raw = _bars()
    calls = []

    async def fetch(code):
        calls.append(code)
        return raw

    request = _request([_identity()], fetch)

    async def run():
        payload = {"themes": [{"theme": "机器人概念", "board": {"name": "机器人概念", "chg_10d": 99.0}}]}
        await _verify(request, payload, date(2026, 10, 8))
        board = payload["themes"][0]["official_interval"]
        assert board["verified"] is False and board["state"] == "partial"
        assert board["source_as_of"] == board["requested_trade_date"] == "2026-10-08"
        assert board["chg_10d"] is None, "insufficient historical bars do not invent a verified return"
        cached = await market_themes._official_bars_cached(request, "885517.TI", date(2026, 10, 8))
        assert len(cached["bars"]) == 8
        cached["bars"][0]["close"] = -1
        again = await market_themes._official_bars_cached(request, "885517.TI", date(2026, 10, 8))
        assert again["bars"][0]["close"] == 101
        await _verify(request, payload, date(2026, 10, 12))
        board = payload["themes"][0]["official_interval"]
        assert board["verified"] is True and board["source_as_of"] == "2026-10-12"
        missing = await market_themes._official_bars_cached(request, "885517.TI", date(2026, 9, 30))
        assert missing["state"] == "unavailable" and missing["bars"] == [] and missing["as_of"] is None

    asyncio.run(run())
    assert raw == _bars() and calls == ["885517.TI"] * 3


@pytest.mark.parametrize("bad", [{"date": "2026-10-01", "close": float("nan")},
                                  {"date": "2026-10-01", "close": 0},
                                  {"date": "2026-10-01", "close": True},
                                  {"date": "wrong", "close": 101}])
def test_invalid_source_bars_remain_unknown(bad):
    async def fetch(_):
        return [bad]

    request = _request([_identity()], fetch)
    result = asyncio.run(market_themes._official_bars_cached(request, "885517.TI", date(2026, 10, 12)))
    assert result["state"] == "unavailable" and result["bars"] == [] and result["as_of"] is None


@pytest.mark.parametrize("payload", [
    None, [], {}, {"code": False, "data": {"item": []}}, {"code": "0", "data": {"item": []}},
    {"code": 0.0, "data": {"item": []}}, {"code": 0, "data": None},
    {"code": 0, "data": {"item": {}}}, {"code": 0, "data": {"item": [None]}},
    {"code": 503, "data": {"item": [{"date_ms": 1790784000000, "close_price": 100}]}},
])
def test_board_bars_rejects_failed_or_malformed_business_envelopes(payload):
    import httpx

    from app.services.theme_catalog_service import ThemeCatalogService

    async def run():
        svc = ThemeCatalogService.__new__(ThemeCatalogService)
        svc._base = "https://source.invalid"
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload))) as client:
            svc._client = client
            with pytest.raises(RuntimeError, match="incomplete or failed snapshot"):
                await svc.fetch_board_bars("885517.TI")

    asyncio.run(run())


def test_board_bars_accepts_an_explicit_successful_empty_window():
    import httpx

    from app.services.theme_catalog_service import ThemeCatalogService

    async def run():
        svc = ThemeCatalogService.__new__(ThemeCatalogService)
        svc._base = "https://source.invalid"
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"code": 0, "data": {
                    "thscode": "885517.TI", "interval": "1d", "adjust": None, "item": []}}))) as client:
            svc._client = client
            assert await svc.fetch_board_bars("885517.TI") == []

    asyncio.run(run())


def test_exact_source_reason_cannot_put_its_kline_into_a_different_provider_board():
    async def fetch(_code):
        return _bars()
    request = _request([_identity()], fetch)
    card = {"theme": "机器人概念", "board": {"name": "人形机器人", "chg_5d": 88.0}}
    asyncio.run(_verify(request, {"themes": [card]}))
    assert card["board"] is None and card["candidate_board"]["name"] == "人形机器人"
    assert card["candidate_board"]["chg_5d"] == 88.0
    assert card["official_interval"]["code"] == "885517.TI" and card["official_interval"]["verified"] is True



def test_catalog_read_failure_preserves_base_payload_and_briefly_recovers(monkeypatch):
    import app.core.ttl_cache as tc

    clock = {"now": 1000.0}
    monkeypatch.setattr(tc, "monotonic", lambda: clock["now"])
    catalog_calls, bar_calls = [], []

    async def fetch(code):
        bar_calls.append(code)
        return _bars()

    request = _request([_identity()], fetch)
    original_catalog = request.app.state.theme_catalog.get_catalog

    def catalog(**kwargs):
        catalog_calls.append(1)
        if len(catalog_calls) == 1:
            raise OSError("isolated catalog read failure")
        return original_catalog(**kwargs)

    request.app.state.theme_catalog.get_catalog = catalog
    original = {"themes": [{"theme": "机器人概念", "board": {
        "name": "机器人概念", "chg_5d": 88.0, "chg_5d_inferred": 3.0,
        "multi_day_verified": True, "multi_day_source": "ths_official_kline"}}],
        "base_marker": "原始基础数据"}

    async def run():
        for _ in range(2):
            payload = deepcopy(original)
            await _verify(request, payload)
            board = payload["themes"][0]["official_interval"]
            assert payload["base_marker"] == "原始基础数据"
            assert board["chg_5d"] is None and board["verified"] is False
            assert board["state"] == "catalog_unavailable"
            assert board["code"] is None and "multi_day_source" not in board
        assert len(catalog_calls) == 1 and bar_calls == []
        clock["now"] += 5
        payload = deepcopy(original)
        await _verify(request, payload)
        assert payload["themes"][0]["official_interval"]["verified"] is True
        result = await market_themes._official_identities_cached(request)
        result["identities"].clear()
        again = await market_themes._official_identities_cached(request)
        assert {key: again["identities"]["机器人概念"][key] for key in _identity()} == _identity()
        assert again["identities"]["机器人概念"]["kind"] == "concept"

    asyncio.run(run())
    assert len(catalog_calls) == 2 and bar_calls == ["885517.TI"]


def test_slow_catalog_shared_thread_returns_pending_then_is_reused(monkeypatch):
    monkeypatch.setattr(market_themes, "THEME_KLINE_VIEW_BUDGET_SECONDS", 0.01)
    release, finished = threading.Event(), threading.Event()
    catalog_calls, bar_calls = [], []

    async def fetch(code):
        bar_calls.append(code)
        return _bars()

    request = _request([_identity()], fetch)
    original_catalog = request.app.state.theme_catalog.get_catalog
    original = {"themes": [{"theme": "机器人概念", "board": {"name": "机器人概念", "chg_5d": 88.0}}]}

    async def run():
        loop, entered = asyncio.get_running_loop(), asyncio.Event()

        def catalog(**kwargs):
            catalog_calls.append(1)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2), "test must release its real worker"
            finished.set()
            return original_catalog(**kwargs)

        request.app.state.theme_catalog.get_catalog = catalog
        payloads = [deepcopy(original), deepcopy(original)]
        reads = [asyncio.create_task(_verify(request, payload)) for payload in payloads]
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(asyncio.gather(*reads), 0.5)
            assert not finished.is_set() and len(catalog_calls) == 1 and bar_calls == []
            for payload in payloads:
                board = payload["themes"][0]["official_interval"]
                assert board["chg_5d"] is None and board["verified"] is False
                assert board["state"] == "pending" and board["code"] is None
        finally:
            release.set()
            await asyncio.gather(*reads, return_exceptions=True)
        result = await market_themes._official_identities_cached(request)
        assert finished.is_set() and result["state"] == "available"
        fresh = deepcopy(original)
        await _verify(request, fresh)
        assert fresh["themes"][0]["official_interval"]["verified"] is True
        assert payloads[0]["themes"][0]["official_interval"]["state"] == "pending"

    asyncio.run(run())
    assert len(catalog_calls) == 1 and bar_calls == ["885517.TI"]


def test_canceled_catalog_waiter_does_not_abandon_shared_worker_or_cache(monkeypatch):
    monkeypatch.setattr(market_themes, "THEME_KLINE_VIEW_BUDGET_SECONDS", 1)
    release, finished = threading.Event(), threading.Event()
    calls = []

    async def fetch(_code):
        return _bars()

    request = _request([_identity()], fetch)
    original_catalog = request.app.state.theme_catalog.get_catalog
    original = {"themes": [{"theme": "机器人概念", "board": {"name": "机器人概念"}}]}

    async def run():
        loop, entered = asyncio.get_running_loop(), asyncio.Event()

        def catalog(**kwargs):
            calls.append(1)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
            finished.set()
            return original_catalog(**kwargs)

        request.app.state.theme_catalog.get_catalog = catalog
        survivor = deepcopy(original)
        one = asyncio.create_task(_verify(request, deepcopy(original)))
        two = asyncio.create_task(_verify(request, survivor))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            one.cancel()
            with pytest.raises(asyncio.CancelledError):
                await one
            assert not finished.is_set() and len(calls) == 1
        finally:
            release.set()
            await asyncio.gather(one, two, return_exceptions=True)
        assert finished.is_set() and survivor["themes"][0]["official_interval"]["verified"] is True
        await _verify(request, deepcopy(original))
        assert len(calls) == 1

    asyncio.run(run())


def test_catalog_revision_changes_during_read_never_upgrade_old_identity():
    release = threading.Event()
    calls, bar_calls = [], []

    async def fetch(code):
        bar_calls.append(code)
        return _bars()

    request = _request([_identity()], fetch)
    svc = request.app.state.theme_catalog
    original = {"themes": [{"theme": "机器人概念", "board": {"name": "机器人概念", "chg_5d": 88.0}}]}

    async def run():
        loop, entered = asyncio.get_running_loop(), asyncio.Event()

        def catalog(**_kwargs):
            revision = svc.revision
            calls.append(revision)
            if revision == 0:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(2)
            return [SimpleNamespace(**_identity("885517.TI" if revision == 0 else "886069.TI"), catalog_active=True)]

        svc.get_catalog = catalog
        first = deepcopy(original)
        read = asyncio.create_task(_verify(request, first))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            svc.revision = 1
        finally:
            release.set()
            await read
        board = first["themes"][0]["official_interval"]
        assert board["state"] == "catalog_unavailable" and board["chg_5d"] is None
        assert bar_calls == []
        second = deepcopy(original)
        await _verify(request, second)
        assert second["themes"][0]["official_interval"]["code"] == "886069.TI"
        assert calls == [0, 1] and bar_calls == ["886069.TI"]

    asyncio.run(run())
