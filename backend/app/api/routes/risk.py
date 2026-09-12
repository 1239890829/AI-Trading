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

    # 取价/账户/持仓组装口径收口到 `paper.risk_check_context`（§6.5b #2）：
    # 撮合层的风控硬拦截与这里的预检**必须同一口径**，否则会出现
    # 「UI 预检说可以、下单被拒」。历史教训：早期用 hub.quotes（仅已订阅标的）
    # 且回退「订单价」，把总仓位严重低估。
    account, positions = paper.risk_check_context(hub)

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
