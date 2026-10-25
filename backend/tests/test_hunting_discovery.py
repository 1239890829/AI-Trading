"""Current classification reads preserve unknowns, permissions and source owners."""
from datetime import datetime
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.api.deps import require_api_token
from app.api.routes.picks import router
from app.core import device_access as access
from app.core.config import settings
from app.models.theme_catalog import Theme, ThemeMember, ThemeOverride
from app.models.watchlist import Base
from app.picks.strategy_registry import list_strategy_keys
from app.services.theme_catalog_service import ThemeCatalogService


@pytest.fixture
def context(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'discovery.sqlite3'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    with sf() as db:
        past = datetime(2020, 1, 1)
        db.add_all([
            Theme(code="885001.TI", name="当前题材", synced_at=past),
            Theme(code="885002.TI", name="退出题材", catalog_active=False, synced_at=past),
            Theme(code="885003.TI", name="未来资料", synced_at=datetime(2100, 1, 1)),
            ThemeMember(theme_code="885001.TI", symbol="600001", synced_at=past),
            ThemeMember(theme_code="885001.TI", symbol="000001", synced_at=past),
            ThemeMember(theme_code="885002.TI", symbol="600001", synced_at=past),
            ThemeMember(theme_code="885003.TI", symbol="600001", synced_at=past),
            ThemeOverride(theme_code="885001.TI", symbol="000001", action="exclude"),
        ])
        db.commit()
    # The actual read implementation needs only its existing session owner.
    # No HTTP client, source sync, model, scheduler or selection writer is built.
    svc = ThemeCatalogService.__new__(ThemeCatalogService)
    svc._sf = sf
    app = FastAPI()
    app.state.theme_catalog = svc
    app.include_router(router, prefix="/api", dependencies=[Depends(require_api_token)])
    statements = []
    event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))
    yield SimpleNamespace(app=app, probe=TestClient(app), statements=statements, svc=svc)
    engine.dispose()


def test_membership_read_is_bounded_bulk_local_and_not_historical(context):
    response = context.probe.get("/api/picks/discovery-memberships", params={
        "symbols": "600001,000001,600001,600999", "trade_date": "2020-01-02"})
    assert response.status_code == 200
    payload = response.json()
    data = payload["data"]
    assert data["scope"] == "current_not_historical"
    assert data["requested_trade_date"] == "2020-01-02"
    assert data["source_as_of"] is None
    assert data["count"] == 3 and data["matched_count"] == 1 and data["unknown_count"] == 2
    assert data["state"] == "partial" and payload["meta"]["partial"]
    rows = {row["symbol"]: row for row in data["items"]}
    assert rows["600001"]["themes"] == [{"theme_code": "885001.TI", "theme_name": "当前题材", "source": "ths_official"}]
    assert rows["000001"]["state"] == "unknown" and rows["000001"]["themes"] == []
    assert rows["600999"]["state"] == "unknown"
    assert len(context.statements) == 2
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in context.statements)


def test_content_digest_excludes_receipt_time_and_unknown_is_not_no_theme(context):
    a = context.probe.get("/api/picks/discovery-memberships", params={"symbols": "600999"}).json()["data"]
    b = context.probe.get("/api/picks/discovery-memberships", params={"symbols": "600999"}).json()["data"]
    assert a["content_version"] == b["content_version"] and len(a["content_version"]) == 64
    assert a["state"] == "unknown" and a["items"][0]["state"] == "unknown"
    assert a["requested_trade_date"] is None and a["source_as_of"] is None
    assert a["items"][0]["reason"]


@pytest.mark.parametrize("symbols,day", [
    ("", None), ("600519.SH", None), ("６００５１９", None), ("600001,", None),
    (",".join(["600001"] * 201), None), ("600001", "2026-02-30"), ("600001", "20261025"),
])
def test_invalid_scope_is_rejected_before_any_database_read(context, symbols, day):
    params = {"symbols": symbols, **({"trade_date": day} if day else {})}
    assert context.probe.get("/api/picks/discovery-memberships", params=params).status_code == 422
    assert context.statements == []


@pytest.mark.parametrize("missing", [True, False])
def test_unavailable_membership_never_syncs_or_fills_guess(context, missing):
    if missing:
        context.app.state.theme_catalog = None
    else:
        def failed(_symbols):
            raise RuntimeError("fixture local read failure")
        context.app.state.theme_catalog = SimpleNamespace(official_for_symbols_bulk=failed)
    data = context.probe.get("/api/picks/discovery-memberships", params={"symbols": "600001"}).json()["data"]
    assert data["state"] == "unavailable" and data["unknown_count"] == 1
    assert data["items"][0]["themes"] == [] and data["items"][0]["state"] == "unknown"
    assert context.statements == []


def test_actual_included_route_accepts_only_ordinary_paired_reader(context, tmp_path, monkeypatch):
    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "devices.sqlite3")
    monkeypatch.setattr(settings, "api_token", "fixture-server")
    store = access.get_device_store()
    origin = "http://discovery.test:3000"
    admin, _ = store.bootstrap(store.bootstrap_code(), "测试管理设备", origin)
    token, row = store.start("普通测试设备", origin)
    store.manage(admin, origin, row["id"], "approve")
    assert store.authorize(token, origin)["maintenance"] is False
    url = "/api/picks/discovery-memberships?symbols=600001"
    assert context.probe.get(url).status_code == 401
    headers = {"X-API-Token": "fixture-server", access.BROWSER_HEADER: "1",
               access.SESSION_HEADER: token, access.ORIGIN_HEADER: origin}
    assert context.probe.get(url, headers=headers).status_code == 200
    from app.core import device_policy
    with monkeypatch.context() as injection:
        injection.setattr(device_policy, "_READ", device_policy._READ - {"/api/picks/discovery-memberships"})
        assert context.probe.get(url, headers=headers).status_code == 403
    forged = {**headers, access.SESSION_HEADER: "unknown"}
    assert context.probe.get(url, headers=forged).status_code == 401
    assert context.probe.get("/api/picks/discovery-memberships/internal", headers=headers).status_code == 404


def test_registry_declares_categories_without_creating_methods_or_running_rejected(monkeypatch):
    import app.picks.strategy_registry as registry
    monkeypatch.setattr(registry, "_verification_of", lambda _: {"available": False})
    rows = {item["key"]: item for item in list_strategy_keys()}
    assert set(rows) == {"daily_picks", "intraday_watch", "pullback_reversal", "triple_volume", "two_thirty_five"}
    assert rows["daily_picks"]["discovery"]["category"] == "multi_factor"
    assert rows["intraday_watch"]["discovery"]["session"] == "intraday"
    for key, row in rows.items():
        metadata = row["discovery"]
        assert all(metadata[field] for field in ["category", "category_label", "session", "session_label", "summary"])
        assert metadata["supports_saved_runs"] == (key in {"daily_picks", "intraday_watch"})
        if row["status"] != "active":
            assert metadata["session"] == "research" and row["evaluable"] is False
