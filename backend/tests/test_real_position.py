"""真实持仓聚合测试（CONTEXT.md: Real Position 域）。

核心契约：
- 记账必须用实际成交价（fill_price），绝不取行情现价——这是需求 1 的命门
- 卖出按摊薄成本结转，已实现盈亏单独累计
- 覆盖层生效时以覆盖值为准（「持仓金额可手动修改」）
- 清仓且无覆盖的标的不出现在持仓视图
"""

from __future__ import annotations

from app.models.real_position import RealPositionOverride, RealTrade
from app.models.watchlist import Base as _B
from app.services.real_position_service import aggregate_positions, load_positions


def _trade(symbol: str, side: str, fill_price: float, quantity: int, fee: float = 0.0, traded_at: str = "2026-08-31", name: str | None = None) -> RealTrade:
    return RealTrade(symbol=symbol, name=name, side=side, fill_price=fill_price, quantity=quantity, fee=fee, traded_at=traded_at)


# ---------------------------------------------------------------- 聚合纯函数


def test_two_buys_weighted_cost_uses_fill_price_not_market():
    """需求 1 命门：18.479 买入 1000 股 + 19.0 买入 500 股 → 加权摊薄成本
    = (18.479×1000 + 19.0×500) / 1500。两笔 fill_price 都必须被尊重。"""
    positions = aggregate_positions(
        [_trade("603118", "buy", 18.479, 1000, name="共进股份"), _trade("603118", "buy", 19.0, 500)],
        {},
    )
    assert len(positions) == 1
    p = positions[0]
    assert p.quantity == 1500
    expected = round((18.479 * 1000 + 19.0 * 500) / 1500, 4)
    assert p.avg_cost == expected
    assert p.name == "共进股份"
    assert p.overridden is False


def test_buy_fee_is_included_in_cost():
    positions = aggregate_positions([_trade("603118", "buy", 18.479, 1000, fee=5.0)], {})
    assert positions[0].cost_total == round(18.479 * 1000 + 5.0, 2)


def test_sell_reduces_quantity_and_carries_cost():
    """卖出：数量相减、剩余成本按摊薄结转、已实现盈亏 = (卖价−摊薄)×数量 − 费用。"""
    positions = aggregate_positions(
        [
            _trade("603118", "buy", 10.0, 1000),
            _trade("603118", "sell", 12.0, 400, fee=4.0),
        ],
        {},
    )
    p = positions[0]
    assert p.quantity == 600
    assert p.avg_cost == 10.0  # 摊薄成本不变（结转）
    assert p.cost_total == 6000.0
    assert p.realized_pnl == (12.0 - 10.0) * 400 - 4.0


def test_sell_more_than_held_truncates_keeps_realized():
    """卖出超过持有：按可卖数量截断、不产生负持仓；清仓标的不消失，
    已实现盈亏保留（卖了的钱不能消失，由 API 分池展示）。"""
    positions = aggregate_positions(
        [_trade("603118", "buy", 10.0, 100), _trade("603118", "sell", 12.0, 300)],
        {},
    )
    p = positions[0]
    assert p.quantity == 0
    assert p.realized_pnl == (12.0 - 10.0) * 100
    assert p.cost_total == 0.0


def test_cleared_position_kept_with_realized_pnl():
    """清仓标的保留在聚合输出（已实现盈亏可见），排序时垫底由 API 分池。"""
    positions = aggregate_positions(
        [
            _trade("603118", "buy", 10.0, 100),
            _trade("603118", "sell", 12.0, 100),
            _trade("600519", "buy", 1000.0, 10),
        ],
        {},
    )
    assert [p.symbol for p in positions] == ["600519", "603118"]  # 持仓在前，清仓垫底
    assert positions[1].quantity == 0 and positions[1].realized_pnl == 200.0


def test_override_takes_precedence():
    """覆盖生效：数量/总成本/已实现盈亏以覆盖为准（「持仓金额可手动修改」）。"""
    trades = [_trade("603118", "buy", 10.0, 1000), _trade("603118", "sell", 12.0, 400)]
    overrides = {"603118": RealPositionOverride(symbol="603118", quantity=700, total_cost=6500.0, realized_pnl=1234.5)}
    p = aggregate_positions(trades, overrides)[0]
    assert p.overridden is True
    assert p.quantity == 700
    assert p.cost_total == 6500.0
    assert p.avg_cost == round(6500.0 / 700, 4)
    assert p.realized_pnl == 1234.5


def test_ordering_by_traded_date_not_input_order():
    """按交易日回放：后录入的更早成交也能正确参与加权（补录场景）。"""
    positions = aggregate_positions(
        [
            _trade("603118", "buy", 20.0, 100, traded_at="2026-09-01"),
            _trade("603118", "buy", 10.0, 100, traded_at="2026-08-01"),
        ],
        {},
    )
    assert positions[0].quantity == 200
    assert positions[0].avg_cost == 15.0


# ---------------------------------------------------------------- 服务层（独立内存库）


def test_load_positions_roundtrip():
    """服务层往返：独立内存库，不碰真实数据库（测试纪律）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    eng = create_engine("sqlite:///:memory:")
    _B.metadata.create_all(eng)
    sf = sessionmaker(bind=eng, expire_on_commit=False)
    with sf() as db:
        db.add(_trade("603118", "buy", 18.479, 1000, name="共进股份"))
        db.commit()
    positions = load_positions(sf)
    p = next(x for x in positions if x.symbol == "603118")
    assert p.quantity == 1000 and p.avg_cost == 18.479
