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

log = logging.getLogger(__name__)


def _tencent_of(provider):
    """取链上的腾讯源；本身就是腾讯或无链时返回 None。"""
    if provider is None or getattr(provider, "name", "") == "tencent":
        return None
    chain = getattr(provider, "providers", None) or []
    return next((p for p in chain if getattr(p, "name", "") == "tencent"), None)


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

    tencent = _tencent_of(provider)
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


async def fill_valuation(provider, q):
    """原地补全估值与市值字段（pe_ttm / pb / 总市值 / 流通市值）。

    实测（2026-08-31）：ths 快照无这些字段，腾讯快照有（pe_ttm 53.66、
    总市值 305.69 亿）。链首是 ths → 估值恒 None → 选股基本面里的 PE 永远"缺失"。

    只在字段缺失时发起请求；补不上就保持原样，绝不臆造。
    """
    if q is None:
        return None
    if None not in (q.pe_ttm, q.pb, q.total_mktcap_yi):
        return q
    tencent = _tencent_of(provider)
    if tencent is None:
        return q
    try:
        tq = await tencent.get_quote(q.symbol)
    except Exception as exc:
        log.warning("tencent 补估值失败 %s: %s", q.symbol, exc)
        return q
    if tq is None:
        return q
    q.pe_ttm = q.pe_ttm if q.pe_ttm is not None else tq.pe_ttm
    q.pb = q.pb if q.pb is not None else tq.pb
    q.total_mktcap_yi = q.total_mktcap_yi if q.total_mktcap_yi is not None else tq.total_mktcap_yi
    q.float_mktcap_yi = q.float_mktcap_yi if q.float_mktcap_yi is not None else tq.float_mktcap_yi
    return q
