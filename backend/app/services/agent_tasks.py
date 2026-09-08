"""AI 控制台任务执行器（方案 P0：只上 L0 只读/生成类任务）。

设计要点：
- **状态机落库 + 内存协程运行**：任务行先写 queued，再由 asyncio 协程推进；
  重启后 running 状态的行会残留（P0 明确：重启时把残留 running 标为 failed，
  不假装还在跑——三态纪律的"缺失不冒充"）。
- **步骤轨迹**：每个 handler 用 `step()` 上下文记录一步（输入/输出/耗时/LLM 信息），
  失败也记录——可追溯三件套第一件。
- **同类型互斥**：同一 type 同时只允许一个运行，防止重复消耗数据源配额
  （复盘会拉两天涨停池 + 全市场宽度，配额敏感）。
- **取消**：只取消尚未开始/可中断的步骤；已完成的步骤与轨迹保留。
- **审计**：任务创建/完成/取消都写 AgentAudit（actor/action/target/before/after）。

P0 只注册 L0 任务（review / data_check）；L1/L2 写类任务在参数配置模块（P1）
接入时按同一套 status/audit 机制扩展，`risk_level` 字段已预留。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.agent import TERMINAL_STATUSES, AgentAudit, AgentTask

log = logging.getLogger(__name__)

#: 任务类型 → (展示名, 风险等级, 说明)
TASK_TYPES: dict[str, dict[str, str]] = {
    "review": {
        "label": "生成复盘报告",
        "risk": "L0",
        "desc": "按指定交易日跑一次盘后复盘（rules/llm 由 ASHARE_REVIEW_MODEL 决定），报告落库",
    },
    "data_check": {
        "label": "数据体检",
        "risk": "L0",
        "desc": "只读检查数据源能力、全市场快照规模、今日告警计数，输出健康摘要",
    },
}

_APP: Any = None
#: type -> asyncio.Task（同类型互斥）
_RUNNING: dict[str, asyncio.Task] = {}
#: task_id -> asyncio.Task（取消用）
_HANDLES: dict[str, asyncio.Task] = {}


def init_agent_runtime(app: Any) -> None:
    """lifespan 注入 app（任务 handler 需要 state 上的服务）。"""
    global _APP
    _APP = app


def record_audit(
    *,
    actor: str,
    action: str,
    target: str = "",
    before: Any = None,
    after: Any = None,
    task_id: str | None = None,
    rollback_ref: str | None = None,
) -> None:
    """写一条审计（执行层任何状态变更都要调用）。失败只记日志，不阻断主流程。"""
    try:
        with get_session_factory()() as db:
            db.add(AgentAudit(
                actor=actor, action=action, target=target,
                before=None if before is None else json.dumps(before, ensure_ascii=False, default=str),
                after=None if after is None else json.dumps(after, ensure_ascii=False, default=str),
                task_id=task_id, rollback_ref=rollback_ref,
            ))
            db.commit()
    except Exception as exc:  # noqa: BLE001  审计失败不阻断业务
        log.warning("agent audit write failed (%s/%s): %s", action, target, exc)


def _load(row: AgentTask) -> dict:
    """ORM 行 → API dict（JSON 字段解码，缺失给空容器而不是抛错）。"""
    def _j(raw: str | None, default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return default

    return {
        "id": row.id,
        "type": row.type,
        "status": row.status,
        "params": _j(row.params, {}),
        "steps": _j(row.steps, []),
        "result_ref": _j(row.result_ref, None),
        "error": _j(row.error, None),
        "risk_level": row.risk_level,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


class _StepRecorder:
    """步骤轨迹记录器：每步写库一次（任务量小，不做批量优化）。"""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.index = 0

    def add(self, *, name: str, input_summary: str, output_summary: str,
            duration_ms: int, ok: bool, llm: dict | None = None) -> None:
        self.index += 1
        step = {
            "index": self.index, "name": name,
            "input_summary": input_summary, "output_summary": output_summary,
            "duration_ms": duration_ms, "ok": ok,
        }
        if llm:
            step["llm"] = llm
        try:
            with get_session_factory()() as db:
                row = db.get(AgentTask, self.task_id)
                if row is None:
                    return
                steps = json.loads(row.steps or "[]")
                steps.append(step)
                row.steps = json.dumps(steps, ensure_ascii=False, default=str)
                db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("agent step write failed (%s): %s", self.task_id, exc)

    @contextlib.contextmanager
    def track(self, name: str, input_summary: str = ""):
        """同步/异步通用的步骤包裹（异步 handler 内用 async with 版）。"""
        t0 = time.perf_counter()
        out: dict = {}
        try:
            yield out
        except Exception as exc:
            self.add(name=name, input_summary=input_summary,
                     output_summary=f"失败：{type(exc).__name__}: {exc}",
                     duration_ms=int((time.perf_counter() - t0) * 1000), ok=False)
            raise
        else:
            self.add(name=name, input_summary=input_summary,
                     output_summary=str(out.get("summary") or ""),
                     duration_ms=int((time.perf_counter() - t0) * 1000), ok=True)


# ---------------------------------------------------------------- handlers


async def _h_review(rec: _StepRecorder, params: dict, app: Any) -> dict:
    """复盘任务：调用既有 review service（rules/llm 由配置决定）。"""
    svc = getattr(app.state, "review", None) if app is not None else None
    if svc is None:
        raise RuntimeError("复盘服务未就绪")
    from datetime import datetime as _dt

    raw_date = params.get("trade_date")
    td = _dt.strptime(raw_date, "%Y%m%d").date() if raw_date else None
    version = params.get("methodology_version")
    rec.add(name="解析参数", input_summary=str(params),
            output_summary=f"trade_date={td or '上一交易日'} version={version or '默认'}",
            duration_ms=0, ok=True)
    t0 = time.perf_counter()
    report = await svc.run(td, methodology_version=version)
    data = report.model_dump() if hasattr(report, "model_dump") else dict(report)
    date_str = str(data.get("brief_date") or data.get("trade_date") or "")
    rec.add(name="执行复盘", input_summary=f"trade_date={td}",
            output_summary=f"报告生成完成（{date_str}）",
            duration_ms=int((time.perf_counter() - t0) * 1000), ok=True,
            llm={"model": str(getattr(svc, "requested_model", "") or "rules"),
                 "prompt_hash": "", "enhanced": bool(data.get("llm_enhanced"))})
    return {"kind": "report", "id": date_str}


async def _h_data_check(rec: _StepRecorder, params: dict, app: Any) -> dict:
    """数据体检（只读）：数据源能力 + 单点源清单 + 快照规模 + 近 12h 告警计数。"""
    from app.services.provider_capabilities import (
        CAPABILITIES,
        providers_supporting,
        single_point_methods,
    )

    t0 = time.perf_counter()
    methods = {m for caps in CAPABILITIES.values() for m in caps}
    supported_methods = [m for m in methods if providers_supporting(m)]
    spm = single_point_methods()
    rec.add(name="数据源能力", input_summary="",
            output_summary=(f"数据源 {len(CAPABILITIES)} 个 / 方法 {len(methods)} 项，"
                            f"任一源可用 {len(supported_methods)} 项，单点源 {len(spm)} 项"),
            duration_ms=int((time.perf_counter() - t0) * 1000), ok=True)

    snap = getattr(getattr(app, "state", None), "snapshot_service", None) if app is not None else None
    rows = getattr(snap, "snapshot", None) or []
    age = None
    try:
        age = getattr(snap, "last_refresh_at", None)
    except Exception:  # noqa: BLE001
        age = None
    rec.add(name="全市场快照", input_summary="", output_summary=f"{len(rows)} 只，最近刷新 {age or '—'}",
            duration_ms=0, ok=bool(rows))

    today_events = 0
    try:
        from app.models.alert import AlertEvent
        from datetime import timedelta

        with get_session_factory()() as db:
            cutoff = datetime.utcnow() - timedelta(hours=12)
            today_events = len(db.execute(
                select(AlertEvent.id).where(AlertEvent.triggered_at >= cutoff)
            ).scalars().all())
    except Exception as exc:  # noqa: BLE001  体检是增强层，失败只降级
        log.debug("data_check events failed: %s", exc)
    rec.add(name="近 12h 告警", input_summary="", output_summary=f"{today_events} 条", duration_ms=0, ok=True)

    return {"kind": "artifact", "id": f"data-check-{int(time.time())}"}


_HANDLERS: dict[str, Callable[[_StepRecorder, dict, Any], Awaitable[dict]]] = {
    "review": _h_review,
    "data_check": _h_data_check,
}


# ---------------------------------------------------------------- 生命周期


def _set_status(task_id: str, status: str, **fields: Any) -> None:
    with get_session_factory()() as db:
        row = db.get(AgentTask, task_id)
        if row is None:
            return
        row.status = status
        for k, v in fields.items():
            if k == "result_ref" or k == "error":
                setattr(row, k, json.dumps(v, ensure_ascii=False, default=str))
            else:
                setattr(row, k, v)
        db.commit()


async def _execute(task_id: str, type_: str, params: dict) -> None:
    rec = _StepRecorder(task_id)
    _set_status(task_id, "running", started_at=datetime.utcnow())
    try:
        handler = _HANDLERS[type_]
        result = await handler(rec, params, _APP)
        _set_status(task_id, "succeeded", result_ref=result, finished_at=datetime.utcnow())
        record_audit(actor="ai", action="task.finish", target=type_, after={"status": "succeeded"},
                     task_id=task_id)
        log.warning("[AGENT-TASK] %s %s 完成", type_, task_id)
    except asyncio.CancelledError:
        _set_status(task_id, "canceled", finished_at=datetime.utcnow())
        record_audit(actor="user", action="task.cancel", target=type_, after={"status": "canceled"},
                     task_id=task_id)
        raise
    except Exception as exc:
        _set_status(task_id, "failed", error={"code": type(exc).__name__, "message": str(exc),
                                              "retryable": True}, finished_at=datetime.utcnow())
        record_audit(actor="ai", action="task.fail", target=type_,
                     after={"status": "failed", "error": str(exc)}, task_id=task_id)
        log.warning("[AGENT-TASK] %s %s 失败：%s", type_, task_id, exc)
    finally:
        _RUNNING.pop(type_, None)
        _HANDLES.pop(task_id, None)


def create_task(type_: str, params: dict | None = None, *, created_by: str = "user") -> dict:
    """创建并启动任务（同类型互斥）。返回任务 dict。"""
    if type_ not in TASK_TYPES:
        raise ValueError(f"未知任务类型：{type_}（可用：{'、'.join(TASK_TYPES)}）")
    if type_ in _RUNNING:
        raise RuntimeError(f"「{TASK_TYPES[type_]['label']}」正在运行中，请等待完成")
    params = params or {}
    task_id = uuid.uuid4().hex
    with get_session_factory()() as db:
        db.add(AgentTask(
            id=task_id, type=type_, status="queued",
            params=json.dumps(params, ensure_ascii=False, default=str),
            risk_level=TASK_TYPES[type_]["risk"], created_by=created_by,
        ))
        db.commit()
    record_audit(actor=created_by, action="task.create", target=type_, after=params, task_id=task_id)

    loop = asyncio.get_event_loop()
    handle = loop.create_task(_execute(task_id, type_, params), name=f"agent-task-{type_}")
    _RUNNING[type_] = handle
    _HANDLES[task_id] = handle
    return get_task(task_id) or {"id": task_id, "type": type_, "status": "queued"}


def get_task(task_id: str) -> dict | None:
    with get_session_factory()() as db:
        row = db.get(AgentTask, task_id)
        return _load(row) if row else None


def list_tasks(limit: int = 30, type_: str | None = None) -> list[dict]:
    with get_session_factory()() as db:
        q = select(AgentTask)
        if type_:
            q = q.where(AgentTask.type == type_)
        rows = db.execute(q.order_by(AgentTask.created_at.desc()).limit(limit)).scalars().all()
        return [_load(r) for r in rows]


def cancel_task(task_id: str) -> dict | None:
    """取消运行中任务（已终态的返回当前状态，不报错）。"""
    row = get_task(task_id)
    if row is None:
        return None
    if row["status"] in TERMINAL_STATUSES:
        return row
    handle = _HANDLES.get(task_id)
    if handle is not None and not handle.done():
        handle.cancel()
    else:
        _set_status(task_id, "canceled", finished_at=datetime.utcnow())
        record_audit(actor="user", action="task.cancel", target=row["type"],
                     after={"status": "canceled"}, task_id=task_id)
    return get_task(task_id)


def list_audit(limit: int = 50, target: str | None = None, task_id: str | None = None) -> list[dict]:
    with get_session_factory()() as db:
        q = select(AgentAudit)
        if target:
            q = q.where(AgentAudit.target == target)
        if task_id:
            q = q.where(AgentAudit.task_id == task_id)
        rows = db.execute(q.order_by(AgentAudit.at.desc()).limit(limit)).scalars().all()
        return [{
            "id": r.id, "actor": r.actor, "action": r.action, "target": r.target,
            "before": json.loads(r.before) if r.before else None,
            "after": json.loads(r.after) if r.after else None,
            "task_id": r.task_id, "rollback_ref": r.rollback_ref,
            "at": r.at.isoformat() if r.at else None,
        } for r in rows]


def reconcile_on_startup() -> int:
    """启动时把残留 running 任务标为 failed（重启=进程没了，不假装还在跑）。"""
    with get_session_factory()() as db:
        rows = db.execute(
            select(AgentTask).where(AgentTask.status.in_(("queued", "running")))
        ).scalars().all()
        n = 0
        for r in rows:
            r.status = "failed"
            r.error = json.dumps({"code": "Interrupted", "message": "服务重启，任务中断",
                                  "retryable": True}, ensure_ascii=False)
            r.finished_at = datetime.utcnow()
            n += 1
        if n:
            db.commit()
    return n
