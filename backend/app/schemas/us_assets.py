"""US read facts retain source identity, currency and session-specific clocks."""
from __future__ import annotations

from datetime import date as CalendarDate, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.asset_evidence import AssetEvidence
from app.schemas.alpaca_data import AlpacaOvernightResult


class UsInstrument(BaseModel):
    instrument_id: str
    asset_type: Literal["us_equity"] = "us_equity"
    market: Literal["US"] = "US"
    symbol: str
    source_symbol: str
    currency: Literal["USD"] = "USD"
    security_type: Literal["stock", "etf"]
    name: str
    english_name: str | None = None
    exchange_source_code: str
    source: Literal["tencent"] = "tencent"


class UsSearchCandidate(BaseModel):
    symbol: str
    source_symbol: str
    market: Literal["US"] = "US"
    name: str
    source_kind: str
    security_type: Literal["stock", "etf"]
    source: Literal["tencent"] = "tencent"
    # Search does not establish denomination or a complete record identity.


class UsSearchResult(BaseModel):
    query: str
    asset_type: Literal["us_equity"] = "us_equity"
    state: Literal["ready", "partial", "blocked"]
    items: list[UsSearchCandidate] = Field(default_factory=list)
    reason: str | None = None
    issues: list[str] = Field(default_factory=list)
    evidence: list[AssetEvidence] = Field(default_factory=list)


class UsRegularQuote(BaseModel):
    state: Literal["ready"] = "ready"
    price: float
    previous_close: float
    change: float | None = None
    change_pct: float | None = None
    change_basis: Literal["source_previous_regular_close"] = "source_previous_regular_close"
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume_shares: float | None = None
    turnover: float | None = None
    source_time: datetime
    source_timezone: Literal["America/New_York"] = "America/New_York"
    received_at: datetime
    observation: Literal["regular_quote", "regular_close", "session_unverified", "stale"]
    feed_delay_seconds: None = None
    realtime_verified: Literal[False] = False
    source: Literal["tencent"] = "tencent"
    evidence: list[AssetEvidence] = Field(default_factory=list)


class UsDailyPoint(BaseModel):
    date: CalendarDate
    open: float
    high: float
    low: float
    close: float
    volume_shares: float


class UsDailySection(BaseModel):
    state: Literal["ready", "unavailable"]
    reason: str | None = None
    points: list[UsDailyPoint] = Field(default_factory=list)
    price_basis: Literal["none"] = "none"
    source: Literal["tencent"] = "tencent"
    source_symbol: str
    evidence: list[AssetEvidence] = Field(default_factory=list)


class UsUnavailableSession(BaseModel):
    state: Literal["unavailable"] = "unavailable"
    reason: str = "当前来源未核实该时段的独立价格和时间；不借用常规收盘价"


class UsSnapshot(BaseModel):
    state: Literal["partial"] = "partial"
    instrument: UsInstrument
    regular: UsRegularQuote
    daily: UsDailySection
    pre_market: UsUnavailableSession = Field(default_factory=UsUnavailableSession)
    post_market: UsUnavailableSession = Field(default_factory=UsUnavailableSession)
    overnight: UsUnavailableSession = Field(default_factory=UsUnavailableSession)
    overnight_data: AlpacaOvernightResult | None = None
    minute: UsUnavailableSession = Field(default_factory=lambda: UsUnavailableSession(
        reason="当前分钟源未提供有效源日期与完整序列；不以单个收盘点绘制分时"))
    clock_phase: Literal["pre_market", "regular", "post_market", "overnight", "weekend"]
    calendar_status: Literal["unverified"] = "unverified"
    calendar_note: str = "按纽约时钟划分时段；节假日和半日市未核，不表示交易所正在开市"
    received_at: datetime
    missing: list[str] = Field(default_factory=list)
