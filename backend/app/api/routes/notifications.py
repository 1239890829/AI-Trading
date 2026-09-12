"""站内通知中心聚合端点（2026-09-07 用户需求③）。

GET /api/notifications?alert_limit=50&news_limit=15&news_min_score=<settings 默认>

三类来源合并为一条时间线，前端按 盘前/盘中/盘后 tab 分类展示：

1. opportunity 个股机会：盘中 watcher 的确认/证伪提醒（AlertEvent，规则名
   ``__picks_watcher__`` 专属——用户自建价格规则不进通知中心，研究页已有专属视图）。
2. daily_picks 每日精选：最近一份组合生成即一条（同日天然去重），标注门控状态。
3. news 消息面/新闻/政策：事件系统 + ``app.events.ranking.score_event`` 评分，
   **score ≥ 阈值才通知**（"新闻不逐条推送"）——评分机制与时事新闻板块（事件 tab
   relevance 排序）完全同源复用，不另起炉灶。

session（盘前/盘中/盘后）按北京时间墙钟划分：<09:30 盘前；09:30–15:05 盘中
（含午休——通知分类不需要午休粒度）；其余盘后。任何单一来源失败都显式降级
（errors 字段），绝不静默空列表。

已读状态（2026-09-12 缺陷修复）也挂在本模块，但它**不是**聚合的一部分，而是
一份独立的持久化状态：

- ``GET  /api/notifications/read-state`` —— 读取权威已读状态；
- ``PUT  /api/notifications/read-state`` —— 提交本地状态，**服务端按单调规则合并**
  后落库并回传合并结果（合并纪律见 ``app/services/notification_read_state.py``）。

为什么必须落服务端：此前只存浏览器 localStorage，而它是**按 origin 命名空间**的，
换源（localhost ↔ 127.0.0.1）／换 profile／清站点数据都会让已读状态整体归零，
表现为「重启后全部已读变未读、徽标回到 65」（实测复现）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.db import get_session_factory
from app.repositories.alert_repo import AlertRepository
from app.core.bjtime import BJ_OFFSET, beijing_now
from app.services import notification_read_state as read_state_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["notifications"])

WATCHER_RULE = "__picks_watcher__"
# 信号健康度预警规则（app/picks/signal_health.py）：与 watcher 分立的系统规则，
# 但同属通知中心应展示的「系统主动提醒」（策略失效预警 vs 个股事件提醒）。
SIGNAL_HEALTH_RULE = "__signal_health__"
_NOTIF_RULE_NAMES = (WATCHER_RULE, SIGNAL_HEALTH_RULE)

# 时事新闻板块的四级分类标签（app/events/impact.py FOUR_LABEL 同值同源）
_FOUR_LABEL = {
    "international": "国际时事",
    "policy": "国家政策",
    "hot": "市场热点",
    "material": "原材料涨价",
}


def _session_of(bj: datetime) -> str:
    """北京时间 naive 墙钟 → 盘前/盘中/盘后（口径见模块 docstring）。"""
    hm = bj.hour * 100 + bj.minute
    if 930 <= hm <= 1505:
        return "intraday"
    if hm < 930:
        return "pre_open"
    return "after_close"


def get_alert_repo(request: Request) -> AlertRepository:
    return request.app.state.alert_repo


def _alert_items(repo: AlertRepository, limit: int) -> list[dict]:
    """watcher 确认/证伪 + 信号健康度预警 → 通知项。triggered_at 已统一北京时间 naive（2026-09-09 告警时区修复；此前存 UTC naive 在此 +8 补偿，补偿点已随存储统一移除）。

    P0-2（2026-09-08 用户指令「AI 盘中分析进站内通知」）：合并 AgentTriage
    判读结论与响应建议进 body——AI 的盘中分析在通知中心直接可见。
    """
    rules = {r.id: r for r in repo.list_rules()}
    notif_rule_ids = {rid for rid, r in rules.items() if r.name in _NOTIF_RULE_NAMES}
    if not notif_rule_ids:
        return []
    events = [e for e in repo.list_events(limit=limit) if e.rule_id in notif_rule_ids]

    # AI 判读与响应建议（P0-2）：event_id → (verdict, reason)
    triage_by_event: dict[int, tuple[str, str]] = {}
    try:
        from sqlalchemy import select as _sel

        from app.core.db import get_session_factory
        from app.models.agent import AgentTriage

        ids = [e.id for e in events]
        if ids:
            with get_session_factory()() as db:
                rows = db.execute(
                    _sel(AgentTriage).where(AgentTriage.event_id.in_(ids))
                ).scalars().all()
                for t in rows:
                    triage_by_event[t.event_id] = (t.verdict, t.reason or "")
    except Exception:  # noqa: BLE001  判读缺失 → body 退化为基础文本
        log.exception("notifications: triage merge failed")

    items: list[dict] = []
    for e in events:
        snap = e.snapshot if isinstance(e.snapshot, dict) else {}
        if isinstance(e.snapshot, str):
            try:
                snap = json.loads(e.snapshot)
            except Exception:  # noqa: BLE001
                snap = {}
        kind = snap.get("kind") or "watcher"
        direction = snap.get("direction") or ""
        text = (snap.get("text") or "").strip()
        bj = e.triggered_at  # 北京时间 naive（存储已统一，勿再 +8）
        kind_label = (
            "确认" if kind == "confirm"
            else ("证伪" if kind == "falsify"
                  else ("健康预警" if kind == "signal_health" else "跟踪"))
        )
        category = "risk" if kind == "signal_health" else "opportunity"
        # 方向级事件（falsify）symbol 是占位 "000000"，不进标题（占位代码泄漏到 UI）
        is_stock = bool(e.symbol and e.symbol != "000000")
        sym_part = f" {e.symbol}" if is_stock else ""
        # 2026-09-09 用户指令：提醒必须完整包含代码+名称（缺任一即补全）——
        # 名称优先取快照（实时），缺失显式「（名称待补）」不臆造
        stock_name = ""
        if is_stock:
            stock_name = (snap.get("name") or "").strip() if isinstance(snap.get("name"), str) else ""
        name_part = f" {stock_name}" if stock_name else ""
        body = text or "（无正文）"
        # P0-2：AI 盘中分析合入 body（判读结论 + 响应建议）
        tri = triage_by_event.get(e.id)
        if tri:
            verdict_label = {"notify": "提醒", "ignore": "已降噪", "escalate": "需关注"}.get(tri[0], tri[0])
            body = f"{body}\nAI 判读（{verdict_label}）：{tri[1]}"
        items.append(
            {
                "id": f"alert-{e.id}",
                "category": category,
                "label": kind_label,
                "session": _session_of(bj) if bj else "intraday",
                "ts": bj.isoformat(sep=" ") if bj else None,
                "title": (
                    f"【{direction or '盘中跟踪'}】{sym_part}{name_part} {kind_label}"
                    if is_stock
                    else f"【{direction or '题材级'}】{kind_label}（方向级提醒，无个股）"
                ).strip(),
                "body": body,
                "symbol": e.symbol if e.symbol and e.symbol != "000000" else None,
                "url": None,
                "score": None,
            }
        )
    return items


def _daily_pick_item() -> dict | None:
    """最近一份每日精选组合 → 一条通知（date 唯一约束 → 同日天然去重）。"""
    from sqlalchemy import select

    from app.models.daily_pick import DailyPickSet

    with get_session_factory()() as db:
        row = db.execute(select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(1)).scalar_one_or_none()
    if row is None:
        return None
    try:
        items = json.loads(row.items or "[]")
    except Exception:  # noqa: BLE001
        items = []
    top = "、".join(
        str(it.get("name") or it.get("symbol")) for it in items[:5] if isinstance(it, dict)
    )
    try:
        meta = json.loads(row.meta or "{}") if isinstance(row.meta, str) else (row.meta or {})
    except Exception:  # noqa: BLE001
        meta = {}
    gate_stand = bool((meta.get("gate") or {}).get("stand_aside"))
    gate_note = "，空仓闸门触发（仅观察）" if gate_stand else ""
    # 时间戳 = **真实生成时刻**（评审 F-9，2026-09-12）。旧实现写死
    # `f"{row.date} 08:40:00"`，两个问题：
    # ① 08:40 是配置漂移的残留——自动生成实为 09:26（`picks_autogen_scheduler` 的
    #    run_hour=9 / run_minute=26），DB 实测 `created_at` 01:26:58 UTC +8 = 09:26:58
    #    北京，与配置精确吻合；组合也可能由人工在盘中/盘后触发，写死则一律显示盘前。
    # ② 它与本模块第 312 行的 `ts` 倒序直接冲突：alert/news 用真实时间，只有这一条
    #    用一个假时间，排序结果与"实际发生顺序"不一致。
    # ⚠️ 换算必须走 `BJ_OFFSET`，**不能**用 `to_beijing_naive()`：后者对 naive 输入按
    #    「已经是北京时间」处理（bjtime 口径），对 UTC 语义的 `created_at` 是零变换，
    #    会静默早 8 小时——正是 S2-8 那类事故的形态。
    ts = (row.created_at + BJ_OFFSET).isoformat(sep=" ") if row.created_at else None
    return {
        "id": f"picks-{row.date}",
        "category": "daily_picks",
        "label": "每日精选",
        "session": "pre_open",  # 组合盘前/盘后生成，归盘前节拍
        "ts": ts,
        "title": f"每日精选 · {row.date}（{len(items)} 只{gate_note}）",
        "body": f"Top：{top or '—'}。名单为盘中跟踪的输入，机会确认以盘中提醒为准，避免开盘即回落被误判。",
        "symbol": None,
        "url": "/picks",
        "score": None,
    }


async def _news_items(store, request: Request, limit: int, min_score: float, now: datetime) -> tuple[list[dict], str | None]:
    """事件系统 → 评分过滤后的新闻通知。返回 (items, error)。"""
    try:
        rows = store.list_events(active_only=False, limit=80)
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)

    # 72h 窗口：通知是「新事」视图，老事件再高分也不打扰（score 内新鲜度另有权重）
    cutoff = now - timedelta(hours=72)
    recent = [r for r in rows if r.published_at and r.published_at >= cutoff]
    if not recent:
        return [], None

    try:
        from app.events.impact import impact_level
        from app.events.ranking import collect_rank_context, score_event
    except Exception as exc:  # noqa: BLE001
        return [], f"ranking import failed: {exc}"

    theme_names = sorted({
        d.target for r in recent for d in (r.directions or []) if d.target_type == "theme"
    })
    symbols = sorted({
        d.target for r in recent for d in (r.directions or []) if d.target_type == "symbol"
    })
    try:
        ctx = await collect_rank_context(request.app.state, theme_names, symbols)
    except Exception as exc:  # noqa: BLE001
        return [], f"rank context failed: {exc}"

    items: list[dict] = []
    for r in recent:
        four = _classify_four_row(r)
        level = impact_level(
            r.title, four=four, certainty=r.certainty, fact_kind=r.fact_kind,
            source_tier=r.source_tier, n_directions=len(r.directions or []),
        )
        symbol_vals = [ctx.stock_chg.get(d.target) for d in (r.directions or []) if d.target_type == "symbol"]
        rank = score_event(
            impact_level=level,
            four=four,
            source_tier=r.source_tier,
            published_at=r.published_at,
            half_life_hours=r.half_life_hours,
            theme_names=[d.target for d in (r.directions or []) if d.target_type == "theme"],
            symbol_chg=symbol_vals,
            ctx=ctx,
            now=now,
        )
        if rank["score"] < min_score:
            continue  # 评分过滤：不逐条推送
        items.append(
            {
                "id": f"event-{r.id}",
                "category": "news",
                "label": _FOUR_LABEL.get(four, four),
                "session": _session_of(r.published_at) if r.published_at else "intraday",
                "ts": r.published_at.isoformat(sep=" ") if r.published_at else None,
                "title": r.title or "（无标题）",
                "body": (r.title or "")[:120],
                "symbol": None,
                "url": r.url,
                "score": rank["score"],
            }
        )
    items.sort(key=lambda x: (-(x["score"] or 0), x["ts"] or ""))
    return items[:limit], None


def _classify_four_row(row) -> str:
    """与 routes/events 内联逻辑同源的四分类（涨价 → 政策 → 国际 → 热点）。"""
    from app.events.impact import classify_four

    return classify_four(row.title, row.category)


@router.get("/notifications")
async def notifications(
    request: Request,
    alert_limit: int = Query(default=50, ge=1, le=200),
    news_limit: int = Query(default=15, ge=1, le=50),
    news_min_score: float | None = Query(default=None, ge=0, le=100),
    repo: AlertRepository = Depends(get_alert_repo),
) -> dict:
    """三类通知合并时间线（来源与分类口径见模块 docstring）。"""
    min_score = news_min_score if news_min_score is not None else settings.notifications_news_min_score
    now = beijing_now().replace(tzinfo=None)  # 事件 published_at 是北京 naive，同语义相减

    errors: dict[str, str] = {}
    alert_items: list[dict] = []
    try:
        alert_items = _alert_items(repo, alert_limit)
    except Exception as exc:  # noqa: BLE001
        log.exception("notifications: alert source failed")
        errors["alerts"] = str(exc)

    pick_item: dict | None = None
    try:
        pick_item = _daily_pick_item()
    except Exception as exc:  # noqa: BLE001
        log.exception("notifications: daily picks source failed")
        errors["daily_picks"] = str(exc)

    news_items: list[dict] = []
    store = getattr(request.app.state, "event_store", None)
    if store is not None:
        news_items, news_err = await _news_items(store, request, news_limit, min_score, now)
        if news_err:
            errors["news"] = news_err
    else:
        errors["news"] = "event store unavailable"

    items = [*alert_items, *news_items, *([pick_item] if pick_item else [])]
    items.sort(key=lambda x: x["ts"] or "", reverse=True)
    return {
        "data": {
            "items": items,
            "count": len(items),
            "generated_at": now.isoformat(sep=" "),
            "news_min_score": min_score,
            "errors": errors or None,
        },
        "meta": {},
    }


# ---------------------------------------------------------------- 已读状态（2026-09-12）


class ReadStateIn(BaseModel):
    """PUT 载荷。三个分量都**只增不减**，非法值由 pydantic 拦在入口。

    时间戳是 epoch 毫秒（正数）；`read_ids` 上界与服务层 ``READ_IDS_MAX`` 一致，
    防止异常客户端把单行 JSON 灌爆。
    """

    seen_before: int = Field(default=0, ge=0)
    read_ids: list[str] = Field(default_factory=list, max_length=read_state_service.READ_IDS_MAX)
    clear_before: int = Field(default=0, ge=0)


def _read_state_payload(state: dict) -> dict:
    return {
        "seen_before": state["seen_before"],
        "read_ids": state["read_ids"],
        "clear_before": state["clear_before"],
        "updated_at": state.get("updated_at"),
    }


@router.get("/notifications/read-state")
def get_read_state() -> dict:
    """权威已读状态。DB 不可用**不伪装成空状态**——已读归零正是本次要修的缺陷形态，
    所以这里让异常直接冒泡成 5xx，前端据此保留本地缓存值（宁可显示旧状态，不可假装未读）。"""
    return {"data": _read_state_payload(read_state_service.load_state()), "meta": {}}


@router.put("/notifications/read-state")
def put_read_state(body: ReadStateIn) -> dict:
    """提交本地状态 → 服务端单调合并 → 落库并回传合并结果（前端以回传值为准）。"""
    merged = read_state_service.save_state(
        {"seen_before": body.seen_before, "read_ids": body.read_ids, "clear_before": body.clear_before}
    )
    return {"data": _read_state_payload(merged), "meta": {}}
