"""选股器出参建模（B2 纪律：新端点严格建模）。

评分卡字段由后端 engine 决定、演进节奏可控 → 严格模型（extra 默认 ignore）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ScreenerSignal(BaseModel):
    """单维度信号（与前端 technical-analysis 的 Signal 同构，另含维度得分）。"""

    name: str
    bias: Literal["bull", "bear", "neutral"]
    score: float  # 0-1 维度得分
    detail: str


class ScreenerItem(BaseModel):
    symbol: str
    name: str
    price: float
    change_pct: float
    turnover_rate: float | None = None
    amount_yi: float  # 成交额（亿元）
    float_cap_yi: float | None = None  # 流通市值（亿元）
    score: float  # 0-100
    grade: Literal["A", "B", "C", "D"]
    bias: Literal["bull", "bear", "neutral"]
    signals: list[ScreenerSignal] = Field(default_factory=list)
    summary: str
    fail_conditions: list[str] = Field(default_factory=list)


class ScreenerPayload(BaseModel):
    """GET /screener 的 data。"""

    items: list[ScreenerItem] = Field(default_factory=list)
    scanned: int  # 快照总行数
    filtered: int  # 截面条件过滤后候选数
    scored: int  # 实际拿到日K并评分的候选数
    failed: int = 0  # 日K拉取失败数（跳过并计数，不臆造）
    snapshot_time: str | None = None  # 快照数据时点（ticktime）
    computed_at: str
    cached: bool = False
    scorer_version: str = "v1"
    disclaimers: list[str] = Field(default_factory=list)
