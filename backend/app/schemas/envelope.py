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
