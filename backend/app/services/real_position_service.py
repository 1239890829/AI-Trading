"""真实持仓聚合服务（CONTEXT.md: Real Position / Trade Flow / Position View）。

两层模型：real_trade 流水是事实层（按实际成交价记账，不可篡改），
持仓视图 = 流水加权聚合，可被 real_position_override 覆盖。

聚合算法（与同花顺/券商 App 的摊薄成本口径一致）：
- 买入：成本总额 += fill_price × qty + fee；数量累加；摊薄成本 = 成本总额 / 数量
- 卖出：已实现盈亏 += (fill_price − 卖出时点摊薄成本) × qty − fee；
  剩余成本按摊薄成本结转（成本总额 −= 摊薄成本 × qty）
- 卖出数量 > 持有数量：按可卖数量截断（账本自洽优先，不臆造负持仓）
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.models.real_position import RealPositionOverride, RealTrade


@dataclass
class AggregatedPosition:
    symbol: str
    name: str | None
    quantity: int
    avg_cost: float | None
    cost_total: float
    realized_pnl: float
    overridden: bool
    trade_count: int
    last_traded_at: str | None


def aggregate_positions(
    trades: list[RealTrade],
    overrides: dict[str, RealPositionOverride],
) -> list[AggregatedPosition]:
    """流水 → 持仓视图。按 traded_at 升序回放（同日按 id 稳定排序）。"""
    ordered = sorted(trades, key=lambda t: (t.traded_at or "", t.id))
    by_symbol: dict[str, dict] = {}
    for t in ordered:
        st = by_symbol.setdefault(
            t.symbol,
            {"name": None, "qty": 0, "cost_total": 0.0, "realized": 0.0, "count": 0, "last": None},
        )
        st["name"] = t.name or st["name"]
        st["count"] += 1
        st["last"] = t.traded_at or st["last"]
        if t.side == "sell":
            sell_qty = min(t.quantity, st["qty"])
            avg = st["cost_total"] / st["qty"] if st["qty"] > 0 else 0.0
            st["realized"] += (t.fill_price - avg) * sell_qty - (t.fee or 0.0)
            st["cost_total"] -= avg * sell_qty
            st["qty"] -= sell_qty
        else:
            st["cost_total"] += t.fill_price * t.quantity + (t.fee or 0.0)
            st["qty"] += t.quantity

    out: list[AggregatedPosition] = []
    for symbol, st in by_symbol.items():
        avg = round(st["cost_total"] / st["qty"], 4) if st["qty"] > 0 else None
        qty, cost_total, realized = st["qty"], round(st["cost_total"], 2), round(st["realized"], 2)
        overridden = symbol in overrides
        if overridden:
            ov = overrides[symbol]
            qty, cost_total = ov.quantity, ov.total_cost
            realized = ov.realized_pnl
            avg = round(cost_total / qty, 4) if qty > 0 else None
        out.append(
            AggregatedPosition(
                symbol=symbol,
                name=st["name"],
                quantity=qty,
                avg_cost=avg,
                cost_total=cost_total,
                realized_pnl=realized,
                overridden=overridden,
                trade_count=st["count"],
                last_traded_at=st["last"],
            )
        )
    # 注意：清仓（qty=0 且无覆盖）的标的**保留**在输出里——其已实现盈亏仍需可见；
    # 由调用方分池展示（持仓中 / 已清仓），不要在这里丢信息。
    out.sort(key=lambda p: (p.quantity <= 0, p.symbol))
    return out


def load_positions(session_factory) -> list[AggregatedPosition]:
    """查库并聚合。轻查询（流水量级为个人记账），无需缓存。"""
    with session_factory() as db:
        trades = list(db.execute(select(RealTrade).order_by(RealTrade.id)).scalars())
        overrides = {o.symbol: o for o in db.execute(select(RealPositionOverride)).scalars()}
    return aggregate_positions(trades, overrides)
