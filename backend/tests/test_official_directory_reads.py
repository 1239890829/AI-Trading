"""Official identities load independently of optional market enhancement."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_api_token
from app.api.routes.theme_catalog import router
from app.core import device_access as access, device_policy
from app.core.config import settings
from app.services.theme_catalog_service import ThemeCatalogService, source_saved_clock

ROBOT, HUMAN = "885517.TI", "886069.TI"


def svc_for(monkeypatch, payload, calls=None, entered=None, release=None):
    svc = ThemeCatalogService(lambda: pytest.fail("industry read must not use concept DB"), api_key="fixture")
    async def get(*args, **kwargs):
        if calls is not None:
            calls.append(kwargs["params"])
        if entered is not None:
            entered.set()
            await release.wait()
        return httpx.Response(200, json=payload, request=httpx.Request("GET", "https://source.invalid"))
    monkeypatch.setattr(svc._client, "get", get)
    return svc


@pytest.mark.parametrize("code", [False, 0.0, 1, "0"])
def test_non_success_envelope_with_residual_items_cannot_be_official(monkeypatch, code):
    async def run():
        svc = svc_for(monkeypatch, {"code": code, "data": {"item": [{"thscode": ROBOT, "name": "机器人概念"}]}})
        with pytest.raises(RuntimeError):
            await svc.read_board_catalog("industry")
        await svc.aclose()
    asyncio.run(run())


def test_directory_identity_is_original_and_cached_returns_are_independent(monkeypatch):
    async def run():
        calls = []
        svc = svc_for(monkeypatch, {"code": 0, "data": {"item": [{"thscode": "881101.TI", "name": "种植业与林业"}, {"thscode": "884001.TI", "name": "种子生产"}]}}, calls)
        result = await svc.read_board_catalog("industry")
        result["items"][0]["name"] = "invented"
        again = await svc.read_board_catalog("industry")
        assert [(r["code"], r["name"]) for r in again["items"]] == [("881101.TI", "种植业与林业"), ("884001.TI", "种子生产")]
        assert again["synced_at"].endswith("+00:00") and calls == [{"tag": "industry"}]
        await svc.aclose()
    asyncio.run(run())


def test_partial_snapshot_never_uses_readiness_as_market_time_or_missing_as_zero(monkeypatch):
    async def run():
        calls = []
        svc = svc_for(monkeypatch, {"code": 0, "data": {"timestamp": 1716134400000, "item": [{"thscode": ROBOT, "last_price": 100, "price_change_ratio_pct": 0}]}}, calls)
        result = await svc.read_board_snapshots([HUMAN, ROBOT, ROBOT])
        assert result["available"] and result["missing_codes"] == [HUMAN]
        assert result["source_as_of"] is None and result["source_ready_at"] is not None
        result["rows"][0]["change_pct"] = 99
        assert (await svc.read_board_snapshots([ROBOT, HUMAN]))["rows"][0]["change_pct"] == 0
        assert calls == [{"thscodes": f"{ROBOT},{HUMAN}"}]
        await svc.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("items", [[{"thscode": "886002.TI"}], [{"thscode": ROBOT}, {"thscode": ROBOT}], [{"thscode": ROBOT, "price_change_ratio_pct": True}]])
def test_bad_or_foreign_snapshot_cannot_fill_another_identity(monkeypatch, items):
    async def run():
        svc = svc_for(monkeypatch, {"code": 0, "data": {"item": items}})
        result = await svc.read_board_snapshots([ROBOT])
        assert result["available"] is False and result["rows"] == [] and result["missing_codes"] == [ROBOT]
        await svc.aclose()
    asyncio.run(run())


def test_canceled_waiters_do_not_abandon_source_or_duplicate_request(monkeypatch):
    async def run():
        calls, entered, release = [], asyncio.Event(), asyncio.Event()
        svc = svc_for(monkeypatch, {"code": 0, "data": {"item": [{"thscode": ROBOT}]}}, calls, entered, release)
        one = asyncio.create_task(svc.read_board_snapshots([ROBOT])); two = asyncio.create_task(svc.read_board_snapshots([ROBOT]))
        await entered.wait(); one.cancel(); two.cancel(); await asyncio.gather(one, two, return_exceptions=True)
        release.set(); await asyncio.sleep(0)
        assert (await svc.read_board_snapshots([ROBOT]))["available"] and len(calls) == 1
        await svc.aclose()
    asyncio.run(run())


def context(svc):
    app = FastAPI(); app.state.theme_catalog = svc; app.state.hub = SimpleNamespace()
    app.state.snapshot_service = SimpleNamespace(
        versioned_snapshot=lambda: ([{"symbol": "300002", "price": 15, "change_pct": 1}], datetime(2026, 1, 1, tzinfo=timezone.utc)),
        freshness=lambda: SimpleNamespace(model_dump=lambda **kw: {"state": "stale", "as_of": "2026-01-01T00:00:00+00:00"}))
    app.include_router(router, prefix="/api", dependencies=[Depends(require_api_token)])
    return TestClient(app)


def test_fast_labels_and_members_return_without_remote_price_or_pool():
    async def directory(kind):
        return {"kind": kind, "catalog_state": "stale", "source": "ths_official", "items": [{"code": ROBOT, "name": "机器人概念"}]}
    def forbidden(*args, **kwargs):
        pytest.fail("base read invoked optional network enrichment")
    svc = SimpleNamespace(read_board_catalog=directory, catalog_size=lambda: 390,
        get_members=lambda code: [SimpleNamespace(symbol="300002", name="神州泰岳", synced_at=datetime(2020, 1, 1))],
        get_official_for_symbol=lambda symbol: [{"theme_code": ROBOT, "theme_name": "机器人概念", "source": "ths_official"}],
        sync_members=forbidden, day_changes=forbidden)
    probe = context(svc)
    data = probe.get(f"/api/themes/catalog/{ROBOT}/detail?enrichment=snapshot").json()["data"]
    assert data["name"] == "机器人概念" and data["members"][0]["price"] == 15
    assert data["members"][0]["limit_up"] is None and data["limit_up_count"] is None
    assert data["membership_state"] == "stale" and data["quote_freshness"]["state"] == "stale"
    assert data["membership_saved_at"] == "2020-01-01T00:00:00+00:00" and data["source_as_of"] is None
    labels = probe.get("/api/themes/stock/300002?enrichment=snapshot").json()["data"]
    assert labels["official"][0]["theme_name"] == "机器人概念" and labels["attribution_state"] == "not_requested"


def test_ordinary_paired_reader_uses_exact_new_route_and_unknown_code_cannot_call_source(tmp_path, monkeypatch):
    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "devices.sqlite3"); monkeypatch.setattr(settings, "api_token", "fixture-server")
    calls = []
    async def directory(kind):
        return {"kind": kind, "source": "ths_official", "items": [{"code": ROBOT, "name": "机器人概念"}]}
    async def quotes(codes):
        calls.append(codes); return {"available": True, "rows": [{"code": ROBOT, "change_pct": 0}], "missing_codes": []}
    probe = context(SimpleNamespace(read_board_catalog=directory, read_board_snapshots=quotes))
    store = access.get_device_store(); origin = "http://directory.test:3000"
    admin, _ = store.bootstrap(store.bootstrap_code(), "测试管理设备", origin)
    token, row = store.start("普通测试设备", origin); store.manage(admin, origin, row["id"], "approve")
    assert store.authorize(token, origin)["maintenance"] is False
    headers = {"X-API-Token": "fixture-server", access.BROWSER_HEADER: "1", access.SESSION_HEADER: token, access.ORIGIN_HEADER: origin}
    url = f"/api/themes/catalog/quotes?kind=concept&codes={ROBOT}"
    assert probe.get(url).status_code == 401 and probe.get(url, headers=headers).status_code == 200
    assert probe.get(url, headers={**headers, access.SESSION_HEADER: "forged"}).status_code == 401
    with monkeypatch.context() as injection:
        injection.setattr(device_policy, "_READ", device_policy._READ - {"/api/themes/catalog/quotes"})
        assert probe.get(url, headers=headers).status_code == 403
    assert probe.get(f"/api/themes/catalog/quotes?codes={HUMAN}", headers=headers).status_code == 404
    assert calls == [[ROBOT]]


def test_saved_sqlite_clock_has_utc_zone():
    assert source_saved_clock(datetime(2026, 1, 1)) == "2026-01-01T00:00:00+00:00"


def test_zero_price_and_future_readiness_remain_unknown(monkeypatch):
    async def run():
        svc = svc_for(monkeypatch, {"code": 0, "data": {"timestamp": 4102444800000, "item": [{"thscode": ROBOT, "last_price": 0, "price_change_ratio_pct": 0}]}})
        result = await svc.read_board_snapshots([ROBOT])
        assert result["rows"] == [{"code": ROBOT, "price": None, "change_pct": None}]
        assert result["source_as_of"] is None and result["source_ready_at"] is None
        await svc.aclose()
    asyncio.run(run())


def test_canceled_service_shutdown_still_drains_and_closes_client(monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        svc = svc_for(monkeypatch, {"code": 0, "data": {"item": [{"thscode": ROBOT}]}}, entered=entered, release=release)
        closed = []
        async def close():
            closed.append(True)
        monkeypatch.setattr(svc._client, "aclose", close)
        reader = asyncio.create_task(svc.read_board_snapshots([ROBOT])); await entered.wait()
        shutdown = asyncio.create_task(svc.aclose()); await asyncio.sleep(0)
        shutdown.cancel(); await asyncio.sleep(0)
        assert not shutdown.done()
        release.set()
        result = await asyncio.gather(reader, shutdown, return_exceptions=True)
        assert result[0]["available"]
        assert isinstance(result[1], asyncio.CancelledError)
        assert closed == [True]
    asyncio.run(run())
