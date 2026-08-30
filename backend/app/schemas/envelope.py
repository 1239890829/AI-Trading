"""统一出参信封（技术评审 B2 首批）。

`Envelope[T]` 是所有端点 `{data, meta}` 形态的类型化表达：
- `data` 用具体模型类型化（/docs 自动文档从此准确）
- `meta` 保持 dict（链名/stale 等运维字段按端点自有节奏演进，不强行建模）

首批只覆盖与既有 Pydantic 模型 1:1 对应的端点组（quotes/kline/order-book/
trades/limit-up/limit-break/longhu/search）——路由返回的就是 model_dump()，
加 response_model 零丢字段风险。themes/sentiment 等聚合形态留第二批单独建模。
"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from app.schemas.market import Kline, LimitUpRecord, LongHuRecord

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: dict = Field(default_factory=dict)


class KlinePayload(BaseModel):
    """GET /kline/{symbol} 的 data。"""

    symbol: str
    timeframe: str
    bars: list[Kline]


class LimitUpPoolPayload(BaseModel):
    """GET /limit-up 与 /limit-break 的 data。"""

    trade_date: str
    pool: list[LimitUpRecord]


class LongHuPayload(BaseModel):
    """GET /longhu 的 data。"""

    trade_date: str
    records: list[LongHuRecord]


class AuctionSnapshot(BaseModel):
    """集合竞价快照条目（/api/auction/{symbol}）。auction_volume 单位为手。"""

    symbol: str
    name: str | None = None
    auction_price: float | None = None
    auction_pct: float | None = None
    auction_volume: float | None = None
    auction_amount: float | None = None
    auction_volume_ratio: float | None = None
    auction_unmatched: float | None = None
    pre_close_price: float | None = None
    data_status: str | None = None  # ready|final|suspended|not_ready


class AuctionBenchmarkItem(BaseModel):
    """短线风向标竞价基准条目（/api/auction-benchmark）。"""

    symbol: str
    name: str | None = None
    auction_pct: float | None = None
    tags: list[str] = Field(default_factory=list)


class AdjustmentEvent(BaseModel):
    """复权事件（/api/adjustment-events/{symbol}）；factor 由调用方推导。"""

    ex_date: str  # YYYY-MM-DD
    dividend: float  # 每股现金分红（税前）
    bonus: float  # 每股送股比例
