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
from app.core.inflight_reads import LocalReadCapacityExceeded
from app.services.source_reads import read_news_input
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

    async def _build() -> dict:
        # 数据源单侧失败降级而非整体 502：新闻源（东财搜索）有间歇软封锁，
        # 若整体失败会把同时可用的公告也吃掉；错误原因显式透出（news_error/
        # announcements_error 非空 = 该侧已降级），绝不静默空列表。双侧都挂才 502。
        async def _read(method: str):
            try:
                return await read_news_input(hub.provider, method, symbol, limit), None
            except LocalReadCapacityExceeded:
                raise
            except Exception as exc:
                return [], str(exc)

        # These sources are independent; retain each side's failure separately.
        results = await asyncio.gather(
            _read("get_news"), _read("get_announcements"), return_exceptions=True,
        )
        for value in results:
            if isinstance(value, BaseException):
                raise value
        (news_rows, news_error), (ann_rows, ann_error) = results
        if news_error and ann_error:
            raise HTTPException(
                status_code=502,
                detail=f"新闻与公告数据源均失败：news({news_error})；announcements({ann_error})",
            )

        def _usage_reporter(receipt: dict) -> None:
            from app.services import agent_budget
            agent_budget.record_unmetered(
                purpose="news.llm", provider=settings.llm_provider, model=settings.news_llm_model,
                state=str(receipt.get("state") or "failed"),
                usage=receipt.get("usage") if isinstance(receipt.get("usage"), dict) else None,
                attempts=max(1, int(receipt.get("attempts") or 1)),
                timeout_seconds=float(receipt.get("timeout_seconds") or 0.0),
                input_chars=max(0, int(receipt.get("input_chars") or 0)),
                output_chars=max(0, int(receipt.get("output_chars") or 0)),
                error_kind=(str(receipt.get("error_kind")) if receipt.get("error_kind") else None),
            )

        rt = SummaryRouter(
            requested=settings.news_model,
            llm=LLMSummarizer(
                base_url=settings.news_llm_base_url,
                api_key=settings.news_llm_api_key,
                model=settings.news_llm_model,
                provider=settings.llm_provider,
                cli_path=settings.llm_cli_path,
                usage_reporter=_usage_reporter,
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
        return data

    # Share the whole cold pipeline, including a failed in-flight result. A
    # completed failure is removed immediately, so a later request can recover.
    pending = getattr(request.app.state, "_news_digest_reads", None)
    if pending is None:
        pending = {}
        request.app.state._news_digest_reads = pending
    task = pending.get(key)
    if task is None or task.done():
        task = asyncio.create_task(cache.get_or_set(key, _build))
        pending[key] = task

        def completed(done: asyncio.Task) -> None:
            if pending.get(key) is done:
                pending.pop(key, None)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(completed)
    # A disconnected caller must not cancel other callers or the summary thread.
    hit, data = await asyncio.shield(task)
    return {"data": data, "meta": {"cached": True} if hit else {}}
