"""市场状态 → 仓位参数的映射配置。

所有数值为**建议参数**。2026-09-13（§6.5b #2）起 main 账户买入在撮合层
经 `PaperTradingEngine._risk_block_reason` 强制过闸（shadow/卖出豁免）；
此前仅 UI 预检提示。
实盘接入时应根据用户风险偏好再分档。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PositionParams:
    """给定市场状态下的仓位与策略参数。"""

    single_stock_max_pct: float  # 单票占组合市值上限
    total_position_max_pct: float  # 总仓位上限
    strategy_weights: dict[str, float]  # 策略权重提示
    stop_loss_pct: float  # 建议止损幅度
    add_position_limit: str  # 加仓限制描述
    high_position_stock_limit: str  # 高位股限制描述
    drawdown_protection_pct: float  # 组合回撤保护线（只减不加）


# ---------------------------------------------------------------------------
# 状态映射表
# ---------------------------------------------------------------------------

STATE_PARAMS: dict[str, PositionParams] = {
    "强势多头": PositionParams(
        single_stock_max_pct=0.30,
        total_position_max_pct=0.90,
        strategy_weights={"趋势": 0.45, "追涨": 0.30, "低吸": 0.20, "价值": 0.05},
        stop_loss_pct=-0.07,
        add_position_limit="可顺势加仓",
        high_position_stock_limit="允许核心题材龙头集中",
        drawdown_protection_pct=-0.08,
    ),
    "震荡偏多": PositionParams(
        single_stock_max_pct=0.25,
        total_position_max_pct=0.70,
        strategy_weights={"趋势": 0.25, "追涨": 0.15, "低吸": 0.45, "价值": 0.15},
        stop_loss_pct=-0.05,
        add_position_limit="回调低吸加仓，不追高",
        high_position_stock_limit="高位股仓位减半",
        drawdown_protection_pct=-0.06,
    ),
    "震荡": PositionParams(
        single_stock_max_pct=0.20,
        total_position_max_pct=0.50,
        strategy_weights={"趋势": 0.15, "追涨": 0.05, "低吸": 0.50, "价值": 0.30},
        stop_loss_pct=-0.04,
        add_position_limit="不主动加仓，只调仓",
        high_position_stock_limit="回避高位加速",
        drawdown_protection_pct=-0.05,
    ),
    "震荡偏空": PositionParams(
        single_stock_max_pct=0.15,
        total_position_max_pct=0.30,
        strategy_weights={"趋势": 0.05, "追涨": 0.00, "低吸": 0.40, "价值": 0.55},
        stop_loss_pct=-0.04,
        add_position_limit="只减仓或空仓观望",
        high_position_stock_limit="禁止参与高位股",
        drawdown_protection_pct=-0.04,
    ),
    "下跌趋势": PositionParams(
        single_stock_max_pct=0.10,
        total_position_max_pct=0.20,
        strategy_weights={"趋势": 0.00, "追涨": 0.00, "低吸": 0.20, "价值": 0.80},
        stop_loss_pct=-0.03,
        add_position_limit="禁止新开仓",
        high_position_stock_limit="禁止参与高位股",
        drawdown_protection_pct=-0.03,
    ),
    "恐慌/极端波动": PositionParams(
        single_stock_max_pct=0.05,
        total_position_max_pct=0.10,
        strategy_weights={"趋势": 0.00, "追涨": 0.00, "低吸": 0.10, "价值": 0.90},
        stop_loss_pct=-0.03,
        add_position_limit="禁止新开仓，优先止损",
        high_position_stock_limit="禁止参与高位股",
        drawdown_protection_pct=-0.02,
    ),
    "数据不足": PositionParams(
        single_stock_max_pct=0.10,
        total_position_max_pct=0.20,
        strategy_weights={"趋势": 0.10, "追涨": 0.00, "低吸": 0.30, "价值": 0.60},
        stop_loss_pct=-0.04,
        add_position_limit="数据不足，保守开仓",
        high_position_stock_limit="回避高位股",
        drawdown_protection_pct=-0.03,
    ),
}


def get_params(state: str) -> PositionParams:
    return STATE_PARAMS.get(state, STATE_PARAMS["数据不足"])
