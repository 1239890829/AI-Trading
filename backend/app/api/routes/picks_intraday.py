"""盘前简报与盘中跟踪 API（选股 2.0 批次 B，CONTEXT.md: Daily Picks 域）。

- POST /api/picks/morning-brief/generate  生成/刷新今日盘前简报（写鉴权）
- GET  /api/picks/morning-brief/today     今日简报（含盘中追加的 alerts）
- GET  /api/picks/watcher/state           盘中跟踪状态（tracker 级明细）
- POST /api/picks/watcher/beat            手动推进一拍（写鉴权；取证/调试用）

简报 payload 存 data/picks/briefs/YYYYMMDD.json（morning_brief 模块 docstring
有持久化决策：不进 prediction_reports 表，避免与 predict 按 target_date 的
单键 upsert 互相覆盖）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

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
