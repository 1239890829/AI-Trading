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
    # 行情必须带限价：本用例测的是资金校验，涨跌停守卫（S1-4）在前，
    # 用不完整的行情会让它先被拒、测不到目标分支。
    engine = make_engine({"600519": __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")})
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 20000))  # 200万 > 100万
    assert order.status == "rejected"
    assert "资金不足" in order.reason


def test_pending_limit_order_and_cancel():
    reset()
    engine = make_engine({"600519": __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")})
    # 限价 95 < 现价 100 → 挂单
    order = asyncio.run(engine.place_order("600519", "buy", 95.0, 100))
    assert order.status == "pending"
    # 撤单 → 资金解冻
    cancelled = engine.cancel(order.id)
    assert cancelled.status == "cancelled"
    acc = engine.ensure_account()
    assert abs(acc.cash - 1_000_000) < 1


# ---------- S1-4：涨跌停守卫不得静默失效（红线 5） ----------
# 口径（用户 2026-09-11 拍板）：**缺限价一律拒单 + 显式原因**。
# 原写法 `if quote.limit_up_price and ...` 在限价为 None/0 时整体短路，
# 守卫静默失效且与"该票今天没触板"完全不可区分。


def test_buy_missing_limit_up_rejected():
    reset()
    engine = make_engine({"600519": __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_down_price=90.0, source="t")})
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 100))
    assert order.status == "rejected"
    assert "缺涨停价" in order.reason


def test_buy_zero_limit_up_rejected():
    """限价为 0 与 None 必须同判——0 在布尔上下文里是假值，走的是同一个静默分支。"""
    reset()
    engine = make_engine({"600519": __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=0.0, source="t")})
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 100))
    assert order.status == "rejected"
    assert "缺涨停价" in order.reason


def test_sell_missing_limit_down_rejected():
    reset()
    q = __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")
    engine = make_engine({"600519": q})
    asyncio.run(engine.place_order("600519", "buy", 100.0, 200))
    with engine._sf() as db:
        from app.models.paper import PaperPosition

        db.query(PaperPosition).filter(PaperPosition.symbol == "600519").one().frozen_today = 0
        db.commit()
    # 限价缺失（用只带涨停价的行情覆盖）
    engine._quote_fn = lambda _sym: _async(
        __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=110.0, source="t")
    )
    order = asyncio.run(engine.place_order("600519", "sell", 99.0, 200))
    assert order.status == "rejected"
    assert "缺跌停价" in order.reason


def test_match_pending_does_not_fill_when_limit_missing():
    """挂单是"限价可用时"挂下的，成交时点必须重新校验——否则等于在涨停板成交。"""
    reset()
    q = __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")
    engine = make_engine({"600519": q})
    order = asyncio.run(engine.place_order("600519", "buy", 95.0, 100))
    assert order.status == "pending"

    # 行情涨到 99（≥ 挂单价 95）但限价字段消失 → 不得成交
    engine._quote_fn = lambda _sym: _async(
        __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=99.0, source="t")
    )
    left = asyncio.run(engine.match_pending())
    assert left == 1, "缺限价时不应撮合挂单"
    with engine._sf() as db:
        from app.models.paper import PaperOrder

        assert db.query(PaperOrder).filter(PaperOrder.id == order.id).one().status == "pending"


def test_match_pending_does_not_fill_at_limit_up():
    """涨到涨停价后挂单不得成交（红线 5 的成交时点校验）。"""
    reset()
    q = __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")
    engine = make_engine({"600519": q})
    order = asyncio.run(engine.place_order("600519", "buy", 95.0, 100))
    assert order.status == "pending"
    engine._quote_fn = lambda _sym: _async(
        __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=110.0, limit_up_price=110.0, limit_down_price=90.0, source="t")
    )
    left = asyncio.run(engine.match_pending())
    assert left == 1, "涨停价上不应成交买单"
    with engine._sf() as db:
        from app.models.paper import PaperOrder

        assert db.query(PaperOrder).filter(PaperOrder.id == order.id).one().status == "pending"


def test_quote_limit_prices_state_three_way():
    Quote = __import__("app.schemas.market", fromlist=["Quote"]).Quote

    assert Quote(symbol="600519", limit_up_price=110.0, limit_down_price=90.0, source="t").limit_prices_state() == "ready"
    assert Quote(symbol="600519", limit_up_price=110.0, source="t").limit_prices_state() == "partial"
    assert Quote(symbol="600519", limit_up_price=0.0, limit_down_price=0.0, source="t").limit_prices_state() == "unavailable"
    assert Quote(symbol="600519", source="t").limit_prices_state() == "unavailable"


def _async(value):
    async def _c():
        return value

    return _c()


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
    q = __import__("app.schemas.market", fromlist=["Quote"]).Quote(symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")
    engine = make_engine({"600519": q})
    asyncio.run(engine.place_order("600519", "buy", 100.0, 100))
    acc = engine.reset(initial_cash=500_000.0)
    assert acc.initial_cash == 500_000.0
    assert abs(acc.cash - 500_000) < 1
    summary = engine.account_summary(0.0)
    assert summary["total"] == 500_000.0
    assert summary["total_pnl"] == 0.0




