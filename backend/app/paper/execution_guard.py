"""Deterministic execution context; display-session tolerance never permits a fill."""
from datetime import datetime, timezone

from app.core.freshness import DEFAULT_FRESH_WITHIN_SECONDS, MAX_FUTURE_SKEW_SECONDS
from app.market.trade_calendar import is_trade_day_on, known_trade_days


def window_issue(now, days=None):
    day_state = is_trade_day_on(now.date(), days=known_trade_days() if days is None else days)
    if day_state is None:
        return "calendar_unknown", "交易日历尚未确认当前交易日，暂不模拟成交"
    minute = now.hour * 60 + now.minute
    if not day_state or not (570 <= minute < 690 or 780 <= minute < 897):
        return "market_closed", "当前不是连续交易撮合窗口；竞价、午休和休市不模拟成交"
    return None


def quote_issue(quote, symbol, now):
    if quote is None or getattr(quote, "symbol", None) != symbol:
        return "quote_unavailable", "缺少同一证券的当前报价"
    if (not getattr(quote, "source", None)
            or getattr(quote, "quality", None) not in {"high", "medium"}):
        return "quote_untrusted", "报价来源或质量未通过执行检查"
    if getattr(quote, "suspended", False) or getattr(quote, "trading_status", None) in {"suspended", "停牌"}:
        return "quote_untrusted", "证券停牌，不模拟成交"
    stamp = getattr(quote, "data_timestamp", None)
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        return "quote_untrusted", "报价缺少明确源时点；不以接收时间代替"
    stamp = stamp.astimezone(now.tzinfo)
    age = (now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
    if stamp.date() != now.date() or age > DEFAULT_FRESH_WITHIN_SECONDS or age < -MAX_FUTURE_SKEW_SECONDS:
        return "quote_untrusted", "报价交易日或源时点超出当前可信执行窗口"
    return None
