"""告警 AI 判读层（方案 docs/ai-agent-console-plan.md P1）。

问题：规则触发 ≠ 值得提醒。价格/炸板/异动规则在震荡日能刷出几十条，
人工看等于没有——这正是"预警"沦为噪音的原因。

设计：
1. **确定性规则优先**（不消耗 LLM）：同一规则在冷却窗口内已判读过 → ignore
   （去重）；事件被用户确认过 → 不再冒泡。
2. **LLM 判读**：把事件 + 当下环境（情绪相位、是否持仓、近 1h 同类事件数）
   喂给模型，要求严格输出 `{verdict, reason}`；verdict ∈ notify/ignore/escalate。
3. **降级**：LLM 不可用/超时/输出非法 → verdict=notify 且 model 标
   `llm_fallback`，reason 显式写"AI 判读不可用，按规则提醒"——**不伪装成
   AI 判断**（09-04 有 LLM 全天降级先例，界面必须能看出区别）。

纪律：判读**不发飞书**（2026-09-08 推送定稿：飞书只保留盘中买点卡），
只在系统内（悬浮球 + 控制台）呈现；`escalate` 由 `_save` 登记为任务中心待办
（P1-36，2026-09-10 接通：`record_escalation` 纯登记 + `needs_confirm` 初始态，
人工在任务中心处置 → `resolve_task`）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any
from datetime import timedelta

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.agent import AgentTriage
from app.models.alert import AlertEvent, AlertRule
from app.core.bjtime import beijing_now_naive

log = logging.getLogger(__name__)


#: 冷却窗口：同一规则在此窗口内已判读过 → 直接 ignore（去重，防刷屏）
COOLDOWN_MINUTES = 30
#: 判读时参考的"近 1h 同类事件"上限（防 prompt 过长）
RECENT_LIMIT = 5

_VERDICTS = ("notify", "ignore", "escalate")

_SYSTEM_PROMPT = (
    "你是 A 股盘中告警判读助手。给定一条系统告警事件与当下环境，判断它值不值得提醒用户。\n"
    "只输出 JSON：{\"verdict\": \"notify|ignore|escalate\", \"reason\": \"中文一句话，≤30 字\"}\n"
    "判据：\n"
    "- notify：与用户持仓/自选相关，或是明确的趋势转折、风险信号，需要马上看\n"
    "- ignore：重复事件、噪音、幅度极小、已过时效、对决策无影响\n"
    "- escalate：影响面大（全市场级风险、系统性异常），需要进待办处理\n"
    "纪律：不提供买卖建议；不确定时选 notify（宁可提醒，不可漏掉风险）。"
)


def _event_context(event: AlertEvent, session_factory) -> dict:
    """构造判读输入（纯 DB 读，不外呼）。"""
    snap = event.snapshot
    if isinstance(snap, str):
        with contextlib.suppress(Exception):
            snap = json.loads(snap)
    snap = snap if isinstance(snap, dict) else {}

    rule_name, condition = "", ""
    with session_factory() as db:
        rule = db.get(AlertRule, event.rule_id)
        if rule is not None:
            rule_name = rule.name or ""
            condition = rule.condition_type or ""
        cutoff = beijing_now_naive() - timedelta(hours=1)
        recent = db.execute(
            select(AlertEvent.id).where(
                AlertEvent.rule_id == event.rule_id,
                AlertEvent.triggered_at >= cutoff,
            )
        ).scalars().all()
    return {
        "rule": rule_name,
        "condition": condition,
        "symbol": event.symbol,
        "trigger_value": event.trigger_value,
        "threshold": event.threshold,
        "text": str(snap.get("text") or "")[:200],
        "kind": snap.get("kind") or "",
        "last_hour_same_rule": len(recent),
    }


async def _llm_verdict(ctx: dict) -> tuple[str, str] | None:
    """LLM 判读；返回 (verdict, reason)，不可用/非法返回 None（调用方降级）。"""
    from app.core.config import settings
    from app.core.llm_client import chat_completion, extract_json_object

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
    ]

    def _call() -> str:
        return chat_completion(
            base_url=settings.review_llm_base_url,
            api_key=settings.review_llm_api_key,
            model=settings.review_llm_model,
            messages=messages,
            provider=settings.llm_provider,
            cli_path=settings.llm_cli_path,
            timeout=30.0,
        )

    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_call), timeout=45.0)
    except Exception as exc:  # noqa: BLE001  LLM 是增强层，失败必须降级
        log.info("alert triage llm unavailable: %s", exc)
        return None

    try:
        data = extract_json_object(raw)
        verdict = str((data or {}).get("verdict") or "").strip().lower()
        reason = str((data or {}).get("reason") or "").strip()
        if verdict not in _VERDICTS:
            return None
        return verdict, reason[:60]
    except Exception:  # noqa: BLE001
        return None


def _already_recent(event: AlertEvent, session_factory) -> bool:
    """同一规则在**事件触发时间**的冷却窗口内是否已有判读（确定性去重）。

    ⚠️ 用事件时间而非判读时间：否则一条 3 小时前发生的旧事件（补判读时
    triage.created_at=现在）会把当前的新事件误判成"冷却期重复"而永久静默
    （2026-09-08 单测抓到）。
    """
    base = event.triggered_at or beijing_now_naive()
    lo = base - timedelta(minutes=COOLDOWN_MINUTES)
    hi = base + timedelta(minutes=COOLDOWN_MINUTES)
    with session_factory() as db:
        near = db.execute(
            select(AlertEvent.id).where(
                AlertEvent.rule_id == event.rule_id,
                AlertEvent.id != event.id,
                AlertEvent.triggered_at >= lo,
                AlertEvent.triggered_at <= hi,
            )
        ).scalars().all()
        if not near:
            return False
        judged = set(
            db.execute(
                select(AgentTriage.event_id).where(AgentTriage.event_id.in_(near))
            ).scalars().all()
        )
        return bool(judged)


async def triage_event(event: AlertEvent, session_factory=None) -> dict | None:
    """判读单条事件并落库。已有判读则返回既有结论（幂等）。"""
    sf = session_factory or get_session_factory()
    with sf() as db:
        exist = db.execute(
            select(AgentTriage).where(AgentTriage.event_id == event.id)
        ).scalars().first()
        if exist is not None:
            return _dump(exist)

    # 1) 确定性去重
    with contextlib.suppress(Exception):
        if _already_recent(event, sf):
            return _save(event.id, "ignore", "同类事件在冷却窗口内已提醒（去重）", "rules", sf)

    # 2) LLM 判读
    ctx = _event_context(event, sf)
    got = await _llm_verdict(ctx)
    if got is None:
        return _save(event.id, "notify", "AI 判读不可用，按规则提醒（未做噪音过滤）",
                     "llm_fallback", sf)
    verdict, reason = got
    # 3) 响应建议（P1-5 盘中回路，只读）：notify/escalate 追加题材上下文与观察指引
    if verdict in ("notify", "escalate"):
        with contextlib.suppress(Exception):
            note = _response_note(event, sf)
            if note:
                reason = f"{reason}｜{note}"
    return _save(event.id, verdict, reason, "llm", sf)


def _response_note(event: AlertEvent, sf) -> str | None:
    """只读响应建议：官方题材归属 + 当日状态 + 观察指引（纯数据拼接，零 LLM）。

    上下文任一缺失 → 返回 None（不臆造）。红线：不含操作指令，只给观察视角。
    """
    symbol = event.symbol
    if not symbol:
        return None
    svc = getattr(_app_state_for_triage(), "theme_catalog", None)
    if svc is None:
        return None
    try:
        themes = [t.get("theme_name") for t in svc.get_official_for_symbol(symbol)]
        themes = [t for t in themes if t][:2]
    except Exception:  # noqa: BLE001
        themes = []
    snap = getattr(getattr(_app_state_for_triage(), "snapshot_service", None), "snapshot", None) or []
    pct = next((r.get("change_pct") for r in snap if r.get("symbol") == symbol), None)
    parts = []
    if themes:
        parts.append(f"官方题材：{'、'.join(themes)}")
    if pct is not None:
        parts.append(f"今日 {pct:+.2f}%")
    if parts:
        parts.append("建议关注所属题材梯队是否延续（看板可查），本提醒不构成买卖建议")
    return "；".join(parts) if parts else None


_APP_STATE: Any = None


def set_app_state(app: Any) -> None:
    """triage 是无 request 上下文的后台服务——app 引用在 lifespan 注入（main.py）。"""
    global _APP_STATE
    _APP_STATE = app


def _app_state_for_triage():
    return _APP_STATE


def _save(event_id: int, verdict: str, reason: str, model: str, sf) -> dict:
    with sf() as db:
        row = AgentTriage(event_id=event_id, verdict=verdict, reason=reason, model=model)
        db.add(row)
        # 2026-09-08 用户指令「触发记录状态不再需要确认」：判读完成即终态，
        # 事件自动置 acknowledged——悬浮球/控制台不再有「待确认」人工环节。
        ev = db.get(AlertEvent, event_id)
        if ev is not None:
            ev.acknowledged = 1
        symbol = ev.symbol if ev is not None else None
        rule_name, trigger_value = "", None
        if ev is not None:
            trigger_value = ev.trigger_value
            with contextlib.suppress(Exception):
                rule = db.get(AlertRule, ev.rule_id)
                rule_name = (rule.name or "") if rule is not None else ""
        db.commit()
        db.refresh(row)
        out = _dump(row)

    # escalate → 任务中心待办（P1-36）。**在 session 之外登记**：record_escalation
    # 会另开一个 session（SQLite 同一时刻只允许一个写事务，嵌套会锁等待）。
    # 必须传 sf（生产=全局工厂；单测=tmp 库），否则判读单测会往生产库写待办。
    if verdict == "escalate":
        _register_escalation(event_id, reason, symbol, rule_name, trigger_value, sf)
    return out


def _register_escalation(event_id: int, reason: str, symbol: str | None,
                         rule_name: str, trigger_value: object, sf) -> str | None:
    """把 escalate 判读登记为待办（纯留痕，不启动执行）。登记失败不影响判读落库。"""
    from app.services.agent_tasks import record_escalation

    head = f"{symbol or '（无代码）'} {rule_name or '告警'}".strip()
    try:
        return record_escalation(
            event_id=event_id,
            summary=f"{head}｜{reason}"[:200],
            detail={
                "symbol": symbol, "rule": rule_name,
                "trigger_value": trigger_value, "reason": reason,
            },
            session_factory=sf,
        )
    except Exception as exc:  # noqa: BLE001  待办是增强层，失败只留痕不上抛
        log.warning("escalation task register failed for event %s: %s", event_id, exc)
        return None


def _dump(row: AgentTriage) -> dict:
    return {
        "id": row.id, "event_id": row.event_id, "verdict": row.verdict,
        "reason": row.reason, "model": row.model, "acked": bool(row.acked),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def triage_pending(limit: int = 20, session_factory=None) -> list[dict]:
    """扫描**未判读**事件并判读（worker 每轮调用）。

    ⚠️ 必须「先查未判读、再按时间倒序」——原实现取「最近 limit 条」再过滤未判读，
    一旦某时段事件量 > limit，较早的未判读事件就被新事件**永久挤出窗口**，
    再也不会被判读（2026-09-10 实测库内 75 条从未判读，含 falsify 18 /
    flow_surge 46 / break_rate 7 / high_board_break 3；而 falsify 是方向证伪这类
    高价值信号）。单轮判读量仍由 RECENT_LIMIT 封顶，避免 LLM 突发批量。
    """
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(AlertEvent)
            .where(AlertEvent.id.not_in(select(AgentTriage.event_id)))
            .order_by(AlertEvent.triggered_at.desc())
            .limit(max(int(limit), 1))
        ).scalars().all()
        pending = list(rows)[:RECENT_LIMIT]
    out = []
    for ev in pending:
        try:
            res = await triage_event(ev, sf)
            if res:
                out.append(res)
        except Exception as exc:  # noqa: BLE001  单条失败不影响其他
            log.warning("triage failed for event %s: %s", ev.id, exc)
    return out


def list_triage(limit: int = 50, verdict: str | None = None, session_factory=None) -> list[dict]:
    sf = session_factory or get_session_factory()
    with sf() as db:
        q = select(AgentTriage).order_by(AgentTriage.created_at.desc())
        if verdict:
            q = q.where(AgentTriage.verdict == verdict)
        return [_dump(r) for r in db.execute(q.limit(limit)).scalars().all()]


#: 气泡时效：超过该窗口的旧判读不再弹出（进控制台告警页仍可查）——
#: 2026-09-09 用户反馈「上午看到 13:32 的旧提醒」即无时效过滤所致
BUBBLE_MAX_AGE_HOURS = 6


def pending_bubbles(limit: int = 5, session_factory=None) -> list[dict]:
    """悬浮球待提醒：notify 且未确认且**6 小时内**的（按事件时间倒序）。

    时效过滤：昨天的旧判读不再挂在悬浮球（历史进控制台告警页）。
    每条必须带 symbol+name（2026-09-09 用户指令：缺任一视为无效提醒）——
    name 从事件快照补，快照也没有则整条过滤（宁缺毋滥）。
    """
    sf = session_factory or get_session_factory()
    cutoff = beijing_now_naive() - timedelta(hours=BUBBLE_MAX_AGE_HOURS)
    with sf() as db:
        rows = db.execute(
            select(AgentTriage).where(AgentTriage.verdict == "notify", AgentTriage.acked == 0)
            .order_by(AgentTriage.id.desc()).limit(limit * 4)
        ).scalars().all()
        out = []
        for t in rows:
            ev = db.get(AlertEvent, t.event_id)
            if ev is None:
                continue
            if ev.triggered_at and ev.triggered_at < cutoff:
                continue  # 过时效：不弹（历史可查，不打扰）
            if not ev.symbol or ev.symbol == "000000":
                continue  # 无代码 = 无效个股提醒（方向级事件走通知中心）
            snap: dict = {}
            if isinstance(ev.snapshot, str):
                with contextlib.suppress(Exception):
                    snap = json.loads(ev.snapshot)
            elif isinstance(ev.snapshot, dict):
                snap = ev.snapshot
            name = str(snap.get("name") or "").strip()
            if not name:
                continue  # 无名称 = 无效提醒（2026-09-09 用户指令：缺任一即无效）
            item = _dump(t)
            item["symbol"] = ev.symbol
            item["name"] = name
            item["trigger_value"] = ev.trigger_value
            item["threshold"] = ev.threshold
            out.append(item)
            if len(out) >= limit:
                break
        return out


def ack_triage(triage_id: int, session_factory=None) -> bool:
    sf = session_factory or get_session_factory()
    with sf() as db:
        row = db.get(AgentTriage, triage_id)
        if row is None:
            return False
        row.acked = 1
        db.commit()
        return True


async def triage_loop(stop: asyncio.Event, interval: float = 30.0) -> None:
    """后台判读 worker：每 interval 扫一次未判读事件（延迟 ≤30s，够用且不刷屏）。"""
    while not stop.is_set():
        try:
            await triage_pending()
        except Exception:
            log.exception("alert triage loop failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
