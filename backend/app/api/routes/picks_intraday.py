"""盘前简报、盘中跟踪与盘后对照 API（选股 2.0 批次 B/C，CONTEXT.md: Daily Picks 域）。

- POST /api/picks/morning-brief/generate  生成/刷新今日盘前简报（写鉴权）
- GET  /api/picks/morning-brief/today     今日简报（含盘中 alerts 与盘后 review）
- GET  /api/picks/watcher/state           盘中跟踪状态（tracker 级明细）
- POST /api/picks/watcher/beat            手动推进一拍（写鉴权；取证/调试用）
- GET  /api/picks/intraday-review         近 30 日方向/提醒胜率统计（批次 C）
- POST /api/picks/intraday-review/run     手动执行当日方向对照 + 提醒收益回填（写鉴权）
- GET  /api/picks/intraday-opportunities  盘中机会：题材强→弱 + 题材内个股辨识度/确定性（2026-09-03）
- GET  /api/picks/intraday-top           盘中跟踪最推荐标的（多维筛选切片，2026-09-04）

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


def _snapshot_by(request: Request) -> dict[str, dict]:
    """全市场快照 symbol → 行（现价来源）。取不到返回空 dict——三态降级，不臆造。"""
    out: dict[str, dict] = {}
    try:
        for row in getattr(request.app.state.snapshot_service, "snapshot", None) or []:
            if row.get("symbol"):
                out[row["symbol"]] = row
    except Exception:  # noqa: BLE001 — 快照不可用不拖垮列表，现价显式 null
        pass
    return out


def _attach_risk_to_themes(data: dict, snap_by: dict[str, dict]) -> None:
    """题材手风琴路径（/intraday-opportunities）的个股补现价/止损/出场。

    与 /intraday-top 共用 ``attach_risk_fields`` 同一份实现——此前该项补全只写在
    intraday-top 路由里，于是同一张选股卡片（PickCard）在两条数据源上字段丰度不同
    （2026-09-10 用户反馈「其他板块打开的字段不一样」）。
    """
    from app.picks.intraday_opportunity import attach_risk_fields

    for th in data.get("themes") or []:
        attach_risk_fields(th.get("stocks") or [], snap_by)


async def _build_opportunities(
    request: Request, trade_date, top_themes: int, stocks_per_theme: int
) -> dict:
    """opportunities payload 构建（两处端点共用：全量视图 + 盘中 top 筛选）。

    读 Parquet 是同步阻塞，丢线程池（market.themes 同款处理，曾卡死事件循环）；
    结果缓存 60s（cache key 含参数，两端点同 key 命中同一份）。
    """
    import asyncio

    from app.core.ttl_cache import cache_on
    from app.picks.intraday_opportunity import assemble
    from app.services.theme_service import _pick_provider, build_theme_board

    hub = request.app.state.hub
    from app.api.routes.market import _load_snapshot_map

    cache = cache_on(request.app.state, "picks.opportunities", 60, maxsize=4)
    key = (trade_date, top_themes, stocks_per_theme)
    hit, payload = cache.get(key)
    if hit:
        # 装配结果命中缓存，但现价/止损每拍都在变 ⇒ 风险字段不进缓存，此处按当前快照重算
        _attach_risk_to_themes(payload["data"], _snapshot_by(request))
        return payload

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
    # 簇级官方概念挂靠（09-08「代糖/玉米搜不到」修复）：猎场手风琴与工作台
    # 题材归属由此获得 official_matches（如「功能糖」→官方「代糖概念/玉米」）；
    # 缓存前写入，两端点共用同一份。
    from app.services.official_match import attach_official

    attach_official(request, payload["data"].get("themes") or [])

    # 猎场批次 A（需求 7）+ 2026-09-09 收紧（用户：跟踪过多且缺乏依据）：
    # 机会候选登记加**量化硬门槛**，避免盲目大面积跟踪——
    #   ① 只在交易时段登记（非交易时段端点被调用不产生台账数据）
    #   ② 候选须满足任一：已涨停(boards≥1) / 涨幅≥5% / 判定 certainty=高
    # watcher 确认与买点触发（dispatch_alert 路径）不受此门槛限制（本就是强信号）。
    with contextlib.suppress(Exception):
        from app.market.trading_status import beijing_now, in_trading_window
        from app.picks.watch_ledger import record_sighting

        now = beijing_now()
        if in_trading_window(now):
            tdate = now.date().isoformat()
            tstamp = now.strftime("%H:%M:%S")
            registered = 0
            for th in payload["data"].get("themes") or []:
                layer = "today_strongest" if th.get("strength_tier") in ("领涨", "强势") else "quiet_starting"
                for s in th.get("stocks") or []:
                    if not s.get("symbol"):
                        continue
                    boards = s.get("boards") or 0
                    pct = s.get("change_pct")
                    cert_high = (s.get("certainty") or {}).get("level") == "高"
                    # KB-DEC-011（2026-09-09 用户指令，修订 KB-DEC-008）：涨停前识别才准入——
                    # ① 已封板的候选一律不入册（封板后发现的=迟到，boards≥1 不再是准入条件）；
                    # ② 未封板但未达临板区（板性×0.65）且判定不足——不跟踪
                    from app.picks.pre_limit_radar import board_limit_pct, is_sealed, pre_limit_floor

                    limit_pct = board_limit_pct(str(s["symbol"]), str(s.get("name") or ""))
                    if pct is None or is_sealed(float(pct), limit_pct):
                        continue
                    if float(pct) < pre_limit_floor(limit_pct) and not cert_high:
                        continue  # 未进临板区且判定不足——不跟踪
                    record_sighting(
                        trade_date=tdate, symbol=str(s["symbol"]),
                        name=str(s.get("name") or ""), layer=layer,
                        source_theme=str(th.get("theme") or ""),
                        reason={
                            "theme": th.get("theme"), "stage": th.get("stage"),
                            "tier": th.get("strength_tier"), "role": s.get("role"),
                            "certainty": s.get("certainty"),
                            "gate": f"pre_limit pct={pct} floor={pre_limit_floor(limit_pct)} limit={limit_pct:.0f}cm cert_high={cert_high}",
                            "basis": (s.get("reason") or "")[:200],
                        },
                        is_leader=s.get("role") in ("龙头", "空间板"),
                        boards=boards,
                        entry_price=None,  # 登记时以告警触发价优先；此处无价格由清算兜底
                        entry_time=tstamp,
                    )
                    registered += 1
            if registered:
                log.info("watch ledger: %d candidates registered (gate: 临板区 or cert=高, 未封板——KB-DEC-011)", registered)
    cache.set(key, payload)
    # 同上：缓存的是装配结果（题材/判定/依据），风险字段按本次快照另行补全
    _attach_risk_to_themes(payload["data"], _snapshot_by(request))
    return payload


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
    from app.market.trading_status import beijing_now
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
    """
    from app.market.trading_status import beijing_now
    from app.picks.relay_rank import compute_relay_rank

    hub = request.app.state.hub
    today = beijing_now().date()
    items = await compute_relay_rank(hub.provider, today)
    return {"data": {"trade_date": today.isoformat(), "items": items,
                     "basis": "kmid2=当日实体/全距(封得实)；max20=20日最高/现价(近新高)。池内 T+5 RankIC 实证为正（P1-3）"},
            "meta": {}}


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
    """盘中机会：先题材（阶段/强度/依据）后题材内个股（辨识度/确定性 + 判定依据）。

    复用题材梯队看板（build_theme_board）+ 热股榜（人气维度），不在本端点重建题材
    逻辑；辨识度/确定性判定规则见 app.picks.intraday_opportunity（纯函数，可回测）。
    热股榜源失败时整体静默降级（hot_available=False，辨识度给 unknown），看板不受影响。
    """
    from app.api.routes.market import _default_trade_date_async

    hub = request.app.state.hub
    trade_date = await _default_trade_date_async(hub)
    return await _build_opportunities(request, trade_date, top_themes, stocks_per_theme)


@router.get("/intraday-top")
async def intraday_top(
    request: Request,
    limit: int = Query(default=8, ge=1, le=30),
) -> dict:
    """盘中跟踪「最推荐标的」：opportunities 的多维筛选切片（工作台动态分组口径）。

    筛选规则与 tier 语义见 app.picks.intraday_opportunity.top_watch_stocks
    （确定性优先、辨识度次之，unknown/低不入选）；与复盘（picks 维度）共用
    同一份口径，保证「分组里看到的」和「复盘对照的」是同一批标的。
    """
    from app.api.routes.market import _default_trade_date_async
    from app.picks.intraday_opportunity import attach_risk_fields, top_watch_stocks

    hub = request.app.state.hub
    trade_date = await _default_trade_date_async(hub)
    payload = await _build_opportunities(request, trade_date, 5, 8)
    data = top_watch_stocks(payload["data"], limit=limit)

    # 2026-09-09 用户需求「盘中跟踪卡片与每日精选一致」：补现价/止损参考/出场纪律
    # （与 PickCard 分节同构；现价来自全市场快照，缺失显式 null 不臆造）。
    # 2026-09-10：改为与题材手风琴共用同一实现——此前是内联在这里的独有逻辑，
    # 导致同一张选股卡片（PickCard）在 /intraday-opportunities 路径上缺这三项。
    # top_watch_stocks 会重建 item dict，故补全须在本函数内对它自己的 items 做一次。
    attach_risk_fields(data.get("items") or [], _snapshot_by(request))
    return {"data": data, "meta": {}}
