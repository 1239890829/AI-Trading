"""每日精选 API（CONTEXT.md: Daily Picks 域）。

- GET  /api/picks/today           当日组合（从库读；不自动生成——生成较重，POST 触发）
- POST /api/picks/generate        生成当日组合（写鉴权；当日调度或手动触发）
- GET  /api/picks/history         历史组合（近 N 日）
- GET  /api/picks/review?date=    复盘日志
- POST /api/picks/review/generate 按表现日读取或生成同版本价格观察（写鉴权）
- GET  /api/picks/meta            走坏原因分布（周末权重微调建议的输入）

数据编排（候选池 → 六维评分 → 门槛 → 落库）**已抽到 `services/picks_pipeline.py`**
（S2-4，2026-09-11）：本模块只做 HTTP 装配与读侧查询，管线可被调度/脚本直接复用。

名单长度声明（2026-09-10）：**盘前选择不是"每天 5 只"**。MAX_PICKS 是容量上限，
实际只数 = 当日达到入选门槛的标的数（可能 0~5，弱市就该更短）。语义上与盘中跟踪的
`top_watch_stocks`（unknown/低不入选）对齐，只是筛选依据换成六维综合分。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import get_hub, require_write_token
from app.core.bjtime import beijing_today
from app.core.db import get_session_factory
from app.services.picks_pipeline import (
    PipelineDeps,
    generate_picks_pipeline,
    parse_pick_meta,
)
from app.services.quote_hub import QuoteHub

log = logging.getLogger(__name__)
router = APIRouter(prefix="/picks", tags=["daily-picks"])


def _db():
    # Session 实例（支持 with 自动 close）；sessionmaker 本身不支持 with（AGENTS.md 6.3）
    return get_session_factory()()


def _attach_latest_execution(items: list[dict], trade_date: str, meta: dict | None = None) -> list[dict]:
    """给每日精选挂最新一版执行复核事实；纯读，不触发新判定/新样本（IMP-006）。"""
    try:
        from app.picks.opportunity_learning import latest_notification_execution

        latest = latest_notification_execution(trade_date, get_session_factory())
    except Exception as exc:  # noqa: BLE001 — 执行证据读失败不能拖垮每日精选
        log.warning("picks latest execution read failed: %s", exc)
        latest = {}
    from app.picks.selection_entry import attach_daily_entries

    from app.services.tradingagents_research import attach_saved_summaries

    return attach_saved_summaries(attach_daily_entries([
        {**it, "execution": latest.get(str(it.get("symbol") or ""))}
        for it in (items or [])
        if isinstance(it, dict)
    ], meta or {}), session_factory=get_session_factory())


@router.post("/generate")
async def generate_picks(request: Request, hub: QuoteHub = Depends(get_hub), _: None = Depends(require_write_token)) -> dict:
    """生成当日持久候选（自动09:26起生成；受控重生覆盖当日行）。

    S2-4：管线本体已抽到 `services/picks_pipeline.py`，本路由只做依赖装配。
    常驻调度（`picks/picks_autogen.py`）直接调服务，不再反向 import 本模块、
    也不再伪造 `SimpleNamespace` 当 request。
    """
    return await generate_picks_pipeline(PipelineDeps.from_state(request.app.state), hub)


async def _live_style_routing(request: Request, hub: QuoteHub, stored: dict | None) -> dict:
    """相位→风格路由按**读取时刻**重算（共享 60s 情绪槽）。

    2026-09-09 修：style_routing 原本只在组合生成时算一次并持久化进 meta，
    全天定格——重启补跑若赶上全市场快照未就绪（breadth=None → CalendarUnavailable），
    phase=None 会被静默落库，「相位缺失·未路由」挂一整天（09-09 实测如此）。
    风格路由语义是「当日微调」，本就该用实时相位；生成时刻快照保留在
    meta.market_phase 作对照，用 phase_source 标明来源（三态纪律：来源显式，
    未知不伪装成「均衡」）。

    P1-3（2026-09-11）：原先此处自建 `picks.live_sentiment` 槽（与市场页
    `market.sentiment` 各算一次），现已并入 `get_cached_sentiment` 的共享槽——
    同一 60s 窗口内猎场与市场页共用一次计算。槽里存的是**领域对象**，
    信封结构由各端点自包，故原先"信封形状不同不能复用"的顾虑不再成立。
    """
    from app.picks.style_router import route_style
    from app.services.market_context import CalendarUnavailable, get_cached_sentiment

    try:
        sent = await get_cached_sentiment(request.app.state, hub)
    except CalendarUnavailable as exc:
        out = dict(stored or route_style(None))
        out["phase_source"] = "unavailable"
        out["phase_note"] = f"实时相位不可用（{exc}），显示生成时刻快照"
        return out
    except Exception as exc:  # noqa: BLE001  读时重算失败是降级不是故障——保留快照，不覆盖成未知
        log.warning("live style routing failed: %s", exc)
        out = dict(stored or route_style(None))
        out["phase_source"] = "unavailable"
        out["phase_note"] = f"实时相位计算失败（{exc}），显示生成时刻快照"
        return out

    live = route_style(sent.get("phase"))
    live["phase_source"] = "live" if live.get("routed") else "unknown_phase"
    return live


def _gate_unavailable(stored: dict | None, note: str) -> dict:
    """实时复核不可用时的降级面：**保留生成时刻结论**，只标注不可用（不伪装成"未触发"）。

    与 `_live_style_routing` 的降级同款纪律（三态：来源显式，未知不伪装）。若连
    生成时刻结论也没有（旧行无 gate），返回的字典不含 `stand_aside` 键——前端
    `gate.stand_aside` 为 undefined 即 falsy，横幅不渲染，也**不会**被误读成"闸门未触发"。
    """
    out = dict(stored) if isinstance(stored, dict) else {}
    out["gate_source"] = "unavailable"
    out["gate_note"] = note
    return out


async def _live_gate(request: Request, hub: QuoteHub, stored: dict | None) -> dict:
    """空仓闸门按**读取时刻**重算（2026-09-16，猎场头部动态化）。

    **修的是什么**：闸门（`picks/gate.evaluate_stand_aside`）此前只在组合生成时
    算一次并持久化进 `meta.gate`，此后全天定格；而紧挨着它的 `style_routing` 是
    读取时重算的。2026-09-16 实测同一次 `/api/picks/today` 响应里同时出现
    「落库相位 = 退潮（横幅：建议空仓观望，已撤除买入区间）」与
    「实时相位 = 高潮，风格路由=题材进攻」——**两个相反结论同屏展示**，且定格的是
    更悲观的那个。用户判断（问题 7）完全命中：情绪判定不该在当天提前定死。

    **为什么定格会错得这么离谱**：组合 09:26 自动生成，此刻在场只有 3 只涨停、
    最高 2 板（meta.limit_up_count=3 / market_max_boards=2），引擎据此判「退潮」
    并无不当——**那是 09:26 的真实状态**。缺陷不在判据，在于**把开盘 3 分钟的
    瞬时快照当成了全天结论**。同一交易日收盘口径实测：涨停 89 家、最高 6 板、
    1进2 晋级率 36%（vs 生成时 8%）、炸板率 11% ⇒ 相位「高潮」，闸门本不该触发。

    **输入同源**：四项输入取自 `sent["gate_inputs"]`（sentiment 引擎本次新增的
    结构化出口）——引擎本就算过它们，收口后读侧**零额外网络调用**，只在本地做
    两次历史分位（实测合计 ~10.3ms/251 样本，走 `asyncio.to_thread` 不占事件循环）。
    生成时与读时因此**必然同口径**，不存在"两份取数逻辑各自漂移"。

    **不做什么**：落库的 `meta.gate` **原样保留**（它记录了生成时刻的真实判断，
    是复盘归因的证据，也是 `items[].buy_range` 已被撤除的原因）；本函数只**新增**
    一个对照面 `meta.gate_live`。GET仍不生成交易或告警；后台买点沿同一纯闸门规则独立重检并归档，不消费本读接口作为许可。
    """
    from app.picks.execution_qualification import gate_from_sentiment
    from app.services.market_context import CalendarUnavailable, get_cached_sentiment

    try:
        sent = await get_cached_sentiment(request.app.state, hub)
    except CalendarUnavailable as exc:
        return _gate_unavailable(stored, f"实时情绪不可用（{exc}），显示生成时刻结论")
    except Exception as exc:  # noqa: BLE001  读时重算是降级而非故障：保留落库结论，不覆盖成"未触发"
        log.warning("live gate sentiment failed: %s", exc)
        return _gate_unavailable(stored, f"实时情绪计算失败（{exc}），显示生成时刻结论")

    return await gate_from_sentiment(sent, stored)


async def _attach_gates(request: Request, hub: QuoteHub, meta: dict) -> dict:
    """给 meta 挂上「生成时刻闸门」与「读取时刻闸门」两面对照面。

    落库值打 `gate_source="stored"`（**不覆盖内容**——它是生成时刻的真实判断，
    也是当日 `buy_range` 被撤除的原因，复盘归因依赖它）。
    """
    stored = meta.get("gate")
    if isinstance(stored, dict):
        stored = {**stored, "gate_source": "stored"}
        meta["gate"] = stored
    meta["gate_live"] = await _live_gate(request, hub, stored)
    return meta


def _read_today_selection(today: str) -> dict | None:
    """Materialize one read-only selection in its worker-owned Session."""
    with _db() as db:
        from app.models.daily_pick import DailyPickSet
        from app.picks.review_contract import day_keys, iso_day, selection_rows_query

        row = db.scalar(select(DailyPickSet).where(DailyPickSet.date.in_(day_keys(today))).order_by(DailyPickSet.date.asc()).limit(1))
        fallback = row is None
        if row is None:
            row = db.scalar(selection_rows_query(1, through=today))
            if row is None:
                return None
        data = {
            "date": iso_day(row.date),
            "items": json.loads(row.items),
            "meta": parse_pick_meta(row.meta),
        }
        if fallback:
            data["stale"] = data["date"] != today
        else:
            data["replaced"] = json.loads(row.replaced or "[]")
        return data


@router.get("/today")
async def today_picks(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    today = beijing_today().isoformat()
    data = await asyncio.to_thread(_read_today_selection, today)
    if data is None:
        return {"data": {"date": None, "items": [], "meta": None, "note": "尚未生成组合：POST /api/picks/generate（或等待当日自动生成）"}, "meta": {}}
    # Live judgements remain per-read, in their existing order, after DB close.
    meta = data["meta"]
    meta["style_routing"] = await _live_style_routing(request, hub, meta.get("style_routing"))
    data["meta"] = await _attach_gates(request, hub, meta)
    # This later evidence read stays after live judgement; do not freeze it into
    # the earlier selection snapshot or run a synchronous Session on the loop.
    data["items"] = await asyncio.to_thread(_attach_latest_execution, data["items"], data["date"], meta)
    return {"data": data, "meta": {}}


@router.get("/history")
async def history(limit: int = Query(default=10, ge=1, le=60)) -> dict:
    with _db() as db:
        from app.picks.review_contract import iso_day, selection_rows_query

        rows = db.scalars(selection_rows_query(limit, through=beijing_today())).all()
        out = []
        for r in rows:
            items = json.loads(r.items)
            out.append(
                {
                    "date": iso_day(r.date),
                    "symbols": [i.get("symbol") for i in items],
                    "score_avg": round(sum(i.get("score", 0) for i in items) / max(len(items), 1), 1),
                }
            )
        return {"data": out, "meta": {"note": "每个交易日展示当前保存的组合；不代表所有生成版本历史"}}


# ---------------------------------------------------------------- 选股复盘


@router.post("/review/generate")
async def generate_review(request: Request, date_str: str | None = Query(default=None, alias="date"), hub: QuoteHub = Depends(get_hub), _: None = Depends(require_write_token)) -> dict:
    """按明确表现日读取或生成同版本的价格观察（历史日只读已有结果）。

    核心逻辑在 app.picks.daily_review.generate_daily_review（与 15:30 全局
    复盘前置步共用同一条代码路径，2026-09-04 抽出——见该模块 docstring）。
    """
    from app.picks.daily_review import generate_daily_review

    try:
        result = await generate_daily_review(hub, request.app.state.snapshot_service, get_session_factory(), trade_date=date_str)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"data": result, "meta": {}}


@router.get("/review")
async def list_reviews(date_str: str | None = Query(default=None, alias="date"), limit: int = Query(default=60, ge=1, le=200), version: str | None = Query(default=None, min_length=1, max_length=64)) -> dict:
    with _db() as db:
        from sqlalchemy import and_, case, func, literal, or_
        from app.models.daily_pick import DailyPickReview, DailyPickSet
        from app.picks.review_contract import day_keys, finite_number, iso_day, object_json, selection_version, trusted_review
        filters = []
        normalized = func.replace(DailyPickReview.date, '-', '')
        if date_str:
            try:
                keys = day_keys(date_str)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            filters.append(DailyPickReview.date.in_(keys))
        if version:
            filters.append(DailyPickReview.selection_version == version)
        # At most limit real days can supply limit principal rows. Canonicalize and
        # prefer the current generation before limiting rows, including limit=1.
        dates = db.scalars(select(func.min(DailyPickReview.date)).where(*filters).group_by(normalized).order_by(normalized.desc()).limit(limit)).all()
        keys = {key for raw_day in dates if iso_day(raw_day) for key in day_keys(raw_day)}
        filters.append(DailyPickReview.date.in_(keys))
        selections = db.scalars(select(DailyPickSet).where(DailyPickSet.date.in_(keys))).all() if keys else []
        by_day, current_conditions = {}, []
        for selection in selections:
            day = iso_day(selection.date)
            if day not in by_day or selection.date == day:
                by_day[day] = (object_json(selection.meta), json.loads(selection.items or "[]"))
        for day, combo in by_day.items():
            current = selection_version(day, combo[0], combo[1])
            if current:
                current_conditions.append(and_(normalized == day.replace('-', ''), DailyPickReview.selection_version == current))
        generation_order = case((or_(*current_conditions), 0), else_=1) if current_conditions else literal(1)
        ranked = select(DailyPickReview.id, func.row_number().over(partition_by=(normalized, DailyPickReview.symbol),
                        order_by=(generation_order, case((func.length(DailyPickReview.date) == 10, 0), else_=1), DailyPickReview.id.desc())).label('rank')).where(*filters).subquery()
        principal_ids = select(ranked.c.id).where(ranked.c.rank == 1)
        rows = db.scalars(select(DailyPickReview).where(DailyPickReview.id.in_(principal_ids)).order_by(normalized.desc(), DailyPickReview.symbol).limit(limit)).all()
        total = db.scalar(select(func.count()).select_from(DailyPickReview).where(*filters)) or 0
        principals = db.scalar(select(func.count()).select_from(ranked).where(ranked.c.rank == 1)) or 0
        def eligible(r):
            combo = by_day.get(iso_day(r.date))
            return bool(finite_number(r.excess_pct) is not None and combo and trusted_review(r.review_context, day=iso_day(r.date), meta=combo[0], items=combo[1], symbol=r.symbol, persisted_version=r.selection_version))
        return {
            "data": [
                {"date": iso_day(r.date), "symbol": r.symbol, "name": r.name, "verdict": r.verdict,
                 "reason_category": r.reason_category, "excess_pct": r.excess_pct, "note": r.note, "selection_version": r.selection_version, "review_context": object_json(r.review_context),
                 "statistics_eligible": eligible(r)}
                for r in rows
            ],
            "meta": {"legacy_duplicates_hidden": total - principals,
                     "note": "同日同股默认展示当前生成版本；可用date+version只读旧版原上下文"},
        }


@router.get("/meta")
async def picks_meta() -> dict:
    """最近同版本可信价格观察的归因与角色占比；不证明成交胜率或选股增量。"""
    from collections import Counter
    from app.models.daily_pick import DailyPickReview
    from app.picks.review_contract import finite_number, iso_day, object_json, selection_rows_query, trusted_review
    with _db() as db:
        reviews = db.scalars(select(DailyPickReview).order_by(DailyPickReview.date.desc()).limit(300)).all()
        sets = db.scalars(selection_rows_query(90, through=beijing_today())).all()
    by_day = {}
    for s in sets:
        day = iso_day(s.date)
        if day not in by_day or s.date == day:
            by_day[day] = (object_json(s.meta), json.loads(s.items or "[]"))
    agg, reasons, seen = {}, Counter(), set()
    excluded = 0
    for r in sorted(reviews, key=lambda x: (x.date != iso_day(x.date), -x.id)):
        day = iso_day(r.date)
        key = (day, r.symbol)
        combo = by_day.get(day)
        value = finite_number(r.excess_pct)
        if key in seen or value is None or not combo or not trusted_review(r.review_context, day=day, meta=combo[0], items=combo[1], symbol=r.symbol, persisted_version=r.selection_version):
            excluded += 1
            continue
        seen.add(key)
        reasons[r.reason_category] += 1
        role = object_json(r.review_context).get("echelon_role")
        if not role:
            continue
        a = agg.setdefault(role, {"count": 0, "good": 0, "bad": 0, "flat": 0, "excess_sum": 0.0})
        a["count"] += 1
        a[r.verdict if r.verdict in ("good", "bad") else "flat"] += 1
        a["excess_sum"] += value
    performance = [{"role": role, "count": a["count"], "good": a["good"], "bad": a["bad"],
                    "flat": a["flat"], "win_rate": round(a["good"] / a["count"] * 100, 1),
                    "avg_excess": round(a["excess_sum"] / a["count"], 2)}
                   for role, a in sorted(agg.items(), key=lambda kv: -kv[1]["count"])]
    return {"data": {"reason_distribution": dict(reasons), "role_performance": performance,
                     "scope": {"review_rows": len(reviews), "selection_rows": len(sets),
                               "excluded_rows": excluded, "trusted_rows": len(seen)},
                     "note": "最近最多300条复盘、90份组合；仅同版本同窗口可信价格观察，旧未绑定与缺收益不进分母；不是成交胜率，权重变更需人工确认"},
            "meta": {}}


# ---------------------------------------------------------------- 执行闸门与影子持仓（picks-intraday-fusion-assessment P0-A/P0-B，2026-09-04）


@router.get("/execution-gate")
async def execution_gate(request: Request, date: str | None = Query(default=None)) -> dict:
    """最新（或指定）组合成员的 9:25 竞价执行闸门三态判定。

    gap ≥ 9.5% 禁买（一字/超高开，历史胜率 12%）/ 5~9.5% 观察 / ≤-5% 异常复核 /
    其余可执行；竞价数据缺失 = unknown，绝不冒充可买。60s 缓存（竞价口径 9:25 后不再变）。
    """
    from app.core.config import settings
    from app.core.ttl_cache import cache_on
    from app.picks.execution_gate import collect_execution_gate

    hub = get_hub(request)
    cache = cache_on(request.app.state, "picks.execution_gate", 60, maxsize=2)
    _, payload = await cache.get_or_set(
        ("execution-gate", date),
        lambda: collect_execution_gate(
            hub, get_session_factory(),
            pick_date=date,
            block_ge=settings.picks_gate_block_gap,
            observe_ge=settings.picks_gate_observe_gap,
            anomaly_le=settings.picks_gate_anomaly_gap,
        ),
    )
    return {"data": payload, "meta": {}}


@router.get("/shadow")
async def shadow_state(request: Request) -> dict:
    """影子持仓账户状态（scope=shadow，独立于交易页签的 main 账户）。"""
    from app.picks.shadow import activation_status, read_shadow_state

    runner = getattr(request.app.state, "paper_shadow", None)
    state = await asyncio.to_thread(runner.state) if runner else await asyncio.to_thread(read_shadow_state, get_session_factory())
    status = activation_status(request.app, "daily")
    note = "晨窗只消费原每日精选组合及执行闸门；选择本视图不会启用或下单。"
    if not status["enabled"]:
        note = "每日精选影子未启用；历史账户保留，选择本视图不会启用。"
    elif not status["activation"]["runner_loaded"]:
        note = "已配置启用，但后台尚未装配；当前不会执行。"
    return {"data": {**state, **status, "note": note}, "meta": {}}


@router.get("/signal-health")
async def picks_signal_health() -> dict:
    """信号健康度（方向1×5 反馈环）：每日精选命中记录的滚动胜率 + CUSUM 下漂。

    status: ok | warning | drift | insufficient（样本 <10 组合日，显式不判 ok）| error。
    预警已接线（告警台账 `alert_event` + 通道矩阵 + 自动 action_items；
    **不进消息通知中心**——该中心只收 `__picks_buy_point__` 买点，`IMP-028`）。
    告警详情在 AI 控制台「提醒与告警」页（`/api/alerts/events`）查看。
    **本端点只覆盖「每日精选组合」一级**；跨策略键的评估见 `/strategy-health`。
    """
    from app.picks.signal_health import collect_signal_health

    payload = collect_signal_health(get_session_factory())
    return {"data": payload, "meta": {}}


@router.get("/strategy-health")
async def picks_strategy_health() -> dict:
    """**策略级**健康度（P1-37/P1-38）：逐策略键独立评估，不合并。

    与 `/signal-health` 的关系：后者是「每日精选组合」一级的视图（保持向后兼容），
    本端点是登记册全量策略键的视图——含 `intraday_watch`（盘中跟踪）等此前
    不在监控视野内的策略。

    status: ok | warning | drift | insufficient | thin | no_pipeline | error | unknown。
    ⚠️ `insufficient`/`thin`/`no_pipeline` 都是「判不出」，**不是 ok 也不是失效**。
    ⚠️ 每条带 `basis`：`market_neutral`（已扣同日市场均值的超额 → 测 alpha 衰减）
    与 `absolute`（绝对收益 → 只测策略自身是否变差），**两者不可互相解释**。
    """
    from app.picks.strategy_registry import collect_all_strategy_health

    payload = collect_all_strategy_health(get_session_factory())
    return {"data": payload, "meta": {}}


@router.get("/strategy-registry")
async def picks_strategy_registry() -> dict:
    """策略登记册全量条目（含已否决者，便于追溯"有哪些策略、各自什么状态"）。

    与 `docs/strategy/strategy-registry.md` §1 总表一致（由测试守卫）。
    """
    from app.picks.strategy_registry import list_strategy_keys, saved_method_runs

    try:
        runs = await asyncio.to_thread(saved_method_runs, get_session_factory())
    except Exception:
        log.warning("saved method runs unavailable", exc_info=True)
        runs = {key: {"state": "error", "reason": "已存运行读取失败，未当作零命中"}
                for key in ("daily_picks", "intraday_watch")}
    items = [{**item, **({"runtime": runs[item["key"]]} if item["key"] in runs else {})}
             for item in list_strategy_keys()]
    return {"data": {"strategies": items}, "meta": {}}


@router.get("/method-runs")
async def method_runs(
    method: Literal["daily_picks", "intraday_watch"],
    trade_date: str | None = Query(None, max_length=10),
    source_version: str | None = Query(None, min_length=1, max_length=64),
    strategy_version: str | None = Query(None, min_length=1, max_length=64),
    source_version_missing: bool = False,
    offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=200),
) -> dict:
    """只读已保存运行目录；每日旧覆盖版本缺失，不重评分或补造历史。"""
    from app.picks.selection_history import list_method_runs

    try:
        data = await asyncio.to_thread(list_method_runs, get_session_factory(), method,
            trade_date=trade_date, source_version=source_version, strategy_version=strategy_version,
            source_version_missing=source_version_missing, offset=offset, limit=limit)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    return {"data": data, "meta": {}}


@router.get("/method-runs/{method}/{run_id}")
async def method_run_detail(
    method: Literal["daily_picks", "intraday_watch"], run_id: str,
    trade_date: str = Query(..., min_length=8, max_length=10),
    content_version: str = Query(..., pattern="^[0-9a-f]{64}$"),
    source_version: str | None = Query(None, min_length=1, max_length=64),
    strategy_version: str | None = Query(None, min_length=1, max_length=64),
    source_version_missing: bool = False,
    offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=200),
    stage: Literal["rank", "hard_gate", "candidate", "notification"] | None = None,
) -> dict:
    """精确运行与内容版本读取；身份消失或变化返回404，不回退最近记录。"""
    from app.picks.selection_history import SavedRunMissing, get_method_run

    try:
        data = await asyncio.to_thread(get_method_run, get_session_factory(), method, run_id,
            trade_date=trade_date, content_version=content_version, source_version=source_version,
            strategy_version=strategy_version, source_version_missing=source_version_missing,
            offset=offset, limit=limit, stage=stage)
    except SavedRunMissing as exc:
        raise HTTPException(404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    return {"data": data, "meta": {}}
