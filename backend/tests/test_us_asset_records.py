"""US watch-only writes use verified identity and never become trading holdings."""
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import require_api_token
from app.api.routes import asset_records as route
from app.core import device_access as access
from app.core.config import settings
from app.models.asset_records import AssetRecord
from app.models.watchlist import Base
from app.repositories.asset_records import list_records


@pytest.fixture
def us_record_client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'records.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(route, "get_session_factory", lambda: factory)
    monkeypatch.setattr(settings, "api_token", "fixture-service")
    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "devices.sqlite3")
    store = access.get_device_store()
    origin = "http://asset.test:3000"
    admin, _ = store.bootstrap(store.bootstrap_code(), "owner", origin)
    token, row = store.start("ordinary", origin)
    store.manage(admin, origin, row["id"], "approve")
    headers = {"X-API-Token": "fixture-service", access.BROWSER_HEADER: "1",
               access.SESSION_HEADER: token, access.ORIGIN_HEADER: origin}

    class Source:
        calls = 0
        identity = {"instrument_id": "us_equity:US:AAPL.OQ:USD", "asset_type": "us_equity",
                    "source_symbol": "AAPL.OQ", "currency": "USD"}

        async def resolve_us(self, symbol):
            self.calls += 1
            assert symbol == self.identity["source_symbol"]
            return dict(self.identity)

    source = Source()
    app = FastAPI()
    app.state.asset_service = source
    app.include_router(route.router, prefix="/api", dependencies=[Depends(require_api_token)])
    try:
        with TestClient(app) as client:
            yield client, headers, source, factory
    finally:
        engine.dispose()


def test_ordinary_us_watch_write_read_cas_delete_is_separate_from_funds(us_record_client):
    client, headers, source, factory = us_record_client
    path = "/api/assets/records/us_equity:US:AAPL.OQ:USD"
    payload = {"source_symbol": "AAPL.OQ", "name": "苹果", "is_watched": True,
               "units": None, "cost_total": None, "expected_version": 0}
    assert client.put(path, json=payload).status_code == 401
    created = client.put(path, json=payload, headers=headers)
    assert created.status_code == 200, created.text
    record = created.json()["data"]
    assert record["asset_type"] == "us_equity" and record["currency"] == "USD"
    assert record["units"] is None and record["cost_total"] is None
    assert record["version"] == 1
    read = client.get("/api/assets/records?asset_type=us_equity", headers=headers)
    assert read.status_code == 200 and read.json()["data"]["items"][0]["name"] == "苹果"
    assert list_records(factory, "fund")["items"] == []
    assert client.put(path, json={**payload, "name": "stale"}, headers=headers).status_code == 409
    assert client.delete(path + "?expected_version=2", headers=headers).status_code == 409
    assert client.delete(path + "?expected_version=1", headers=headers).status_code == 200
    assert list_records(factory, "us_equity")["items"] == []
    with factory() as db:
        assert db.get(AssetRecord, "us_equity:US:AAPL.OQ:USD").is_deleted


@pytest.mark.parametrize("changes", [{"units": 1, "cost_total": 2}, {"source_symbol": "600519"},
                                    {"source_symbol": "AAPL"}, {"source_symbol": "AAPL.SZ"}])
def test_us_writes_reject_holdings_and_unverified_symbol_shapes(us_record_client, changes):
    client, headers, source, factory = us_record_client
    payload = {"source_symbol": "AAPL.OQ", "units": None, "cost_total": None, **changes}
    assert client.put("/api/assets/records/us_equity:US:AAPL.OQ:USD", json=payload, headers=headers).status_code == 422
    assert source.calls == 0 and list_records(factory, "us_equity")["items"] == []


def test_us_identity_or_currency_mismatch_keeps_original_records(us_record_client):
    client, headers, source, factory = us_record_client
    payload = {"source_symbol": "AAPL.OQ", "units": None, "cost_total": None}
    path = "/api/assets/records/us_equity:US:AAPL.OQ:USD"
    source.identity = {**source.identity, "currency": "CNY"}
    assert client.put(path, json=payload, headers=headers).status_code == 422
    assert list_records(factory, "us_equity")["items"] == []
    source.calls = 0
    assert client.put("/api/assets/records/fund:OF:040046:CNY", json=payload, headers=headers).status_code == 422
    assert source.calls == 0


def test_source_confirmed_long_symbol_has_same_save_contract_as_quote_identity(us_record_client):
    client, headers, source, factory = us_record_client
    symbol = "ABCDEFGHIJKLMNOPQRSTUVWX.OQ"
    identity = f"us_equity:US:{symbol}:USD"
    source.identity = {"instrument_id": identity, "asset_type": "us_equity", "source_symbol": symbol, "currency": "USD"}
    response = client.put(f"/api/assets/records/{identity}", json={"source_symbol": symbol, "name": "来源确认的长代码", "expected_version": 0}, headers=headers)
    assert response.status_code == 200, response.text
    assert list_records(factory, "us_equity")["items"][0]["source_symbol"] == symbol
    assert client.delete(f"/api/assets/records/{identity}?expected_version=1", headers=headers).status_code == 200
