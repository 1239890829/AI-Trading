"""Missing labels and shared members must not manufacture a common catalyst."""
import asyncio
import sqlite3
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import utcnow
from app.models.theme_catalog import Theme, ThemeMember, ThemeOverride
from app.services.theme_catalog_service import ThemeCatalogService
from app.services.official_match import attach_official_async
from app.picks.intraday_opportunity import attach_participants
from app.picks.board_surge import build_theme_index


@pytest.fixture
def svc(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}", connect_args={"check_same_thread": False})
    for table in (Theme.__table__, ThemeMember.__table__, ThemeOverride.__table__):
        table.create(engine)
    service = ThemeCatalogService(sessionmaker(bind=engine, expire_on_commit=False), api_key="test")
    yield service
    asyncio.run(service.aclose())
    engine.dispose()


def seed(service):
    past = utcnow() - timedelta(days=2)
    future = utcnow() + timedelta(days=13)
    with service._sf() as db:
        db.add_all([Theme(code="886042.TI", name="存储芯片", synced_at=past),
                    Theme(code="990001.TI", name="存储芯片", synced_at=future)])
        db.add_all([ThemeMember(theme_code=code, symbol=s, name="测试")
                    for code in ("886042.TI", "990001.TI") for s in ("600171", "600001")])
        db.commit()
    return past.replace(tzinfo=None)


def test_catalog_quarantines_absent_identity_without_deleting_history(svc):
    past = seed(svc)
    svc._write_catalog([{"code": "886042.TI", "name": "存储芯片"},
                        {"code": "886113.TI", "name": "2026三季报预增"}])
    assert {t.code for t in svc.get_catalog()} == {"886042.TI", "886113.TI"}
    assert svc.get_official_for_symbol("600171")[0]["theme_code"] == "886042.TI"
    assert svc.get_members("990001.TI") == []
    assert svc.member_symbols_bulk(["990001.TI"]) == {}
    assert svc.catalog_size() == 2
    index, _ = build_theme_index(svc._sf)
    assert index["600171"] == [("886042.TI", "存储芯片")]
    with svc._sf() as db:
        old = db.execute(select(Theme).where(Theme.code == "990001.TI")).scalar_one()
        active = db.execute(select(Theme).where(Theme.code == "886042.TI")).scalar_one()
        assert old.catalog_active is False
        assert active.synced_at == past
        assert len(list(db.execute(select(ThemeMember)).scalars())) == 4
    assert not svc.catalog_needs_sync()


def test_future_membership_clock_is_due_for_resync(svc):
    seed(svc)
    assert "990001.TI" in svc.stale_codes(20)
    assert svc.get_members("990001.TI") == []
    assert svc.member_symbols_bulk(["990001.TI"]) == {}
    assert all(row["theme_code"] != "990001.TI" for row in svc.get_official_for_symbol("600171"))
    assert all(row[0] != "990001.TI" for row in build_theme_index(svc._sf)[0]["600171"])


@pytest.mark.parametrize("payload", [
    {"code": 0, "data": {"item": []}},
    {"code": 503, "data": {"item": [{"thscode": "886042.TI", "name": "存储芯片"}]}},
    {"code": 0, "data": {"item": [{"thscode": "886042.TI"}]}},
    {"code": 0, "data": {"item": [{"thscode": "886042.TI", "name": "a"}]*2}},
])
def test_bad_catalog_never_deactivates_previous_rows(svc, payload):
    seed(svc)
    async def run():
        await svc._client.aclose()
        svc._client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)))
        with pytest.raises(RuntimeError):
            await svc.sync_catalog()
    asyncio.run(run())
    assert svc.catalog_size() == 2


def test_unknown_and_partial_overlap_cannot_supply_candidate_theme(svc):
    seed(svc)
    svc._write_catalog([{"code": "886042.TI", "name": "存储芯片"}])
    rows = [{"symbol": s} for s in ("600171", "600001")]
    unknown = {"theme": "未分类", "stocks": rows}
    partial = {"theme": "存储芯片", "stocks": rows,
               "supporting_symbols": ["600171", "600001", "600999"]}
    exact = {"theme": "存储芯片", "stocks": rows}
    async def run():
        await svc._client.aclose()

        def industry(request):
            assert request.url.path.endswith("/catalog/ths-index-list")
            assert request.url.params.get("tag") == "industry"
            return httpx.Response(200, json={"code": 0, "data": {"item": [
                {"thscode": "881126.TI", "name": "汽车零部件"}]}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(industry)) as client:
            svc._client = client
            await attach_official_async(SimpleNamespace(theme_catalog=svc), [unknown, partial, exact],
                                        view_budget=None)

    asyncio.run(run())
    assert unknown["official_matches"] == [] and unknown["catalog_code"] is None
    assert partial["official_matches"] and partial["candidate_catalog_code"] is None
    assert exact["candidate_catalog_code"] == "886042.TI"


def test_unknown_rejected_even_with_legacy_catalog_code():
    row = {"theme": "未分类", "limit_up_count": 14, "catalog_code": "886042.TI",
           "candidate_catalog_code": "886042.TI"}
    result = attach_participants([row], snapshot_by={}, ever_sealed_symbols=set(),
                                 limit_up_total=37, members_by_code={})
    assert row["participants"] == []
    assert result["theme_gate_counts"] == {"unclassified": 1}


def test_sqlite_guard_blocks_project_file_before_creation():
    target = Path(__file__).parents[1] / "data" / "test-must-not-exist.db"
    assert not target.exists()
    with pytest.raises(RuntimeError, match="isolated temporary"):
        sqlite3.connect(target)
    assert not target.exists()


def test_catalog_sync_is_singleflight_and_member_change_invalidates_revision(svc):
    seed(svc)
    calls = []
    async def fetch():
        calls.append(1)
        await asyncio.sleep(0)
        return [{"code": "886042.TI", "name": "存储芯片"}]
    svc.fetch_catalog = fetch
    async def run():
        assert await asyncio.gather(svc.sync_catalog(), svc.sync_catalog()) == [1, 1]
    asyncio.run(run())
    assert len(calls) == 1
    revision = svc.revision
    svc._write_members("886042.TI", [{"symbol": "600171", "name": "上海贝岭"}])
    assert svc.revision == revision + 1
    assert svc.member_symbols_bulk(["886042.TI"]) == {"886042.TI": ["600171"]}
    # Explicit maintenance of a retired row preserves uniqueness and remains hidden.
    svc._write_members("990001.TI", [{"symbol": "600171", "name": "测试"}])
    assert svc.get_members("990001.TI") == []


@pytest.mark.parametrize("suffix", ["?mode=rw", "?mode=memory-invalid", "?label=mode=memory"])
def test_sqlite_uri_cannot_bypass_isolation(suffix):
    target = Path(__file__).parents[1] / "data" / "test-must-not-exist.db"
    with pytest.raises(RuntimeError, match="isolated temporary"):
        sqlite3.connect(target.as_uri() + suffix, uri=True)
    assert not target.exists()
