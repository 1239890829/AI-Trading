"""Exercise real route assembly and paired ordinary reads without market IO."""
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.deps import require_api_token
from app.api.routes.market_flow import router
from app.core import device_access as access
from app.core.config import settings
from app.market import board_flow as bf


@pytest.fixture
def ordinary_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "devices.sqlite3")
    monkeypatch.setattr(settings, "api_token", "fixture-service")
    store = access.get_device_store()
    origin = "http://funds-reader.test:3000"
    owner, _ = store.bootstrap(store.bootstrap_code(), "test owner", origin)
    token, row = store.start("ordinary reader", origin)
    store.manage(owner, origin, row["id"], "approve")
    assert store.authorize(token, origin)["maintenance"] is False
    headers = {
        "X-API-Token": "fixture-service", access.BROWSER_HEADER: "1",
        access.SESSION_HEADER: token, access.ORIGIN_HEADER: origin,
    }
    app = FastAPI()
    app.include_router(router, prefix="/api", dependencies=[Depends(require_api_token)])
    app.include_router(router, prefix="/internal/api", dependencies=[Depends(require_api_token)])
    return TestClient(app), headers


def _sources(monkeypatch):
    calls = []

    async def boards(kind, range_key, *, fresh=False):
        calls.append(("boards", kind, range_key, fresh))
        return {"available": True, "source": "eastmoney_official_funds", "rows": []}

    async def members(code, *, kind="concept", fresh=False):
        calls.append(("website", code, kind, fresh))
        return {"available": True, "source": "eastmoney_official_funds", "board_code": code, "rows": []}

    async def original(code):
        calls.append(("clist", code))
        return {"available": False, "board_code": code, "rows": [], "reason": "fixture unavailable"}

    monkeypatch.setattr(bf, "get_board_fund_flow", boards)
    monkeypatch.setattr(bf, "get_official_website_members", members, raising=False)
    monkeypatch.setattr(bf, "get_board_members", original)
    return calls


@pytest.mark.parametrize("kind", ["concept", "industry"])
def test_paired_ordinary_reader_reaches_exact_website_consumer(ordinary_reader, monkeypatch, kind):
    client, headers = ordinary_reader
    calls = _sources(monkeypatch)
    listing = client.get("/api/market/board-fund-flow", params={"range": "website", "kind": kind, "fresh": "true"}, headers=headers)
    members = client.get("/api/market/board-fund-flow/bk1184/members", params={"view": "website", "kind": kind, "fresh": "true"}, headers=headers)
    assert listing.status_code == members.status_code == 200
    assert members.json()["data"]["board_code"] == "BK1184"
    assert calls == [("boards", kind, "website", True), ("website", "BK1184", kind, True)]


def test_legacy_member_route_keeps_original_consumer_and_unavailable_result(ordinary_reader, monkeypatch):
    client, headers = ordinary_reader
    calls = _sources(monkeypatch)
    result = client.get("/api/market/board-fund-flow/BK1184/members", headers=headers)
    assert result.status_code == 200
    assert result.json()["data"]["available"] is False
    assert calls == [("clist", "BK1184")]


@pytest.mark.parametrize("path,params", [
    ("/api/market/board-fund-flow", {"kind": "region", "range": "website"}),
    ("/api/market/board-fund-flow", {"range": "unexpected"}),
    ("/api/market/board-fund-flow/BK1184/members", {"view": "unexpected"}),
    ("/api/market/board-fund-flow/BK1184/members", {"view": "website", "kind": "region"}),
    ("/api/market/board-fund-flow/885517.TI/members", {"view": "website"}),
    ("/api/market/board-fund-flow/BK1184/members", {"view": "website", "fresh": "unexpected"}),
])
def test_invalid_identity_or_mode_never_reaches_source(ordinary_reader, monkeypatch, path, params):
    client, headers = ordinary_reader
    calls = _sources(monkeypatch)
    assert client.get(path, params=params, headers=headers).status_code == 422
    assert calls == []


def test_website_parameters_do_not_expand_identity_or_pairing_permission(ordinary_reader, monkeypatch):
    client, headers = ordinary_reader
    calls = _sources(monkeypatch)
    path = "/api/market/board-fund-flow/BK1184/members?view=website&kind=concept"
    assert client.get(path).status_code == 401
    unpaired = {**headers, access.SESSION_HEADER: "unpaired-fixture"}
    assert client.get(path, headers=unpaired).status_code == 401
    assert client.get("/internal" + path, headers=headers).status_code == 403
    assert calls == []
