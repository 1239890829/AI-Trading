
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.paper.engine import PaperTradingEngine

router = APIRouter(tags=["paper"])


def _engine(request) -> PaperTradingEngine:
    return request.app.state.paper


class OrderIn(BaseModel):
    symbol: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    side: str = Field(pattern=r"^(buy|sell)$")
    price: float = Field(gt=0)
    quantity: int = Field(gt=0)


async def _positions_with_live(request):
    engine = _engine(request)
    hub = request.app.state.hub
    positions = engine.positions_with_pnl({})
    price_map = {q.symbol: q.price for q in hub.get_quotes() if q.price}
    for pos in positions:
        if pos["last_price"] is None and pos["symbol"] in price_map:
            last = price_map[pos["symbol"]]
            pos["last_price"] = last
            pos["pnl"] = round((last - pos["cost_price"]) * pos["quantity"], 2)
            pos["pnl_pct"] = round((last - pos["cost_price"]) / pos["cost_price"] * 100, 2) if pos["cost_price"] else None
    return engine, positions


@router.get("/paper/account")
async def paper_account(request: Request):
    engine, positions = await _positions_with_live(request)
    mv = sum((p["last_price"] or p["cost_price"]) * p["quantity"] for p in positions)
    return {"data": engine.account_summary(mv)}


@router.get("/paper/positions")
async def paper_positions(request: Request):
    engine, positions = await _positions_with_live(request)
    return {"data": positions}


@router.get("/paper/orders")
async def paper_orders(request: Request, status: str | None = None):
    engine = _engine(request)
    from app.models.paper import PaperOrder

    with engine._sf() as db:
        q = db.query(PaperOrder).order_by(PaperOrder.id.desc()).limit(50)
        if status:
            q = q.filter(PaperOrder.status == status)
        rows = q.all()
        return {"data": [
            {"id": o.id, "symbol": o.symbol, "side": o.side, "price": o.price, "quantity": o.quantity,
             "status": o.status, "filled_price": o.filled_price, "fee": o.fee, "reason": o.reason,
             "created_at": o.created_at.isoformat() if o.created_at else None}
            for o in rows
        ]}


@router.post("/paper/orders")
async def place_order(body: OrderIn, request: Request):
    engine = _engine(request)
    order = await engine.place_order(body.symbol, body.side, body.price, body.quantity)
    if order.status == "rejected":
        raise HTTPException(status_code=422, detail=order.reason)
    return {"data": {"id": order.id, "symbol": order.symbol, "side": order.side, "status": order.status,
                     "filled_price": order.filled_price, "fee": order.fee, "reason": order.reason}}


@router.delete("/paper/orders/{order_id}")
async def cancel_order(order_id: int, request: Request):
    engine = _engine(request)
    o = engine.cancel(order_id)
    if o is None:
        raise HTTPException(status_code=404, detail="挂单不存在或已成交")
    return {"data": {"id": o.id, "status": o.status}}
