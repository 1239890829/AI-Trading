"""告警 AI 判读层（方案 docs/summary/ai-evolution.md P1）。

问题：规则触发 ≠ 值得提醒。价格/炸板/异动规则在震荡日能刷出几十条，
人工看等于没有——这正是"预警"沦为噪音的原因。

设计：
1. **确定性规则优先**（不消耗 LLM）：过期事件、完全重复的事件和已知系统
   运行状态先处理；同规则同标的的新事件种类、方向、阈值与版本不互相静音。
   规则去重记录不延长冷却期，同一事件的并发请求共享一次判读。
2. **LLM 判读**：把事件 + 当下环境（情绪相位、是否持仓、近 1h 同类事件数）
   喂给模型，要求严格输出 `{verdict, reason}`；verdict ∈ notify/ignore/escalate。
3. **降级**：LLM 不可用/超时/输出非法 → verdict=notify 且 model 标
   `llm_fallback`，reason 保留失败类别并明确本条未经过 AI 判读。
   告警独立日次数预算默认不限，但单次资源和未知用量硬门继续执行。

纪律：判读**不发飞书**（2026-09-08 推送定稿：飞书只保留盘中买点卡），
只在系统内（悬浮球 + 控制台）呈现；`escalate` 由 `_save` 登记为任务中心待办
（P1-36，2026-09-10 接通：`record_escalation` 纯登记 + `needs_confirm` 初始态，
人工在任务中心处置 → `resolve_task`）。
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from typing import Any
from datetime import timedelta

from sqlalchemy import func, select

from app.core.db import get_session_factory
from app.models.agent import AgentAudit, AgentTriage
from app.models.alert import AlertEvent, AlertRule
from app.core.bjtime import BJ_TZ, beijing_now_naive

log = logging.getLogger(__name__)


#: 同规则、标的与事件事实的冷却窗口；ignore 去重记录不续期。
COOLDOWN_MINUTES = 30
#: 判读时参考的"近 1h 同类事件"上限（防 prompt 过长）
RECENT_LIMIT = 5

_VERDICTS = ("notify", "ignore", "escalate")

_JEV_CRITERIA = {
    "notify": "需要用户现在看到：与持仓/自选直接相关，或是明确趋势/风险变化；一般级重要性。",
    "ignore": "重复、噪音、幅度很小、过时或对用户决策没有可解释影响。",
    "escalate": "影响面大或系统性异常，不只是普通提醒，应进入任务中心进一步处理。",
}
_JEV_REASON = {
    "notify": "Jev结构化判读：值得提醒",
    "ignore": "Jev结构化判读：噪音/重复，可忽略",
    "escalate": "Jev结构化判读：影响面较大，升级处理",
}

_SYSTEM_PROMPT = (
    "你是 A 股盘中告警判读助手。给定一条系统告警事件与当下环境，判断它值不值得提醒用户。\n"
    "只输出 JSON：{\"verdict\": \"notify|ignore|escalate\", \"reason\": \"中文一句话，≤30 字\"}\n"
    "判据：\n"
    "- notify：与用户持仓/自选相关，或是明确的趋势转折、风险信号，需要马上看\n"
    "- ignore：重复事件、噪音、幅度极小、已过时效、对决策无影响\n"
    "- escalate：影响面大（全市场级风险、系统性异常），需要进待办处理\n"
    "- relationship 中 null 表示关系未知；未持仓/未自选不代表机会无效。\n"
    "- 规则触发不等于持续上涨；区分首次事实、重复波动与失效风险。\n"
    "纪律：不提供买卖建议；不确定时选 notify（宁可提醒，不可漏掉风险）。"
)


def _snapshot(event: AlertEvent) -> dict:
    snap = event.snapshot
    if isinstance(snap, str):
        with contextlib.suppress(ValueError, TypeError):
            snap = json.loads(snap)
    return snap if isinstance(snap, dict) else {}


def _event_context(event: AlertEvent, session_factory) -> dict:
    """构造判读输入（纯 DB 读，不外呼）。"""
    snap = _snapshot(event)

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
    # Send only relationship flags, never account amounts, costs or trade history.
    relationship: dict[str, bool | None] = {"watchlist": None, "paper_held": None, "recorded_held": None}
    try:
        from app.models.watchlist import WatchlistItem
        from app.models.paper import PaperPosition, SCOPE_MAIN
        from app.services.real_position_service import load_positions

        with session_factory() as db:
            relationship["watchlist"] = db.scalar(select(WatchlistItem.id).where(
                WatchlistItem.symbol == event.symbol).limit(1)) is not None
            relationship["paper_held"] = db.scalar(select(PaperPosition.id).where(
                PaperPosition.symbol == event.symbol, PaperPosition.scope == SCOPE_MAIN,
                PaperPosition.quantity > 0).limit(1)) is not None
        relationship["recorded_held"] = any(
            position.symbol == event.symbol and position.quantity > 0
            for position in load_positions(session_factory)
        )
    except Exception:
        log.info("alert triage relationship unavailable", exc_info=False)
    return {
        "rule": rule_name,
        "condition": condition,
        "symbol": event.symbol,
        "trigger_value": event.trigger_value,
        "threshold": event.threshold,
        "text": str(snap.get("text") or "")[:200],
        "kind": snap.get("kind") or "",
        "direction": str(snap.get("direction") or "")[:80],
        "relationship": relationship,
        "event_time": event.triggered_at.isoformat() if event.triggered_at else None,
        "last_hour_same_rule": len(recent),
    }


async def _jev_verdict(ctx: dict) -> dict | None:
    """Jev 三分类前置层；失败/非法返回 None，调用方继续 DeepSeek/规则路径。"""
    from app.core.config import settings
    from app.core.jev_client import evaluate

    mode = str(getattr(settings, "jev_alert_triage_mode", "off") or "off").strip().lower()
    if mode not in {"shadow", "cascade"}:
        return None
    questions = {
        "verdict": {
            "type": "choice",
            "instructions": {
                "task": "根据告警事件与上下文，选择本系统应如何处理该提醒。",
                "rules": [
                    "只判断提醒优先级，不给买卖建议。",
                    "若证据不足，不要把普通提醒升级为系统性异常。",
                    "重复、过时或决策无关的信息应 ignore。",
                ],
            },
            "criteria": _JEV_CRITERIA,
        }
    }

    def _call() -> dict:
        return evaluate(ctx, questions, purpose="alert_triage")

    worker = asyncio.create_task(asyncio.to_thread(_call))
    try:
        # The adapter owns the HTTP deadline. Do not release a shadow slot while
        # a timed-out to_thread worker is still running in the executor.
        result = await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Cancellation is cooperative: keep the slot occupied until synchronous
        # HTTP work really drains. Otherwise a restart can spawn another worker.
        await asyncio.shield(worker)
        raise
    except Exception as exc:  # noqa: BLE001 — 增强层故障不得拖垮告警
        log.info("alert triage jev unavailable: %s", type(exc).__name__)
        return None
    if not result.get("ok"):
        return None
    answer = (result.get("answers") or {}).get("verdict")
    if not isinstance(answer, dict):
        return None
    verdict = str(answer.get("choice") or "").strip().lower()
    confidence = answer.get("confidence")
    probabilities = answer.get("probabilities")
    if verdict not in _VERDICTS or type(confidence) not in (int, float):
        return None
    if not 0.0 <= float(confidence) <= 1.0 or not isinstance(probabilities, dict):
        return None
    if set(probabilities) != set(_VERDICTS):
        return None
    if (any(type(p) not in (int, float) or not 0.0 <= p <= 1.0 for p in probabilities.values())
            or abs(sum(probabilities.values()) - 1.0) > max(0.001, .0051 * len(_VERDICTS))):
        return None
    return {
        "verdict": verdict,
        "confidence": float(confidence),
        "model": str(result.get("model") or "jev"),
        "latency_ms": float(result.get("latency_ms") or 0.0),
    }


async def _llm_verdict(ctx: dict, session_factory=None, failure: dict | None = None) -> tuple[str, str] | None:
    """LLM 判读；返回 (verdict, reason)，不可用/非法返回 None（调用方降级）。"""
    from app.core.config import settings
    from app.core.llm_client import LLMError, chat_completion, extract_json_object

    def unavailable(kind: str) -> None:
        if failure is not None:
            failure["kind"] = kind
        return None

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
    ]

    # Deterministically unavailable providers do not consume a budget slot.
    if not settings.review_llm_model:
        return unavailable("not_configured")
    if settings.llm_provider == "claude_cli":
        from app.core.llm_client import resolve_cli_path
        if resolve_cli_path(settings.llm_cli_path) is None:
            return unavailable("unavailable")
    elif not (settings.review_llm_base_url and settings.review_llm_api_key and settings.review_llm_model):
        return unavailable("not_configured")

    from app.services import agent_budget
    sf = session_factory or get_session_factory()
    lease = None
    # The shared CLI adapter enforces a 120s minimum, so reserve its real deadline.
    timeout = 120.0 if settings.llm_provider == "claude_cli" else 30.0
    try:
        lease = agent_budget.reserve(
            agent_budget.SCOPE_ALERT_TRIAGE_LLM, purpose="triage.llm", kind="model",
            provider=settings.llm_provider, model=settings.review_llm_model,
            timeout_seconds=timeout, max_retries=0,
            input_chars=sum(len(str(m.get("content") or "")) for m in messages),
            session_factory=sf,
        )
        agent_budget.start(lease["id"], sf)
    except agent_budget.BudgetError as exc:
        if lease is not None:
            agent_budget.release(lease["id"], sf)
        log.info("alert triage llm budget blocked: %s", exc)
        return unavailable(exc.code)
    usage_box = {"value": None}

    def _call() -> str:
        return chat_completion(
            base_url=settings.review_llm_base_url,
            api_key=settings.review_llm_api_key,
            model=settings.review_llm_model,
            messages=messages,
            provider=settings.llm_provider,
            cli_path=settings.llm_cli_path,
            timeout=timeout,
            usage_callback=lambda value: usage_box.__setitem__("value", value),
        )

    worker = asyncio.create_task(asyncio.to_thread(_call))
    try:
        # The adapter owns the deadline. Canceling to_thread cannot stop external I/O.
        raw = await asyncio.shield(worker)
        agent_budget.finish(
            lease["id"], state="succeeded", usage=usage_box["value"],
            output_chars=len(raw), attempts=1, session_factory=sf,
        )
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await asyncio.shield(worker)
        with contextlib.suppress(Exception):
            agent_budget.finish(lease["id"], state="canceled", usage=usage_box["value"],
                                error_kind="cancelled", session_factory=sf)
        raise
    except Exception as exc:  # noqa: BLE001  LLM 是增强层，失败必须降级
        kind = (exc.kind.value if isinstance(exc, LLMError) else exc.code
                if isinstance(exc, agent_budget.BudgetError) else "timeout"
                if isinstance(exc, TimeoutError) else "unavailable")
        with contextlib.suppress(Exception):
            agent_budget.finish(lease["id"], state="failed", usage=usage_box["value"],
                                error_kind=kind, session_factory=sf)
        log.info("alert triage llm unavailable: kind=%s", kind)
        return unavailable(kind)

    try:
        data = extract_json_object(raw)
        verdict = str((data or {}).get("verdict") or "").strip().lower()
        reason = str((data or {}).get("reason") or "").strip()
        if verdict not in _VERDICTS:
            return unavailable("bad_response")
        return verdict, reason[:60]
    except Exception:  # noqa: BLE001
        return unavailable("bad_response")


def _fallback_reason(kind: str | None) -> str:
    reason = {
        "budget_exhausted": "今日 AI 判读次数预算已用尽",
        "usage_unknown": "AI 调用用量待核实，已暂停自主判读",
        "reservation_expired": "AI 判读预算预留已跨日，请重新核验",
        "not_configured": "AI 判读通道未配置",
        "unavailable": "AI 判读通道不可用",
        "timeout": "AI 判读调用超时",
        "quota": "AI 服务额度不足",
        "gateway_error": "AI 判读服务调用失败",
        "bad_response": "AI 判读回复格式无效",
        "empty": "AI 判读回复为空",
        "input_budget_exceeded": "AI 判读输入超过资源限制",
        "output_budget_exceeded": "AI 判读输出超过资源限制",
        "timeout_budget_exceeded": "AI 判读超时配置超过资源限制",
        "retry_budget_exceeded": "AI 判读重试配置超过资源限制",
    }.get(kind or "", "AI 判读不可用")
    return f"{reason}，按规则提醒（本条未经过 AI 判读）"


def _already_recent(event: AlertEvent, session_factory) -> bool:
    """同规则、标的和事件事实在冷却期内已有判读；规则去重不续期。

    不同kind/方向/阈值/来源版本不能互相静音。基础价格阈值的持续满足
    仍遵循原30分钟冷却；派生事件已有生产方重触发门，新的触发值可重判。
    """
    base = event.triggered_at or beijing_now_naive()
    if base.tzinfo is not None:
        base = base.astimezone(BJ_TZ).replace(tzinfo=None)
    lo = base - timedelta(minutes=COOLDOWN_MINUTES)
    key = (event.symbol or "").strip() or "000000"
    norm_sym = func.coalesce(func.nullif(AlertEvent.symbol, ""), "000000")
    snap = _snapshot(event)

    def identity(snapshot: dict) -> tuple:
        ref = snapshot.get("execution_ref")
        return (
            snapshot.get("kind") or "", snapshot.get("direction") or "",
            snapshot.get("source_version") or "",
            ref.get("decision_version") if isinstance(ref, dict) else None,
        )

    with session_factory() as db:
        near = db.execute(
            select(AlertEvent).join(AgentTriage, AgentTriage.event_id == AlertEvent.id).where(
                AlertEvent.rule_id == event.rule_id, norm_sym == key,
                AlertEvent.id != event.id,
                AlertEvent.triggered_at >= lo, AlertEvent.triggered_at <= base,
                ~((AgentTriage.model == "rules") & (AgentTriage.verdict == "ignore")),
            )
        ).scalars().all()
        for previous in near:
            if identity(_snapshot(previous)) != identity(snap) or previous.threshold != event.threshold:
                continue
            if snap.get("kind") and previous.trigger_value != event.trigger_value:
                continue
            return True
    return False


# One in-flight comparison, no queue: a slow shadow call cannot accumulate
# workers or hold up the baseline triage loop. Only metadata is persisted.
_shadow_task: asyncio.Task | None = None


def _shadow_receipt(event_id, ctx, baseline, recommendation, state, sf):
    try:
        with sf() as db:
            db.add(AgentAudit(
                actor="scheduler", action="triage.shadow", target=str(event_id),
                before=json.dumps({
                    "contract": "triage-shadow-v1", "input_sha256": hashlib.sha256(
                        json.dumps(ctx, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest(),
                    "baseline": {k: baseline[k] for k in ("verdict", "model")},
                }, ensure_ascii=False),
                after=json.dumps({"state": state, "recommendation": recommendation,
                                  "adopted": False, "reason": "shadow_only"}, ensure_ascii=False),
            ))
            db.commit()
    except Exception:  # audit enhancement must not invalidate a saved reminder
        log.exception("triage shadow receipt failed for event %s", event_id)


def _schedule_shadow(event_id, ctx, baseline, sf):
    global _shadow_task
    if _shadow_task is not None and not _shadow_task.done():
        _shadow_receipt(event_id, ctx, baseline, None, "skipped_busy", sf)
        return

    async def compare():
        from app.core.jev_client import record_comparison
        recommendation = None
        try:
            recommendation = await _jev_verdict(ctx)
            if recommendation is not None:
                record_comparison("alert_triage", recommendation["verdict"],
                                  baseline["verdict"], confidence=recommendation["confidence"])
        except asyncio.CancelledError:
            _shadow_receipt(event_id, ctx, baseline, None, "canceled", sf)
            raise
        except Exception:
            log.exception("triage shadow failed for event %s", event_id)
        _shadow_receipt(event_id, ctx, baseline, recommendation,
                        "compared" if recommendation is not None else "unavailable", sf)

    _shadow_task = asyncio.create_task(compare(), name="triage-shadow")


async def drain_shadow():
    """Shutdown waits for the adapter-bounded worker; cancellation is not a kill."""
    task = _shadow_task
    if task is not None:
        await asyncio.shield(task)


async def triage_event(event: AlertEvent, session_factory=None) -> dict | None:
    """判读单条事件并落库。已有判读则返回既有结论（幂等）。"""
    sf = session_factory or get_session_factory()
    key = (sf, event.id)
    task = _triage_inflight.get(key)
    if task is None:
        task = asyncio.create_task(_triage_event(event, sf), name=f"triage-{event.id}")
        _triage_inflight[key] = task
        task.add_done_callback(lambda done: _triage_inflight.pop(key, None))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Keep both the receipt and shared result alive until the real call drains.
        with contextlib.suppress(Exception):
            await asyncio.shield(task)
        raise


_triage_inflight: dict[tuple[Any, int], asyncio.Task] = {}


async def _triage_event(event: AlertEvent, sf) -> dict | None:
    with sf() as db:
        exist = db.execute(
            select(AgentTriage).where(AgentTriage.event_id == event.id)
        ).scalars().first()
        if exist is not None:
            return _dump(exist)

    # Historical backlog remains auditable without consuming today's model calls.
    now = beijing_now_naive()
    triggered = event.triggered_at
    if triggered is not None and triggered.tzinfo is not None:
        triggered = triggered.astimezone(BJ_TZ).replace(tzinfo=None)
    if triggered is not None and triggered < now - timedelta(hours=BUBBLE_MAX_AGE_HOURS):
        return _save(event.id, "ignore", "事件已超过提醒时效，保留历史记录", "rules", sf)
    if triggered is None or triggered > now:
        return _save(event.id, "notify", "事件时间未知或异常，按规则提醒，请核对来源时间", "rules", sf)

    # 1) 确定性去重
    with contextlib.suppress(Exception):
        if _already_recent(event, sf):
            return _save(event.id, "ignore", "同类事件在冷却窗口内已提醒（去重）", "rules", sf)

    # 2) Jev 低成本结构化判读（默认 shadow，不改变用户可见行为）。
    ctx = _event_context(event, sf)
    if ctx["condition"] in {"llm_gateway_probe", "ths_reason_sentinel"}:
        return _save(event.id, "notify", "系统运行状态提醒：" + (ctx["text"] or ctx["condition"]), "rules", sf)
    from app.core.config import settings
    from app.core.jev_client import record_comparison

    mode = str(getattr(settings, "jev_alert_triage_mode", "off") or "off").strip().lower()
    jev = await _jev_verdict(ctx) if mode == "cascade" else None
    if mode == "cascade" and jev is not None:
        min_conf = float(getattr(settings, "jev_alert_triage_accept_confidence", 0.90))
        if float(jev["confidence"]) >= min_conf:
            verdict = str(jev["verdict"])
            reason = f"{_JEV_REASON[verdict]}（置信 {float(jev['confidence']):.2f}）"
            if verdict in ("notify", "escalate"):
                with contextlib.suppress(Exception):
                    note = _response_note(event, sf)
                    if note:
                        reason = f"{reason}｜{note}"
            return _save(event.id, verdict, reason, "jev", sf)

    # 3) DeepSeek 复杂判读：Jev 低置信/不可用，或 shadow 模式一律继续。
    failure: dict[str, str] = {}
    got = await _llm_verdict(ctx, sf, failure)
    if got is None:
        result = _save(event.id, "notify", _fallback_reason(failure.get("kind")),
                       "llm_fallback", sf)
        if mode == "shadow":
            _schedule_shadow(event.id, ctx, result, sf)
        return result
    verdict, reason = got
    if jev is not None:
        record_comparison(
            "alert_triage",
            str(jev["verdict"]),
            verdict,
            confidence=float(jev["confidence"]),
        )
        log.info(
            "alert triage Jev comparison mode=%s agree=%s confidence=%.3f",
            mode, str(jev["verdict"]) == verdict, float(jev["confidence"]),
        )
    # 4) 响应建议（P1-5 盘中回路，只读）：notify/escalate 追加题材上下文与观察指引
    if verdict in ("notify", "escalate"):
        with contextlib.suppress(Exception):
            note = _response_note(event, sf)
            if note:
                reason = f"{reason}｜{note}"
    result = _save(event.id, verdict, reason, "llm", sf)
    if mode == "shadow":
        _schedule_shadow(event.id, ctx, result, sf)
    return result


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
                continue  # 无代码 = 无效个股提醒（方向级事件在盘面页「事件」标签，不进通知中心）
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
    """串行完成本批判读后等待 interval；模型耗时不计作固定30秒延迟保证。"""
    try:
        while not stop.is_set():
            try:
                await triage_pending()
            except Exception:
                log.exception("alert triage loop failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)
    finally:
        if _triage_inflight:
            await asyncio.gather(*(asyncio.shield(task) for task in list(_triage_inflight.values())),
                                 return_exceptions=True)
        await drain_shadow()
