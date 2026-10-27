"""The existing official source owns exhaustive, dated-as-unknown index reads."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services.theme_catalog_service import ThemeCatalogService


def directory(count=103, kind="concept"):
    return {"source": "ths_official", "kind": kind, "total": count, "catalog_state": "ready",
            "synced_at": "2026-10-27T01:00:00+00:00", "items": [
                {"code": f"{880000 + i}.TI", "name": f"来源原名{i}", "kind": kind,
                 "source": "ths_official", "synced_at": "2026-10-27T01:00:00+00:00"} for i in range(count)]}


def source(monkeypatch, *, count=103, transform=None):
    svc = ThemeCatalogService(lambda: pytest.fail("no database expected"), api_key="fixture")
    calls = []
    async def catalog(kind):
        return deepcopy(directory(count, kind))
    async def get(url, **kwargs):
        codes = kwargs["params"]["thscodes"].split(",")
        calls.append(codes)
        rows = [{"thscode": code, "last_price": 100, "price_change_ratio_pct": int(code[:6]) - 880050} for code in codes]
        if transform:
            rows = transform(rows, len(calls))
        return httpx.Response(200, content=json.dumps({"code": 0, "data": {"timestamp": 1793057685000, "item": rows}}).encode(),
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(svc, "read_board_catalog", catalog)
    monkeypatch.setattr(svc._client, "get", get)
    return svc, calls


@pytest.mark.parametrize("count", [390, 1000, 1001])
def test_real_stored_concept_directory_never_calls_a_truncated_limit_complete(tmp_path, monkeypatch, count):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.theme_catalog import Theme
    engine = create_engine(f"sqlite:///{tmp_path / 'formal-concepts.sqlite3'}")
    Theme.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([Theme(code=f"{880000+i}.TI", name=f"来源原名{i:04d}", source="ths_official", catalog_active=True)
                    for i in range(count)])
        db.add_all([Theme(code="990000.TI", name="A人工标签", source="manual", catalog_active=True),
                    Theme(code="990001.TI", name="A退出目录", source="ths_official", catalog_active=False)])
        db.commit()
    async def run():
        svc = ThemeCatalogService(factory, api_key="fixture")
        async def forbidden(*args, **kwargs):
            pytest.fail("stored complete-directory boundary must not issue a source request")
        monkeypatch.setattr(svc._client, "get", forbidden)
        try:
            if count > 1000:
                with pytest.raises(RuntimeError, match="exceeds supported complete range"):
                    await svc.read_board_catalog("concept")
                with pytest.raises(RuntimeError, match="exceeds supported complete range"):
                    await svc.read_board_ranking("concept")
                assert len(svc._ranking_reads) == 0
            else:
                result = await svc.read_board_catalog("concept")
                assert result["total"] == len(result["items"]) == count
                assert result["items"][0]["name"] == "来源原名0000"
                assert result["items"][-1]["code"] == f"{880000+count-1}.TI"
        finally:
            await svc.aclose()
    try:
        asyncio.run(run())
    finally:
        engine.dispose()


def test_exhaustive_original_identities_not_top20_or_member_averages(monkeypatch):
    async def run():
        svc, calls = source(monkeypatch)
        result = await svc.read_board_ranking("concept")
        assert list(map(len, calls)) == [50, 50, 3]
        assert result["directory_total"] == result["source_returned"] == 103
        assert result["coverage_state"] == "source_complete" and result["value_count"] == 103
        assert len(result["rows"]) == 103 and result["rows"][-1]["name"] == "来源原名102"
        assert result["rows"][50]["change_pct"] == 0
        assert result["trade_date"] is None and result["source_as_of"] is None
        assert result["source_ready_from"] and result["quality"] == "stale"
        result["rows"][0]["name"] = "invented"
        assert (await svc.read_board_ranking("concept"))["rows"][0]["name"] == "来源原名0"
        assert len(calls) == 3
        await asyncio.gather(svc.read_board_ranking("concept"), svc.read_board_ranking("concept", fresh=True))
        assert len(calls) == 6
        await svc.aclose()
    asyncio.run(run())


def test_missing_and_null_quotes_retain_every_identity_and_do_not_fill_zero(monkeypatch):
    def transform(rows, call):
        rows[0]["price_change_ratio_pct"] = None
        return rows[:-1] if call == 1 else rows
    async def run():
        svc, _ = source(monkeypatch, count=53, transform=transform)
        result = await svc.read_board_ranking("concept")
        assert len(result["rows"]) == 53 and result["source_returned"] == 52
        assert result["coverage_state"] == "partial" and result["value_count"] == 50
        assert result["rows"][0]["change_pct"] is None
        assert result["rows"][49]["price"] is None and result["rows"][49]["quote_state"] == "missing"
        assert result["missing_codes"] == ["880049.TI"]
        await svc.aclose()
    asyncio.run(run())


def test_directory_change_cannot_publish_a_mixed_version(monkeypatch):
    async def run():
        svc, _ = source(monkeypatch, count=2)
        reads = 0
        async def catalog(kind):
            nonlocal reads
            reads += 1
            value = directory(2)
            if reads > 1:
                value["items"][0]["name"] = "来源已更名"
            return value
        monkeypatch.setattr(svc, "read_board_catalog", catalog)
        with pytest.raises(RuntimeError, match="directory changed"):
            await svc.read_board_ranking("concept")
        await svc.aclose()
    asyncio.run(run())


def test_same_kind_concurrent_and_canceled_readers_share_one_owner(monkeypatch):
    async def run():
        svc, calls = source(monkeypatch, count=51)
        original = svc._client.get
        entered, release = asyncio.Event(), asyncio.Event()
        async def get(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(svc._client, "get", get)
        first = asyncio.create_task(svc.read_board_ranking("concept"))
        second = asyncio.create_task(svc.read_board_ranking("concept"))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second)["source_returned"] == 51 and len(calls) == 2
        await svc.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("status,business", [(401, None), (403, None), (429, None), (456, None), (200, 2001), (200, 2003), (200, 4001)])
def test_refusal_stops_remaining_batches_without_losing_directory(monkeypatch, status, business):
    async def run():
        svc, _ = source(monkeypatch, count=103)
        calls = []
        async def get(url, **kwargs):
            calls.append(kwargs)
            return httpx.Response(status, json={"code": business, "data": None}, request=httpx.Request("GET", url))
        monkeypatch.setattr(svc._client, "get", get)
        result = await svc.read_board_ranking("concept")
        assert len(calls) == 1 and result["source_returned"] == 0 and not result["available"]
        assert len(result["rows"]) == 103 and all(row["change_pct"] is None for row in result["rows"])
        assert result["coverage_state"] == "partial"
        await svc.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("body", [b'{"code":4817,"data":null}', b'<html>Verification required</html>', b'{"code":0,"data":{"item":{}}}'])
def test_unconfirmed_or_unknown_application_response_stops_after_one_request(monkeypatch, body):
    async def run():
        svc, _ = source(monkeypatch, count=390)
        calls = []
        async def get(url, **kwargs):
            calls.append(kwargs)
            return httpx.Response(200, content=body, request=httpx.Request("GET", url))
        monkeypatch.setattr(svc._client, "get", get)
        result = await svc.read_board_ranking("concept")
        assert len(calls) == 1
        assert result["source_returned"] == result["value_count"] == 0
        assert len(result["rows"]) == len(result["missing_codes"]) == 390
        assert all(row["quote_state"] == "unavailable" and row["change_pct"] is None for row in result["rows"])
        assert result["coverage_state"] == "partial" and result["source_as_of"] is None
        await svc.aclose()
    asyncio.run(run())


def test_round_deadline_and_close_drain_slow_source_without_partial_success_cache(monkeypatch):
    from app.services import theme_catalog_service as module
    async def run():
        svc, _ = source(monkeypatch, count=103)
        exited = asyncio.Event()
        async def slow(*args, **kwargs):
            try:
                await asyncio.sleep(1)
            finally:
                exited.set()
        monkeypatch.setattr(module, "DIRECTORY_QUOTE_BUDGET", .02)
        monkeypatch.setattr(svc._client, "get", slow)
        with pytest.raises(TimeoutError):
            await svc.read_board_ranking("concept")
        await svc.aclose()
        assert exited.is_set() and len(svc._ranking_reads) == 0
    asyncio.run(run())


def test_elapsed_parse_or_final_validation_cannot_publish_after_total_deadline(monkeypatch):
    from app.services import theme_catalog_service as module
    real_monotonic = module.time.monotonic
    elapsed = 0
    def transform(rows, _):
        nonlocal elapsed
        elapsed = 20
        return rows
    async def run():
        svc, _ = source(monkeypatch, count=2, transform=transform)
        # Replace this owner's clock object; never freeze the event loop clock.
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: real_monotonic() + elapsed))
        with pytest.raises(TimeoutError):
            await svc.read_board_ranking("concept")
        assert len(svc._ranking_reads) == 0
        await svc.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["duplicate", "foreign", "boolean", "infinite"])
def test_bad_source_batch_cannot_be_promoted_to_complete(monkeypatch, mutation):
    def transform(rows, _):
        if mutation == "duplicate":
            return [rows[0], rows[0]]
        if mutation == "foreign":
            rows[0]["thscode"] = "999999.TI"
        elif mutation == "boolean":
            rows[0]["price_change_ratio_pct"] = True
        else:
            rows[0]["price_change_ratio_pct"] = float("inf")
        return rows
    async def run():
        svc, _ = source(monkeypatch, count=2, transform=transform)
        result = await svc.read_board_ranking("concept")
        assert result["source_returned"] == 0 and result["coverage_state"] == "partial"
        assert [row["name"] for row in result["rows"]] == ["来源原名0", "来源原名1"]
        await svc.aclose()
    asyncio.run(run())


def test_route_directory_scope_retains_exact_existing_codes_contract(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes.theme_catalog import router
    calls = []
    async def ranking(kind, *, fresh=False):
        calls.append((kind, fresh))
        return {"scope": "directory", "kind": kind, "rows": []}
    async def catalog(kind):
        return directory(1, kind)
    async def quotes(codes):
        calls.append(codes)
        return {"available": True, "rows": []}
    app = FastAPI()
    app.state.theme_catalog = SimpleNamespace(read_board_ranking=ranking, read_board_catalog=catalog, read_board_snapshots=quotes)
    app.include_router(router, prefix="/api")
    probe = TestClient(app)
    assert probe.get("/api/themes/catalog/quotes?scope=directory&kind=industry&fresh=true").status_code == 200
    assert calls == [("industry", True)]
    assert probe.get("/api/themes/catalog/quotes?scope=directory&codes=880000.TI").status_code == 422
    assert probe.get("/api/themes/catalog/quotes").status_code == 422
    assert probe.get("/api/themes/catalog/quotes?codes=880000.TI").status_code == 200
    assert calls[-1] == ["880000.TI"]


def test_complete_scope_is_the_same_identity_bound_ordinary_read(tmp_path, monkeypatch):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import require_api_token
    from app.api.routes.theme_catalog import router
    from app.core import device_access as access
    from app.core.config import settings
    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "devices.sqlite3")
    monkeypatch.setattr(settings, "api_token", "fixture-server")
    async def ranking(kind, *, fresh=False):
        return {"scope": "directory", "kind": kind}
    app = FastAPI()
    app.state.theme_catalog = SimpleNamespace(read_board_ranking=ranking)
    app.include_router(router, prefix="/api", dependencies=[Depends(require_api_token)])
    probe = TestClient(app)
    store = access.get_device_store()
    origin = "http://directory.test:3000"
    admin, _ = store.bootstrap(store.bootstrap_code(), "测试管理设备", origin)
    token, row = store.start("普通测试设备", origin)
    store.manage(admin, origin, row["id"], "approve")
    assert store.authorize(token, origin)["maintenance"] is False
    headers = {"X-API-Token": "fixture-server", access.BROWSER_HEADER: "1", access.SESSION_HEADER: token, access.ORIGIN_HEADER: origin}
    url = "/api/themes/catalog/quotes?scope=directory&kind=concept"
    assert probe.get(url).status_code == 401
    assert probe.get(url, headers=headers).status_code == 200
    assert probe.get(url, headers={**headers, access.SESSION_HEADER: "forged"}).status_code == 401
