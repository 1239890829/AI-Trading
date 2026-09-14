
from __future__ import annotations

import logging
from datetime import timezone

from fastapi import Depends, APIRouter, HTTPException, Request

from app.api.deps import require_write_token
from app.core.bjtime import BJ_TZ
from pydantic import BaseModel, Field

from app.paper.engine import PaperTradingEngine

log = logging.getLogger(__name__)

router = APIRouter(tags=["paper"])


def _engine(request) -> PaperTradingEngine:
    return request.app.state.paper


class OrderIn(BaseModel):
    symbol: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    side: str = Field(pattern=r"^(buy|sell)$")
    # `allow_inf_nan=False`（R05）：`gt=0` 单独用**挡不住 Infinity**
    # （`inf > 0` 成立），JSON 里的 `1e999` 会被解析成 inf 一路算进成本，
    # 把账户余额污染成 inf。NaN 比较恒 False 侥幸被 gt 挡住，但两者都该显式拒。
    price: float = Field(gt=0, allow_inf_nan=False)
    quantity: int = Field(gt=0)


class ResetIn(BaseModel):
    initial_cash: float | None = Field(default=None, gt=0, le=1_000_000_000)


async def _positions_with_live(request):
    engine = _engine(request)
    hub = request.app.state.hub
    # R04（2026-09-14）：读持仓前先结算 T+1。`available` 是 `quantity - frozen_today`
    # 的派生值，结算只发生在下单路径时，界面上的「可卖」会长期停在 0
    # （买入后未再下单的持仓），把一个本可卖出的仓位显示成"当日买入不可卖"。
    # 结算失败**不阻断读**，但必须留痕（不静默）：位置照常返回，只是冻结节可能偏旧；
    # 真正的卖出安全由 `place_order` 内的同一套解冻 + `available` 硬校验兜底。
    try:
        await engine.settle_t1()
    except Exception as exc:  # noqa: BLE001
        log.warning("T+1 结算失败（持仓读快照用旧冻结态）：%s: %s", type(exc).__name__, exc)
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
    """委托列表（**只含本账户域**）。

    两处历史缺陷（R06，2026-09-14 修复）：
    1. 查询未带 `scope` ⇒ main 端点会连 **shadow（每日精选影子账户）** 的委托
       一起返回，前端委托列表与 K 线 B/S 标记会混入研究账户的记录；
    2. `order_by(...).limit(50)` **之后**才 `.filter(status)` ⇒ SQLAlchemy 抛
       `InvalidRequestError`（limit 之后不能再 filter），即「带 status 的查询**必报错**」。
       修法不是把 filter 挪到前面那么简单——顺序必须是
       `filter(scope) → filter(status) → order_by → limit`，否则又回到"先截断再筛"，
       会漏掉符合条件但排在 50 条之后的数据。
    """
    engine = _engine(request)
    from app.models.paper import PaperOrder

    with engine._sf() as db:
        q = db.query(PaperOrder).filter(PaperOrder.scope == engine.scope)
        if status:
            q = q.filter(PaperOrder.status == status)
        rows = q.order_by(PaperOrder.id.desc()).limit(50).all()
        return {"data": [
            {"id": o.id, "symbol": o.symbol, "side": o.side, "price": o.price, "quantity": o.quantity,
             "status": o.status, "filled_price": o.filled_price, "fee": o.fee, "reason": o.reason,
             "created_at": o.created_at.isoformat() if o.created_at else None}
            for o in rows
        ]}


@router.post("/paper/orders", dependencies=[Depends(require_write_token)])
async def place_order(body: OrderIn, request: Request):
    engine = _engine(request)
    order = await engine.place_order(body.symbol, body.side, body.price, body.quantity)
    if order.status == "rejected":
        raise HTTPException(status_code=422, detail=order.reason)
    return {"data": {"id": order.id, "symbol": order.symbol, "side": order.side, "status": order.status,
                     "filled_price": order.filled_price, "fee": order.fee, "reason": order.reason}}


@router.get("/paper/fills")
async def paper_fills(request: Request, symbol: str | None = None):
    """已成交记录（K线 B/S 标记数据源）：date/side/price/quantity。

    R06：原查询**未带 scope** ⇒ main 的成交记录里混入 shadow 的成交，
    图上 B/S 标记会把影子账户的买卖画到真实操作上（口径污染且无从察觉）。
    """
    engine = _engine(request)
    from app.models.paper import PaperOrder

    with engine._sf() as db:
        q = db.query(PaperOrder).filter(
            PaperOrder.scope == engine.scope, PaperOrder.status == "filled"
        )
        if symbol:
            q = q.filter(PaperOrder.symbol == symbol)
        rows = q.order_by(PaperOrder.id.desc()).limit(200).all()
        return {"data": [
            {"symbol": o.symbol,
             "date": (o.created_at.replace(tzinfo=timezone.utc).astimezone(BJ_TZ).strftime("%Y-%m-%d") if o.created_at else ""),
             "side": o.side,
             "price": o.filled_price or o.price,
             "quantity": o.quantity,
             "fee": o.fee}
            for o in rows
        ]}


@router.delete("/paper/orders/{order_id}", dependencies=[Depends(require_write_token)])
async def cancel_order(order_id: int, request: Request):
    engine = _engine(request)
    o = engine.cancel(order_id)
    if o is None:
        raise HTTPException(status_code=404, detail="挂单不存在或已成交")
    return {"data": {"id": o.id, "status": o.status}}


@router.post("/paper/reset", dependencies=[Depends(require_write_token)])
async def reset_account(body: ResetIn, request: Request):
    """重置模拟账户：清仓、清委托与成交历史、资金回到初始额度。

    source="api" 进入审计日志——重置是破坏性操作，事后必须能定位是谁在何时触发。
    """
    engine = _engine(request)
    acc = engine.reset(body.initial_cash, source="api")
    return {"data": {"cash": round(acc.cash, 2), "initial_cash": round(acc.initial_cash, 2),
                     "total": round(acc.cash, 2), "total_pnl": 0.0, "total_pnl_pct": 0.0,
                     "market_value": 0.0}}


@router.get("/paper/reconcile")
async def paper_reconcile(request: Request):
    """模拟账本**只读对账**（`IMP-003` / 报告 F1）。

    把「账本对不对」从「人去翻流水」变成四条可执行的不变量——资金守恒 /
    预留与未决单一致 / scope 隔离 / 成交与持仓变动对照；每条异常都带
    `order_ids`（可追到具体订单）。详见 `app/paper/reconcile.py` 的模块 docstring。

    **只读**：不改余额、不重算、不落库。`ok=false` 只表示**账本不自洽**，
    不表示需要立刻重算——**重算是资金口径变更，须用户拍板**。
    """
    from app.paper.reconcile import reconcile

    engine = _engine(request)
    return {"data": reconcile(engine._sf)}
