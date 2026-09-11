from __future__ import annotations

import math
from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.core.freshness import DEFAULT_FRESH_WITHIN_SECONDS, Freshness


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

    def limit_prices_state(self) -> str:
        """涨跌停价可用性**三态**：`ready` / `partial` / `unavailable`。

        为什么必须显式（2026-09-11 S1-4）：撮合引擎的「涨停不可买入 / 跌停不可卖出」
        是红线硬拦截，原写法是 `if quote.limit_up_price and ...`——限价为 `None`
        （或 0）时**整体短路**，守卫静默失效且与「该票今天没触板」不可区分。
        补价依赖链上腾讯单源（曾真实被封），所以「拿不到限价」是会发生的状态，
        不是异常路径。

        `<= 0` 与 `None` 同等视为**不可用**：0 在布尔上下文中是假值，
        与 `None` 走的是同一个静默分支，必须归到同一态。
        """
        up = self.limit_up_price if (self.limit_up_price or 0) > 0 else None
        down = self.limit_down_price if (self.limit_down_price or 0) > 0 else None
        if up is not None and down is not None:
            return "ready"
        if up is None and down is None:
            return "unavailable"
        return "partial"

    def freshness(self, *, fresh_within: float = DEFAULT_FRESH_WITHIN_SECONDS) -> Freshness:
        """本报价的新鲜度（S2-1 契约的 **Quote 样板**）。

        年龄取 `data_timestamp`（上游给的数据时点）优先，缺失回退 `received_at`
        （本机接收时点）——两者语义不同，回退时**不假装是同一个**：`reason`
        会写明是「无数据时间戳」，读的人能判断这个年龄到底在量什么。

        质量等级与年龄是**两个独立维度**，任一为差都要降级：
        `invalid` 表示校验层已判定报价不可用（价格/涨跌幅越界等）⇒ 直接
        `unavailable`，绝不能因为"刚收到"就当成 ready。
        """
        if self.quality is Quality.invalid:
            return Freshness.unavailable(
                reason="数据源质量自评 invalid，报价不可用于结论", source=self.source
            )
        as_of = self.data_timestamp or self.received_at
        f = Freshness.from_age(
            as_of=as_of,
            fresh_within=fresh_within,
            source=self.source,
            missing_reason=(
                "报价无 data_timestamp 且无 received_at，无法判定新鲜度"
                if self.received_at is None
                else "无数据时间戳，按接收时间判定"
            ),
        )
        if self.quality is Quality.stale and f.state == "ready":
            return Freshness.stale(
                as_of=as_of, age_seconds=f.age_seconds, source=self.source,
                reason="数据源自评质量 stale（年龄虽在窗口内）",
            )
        return f


class TradingStatus(str, Enum):
    """个股交易状态（UI 缺陷 #1：休市/停牌标识）。

    只有三个值，没有 `pre_listing`：实测（2026-09-02）"K 线为空"的标的
    （301688 / 601091）日 K 接口直接 **502**，是数据源里没有这只票，
    不是"已上市但无成交"。把它标成"未上市"等于把数据源故障说成事实，
    故一律落 `unknown`。
    """

    trading = "trading"      # 正常交易
    suspended = "suspended"  # 停牌（有历史K线但最近交易日缺 bar）
    unknown = "unknown"      # 无法判定（无K线 / 日历不可用）


class TradingStatusInfo(BaseModel):
    """停牌判定的**结论 + 依据**。reason 必须可读，前端直接展示给用户。"""

    status: TradingStatus = TradingStatus.unknown
    suspended_days: int | None = None    # 已停牌的交易日数（仅 suspended）
    suspended_since: date | None = None  # 首个缺失的交易日（停牌起始日）
    last_bar_date: date | None = None    # 最后一根日K对应的交易日
    anchor_date: date | None = None      # 判定基准交易日（最近交易日）
    reason: str = ""                     # 判定依据，缺失/异常时说明原因


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
    max_seal_money: float | None = None  # 盘中最高封单（ths seal字段；封单留存率分母）
    turnover_rate: float | None = None
    consecutive_boards: int | None = None  # 连板数
    boards_stat: str | None = None  # 如 "3天2板"
    reason: str | None = None  # 涨停原因/题材（ths 官方口径）
    # --- 东财 push2ex 特有、ths 不提供的增强维度（题材梯队看板用）---
    float_market_cap: float | None = None  # 流通市值（元）ltsz
    total_market_cap: float | None = None  # 总市值（元）tshare
    amount: float | None = None  # 成交额（元）amount
    industry_board: str | None = None  # 所属行业板块 hybk（东财口径）


class AnomalyRecord(AuditFields):
    """当日个股异动原因（ths 独占口径，today-only 无历史）。

    analysis/keywords 是官方给出的异动归因文本——热点消息验证环
    （docs/summary/architecture-design.md §3）的盘面证据源，也是自选监控「为什么异动」的答案。
    """

    symbol: str
    name: str | None = None
    tag: str | None = None  # 官方 tag_name：涨停/跌停/大幅上涨/大幅下跌/快速反弹/快速下跌
    analysis: str | None = None  # 官方异动原因分析原文（不得改写）
    keywords: list[str] = Field(default_factory=list)
    source: str | None = None


class LimitDownRecord(AuditFields):
    """跌停池单条记录（东财 push2ex getTopicDTPool）。

    与 LimitUpRecord 刻意分开而非复用：跌停池没有涨停原因/梯队/封板时间等语义，
    硬塞会产生大量"字段存在但恒空"的错义位；且连续跌停天数（days）与连板数
    （consecutive_boards）是不同概念。2026-09-04 市场页跌停入口联动新增。
    """

    symbol: str
    name: str | None = None
    trade_date: date
    price: float | None = None
    change_pct: float | None = None
    consecutive_days: int | None = None  # 连续跌停天数 days
    open_count: int | None = None  # 开板次数 oc（跌停后打开次数）
    seal_amount: float | None = None  # 封单额（元）fba
    turnover_rate: float | None = None  # hs
    # --- 东财 push2ex 增强维度（与涨停池同名字段同义）---
    float_market_cap: float | None = None  # 流通市值（元）ltsz
    total_market_cap: float | None = None  # 总市值（元）tshare
    amount: float | None = None  # 成交额（元）amount
    industry_board: str | None = None  # 所属行业板块 hybk（东财口径）


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
    # 概念标签（如"玉米,粮食概念,乳业"）。**它不是上榜原因**——
    # 2026-09-02 之前 ths provider 在 reason 缺失时用概念标签顶替 reason，
    # 界面"上榜原因"因此显示成题材概念（错义且不报错）。拆成独立字段保留这份信息。
    concept_tags: str | None = None
    # --- 2026-09-02 补齐：统计区间与资金结构维度 ---
    # ⚠️ range_days 是**统计区间**不是"上榜天数"：交易所对同一只股票可同时披露
    # 「当日榜」（日涨幅偏离值达 7% 等）与「三日榜」（连续三日偏离值累计达 20% 等），
    # 两者的 buy/sell/net 是**不同区间的累计值**，不可相加、不可互相替代。
    # 实测 2026-08-31 ths 返回 76 条 / 70 只股票，差值 6 即同时上日榜+三日榜的个股；
    # 其中 002396 星网锐捷日榜净额 -9783 万、三日榜 +7252 万——**符号相反**。
    # 该字段此前未解析，下游 {r.symbol: r} 建 dict 静默覆盖，取到哪条取决于服务端返回顺序，
    # 曾导致"游资净买入"证据方向随机反转。缺失它 = 任何按 symbol 去重都是错的。
    range_days: int | None = None
    net_rate: float | None = None  # 净额占区间成交额比（小数），跨市值可比性优于绝对金额
    org_net_value: float | None = None  # 机构净额；缺失表示**该榜单无机构参与**，不可填 0
    hot_money_net_value: float | None = None  # 游资净额；缺失同上
    hot_rank: int | None = None  # 热度排名


class SymbolSearchItem(BaseModel):
    symbol: str
    name: str | None = None
    market: str | None = None
    source: str
    is_realtime: bool = False
