"""盘前简报、盘中跟踪与盘后对照 API（选股 2.0 批次 B/C，CONTEXT.md: Daily Picks 域）。

- POST /api/picks/morning-brief/generate  生成/刷新今日盘前简报（写鉴权）
- GET  /api/picks/morning-brief/today     今日简报（含盘中 alerts 与盘后 review）
- GET  /api/picks/watcher/state           盘中跟踪状态（tracker 级明细）
- POST /api/picks/watcher/beat            手动推进一拍（写鉴权；取证/调试用）
- GET  /api/picks/intraday-review         近 30 日方向/提醒胜率统计（批次 C）
- POST /api/picks/intraday-review/run     手动执行当日方向对照 + 提醒收益回填（写鉴权）
- GET  /api/picks/intraday-opportunities  盘中机会：题材强→弱 + 题材内个股辨识度/确定性（2026-09-03）

简报 payload 存 data/picks/briefs/YYYYMMDD.json（morning_brief 模块 docstring
有持久化决策：不进 prediction_reports 表，避免与 predict 按 target_date 的
单键 upsert 互相覆盖；对照结果同样落简报文件，不进 prediction_themes，
避免 apply_verify 按 target_date/theme 撞行与 hit_stats 语义污染）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import require_write_token
from app.core.config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/picks", tags=["picks-intraday"])


@router.post("/morning-brief/generate")
async def generate_morning_brief(
    request: Request, _: None = Depends(require_write_token)
) -> dict:
    """生成/刷新今日盘前简报（覆盖当日文件；盘中 alerts 会丢——重跑前先想清楚）。"""
    from app.picks.morning_brief import build_and_save

    payload = await build_and_save(request.app, trigger="manual")
    return {"data": payload, "meta": {}}


@router.get("/morning-brief/today")
async def today_morning_brief() -> dict:
    from app.picks.morning_brief import brief_for_today

    target, payload = brief_for_today()
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"今日（{target}）尚无盘前简报：POST /api/picks/morning-brief/generate，或等 08:40 调度",
        )
    return {"data": payload, "meta": {}}


@router.get("/watcher/state")
async def watcher_state(request: Request) -> dict:
    """盘中跟踪状态。watcher 未启动时说明原因（开关关 / 无当日简报）。"""
    watcher = getattr(request.app.state, "picks_watcher", None)
    if watcher is None:
        _, payload = None, None
        from app.picks.morning_brief import brief_for_today

        _, payload = brief_for_today()
        return {
            "data": {
                "active": False,
                "enabled": settings.picks_watcher_enabled,
                "brief_exists": payload is not None,
                "note": (
                    "watcher 空转：今日无盘前简报（先生成简报）"
                    if payload is None
                    else "watcher 尚未推进（未到交易时段或下个拍未到）"
                ),
            },
            "meta": {},
        }
    return {"data": watcher.state(), "meta": {}}


@router.post("/watcher/beat")
async def watcher_beat(request: Request, _: None = Depends(require_write_token)) -> dict:
    """手动推进一拍：取数 → 全部 tracker step → 分发提醒。

    与 watcher_loop 走同一套代码路径（ensure_watcher / collect_beat_inputs /
    dispatch_alert），用于盘后取证、规则验证与演示——不是第二条逻辑。
    """
    from app.picks.watcher import collect_beat_inputs, dispatch_alert, ensure_watcher

    watcher = ensure_watcher(request.app)
    if watcher is None:
        raise HTTPException(
            status_code=404,
            detail="今日无盘前简报，无方向可跟踪：先 POST /api/picks/morning-brief/generate",
        )
    env_cache = getattr(request.app.state, "picks_env_cache", None) or {"at": 0.0, "env": None}
    request.app.state.picks_env_cache = env_cache
    beat = await collect_beat_inputs(
        request.app, env_cache, env_refresh_seconds=settings.picks_watcher_env_refresh_seconds
    )
    alerts = watcher.step(beat)
    dispatched = []
    for a in alerts:
        dispatched.append({"key": a.get("key"), "dispatched": await dispatch_alert(request.app, a)})
    request.app.state.picks_watcher = watcher  # 手动拍挂回 state，GET state 可见
    return {
        "data": {
            "beat": {
                "now_minutes": beat.get("now_minutes"),
                "trading": beat.get("trading"),
                "pool_count": beat.get("pool_count"),
                "board_count": beat.get("board_count"),
                "env": beat.get("env"),
                "themes_count": len(beat.get("themes") or {}),
            },
            "alerts": dispatched,
            "state": watcher.state(),
        },
        "meta": {},
    }


# ---------------------------------------------------------------- 盘后对照（批次 C）


@router.get("/intraday-review")
async def intraday_review(limit: int = Query(default=30, ge=1, le=90)) -> dict:
    """近 limit 个简报日的方向四分类与提醒 T+1/T+3 胜率统计（§7.2）。

    统计读 data/picks/briefs/*.json 聚合（文件持久化决策见模块 docstring）；
    未复盘/未到期的提醒在返回里显式 pending，绝不冒充已验证。
    """
    from app.picks.review_intraday import intraday_stats

    return {"data": intraday_stats(limit), "meta": {}}


@router.post("/intraday-review/run")
async def run_intraday_review(
    request: Request, _: None = Depends(require_write_token)
) -> dict:
    """手动执行当日方向对照 + 全量提醒收益回填（15:35 调度的同代码路径）。

    09:25 前拒绝（当日盘面未形成，对照只会产出垃圾）；当日无简报 404。
    """
    from app.picks.review_intraday import run_review

    result = await run_review(request.app, trigger="manual")
    if not result.get("ok"):
        reason = result.get("reason")
        detail = result.get("detail") or "当日无盘前简报：先 POST /api/picks/morning-brief/generate"
        if reason == "no_brief":
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=409, detail=detail)
    return {"data": result, "meta": {}}


# ---------------------------------------------------------------- 盘中机会视图


@router.get("/intraday-opportunities")
async def intraday_opportunities(
    request: Request,
    top_themes: int = Query(default=5, ge=1, le=20),
    stocks_per_theme: int = Query(default=8, ge=1, le=30),
) -> dict:
    """盘中机会：先题材（阶段/强度/依据）后题材内个股（辨识度/确定性 + 判定依据）。

    复用题材梯队看板（build_theme_board）+ 热股榜（人气维度），不在本端点重建题材
    逻辑；辨识度/确定性判定规则见 app.picks.intraday_opportunity（纯函数，可回测）。
    热股榜源失败时整体静默降级（hot_available=False，辨识度给 unknown），看板不受影响。
    结果缓存 60s。
    """
    import asyncio
    from datetime import date as _date

    from app.core.ttl_cache import cache_on
    from app.picks.intraday_opportunity import assemble
    from app.services.theme_service import _pick_provider, build_theme_board

    hub = request.app.state.hub
    from app.api.routes.market import _default_trade_date_async, _load_snapshot_map

    trade_date: _date = await _default_trade_date_async(hub)
    cache = cache_on(request.app.state, "picks.opportunities", 60, maxsize=4)
    key = (trade_date, top_themes, stocks_per_theme)
    hit, payload = cache.get(key)
    if hit:
        return payload

    # 读 Parquet 是同步阻塞，丢线程池（market.themes 同款处理，曾卡死事件循环）
    snapshot_map = await asyncio.to_thread(_load_snapshot_map, request, trade_date)
    board = await build_theme_board(hub.provider, trade_date, snapshot_map=snapshot_map)

    hot_rows: list[dict] = []
    hot_available = False
    ths = _pick_provider(hub.provider, "ThsFuyaoProvider")
    if ths is not None:
        try:
            hot_rows = (await ths.get_hot_stock_list("day"))[:50]
            hot_available = bool(hot_rows)
        except Exception:  # noqa: BLE001 — 人气维度失败不拖垮机会视图，只降级
            log.warning("hot stock list unavailable, distinctiveness degrades to unknown")

    payload = {
        "data": assemble(
            board, hot_rows, hot_available, top_themes=top_themes, stocks_per_theme=stocks_per_theme
        ),
        "meta": {},
    }
    cache.set(key, payload)
    return payload
