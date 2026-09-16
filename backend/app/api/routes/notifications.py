"""站内个股机会通知端点（2026-09-15 收敛；2026-09-16 `IMP-034` 清理死代码）。

GET /api/notifications?alert_limit=50&news_limit=15&news_min_score=<settings 默认>

通知中心只消费 ``__picks_buy_point__`` 规则产生的有效个股事件。该规则的上游是
``picks.buy_point.evaluate_buy_points``：每日精选候选须同时通过置信档、多维评分、
红线否决、空仓闸门、买入区间、涨停区和实时行情检查后才会落事件。

板块资金异动、题材方向确认/证伪、信号健康、每日精选摘要和新闻仍保留在各自页面与
审计表中，但不再进入消息通知。这样「可研究的信息」与「值得打断用户的个股机会」
不再混为一谈。飞书原本就只发送同一买点规则的逐股卡片，口径保持一致。

⚠️ **`news_limit` / `news_min_score` 仅为旧客户端兼容保留、已不被消费**
（响应里的 ``news_min_score`` 同理，取值仍是配置默认）。`IMP-028` 收敛时留下的
``_daily_pick_item`` / ``_news_items`` / ``_classify_four_row`` 三个不再被引用的
生产者已于 `IMP-034` **删除**（连同守着一个够不到的落点的用例）——留着它们会给出
虚假的覆盖信心（同族教训见 `kb/09-verification-pitfalls.md`）。

session（盘前/盘中/盘后）**交易日历优先**（2026-09-13 用户报告的周末误标盘中修复）：
非交易日（周末/节假日）一律归**盘前**节拍（下一交易日开盘前消化的资讯）；
交易日按墙钟：<09:30 盘前；09:30–15:05 盘中（含午休——通知分类不需要午休粒度）；
其余盘后。日历不可用（trading_days 失败）回退墙钟——宁可放行不因日历故障误判
（与 in_trading_window 同哲学）。任何单一来源失败都显式降级（errors 字段）。

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
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import require_write_token
from app.core.config import settings
from app.repositories.alert_repo import AlertRepository
from app.core.bjtime import beijing_now
from app.market import trade_calendar as tc
from app.services import notification_read_state as read_state_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["notifications"])

BUY_POINT_RULE = "__picks_buy_point__"
_NOTIF_RULE_NAMES = (BUY_POINT_RULE,)


def _session_of(bj: datetime, trading_dates: set | None = None) -> str:
    """北京时间 naive → 盘前/盘中/盘后。

    trading_dates（交易日 date 集合，日历唯一入口 trading_days 的产物）非 None 时
    **日历优先**：非交易日一律盘前节拍（周末/节假日的消息在下一交易日开盘前消化）；
    None（日历不可用）回退纯墙钟——回退是显式降级而非静默错误。
    """
    if trading_dates is not None and bj.date() not in trading_dates:
        return "pre_open"
    hm = bj.hour * 100 + bj.minute
    if 930 <= hm <= 1505:
        return "intraday"
    if hm < 930:
        return "pre_open"
    return "after_close"


def get_alert_repo(request: Request) -> AlertRepository:
    return request.app.state.alert_repo


def _alert_items(repo: AlertRepository, limit: int, trading_dates: set | None = None) -> list[dict]:
    """多维门控后的买点事件 → 个股机会通知。triggered_at 为北京时间 naive。

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
        kind = snap.get("kind") or ""
        # 规则名和事件形状双重收口：历史脏行、占位代码、缺名称或非 buy_point
        # 都不冒充「真正机会」。上游缺证据时宁缺毋滥。
        stock_name = (snap.get("name") or "").strip() if isinstance(snap.get("name"), str) else ""
        if kind != "buy_point" or not e.symbol or e.symbol == "000000" or not stock_name:
            continue
        direction = snap.get("direction") or ""
        text = (snap.get("text") or "").strip()
        bj = e.triggered_at  # 北京时间 naive（存储已统一，勿再 +8）
        body = text or "（无正文）"
        # P0-2：AI 盘中分析合入 body（判读结论 + 响应建议）
        tri = triage_by_event.get(e.id)
        if tri:
            verdict_label = {"notify": "提醒", "ignore": "已降噪", "escalate": "需关注"}.get(tri[0], tri[0])
            body = f"{body}\nAI 判读（{verdict_label}）：{tri[1]}"
        items.append(
            {
                "id": f"alert-{e.id}",
                "category": "opportunity",
                "label": "个股机会",
                "session": _session_of(bj, trading_dates) if bj else "intraday",
                "ts": bj.isoformat(sep=" ") if bj else None,
                "title": f"【{direction or '盘中买点'}】{e.symbol} {stock_name}".strip(),
                "body": body,
                "symbol": e.symbol,
                "url": None,
                "score": None,
            }
        )
    return items


@router.get("/notifications")
async def notifications(
    request: Request,
    alert_limit: int = Query(default=50, ge=1, le=200),
    news_limit: int = Query(default=15, ge=1, le=50),
    news_min_score: float | None = Query(default=None, ge=0, le=100),
    repo: AlertRepository = Depends(get_alert_repo),
) -> dict:
    """只返回经过买点多维门控的有效个股机会；旧查询参数保留兼容。"""
    min_score = news_min_score if news_min_score is not None else settings.notifications_news_min_score
    now = beijing_now().replace(tzinfo=None)  # 事件 published_at 是北京 naive，同语义相减

    # 交易日历一次取（请求级）：session 分类的日历优先判定（§6.25 周末误标盘中修复）。
    # 失败 → None → 回退墙钟（宁可放行不因日历故障误判，与 in_trading_window 同哲学）。
    trading_dates: set | None = None
    try:
        provider = getattr(getattr(request.app.state, "hub", None), "provider", None)
        if provider is not None:
            days = await tc.trading_days(provider)
            trading_dates = set(days) if days else None
    except Exception:  # noqa: BLE001
        log.warning("notifications: trading calendar unavailable, session 回退墙钟", exc_info=True)
        trading_dates = None

    errors: dict[str, str] = {}
    alert_items: list[dict] = []
    try:
        alert_items = _alert_items(repo, alert_limit, trading_dates)
    except Exception as exc:  # noqa: BLE001
        log.exception("notifications: alert source failed")
        errors["alerts"] = str(exc)

    items = alert_items
    items.sort(key=lambda x: x["ts"] or "", reverse=True)
    return {
        "data": {
            "items": items,
            "count": len(items),
            "generated_at": now.isoformat(sep=" "),
            "news_min_score": min_score,
            "policy": "stock_opportunities_only",
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


@router.put("/notifications/read-state", dependencies=[Depends(require_write_token)])
def put_read_state(body: ReadStateIn) -> dict:
    """提交本地状态 → 服务端单调合并 → 落库并回传合并结果（前端以回传值为准）。"""
    merged = read_state_service.save_state(
        {"seen_before": body.seen_before, "read_ids": body.read_ids, "clear_before": body.clear_before}
    )
    return {"data": _read_state_payload(merged), "meta": {}}
