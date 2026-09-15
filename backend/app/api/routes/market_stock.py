"""个股资料：财务、公司概况、公告、资讯（正文 / 个股）、搜索。

（自 `market.py` 切出，2026-09-15 IMP-005 批 3。**只搬位置，未改逻辑**：
分片正文与原文件对应定义逐字相同。跨分片共用的信封辅助在 `market_envelope`。）
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
from app.data_providers.eastmoney import ProviderError
from app.market.article import ArticleFetchError, classify_url, fetch_article
from app.schemas.envelope import (
    Envelope,
)
from app.schemas.market import (
    SymbolSearchItem,
)
from app.services.quote_hub import QuoteHub

router = APIRouter(tags=["market"])
log = logging.getLogger("app.api.routes.market")

from app.api.routes.market_envelope import (
    meta_payload,
)


@router.get("/financials/{symbol}")
async def financials(symbol: str, periods: int = Query(default=8, ge=1, le=20), hub: QuoteHub = Depends(get_hub)) -> dict:
    """财务摘要（东财业绩报表：营收/净利/同比/毛利率/ROE/EPS，按报告期倒序）。"""
    try:
        rows = await hub.provider.get_financials(symbol, periods)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"财务数据源失败：{exc}")
    return {"data": {"symbol": symbol, "periods": rows}, "meta": meta_payload(hub)}


@router.get("/company/{symbol}")
async def company(symbol: str, hub: QuoteHub = Depends(get_hub)) -> dict:
    """公司资料：简介/行业/主营业务（东财 F10）。"""
    try:
        profile = await hub.provider.get_company_profile(symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"公司资料数据源失败：{exc}")
    return {"data": profile, "meta": meta_payload(hub)}


@router.get("/announcements/{symbol}")
async def announcements(
    symbol: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=30),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """个股公告（东财，title/date/类型/原文链接）。进程内缓存 60s（切股回看不闪加载）。"""
    cache = cache_on(request.app.state, "market.announcements", 60, maxsize=512)
    key = (symbol, limit)
    hit, cached = cache.get(key)
    if hit:
        return {"data": cached, "meta": {**meta_payload(hub), "cached": True}}
    try:
        rows = await hub.provider.get_announcements(symbol, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"公告数据源失败：{exc}")
    data = {"symbol": symbol, "items": rows}
    cache.set(key, data)
    return {"data": data, "meta": meta_payload(hub)}


@router.get("/news/content", response_model=Envelope[dict])
async def news_content(
    request: Request,
    url: str = Query(description="资讯原文链接（仅支持白名单域名）"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """资讯正文抓取（弹窗展示）：新闻/快讯解析文章页正文，公告走官方全文 API。

    必须注册在 /news/{symbol} 之前，否则 content 会被吞成股票代码。
    域名白名单外的 URL 直接 400（SSRF 防护 + 版权边界）。抓取/解析失败
    返回 502 与原因，前端据此降级为「摘要 + 原文链接」。
    """
    try:
        classify_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    cache = cache_on(request.app.state, "news.content", 600, maxsize=256)
    hit, cached = cache.get(url)
    if hit:
        return {"data": {**cached, "cached": True}, "meta": {**meta_payload(hub), "cached": True}}

    try:
        article = await fetch_article(url)
    except ArticleFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:  # 网络层异常统一收敛为可降级失败
        raise HTTPException(status_code=502, detail=f"正文抓取失败：{exc}")
    cache.set(url, article)
    return {"data": article, "meta": meta_payload(hub)}


@router.get("/news/{symbol}")
async def news(
    symbol: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=30),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """个股相关新闻（东财资讯检索，含正文摘要）。进程内缓存 60s（技术债 #4）。"""
    cache = cache_on(request.app.state, "market.news", 60, maxsize=512)
    key = (symbol, limit)
    hit, cached = cache.get(key)
    if hit:
        return {"data": cached, "meta": {**meta_payload(hub), "cached": True}}
    try:
        rows = await hub.provider.get_news(symbol, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"新闻数据源失败：{exc}")
    data = {"symbol": symbol, "items": rows}
    cache.set(key, data)
    return {"data": data, "meta": meta_payload(hub)}


@router.get("/search", response_model=Envelope[list[SymbolSearchItem]])
async def search(q: str = Query(min_length=1, max_length=20), hub: QuoteHub = Depends(get_hub)):
    """代码/名称搜索（tencent→eastmoney failover，链内语义见 CompositeProvider.search）。

    - 进程内 30s TTL 缓存（挂 hub 单例，LRU 有界）：吸收中文 IME 逐字输入的
      前缀突发，同词并发单飞；空结果同样缓存（垃圾前缀不再反复打上游）。
    - 上游全挂（含熔断）→ 502，前端 role=alert 失败提示承接；
      全链"无匹配"→ 200 []，前端展示空结果提示——两种状态不再混为一谈
      （旧实现把 ProviderError 吞成 [] 再兜底 MockProvider，故障被伪装成"没搜到"）。
    - 响应带 Cache-Control: no-store：搜索结果依赖上游实时状态，禁止浏览器
      （尤其 Safari 的启发式缓存）把瞬断窗口里的空结果缓存下来反复回放。
    """
    cache = cache_on(hub, "market.search", 30, maxsize=256)
    kw = q.strip()

    async def _do() -> list[dict]:
        items = await hub.provider.search(kw)
        return [i.model_dump() for i in items]

    try:
        hit, rows = await cache.get_or_set(kw, _do)
    except ProviderError as exc:
        log.warning("search failed: %s", exc)
        raise HTTPException(status_code=502, detail="搜索数据源暂不可用，请稍后重试") from exc
    payload = {"data": rows, "meta": {**meta_payload(hub), "cached": hit}}
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})
