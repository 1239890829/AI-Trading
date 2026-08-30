import sys
sys.path.insert(0, ".")

import asyncio

from app.core.db import get_session_factory
from app.paper.engine import PaperTradingEngine

FIXED = lambda: __import__("datetime").datetime(2026, 8, 28, 10, 30)  # noqa: E731


def make_engine(quote_map):
    class Q:
        def __init__(self, price, lu=None, ld=None):
            self.price = price
            self.limit_up_price = lu
            self.limit_down_price = ld

    async def quote_fn(symbol):
        return quote_map.get(symbol)

    engine = PaperTradingEngine(get_session_factory(), quote_fn)
    return engine


def reset():
    from app.models.paper import PaperAccount, PaperOrder, PaperPosition
    from app.models.watchlist import Base
    from app.core.db import get_engine

    Base.metadata.create_all(get_engine())
    sf = get_session_factory()
    with sf() as db:
        for m in (PaperAccount, PaperOrder, PaperPosition):
            db.query(m).delete()
        db.commit()


def test_buy_fill_and_t1():
    reset()
    engine = make_engine({"600519": __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")})
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 200))
    assert order.status == "filled", order.reason
    assert order.filled_price == 100.0
    acc = engine.ensure_account()
    assert acc.cash < 1_000_000  # 扣款
    from app.models.paper import PaperPosition

    with engine._sf() as db:
        pos = db.query(PaperPosition).filter(PaperPosition.symbol == "600519").one()
        assert pos.quantity == 200
        assert pos.available == 0  # T+1 当日不可卖


def test_buy_limit_up_rejected():
    reset()
    engine = make_engine({"600519": __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=110.0, source="t")})
    order = asyncio.run(engine.place_order("600519", "buy", 110.0, 100))
    assert order.status == "rejected"
    assert "涨停" in order.reason


def test_sell_t1_blocked_then_allowed():
    reset()
    q = __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")
    engine = make_engine({"600519": q})
    asyncio.run(engine.place_order("600519", "buy", 100.0, 200))
    # 当日卖出 → 可卖数量不足
    order = asyncio.run(engine.place_order("600519", "sell", 99.0, 200))
    assert order.status == "rejected"
    assert "可卖" in order.reason
    # 模拟次日（frozen 清零）
    from app.models.paper import PaperPosition

    with engine._sf() as db:
        pos = db.query(PaperPosition).filter(PaperPosition.symbol == "600519").one()
        pos.frozen_today = 0
        db.commit()
    order2 = asyncio.run(engine.place_order("600519", "sell", 99.0, 200))
    assert order2.status == "filled"
    acc = engine.ensure_account()
    assert acc.cash > 1_000_000 * 0.9  # 卖出回款（价格略降+费用）


def test_sell_limit_down_rejected():
    reset()
    engine = make_engine({"600519": __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_down_price=90.0, source="t")})
    order = asyncio.run(engine.place_order("600519", "sell", 90.0, 100))
    assert order.status == "rejected"
    assert "跌停" in order.reason


def test_buy_odd_lot_rejected():
    reset()
    engine = make_engine({"600519": __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, source="t")})
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 150))
    assert order.status == "rejected"
    assert "100股" in order.reason


def test_insufficient_cash_rejected():
    reset()
    engine = make_engine({"600519": __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, source="t")})
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 20000))  # 200万 > 100万
    assert order.status == "rejected"
    assert "资金不足" in order.reason


def test_pending_limit_order_and_cancel():
    reset()
    engine = make_engine({"600519": __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, source="t")})
    # 限价 95 < 现价 100 → 挂单
    order = asyncio.run(engine.place_order("600519", "buy", 95.0, 100))
    assert order.status == "pending"
    # 撤单 → 资金解冻
    cancelled = engine.cancel(order.id)
    assert cancelled.status == "cancelled"
    acc = engine.ensure_account()
    assert abs(acc.cash - 1_000_000) < 1


def test_reset_clears_positions_and_restores_cash():
    reset()
    from app.models.paper import PaperOrder, PaperPosition

    q = __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")
    engine = make_engine({"600519": q})
    asyncio.run(engine.place_order("600519", "buy", 100.0, 200))
    with engine._sf() as db:
        assert db.query(PaperPosition).count() == 1
        assert db.query(PaperOrder).count() == 1

    acc = engine.reset()
    assert abs(acc.cash - 1_000_000) < 1

    with engine._sf() as db:
        assert db.query(PaperPosition).count() == 0
        assert db.query(PaperOrder).count() == 0
    assert engine.positions_with_pnl({}) == []


def test_reset_with_custom_initial_cash():
    reset()
    q = __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, source="t")
    engine = make_engine({"600519": q})
    asyncio.run(engine.place_order("600519", "buy", 100.0, 100))
    acc = engine.reset(initial_cash=500_000.0)
    assert acc.initial_cash == 500_000.0
    assert abs(acc.cash - 500_000) < 1
    summary = engine.account_summary(0.0)
    assert summary["total"] == 500_000.0
    assert summary["total_pnl"] == 0.0




