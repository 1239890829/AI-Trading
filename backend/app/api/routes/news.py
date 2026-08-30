"""新闻/公告摘要端点。

规则摘要器永远可用（零外部依赖），LLM 配置后自动升级、失败自动降级，
`model` 字段回传实际使用的摘要器与降级原因，前端据此标注来源。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_hub
from app.core.config import settings
from app.news.llm import LLMSummarizer
from app.news.router import SummaryRouter
from app.schemas.envelope import Envelope
from app.schemas.news_digest import DigestItem, NewsDigestPayload

router = APIRouter(tags=["news"])


def _router_cached(hub, name: str) -> dict:
    attr = f"_cache_{name}"
    cache = getattr(hub, attr, None)
    if cache is None:
        cache = {}
        setattr(hub, attr, cache)
    return cache


def _ttl_hit(cache: dict, key) -> tuple[bool, object]:
    hit = cache.get(key)
    if hit and time.monotonic() - hit[0] < 60:
        return True, hit[1]
    return False, None


def _to_item(row: dict) -> DigestItem:
    """规则摘要器返回的是「原字段 + 摘要字段」的合并字典，直接用即可。

    切勿拿原始 rows 与结果 zip 配对——结果已按重要度重排，配对会张冠李戴。
    """
    known = {k: row[k] for k in row if k in DigestItem.model_fields}
    return DigestItem(**known)


@router.get("/news/digest/{symbol}", response_model=Envelope[NewsDigestPayload])
async def news_digest(
    symbol: str,
    limit: int = Query(default=10, ge=1, le=30),
    hub=Depends(get_hub),
) -> dict:
    """个股新闻+公告摘要：重要度分级、消息面情绪、事实摘要、关键数字。

    按重要度倒序返回。摘要只做事实抽取，不含任何买卖建议。
    """
    cache = _router_cached(hub, "news_digest")
    key = (symbol, limit)
    hit, cached = _ttl_hit(cache, key)
    if hit:
        return {"data": cached, "meta": {"cached": True}}

    try:
        news_rows = await hub.provider.get_news(symbol, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"新闻数据源失败：{exc}")
    try:
        ann_rows = await hub.provider.get_announcements(symbol, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"公告数据源失败：{exc}")

    rt = SummaryRouter(
        requested=settings.news_model,
        llm=LLMSummarizer(
            base_url=settings.news_llm_base_url,
            api_key=settings.news_llm_api_key,
            model=settings.news_llm_model,
        ),
    )
    summarized, usage = rt.summarize(news_rows, ann_rows)

    payload = NewsDigestPayload(
        symbol=symbol,
        news=[_to_item(r) for r in summarized.get("news", [])],
        announcements=[_to_item(r) for r in summarized.get("announcements", [])],
        model=usage,
    )
    data = payload.model_dump()
    cache[key] = (time.monotonic(), data)
    return {"data": data, "meta": {}}
