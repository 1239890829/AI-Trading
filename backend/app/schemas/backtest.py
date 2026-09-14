"""回测出参建模（B2 纪律：严格模型）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BacktestTrade(BaseModel):
    signal_ts: str
    fill_ts: str
    side: Literal["buy", "sell"]
    price: float
    ref_price: float
    qty: int
    fee: float
    ok: bool
    reason: str = ""
    # R14：True = 引擎按调用方要求**假设**清算的产物（reason=assumed_liquidation），
    # 不计入 n_trades / win_rate 等成交统计；真实成交恒为 False。
    synthetic: bool = False


class BacktestOpenPosition(BaseModel):
    """数据流末仍未平仓的持仓（R14）——**按末价估值，不是成交**。"""

    qty: int
    entry_ts: str
    entry_price: float
    last_ts: str
    mark_price: float
    mark_value: float
    cost: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    liquidated_by: str = ""


class BacktestEquityPoint(BaseModel):
    ts: str
    value: float
    benchmark: float


class BacktestMetrics(BaseModel):
    total_return: float
    benchmark_return: float
    excess_return: float
    annual_return: float
    max_drawdown: float
    max_drawdown_days: int
    sharpe: float
    sortino: float
    calmar: float
    win_rate: float
    profit_loss_ratio: float
    in_return: float
    out_return: float


class BacktestPayload(BaseModel):
    """POST /backtest/run 的 data。"""

    symbol: str
    strategy_id: str
    bars_count: int
    metrics: BacktestMetrics
    equity: list[BacktestEquityPoint] = Field(default_factory=list)
    trades: list[BacktestTrade] = Field(default_factory=list)
    metrics_extra: dict = Field(default_factory=dict)  # performance 28 项全量
    config: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    # R14：区间末持仓（按末价估值）。None = 末段空仓。**不进成交统计**。
    open_position: BacktestOpenPosition | None = None
