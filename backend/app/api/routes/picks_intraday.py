"""盘前简报、盘中跟踪与盘后对照 API（选股 2.0 批次 B/C，CONTEXT.md: Daily Picks 域）。

- POST /api/picks/morning-brief/generate  生成/刷新今日盘前简报（写鉴权）
- GET  /api/picks/morning-brief/today     今日简报（含盘中 alerts 与盘后 review）
- GET  /api/picks/watcher/state           盘中跟踪状态（tracker 级明细）
- GET  /api/picks/board-surge             板块异动检测状态（自主发现，2026-09-13 第一期）
- POST /api/picks/watcher/beat            手动推进一拍（写鉴权；取证/调试用）
- GET  /api/picks/intraday-review         近 30 日方向/提醒胜率统计（批次 C）
- POST /api/picks/intraday-review/run     手动执行当日方向对照 + 提醒收益回填（写鉴权）
- GET  /api/picks/intraday-opportunities  盘中机会：题材强→弱 + 题材内个股辨识度/确定性（2026-09-03）
- GET  /api/picks/intraday-top           盘中跟踪最推荐标的（多维筛选切片，2026-09-04）
- GET  /api/picks/kb-routing              场景化 KB 路由表 + 知识库索引覆盖度（2026-09-16，RSH-027）

简报 payload 存 data/picks/briefs/YYYYMMDD.json（morning_brief 模块 docstring
有持久化决策：不进 prediction_reports 表，避免与 predict 按 target_date 的
单键 upsert 互相覆盖；对照结果同样落简报文件，不进 prediction_themes，
避免 apply_verify 按 target_date/theme 撞行与 hit_stats 语义污染）。
"""
from __future__ import annotations

import contextlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import require_write_token
from app.core.bjtime import beijing_now
from app.core.config import settings
from app.core.ttl_cache import cache_on
from app.services.market_snapshot import default_trade_date

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


@router.get("/board-surge")
async def board_surge_state(request: Request) -> dict:
    """板块异动检测状态（2026-09-13 第一期）：当日序列概览 + 最新一拍 Top 板块 + 已提醒事件。

    触发阈值为初始参数（未经实证，见 board_surge.py 模块头）；未启动时如实说明。
    """
    from app.picks.board_surge import todays_state

    if not settings.board_surge_enabled:
        raise HTTPException(status_code=409, detail="board_surge_enabled=False（配置关闭）")
    return {"data": todays_state(request.app), "meta": {}}


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


from app.picks.intraday_opportunity_runtime import (
    build_opportunities as _build_opportunities,
    snapshot_by as _snapshot_by,
)


@router.get("/watch-ledger")
async def watch_ledger(
    request: Request,
    date: str | None = Query(default=None, description="YYYY-MM-DD，缺省=北京今天"),
    days: int = Query(default=5, ge=1, le=30, description="历史天数"),
) -> dict:
    """盘中跟踪台账（猎场批次 A，需求 7/8/9/10/11）：

    当日全量行（tracking + settled，含入选说明与盈亏）+ 近 N 日历史 + 当日统计。
    数据源：盘中 watcher 确认/买点触发/机会候选**首见登记**，收盘复盘自动清算。
    """
    from app.picks.watch_ledger import day_stats, get_day, get_history

    target = date or beijing_now().date().isoformat()
    return {
        "data": {
            "trade_date": target,
            "rows": get_day(target),
            "stats": day_stats(target),
            "history": get_history(days),
        },
        "meta": {},
    }


@router.get("/opportunity-learning")
async def opportunity_learning(
    date: str | None = Query(default=None, description="YYYY-MM-DD，缺省=北京今天"),
) -> dict:
    """个股机会漏斗与结果标签覆盖率；selected 旧口径与全漏斗分母分开（只读）。"""
    from app.picks.opportunity_learning import learning_summary

    target = date or beijing_now().date().isoformat()
    return {"data": learning_summary(target), "meta": {}}


@router.get("/opportunity-learning/replay/{run_id}")
async def replay_opportunity_run(run_id: str) -> dict:
    """仅用归档证据离线重放一次候选/硬门/精排/通知决策。"""
    from app.picks.opportunity_learning import replay_run

    result = replay_run(run_id)
    if not result["records"]:
        raise HTTPException(status_code=404, detail="未找到该决策运行")
    return {"data": result, "meta": {}}


@router.get("/opportunity-scorecard")
async def opportunity_scorecard(
    date: str | None = Query(default=None, description="YYYY-MM-DD，缺省=北京今天"),
    top_k: int = Query(default=5, ge=1, le=50, description="精排队列前 K 名"),
    run_id: str | None = Query(default=None, max_length=64, description="Top-K 指定单一决策 run；缺省选最新匹配 rank run"),
    horizon: str | None = Query(default=None, max_length=16, description="结果标签 horizon；缺省用当前口径"),
    strategy_version: str | None = Query(default=None, max_length=64, description="策略版本；缺省用当前版本"),
    feature_version: str | None = Query(default=None, max_length=64, description="特征版本；缺省用当前版本"),
) -> dict:
    """当日机会决策记分卡：审计行、全漏斗分母与 selected 独立样本分开。

    Top-K 必须绑定单一 run/as-of 并按 symbol 去重；日级 selected 判据按 symbol×trade_date
    去重。指定版本没有漏斗样本时 verdict 为 ``no_matching_denominator``；存在漏斗但 market
    label 未齐时为 ``incomplete_denominator``；标签齐全但任一漏斗机会的决策事实非 ``ready``
    时为 ``degraded_input``。degraded/unavailable/unknown 仍保留审计与分母，但不得进入效果样本。
    `d0_close` 受 A 股 T+1 限制，不得解释为可实现净收益。
    """
    from app.picks.opportunity_learning import (
        FEATURE_VERSION, OUTCOME_HORIZON, STRATEGY_VERSION,
        opportunity_scorecard as _scorecard,
    )

    target = date or beijing_now().date().isoformat()
    return {"data": _scorecard(
        target, top_k=top_k, run_id=run_id,
        horizon=horizon or OUTCOME_HORIZON,
        strategy_version=strategy_version or STRATEGY_VERSION,
        feature_version=feature_version or FEATURE_VERSION,
    ), "meta": {}}


@router.get("/kb-routing")
async def kb_routing(request: Request) -> dict:
    """场景化知识库路由表 + 索引覆盖度（只读，`RSH-027` 切片 1）。

    与 `/opportunity-learning` 的关系：那个回答「归档了什么、KB 引用状态分布如何」
    （`kb_ref_states`），这个回答「**按蓝图 §5，各场景允许调用哪些知识、禁止什么**」
    以及「知识库索引被解析得完整不完整」。

    ⚠️ `index.unparsed_rows` 非空或 `coverage_identity_holds` 为 `false`
    ⇒ 索引表出现了当前解析器不认的新写法（**静默漏条目**的预警信号），
    不是"没有数据"。这正是 `RSH-027` 修掉的那类偏差的可见化。

    路由表与索引都是进程内不变的静态事实（KB 索引只在发版时变），故缓存 300s。
    """
    from app.picks.kb_routing import index_overview, routing_table

    async def build() -> dict:
        return {
            "data": {"scenarios": routing_table(), "index": index_overview()},
            "meta": {"note": "KB 尚未进入个股收益打分；须先通过有/无 KB 影子消融（蓝图 §5）"},
        }

    cache = cache_on(request.app.state, "picks.kb_routing", 300, maxsize=1)
    _, payload = await cache.get_or_set("kb-routing", build)
    return payload


@router.get("/position-labels")
async def position_labels(request: Request) -> dict:
    """闭环「标签」（2026-09-09 用户指令 3）：symbol → sim/real。

    sim = 模拟盘有持仓；real = 真实持仓流水净额 > 0（同股 real 优先——风险等级更高）。
    标签是持仓状态的**派生**：卖出/删流水后自动消失（用户确认卖出→移除标签）。
    """
    state = request.app.state
    engine = getattr(state, "paper", None)
    labels: dict[str, str] = {}
    if engine is not None:
        with contextlib.suppress(Exception):
            for p in engine.positions_with_pnl({}):
                if (p.get("quantity") or 0) > 0:
                    labels[p["symbol"]] = "sim"
    with contextlib.suppress(Exception):
        from sqlalchemy import select

        from app.core.db import get_session_factory
        from app.models.real_position import RealTrade

        with get_session_factory()() as db:
            trades = db.execute(select(RealTrade.symbol, RealTrade.side, RealTrade.quantity)).all()
        net: dict[str, int] = {}
        for sym, side, qty in trades:
            net[sym] = net.get(sym, 0) + (qty if side == "buy" else -qty)
        for sym, qty in net.items():
            if qty > 0:
                labels[sym] = "real"
    return {"data": {"labels": labels}, "meta": {}}


@router.get("/leader-archive")
async def leader_archive(request: Request, theme: str | None = Query(default=None)) -> dict:
    """历史龙头档案（猎场需求 2）：近 30 日涨停池按官方标签聚合的最高连板股。

    theme 传题材名（子串匹配：「代糖」命中「代糖概念」）→ 该题材 top5 龙头；
    不传 → 全档案（题材 → 龙头列表）。缓存 20h（历史档案盘中不变）。
    """
    from app.services.leader_archive import get_archive, leaders_for_theme

    hub = request.app.state.hub
    if theme:
        leaders = await leaders_for_theme(hub.provider, theme)
        return {"data": {"theme": theme, "leaders": leaders}, "meta": {}}
    archive = await get_archive(hub.provider)
    return {"data": archive, "meta": {}}


@router.get("/relay-rank")
async def relay_rank(request: Request) -> dict:
    """接力质量排序（P1-6，2026-09-09）：今日涨停池按 kmid2/max20 排序 → 次日
    接力候选顺序参考。数据支撑：P1-3 池内条件 IC（kmid2 +0.060、max20 +0.052，
    可执行 lag1 口径 1466+ 交易日样本）。红线：只排序+依据，非买卖信号。

    P0-3（2026-09-11）：加 300s 缓存。该结果按**日**变化（池 + 日 K 都是日频），
    而前端按分钟级轮询 ⇒ 原先每轮都把整池逐只日 K 重拉一遍。缓存键含 trade_date，
    跨日自动失效，不会把昨天榜挂到今天。计算本体也已由串行改有界并发（见 relay_rank.py）。
    """
    from app.core.ttl_cache import cache_on
    from app.picks.relay_rank import compute_relay_rank

    hub = request.app.state.hub
    today = beijing_now().date()

    async def build() -> dict:
        items = await compute_relay_rank(hub.provider, today)
        return {"data": {"trade_date": today.isoformat(), "items": items,
                         "basis": "kmid2=当日实体/全距(封得实)；max20=20日最高/现价(近新高)。池内 T+5 RankIC 实证为正（P1-3）"},
                "meta": {}}

    cache = cache_on(request.app.state, "picks.relay_rank", 300, maxsize=4)
    _, payload = await cache.get_or_set(today, build)
    return payload


@router.get("/lurk-pool")
async def lurk_pool(request: Request) -> dict:
    """潜伏观察池（P1-5，2026-09-09）：缩量横盘+试盘+回踩确认票（KB-STOCK-24，
    P1-2 实证 20 日涨停 1.67×）。中线观察参考，非短线买入信号。数据=marketdb
    daily_k（同步 DuckDB 丢线程池，曾卡事件循环同款）。"""
    import asyncio

    from app.picks.lurk_pool import scan_lurk_pool

    out = await asyncio.to_thread(scan_lurk_pool)
    return {"data": out, "meta": {}}


@router.get("/intraday-opportunities")
async def intraday_opportunities(
    request: Request,
    top_themes: int = Query(default=5, ge=1, le=20),
    stocks_per_theme: int = Query(default=8, ge=1, le=30),
) -> dict:
    """盘中机会：先题材（阶段/强度/依据）后题材内个股——**候选与参考分离**。

    2026-09-15 口径变更（用户指令）：`themes[].stocks` 是涨停梯队（已封板，**仅参考**，
    每只带 `tradability` 标注"不可参与"），`themes[].participants` 才是猎场候选
    （该题材内**当前未封板**、可进入参与评估的联动个股；不保证成交）。复用题材梯队看板
    （build_theme_board）+ 热股榜（人气维度），不在本端点重建题材逻辑；
    辨识度/确定性/联动判定规则见 app.picks.intraday_opportunity 与
    app.picks.tradability（纯函数，可回测）。
    热股榜源失败时整体静默降级（hot_available=False，辨识度给 unknown），看板不受影响。
    """
    
    hub = request.app.state.hub
    trade_date = await default_trade_date(hub)
    return await _build_opportunities(request, trade_date, top_themes, stocks_per_theme)


@router.get("/intraday-top")
async def intraday_top(
    request: Request,
    limit: int = Query(default=8, ge=1, le=30),
) -> dict:
    """盘中跟踪「最推荐标的」：opportunities 的多维筛选切片（工作台动态分组口径）。

    2026-09-15 口径变更（用户指令）：`items` = **可参与评估**的题材联动候选（当前未封板且通过门槛；不保证成交）；
    曾封板梯队移入 `reference_items` 作为历史参考，若当前开板并进入 participant 则从参考区去重。
    筛选规则与 tier 语义见 app.picks.intraday_opportunity.top_watch_stocks；
    与复盘（picks 维度）共用同一份口径，保证「分组里看到的」和「复盘对照的」是同一批标的。
    """
    from app.picks.intraday_opportunity import attach_risk_fields, top_watch_stocks

    hub = request.app.state.hub
    trade_date = await default_trade_date(hub)
    payload = await _build_opportunities(request, trade_date, 5, 8)
    data = top_watch_stocks(payload["data"], limit=limit)

    # 2026-09-09 用户需求「盘中跟踪卡片与每日精选一致」：补现价/止损参考/出场纪律
    # （与 PickCard 分节同构；现价来自全市场快照，缺失显式 null 不臆造）。
    # 2026-09-10：改为与题材手风琴共用同一实现。
    # 2026-09-15：参考区（涨停梯队）**同样**补全——它也是要给人看的卡片，
    # 不能因为是"参考"就少字段（此前只有 items 走这一步，正是当年反馈的同类问题）。
    snap_by = _snapshot_by(request)
    attach_risk_fields(data.get("items") or [], snap_by)
    attach_risk_fields(data.get("reference_items") or [], snap_by)
    return {"data": data, "meta": {}}
