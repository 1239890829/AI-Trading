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
    turnover_rate: float | None = None
    consecutive_boards: int | None = None  # 连板数
    boards_stat: str | None = None  # 如 "3天2板"
    reason: str | None = None  # 涨停原因/题材（ths 官方口径）
    # --- 东财 push2ex 特有、ths 不提供的增强维度（题材梯队看板用）---
    float_market_cap: float | None = None  # 流通市值（元）ltsz
    total_market_cap: float | None = None  # 总市值（元）tshare
    amount: float | None = None  # 成交额（元）amount
    industry_board: str | None = None  # 所属行业板块 hybk（东财口径）


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
