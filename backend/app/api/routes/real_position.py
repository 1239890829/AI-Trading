"""真实持仓 API（CONTEXT.md: Real Position 域；与模拟账户 /api/paper/* 完全独立）。

- GET  /api/real/positions           持仓视图（流水聚合 + 覆盖 + 行情补现价与当日涨跌）
- POST /api/real/trades              记一笔买入/卖出（按实际成交价，绝不按现价）
- DELETE /api/real/trades/{id}       删除流水（修正历史：删了重录）
- PATCH /api/real/positions/{symbol} 手动覆盖持仓数量/总成本（「持仓金额可手动修改」）
- DELETE /api/real/positions/{symbol} 整只删除（清掉该标的全部流水与覆盖）
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_hub
from app.core.db import get_session_factory
from app.services.quote_hub import QuoteHub
from app.services.real_position_service import load_positions

router = APIRouter(prefix="/real", tags=["real-position"])


def _sf():
    # 返回 Session 实例（支持 with 自动 close）；sessionmaker 本身不支持 with（AGENTS.md 6.3）。
    # 注意 service.load_positions 期望「factory 可调用」，那里继续传 get_session_factory()。
    return get_session_factory()()


class TradeIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    name: str | None = None
    side: str = Field(pattern="^(buy|sell)$")
    fill_price: float = Field(gt=0)
    quantity: int = Field(gt=0)
    fee: float = Field(default=0.0, ge=0)
    traded_at: str = Field(default="", max_length=10)
    note: str | None = Field(default=None, max_length=256)


class OverrideIn(BaseModel):
    quantity: int = Field(gt=0)
    total_cost: float = Field(gt=0)
    realized_pnl: float = Field(default=0.0)


async def _quotes_for(hub: QuoteHub, symbols: list[str]) -> dict[str, Any]:
    """行情补现价（2026-09-07 R3 收口：实现单点在 quote_enrich.fetch_quotes_batched）。"""
    from app.services.quote_enrich import fetch_quotes_batched

    return await fetch_quotes_batched(hub, symbols, prefer_cache=True)


@router.get("/positions")
async def list_positions(hub: QuoteHub = Depends(get_hub)) -> dict:
    """持仓视图。字段口径：

    - avg_cost 摊薄成本（含买入费用；被覆盖时以覆盖值为准，overridden=true）
    - last_price 实时价，行情缺失回退成本价（不虚构现价）
    - market_value/unrealized_pnl/unrealized_pct 市值与浮动盈亏
    - realized_pnl 已实现盈亏（卖出流水累计）
    """
    from app.data_quality.validator import validate_quote

    positions = load_positions(_sf)
    quotes = await _quotes_for(hub, [p.symbol for p in positions]) if positions else {}
    items = []
    cleared = []
    for p in positions:
        q = quotes.get(p.symbol)
        last_price = None
        day_change_pct = None
        if q is not None:
            try:
                qv = validate_quote(q)
            except Exception:
                qv = q
            last_price = qv.price
            day_change_pct = qv.change_pct
        if last_price is None:
            last_price = p.avg_cost  # 缺行情回退成本价（既有口径，不虚构现价）
        market_value = round(last_price * p.quantity, 2) if last_price is not None else None
        unrealized = round(market_value - p.cost_total, 2) if market_value is not None else None
        unrealized_pct = (
            round(unrealized / p.cost_total * 100, 2) if unrealized is not None and p.cost_total > 0 else None
        )
        row = {
            "symbol": p.symbol,
            "name": p.name,
            "quantity": p.quantity,
            "avg_cost": p.avg_cost,
            "cost_total": p.cost_total,
            "last_price": last_price,
            "day_change_pct": day_change_pct,
            "market_value": market_value,
            "unrealized_pnl": unrealized,
            "unrealized_pct": unrealized_pct,
            "realized_pnl": p.realized_pnl,
            "overridden": p.overridden,
            "trade_count": p.trade_count,
            "last_traded_at": p.last_traded_at,
        }
        if p.quantity > 0 or p.overridden:
            items.append(row)
        else:
            # 已清仓：只留已实现盈亏相关字段，市值类不展示
            cleared.append(
                {
                    "symbol": p.symbol,
                    "name": p.name,
                    "realized_pnl": p.realized_pnl,
                    "trade_count": p.trade_count,
                    "last_traded_at": p.last_traded_at,
                }
            )
    total = {
        "market_value": round(sum(i["market_value"] or 0 for i in items), 2),
        "cost_total": round(sum(i["cost_total"] for i in items), 2),
        "unrealized_pnl": round(sum(i["unrealized_pnl"] or 0 for i in items), 2),
        # 已实现盈亏汇总含已清仓标的（卖了的钱不能消失）
        "realized_pnl": round(sum(i["realized_pnl"] for i in items) + sum(c["realized_pnl"] for c in cleared), 2),
    }
    return {"data": {"items": items, "cleared": cleared, "total": total, "count": len(items)}, "meta": {}}


@router.post("/trades")
async def create_trade(body: TradeIn) -> dict:
    symbol = body.symbol.strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol 不能为空")
    traded_at = body.traded_at or date.today().isoformat()
    with _sf() as db:
        from app.models.real_position import RealTrade

        row = RealTrade(
            symbol=symbol,
            name=body.name,
            side=body.side,
            fill_price=body.fill_price,
            quantity=body.quantity,
            fee=body.fee,
            traded_at=traded_at,
            note=body.note,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"data": {"id": row.id, "symbol": symbol, "side": body.side, "fill_price": body.fill_price, "quantity": body.quantity}, "meta": {}}


@router.delete("/trades/{trade_id}")
async def delete_trade(trade_id: int) -> dict:
    with _sf() as db:
        from sqlalchemy import select

        from app.models.real_position import RealTrade

        row = db.execute(select(RealTrade).where(RealTrade.id == trade_id)).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"流水 {trade_id} 不存在")
        symbol = row.symbol
        db.delete(row)
        # 流水被修正后，该标的的覆盖可能已过时：不自动删覆盖（覆盖是显式人工决定）
        db.commit()
    return {"data": {"deleted": trade_id, "symbol": symbol}, "meta": {}}


@router.patch("/positions/{symbol}")
async def override_position(symbol: str, body: OverrideIn) -> dict:
    with _sf() as db:
        from sqlalchemy import select

        from app.models.real_position import RealPositionOverride

        row = db.execute(select(RealPositionOverride).where(RealPositionOverride.symbol == symbol)).scalar_one_or_none()
        if row is None:
            row = RealPositionOverride(symbol=symbol, quantity=body.quantity, total_cost=body.total_cost, realized_pnl=body.realized_pnl)
            db.add(row)
        else:
            row.quantity = body.quantity
            row.total_cost = body.total_cost
            row.realized_pnl = body.realized_pnl
        db.commit()
    return {"data": {"symbol": symbol, "quantity": body.quantity, "total_cost": body.total_cost, "overridden": True}, "meta": {}}


@router.delete("/positions/{symbol}")
async def delete_position(symbol: str) -> dict:
    """整只删除：清掉该标的全部流水与覆盖（清仓/记错标的时用）。"""
    with _sf() as db:
        from sqlalchemy import delete as sa_delete, select

        from app.models.real_position import RealPositionOverride, RealTrade

        n = len(list(db.execute(select(RealTrade).where(RealTrade.symbol == symbol)).scalars()))
        db.execute(sa_delete(RealTrade).where(RealTrade.symbol == symbol))
        db.execute(sa_delete(RealPositionOverride).where(RealPositionOverride.symbol == symbol))
        db.commit()
    return {"data": {"symbol": symbol, "deleted_trades": n}, "meta": {}}
