from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.schemas.envelope import Envelope
from app.schemas.risk import OrderCheckRequest, OrderCheckResponse, RiskStatePayload

router = APIRouter(tags=["risk"])


def get_risk_engine(request: Request):
    return request.app.state.risk_engine


@router.get("/risk/state")
async def risk_state(engine=Depends(get_risk_engine)) -> Envelope[RiskStatePayload]:
    """当前市场状态与仓位建议参数。"""
    await engine.refresh()
    return Envelope(data=RiskStatePayload(**engine.state_payload()))


@router.post("/risk/check-order")
async def check_order(
    body: OrderCheckRequest,
    request: Request,
    engine=Depends(get_risk_engine),
) -> Envelope[OrderCheckResponse]:
    """对拟提交订单做风险预检（不实际下单）。"""
    paper = request.app.state.paper
    hub = request.app.state.hub

    # 取价口径必须与 /paper/account 一致：优先 hub 全量报价，缺失时回退成本价。
    # 早期版本用 hub.quotes（仅已订阅标的）且回退到「订单价」，会把总仓位严重低估。
    price_map = {q.symbol: q.price for q in hub.get_quotes() if q.price}
    positions = paper.positions_with_pnl(price_map)
    for pos in positions:
        if pos["last_price"] is None and pos["symbol"] in price_map:
            last = price_map[pos["symbol"]]
            pos["last_price"] = last
            pos["pnl"] = round((last - pos["cost_price"]) * pos["quantity"], 2)
            pos["pnl_pct"] = (
                round((last - pos["cost_price"]) / pos["cost_price"] * 100, 2)
                if pos["cost_price"]
                else None
            )
    market_value = sum(
        (p.get("last_price") or p.get("cost_price") or 0) * p.get("quantity", 0)
        for p in positions
    )
    account = paper.account_summary(market_value)
    account["total_equity"] = account["total"]

    quote = hub.quotes.get(body.symbol)
    quote_dict = quote.model_dump() if quote else None

    result = engine.check_order(
        symbol=body.symbol,
        side=body.side,
        price=body.price,
        quantity=body.quantity,
        account=account,
        positions=positions,
        quote=quote_dict,
    )
    return Envelope(data=OrderCheckResponse(**result))
