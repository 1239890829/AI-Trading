"""市场情绪与宽度：情绪分、连板梯队、情绪历史、宽度、热力图。

（自 `market.py` 切出，2026-09-15 IMP-005 批 3。**只搬位置，未改逻辑**：
分片正文与原文件对应定义逐字相同。跨分片共用的信封辅助在 `market_envelope`。）
"""

from __future__ import annotations

import logging
import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
from app.schemas.envelope import (
    BreadthData,
    Envelope,
    SentimentHistoryItem,
    SentimentHistoryPayload,
    SentimentPayload,
)
from app.services.quote_hub import QuoteHub
from app.core.bjtime import beijing_now_naive

router = APIRouter(tags=["market"])
log = logging.getLogger("app.api.routes.market")

from app.api.routes.market_envelope import (
    meta_payload,
)


@router.get("/market/sentiment", response_model=Envelope[SentimentPayload])
async def market_sentiment(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    """情绪周期判定（§5.5）：阶段+温度+指标依据+置信度+误判原因+切换条件+次日验证项。

    计算逻辑在 `app.services.market_context.compute_market_sentiment`，
    与复盘 Agent 共用同一实现——口径只有一个，避免两边漂移。
    结果走**共享 60s 缓存槽**（P1-3：`get_cached_sentiment`，与题材相位、
    介入条件清单、猎场相位路由、事件排序、风控刷新同一槽），本端点只负责
    套上行情 meta 信封——`meta.generated_at` 是**本次响应**的时刻，与
    领域对象的计算时刻（缓存命中时可能早 60s）本就不同，不能混为一谈。
    """
    from app.services.market_context import CalendarUnavailable, get_cached_sentiment

    try:
        result = await get_cached_sentiment(request.app.state, hub)
    except CalendarUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"data": result, "meta": meta_payload(hub)}


_sent_hist_backfilled = {"done": False, "retry_after": 0.0}


@router.get("/market/ladder-check")
async def ladder_check(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    """B4 数据源自证：ths 连板天梯 seal_nextday 交叉验证自算晋级率。

    可验 2进3 与高位存活（首板不在天梯，1进2 不可验）。逐日给出
    match/drift/insufficient，drift 说明两日池拼接逻辑有问题（服务端同时 warning）。
    结果缓存 10 分钟（数据日频更新）。
    """
    from app.sentiment.ladder_check import run_ladder_check

    cache = cache_on(request.app.state, "sentiment.ladder_check", 600, maxsize=1)
    hit, payload = cache.get(())
    if hit:
        return payload
    try:
        data = await run_ladder_check(hub)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"天梯交叉验证失败：{exc}") from exc
    payload = {"data": data, "meta": meta_payload(hub)}
    cache.set((), payload)
    return payload


@router.get("/market/sentiment-history", response_model=Envelope[SentimentHistoryPayload])
async def market_sentiment_history(
    request: Request,
    hub: QuoteHub = Depends(get_hub),
    days: int = Query(default=10, ge=2, le=60),
) -> dict:
    """情绪周期序列（retro #17）：近 N 个交易日情绪判定 + 周期起点定位。

    数据三路合一（口径全部来自 compute_market_sentiment，与实时端点一致）：
    ① 复盘报告回填（进程内一次，幂等只补缺）；② 惰性补录——交易日 15:00 后
    缺当日记录则现算落库；③ 历史表已有数据。已存在的日期不覆盖。
    """
    from app.core.db import get_session_factory
    from app.market.sentiment_history import (
        backfill_from_reports,
        get_history,
        locate_cycle,
        upsert_if_absent,
    )

    sf = get_session_factory()

    # ① 历史报告回填（进程内只跑一次；表空且无报告时零成本）
    backfilled = 0
    if not _sent_hist_backfilled["done"] and time.time() >= _sent_hist_backfilled["retry_after"]:
        try:
            backfilled = backfill_from_reports(sf)
            # **只有成功才置 done**：原实现无论成败都置 True，而这是进程级一次标志，
            # 一次瞬时失败（DB 忙 / 报告目录未就绪）会让历史回填在本次进程生命周期内
            # 永不重试 —— 用户看到「历史序列只有最近几天」却无从判断是本就无数据
            # 还是回填坏了（数字出得来、结论是错的，属"错了也看不出来"）。
            _sent_hist_backfilled["done"] = True
        except Exception:
            _sent_hist_backfilled["retry_after"] = time.time() + 600.0  # 退避 10 分钟
            log.warning("sentiment history backfill failed（10 分钟后重试）", exc_info=True)

    # ② 惰性补录：交易日 15:05 后缺当日记录 → 现算落库
    now_bj = beijing_now_naive()
    today_key = now_bj.strftime("%Y%m%d")
    try:
        from app.market.trade_calendar import is_trade_day_on, trading_days

        days_list = await trading_days(hub.provider)
        # 三态写法（2026-09-14，账本 §6.6 行 14）：`now.date() in days_list` 是**二态**
        # 判定，而源头日历是**尾随窗口**（不含未来日期）——双源失败 + 限频窗口下拿到
        # 旧快照（末日停在上一交易日）时，`in` 会把「**未判定**」塌缩成「**确认非交易日**」，
        # 静默跳过 15:05 的惰性补录**且无任何告警**（与 F7 六处同一反模式）。
        # 与下面的 except 分支**同口径**：真不确定时退回「工作日即交易日」的乐观判据
        # ——宁可多重算一次（`upsert_if_absent` 幂等，已有记录不会重复落），
        # 不可静默漏一天。
        state = is_trade_day_on(now_bj.date(), days_list)
        trade_day_ok = (now_bj.weekday() < 5) if state is None else state
    except Exception:
        trade_day_ok = now_bj.weekday() < 5
    if trade_day_ok and (now_bj.hour, now_bj.minute) >= (15, 5):
        try:
            from app.services.market_context import get_cached_sentiment

            existing = {h["trade_date"] for h in get_history(sf, days=days)}
            if today_key not in existing:
                # 走共享槽：落库的相位与同一时刻界面展示的相位必须同源，
                # 否则"盘后补录"会写出一个用户从没见过的相位（P1-3）。
                result = await get_cached_sentiment(request.app.state, hub)
                entry = {
                    "trade_date": today_key,
                    "phase": result.get("phase") or "分歧",
                    "temperature": result.get("temperature"),
                    "confidence": result.get("confidence"),
                    "phase_unreliable": bool(result.get("phase_unreliable")),
                    "source": "live",
                    "detail": result,
                }
                if upsert_if_absent(sf, entry):
                    backfilled += 1
        except Exception:
            log.warning("sentiment history lazy capture failed", exc_info=True)

    # ③ 序列 + 周期定位
    history = get_history(sf, days=days)
    cycle = locate_cycle(history)
    payload = SentimentHistoryPayload(
        items=[SentimentHistoryItem(**h) for h in history],
        cycle=cycle,
        backfilled=backfilled,
        notes=[
            "序列自功能上线起积累；复盘报告里已有的历史判定会自动回填",
            "周期起点 = 最近一次 强(回暖/升温/高潮)·中(分歧)·弱(退潮/冰点) 分段切换日",
        ],
    )
    return {"data": payload, "meta": meta_payload(hub)}


@router.get("/market/breadth", response_model=Envelope[BreadthData])
async def market_breadth(request: Request) -> dict:
    """市场宽度：涨跌家数、涨跌停家数、两市成交额（来源：新浪全市场快照）。"""
    svc = request.app.state.snapshot_service
    payload = svc.breadth_payload()
    if payload["breadth"] is None:
        raise HTTPException(status_code=503, detail="全市场快照尚未就绪（冷启动抓取约需数秒）")
    return {"data": payload, "meta": meta_payload(request.app.state.hub)}


@router.get("/market/heatmap")
async def market_heatmap(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    """A 股云图载荷：全市场快照 × 行业映射（TDX HY）→ 分组 treemap 数据。

    - 面积权重 = 流通市值（快照 nmc）；颜色 = 当日涨跌幅；
    - 组内仅保留流通市值 Top 12，其余并入「其他(n只)」聚合块（市值加权涨跌幅）；
    - 行业映射 24h 缓存，TDX 不可用时个股归「未分类」并在 industry_coverage 标注覆盖率。
    """
    svc = request.app.state.snapshot_service
    rows = svc.snapshot or []
    if not rows:
        raise HTTPException(status_code=503, detail="全市场快照尚未就绪（冷启动抓取约需数秒）")

    cache = cache_on(request.app.state, "market.heatmap", 60, maxsize=1)
    hit, payload = cache.get(())
    if hit:
        return payload

    from app.services.heatmap_service import build_heatmap, get_industry_map_async

    industry_map = await get_industry_map_async()
    data = build_heatmap(rows, industry_map)
    payload = {"data": data, "meta": meta_payload(hub)}
    cache.set((), payload)
    return payload
