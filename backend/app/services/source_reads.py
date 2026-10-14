"""Opt-in raw inputs shared by current display/consumer reads, never probes."""
from app.core.bjtime import beijing_now
from app.core.inflight_reads import read_inflight, read_policy


async def read_pool_input(provider, method, trade_date):
    if method not in {"get_limit_up_pool", "get_limit_break_pool"}:
        raise ValueError("unsupported shared pool input")
    return await read_inflight(provider, ("pool-input", method, trade_date, read_policy(provider, method)),
                               lambda: getattr(provider, method)(trade_date))


async def read_news_input(provider, method, symbol, limit):
    if method not in {"get_news", "get_announcements"}:
        raise ValueError("unsupported shared news input")
    return await read_inflight(provider, ("news-input", method, beijing_now().date(), symbol, limit, read_policy(provider, method)),
                               lambda: getattr(provider, method)(symbol, limit))
