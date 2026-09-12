"""个股停牌判定。

## 为什么需要它

UI 缺陷 #1（docs/summary/review-governance.md §2）：个股页没有任何休市/停牌标识，
用户看到一只股票"价格不动、量能为 0"时，无法区分是**停牌**、**一字涨停无量**
还是**数据源挂了**——三种情况界面表现几乎一样，但含义完全不同。

## 判据：有历史 K 线，但最近交易日缺 bar = 停牌

不要靠"成交量 = 0"硬猜。2026-09-02 全市场快照实测（5565 只）：

| 现象 | 只数 | 真相 |
|---|---|---|
| `volume == 0` 且有昨收无最新价 | 7（002274 prev=6.47 等） | 真的停牌 |
| `volume == 0` 且全字段 None | 12（301688 / 601091 / 920071…） | 日K 接口 **502**，数据源里没这只票 |
| 一字涨停 | 若干 | 无量但仍然在交易 |

**一字涨停同样无量**，所以 `volume == 0` 判停牌必然误标；而"全字段 None"那一类
跟停牌在快照层完全同形，只有拿日 K 才能分开。

## 为什么空 K 线判 `unknown` 而不是 `pre_listing`

最初设计里有个 `pre_listing`（未上市新股）。实测推翻：301688 / 601091 的"零 bar"
不是"已上市未成交"，是**接口 502 拿不到数据**。把数据源故障标成"未上市"属于
典型的静默误判——不报错、界面照常、结论是编的。故空 K 线一律 `unknown`，
并在 reason 里同时写明两种可能，前端不渲染徽标。

## 盘中口径（最容易踩的坑）

当日未收盘时，数据源通常**还没有当日 bar**。若直接拿"最后一根 bar 日期"对
"最近交易日"比，盘中会把**所有正常股票**误判成停牌 1 天。
因此当日未收盘时，"缺今天"必须从停牌天数里剔除。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from app.core.bjtime import BJ_TZ  # S2-8：时区常量收敛到全站唯一权威
from app.market.trade_calendar import last_trade_date
from app.schemas.market import TradingStatus, TradingStatusInfo

# 收盘后日K才稳定可取；留 5 分钟余量（交易所收完到数据源落地有延迟）
_MARKET_CLOSE_HHMM = 1505

# 缺失跨度超过这么多个交易日（≈1 年）时，多半是退市或长期停牌，reason 里额外提示
_LONG_GAP_DAYS = 250


def bar_date(ts: datetime) -> date:
    """K 线时间戳 → 北京时间日期。

    实测日 K 的 `ts` 形如 `2026-09-01T00:00:00+08:00`（带偏移），直接 `.date()`
    就是交易日；naive 的按北京时间理解。统一走一遍转换，避免将来某个源
    换存 UTC 午夜（那会让 `.date()` 整体偏一天）。
    """
    if ts.tzinfo is None:
        return ts.date()
    return ts.astimezone(BJ_TZ).date()


def _market_closed(now: datetime) -> bool:
    """当日是否已收盘（北京时间）。"""
    bj = now.astimezone(BJ_TZ)
    return bj.hour * 100 + bj.minute >= _MARKET_CLOSE_HHMM


def resolve_trading_status(
    bar_dates: Iterable[date],
    trade_days: list[date],
    *,
    now: datetime,
) -> TradingStatusInfo:
    """判定交易状态。**纯函数**，不碰 IO，便于单测。

    Args:
        bar_dates: 日 K 的交易日集合（顺序无所谓，内部排序取最大值）
        trade_days: 升序交易日列表（`trade_calendar.trading_days()`）
        now: 当前时刻（任意时区，内部转北京时间）

    Returns:
        永远返回结论，永不抛异常——判定失败时落 `unknown` 并写明原因，
        让调用方/前端能如实呈现，而不是静默退化成 `trading`。
    """
    days = sorted({d for d in trade_days if isinstance(d, date)})
    if not days:
        return TradingStatusInfo(status=TradingStatus.unknown, reason="交易日历不可用，无法判定")

    bars = sorted({d for d in bar_dates if isinstance(d, date)})
    if not bars:
        return TradingStatusInfo(
            status=TradingStatus.unknown,
            reason="无日K数据（数据源不可用，或该标的尚未上市/已退市）",
        )

    today = now.astimezone(BJ_TZ).date()
    anchor = last_trade_date(days, today)
    if anchor is None:
        return TradingStatusInfo(status=TradingStatus.unknown, reason="交易日历未覆盖当前日期，无法判定")

    last_bar = bars[-1]
    if last_bar >= anchor:
        return TradingStatusInfo(
            status=TradingStatus.trading,
            last_bar_date=last_bar,
            anchor_date=anchor,
            reason=f"最近交易日 {anchor:%Y-%m-%d} 有成交",
        )

    missing = [d for d in days if last_bar < d <= anchor]
    # 盘中：当日未收盘，数据源通常还没有当日 bar，"缺今天"是正常现象不是停牌
    if anchor == today and not _market_closed(now):
        missing = [d for d in missing if d != anchor]
    if not missing:
        # 唯一缺口就是今天，且今天还没收盘 → 正常交易
        return TradingStatusInfo(
            status=TradingStatus.trading,
            last_bar_date=last_bar,
            anchor_date=anchor,
            reason=f"当日未收盘、日K尚未生成，最近完整交易日 {last_bar:%Y-%m-%d} 有成交",
        )

    n = len(missing)
    reason = f"自 {missing[0]:%Y-%m-%d} 起无成交，截至 {anchor:%Y-%m-%d} 已停牌 {n} 个交易日"
    if n > _LONG_GAP_DAYS:
        reason += "（跨度超过一年，可能为长期停牌或已退市）"
    return TradingStatusInfo(
        status=TradingStatus.suspended,
        suspended_days=n,
        suspended_since=missing[0],
        last_bar_date=last_bar,
        anchor_date=anchor,
        reason=reason,
    )
