"""新闻/公告摘要端点。

规则摘要器永远可用（零外部依赖），LLM 配置后自动升级、失败自动降级，
`model` 字段回传实际使用的摘要器与降级原因，前端据此标注来源。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_hub
from app.core.config import settings
from app.core.ttl_cache import cache_on
from app.news.llm import LLMSummarizer
from app.news.router import SummaryRouter
from app.schemas.envelope import Envelope
from app.schemas.news_digest import DigestItem, NewsDigestPayload

router = APIRouter(tags=["news"])


def _to_item(row: dict) -> DigestItem:
    """规则摘要器返回的是「原字段 + 摘要字段」的合并字典，直接用即可。

    切勿拿原始 rows 与结果 zip 配对——结果已按重要度重排，配对会张冠李戴。
    """
    known = {k: row[k] for k in row if k in DigestItem.model_fields}
    return DigestItem(**known)


@router.get("/news/digest/{symbol}", response_model=Envelope[NewsDigestPayload])
async def news_digest(
    symbol: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=30),
    hub=Depends(get_hub),
) -> dict:
    """个股新闻+公告摘要：重要度分级、消息面情绪、事实摘要、关键数字。

    按重要度倒序返回。摘要只做事实抽取，不含任何买卖建议。
    """
    # 2026-09-09：TTL 60s 实测不够——生成一次要 ~32s（抓原文+摘要），缓存几乎
    # 永远过期，表现为「每次打开都很慢」。个股资讯是日频数据，5 分钟缓存可接受。
    cache = cache_on(request.app.state, "news.digest", 300, maxsize=512)
    key = (symbol, limit)
    hit, cached = cache.get(key)
    if hit:
        return {"data": cached, "meta": {"cached": True}}

    # 数据源单侧失败降级而非整体 502：新闻源（东财搜索）有间歇软封锁，
    # 若整体失败会把同时可用的公告也吃掉；错误原因显式透出（news_error/
    # announcements_error 非空 = 该侧已降级），绝不静默空列表。双侧都挂才 502。
    news_rows: list = []
    ann_rows: list = []
    news_error: str | None = None
    ann_error: str | None = None
    try:
        news_rows = await hub.provider.get_news(symbol, limit)
    except Exception as exc:
        news_error = str(exc)
    try:
        ann_rows = await hub.provider.get_announcements(symbol, limit)
    except Exception as exc:
        ann_error = str(exc)
    if news_error and ann_error:
        raise HTTPException(
            status_code=502,
            detail=f"新闻与公告数据源均失败：news({news_error})；announcements({ann_error})",
        )

    rt = SummaryRouter(
        requested=settings.news_model,
        llm=LLMSummarizer(
            base_url=settings.news_llm_base_url,
            api_key=settings.news_llm_api_key,
            model=settings.news_llm_model,
            provider=settings.llm_provider,
            cli_path=settings.llm_cli_path,
        ),
    )
    # LLM 接入后摘要是同步 HTTP，必须丢线程池，不阻塞事件循环
    summarized, usage = await asyncio.to_thread(rt.summarize, news_rows, ann_rows)

    payload = NewsDigestPayload(
        symbol=symbol,
        news=[_to_item(r) for r in summarized.get("news", [])],
        announcements=[_to_item(r) for r in summarized.get("announcements", [])],
        model=usage,
        news_error=news_error,
        announcements_error=ann_error,
    )
    data = payload.model_dump()
    cache.set(key, data)
    return {"data": data, "meta": {}}
