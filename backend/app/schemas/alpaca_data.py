"""An optional overnight bid/ask observation, never a last trade or order price."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.asset_evidence import AssetEvidence


class AlpacaIndicativeQuote(BaseModel):
    bid_price: float | None
    ask_price: float | None
    bid_size_shares: int
    ask_size_shares: int
    bid_exchange: str
    ask_exchange: str
    conditions: list[str]
    tape: Literal["N"] | None = None
    source_time: datetime
    source_timestamp: str
    received_at: datetime
    currency: Literal["USD"] = "USD"
    currency_basis: Literal["explicit_request_and_official_contract"] = "explicit_request_and_official_contract"
    price_kind: Literal["indicative_bid_ask"] = "indicative_bid_ask"
    last_trade_available: Literal[False] = False
    source: Literal["alpaca"] = "alpaca"
    feed: Literal["overnight"] = "overnight"


class AlpacaOvernightResult(BaseModel):
    state: Literal["disabled", "missing_credentials", "authorization_unverified", "unavailable", "stale", "available"]
    reason: str
    instrument_id: str | None = None
    symbol: str | None = None
    source: Literal["alpaca"] = "alpaca"
    feed: Literal["overnight"] = "overnight"
    session: Literal["overnight"] = "overnight"
    authorization_verified: bool = False
    quote: AlpacaIndicativeQuote | None = None
    source_age_seconds: float | None = None
    freshness_limit_seconds: Literal[60] = 60
    received_at: datetime | None = None
    checked_at: datetime
    calendar_status: Literal["unverified"] = "unverified"
    calendar_note: str = "纽约时钟只用于请求窗口；节假日、停牌及实际开市状态未核"
    missing: list[str] = Field(default_factory=list)
    evidence: list[AssetEvidence] = Field(default_factory=list)
