"""The read-only ledger API must keep repo Sessions off the event loop."""
from __future__ import annotations


def test_ledger_api_worker_sessions_response_and_failed_read_recovery(tmp_path, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace

    import httpx
    import pytest
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.api.routes import picks_intraday
    from app.models.watch_ledger import WatchLedger
    from app.picks import watch_ledger as repo

    engine = create_engine(f"sqlite:///{tmp_path / 'ledger-api.db'}")
    WatchLedger.__table__.create(engine)
    sf = sessionmaker(bind=engine)
    target = "2026-10-12"
    repo.record_sighting(trade_date=target, symbol="600519", name="fixture",
                         reason={"kind": "technical"}, session_factory=sf)
    expected = {"data": {"trade_date": target, "rows": repo.get_day(target, sf),
                         "stats": repo.day_stats(target, sf), "history": repo.get_history(3, sf)},
                "meta": {}}
    fail = SimpleNamespace(next=True)
    opened, closed = [], []

    class RecordedSession(Session):
        def __enter__(self):
            opened.append((self, threading.get_ident()))
            return super().__enter__()

        def execute(self, *args, **kwargs):
            if fail.next:
                fail.next = False
                raise RuntimeError("isolated read unavailable")
            return super().execute(*args, **kwargs)

        def __exit__(self, *args):
            result = super().__exit__(*args)
            closed.append((self, threading.get_ident()))
            return result

    monkeypatch.setattr(repo, "get_session_factory",
                        lambda: sessionmaker(bind=engine, class_=RecordedSession))
    api = FastAPI()
    api.include_router(picks_intraday.router, prefix="/api")
    main_thread = threading.get_ident()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),
                                     base_url="http://isolated") as client:
            with pytest.raises(RuntimeError, match="isolated read unavailable"):
                await client.get(f"/api/picks/watch-ledger?date={target}&days=3")
            assert len(opened) == len(closed) == 1
            response = await client.get(f"/api/picks/watch-ledger?date={target}&days=3")
            assert response.status_code == 200
            assert response.json() == expected
        assert len(opened) == len(closed) == 6
        assert len({id(session) for session, _thread in opened}) == 6
        assert all(thread != main_thread for _session, thread in opened)
        assert opened == closed

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()
