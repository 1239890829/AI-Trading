from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AlertRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    condition_type: str = Field(pattern=r"^(price_above|price_below|change_pct_above|change_pct_below)$")
    threshold: float
    symbols: list[str] = Field(default_factory=list)
    scope: str = Field(default="watchlist", pattern=r"^(watchlist|symbols|all)$")
    cooldown_seconds: int = Field(default=300, ge=0)
    channels: list[str] = Field(default_factory=lambda: ["in_app", "log"])
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    condition_type: str | None = Field(default=None, pattern=r"^(price_above|price_below|change_pct_above|change_pct_below)$")
    threshold: float | None = None
    symbols: list[str] | None = None
    scope: str | None = Field(default=None, pattern=r"^(watchlist|symbols|all)$")
    cooldown_seconds: int | None = Field(default=None, ge=0)
    channels: list[str] | None = None
    enabled: bool | None = None


class AlertRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    condition_type: str
    threshold: float
    symbols: list[str]
    scope: str
    cooldown_seconds: int
    channels: list[str]
    enabled: bool
    last_triggered_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("symbols", "channels", mode="before")
    @classmethod
    def _json_load(cls, v):
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except Exception:
                return []
        return v or []


class AlertEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: int
    symbol: str
    trigger_value: float
    threshold: float
    triggered_at: datetime
    acknowledged: bool
    delivered_channels: list[str]
    snapshot: dict | None = None

    @field_validator("delivered_channels", mode="before")
    @classmethod
    def _json_load(cls, v):
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except Exception:
                return []
        return v or []

    @field_validator("snapshot", mode="before")
    @classmethod
    def _snapshot_load(cls, v):
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except Exception:
                return None
        return v


class AlertChannelsOut(BaseModel):
    available: list[str]
    default: list[str]
    # name -> 是否已配置到可真正发出（如 feishu 需配 webhook）；未列出的通道
    # 选中后只会显式跳过，前端可据此禁用或标灰
    configured: dict[str, bool] = {}
