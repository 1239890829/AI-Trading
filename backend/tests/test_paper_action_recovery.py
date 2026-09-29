"""User action retry, expiry and rollback against an isolated file database."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes.paper import OrderIn, place_order
from app.models.paper import PaperOrder, PaperPosition
from app.models.watchlist import Base
from app.paper.engine import PaperTradingEngine


def test_retry_same_action_does_not_fill_twice(tmp_path):
    db_engine = create_engine(f"sqlite:///{tmp_path / 'paper.db'}")
    Base.metadata.create_all(db_engine)
    sf = sessionmaker(bind=db_engine, expire_on_commit=False)

    async def quote(_):
        return SimpleNamespace(price=10, limit_up_price=11, limit_down_price=9)

    engine = PaperTradingEngine(sf, quote)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(paper=engine)))
    body = OrderIn(symbol="600127", side="buy", price=10, quantity=100,
                   request_id=str(uuid4()),
                   expires_at=datetime.now(timezone.utc) + timedelta(seconds=30))
    first = asyncio.run(place_order(body, request))
    second = asyncio.run(place_order(body, request))
    assert second == {"data": {**first["data"], "replayed": True}}
    with sf() as db:
        assert db.query(PaperOrder).count() == 1
        assert db.query(PaperPosition).one().quantity == 100
    db_engine.dispose()


import pytest
from fastapi import HTTPException
from sqlalchemy import event
from app.models.paper import PaperActionReceipt


@pytest.fixture
def action_env(tmp_path):
    bind = create_engine(f"sqlite:///{tmp_path / 'actions.db'}")
    Base.metadata.create_all(bind)
    sf = sessionmaker(bind=bind, expire_on_commit=False)
    quotes = {"price": 10.0, "calls": 0}

    async def quote(_):
        quotes["calls"] += 1
        await asyncio.sleep(0)
        return SimpleNamespace(price=quotes["price"], limit_up_price=11, limit_down_price=9)

    engine = PaperTradingEngine(sf, quote)
    engine.ensure_account()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(paper=engine)))
    body = OrderIn(symbol="600127", side="buy", price=10, quantity=100,
                   request_id=uuid4(), expires_at=datetime.now(timezone.utc) + timedelta(seconds=30))
    yield bind, sf, engine, request, body, quotes
    bind.dispose()


def test_concurrent_retry_and_restart_use_committed_receipt(action_env):
    _, sf, engine, req, body, quotes = action_env

    async def submit_twice():
        return await asyncio.gather(place_order(body, req), place_order(body, req))

    a, b = asyncio.run(submit_twice())
    assert b == {"data": {**a["data"], "replayed": True}}
    engine2 = PaperTradingEngine(sf, engine._quote_fn)
    req.app.state.paper = engine2
    calls = quotes["calls"]
    assert asyncio.run(place_order(body, req)) == b
    assert quotes["calls"] == calls
    with sf() as db:
        assert db.query(PaperOrder).count() == 1
        assert db.query(PaperActionReceipt).count() == 1
        assert db.query(PaperPosition).one().quantity == 100
    assert engine2.ensure_account().cash == pytest.approx(998994.99)


def test_lost_receipt_commit_rolls_back_engine_commit(action_env):
    bind, sf, engine, req, body, _ = action_env

    def fail_receipt(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO paper_action_receipt"):
            raise RuntimeError("disk write failed")

    event.listen(bind, "before_cursor_execute", fail_receipt)
    with pytest.raises(RuntimeError, match="disk write failed"):
        asyncio.run(place_order(body, req))
    event.remove(bind, "before_cursor_execute", fail_receipt)
    with sf() as db:
        assert db.query(PaperOrder).count() == 0
        assert db.query(PaperPosition).count() == 0
        assert db.query(PaperActionReceipt).count() == 0
    assert engine.ensure_account().cash == 1_000_000
    assert asyncio.run(place_order(body, req))["data"]["status"] == "filled"


def test_expired_or_reused_identity_cannot_create_new_order(action_env):
    _, sf, engine, req, body, quotes = action_env
    expired = body.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    with pytest.raises(HTTPException) as caught:
        asyncio.run(place_order(expired, req))
    assert caught.value.status_code == 422
    assert quotes["calls"] == 0
    first = asyncio.run(place_order(body, req))
    conflict = body.model_copy(update={"quantity": 200})
    with pytest.raises(HTTPException) as caught:
        asyncio.run(place_order(conflict, req))
    assert caught.value.status_code == 409
    engine.reset()
    assert asyncio.run(place_order(body, req)) == {"data": {**first["data"], "replayed": True}}
    with sf() as db:
        assert db.query(PaperOrder).count() == 0
        assert db.query(PaperPosition).count() == 0
    assert engine.ensure_account().cash == 1_000_000


def test_rejected_action_stays_rejected_after_quote_recovers(action_env):
    _, sf, _, req, body, quotes = action_env
    quotes["price"] = 0
    with pytest.raises(HTTPException, match="停牌或无行情"):
        asyncio.run(place_order(body, req))
    quotes["price"] = 10
    with pytest.raises(HTTPException, match="停牌或无行情"):
        asyncio.run(place_order(body, req))
    with sf() as db:
        assert db.query(PaperOrder).count() == 0
        assert db.query(PaperActionReceipt).count() == 1


def test_scope_and_pending_cancel_preserve_cash(action_env):
    _, sf, engine, req, body, _ = action_env
    pending = body.model_copy(update={"price": 9.5})
    result = asyncio.run(place_order(pending, req))
    assert result["data"]["status"] == "pending"
    engine.cancel(result["data"]["id"])
    assert asyncio.run(place_order(pending, req)) == {"data": {**result["data"], "replayed": True}}
    assert engine.ensure_account().cash == 1_000_000
    req.app.state.paper = PaperTradingEngine(sf, engine._quote_fn, scope="shadow")
    asyncio.run(place_order(body, req))
    with sf() as db:
        assert db.query(PaperActionReceipt).count() == 2
        assert db.query(PaperPosition).one().scope == "shadow"


def test_reset_backup_restores_original_book_and_cannot_overwrite_new_actions(action_env):
    _, sf, engine, req, body, _ = action_env
    from app.paper.reset_recovery import restore
    asyncio.run(place_order(body, req))
    cash = engine.ensure_account().cash
    acc = engine.reset()
    assert restore(engine, acc.reset_backup_id)["restored"] is True
    assert restore(engine, acc.reset_backup_id)["replayed"] is True
    assert engine.ensure_account().cash == cash
    with sf() as db:
        assert db.query(PaperPosition).one().quantity == 100
        assert db.query(PaperOrder).count() == 1
    acc = engine.reset()
    another = body.model_copy(update={"request_id": uuid4()})
    asyncio.run(place_order(another, req))
    with pytest.raises(ValueError, match="已有变化"):
        restore(engine, acc.reset_backup_id)
    with sf() as db:
        assert db.query(PaperPosition).one().quantity == 100


def test_manual_record_rejects_nonfinite_and_impossible_dates():
    from pydantic import ValidationError
    from app.api.routes.real_position import TradeIn
    for change in ({"fill_price": float("inf")}, {"fee": float("nan")},
                   {"traded_at": "2026-02-30"}, {"traded_at": "9999-01-01"}):
        with pytest.raises(ValidationError):
            TradeIn(**{"symbol": "600127", "side": "buy", "fill_price": 10,
                       "quantity": 100, **change})
