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
    config: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
