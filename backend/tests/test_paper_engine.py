import sys
sys.path.insert(0, ".")

import asyncio

from app.core.db import get_session_factory
from app.paper.engine import PaperTradingEngine
from app.paper.reconcile import reconcile


def assert_ledger_consistent(sf):
    """**业务不变量断言**：账本必须自洽（资金守恒 / 预留成对 / scope 隔离 / 成交对持仓）。

    `GOV-002`（报告 O4）：本文件的既有断言是**行为级**的——`order.status == "filled"`、
    `acc.cash < 1_000_000` 这类只看"函数返回了什么"。它们**证明不了钱是对的**：
    R01（挂单冻结重复扣款）当年就是在**全部行为断言全绿**的情况下活着的
    （`acc.cash < 1_000_000` 对"扣一次"和"扣两次"同样成立——**弱断言不含守恒量**）。

    故此处改断**业务不变量**，且**直接复用生产对账器**（`IMP-003`）：判据只有一份，
    生产怎么判这里就怎么判，不重写第二套恒等式（重写 = 第二个真相源，费率一改就静默失真）。
    """
    rep = reconcile(sf)
    bad = [
        f for s in rep["scopes"] for f in s["findings"] if f["severity"] == "error"
    ]
    assert rep["ok"] is True, f"账本不自洽：{bad}"


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

    # 成交之后账本必须仍自洽（`GOV-002`：把断言从"函数返回"提到"业务不变量"）
    assert_ledger_consistent(engine._sf)


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
    # ⚠️ 上面那条是**弱断言**：它对"回款正确"与"回款少了一半"同样成立（只给了下界）。
    # 真正的判据是业务不变量——买→卖一轮之后，账本的资金守恒/持仓对照必须仍成立。
    assert_ledger_consistent(engine._sf)


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


# ---------------------------------------------------------------- 业务不变量（GOV-002）

def test_cash_conservation_holds_across_full_order_lifecycle():
    """**资金守恒不变量**：买 → 卖 → 再挂单（未成交）全流程后，钱必须一笔不差。

    `GOV-002`（报告 O4）：本文件既有用例只断"函数返回了什么"，**盖不住守恒量**——
    报告点名的 `assert acc.cash > 1_000_000 * 0.9` 就是典型：下界断言对
    "回款正确"与"回款被吞掉一半"**同样成立**，R01（挂单冻结重复扣款）正是在
    **这类断言全绿**的情况下活着的。

    本用例改断**恒等式**（同源：判据来自生产 `reconcile`，不在此重写）：
        initial_cash − cash == Σ已成交买单实付 + Σ挂单中买单冻结额 − Σ卖出净收入
    并额外断"账本自洽"（四条不变量全过）——**把这笔交易当成一次对账**。
    """
    reset()
    from app.schemas.market import Quote
    quote = Quote(
        symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")
    engine = make_engine({"600519": quote})

    # ① 买入成交
    assert asyncio.run(engine.place_order("600519", "buy", 100.0, 200)).status == "filled"
    # ② 卖出成交（限价须低于现价才即时成交；当日可卖量不足 ⇒ 先解冻，模拟次日）
    from app.models.paper import PaperPosition
    with engine._sf() as db:
        pos = db.query(PaperPosition).filter(PaperPosition.symbol == "600519").one()
        pos.frozen_today = 0
        db.commit()
    assert asyncio.run(engine.place_order("600519", "sell", 99.0, 100)).status == "filled"
    # ③ 再挂一笔**不成交**的买单（限价 90 < 现价 100 ⇒ 挂单 pending，占用冻结额）
    assert asyncio.run(engine.place_order("600519", "buy", 90.0, 100)).status == "pending"

    # 全流程结束 ⇒ 账本必须自洽（资金守恒 + 预留成对 + scope 隔离 + 成交对持仓）
    assert_ledger_consistent(engine._sf)

    # 守恒式显式复核一次（把不变量写在测试里，失败时能直接看出残差）
    import app.paper.reconcile as rec
    from app.models.paper import PaperOrder
    with engine._sf() as db:
        acc = engine.ensure_account()
        orders = db.query(PaperOrder).all()
        _, metrics = rec._cash_conservation(acc, orders)
    assert abs(metrics["residual"]) <= rec.TOL, f"资金守恒残差 {metrics['residual']}"


def test_ledger_consistency_assertion_can_actually_fail():
    """**判据自证**（[[KB-ENG-65]]）：`assert_ledger_consistent` 必须真能变红。

    一个"永远绿"的守卫比没有守卫更糟——它会让后续所有改动都以为账本没问题。
    故此处**定向注入 R01 形态**（账户现金被多扣一笔、订单侧却没有对应冻结额），
    断言判据**报错**且错误里带得出 `cash_conservation` 这条检查名。

    注入只在**测试内**改数据、不碰生产代码（改完即随 fixture 重建消失）。
    """
    import pytest

    reset()
    from app.schemas.market import Quote
    quote = Quote(
        symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")
    engine = make_engine({"600519": quote})
    asyncio.run(engine.place_order("600519", "buy", 100.0, 100))

    # 注入：凭空多扣 187.00（≈ R01「冻结额未归还 / 重复扣款」在资金面上的形态）
    from app.models.paper import PaperAccount
    with engine._sf() as db:
        acc = db.query(PaperAccount).first()
        acc.cash -= 187.00
        db.commit()

    with pytest.raises(AssertionError) as ei:
        assert_ledger_consistent(engine._sf)
    assert "cash_conservation" in str(ei.value), "判据变红了，但没指出是哪条不变量"




