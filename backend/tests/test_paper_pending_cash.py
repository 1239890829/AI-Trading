"""模拟盘**挂单资金账目**回归（2026-09-14）。

背景（本轮架构审查实测缺陷）：买入挂单在 `place_order` 里先扣一次「冻结额」
（`acc.cash -= need`），`match_pending` 成交时走 `_fill` 又扣一次「实付」
（`acc.cash -= cost`），**冻结额永不归还** ⇒ 同一笔钱扣两次，模拟盘资金
系统性偏低。而模拟盘正是「策略胜率基线」的来源，账目失真会污染基线。

同源第二症状（更隐蔽）：大额挂单在成交时会被**正确性拒单**——因为可用资金
先被冻结扣走，`_fill` 的资金校验用的是「冻结后余额」，于是 `cost > cash`
成立，一笔本来完全买得起的委托被判「可用资金不足（含费用）」。

为什么原门禁没照出来：唯一覆盖挂单成交路径的 `test_paper_risk_gate.py`
只断言 `status == "filled"`，**从未断言 cash**；而金额断言缺失时，
「扣两次」与「扣一次」在状态字段上完全同形（KB-ENG-65 形态②同族：
状态断言对金额不敏感）。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.db import get_engine, get_session_factory
from app.paper.engine import PaperTradingEngine, buy_freeze_amount


class _Quotes:
    """可变报价源：同一标的可中途改价，用于驱动「挂单 → 价格到位 → 成交」。"""

    def __init__(self, price: float, limit_up: float | None = 110.0, limit_down: float | None = 90.0):
        self.price = price
        self.limit_up_price = limit_up
        self.limit_down_price = limit_down


def _make(quotes: dict[str, _Quotes]) -> PaperTradingEngine:
    async def quote_fn(symbol):
        return quotes.get(symbol)

    return PaperTradingEngine(get_session_factory(), quote_fn)


def _reset() -> None:
    from app.core.db import get_session_factory as _sf
    from app.models.paper import PaperAccount, PaperOrder, PaperPosition
    from app.models.watchlist import Base

    Base.metadata.create_all(get_engine())
    with _sf()() as db:
        for model in (PaperAccount, PaperOrder, PaperPosition):
            db.query(model).delete()
        db.commit()


def _cash(engine: PaperTradingEngine) -> float:
    return engine.ensure_account().cash


def _set_cash(engine: PaperTradingEngine, amount: float) -> None:
    with engine._sf() as db:
        engine._account(db).cash = amount
        db.commit()


# ---------- 修复的主症状：挂单成交只扣一次 ----------


def test_pending_buy_fill_charges_exactly_once():
    _reset()
    quotes = {"600519": _Quotes(price=100.0)}
    engine = _make(quotes)

    # 限价 95 < 现价 100 ⇒ 挂单，下单时冻结 95,000 + 佣金
    order = asyncio.run(engine.place_order("600519", "buy", 95.0, 1000))
    assert order.status == "pending", order.reason

    frozen = buy_freeze_amount(95.0, 1000)
    cash_after_freeze = _cash(engine)
    assert cash_after_freeze == pytest.approx(1_000_000.0 - frozen, abs=0.01)

    # 价格跌到 94 ⇒ 触发撮合，按 94 成交
    quotes["600519"].price = 94.0
    left = asyncio.run(engine.match_pending())
    assert left == 0

    cost = buy_freeze_amount(94.0, 1000)
    cash_after_fill = _cash(engine)

    # 第一断言（绝对结果）：只扣实付
    assert cash_after_fill == pytest.approx(1_000_000.0 - cost, abs=0.01), (
        f"挂单成交后余额应为「初始 − 实付 {cost:.2f}」，实际 {cash_after_fill:.2f}"
    )
    # 第二断言（方向性，钉住历史缺陷形态）：绝不能变成「冻结 + 实付」双扣
    double_deduct = 1_000_000.0 - frozen - cost
    assert abs(cash_after_fill - double_deduct) > 1.0, "仍表现为重复扣款"


def test_large_pending_buy_is_not_rejected_at_fill_time():
    """同源症状：冻结额被当成已花掉的钱，导致买得起的委托在成交时被拒。"""
    _reset()
    quotes = {"600519": _Quotes(price=100.0)}
    engine = _make(quotes)
    _set_cash(engine, 200_000.0)

    # 冻结 190,045 左右 ≈ 账户的 95%，下单时校验能过
    order = asyncio.run(engine.place_order("600519", "buy", 95.0, 1900))
    assert order.status == "pending", order.reason

    quotes["600519"].price = 95.0  # 限价到位
    asyncio.run(engine.match_pending())

    with engine._sf() as db:
        from app.models.paper import PaperOrder

        row = db.query(PaperOrder).filter(PaperOrder.id == order.id).one()
        assert row.status == "filled", (
            f"冻结已覆盖全额，成交不该因「可用资金不足」被拒：{row.reason}"
        )


# ---------- 撤单/未成交：冻结额必须原额归还 ----------


def test_pending_buy_cancel_refunds_freeze_exactly():
    _reset()
    quotes = {"600519": _Quotes(price=100.0)}
    engine = _make(quotes)

    order = asyncio.run(engine.place_order("600519", "buy", 95.0, 1000))
    assert order.status == "pending", order.reason
    assert _cash(engine) < 1_000_000.0

    cancelled = engine.cancel(order.id)
    assert cancelled is not None and cancelled.status == "cancelled"
    assert _cash(engine) == pytest.approx(1_000_000.0, abs=0.01), "撤单后资金须回到初始值"


def test_pending_sell_does_not_touch_cash_before_fill():
    """卖出挂单不涉及资金冻结——成交前现金不得变动（防「顺手加个冻结」）。"""
    _reset()
    quotes = {"600519": _Quotes(price=100.0)}
    engine = _make(quotes)

    asyncio.run(engine.place_order("600519", "buy", 100.0, 1000))  # 先建仓（当日冻结）
    with engine._sf() as db:
        from app.models.paper import PaperPosition

        pos = db.query(PaperPosition).filter(PaperPosition.symbol == "600519").one()
        pos.frozen_today = 0  # 模拟已过 T+1
        db.commit()

    before = _cash(engine)
    sell = asyncio.run(engine.place_order("600519", "sell", 105.0, 1000))  # 高于现价 ⇒ 挂单
    assert sell.status == "pending", sell.reason
    assert _cash(engine) == pytest.approx(before, abs=0.01)
