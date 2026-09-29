"""Atomic API action receipts. Existing matching/risk rules remain the executor.

The SQLite outer transaction owns the receipt and all nested engine commits.
Fetch external inputs before BEGIN; inside the transaction callbacks return the
captured inputs without suspension. A crash rolls back both money and receipt.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.models.paper import PaperActionReceipt
from app.paper.engine import PaperTradingEngine


def _replay(sf, scope, request_id, draft):
    with sf() as db:
        row = db.get(PaperActionReceipt, (scope, request_id))
        if row is None:
            return None
        if row.draft != draft:
            raise HTTPException(409, "请求标识已用于另一份订单，请核对原委托")
        return {**json.loads(row.result), "replayed": True}


def _valid_deadline(expires_at):
    remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
    if not 0 < remaining <= 60:
        raise HTTPException(422, "提交已超期或时钟不一致，请刷新预检后重新确认")


async def submit_action(engine: PaperTradingEngine, body) -> dict:
    draft = body.model_dump_json(exclude={"request_id"})
    request_id = str(body.request_id)
    prior = _replay(engine._sf, engine.scope, request_id, draft)
    if prior is not None:
        return prior
    _valid_deadline(body.expires_at)
    quote = await engine._quote_fn(body.symbol)
    days = None
    if engine._tdays_fn is not None:
        try:
            days = await engine._tdays_fn()
        except Exception:
            # The existing engine owns and reports the calendar fallback.
            days = None

    async def captured_quote(_):
        return quote

    async def captured_days():
        return days

    with engine._sf() as probe:
        bind = probe.get_bind()
    with bind.connect() as connection:
        # Explicit BEGIN prevents SQLite's legacy savepoint auto-commit behavior.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            sf = sessionmaker(bind=connection, expire_on_commit=False,
                              join_transaction_mode="create_savepoint")
            prior = _replay(sf, engine.scope, request_id, draft)
            if prior is not None:
                connection.rollback()
                return prior
            _valid_deadline(body.expires_at)
            executor = PaperTradingEngine(sf, captured_quote, captured_days,
                                          scope=engine.scope, risk_engine=engine._risk_engine)
            order = await executor.place_order(body.symbol, body.side, body.price, body.quantity)
            result = {"replayed": False, "id": order.id, "symbol": order.symbol, "side": order.side,
                      "status": order.status, "filled_price": order.filled_price,
                      "fee": order.fee, "reason": order.reason}
            with sf() as db:
                db.add(PaperActionReceipt(scope=engine.scope, request_id=request_id,
                                         draft=draft, result=json.dumps(result)))
                db.commit()
            connection.commit()
            return result
        except BaseException:
            connection.rollback()
            raise
