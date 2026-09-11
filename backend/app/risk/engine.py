"""风险引擎：市场状态 + 仓位参数 + 订单预检。

位置：信号/策略输出 与 模拟撮合 之间，所有订单建议先经过 check_order。
当前为 v1：规则拦截为主；未来可接入 Auditor 与 AI 结论有效期。
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime

from app.core.db import utcnow
from app.risk.config import PositionParams, get_params
from app.risk.state_classifier import classify_market_state
from app.services.market_context import CalendarUnavailable, compute_market_sentiment

log = logging.getLogger(__name__)


def _fmt_pct(pct: float) -> str:
    return f"{pct * 100:.0f}%"


class RiskEngine:
    """持仓、订单、市场状态三位一体的风险预检。"""

    def __init__(self, hub, snapshot_service, session_factory, alert_engine=None, app_state=None):
        self.hub = hub
        self.snapshot_service = snapshot_service
        self._session_factory = session_factory
        self.alert_engine = alert_engine
        # P1-3：持有进程级单例（app.state）时，情绪判定走全站共享 60s 槽，
        # 与市场页/题材/猎场/事件排序共用一次计算。为 None（单测直接构造）时
        # 退回直算——不为了缓存而给单测塞一个假 state。
        self._app_state = app_state
        self._state: str = "数据不足"
        self._reasons: list[str] = []
        self._params: PositionParams = get_params("数据不足")
        self._updated_at: datetime | None = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def reasons(self) -> list[str]:
        return self._reasons

    @property
    def params(self) -> PositionParams:
        return self._params

    async def _sentiment(self) -> dict:
        """情绪判定：有 app.state 走全站共享槽，否则直算（单测/脚本场景）。"""
        if self._app_state is None:
            return await compute_market_sentiment(self.hub, self.snapshot_service)
        from app.services.market_context import get_cached_sentiment

        return await get_cached_sentiment(self._app_state, self.hub)

    async def refresh(self) -> None:
        """刷新市场状态。失败时保留上一状态或 fallback 到数据不足。

        P1-3：情绪判定经共享 60s 缓存槽（`get_cached_sentiment`）。此前每次
        refresh 都全量重算（全市场宽度 + 两天涨停池 + 炸板池），而盘内本方法
        60s 跑一次、市场页也在算同一件事——同一个值算了 N 遍。代价是判定最多
        滞后 60s：这与全站其余消费方看到的是**同一个**值，一致性反而更强
        （此前风控与市场页可能给出不同相位，因为各自取了不同时刻的快照）。
        """
        try:
            breadth = self.snapshot_service.breadth
            sentiment = await self._sentiment()
            state, reasons = classify_market_state(self.hub.indices, breadth, sentiment)
        except CalendarUnavailable as exc:
            state, reasons = "数据不足", [f"日历/数据未就绪：{exc}"]
        except Exception as exc:
            log.warning("risk state refresh failed: %s", exc)
            state, reasons = self._state or "数据不足", self._reasons + [f"刷新失败：{exc}"]

        self._state = state
        self._reasons = reasons
        self._params = get_params(state)
        self._updated_at = utcnow()

    def state_payload(self) -> dict:
        return {
            "state": self._state,
            "reasons": self._reasons,
            "params": asdict(self._params),
            "updated_at": self._updated_at.isoformat() if self._updated_at else None,
        }

    def check_order(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: int,
        account: dict,
        positions: list[dict],
        quote: dict | None = None,
    ) -> dict:
        """订单预检。返回 {allowed: bool, max_qty: int, reasons: list[str], warnings: list[str]}。"""
        reasons: list[str] = []
        warnings: list[str] = []
        allowed = True

        equity = account.get("total_equity") or account.get("total") or account.get("cash", 0)
        cash = account.get("cash", 0)
        order_value = price * quantity

        def _lot(value: float) -> int:
            """金额折算为可下单的整手股数（向下取整到 100 股，永不为负）。"""
            if price <= 0 or value <= 0:
                return 0
            return int(value / price / 100) * 100

        def _value(p: dict) -> float:
            """持仓市值。缺实时价时回退成本价——绝不能回退到「本次订单价」，
            否则未订阅行情的持仓会被按订单价计价，总仓位被严重低估、风控形同虚设。"""
            unit = p.get("last_price") or p.get("cost_price") or 0
            return p.get("quantity", 0) * unit

        held = [p for p in positions if p.get("symbol") == symbol]
        held_qty = sum(p.get("quantity", 0) for p in held)
        held_available = sum(p.get("available", p.get("quantity", 0)) for p in held)

        # 1. 数据质量
        if quote:
            quality = quote.get("quality")
            if quality in ("stale", "invalid", "low"):
                allowed = False
                reasons.append(f"数据质量={quality}，禁止据此下单")

        # 2. 市场状态限制
        if self._state in ("下跌趋势", "恐慌/极端波动") and side == "buy":
            allowed = False
            reasons.append(f"市场状态「{self._state}」禁止新开买入")
        elif self._state == "震荡偏空" and side == "buy":
            warnings.append("市场状态偏空，买入需额外谨慎")

        # 3. 单票仓位上限
        position_value = held_qty * price  # 本单标的按订单价计价
        new_position_value = position_value + (order_value if side == "buy" else -order_value)
        max_by_single = _lot(equity * self._params.single_stock_max_pct - position_value)
        if side == "buy" and equity > 0 and new_position_value / equity > self._params.single_stock_max_pct:
            allowed = False
            reasons.append(
                f"单票仓位上限 {_fmt_pct(self._params.single_stock_max_pct)}："
                f"{symbol} 买入后约占 {_fmt_pct(new_position_value / equity)}"
                f"（现持仓 {position_value:,.0f}），本次最多可再买 {max_by_single} 股"
            )

        # 4. 总仓位上限
        total_position_value = sum(_value(p) for p in positions)
        after_total = total_position_value + (order_value if side == "buy" else -order_value)
        max_by_total = _lot(equity * self._params.total_position_max_pct - total_position_value)
        if side == "buy" and equity > 0 and after_total / equity > self._params.total_position_max_pct:
            allowed = False
            reasons.append(
                f"总仓位上限 {_fmt_pct(self._params.total_position_max_pct)}："
                f"买入后约 {_fmt_pct(after_total / equity)}"
                f"（当前 {_fmt_pct(total_position_value / equity)}），"
                f"本次最多可再买 {max_by_total} 股"
            )

        # 5. 回撤保护（只减不加）
        initial = account.get("initial_cash")
        if not initial:
            # 缺 initial_cash 时静默回退 equity 会让保护恒不触发，必须显式暴露
            warnings.append("账户缺少 initial_cash，回撤保护未生效")
        elif side == "buy":
            pnl_pct = (equity - initial) / initial
            if pnl_pct < self._params.drawdown_protection_pct:
                allowed = False
                reasons.append(
                    f"组合回撤 {pnl_pct:.2%} 已超过保护线 {self._params.drawdown_protection_pct:.2%}，只减不加"
                )

        # 6. 现金不足
        if side == "buy" and order_value > cash:
            allowed = False
            reasons.append(f"可用现金 {cash:,.2f} 不足，本次需 {order_value:,.2f}")

        # 7. 流动性提示
        if quote and side == "buy":
            amount = quote.get("amount") or 0
            if amount < 50_000_000:  # 小于 5000 万成交额
                warnings.append("标的成交额偏低，注意流动性风险")

        if allowed and not reasons:
            reasons.append("通过预检")

        # max_qty 语义统一为「本次最多可下单股数」，与是否放行无关：
        # 买入受单票上限 / 总仓位 / 现金三者共同约束；卖出受可卖数量（T+1）约束。
        if side == "buy":
            max_qty = min(max_by_single, max_by_total, _lot(cash))
        else:
            max_qty = held_available

        return {
            "allowed": allowed,
            "max_qty": max_qty,
            "reasons": reasons,
            "warnings": warnings,
            "state": self._state,
        }
