"""下单/读取路径的**执行层守卫**（R05 数值有限性 + R06 账户域隔离 + R01 权益口径）。

三条都是「门禁全绿也照不出来」的形态，故每条都配**绝对结果断言**
（断言金额/集合本身），而不是只断言 `status`（KB-ENG-65 形态②：
状态断言对金额不敏感；`status == "filled"` 在按 0 元成交时同样成立）。
"""

from __future__ import annotations

import asyncio
import types

import pytest

from app.core.db import get_engine, get_session_factory
from app.paper.engine import PaperTradingEngine, buy_freeze_amount, is_valid_price


class _Quotes:
    def __init__(self, price, limit_up=110.0, limit_down=90.0):
        self.price = price
        self.limit_up_price = limit_up
        self.limit_down_price = limit_down


def _make(quotes: dict[str, _Quotes], *, scope: str = "main") -> PaperTradingEngine:
    async def quote_fn(symbol):
        return quotes.get(symbol)

    return PaperTradingEngine(get_session_factory(), quote_fn, scope=scope)


def _reset() -> None:
    from app.models.paper import PaperAccount, PaperOrder, PaperPosition
    from app.models.watchlist import Base

    Base.metadata.create_all(get_engine())
    with get_session_factory()() as db:
        for model in (PaperAccount, PaperOrder, PaperPosition):
            db.query(model).delete()
        db.commit()


def _cash(engine: PaperTradingEngine) -> float:
    return engine.ensure_account().cash


def _req(engine: PaperTradingEngine):
    """路由函数只用到 `request.app.state.paper`，无需起 lifespan（35-46s）。"""
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(paper=engine))
    )


@pytest.fixture(autouse=True)
def _cleanup_after():
    """**每个用例结束后**清空模拟盘三表。

    为什么必须在 teardown 清（2026-09-14 实测踩到）：这些用例会真的建仓，
    而 `test_api::test_paper_fills_and_reset` 依赖「600519 无持仓」才能通过
    —— 留一行持仓过去，它会被风控以「单票仓位上限 10%」拦成 422。
    完整套件里 `test_api.py` 按文件名排在前面侥幸不撞，但**那种绿依赖文件名的
    字母序**（用 `pytest 指定文件顺序` 复跑就红）。隔离必须自证，不能靠排序。
    """
    yield
    _reset()


def _orders(engine: PaperTradingEngine, **kw):
    from app.api.routes.paper import paper_orders

    return asyncio.run(paper_orders(_req(engine), **kw))["data"]


def _fills(engine: PaperTradingEngine, **kw):
    from app.api.routes.paper import paper_fills

    return asyncio.run(paper_fills(_req(engine), **kw))["data"]


# ============================================================ R05 数值有限性


@pytest.mark.parametrize(
    "value,ok",
    [
        (10.0, True), (0.01, True), (1, True),
        (0, False), (0.0, False), (-1.0, False),
        (float("nan"), False),          # `nan <= 0` 为 False ⇒ 原判据放行
        (float("inf"), False), (float("-inf"), False),
        (None, False), ("10", False),   # 字符串不得被隐式当成价
        (True, False),                  # bool 是 int 子类，True 会被当成价 1
    ],
)
def test_is_valid_price_matrix(value, ok):
    assert is_valid_price(value) is ok


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_place_order_rejects_non_finite_price(bad):
    """委托价非法 ⇒ 拒单且**不得产生任何成交/持仓/资金变动**。

    断言「非法价格」这个**原因本身**（而非只断言 rejected）：不这么钉，
    `inf` 会被下游的 `need > acc.cash`（inf 恒大于余额）以
    「可用资金不足」糊过去 —— 结论碰巧对、归因全错，用户以为该票买不起。
    """
    _reset()
    engine = _make({"600519": _Quotes(price=100.0)})
    before = _cash(engine)

    order = asyncio.run(engine.place_order("600519", "buy", bad, 1000))

    assert order.status == "rejected", f"{bad!r} 应被拒，实际 {order.status}"
    assert "有限正数" in (order.reason or ""), f"{bad!r} 的拒单归因错：{order.reason!r}"
    assert _cash(engine) == pytest.approx(before, abs=0.01)
    from app.models.paper import PaperOrder, PaperPosition

    with engine._sf() as db:
        assert db.query(PaperPosition).count() == 0
        assert db.query(PaperOrder).count() == 0  # 拒单不落库（与既有语义一致）
        acc = engine._account(db)
        assert acc.cash == pytest.approx(before, abs=0.01), "NaN 若写进 cash 会污染整列（NOT NULL/比较全 False）"


def test_zero_quote_price_is_not_tradable():
    """行情价为 0 ⇒ 按「停牌/无行情」拒单（原判据 `<= 0` 能挡，但需与 NaN 一起钉住）。"""
    _reset()
    engine = _make({"600519": _Quotes(price=0.0)})
    order = asyncio.run(engine.place_order("600519", "buy", 100.0, 1000))
    assert order.status == "rejected"
    assert "停牌或无行情" in (order.reason or "")


def test_match_pending_skips_when_quote_price_becomes_zero():
    """**R05 主症状**：挂单期间行情价变 0 ⇒ 不得按 0 元成交。

    原判据只有 `quote.price is None`，0 会进来并按 0 元撮合：
    买入白得 1000 股、只付了 5 元最低佣金（现金几乎不动）。
    """
    _reset()
    quotes = {"600519": _Quotes(price=100.0)}
    engine = _make(quotes)
    order = asyncio.run(engine.place_order("600519", "buy", 95.0, 1000))
    assert order.status == "pending", order.reason

    quotes["600519"].price = 0.0  # 上游缺值被填 0 的常见形态
    left = asyncio.run(engine.match_pending())

    assert left == 1, "行情价无效时应跳过本轮（挂单保留），而不是撮合成交"
    from app.models.paper import PaperOrder, PaperPosition

    with engine._sf() as db:
        assert db.query(PaperOrder).filter(PaperOrder.id == order.id).one().status == "pending"
        assert db.query(PaperPosition).count() == 0, "按 0 元成交会凭空得到持仓"
    # 冻结额仍被占用，撤单后必须原额归还
    assert engine.cancel(order.id).status == "cancelled"
    assert _cash(engine) == pytest.approx(1_000_000.0, abs=0.01)


@pytest.mark.parametrize("bad", [0.0, float("nan"), float("inf")])
def test_fill_rejects_invalid_fill_price(bad):
    """成交路径二次校验：`match_pending` 会直接调 `_fill`，不能只信下单时的价。"""
    _reset()
    engine = _make({"600519": _Quotes(price=100.0)})
    with engine._sf() as db:
        from app.models.paper import PaperOrder

        acc = engine._account(db)
        order = PaperOrder(scope="main", symbol="600519", side="buy", price=100.0,
                           quantity=1000, status="pending")
        db.add(order)
        db.commit()
        before = acc.cash
        out = asyncio.run(engine._fill(db, acc, order, bad, 1000))
        assert out.status == "rejected"
        assert acc.cash == pytest.approx(before, abs=0.01), "非法成交价不得动资金"


def test_fill_sell_rejects_invalid_fill_price():
    """卖出按 0 元成交会交出持仓且分文不入（proceeds 被费用吃成负数）。"""
    _reset()
    engine = _make({"600519": _Quotes(price=100.0)})
    asyncio.run(engine.place_order("600519", "buy", 100.0, 1000))  # 建仓
    with engine._sf() as db:
        from app.models.paper import PaperOrder, PaperPosition

        pos = db.query(PaperPosition).filter(PaperPosition.symbol == "600519").one()
        pos.frozen_today = 0  # 模拟已过 T+1
        order = PaperOrder(scope="main", symbol="600519", side="sell", price=100.0,
                           quantity=1000, status="pending")
        db.add(order)
        db.commit()
        acc = engine._account(db)
        out = asyncio.run(engine._fill_sell(db, acc, order, float("nan"), 1000))
        assert out.status == "rejected"
        assert db.query(PaperPosition).filter(PaperPosition.symbol == "600519").one().quantity == 1000


# ============================================================ R06 账户域隔离


def test_orders_endpoint_filters_by_status_without_error():
    """带 `status` 的查询**必须能跑通**（原实现 limit 之后 filter ⇒ InvalidRequestError）。"""
    _reset()
    engine = _make({"600519": _Quotes(price=100.0)})
    asyncio.run(engine.place_order("600519", "buy", 95.0, 1000))        # pending
    asyncio.run(engine.place_order("600519", "buy", 100.0, 1000))       # filled

    assert len(_orders(engine)) == 2
    assert [r["status"] for r in _orders(engine, status="pending")] == ["pending"]
    assert [r["status"] for r in _orders(engine, status="filled")] == ["filled"]
    # 无匹配时返回空列表而不是报错
    assert _orders(engine, status="cancelled") == []


def test_status_filter_is_not_truncated_by_limit():
    """`filter(status)` 必须在 `limit` **之前**——否则符合条件但排在后面的记录会被漏掉。

    这是「把 filter 挪到前面」这种修法的边界：先取 50 条再筛，
    第 51 条起符合条件的记录永远看不到（且表现为"没有数据"，静默）。
    """
    _reset()
    engine = _make({"600519": _Quotes(price=100.0)})
    for _ in range(3):
        asyncio.run(engine.place_order("600519", "buy", 100.0, 100))  # 3 条 filled（最旧）
    for _ in range(55):
        asyncio.run(engine.place_order("600519", "buy", 95.0, 100))   # 55 条 pending（最新）
    pending = engine.frozen_cash()
    assert pending > 0, "夹具前提：必须真的产生了 pending 挂单"

    rows = _orders(engine, status="filled")
    assert len(rows) == 3, f"3 条 filled 被 limit 截断成 {len(rows)} 条 ⇒ filter 顺序错"


def test_orders_and_fills_do_not_leak_shadow_scope():
    """**R06 主症状**：main 端点不得返回 shadow 的记录。

    写入侧早已 scope 化，但读取侧直接查表 ⇒ main 的委托列表与 K 线 B/S 标记
    会混入每日精选影子账户的操作（研究口径污染真实操作视图，且无从察觉）。
    """
    _reset()
    main = _make({"600519": _Quotes(price=100.0)}, scope="main")
    shadow = _make({"600519": _Quotes(price=100.0)}, scope="shadow")
    asyncio.run(main.place_order("600519", "buy", 95.0, 1000))      # main 挂单（pending）
    asyncio.run(shadow.place_order("600519", "buy", 95.0, 1000))    # shadow 同形挂单

    rows = _orders(main)
    from app.models.paper import PaperOrder

    with main._sf() as db:
        main_count = db.query(PaperOrder).filter(PaperOrder.scope == "main").count()
        total = db.query(PaperOrder).count()
    assert main_count < total, "夹具前提：库里必须确实有 shadow 记录，否则测不出泄漏"
    assert len(rows) == main_count, f"泄漏了 {len(rows) - main_count} 条 shadow 委托"

    # 成交视图同源：让 main 成交一笔，shadow 保持 pending
    asyncio.run(main.place_order("600519", "buy", 100.0, 100))
    fills = _fills(main)
    assert len(fills) == 1 and fills[0]["side"] == "buy"
    assert len(_fills(shadow)) == 0, "shadow 自己也没成交，互不串号"


def test_fills_symbol_filter_still_works():
    _reset()
    engine = _make({"600519": _Quotes(price=100.0)})
    asyncio.run(engine.place_order("600519", "buy", 100.0, 1000))
    assert len(_fills(engine)) == 1
    assert len(_fills(engine, symbol="600519")) == 1
    assert _fills(engine, symbol="000001") == []


# ============================================================ R01 权益口径


def test_equity_includes_frozen_cash_for_pending_buy():
    """挂单冻结额仍是自己的钱 ⇒ 总权益不得因其下降（R01「权益漏计冻结资金」）。

    合成场景：初始 100 万，限价 95 挂 1000 股（现价 100）。
    修复前 `total = cash + 市值` ⇒ 权益从 100 万虚降至 904,975.30，
    用户会以为自己"亏了 9 万"，而那是自己的挂单。
    """
    _reset()
    engine = _make({"600519": _Quotes(price=100.0)})
    order = asyncio.run(engine.place_order("600519", "buy", 95.0, 1000))
    assert order.status == "pending", order.reason

    frozen = buy_freeze_amount(95.0, 1000)
    summary = engine.account_summary(0.0)

    assert summary["frozen_cash"] == pytest.approx(frozen, abs=0.01)
    assert summary["cash"] == pytest.approx(1_000_000.0 - frozen, abs=0.01)
    assert summary["total"] == pytest.approx(1_000_000.0, abs=0.01), "挂单不该改变净资产"
    assert summary["total_pnl"] == pytest.approx(0.0, abs=0.01)

    # 撤单后冻结归零，三者仍自洽（守恒）
    engine.cancel(order.id)
    after = engine.account_summary(0.0)
    assert after["frozen_cash"] == 0.0
    assert after["total"] == pytest.approx(1_000_000.0, abs=0.01)


def test_equity_conservation_across_pending_then_fill():
    """冻结 → 成交 全链路守恒：total 不因中间状态而漂移。"""
    _reset()
    quotes = {"600519": _Quotes(price=100.0)}
    engine = _make(quotes)
    asyncio.run(engine.place_order("600519", "buy", 95.0, 1000))  # pending，冻结

    quotes["600519"].price = 94.0
    asyncio.run(engine.match_pending())  # 按 94 成交

    from app.models.paper import PaperPosition

    with engine._sf() as db:
        pos = db.query(PaperPosition).filter(PaperPosition.symbol == "600519").one()
        qty, cost = pos.quantity, pos.cost_price
    summary = engine.account_summary(qty * 94.0)

    assert summary["frozen_cash"] == 0.0
    # 净资产 = 初始 − 已实现费用（佣金+过户费），按市值计价后不含浮亏
    expected = 1_000_000.0 - buy_freeze_amount(94.0, 1000) + qty * 94.0
    assert summary["total"] == pytest.approx(expected, abs=0.02)
    assert cost == pytest.approx(94.0, abs=0.01)  # 未把费用摊进成本（既有口径）
