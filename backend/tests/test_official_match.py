"""Official attachment keeps its read contract while avoiding N+1 and loop blocking."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models.theme_catalog import Theme, ThemeMember
from app.services import official_match as official
from app.services.theme_catalog_service import ThemeCatalogService


@pytest.fixture
def catalog(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'official.db'}")
    Theme.__table__.create(engine)
    ThemeMember.__table__.create(engine)
    sf = sessionmaker(bind=engine)
    with sf() as db:
        db.add_all([Theme(code=code, name=name) for code, name in [
            ("A", "A概念"), ("B", "B概念"), ("C", "C大概念"), ("D", "空概念"),
        ]])
        db.add_all([
            ThemeMember(theme_code=code, symbol=symbol)
            for code in ("A", "B") for symbol in ("600001", "600002")
        ])
        db.add_all([ThemeMember(theme_code="C", symbol=f"{600001 + i:06d}")
                    for i in range(301)])
        db.add(ThemeMember(theme_code="orphan", symbol="600001"))
        db.commit()
    # Read methods only need the factory; do not construct an upstream HTTP client.
    svc = ThemeCatalogService.__new__(ThemeCatalogService)
    svc._sf = sf
    queries = []
    event.listen(engine, "before_cursor_execute",
                 lambda _conn, _cursor, statement, *_args: queries.append(statement))
    yield svc, engine, queries
    engine.dispose()


def _cards():
    return [
        {"theme": "A概念", "stocks": [{"symbol": s} for s in ("600001", "600002", "600999")]},
        {"theme": "动态标签", "ladder": [{"symbol": s} for s in ("600002", "600001")]},
        {"theme": "空概念", "stocks": [{"symbol": "600001"}]},
    ]


def test_cold_index_batches_real_sqlite_and_keeps_order_sizes_and_empty(catalog):
    svc, _engine, queries = catalog
    index, sizes = official._official_symbol_index(svc)
    assert index["600001"] == [("A", "A概念"), ("B", "B概念"), ("C", "C大概念")]
    assert sizes == {"A": 2, "B": 2, "C": 301, "D": 0}
    assert len(queries) == 2, "one catalog + one existing bulk member read"


def test_bulk_failure_retains_individual_coverage_and_item_failure(catalog, monkeypatch):
    svc, _engine, _queries = catalog
    attempts = []
    read = svc.get_members

    def bulk(_codes):
        attempts.append("bulk")
        raise RuntimeError("batch unavailable")

    def single(code):
        attempts.append(code)
        if code == "B":
            raise RuntimeError("one concept unavailable")
        return read(code)

    monkeypatch.setattr(svc, "member_symbols_bulk", bulk)
    monkeypatch.setattr(svc, "get_members", single)
    index, sizes = official._official_symbol_index(svc)
    assert attempts == ["bulk", "A", "B", "C", "D"]
    assert index["600001"] == [("A", "A概念"), ("C", "C大概念")]
    assert sizes == {"A": 2, "C": 301, "D": 0}


def test_attachment_keeps_hot_exact_match_read_and_filters_large_concept(catalog):
    svc, _engine, queries = catalog
    state = SimpleNamespace(theme_catalog=svc)
    first = _cards()
    official.attach_official(state, first)
    assert [row["official"] for row in first[0]["stocks"]] == [True, True, False]
    assert "official" not in first[1]["ladder"][0]
    matches = [{"code": "A", "name": "A概念", "hits": 2},
               {"code": "B", "name": "B概念", "hits": 2}]
    assert first[0]["official_matches"] == first[1]["official_matches"] == matches
    assert first[2]["stocks"][0]["official"] is False
    assert first[2]["official_matches"] == [] and first[2]["catalog_code"] == "D"
    assert len(queries) == 6
    # A hot reverse index must not replace the existing fresh exact-name member read.
    with svc._sf() as db:
        db.query(ThemeMember).filter_by(theme_code="A", symbol="600001").delete()
        db.commit()
    queries.clear()
    second = _cards()
    official.attach_official(state, second)
    assert second[0]["stocks"][0]["official"] is False
    assert second[0]["official_matches"] == matches
    assert len(queries) == 4


def test_two_threads_share_one_cold_index_and_expiry_rebuild(catalog, monkeypatch):
    import app.core.ttl_cache as tc

    svc, _engine, _queries = catalog
    state = SimpleNamespace(theme_catalog=svc)
    clock = {"now": 1000.0}
    monkeypatch.setattr(tc, "monotonic", lambda: clock["now"])
    original = official._official_symbol_index
    entered, release = threading.Event(), threading.Event()
    rendezvous = threading.Barrier(2)
    calls = []

    def held(service):
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(1)
        return original(service)

    def read():
        rendezvous.wait(timeout=1)
        return official._get_index(state)

    monkeypatch.setattr(official, "_official_symbol_index", held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(read), pool.submit(read)
        try:
            assert entered.wait(1)
            # Both workers crossed the barrier before the owner can finish.
            threading.Event().wait(0.03)
        finally:
            release.set()
        assert one.result() == two.result()
    assert len(calls) == 1
    clock["now"] += 300
    official._get_index(state)
    assert len(calls) == 2


@pytest.mark.parametrize("consumer", ["opportunities", "market_themes"])
def test_real_attachment_sessions_yield_loop_and_isolate_cached_cards(catalog, monkeypatch, consumer):
    from app.api.routes import market_themes
    from app.picks import board_surge, intraday_opportunity as opportunity
    from app.picks import intraday_opportunity_runtime as runtime
    from app.services import theme_service

    svc, engine, _queries = catalog
    main_thread = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    opened, closed = [], []

    class RecordedSession(Session):
        def __enter__(self):
            opened.append((self, threading.get_ident()))
            return super().__enter__()

        def execute(self, *args, **kwargs):
            if not entered.is_set():
                entered.set()
                assert release.wait(2)
            return super().execute(*args, **kwargs)

        def __exit__(self, *args):
            result = super().__exit__(*args)
            closed.append((self, threading.get_ident()))
            return result

    svc._sf = sessionmaker(bind=engine, class_=RecordedSession)
    state = SimpleNamespace(theme_catalog=svc, hub=SimpleNamespace(provider=object()))
    app = SimpleNamespace(state=state)
    cached_cards = _cards()
    original_cards = deepcopy(cached_cards)

    async def board(*_args, **_kwargs):
        return {"themes": []}

    async def cached(*_args):
        return {"data": {"themes": cached_cards}, "meta": {}}

    async def verify(*_args):
        return None

    monkeypatch.setattr(theme_service, "build_theme_board", board)
    monkeypatch.setattr(theme_service, "_pick_provider", lambda *_args: None)
    monkeypatch.setattr(opportunity, "assemble", lambda *_args, **_kwargs:
                        {"themes": deepcopy(cached_cards), "summary": {"limit_up_total": 0}})
    monkeypatch.setattr(board_surge, "get_index_cache", lambda _app: SimpleNamespace(get=lambda: ({}, {})))
    monkeypatch.setattr(opportunity, "attach_participants", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(market_themes, "_theme_board_cached", cached)
    monkeypatch.setattr(market_themes, "_verify_board_multi_day", verify)

    async def run():
        if consumer == "opportunities":
            read = runtime._build_opportunities_uncached(
                app, date(2026, 10, 12), 5, 8,
                snapshot_bundle=({"600001": {"symbol": "600001"}}, "unknown", None),
            )
        else:
            read = market_themes.themes(
                date_str="2026-10-12", min_boards=0, min_count=1,
                sort="strength", limit=30, request=SimpleNamespace(app=app), hub=state.hub,
            )
        task = asyncio.create_task(read)
        # A bounded watchdog only frees the old blocking implementation on RED.
        watchdog = threading.Timer(1, release.set)
        watchdog.start()
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            yielded = not release.is_set()
        finally:
            release.set()
            watchdog.cancel()
        payload = await task
        assert yielded, "event loop must run while the real DB read is held"
        assert len(opened) == len(closed) and opened == closed
        assert all(thread != main_thread for _session, thread in opened)
        assert len({id(session) for session, _thread in opened}) == len(opened)
        assert payload["data"]["themes"][0]["official_matches"][0]["code"] == "A"
        assert cached_cards == original_cards

    asyncio.run(run())
