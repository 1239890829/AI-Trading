"""行情补全：ths 快照缺涨跌停价时从腾讯源补齐。

为什么单独抽出来：这份补全最早只挂在撮合引擎的 live_quote 上（main.py），
涨跌停校验依赖它；但 REST 单只行情 `/api/quotes/{symbol}` 走的是原始 provider，
返回 limit_up_price=null——同一个标的，两个端点口径不一致。

撮合是红线（涨跌停硬拦截不可绕过），所以补全逻辑必须只有一份，
两处共用，避免哪天改了一边另一边悄悄失效。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


async def fill_limit_prices(provider, q):
    """原地补全 quote 的涨跌停价（缺失时从链上的 tencent 源取）。

     provider 为 None、链上无 tencent、或取价失败时都保持原样——
     补不上就补不上，绝不臆造限价。
    """
    if q is None:
        return None
    if q.limit_up_price is not None and q.limit_down_price is not None:
        return q
    if provider is None or getattr(provider, "name", "") == "tencent":
        return q

    chain = getattr(provider, "providers", None) or []
    tencent = next((p for p in chain if getattr(p, "name", "") == "tencent"), None)
    if tencent is None:
        return q
    try:
        tq = await tencent.get_quote(q.symbol)
    except Exception as exc:
        log.warning("tencent 补涨跌停价失败 %s: %s", q.symbol, exc)
        return q
    if tq is None:
        return q
    q.limit_up_price = q.limit_up_price if q.limit_up_price is not None else tq.limit_up_price
    q.limit_down_price = q.limit_down_price if q.limit_down_price is not None else tq.limit_down_price
    return q
