"""行情补全：首源快照缺字段时从链上备源补齐。

为什么单独抽出来：这类补全最早只挂在撮合引擎的 live_quote 上（main.py），
涨跌停校验依赖它；但 REST 单只行情 `/api/quotes/{symbol}` 走的是原始 provider，
返回 limit_up_price=null——同一个标的，两个端点口径不一致。

撮合是红线（涨跌停硬拦截不可绕过），所以补全逻辑必须只有一份，
两处共用，避免哪天改了一边另一边悄悄失效。

**字段级补全的背景**：provider 链是"整方法 failover"——首源只要不抛异常就算成功，
即使它返回的字段不全。实测 ths 快照不含 pe_ttm/pb/市值，而链首是 ths，
导致估值字段恒为 None。补全是这类"首源成功但字段缺失"的通用解法，
不改变 failover 语义（价格等核心字段仍以首源为准）。
"""
from __future__ import annotations

import logging
import math
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.core.bjtime import BJ_TZ, beijing_now
from app.core.freshness import DEFAULT_FRESH_WITHIN_SECONDS, MAX_FUTURE_SKEW_SECONDS
from app.data_quality.validator import validate_quote
from app.market.trade_calendar import in_trading_window
from app.schemas.market import Quality, Quote, QuoteFieldSource

log = logging.getLogger(__name__)


def _tencent_of(provider):
    """取链上的腾讯源；本身就是腾讯或无链时返回 None。"""
    if provider is None or getattr(provider, "name", "") == "tencent":
        return None
    chain = getattr(provider, "providers", None) or []
    return next((p for p in chain if getattr(p, "name", "") == "tencent"), None)


async def fetch_quotes_batched(
    hub, symbols: list[str], *, prefer_cache: bool = False, batch_size: int = 50
) -> dict[str, Any]:
    """批量行情补价（腾讯直查 50/批）——多处同构循环的单一实现（R3 收口）。

    real_position / picks / market(speed-rank) / daily_review / morning_brief /
    theme_catalog / events.ranking 历史上各写一份「composite 找 tencent →
    range(0, n, 50) → get_quotes → 吞错 log」，2026-09-07 收口到这里，
    避免改批大小/换源时漏改某处。

    prefer_cache=True 时先读 hub.get_quotes（自选/指数已订阅标的的内存快照；
    未订阅代码返回空、不会被冒充），miss 的才直查腾讯。
    单批失败记 log 继续（错误不静默吞，也不让一批失败打死整体）。
    返回 dict[symbol → Quote]；展示口径由调用方自行加工。
    """
    found: dict[str, Any] = {}
    if prefer_cache:
        for q in hub.get_quotes(symbols):
            found[q.symbol] = q
    missing = [s for s in symbols if s not in found]
    if not missing:
        return found
    provider = hub.provider
    if getattr(provider, "name", "") == "tencent":
        target = provider
    else:
        chain = getattr(provider, "providers", None) or []
        target = next(
            (p for p in chain if getattr(p, "name", "") == "tencent"), provider
        )
    for i in range(0, len(missing), batch_size):
        try:
            for q in await target.get_quotes(missing[i : i + batch_size]):
                found[q.symbol] = q
        except Exception as exc:
            log.warning("quotes batch %s failed: %s", i // batch_size, exc)
    return found


async def fetch_quotes_list(hub, symbols: list[str], *, prefer_cache: bool = False) -> list:
    """`fetch_quotes_batched` 的**列表形态**（顺序即 dict 插入序，调用方只做遍历）。

    为什么单列一个入口：`api/routes/market.py` 的私有 `_batch_quotes` 曾被
    `api/routes/assistant.py` 跨模块当公共 API 用（S2-4）。列表/字典两种形态
    都收在这里，消费方从服务层取，不再依赖某个路由的私有名。
    """
    found = await fetch_quotes_batched(hub, symbols, prefer_cache=prefer_cache)
    return list(found.values())


def _identity(q) -> tuple[str, str] | None:
    symbol, market = q.symbol.lower(), q.market
    if market not in {"SH", "SZ", "BJ"}:
        return None
    if len(symbol) == 8 and symbol[:2] in {"sh", "sz", "bj"}:
        if symbol[:2].upper() != market:
            return None
        symbol = symbol[2:]
    return (symbol, market) if len(symbol) == 6 and symbol.isdigit() else None


def _source_clock(q) -> datetime | None:
    stamp = q.data_timestamp
    # No received_at fallback: reception does not prove a field's source day.
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp.astimezone(BJ_TZ)


def _finite(value, *, positive: bool = False) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > 0 if positive else value != 0))


def _supplement_rejection(q, tq, now: datetime) -> str | None:
    if not isinstance(tq, Quote) or tq.source != "tencent":
        return "source_identity_unknown"
    if _identity(q) is None or _identity(q) != _identity(tq):
        return "security_identity_mismatch"
    if (not isinstance(tq.received_at, datetime) or tq.received_at.tzinfo is None
            or tq.received_at.utcoffset() is None or tq.received_at > now):
        return "received_clock_unknown"
    if tq.quality != Quality.high or set(tq.quality_reasons) - {"off_session"}:
        return "source_quality_untrusted"
    primary_time, source_time = _source_clock(q), _source_clock(tq)
    if primary_time is None or source_time is None:
        return "source_clock_unknown"
    if primary_time > now or source_time > now:
        return "source_clock_future"
    if (source_time - tq.received_at).total_seconds() > MAX_FUTURE_SKEW_SECONDS:
        return "source_receipt_clock_mismatch"
    if primary_time.date() != source_time.date() or source_time.date() != now.date():
        return "source_day_mismatch"
    # Parse-only providers default to high. Validate a copy so their original
    # verdict and the primary fact are never rewritten by this supplement.
    checked = tq.model_copy(deep=True, update={"symbol": _identity(tq)[0]})
    validate_quote(checked, live=in_trading_window(now))
    if checked.quality != Quality.high or not _finite(tq.price, positive=True):
        return "source_values_untrusted"
    return None


def _record_source(q, tq, field: str) -> None:
    # Route callers may hold a shallow model_copy of a shared Hub row.
    q.field_sources = deepcopy(q.field_sources)
    q.field_sources[field] = QuoteFieldSource(source=tq.source,
        data_timestamp=tq.data_timestamp, received_at=tq.received_at)


def _apply_limit_prices(q, tq):
    """Only same-day finite source limits; no calculated limit percentages."""
    up, down = tq.limit_up_price, tq.limit_down_price
    valid_up, valid_down = _finite(up, positive=True), _finite(down, positive=True)
    if ((valid_up and tq.price > up) or (valid_down and tq.price < down)
            or (valid_up and valid_down and down >= up)):
        log.warning("tencent limit supplement rejected %s: inconsistent_source_limits", q.symbol)
        return
    next_up = q.limit_up_price if q.limit_up_price is not None else (up if valid_up else None)
    next_down = q.limit_down_price if q.limit_down_price is not None else (down if valid_down else None)
    if (_finite(next_up, positive=True) and _finite(next_down, positive=True)
            and next_down >= next_up):
        log.warning("tencent limit supplement rejected %s: inconsistent_primary_limits", q.symbol)
        return
    if (not _finite(q.price, positive=True)
            or (_finite(next_up, positive=True) and q.price > next_up)
            or (_finite(next_down, positive=True) and q.price < next_down)):
        log.warning("tencent limit supplement rejected %s: primary_price_outside_limits", q.symbol)
        return
    if q.limit_up_price is None and valid_up:
        _record_source(q, tq, "limit_up_price")
        q.limit_up_price = up
    if q.limit_down_price is None and valid_down:
        _record_source(q, tq, "limit_down_price")
        q.limit_down_price = down


def _apply_valuation(q, tq):
    """Price-dependent ratios/caps cannot borrow a different observation window."""
    source_time, primary_time = _source_clock(tq), _source_clock(q)
    if abs((source_time - primary_time).total_seconds()) > DEFAULT_FRESH_WITHIN_SECONDS:
        log.warning("tencent valuation supplement rejected %s: source_window_mismatch", q.symbol)
        return
    # Negative PE/PB can be a real loss/negative-equity fact. Never turn it into
    # zero or a positive ratio; market capitalisation, unlike ratios, is positive.
    for field in ("pe_ttm", "pb", "total_mktcap_yi", "float_mktcap_yi"):
        value = getattr(tq, field)
        if (getattr(q, field) is None
                and _finite(value, positive=field.endswith("mktcap_yi"))):
            _record_source(q, tq, field)
            setattr(q, field, value)


async def _tencent_snapshot(provider, q):
    """Read once and fail closed before copying any supplementary field."""
    tencent = _tencent_of(provider)
    if tencent is None:
        return None
    try:
        tq = await tencent.get_quote(q.symbol)
        reason = _supplement_rejection(q, tq, beijing_now())
        if reason is not None:
            log.warning("tencent quote supplement rejected %s: %s", q.symbol, reason)
            return None
        return tq
    except Exception as exc:  # noqa: BLE001  补全是尽力而为
        log.warning("tencent 补全失败 %s: %s", q.symbol, exc)
        return None


async def enrich_quote(provider, q):
    """**一次取数**同时补涨跌停价与估值（P1-4，2026-09-11）。

    背景：`fill_limit_prices` 与 `fill_valuation` 各自 `await tencent.get_quote()`，
    而 `tencent.get_quote → _snapshot` **无缓存**（每次真实 HTTP）⇒ 详情页在
    「链首 ths 快照缺涨跌停价 + 缺 pe/pb/市值」时，对**同一 symbol 串行发两次同源请求**。
    个股详情每次打开、每次切股都走这条路径。

    这里把两类字段的取数合并为一次；需要补的字段全都有值时**一次请求都不发**
    （与两个原函数各自的短路条件等价）。字段映射仍走 `_apply_*`，
    与 `fill_limit_prices` / `fill_valuation` 共用同一份实现。
    """
    if q is None:
        return None
    need_limit = q.limit_up_price is None or q.limit_down_price is None
    need_val = None in (q.pe_ttm, q.pb, q.total_mktcap_yi)
    if not (need_limit or need_val):
        return q
    if _tencent_of(provider) is None:
        return q
    tq = await _tencent_snapshot(provider, q)
    if tq is None:
        return q
    if need_limit:
        _apply_limit_prices(q, tq)
    if need_val:
        _apply_valuation(q, tq)
    return q


async def fill_limit_prices(provider, q):
    """原地补全 quote 的涨跌停价（缺失时从链上的 tencent 源取）。

     provider 为 None、链上无 tencent、或取价失败时都保持原样——
     补不上就补不上，绝不臆造限价。

     只补限价、不碰估值。**同时需要估值时请用 `enrich_quote`**——
     两者串联会对同一 symbol 发两次同源 HTTP（P1-4）。
    """
    if q is None:
        return None
    if q.limit_up_price is not None and q.limit_down_price is not None:
        return q
    if _tencent_of(provider) is None:
        return q
    tq = await _tencent_snapshot(provider, q)
    if tq is None:
        return q
    _apply_limit_prices(q, tq)
    return q


async def fill_valuation(provider, q):
    """原地补全估值与市值字段（pe_ttm / pb / 总市值 / 流通市值）。

    实测（2026-08-31）：ths 快照无这些字段，腾讯快照有（pe_ttm 53.66、
    总市值 305.69 亿）。链首是 ths → 估值恒 None → 选股基本面里的 PE 永远"缺失"。

    只在字段缺失时发起请求；补不上就保持原样，绝不臆造。
    **同时需要涨跌停价时请用 `enrich_quote`**（P1-4：避免两次同源请求）。
    """
    if q is None:
        return None
    if None not in (q.pe_ttm, q.pb, q.total_mktcap_yi):
        return q
    if _tencent_of(provider) is None:
        return q
    tq = await _tencent_snapshot(provider, q)
    if tq is None:
        return q
    _apply_valuation(q, tq)
    return q
