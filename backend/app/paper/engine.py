"""模拟交易撮合引擎（Phase 6 slice 1）。

A股规则落地（full.md §14/§2.4，配置化）：
- T+1：当日买入 frozen，之后首个交易日解冻（官方交易日历，回退自然日）
- 涨停无法买入 / 跌停无法卖出（涨跌停价来自行情源）
- 停牌（无价格）拒绝；买入数量须为 100 股整数倍；资金/可卖数量校验
- 限价撮合：买价 ≥ 现价 按现价成交，否则挂单轮询；卖反向
- 费用：佣金(万2.5, 最低5元) + 卖出印花税(0.05%) + 过户费(0.001%, 双边)；滑点暂为 0（配置化保留）
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models.paper import PaperAccount, PaperOrder, PaperPosition

log = logging.getLogger(__name__)

FEE = {
    "commission_rate": 0.00025,  # 佣金万2.5
    "commission_min": 5.0,
    "stamp_tax": 0.0005,  # 印花税（卖出）
    "transfer_fee": 0.00001,  # 过户费万0.1（双边）
    "slippage": 0.0,
}


def calc_fee(side: str, price: float, qty: int) -> float:
    commission = max(price * qty * FEE["commission_rate"], FEE["commission_min"])
    fee = commission + price * qty * FEE["transfer_fee"]  # 过户费双边
    if side == "sell":
        fee += price * qty * FEE["stamp_tax"]
    return round(fee + price * qty * FEE["slippage"], 2)


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")


class PaperTradingEngine:
    def __init__(self, session_factory, quote_fn, trading_days_fn=None, *, scope: str = "main"):
        self._sf = session_factory
        self._quote_fn = quote_fn  # async (symbol) -> Quote | None（走实时链）
        self._tdays_fn = trading_days_fn  # async () -> list[str] | None
        #: 账户域：main=交易页签；shadow=每日精选影子持仓（数据隔离，互不可见）
        self.scope = scope

    # ---------- 账户 ----------

    def ensure_account(self) -> PaperAccount:
        with self._sf() as db:
            acc = db.query(PaperAccount).filter(PaperAccount.scope == self.scope).first()
            if acc is None:
                acc = PaperAccount(scope=self.scope, cash=1_000_000.0, initial_cash=1_000_000.0)
                db.add(acc)
                db.commit()
                db.refresh(acc)
            return acc

    # ---------- 重置 ----------

    def reset(self, initial_cash: float | None = None, *, source: str = "engine") -> PaperAccount:
        """清空全部持仓与委托，账户资金回到初始额度。

        硬约束：仅重置模拟账户，不触碰任何外部接口；重置后账单历史一并清空，
        因为成交记录挂靠在订单表上，保留订单会导致持仓与历史不一致。

        审计：重置是破坏性操作且曾出现过"账户莫名被清空、无法定位来源"的情况，
        因此必须单行记录 **重置前状态**（持仓数/委托数/资金）+ 触发来源，
        而不是只记结果——事后定位全靠这条。
        """
        with self._sf() as db:
            pos_before = db.query(PaperPosition).filter(PaperPosition.scope == self.scope).count()
            ord_before = db.query(PaperOrder).filter(PaperOrder.scope == self.scope).count()
            old = db.query(PaperAccount).filter(PaperAccount.scope == self.scope).first()
            cash_before = old.cash if old else None
            initial_before = old.initial_cash if old else None

            db.query(PaperPosition).filter(PaperPosition.scope == self.scope).delete()
            db.query(PaperOrder).filter(PaperOrder.scope == self.scope).delete()
            acc = db.query(PaperAccount).filter(PaperAccount.scope == self.scope).first()
            if acc is None:
                acc = PaperAccount(scope=self.scope, cash=1_000_000.0, initial_cash=1_000_000.0)
            if initial_cash is not None and initial_cash > 0:
                acc.initial_cash = float(initial_cash)
            acc.cash = acc.initial_cash
            acc.updated_at = utcnow()
            db.add(acc)
            db.commit()
            db.refresh(acc)
            log.info(
                "paper account RESET audit: source=%s custom_initial=%s | "
                "before: positions=%d orders=%d cash=%s initial=%s | after: cash=%s initial=%s",
                source, initial_cash is not None,
                pos_before, ord_before, cash_before, initial_before,
                acc.cash, acc.initial_cash,
            )
            return acc

    # ---------- T+1 解冻 ----------

    async def _unfreeze(self, db: Session) -> None:
        positions = db.query(PaperPosition).filter(PaperPosition.frozen_today > 0).all()
        if not positions:
            return
        next_day = await self._next_trading_day()
        today = _today()
        for pos in positions:
            if next_day is not None:
                if today >= next_day:
                    pos.frozen_today = 0
            elif pos.buy_date and today > pos.buy_date:
                pos.frozen_today = 0

    async def _next_trading_day(self) -> str | None:
        if self._tdays_fn is None:
            return None
        try:
            days = await self._tdays_fn()
            today = _today()
            future = [d for d in days if d > today]
            return future[0] if future else None
        except Exception:
            return None

    # ---------- 下单 ----------

    async def place_order(self, symbol: str, side: str, price: float, qty: int) -> PaperOrder:
        acc = self.ensure_account()
        quote = await self._quote_fn(symbol)
        order = PaperOrder(scope=self.scope, symbol=symbol, side=side, price=price, quantity=qty, status="pending")

        def reject(reason: str) -> PaperOrder:
            order.status = "rejected"
            order.reason = reason
            return order

        if not symbol.isdigit() or len(symbol) != 6:
            return reject("非法代码")
        if side not in {"buy", "sell"}:
            return reject("非法方向")
        if price is None or price <= 0:
            return reject("非法价格")
        if qty <= 0:
            return reject("非法数量")
        if quote is None or quote.price is None or quote.price <= 0:
            return reject("停牌或无行情，无法交易")
        if side == "buy" and qty % 100 != 0:
            return reject("买入数量须为100股整数倍")
        if quote.limit_up_price and side == "buy" and price >= quote.limit_up_price:
            return reject(f"涨停价 {quote.limit_up_price}，无法买入")
        if quote.limit_down_price and side == "sell" and price <= quote.limit_down_price:
            return reject(f"跌停价 {quote.limit_down_price}，无法卖出")

        with self._sf() as db:
            await self._unfreeze(db)
            if side == "buy":
                need = price * qty + calc_fee("buy", price, qty)
                if need > acc.cash:
                    return reject(f"可用资金不足（需 {need:.0f}，余 {acc.cash:.0f}）")
                # 限价撮合：买价 >= 现价 → 按现价成交；否则挂单
                if price >= quote.price:
                    return await self._fill(db, acc, order, quote.price, qty)
                acc.cash -= need  # 挂单冻结
                db.add(order)
                db.add(acc)
                db.commit()
                db.refresh(order)
                return order
            # sell
            pos = db.query(PaperPosition).filter(PaperPosition.symbol == symbol).one_or_none()
            if pos is None or pos.available < qty:
                avail = pos.available if pos else 0
                return reject(f"可卖数量不足（{avail}）")
            if price <= quote.price:
                return await self._fill_sell(db, acc, order, quote.price, qty)
            db.add(order)
            db.commit()
            db.refresh(order)
            return order

    # ---------- 成交（买） ----------

    async def _fill(self, db: Session, acc: PaperAccount, order: PaperOrder, fill_price: float, qty: int) -> PaperOrder:
        fee = calc_fee("buy", fill_price, qty)
        cost = fill_price * qty + fee
        if cost > acc.cash:
            order.status = "rejected"
            order.reason = "可用资金不足（含费用）"
            db.add(order)
            db.commit()
            return order
        acc.cash -= cost
        pos = db.query(PaperPosition).filter(
            PaperPosition.scope == self.scope, PaperPosition.symbol == order.symbol
        ).one_or_none()
        today = _today()
        if pos is None:
            pos = PaperPosition(scope=self.scope, symbol=order.symbol, quantity=0, frozen_today=0, cost_price=0, buy_date=today)
            db.add(pos)
        total_cost = pos.cost_price * pos.quantity + fill_price * qty
        pos.quantity += qty
        pos.cost_price = round(total_cost / pos.quantity, 4)
        pos.frozen_today += qty  # T+1
        pos.buy_date = today
        order.status = "filled"
        order.filled_price = fill_price
        order.fee = fee
        db.add(acc)
        db.add(pos)
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    # ---------- 成交（卖） ----------

    async def _fill_sell(self, db: Session, acc: PaperAccount, order: PaperOrder, fill_price: float, qty: int) -> PaperOrder:
        pos = db.query(PaperPosition).filter(PaperPosition.symbol == order.symbol).one_or_none()
        fee = calc_fee("sell", fill_price, qty)
        proceeds = fill_price * qty - fee
        acc.cash += proceeds
        pos.quantity -= qty
        if pos.quantity <= 0:
            db.delete(pos)
        order.status = "filled"
        order.filled_price = fill_price
        order.fee = fee
        db.add(acc)
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    # ---------- 挂单轮询撮合 ----------

    async def match_pending(self) -> int:
        """对全部挂单重试撮合（价格到位即成交）。返回**剩余挂单数**（技术债 #5：
        调用方据此自适应降频——无挂单时空转降频，有挂单才密集轮询）。"""
        with self._sf() as db:
            pending = db.query(PaperOrder).filter(
                PaperOrder.status == "pending", PaperOrder.scope == self.scope
            ).all()
            symbols = {o.symbol for o in pending}
            pending_left = len(pending)
        if not symbols:
            return pending_left
        for sym in symbols:
            try:
                quote = await self._quote_fn(sym)
            except Exception:
                continue
            if quote is None or quote.price is None:
                continue
            with self._sf() as db:
                for o in db.query(PaperOrder).filter(
                    PaperOrder.status == "pending", PaperOrder.scope == self.scope,
                    PaperOrder.symbol == sym,
                ).all():
                    acc = self.ensure_account()
                    if o.side == "buy" and o.price >= quote.price:
                        await self._fill(db, acc, o, quote.price, o.quantity)
                    elif o.side == "sell" and o.price <= quote.price:
                        await self._fill_sell(db, acc, o, quote.price, o.quantity)
        with self._sf() as db:
            pending_left = db.query(PaperOrder).filter(
                PaperOrder.status == "pending", PaperOrder.scope == self.scope
            ).count()
        return pending_left

    # ---------- 撤单 ----------

    def cancel(self, order_id: int) -> PaperOrder | None:
        with self._sf() as db:
            o = db.query(PaperOrder).filter(
                PaperOrder.id == order_id, PaperOrder.status == "pending",
                PaperOrder.scope == self.scope,
            ).one_or_none()
            if o is None:
                return None
            o.status = "cancelled"
            if o.side == "buy":  # 解冻资金（含预扣佣金）
                acc = self.ensure_account()
                acc.cash += o.price * o.quantity + calc_fee("buy", o.price, o.quantity)
                db.add(acc)
            db.add(o)
            db.commit()
            db.refresh(o)
            return o

    # ---------- 持仓快照（含市值盈亏） ----------

    def positions_with_pnl(self, price_map: dict[str, float]) -> list[dict]:
        with self._sf() as db:
            positions = db.query(PaperPosition).filter(PaperPosition.scope == self.scope).all()
            out = []
            for pos in positions:
                last = price_map.get(pos.symbol)
                pnl = None if last is None else round((last - pos.cost_price) * pos.quantity, 2)
                pnl_pct = None if (last is None or pos.cost_price == 0) else round((last - pos.cost_price) / pos.cost_price * 100, 2)
                out.append({
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "available": pos.available,
                    "cost_price": pos.cost_price,
                    "last_price": last,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                })
            return out

    def account_summary(self, market_value: float) -> dict:
        acc = self.ensure_account()
        total = acc.cash + market_value
        return {
            "cash": round(acc.cash, 2),
            "market_value": round(market_value, 2),
            "total": round(total, 2),
            # 回撤保护依赖此项：缺它时风控会把 initial 退回 equity，pnl 恒为 0、保护永不触发
            "initial_cash": round(acc.initial_cash, 2),
            "total_pnl": round(total - acc.initial_cash, 2),
            "total_pnl_pct": round((total - acc.initial_cash) / acc.initial_cash * 100, 2),
        }
