"""Manual fund records retain namespace, currency and compare-and-swap writes."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import Session

from app.api.deps import require_api_token
from app.api.routes import asset_records as route
from app.core import device_access as access
from app.core.config import settings
from app.core.migrations import run_migrations
from app.models.asset_records import AssetRecord
from app.models.watchlist import Base, WatchlistItem
from app.repositories.asset_records import RecordConflict, list_records, remove_record, save_record

IDENTITY = {"instrument_id": "fund:OF:000001:CNY", "asset_type": "fund",
            "source_symbol": "000001.OF", "currency": "CNY"}
VALUES = {"name": "夹具基金", "is_watched": True, "units": Decimal("10.0001"),
          "cost_total": Decimal("12.3456789"), "note": None}


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'assets.db'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def test_fund_stock_collision_does_not_touch_a_share_records(factory):
    with factory() as db:
        db.add(WatchlistItem(symbol="000001", name="原A股", group_name="原分组"))
        db.commit()
    result = save_record(factory, IDENTITY, VALUES, 0)
    assert result["units"] == 10.0001
    assert result["cost_total"] == 12.3456789
    assert result["currency"] == "CNY"
    assert result["basis"] == "manual_not_broker_verified"
    assert result["updated_at"].endswith("+00:00")
    remove_record(factory, result["instrument_id"], result["version"])
    with factory() as db:
        original = db.scalar(select(WatchlistItem).where(WatchlistItem.symbol == "000001"))
        assert (original.name, original.group_name) == ("原A股", "原分组")
    assert list_records(factory, "fund")["items"] == []


def test_currency_namespaces_and_missing_cost_are_preserved(factory):
    for cur in ("CNY", "USD"):
        save_record(factory, {**IDENTITY, "currency": cur, "instrument_id": f"fund:OF:000001:{cur}"},
                    {**VALUES, "units": None, "cost_total": None}, 0)
    results = list_records(factory, "fund")["items"]
    assert {row["currency"] for row in results} == {"USD", "CNY"}
    assert all(row["units"] is None and row["cost_total"] is None for row in results)
    assert list_records(factory, "us_equity")["items"] == []


def test_delete_recreate_never_reuses_stale_revision_or_private_values(factory):
    save_record(factory, IDENTITY, VALUES, 0)
    remove_record(factory, IDENTITY["instrument_id"], 1)
    with factory() as db:
        tombstone = db.get(AssetRecord, IDENTITY["instrument_id"])
        assert tombstone.version == 2 and tombstone.is_deleted
        assert tombstone.units is None and tombstone.cost_total is None and tombstone.note is None
    recreated = save_record(factory, IDENTITY, {**VALUES, "note": "重新记录"}, 0)
    assert recreated["version"] == 3
    with pytest.raises(RecordConflict):
        save_record(factory, IDENTITY, VALUES, 1)
    with pytest.raises(RecordConflict):
        remove_record(factory, IDENTITY["instrument_id"], 1)
    assert list_records(factory, "fund")["items"][0]["note"] == "重新记录"


def test_concurrent_edit_and_stale_delete_cannot_overwrite(factory):
    save_record(factory, IDENTITY, VALUES, 0)
    def write(value):
        try:
            return save_record(factory, IDENTITY, {**VALUES, "note": value}, 1)
        except RecordConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["first", "second"]))
    assert sum(row is not None for row in results) == 1
    with pytest.raises(RecordConflict):
        remove_record(factory, IDENTITY["instrument_id"], 1)
    current = list_records(factory, "fund")["items"][0]
    assert current["version"] == 2
    assert current["note"] in {"first", "second"}


def test_save_response_is_this_commit_not_another_writers_post_commit_version(factory):
    with factory() as db:
        engine = db.get_bind()
    class InterleavedSession(Session):
        def commit(self):
            super().commit()
            if not getattr(self, "_interleaved", False):
                self._interleaved = True
                save_record(factory, IDENTITY, {**VALUES, "note": "另一笔修改"}, 1)
    interleaved = sessionmaker(bind=engine, class_=InterleavedSession, expire_on_commit=True)
    result = save_record(interleaved, IDENTITY, VALUES, 0)
    assert result["version"] == 1
    assert result["note"] is None
    current = list_records(factory, "fund")["items"][0]
    assert current["version"] == 2 and current["note"] == "另一笔修改"


@pytest.mark.parametrize("changes", [
    {"units": "NaN"}, {"units": "Infinity"}, {"units": -1}, {"units": True},
    {"units": 1, "cost_total": None}, {"units": 0, "cost_total": 1},
    {"source_symbol": "000001.SZ"}, {"expected_version": True},
    {"units": None, "cost_total": None, "is_watched": False},
])
def test_manual_record_rejects_incomplete_or_non_finite_input(changes):
    with pytest.raises(ValueError):
        route.RecordInput(**{"source_symbol": "000001.OF", **VALUES, **changes})


def test_migration_repeated_upgrade_preserves_existing_data_and_target(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'target.db'}")
    try:
        assert run_migrations(engine) == "created"
        factory = sessionmaker(bind=engine)
        save_record(factory, IDENTITY, VALUES, 0)
        assert run_migrations(engine) == "upgraded"
        with factory() as db:
            row = db.get(AssetRecord, IDENTITY["instrument_id"])
            assert row.units == Decimal("10.00010000")
            assert row.cost_total == Decimal("12.34567890")
    finally:
        engine.dispose()


def test_migration_refuses_dropping_saved_fund_records(tmp_path):
    from alembic import command
    from alembic.config import Config
    from app.core.migrations import BACKEND_DIR
    engine = create_engine(f"sqlite:///{tmp_path / 'preserve.db'}")
    try:
        run_migrations(engine)
        factory = sessionmaker(bind=engine)
        save_record(factory, IDENTITY, VALUES, 0)
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
        cfg.attributes["configure_logger"] = False
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            with pytest.raises(RuntimeError, match="preservation plan"):
                command.downgrade(cfg, "d2f4b6e8a0c1")
        assert list_records(factory, "fund")["items"][0]["units"] == 10.0001
    finally:
        engine.dispose()


def test_actual_router_ordinary_pairing_identity_failure_and_cas(factory, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "api_token", "fixture-service")
    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "device.sqlite3")
    monkeypatch.setattr(route, "get_session_factory", lambda: factory)
    store = access.get_device_store()
    origin = "http://asset.test:3000"
    admin, _ = store.bootstrap(store.bootstrap_code(), "本机", origin)
    token, row = store.start("普通设备", origin)
    store.manage(admin, origin, row["id"], "approve")
    assert store.authorize(token, origin)["maintenance"] is False
    headers = {"X-API-Token": "fixture-service", access.BROWSER_HEADER: "1",
               access.SESSION_HEADER: token, access.ORIGIN_HEADER: origin}
    class Source:
        identity = dict(IDENTITY)
        async def resolve_fund(self, code):
            return dict(self.identity)
    source = Source()
    app = FastAPI()
    app.state.asset_service = source
    app.include_router(route.router, prefix="/api", dependencies=[Depends(require_api_token)])
    with TestClient(app) as client:
        path = "/api/assets/records/" + IDENTITY["instrument_id"]
        payload = {"source_symbol": "000001.OF", "units": 10, "cost_total": 12}
        assert client.put(path, json=payload).status_code == 401
        source.identity["currency"] = "USD"
        source.identity["instrument_id"] = "fund:OF:000001:USD"
        assert client.put(path, json=payload, headers=headers).status_code == 422
        assert list_records(factory, "fund")["items"] == []
        source.identity = dict(IDENTITY)
        created = client.put(path, json=payload, headers=headers)
        assert created.status_code == 200, created.text
        assert created.json()["data"]["version"] == 1
        assert client.put(path, json=payload, headers=headers).status_code == 409
        assert len(client.get("/api/assets/records", headers=headers).json()["data"]["items"]) == 1
        assert client.delete(path + "?expected_version=2", headers=headers).status_code == 409
        assert client.delete(path + "?expected_version=1", headers=headers).status_code == 200
        assert client.get("/api/assets/records", headers=headers).json()["data"]["items"] == []
