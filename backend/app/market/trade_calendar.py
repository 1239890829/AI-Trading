"""A 股交易日历。

## 为什么需要它

东财涨停池接口（push2ex / getTopicZTPool）有个致命行为：**传入非交易日时静默
返回最近一个交易日的数据，不报错、响应里没有日期字段**。于是 `date.today()` 与
`today - 1 day` 会取到同一份数据，"昨日涨停股今日表现"退化成"涨停股查自己涨停
那天的收盘价"，恒等于 +10%。2026-08-29（周六）线上把市场判成「高潮 / 置信度高」
就是这么来的。

`date.weekday()` 只能跳周末，遇法定节假日（春节、国庆、清明…）必然复现同一事故，
所以必须有一份真实交易日历。

## 日历来源（2026-08-29 修正：主源改为 ths 官方端点）

**主源：ths 官方 `GET /api/a-share/calendar/trading-days`**（近一年交易日）。
**备源：上证综指（sh000001）日 K 的日期集合**（腾讯 `fqkline` 推导）。

为什么必须换成官方端点（这是本次改动的原因）：

| 源 | 天数 | 范围 |
|---|---|---|
| ths 官方 | **242** | 2025-08-29 → 2026-08-28 |
| 腾讯日 K 推导 | 124 | 2026-03-03 → 2026-08-28 |

实测两者交集 124 天，**仅 ths 有 118 天、仅腾讯有 0 天**——官方端点完全覆盖推导口径，
且是交易所权威日历，不依赖 K 线接口可用性（东财 K 线在本机已实测不可用，
推导路径未来也可能踩到同样问题）。

⚠️ 首个版本只实现了腾讯推导，**绕开了早就存在且 main.py:75 / market.py:257 已在使用的
官方端点**，等于同一件事有两套实现且新的那套更差。现在改为官方优先、推导兜底。

推导路径的保留理由：指数永不停牌、交易日必然有 bar，作为无 Key 环境下的降级手段仍有价值。

代价是需要一次网络请求，因此进程内缓存（默认 12 小时），且失败时**显式降级**——
宁可让调用方看到"日历不可用"，也不要默默退回"只跳周末"的错误逻辑。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger(__name__)

INDEX_SYMBOL = "sh000001"  # 上证综指
_CACHE_TTL_SEC = 12 * 3600
_MIN_DAYS = 5  # 少于这个天数视为拉取失败，宁可报错也不用

_lock = asyncio.Lock()
_cached_days: list[date] = []
_cached_at = 0.0


def _normalize(days: list[date]) -> list[date]:
    return sorted({d for d in days if isinstance(d, date)})


def _iter_providers(provider):
    """展开 provider 链：CompositeProvider 取 .providers，裸 provider 取自身。"""
    if provider is None:
        return []
    inner = getattr(provider, "providers", None)
    if isinstance(inner, list) and inner:
        return list(inner)
    return [provider]


def _parse_official(raw) -> list[date]:
    """解析官方日历返回值（'YYYYMMDD' 字符串，也可能已是 date）。"""
    out: list[date] = []
    for it in raw or []:
        if isinstance(it, date):
            out.append(it)
            continue
        s = str(it).strip()
        if len(s) >= 8 and s[:8].isdigit():
            try:
                out.append(date(int(s[:4]), int(s[4:6]), int(s[6:8])))
            except ValueError:
                continue
    return _normalize(out)


async def _official_days(provider) -> list[date]:
    """官方交易日历端点。返回空列表表示没有 provider 提供该能力。"""
    for p in _iter_providers(provider):
        fn = getattr(p, "get_trading_days", None)
        if fn is None:
            continue
        try:
            days = _parse_official(await fn())
        except Exception as exc:
            log.warning("trading calendar from %s failed: %s", getattr(p, "name", p), exc)
            continue
        if days:
            return days
    return []


async def _index_kline_days(provider, lookback_days: int) -> list[date]:
    """备源：上证综指日 K 的日期集合。指数永不停牌，交易日必然有 bar。"""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    bars = await provider.get_kline(INDEX_SYMBOL, "1d", start, end)
    return _normalize([b.ts.date() for b in bars])


async def trading_days(provider, lookback_days: int = 120) -> list[date]:
    """返回升序的交易日列表。

    优先 ths 官方日历（权威、近一年、含节假日调整），失败后退到上证日 K 推导。
    两者都失败时抛 `RuntimeError` 而不是返回猜测值——猜测的日历比没有日历更危险。
    """
    global _cached_days, _cached_at
    now = time.monotonic()
    if _cached_days and now - _cached_at < _CACHE_TTL_SEC:
        return list(_cached_days)

    async with _lock:
        # 双检：等锁期间可能已被别的协程填满
        if _cached_days and time.monotonic() - _cached_at < _CACHE_TTL_SEC:
            return list(_cached_days)

        days: list[date] = []
        source = ""
        try:
            days = await _official_days(provider)
            if days:
                source = "official"
        except Exception as exc:  # pragma: no cover - 防御，_official_days 内部已兜底
            log.warning("official trading calendar error: %s", exc)

        if len(days) < _MIN_DAYS:
            try:
                days = await _index_kline_days(provider, lookback_days)
                if days:
                    source = "index-kline"
            except Exception as exc:
                log.warning("index kline calendar failed: %s", exc)

        if len(days) < _MIN_DAYS:
            raise RuntimeError(
                f"交易日历拉取异常：仅得 {len(days)} 个交易日（<{_MIN_DAYS}），拒绝使用"
            )
        _cached_days = days
        _cached_at = time.monotonic()
        log.info("trading calendar loaded: %s days from %s, last=%s",
                 len(days), source, days[-1])
        return list(days)


def is_trade_day(days: list[date], d: date) -> bool:
    """二分查找：d 是否为交易日。"""
    import bisect

    i = bisect.bisect_left(days, d)
    return i < len(days) and days[i] == d


def last_trade_date(days: list[date], asof: date | None = None) -> date | None:
    """返回 <= asof 的最后一个交易日。asof 默认为今天。"""
    import bisect

    if not days:
        return None
    asof = asof or date.today()
    i = bisect.bisect_right(days, asof)
    return days[i - 1] if i > 0 else None


def prev_trade_date(days: list[date], d: date) -> date | None:
    """返回 d 之前的**上一个**交易日（严格早于 d，不含 d 本身）。"""
    import bisect

    if not days:
        return None
    i = bisect.bisect_left(days, d)
    return days[i - 1] if i > 0 else None


def nth_prev_trade_date(days: list[date], anchor: date, n: int) -> date | None:
    """以 anchor 为起点（须为交易日）往前数第 n 个交易日。n=1 即前一交易日。"""
    import bisect

    if not days or n < 0:
        return None
    i = bisect.bisect_left(days, anchor)
    j = i - n
    return days[j] if j >= 0 else None


def recent_trade_dates(days: list[date], anchor: date, count: int) -> list[date]:
    """返回以 anchor 结尾（含）的连续 count 个交易日，**由近及远**。"""
    import bisect

    if not days or count <= 0:
        return []
    i = bisect.bisect_left(days, anchor)
    end = i + 1  # 若 anchor 不是交易日，bisect_left 指向其后一个，正好截断到它之前
    if i >= len(days) or days[i] != anchor:
        end = i
    start = max(0, end - count)
    return list(reversed(days[start:end]))


def invalidate_cache() -> None:
    """测试用：清空进程内缓存。"""
    global _cached_days, _cached_at
    _cached_days = []
    _cached_at = 0.0
