from __future__ import annotations

import math
from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean(v: float | None) -> float | None:
    if v is None or not math.isfinite(v):
        return None
    return v


class Quality(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    stale = "stale"
    invalid = "invalid"


class AuditFields(BaseModel):
    source: str
    quality: Quality = Quality.high
    quality_reasons: list[str] = Field(default_factory=list)
    received_at: datetime = Field(default_factory=utcnow)


class Quote(AuditFields):
    symbol: str
    name: str | None = None
    market: str | None = None  # SH / SZ / BJ
    price: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    volume: float | None = None  # 单位见 docs/data-sources.md
    amount: float | None = None  # 元
    turnover_rate: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    total_mktcap_yi: float | None = None  # 亿元
    float_mktcap_yi: float | None = None  # 亿元
    limit_up_price: float | None = None
    limit_down_price: float | None = None
    data_timestamp: datetime | None = None

    def model_post_init(self, __context) -> None:
        for f in ("price", "open", "high", "low", "prev_close", "change", "change_pct",
                  "volume", "amount", "turnover_rate", "pe_ttm", "pb",
                  "total_mktcap_yi", "float_mktcap_yi", "limit_up_price", "limit_down_price"):
            setattr(self, f, _clean(getattr(self, f)))


class OrderBookLevel(BaseModel):
    price: float | None = None
    volume: float | None = None


class OrderBook(AuditFields):
    symbol: str
    bids: list[OrderBookLevel] = Field(default_factory=list)  # 按价格降序
    asks: list[OrderBookLevel] = Field(default_factory=list)  # 按价格升序
    data_timestamp: datetime | None = None


class Trade(AuditFields):
    symbol: str
    ts: datetime | None = None
    price: float | None = None
    volume: float | None = None
    side: str | None = None  # buy / sell / neutral


class Kline(AuditFields):
    symbol: str
    timeframe: str  # 1m/5m/15m/30m/60m/1d/1w
    ts: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    amount: float | None = None
    change_pct: float | None = None
    turnover_rate: float | None = None


class LimitUpRecord(AuditFields):
    symbol: str
    name: str | None = None
    trade_date: date
    price: float | None = None
    change_pct: float | None = None
    first_seal_time: str | None = None
    last_seal_time: str | None = None
    seal_count: int | None = None
    break_count: int | None = None
    seal_amount: float | None = None
    turnover_rate: float | None = None
    consecutive_boards: int | None = None  # 连板数
    boards_stat: str | None = None  # 如 "3天2板"


class LongHuRecord(AuditFields):
    symbol: str
    name: str | None = None
    trade_date: date
    close: float | None = None
    change_pct: float | None = None
    turnover_rate: float | None = None
    amount: float | None = None
    net_buy: float | None = None
    buy_amount: float | None = None
    sell_amount: float | None = None
    reason: str | None = None


class SymbolSearchItem(BaseModel):
    symbol: str
    name: str | None = None
    market: str | None = None
    source: str
    is_realtime: bool = False


class BoardQuote(AuditFields):
    """板块排行条目（新浪行业/概念闪电排行口径）。"""

    name: str
    count: int | None = None  # 成分股数
    change_pct: float | None = None
    volume: float | None = None  # 股
    amount: float | None = None  # 元
    leader_symbol: str | None = None
    leader_name: str | None = None
    leader_change_pct: float | None = None
    leader_price: float | None = None
