"""盘后复盘 Agent 的核心数据结构。

设计原则：
- **缺失是一等公民**：任何字段取不到都必须落在 `gaps` 里，不允许用 0 / 空串 / 默认值
  冒充"没有"。`DataGap` 带 `impact` 字段，说明这个缺失会影响哪些结论——
  否则"缺了"和"没有"在下游看起来一模一样，正是本项目踩过的事故类型。
- **可序列化**：全部用 Pydantic，报告要能整体落 JSON、能 diff、能跨版本对比。
- **成本可追溯**：`ModelUsage` 记录"想用什么 / 实际用了什么 / 降级路径 / 成本"。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 数据缺失


class DataGap(BaseModel):
    """数据缺失标注。宁可留空并说明，也不臆测。"""

    field: str = Field(..., description="缺失的字段名")
    source: str = Field(..., description="本该从哪个源取")
    reason: str = Field(..., description="为什么没取到（异常文本或判定依据）")
    impact: str = Field(..., description="影响哪些结论，如 'trades.pnl 归因不可用'")
    severity: str = Field("warn", description="warn 降级可用 / block 该维度不可用")


# ---------------------------------------------------------------- 数据快照


class IndexQuote(BaseModel):
    symbol: str
    name: str
    close: float
    change_pct: float
    amount: float | None = None


class MarketSnapshot(BaseModel):
    """市场环境数据快照（自采）。"""

    trade_date: str
    indices: list[IndexQuote] = Field(default_factory=list)
    breadth: dict[str, Any] = Field(default_factory=dict)
    sentiment: dict[str, Any] = Field(default_factory=dict)
    themes: list[dict[str, Any]] = Field(default_factory=list)
    theme_summary: dict[str, Any] = Field(default_factory=dict)
    gaps: list[DataGap] = Field(default_factory=list)
    #: 数据链健康快照（provider 熔断/切换 + ths 涨停原因哨兵）。
    #: None = 未采集（单源部署或采集失败）——三态纪律：缺失不冒充"健康"。
    provider_health: dict[str, Any] | None = None
    #: 竞价溢价比（昨日涨停股今日竞价承接，P0 因子）。None = 未采集。
    auction_premium: dict[str, Any] | None = None


class OrderRecord(BaseModel):
    id: int
    symbol: str
    side: str
    price: float
    quantity: int
    status: str
    filled_price: float | None = None
    fee: float = 0.0
    reason: str | None = None
    created_at: str | None = None


class PositionRecord(BaseModel):
    symbol: str
    quantity: int
    frozen_today: int = 0
    cost_price: float = 0.0
    last_price: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None


class TradingSnapshot(BaseModel):
    """当日操作与账户数据快照（自采）。"""

    trade_date: str
    account: dict[str, Any] = Field(default_factory=dict)
    orders: list[OrderRecord] = Field(default_factory=list)
    positions: list[PositionRecord] = Field(default_factory=list)
    realized_pnl: float | None = None
    trade_count: int = 0
    gaps: list[DataGap] = Field(default_factory=list)


class PickEntry(BaseModel):
    """每日精选组合条目（daily_pick_set.items 的裁剪视图）。"""

    symbol: str
    name: str | None = None
    score: float | None = None
    themes: list[str] = Field(default_factory=list)
    observation_only: bool = False


class PickReviewEntry(BaseModel):
    """单只推荐股的复盘归因（daily_pick_review 行）。

    verdict: good=达成 / flat=踏空或数据缺失不可评 / bad=失误；
    reason_category 六类见 app.picks.engine.classify_failure。
    """

    symbol: str
    name: str | None = None
    verdict: str
    reason_category: str
    excess_pct: float | None = None
    note: str | None = None


class PicksSnapshot(BaseModel):
    """每日精选组合 + 当日逐股归因快照（2026-09-04 新增维度）。

    组合 T-1 生成、T 日持有：复盘日（trade_date）对照的组合是
    combo_date（=组合生成日）那份；reviews 是当日收盘后 classify_failure 的结果。
    """

    trade_date: str
    combo_date: str | None = None
    items: list[PickEntry] = Field(default_factory=list)
    reviews: list[PickReviewEntry] = Field(default_factory=list)
    gaps: list[DataGap] = Field(default_factory=list)


class ReviewData(BaseModel):
    """一次复盘采集到的全部原始数据。"""

    trade_date: str
    market: MarketSnapshot
    trading: TradingSnapshot
    #: 每日精选快照。None = 未采集（旧版本服务/采集失败）——三态：缺失不冒充"无组合"。
    picks: PicksSnapshot | None = None
    collected_at: str = Field(default_factory=lambda: utcnow().isoformat())

    @property
    def all_gaps(self) -> list[DataGap]:
        picks_gaps = self.picks.gaps if self.picks else []
        return [*self.market.gaps, *self.trading.gaps, *picks_gaps]

    def blocked_dimensions(self) -> set[str]:
        """被数据缺失阻断的维度——这些维度不得产出结论。"""
        return {g.impact.split(".")[0] for g in self.all_gaps if g.severity == "block"}


# ---------------------------------------------------------------- 模型使用


class ModelUsage(BaseModel):
    """本次复盘实际用了什么模型、花了多少。

    `requested` 与 `actual` 分开是刻意的：配置里想用 LLM、实际降级到规则引擎时，
    如果不记录 `degraded`，报告读者会以为结论来自 LLM。
    """

    requested: str = Field(..., description="配置指定的模型/分析器")
    actual: str = Field(..., description="实际使用的模型/分析器")
    fallback_chain: list[str] = Field(default_factory=list, description="尝试过的降级路径")
    degraded: bool = False
    reason: str = ""
    cost: float | None = None
    tokens: dict[str, int] | None = None
    latency_ms: int = 0


# ---------------------------------------------------------------- 结论


class DimensionResult(BaseModel):
    """单个复盘维度的产出。"""

    key: str = Field(..., description="trades | market | system")
    title: str
    status: str = Field("ok", description="ok | degraded | blocked")
    findings: list[str] = Field(default_factory=list, description="观察到的事实")
    judgements: list[str] = Field(default_factory=list, description="基于事实的判断")
    evidence: dict[str, Any] = Field(default_factory=dict)
    gaps: list[DataGap] = Field(default_factory=list)


class ActionItem(BaseModel):
    """可落地的改进项。"""

    id: str = ""
    title: str
    category: str = Field(..., description="parameter | strategy | data | process")
    priority: str = Field("P1", description="P0 | P1 | P2")
    expected_impact: str
    evidence: str
    target: str = Field("", description="要改的具体位置，如 sentiment.EARNING_BANDS.promo_1to2")
    proposed_change: str = ""
    status: str = Field("pending", description="pending | confirmed | applied | rejected | reverted")


class MetaInsight(BaseModel):
    """元结论：关于「复盘方式本身」的结论。

    这是方法论自我迭代的载体——记的不是"市场怎么样"，
    而是"这样复盘有没有用"。
    """

    dimension: str
    observation: str
    effectiveness: str = Field(..., description="effective | ineffective | unknown")
    evidence: str = ""
    suggestion: str = ""


# ---------------------------------------------------------------- 报告


class ReviewReport(BaseModel):
    """一次盘后复盘的完整产出。"""

    review_id: str = ""
    trade_date: str
    generated_at: str = Field(default_factory=lambda: utcnow().isoformat())
    methodology_version: str = "v1"
    model: ModelUsage
    data: ReviewData
    dimensions: list[DimensionResult] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    meta_insights: list[MetaInsight] = Field(default_factory=list)
    summary: str = ""

    def dimension(self, key: str) -> DimensionResult | None:
        return next((d for d in self.dimensions if d.key == key), None)
