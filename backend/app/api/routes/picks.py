"""每日精选 API（CONTEXT.md: Daily Picks 域）。

- GET  /api/picks/today           当日组合（从库读；不自动生成——生成较重，POST 触发）
- POST /api/picks/generate        生成今日组合（写鉴权；收盘后复盘管线或手动触发）
- GET  /api/picks/history         历史组合（近 N 日）
- GET  /api/picks/review?date=    复盘日志
- POST /api/picks/review/generate 对最近组合生成/刷新复盘（写鉴权）
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


@router.post("/generate")
async def generate_picks(request: Request, hub: QuoteHub = Depends(get_hub), _: None = Depends(require_write_token)) -> dict:
    """生成今日组合（T 日收盘后跑，产出 T+1 组合；重复生成覆盖当日行）。

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
    一个对照面 `meta.gate_live`。也不动推送口径——这是展示层复核，不是新的告警源。
    """
    from app.picks.gate import evaluate_stand_aside
    from app.services.market_context import CalendarUnavailable, get_cached_sentiment

    try:
        sent = await get_cached_sentiment(request.app.state, hub)
    except CalendarUnavailable as exc:
        return _gate_unavailable(stored, f"实时情绪不可用（{exc}），显示生成时刻结论")
    except Exception as exc:  # noqa: BLE001  读时重算是降级而非故障：保留落库结论，不覆盖成"未触发"
        log.warning("live gate sentiment failed: %s", exc)
        return _gate_unavailable(stored, f"实时情绪计算失败（{exc}），显示生成时刻结论")

    gi = sent.get("gate_inputs") or {}
    promo_1to2 = gi.get("promotion_1to2")
    break_rate = gi.get("break_rate")
    promo_pctl = None
    break_pctl = None
    try:
        from app.sentiment import metric_history

        # 与生成时同一处函数、同样合并为一次线程跳（两个分位一起算）：
        # 从事件循环里做同步磁盘读会卡住整个进程（G-2 的同一教训）。
        def _both_pctl() -> tuple[float | None, float | None]:
            p = (metric_history.percentile_of_value("promo_1to2", promo_1to2) or {}).get("percentile")
            b = (metric_history.percentile_of_value("break_rate", break_rate) or {}).get("percentile")
            return p, b

        promo_pctl, break_pctl = await asyncio.to_thread(_both_pctl)
    except Exception as exc:  # noqa: BLE001  分位不可用时闸门自动回落绝对经验值并在理由里写明（不静默）
        log.warning("live gate percentile failed: %s", exc)

    gate = evaluate_stand_aside(
        phase=sent.get("phase"),
        promotion_1to2=promo_1to2,
        promotion_1to2_pctl=promo_pctl,
        break_rate=break_rate,
        break_rate_pctl=break_pctl,
        limit_down=gi.get("limit_down"),
        prev_zt_median_pct=gi.get("prev_zt_median_pct"),
        phase_unreliable=bool(sent.get("phase_unreliable")),
    )
    gate["gate_source"] = "live"
    # 对照面：实时相位 vs 生成时刻相位、本次复核用的交易日。前端凭此说明
    # 「结论为何变化」，不必自己推断（也不该在前端重算规则）。
    gate["recheck"] = {
        "phase": sent.get("phase"),
        "trade_date": sent.get("trade_date"),
        "judged_at": sent.get("judged_at"),
        "stored_phase": (stored or {}).get("phase"),
        "phase_changed": (stored or {}).get("phase") != sent.get("phase"),
        "inputs_source": "sentiment.gate_inputs",
        "break_caliber": gi.get("break_caliber"),
    }
    return gate


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


@router.get("/today")
async def today_picks(request: Request, hub: QuoteHub = Depends(get_hub)) -> dict:
    today = beijing_today().isoformat()
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        row = db.execute(select(DailyPickSet).where(DailyPickSet.date == today)).scalar_one_or_none()
        if row is None:
            row = db.execute(select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(1)).scalar_one_or_none()
            if row is None:
                return {"data": {"date": None, "items": [], "meta": None, "note": "尚未生成组合：POST /api/picks/generate（或等收盘管线）"}, "meta": {}}
            meta = parse_pick_meta(row.meta)  # 炒作阶段与空仓闸门状态（前端横幅需要）
            meta["style_routing"] = await _live_style_routing(request, hub, meta.get("style_routing"))
            meta = await _attach_gates(request, hub, meta)
            return {
                "data": {
                    "date": row.date,
                    "items": json.loads(row.items),
                    "stale": row.date != today,
                    "meta": meta,
                },
                "meta": {},
            }
        meta = parse_pick_meta(row.meta)
        meta["style_routing"] = await _live_style_routing(request, hub, meta.get("style_routing"))
        meta = await _attach_gates(request, hub, meta)
        return {
            "data": {
                "date": row.date,
                "items": json.loads(row.items),
                "replaced": json.loads(row.replaced or "[]"),
                "meta": meta,
            },
            "meta": {},
        }


@router.get("/history")
async def history(limit: int = Query(default=10, ge=1, le=60)) -> dict:
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        rows = db.execute(select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(limit)).scalars().all()
        out = []
        for r in rows:
            items = json.loads(r.items)
            out.append(
                {
                    "date": r.date,
                    "symbols": [i.get("symbol") for i in items],
                    "score_avg": round(sum(i.get("score", 0) for i in items) / max(len(items), 1), 1),
                }
            )
        return {"data": out, "meta": {}}


# ---------------------------------------------------------------- 选股复盘


@router.post("/review/generate")
async def generate_review(request: Request, hub: QuoteHub = Depends(get_hub), _: None = Depends(require_write_token)) -> dict:
    """对最近一份组合生成/刷新复盘（表现日 = 今天；组合 T-1 生成、T 日持有）。

    核心逻辑在 app.picks.daily_review.generate_daily_review（与 15:30 全局
    复盘前置步共用同一条代码路径，2026-09-04 抽出——见该模块 docstring）。
    """
    from app.picks.daily_review import generate_daily_review

    try:
        result = await generate_daily_review(hub, request.app.state.snapshot_service, get_session_factory())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"data": result, "meta": {}}


@router.get("/review")
async def list_reviews(date_str: str | None = Query(default=None, alias="date"), limit: int = Query(default=60, ge=1, le=200)) -> dict:
    with _db() as db:
        from app.models.daily_pick import DailyPickReview

        q = select(DailyPickReview).order_by(DailyPickReview.date.desc(), DailyPickReview.symbol)
        if date_str:
            q = select(DailyPickReview).where(DailyPickReview.date == date_str).order_by(DailyPickReview.symbol)
        rows = db.execute(q.limit(limit)).scalars().all()
        return {
            "data": [
                {"date": r.date, "symbol": r.symbol, "name": r.name, "verdict": r.verdict,
                 "reason_category": r.reason_category, "excess_pct": r.excess_pct, "note": r.note}
                for r in rows
            ],
            "meta": {},
        }


@router.get("/meta")
async def picks_meta() -> dict:
    """元结论：走坏原因分布 + 按梯队角色的胜率分布。

    角色胜率是回答「能不能按题材抓妖」的直接证据：龙头/补涨/滞涨各自的
    实际胜率与平均超额，比任何主观判断都硬。样本不足时如实标注。
    """
    with _db() as db:
        from sqlalchemy import func

        from app.models.daily_pick import DailyPickReview, DailyPickSet

        rows = db.execute(
            select(DailyPickReview.reason_category, func.count(DailyPickReview.id)).group_by(DailyPickReview.reason_category)
        ).all()
        reviews = db.execute(
            select(DailyPickReview).order_by(DailyPickReview.date.desc()).limit(300)
        ).scalars().all()
        sets = db.execute(
            select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(90)
        ).scalars().all()

    # (date, symbol) → 梯队角色（角色存在组合 items JSON 里）
    role_of: dict[tuple[str, str], str] = {}
    for s in sets:
        try:
            for item in json.loads(s.items):
                if item.get("echelon_role"):
                    role_of[(s.date, item["symbol"])] = item["echelon_role"]
        except Exception:
            continue

    agg: dict[str, dict] = {}
    for r in reviews:
        role = role_of.get((r.date, r.symbol))
        if role is None:
            continue  # 该条复盘早于梯队维度上线（8-31 前），角色未知不硬凑
        a = agg.setdefault(role, {"count": 0, "good": 0, "bad": 0, "flat": 0, "excess_sum": 0.0})
        a["count"] += 1
        a[r.verdict if r.verdict in ("good", "bad") else "flat"] += 1
        a["excess_sum"] += r.excess_pct or 0.0
    role_performance = []
    for role, a in sorted(agg.items(), key=lambda kv: -kv[1]["count"]):
        role_performance.append(
            {
                "role": role,
                "count": a["count"],
                "good": a["good"],
                "bad": a["bad"],
                "flat": a["flat"],
                "win_rate": round(a["good"] / a["count"] * 100, 1),
                "avg_excess": round(a["excess_sum"] / a["count"], 2),
            }
        )
    return {
        "data": {
            "reason_distribution": {r[0]: r[1] for r in rows},
            "role_performance": role_performance,
            "note": "分布与角色胜率供周末权重微调建议参考；权重变更需人工确认",
        },
        "meta": {},
    }


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
    runner = getattr(request.app.state, "paper_shadow", None)
    if runner is None:
        return {"data": {"enabled": False, "note": "影子持仓未启用（ASHARE_PICKS_SHADOW_ENABLED）"}, "meta": {}}
    return {"data": {"enabled": True, **runner.state()}, "meta": {}}


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
    from app.picks.strategy_registry import list_strategy_keys

    return {"data": {"strategies": list_strategy_keys()}, "meta": {}}
