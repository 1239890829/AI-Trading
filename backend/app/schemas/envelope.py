"""统一出参信封（技术评审 B2）。

`Envelope[T]` 是所有端点 `{data, meta}` 形态的类型化表达：
- `data` 用具体模型类型化（/docs 自动文档从此准确）
- `meta` 保持 dict（链名/stale 等运维字段按端点自有节奏演进，不强行建模）

建模分层：
- **严格模型**（extra 默认 ignore）：与既有模型 1:1 的稳定端点
  （quotes/kline/order-book/trades/limit-up/limit-break/longhu/search/overview/minute-line）；
- **宽容模型**（extra="allow"）：高频演进的聚合载荷（sentiment/breadth/themes）——
  已知字段文档化，未知字段**保留透传**，服务端加字段不破坏前端；
- themes 的卡片字段面大且高频重构，字段级模型由前端 types/market.ts 镜像维护，
  后端只锁 summary 与 envelope 骨架——这是显式取舍，不是遗漏。
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.market import Kline, LimitUpRecord, LongHuRecord, Quote

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


# ---------------------------------------------------------------- B2 第二批：聚合载荷


class OverviewPayload(BaseModel):
    """GET /market/overview 的 data。"""

    indices: list[Quote]
    total_amount: float


class MinutePointModel(BaseModel):
    """分钟分时点（与 /api/minute-line points 元素一致）。"""

    ts: str
    price: float
    volume: float | None = None
    cum_amount: float | None = None
    cum_volume: int | None = None
    avg: float | None = None
    source: str


class MinuteLinePayload(BaseModel):
    """GET /minute-line/{symbol} 的 data。

    vr_baseline_5m：精确量比基线（最近 5 个完整交易日逐 5min 槽同期累计量均值，
    不含当日）；TDX 历史未落地时为 null，前端回退近似口径。
    """

    symbol: str
    points: list[MinutePointModel]
    vr_baseline_5m: list[float] | None = None


class SentimentIndicator(BaseModel):
    name: str
    value: str | float | int | None = None
    note: str | None = None


class SentimentPayload(BaseModel):
    """GET /market/sentiment 的 data（情绪引擎输出 + 池计数）。

    情绪引擎字段面随方法论演进（**result 展开**），extra="allow" 透传新字段。
    """

    model_config = ConfigDict(extra="allow")

    phase: str | None = None
    temperature: float | None = None
    confidence: str | None = None
    reasons: list[str] = Field(default_factory=list)
    misjudge_caveats: list[str] = Field(default_factory=list)
    switch_conditions: str | list | None = None
    indicators: list[SentimentIndicator] = Field(default_factory=list)
    ladder: dict[str, float] = Field(default_factory=dict)
    judged_at: str | None = None
    pool_today_count: int | None = None
    pool_yesterday_count: int | None = None
    is_last_trade_date_today: bool | None = None


class BreadthData(BaseModel):
    """GET /market/breadth 的 data（全市场快照口径）。"""

    model_config = ConfigDict(extra="allow")

    breadth: dict[str, Any] | None = None
    snapshot_age_seconds: float | None = None
    rows: int | None = None


class ThemeSummary(BaseModel):
    """题材看板 summary（路由按 min_count/min_boards 过滤，theme_count 为过滤前识别总数）。"""

    model_config = ConfigDict(extra="allow")

    limit_up_total: int = 0
    theme_count: int = 0
    market_max_boards: int | None = None
    market_break_rate: float | None = None
    top_theme: str | None = None


class ThemeBoardPayload(BaseModel):
    """GET /themes 的 data 骨架。

    卡片（themes[]）字段面大且高频重构：此处以 dict 透传，
    字段级契约由前端 types/market.ts 镜像维护。
    """

    model_config = ConfigDict(extra="allow")

    trade_date: str | None = None
    prev_trade_date: str | None = None
    themes: list[dict[str, Any]] = Field(default_factory=list)
    broken_ladder: list[dict[str, Any]] = Field(default_factory=list)
    summary: ThemeSummary = Field(default_factory=ThemeSummary)
    caveats: list[str] = Field(default_factory=list)


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


# ---------------------------------------------------------------- 情绪周期序列（retro #17）


class SentimentHistoryItem(BaseModel):
    """单日情绪判定。"""

    trade_date: str  # YYYYMMDD
    phase: str
    temperature: float | None = None
    confidence: str | None = None
    phase_unreliable: bool = False
    source: str  # review / live


class CycleSegment(BaseModel):
    group: str  # strong / neutral / weak
    start_date: str
    days: int


class SentimentHistoryPayload(BaseModel):
    """GET /market/sentiment-history 的 data。"""

    items: list[SentimentHistoryItem] = Field(default_factory=list)
    cycle: dict = Field(default_factory=dict)  # {start_date, start_phase, days, current_group}
    backfilled: int = 0  # 本次调用从复盘报告回填的条数
    notes: list[str] = Field(default_factory=list)
