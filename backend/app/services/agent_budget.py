"""IMP-052 durable Agent budgets and model-usage receipts.

This module deliberately does **not** create another task system.  It owns two facts only:

1. a small daily quota slot reservation (LLM autonomy / automatic task / code proposal), and
2. metadata-only usage receipts (provider/model/token counts/attempts/timeout, never prompt text).

Budgeted scopes reserve a numbered slot under a DB unique constraint.  Competing processes may
race, but only one can commit the last slot.  A reservation is started immediately before external
I/O or an autonomous action.  Unstarted stale reservations can be deleted and reused; a started
row is never silently released because the external side effect may already have happened.
Unknown token usage is represented as unknown, never as zero.  For the autonomous LLM scope an
unknown started/terminal receipt blocks later reservations for the same Beijing day (fail closed).
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.bjtime import beijing_now, beijing_now_naive
from app.core.config import settings
from app.core.db import get_session_factory
from app.models.agent import AgentResourceUsage

SCOPE_AUTONOMY_LLM = "autonomy_llm"
SCOPE_AUTONOMY_TASK = "autonomy_task"
SCOPE_CODE_PROPOSAL = "code_proposal"
SCOPE_TELEMETRY = "telemetry"

_STARTED_OR_TERMINAL = {"started", "succeeded", "failed", "canceled", "unknown"}
_TERMINAL = {"succeeded", "failed", "canceled", "unknown"}


class BudgetError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _today() -> str:
    return beijing_now().date().isoformat()


def _limit_for(scope: str) -> int | None:
    if scope == SCOPE_AUTONOMY_LLM:
        return max(0, int(settings.agent_daily_llm_budget))
    if scope == SCOPE_AUTONOMY_TASK:
        return max(0, int(settings.agent_daily_task_budget))
    if scope == SCOPE_CODE_PROPOSAL:
        return 1
    if scope == SCOPE_TELEMETRY:
        return None
    raise ValueError(f"未知预算 scope: {scope}")




def check_model_input(input_chars: int) -> int:
    chars = max(0, int(input_chars or 0))
    limit = max(1, int(settings.agent_model_max_input_chars))
    if chars > limit:
        raise BudgetError("input_budget_exceeded", f"模型输入 {chars} 字符超过 Agent 上限 {limit}")
    return chars


def check_model_output(output_chars: int) -> int:
    chars = max(0, int(output_chars or 0))
    limit = max(1, int(settings.agent_model_max_output_chars))
    if chars > limit:
        raise BudgetError("output_budget_exceeded", f"模型输出 {chars} 字符超过 Agent 上限 {limit}")
    return chars


def _usage_pair(usage: dict | None) -> tuple[int | None, int | None, bool]:
    if not isinstance(usage, dict):
        return None, None, False
    inp = usage.get("input_tokens", usage.get("prompt_tokens"))
    out = usage.get("output_tokens", usage.get("completion_tokens"))
    try:
        inp_i = int(inp) if inp is not None else None
        out_i = int(out) if out is not None else None
    except (TypeError, ValueError):
        return None, None, False
    if inp_i is None or out_i is None or inp_i < 0 or out_i < 0:
        return None, None, False
    return inp_i, out_i, True


def recover_stale_reservations(session_factory=None, *, now=None) -> int:
    """Free only reservations that never started and exceeded their short lease TTL.

    Started calls/actions are intentionally *not* recovered automatically: after external I/O began
    we cannot prove it consumed zero budget.  Those rows continue to occupy the day slot.
    """
    sf = session_factory or get_session_factory()
    now = now or beijing_now_naive()
    ttl = max(1, int(settings.agent_budget_reservation_ttl_seconds))
    cutoff = now - timedelta(seconds=ttl)
    with sf() as db:
        result = db.execute(
            delete(AgentResourceUsage).where(
                AgentResourceUsage.state == "reserved",
                AgentResourceUsage.created_at < cutoff,
            )
        )
        db.commit()
        return int(result.rowcount or 0)


def _unknown_usage_exists(sf, date: str, scope: str) -> bool:
    with sf() as db:
        rows = db.execute(
            select(AgentResourceUsage.id).where(
                AgentResourceUsage.budget_date == date,
                AgentResourceUsage.scope == scope,
                AgentResourceUsage.state.in_(_STARTED_OR_TERMINAL),
                AgentResourceUsage.kind == "model",
                AgentResourceUsage.usage_known == 0,
            ).limit(1)
        ).first()
        return rows is not None


def reserve(
    scope: str,
    *,
    purpose: str,
    kind: str,
    task_id: str | None = None,
    provider: str = "",
    model: str = "",
    timeout_seconds: float = 0.0,
    max_retries: int = 0,
    input_chars: int = 0,
    session_factory=None,
) -> dict:
    """Atomically reserve one daily budget slot across processes.

    ``autonomy_llm`` additionally refuses a new call when a previous started call has unknown token
    usage.  That prevents a timeout/crash from being silently counted as 0 tokens and then retried
    indefinitely under a misleading budget display.
    """
    sf = session_factory or get_session_factory()
    recover_stale_reservations(sf)
    limit = _limit_for(scope)
    if limit is None:
        raise ValueError("unmetered telemetry does not use reserve(); call record_unmetered()")
    if limit <= 0:
        raise BudgetError("budget_exhausted", f"{scope} 每日预算为 0，拒绝执行")
    checked_input_chars = check_model_input(input_chars) if kind == "model" else max(0, int(input_chars or 0))
    timeout = max(0.0, float(timeout_seconds or 0.0))
    max_timeout = max(0.0, float(settings.agent_model_max_timeout_seconds))
    if kind == "model" and timeout > max_timeout:
        raise BudgetError(
            "timeout_budget_exceeded",
            f"模型 timeout={timeout:g}s 超过 Agent 上限 {max_timeout:g}s",
        )
    retries = max(0, int(max_retries or 0))
    max_allowed_retries = max(0, int(settings.agent_model_max_retries))
    if kind == "model" and retries > max_allowed_retries:
        raise BudgetError(
            "retry_budget_exceeded",
            f"模型重试预算 {retries} 超过 Agent 上限 {max_allowed_retries}",
        )

    date = _today()
    if scope == SCOPE_AUTONOMY_LLM and _unknown_usage_exists(sf, date, scope):
        raise BudgetError(
            "usage_unknown",
            "今日已有自主模型调用的 token 用量未知；拒绝继续消耗，须先核实/跨日恢复",
        )

    now = beijing_now_naive()
    for slot in range(1, limit + 1):
        row = AgentResourceUsage(
            id=uuid.uuid4().hex,
            budget_date=date,
            scope=scope,
            slot=slot,
            kind=str(kind or "task")[:16],
            purpose=str(purpose or "unspecified")[:80],
            task_id=(str(task_id)[:36] if task_id else None),
            provider=str(provider or "")[:32],
            model=str(model or "")[:80],
            state="reserved",
            attempts=0,
            max_retries=retries,
            timeout_ms=max(0, int(round(timeout * 1000))),
            input_chars=checked_input_chars,
            output_chars=0,
            input_tokens=None,
            output_tokens=None,
            usage_known=0,
            created_at=now,
        )
        with sf() as db:
            db.add(row)
            try:
                db.commit()
                db.refresh(row)
                return dump(row)
            except IntegrityError:
                db.rollback()  # another process owns this numbered slot; try next
    raise BudgetError("budget_exhausted", f"{scope} 今日预算已用尽（{limit}/{limit}）")


def start(usage_id: str, session_factory=None) -> dict:
    sf = session_factory or get_session_factory()
    now = beijing_now_naive()
    with sf() as db:
        result = db.execute(
            update(AgentResourceUsage).where(
                AgentResourceUsage.id == usage_id,
                AgentResourceUsage.state == "reserved",
            ).values(state="started", started_at=now, attempts=1)
        )
        if result.rowcount != 1:
            db.rollback()
            row = db.get(AgentResourceUsage, usage_id)
            if row is not None and row.state in _STARTED_OR_TERMINAL:
                return dump(row)
            raise BudgetError("reservation_lost", "预算预留已失效或被回收；拒绝启动")
        db.commit()
        return dump(db.get(AgentResourceUsage, usage_id))


def finish(
    usage_id: str,
    *,
    state: str,
    usage: dict | None = None,
    output_chars: int = 0,
    attempts: int | None = None,
    error_kind: str | None = None,
    session_factory=None,
) -> dict:
    if state not in _TERMINAL:
        raise ValueError(f"非法 usage 终态: {state}")
    sf = session_factory or get_session_factory()
    inp, out, known = _usage_pair(usage)
    output_budget_error: BudgetError | None = None
    try:
        checked_output_chars = check_model_output(output_chars)
    except BudgetError as exc:
        checked_output_chars = max(0, int(output_chars or 0))
        output_budget_error = exc
        state = "failed"
        error_kind = exc.code
    now = beijing_now_naive()
    with sf() as db:
        row = db.get(AgentResourceUsage, usage_id)
        if row is None:
            raise ValueError("usage reservation 不存在")
        if row.state in _TERMINAL:
            return dump(row)
        if row.state != "started":
            raise BudgetError("not_started", "usage reservation 尚未 start，不能写终态")
        row.state = state
        row.finished_at = now
        row.input_tokens = inp
        row.output_tokens = out
        row.usage_known = 1 if known else 0
        row.output_chars = checked_output_chars
        if attempts is not None:
            row.attempts = max(row.attempts, int(attempts))
        row.error_kind = (str(error_kind)[:32] if error_kind else None)
        db.commit()
        db.refresh(row)
        out_row = dump(row)
    if output_budget_error is not None:
        raise output_budget_error
    return out_row


def release(usage_id: str, session_factory=None) -> bool:
    """Release only a never-started reservation. Started side effects are never refunded."""
    sf = session_factory or get_session_factory()
    with sf() as db:
        result = db.execute(
            delete(AgentResourceUsage).where(
                AgentResourceUsage.id == usage_id,
                AgentResourceUsage.state == "reserved",
            )
        )
        db.commit()
        return bool(result.rowcount)


def record_unmetered(
    *,
    purpose: str,
    provider: str,
    model: str,
    state: str,
    usage: dict | None = None,
    attempts: int = 1,
    timeout_seconds: float = 0.0,
    input_chars: int = 0,
    output_chars: int = 0,
    task_id: str | None = None,
    error_kind: str | None = None,
    session_factory=None,
) -> dict:
    """Persist a metadata-only model receipt without consuming the autonomy daily quota."""
    if state not in _TERMINAL:
        raise ValueError(f"非法 telemetry 终态: {state}")
    sf = session_factory or get_session_factory()
    checked_input_chars = check_model_input(input_chars)
    output_budget_error: BudgetError | None = None
    try:
        checked_output_chars = check_model_output(output_chars)
    except BudgetError as exc:
        checked_output_chars = max(0, int(output_chars or 0))
        output_budget_error = exc
        state = "failed"
        error_kind = exc.code
    inp, out, known = _usage_pair(usage)
    now = beijing_now_naive()
    row = AgentResourceUsage(
        id=uuid.uuid4().hex,
        budget_date=_today(),
        scope=SCOPE_TELEMETRY,
        slot=None,
        kind="model",
        purpose=str(purpose or "unspecified")[:80],
        task_id=(str(task_id)[:36] if task_id else None),
        provider=str(provider or "")[:32],
        model=str(model or "")[:80],
        state=state,
        attempts=max(0, int(attempts)),
        max_retries=max(0, int(attempts) - 1),
        timeout_ms=max(0, int(round(float(timeout_seconds or 0.0) * 1000))),
        input_chars=checked_input_chars,
        output_chars=checked_output_chars,
        input_tokens=inp,
        output_tokens=out,
        usage_known=1 if known else 0,
        error_kind=(str(error_kind)[:32] if error_kind else None),
        created_at=now,
        started_at=now if attempts else None,
        finished_at=now,
    )
    with sf() as db:
        db.add(row)
        db.commit()
        db.refresh(row)
        out_row = dump(row)
    if output_budget_error is not None:
        raise output_budget_error
    return out_row


def budget_status(session_factory=None, *, date: str | None = None) -> dict:
    sf = session_factory or get_session_factory()
    date = date or _today()
    with sf() as db:
        rows = db.execute(
            select(AgentResourceUsage).where(AgentResourceUsage.budget_date == date)
        ).scalars().all()
    llm = [r for r in rows if r.scope == SCOPE_AUTONOMY_LLM]
    tasks = [r for r in rows if r.scope == SCOPE_AUTONOMY_TASK]
    telemetry = [r for r in rows if r.kind == "model"]
    llm_limit = max(0, int(settings.agent_daily_llm_budget))
    task_limit = max(0, int(settings.agent_daily_task_budget))
    input_tokens = sum(int(r.input_tokens or 0) for r in telemetry if r.usage_known)
    output_tokens = sum(int(r.output_tokens or 0) for r in telemetry if r.usage_known)
    unknown = sum(1 for r in telemetry if r.started_at is not None and not r.usage_known)
    return {
        "date": date,
        "llm_used": len(llm),
        "llm_budget": llm_limit,
        "llm_exhausted": len(llm) >= llm_limit,
        "llm_reserved": sum(r.state == "reserved" for r in llm),
        "tasks_used": len(tasks),
        "task_budget": task_limit,
        "tasks_exhausted": len(tasks) >= task_limit,
        "tasks_reserved": sum(r.state == "reserved" for r in tasks),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "token_usage_known_calls": sum(1 for r in telemetry if r.usage_known),
        "token_usage_unknown_calls": unknown,
        "token_accounting_complete": unknown == 0,
    }


def recent_usage(session_factory=None, *, limit: int = 50) -> list[dict]:
    sf = session_factory or get_session_factory()
    cap = max(1, min(500, int(limit)))
    with sf() as db:
        rows = db.execute(
            select(AgentResourceUsage)
            .order_by(AgentResourceUsage.created_at.desc(), AgentResourceUsage.id.desc())
            .limit(cap)
        ).scalars().all()
        return [dump(row) for row in rows]


def dump(row: AgentResourceUsage) -> dict:
    return {
        "id": row.id,
        "budget_date": row.budget_date,
        "scope": row.scope,
        "slot": row.slot,
        "kind": row.kind,
        "purpose": row.purpose,
        "task_id": row.task_id,
        "provider": row.provider,
        "model": row.model,
        "state": row.state,
        "attempts": row.attempts,
        "max_retries": row.max_retries,
        "timeout_ms": row.timeout_ms,
        "input_chars": row.input_chars,
        "output_chars": row.output_chars,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "usage_known": bool(row.usage_known),
        "error_kind": row.error_kind,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }
