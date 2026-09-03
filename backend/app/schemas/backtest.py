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
    metrics_extra: dict = Field(default_factory=dict)  # performance 28 项全量
    config: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class WalkForwardWindow(BaseModel):
    """单窗摘要：样本内选参 + 样本外验证。"""

    train_len: int
    test_len: int
    test_start_ts: str
    test_end_ts: str
    best_params: dict
    objective_train: float
    train_metrics: dict = Field(default_factory=dict)
    test_metrics: dict = Field(default_factory=dict)


class WalkForwardPayload(BaseModel):
    """POST /backtest/walkforward 的 data。"""

    symbol: str
    strategy_id: str
    objective: str
    windows: list[WalkForwardWindow] = Field(default_factory=list)
    oos_metrics: dict = Field(default_factory=dict)
    param_stability: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
