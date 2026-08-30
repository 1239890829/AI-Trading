from __future__ import annotations

from pydantic import BaseModel, Field


class OrderCheckRequest(BaseModel):
    symbol: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    side: str = Field(pattern=r"^(buy|sell)$")
    price: float = Field(gt=0)
    quantity: int = Field(gt=0)


class OrderCheckResponse(BaseModel):
    allowed: bool
    max_qty: int
    reasons: list[str]
    warnings: list[str]
    state: str


class PositionParamsModel(BaseModel):
    single_stock_max_pct: float
    total_position_max_pct: float
    strategy_weights: dict[str, float]
    stop_loss_pct: float
    add_position_limit: str
    high_position_stock_limit: str
    drawdown_protection_pct: float


class RiskStatePayload(BaseModel):
    state: str
    reasons: list[str]
    params: PositionParamsModel
    updated_at: str | None = None
