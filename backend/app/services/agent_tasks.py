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
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from app.core.bjtime import beijing_now_naive
from app.core.config import settings
from app.core.db import get_session_factory
from app.models.agent import TERMINAL_STATUSES, AgentAudit, AgentTask

log = logging.getLogger(__name__)

#: 任务类型元数据（展示名 / 风险等级 / 说明）。
#:
#: ⚠️ **收录 ≠ 可创建**。`mutation` 与 `escalation` 是**服务侧登记**产生的条目
#: （record_mutation / record_escalation），没有 handler、不启动执行；它们出现在
#: 本表只为让任务中心与审计有统一标签来源，**不属于**「新建任务」的可选项
#: ——放进可创建清单会建出必然失败的 L1 任务（按钮点了就报 handler 缺失）。
#: 可创建集合见 `CREATABLE_TASK_TYPES`（= handler 注册表本身，唯一真相源）。
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
    "mutation": {
        "label": "系统变更留痕",
        "risk": "L1",
        "desc": "任何策略/参数/代码改动的前置登记（2026-09-08 用户指令：改动前必须先建任务）",
    },
    "escalation": {
        "label": "告警升级待办",
        "risk": "L1",
        "desc": "AI 判读判定 escalate 的告警登记（P1-36）：待人工处置后关闭，系统不自动动手",
    },
}


def record_mutation(*, source: str, kind: str, summary: str, detail: dict | None = None) -> str:
    """改动留痕（2026-09-08 用户指令「任何改动前必须先创建对应任务」）。

    与 create_task 不同：纯登记，不启动任何执行；返回 task_id 供执行结果回填
    （update_mutation_result）。A 类参数变更 / C 类代码执行 / 人工 apply 三个
    写入口必须先调用本函数，否则变更记录在任务中心不可见（追溯性缺口）。
    """
    task_id = uuid.uuid4().hex
    detail = detail or {}
    with get_session_factory()() as db:
        db.add(AgentTask(
            id=task_id, type="mutation", status="queued",
            params=json.dumps({
                "source": source, "kind": kind, "summary": summary, **detail,
            }, ensure_ascii=False, default=str),
            risk_level="L1", created_by=source,
        ))
        db.commit()
    record_audit(actor=source, action="mutation.create", target=kind, after={"summary": summary}, task_id=task_id)
    return task_id


def update_mutation_result(task_id: str, status: str, result: str) -> None:
    """回填留痕任务的执行结果（queued → succeeded/failed）。

    AgentTask 无自由 result 字段：结果文本写进 params JSON 的 `result` 键
    （前端任务详情展示 params 全量），失败时同时落 error。
    """
    with get_session_factory()() as db:
        row = db.get(AgentTask, task_id)
        if row is None:
            return
        row.status = status if status in ("succeeded", "failed") else "failed"
        try:
            params = json.loads(row.params or "{}")
        except Exception:  # noqa: BLE001
            params = {}
        params["result"] = result[:2000]
        row.params = json.dumps(params, ensure_ascii=False, default=str)
        if row.status == "failed":
            row.error = json.dumps({"code": "MutationFailed", "message": result[:500]},
                                   ensure_ascii=False)
        row.finished_at = beijing_now_naive()
        db.commit()


#: escalate 待办的任务 id 前缀（`esc-<event_id>`）——**确定性 id = 幂等**：
#: 同一告警事件重复判读不会在任务中心刷出多条待办。
ESCALATION_ID_PREFIX = "esc-"


def record_escalation(
    *, event_id: int, summary: str, detail: dict | None = None,
    source: str = "alert_triage", session_factory=None,
) -> str:
    """告警升级（escalate）→ 任务中心待办（P1-36）。

    与 `create_task` 的区别同 `record_mutation`：**纯登记、不启动执行**——
    escalate 的语义是「这件事需要人来判断」，系统自动处理反而违背判读初衷。

    与 `record_mutation` 的两点差异：
    1. **幂等**：task_id 由事件 id 派生（`esc-<event_id>`），重复登记返回同一条；
    2. **初始态 `needs_confirm`**（待确认）而非 `queued`：queued 意为"等待执行"，
       而本类条目**永远不会被执行**——用 queued 会让任务中心永久显示"排队中"
       并触发前端 3s 轮询。needs_confirm 正是"停在预览态等人工处置"，处置入口
       见 `resolve_task`。

    `session_factory` 可注入：单测里必须与 triage 同库（否则判读单测会写生产库）。
    """
    task_id = f"{ESCALATION_ID_PREFIX}{int(event_id)}"
    detail = detail or {}
    sf = session_factory or get_session_factory()
    with sf() as db:
        if db.get(AgentTask, task_id) is not None:
            return task_id
        db.add(AgentTask(
            id=task_id, type="escalation", status="needs_confirm",
            params=json.dumps({
                "source": source, "event_id": int(event_id), "summary": summary, **detail,
            }, ensure_ascii=False, default=str),
            risk_level="L1", created_by=source,
        ))
        db.commit()
    record_audit(actor=source, action="escalation.create", target="alert",
                 after={"summary": summary, "event_id": int(event_id)}, task_id=task_id)
    return task_id


def resolve_task(task_id: str, outcome: str, note: str = "") -> dict | None:
    """人工处置 needs_confirm 类待办（P1-36）。

    outcome：`done`（已处置，→ succeeded）/ `dismissed`（判定无需处理，→ canceled）。
    只有 needs_confirm 可被处置——running 任务走 cancel_task，终态任务原样返回
    （幂等，不报错）。**没有处置入口的待办是死胡同**：只登记不关闭，任务中心会
    无限累积，反而淹没真正需要看的条目。
    """
    if outcome not in ("done", "dismissed"):
        raise ValueError(f"未知处置结论：{outcome}（可用：done、dismissed）")
    row = get_task(task_id)
    if row is None:
        return None
    if row["status"] != "needs_confirm":
        return row  # 已终态/运行中：原样返回，幂等
    status = "succeeded" if outcome == "done" else "canceled"
    with get_session_factory()() as db:
        task = db.get(AgentTask, task_id)
        if task is None:
            return None
        try:
            params = json.loads(task.params or "{}")
        except Exception:  # noqa: BLE001
            params = {}
        params["resolve"] = {"outcome": outcome, "note": note[:500]}
        task.params = json.dumps(params, ensure_ascii=False, default=str)
        task.status = status
        task.finished_at = beijing_now_naive()
        db.commit()
    record_audit(actor="user", action="task.resolve", target=row["type"],
                 after={"status": status, "outcome": outcome, "note": note[:200]},
                 task_id=task_id)
    return get_task(task_id)

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


#: 议程留痕条目的 ID 前缀（`agenda:2026-09-10`）。与真实任务（uuid hex）天然不冲突，
#: 前端据此渲染为只读条目（无取消按钮）。
AGENDA_ID_PREFIX = "agenda:"

#: 议程状态 → 任务状态词汇。前端 `STATUS_META` 只认任务这套词，映射在此收口；
#: 原始状态保留在 `params.agenda_status`，不丢信息。
_AGENDA_STATUS_MAP = {
    "generating": "running",
    "executed": "succeeded",
    "failed": "failed",
    "skipped": "canceled",
}


def agenda_as_task(row) -> dict:
    """议程行 → **只读**任务视图（P1-14 留痕合一，2026-09-10）。

    为什么是视图映射而不是新落一张表：议程已经把 inputs/items/budget/status 四段
    结构化留痕写全了，再写一份就是**两处真相源**（必然漂移，见 KB-ENG-26）。
    本函数只做形状转换：

    - `steps` = 「证据采集与预算」一段 + 每个议程条目一段（A/B/C 类各写各的），
      字段名照搬 `_StepRecorder` ⇒ 前端**渲染代码零改动**；
    - 状态映射到任务词汇，原始状态留在 `params.agenda_status`；
    - `read_only=True` ⇒ 前端隐藏取消按钮、标注"自动执行"。

    风险等级标 L1：议程含 A 类参数变更（进影子队列，非直接生效）与 B 类文档沉淀；
    C 类代码执行有自己的独立留痕任务（`code_executor` 的 mutation）。
    """
    def _j(raw: str | None, default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return default

    items = _j(row.items, [])
    budget = _j(row.budget, {})
    steps: list[dict] = [{
        "index": 1, "name": "证据采集与预算", "input_summary": "",
        "output_summary": (
            f"LLM {budget.get('llm_used', '?')}/{budget.get('llm_budget', '?')} · "
            f"任务 {budget.get('tasks_used', '?')}/{budget.get('task_budget', '?')}"
            + ("（LLM 预算耗尽）" if budget.get("llm_exhausted") else "")
        ),
        "duration_ms": 0, "ok": True,
    }]
    for i, it in enumerate(items, start=2):
        status = it.get("status") or "pending"
        steps.append({
            "index": i,
            "name": f"{it.get('class') or '?'} 类 · {it.get('priority') or '—'}",
            "input_summary": (it.get("evidence") or {}).get("source") or "",
            # 标题优先用 finding（人读得懂的结论），执行结果用 result 附后
            "output_summary": f"[{status}] {it.get('finding') or it.get('summary') or ''}"
                              + (f" → {it['result']}" if it.get("result") else ""),
            "duration_ms": 0,
            # deferred/failed/rejected 都不算「做成」——deferred 是"延后（附原因）"，
            # 界面标 ✗ 才对得起审计语义（成功假象是留痕最不该犯的错）
            "ok": status not in ("failed", "rejected", "deferred"),
        })

    # error 单独处理：不能用 `_j(row.error, None)` —— 裸字符串会 JSON 解析失败
    # 后**静默变成 None**，于是"议程失败了"在界面上显示成"没有错误"。
    # 错误字段的失败模式必须是"原文照登"，不是"丢了"。
    error: dict | None = None
    if row.error:
        try:
            parsed = json.loads(row.error)
        except Exception:  # noqa: BLE001
            parsed = row.error
        error = parsed if isinstance(parsed, dict) else {
            "code": "AgendaError", "message": str(parsed), "retryable": False,
        }

    created = row.created_at.isoformat() if row.created_at else None
    return {
        "id": f"{AGENDA_ID_PREFIX}{row.date}",
        "type": "agenda",
        "status": _AGENDA_STATUS_MAP.get(row.status, "succeeded"),
        "params": {
            "agenda_date": row.date,
            "agenda_status": row.status,
            "budget": budget,
            "items": items,
        },
        "steps": steps,
        "result_ref": {"kind": "agenda", "id": row.date},
        "error": error,
        "risk_level": "L1",
        "created_by": "agenda",
        "read_only": True,
        "created_at": created,
        "started_at": created,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "cancel_requested_at": None,
    }


def list_agenda_tasks(limit: int = 30) -> list[dict]:
    """议程留痕（近 limit 天，倒序）→ 只读任务视图。"""
    from app.models.agent import AgentAgenda

    with get_session_factory()() as db:
        rows = db.execute(
            select(AgentAgenda).order_by(AgentAgenda.date.desc()).limit(limit)
        ).scalars().all()
        return [agenda_as_task(r) for r in rows]


def _get_agenda_task(agenda_date: str) -> dict | None:
    from app.models.agent import AgentAgenda

    with get_session_factory()() as db:
        row = db.execute(
            select(AgentAgenda).where(AgentAgenda.date == agenda_date)
        ).scalar_one_or_none()
        return agenda_as_task(row) if row else None


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
        "cancel_requested_at": (row.cancel_requested_at.isoformat() if row.cancel_requested_at else None),
    }


class TaskWallTimeout(TimeoutError):
    """Raised only by the Agent task cooperative wall-time checkpoint."""


class _StepRecorder:
    """步骤轨迹记录器：每步写库一次（任务量小，不做批量优化）。"""

    def __init__(self, task_id: str, *, deadline_monotonic: float | None = None):
        self.task_id = task_id
        self.index = 0
        self.deadline_monotonic = deadline_monotonic

    def checkpoint(self) -> None:
        """Cooperatively stop only between bounded stages.

        Python cannot kill a thread already running under ``asyncio.to_thread``.  A checkpoint
        therefore never claims ``canceled``/``TaskTimeout`` until that in-flight stage has
        actually returned.
        """
        if _cancel_requested(self.task_id):
            raise asyncio.CancelledError
        if self.deadline_monotonic is not None and time.monotonic() >= self.deadline_monotonic:
            raise TaskWallTimeout

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
    rec.checkpoint()
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
    rec.checkpoint()
    report = await svc.run(td, methodology_version=version, cancel_check=rec.checkpoint)
    rec.checkpoint()
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
    rec.checkpoint()
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
    rec.checkpoint()

    snap = getattr(getattr(app, "state", None), "snapshot_service", None) if app is not None else None
    rows = getattr(snap, "snapshot", None) or []
    # 快照刷新时间：真实属性名 last_success（tz-aware UTC）——曾误写 last_refresh_at
    # 导致体检永远显示「最近刷新 —」（三态降级正确，但信息丢失）
    age_text = "—"
    try:
        last_ok = getattr(snap, "last_success", None)
        if last_ok is not None:
            from datetime import datetime as _dt, timezone as _tz

            age_min = max(0, int((_dt.now(_tz.utc) - last_ok).total_seconds() // 60))
            age_text = f"{age_min} 分钟前"
    except Exception:  # noqa: BLE001  显示辅助失败不阻断体检
        age_text = "—"
    rec.add(name="全市场快照", input_summary="", output_summary=f"{len(rows)} 只，最近刷新 {age_text}",
            duration_ms=0, ok=bool(rows))
    rec.checkpoint()

    today_events = 0
    try:
        from app.models.alert import AlertEvent
        from datetime import timedelta

        with get_session_factory()() as db:
            # triggered_at 是 alert 域的北京 naive（勿与 agent 域旧 UTC 口径混淆）——
            # cutoff 必须同口径；此前误用 utcnow-12h，实际统计的是近 20h。
            cutoff = beijing_now_naive() - timedelta(hours=12)
            today_events = len(db.execute(
                select(AlertEvent.id).where(AlertEvent.triggered_at >= cutoff)
            ).scalars().all())
    except Exception as exc:  # noqa: BLE001  体检是增强层，失败只降级
        log.debug("data_check events failed: %s", exc)
    rec.add(name="近 12h 告警", input_summary="", output_summary=f"{today_events} 条", duration_ms=0, ok=True)
    rec.checkpoint()

    return {"kind": "artifact", "id": f"data-check-{int(time.time())}"}


_HANDLERS: dict[str, Callable[[_StepRecorder, dict, Any], Awaitable[dict]]] = {
    "review": _h_review,
    "data_check": _h_data_check,
}

#: 可创建（有 handler、能真正跑起来）的任务类型 = handler 注册表本身。
#: `GET /agent/task-types` 与 `create_task` 都以此为准——否则会出现"清单里有按钮、
#: 点了必失败"的类型（mutation/escalation 是服务侧登记条目，非可创建任务）。
CREATABLE_TASK_TYPES: frozenset[str] = frozenset(_HANDLERS)


def creatable_task_types() -> list[dict]:
    """可创建任务清单（含展示元数据）。

    顺序 = `TASK_TYPES` 声明顺序（稳定），前端按钮顺序与后端口径一致；
    登记类类型（mutation/escalation）在此被过滤掉——它们不是"能点的任务"。
    """
    return [
        {"type": k, "label": v["label"], "risk": v["risk"], "desc": v["desc"]}
        for k, v in TASK_TYPES.items() if k in CREATABLE_TASK_TYPES
    ]


# ---------------------------------------------------------------- 生命周期


def _cancel_requested(task_id: str) -> bool:
    with get_session_factory()() as db:
        row = db.get(AgentTask, task_id)
        return bool(row and row.cancel_requested_at is not None)


def _mark_cancel_requested(task_id: str) -> tuple[dict | None, bool]:
    """Persist cancel intent; never claim terminal cancellation before the owner stops."""
    now = beijing_now_naive()
    with get_session_factory()() as db:
        row = db.get(AgentTask, task_id)
        if row is None:
            return None, False
        if row.status in TERMINAL_STATUSES:
            return _load(row), False
        changed = row.cancel_requested_at is None
        if changed:
            row.cancel_requested_at = now
            db.commit()
            db.refresh(row)
        return _load(row), changed


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
    timeout = max(0.1, float(settings.agent_task_timeout_seconds))
    rec = _StepRecorder(task_id, deadline_monotonic=time.monotonic() + timeout)
    if _cancel_requested(task_id):
        _set_status(task_id, "canceled", finished_at=beijing_now_naive())
        record_audit(actor="user", action="task.cancel", target=type_,
                     after={"status": "canceled", "confirmed": True, "owner": "before_start"},
                     task_id=task_id)
        _RUNNING.pop(type_, None)
        _HANDLES.pop(task_id, None)
        return

    _set_status(task_id, "running", started_at=beijing_now_naive())
    try:
        rec.checkpoint()
        handler = _HANDLERS[type_]
        result = await handler(rec, params, _APP)
        rec.checkpoint()
        _set_status(task_id, "succeeded", result_ref=result, finished_at=beijing_now_naive())
        record_audit(actor="ai", action="task.finish", target=type_, after={"status": "succeeded"},
                     task_id=task_id)
        log.warning("[AGENT-TASK] %s %s 完成", type_, task_id)
    except asyncio.CancelledError:
        _set_status(task_id, "canceled", finished_at=beijing_now_naive())
        record_audit(actor="user", action="task.cancel", target=type_,
                     after={"status": "canceled", "confirmed": True,
                            "owner": "cooperative_checkpoint"}, task_id=task_id)
    except TaskWallTimeout:
        _set_status(
            task_id, "failed",
            error={"code": "TaskTimeout",
                   "message": f"任务超过 {float(settings.agent_task_timeout_seconds):g}s wall-time 上限；当前不可中断步骤已收尾",
                   "retryable": True},
            finished_at=beijing_now_naive(),
        )
        record_audit(actor="ai", action="task.fail", target=type_,
                     after={"status": "failed", "error": "TaskTimeout"}, task_id=task_id)
        log.warning("[AGENT-TASK] %s %s 超时（bounded stage drained）", type_, task_id)
    except Exception as exc:
        _set_status(task_id, "failed", error={"code": type(exc).__name__, "message": str(exc),
                                              "retryable": True}, finished_at=beijing_now_naive())
        record_audit(actor="ai", action="task.fail", target=type_,
                     after={"status": "failed", "error": str(exc)}, task_id=task_id)
        log.warning("[AGENT-TASK] %s %s 失败：%s", type_, task_id, exc)
    finally:
        _RUNNING.pop(type_, None)
        _HANDLES.pop(task_id, None)

def create_task(type_: str, params: dict | None = None, *, created_by: str = "user") -> dict:
    """创建并启动任务（同类型互斥）。返回任务 dict。

    只接受 `CREATABLE_TASK_TYPES`（有 handler 的类型）；登记类条目
    （mutation / escalation）走 record_* 入口，不经此处——否则任务会创建成功
    却在 `_HANDLERS[type_]` 处立刻失败，留下一条无意义的 failed 记录。
    """
    if type_ not in CREATABLE_TASK_TYPES:
        raise ValueError(
            f"未知任务类型：{type_}"
            f"（可用：{'、'.join(t['type'] for t in creatable_task_types())}）"
        )
    existing = _RUNNING.get(type_)
    if existing is not None:
        if existing.done():
            _RUNNING.pop(type_, None)
        else:
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
    if task_id.startswith(AGENDA_ID_PREFIX):
        return _get_agenda_task(task_id[len(AGENDA_ID_PREFIX):])
    with get_session_factory()() as db:
        row = db.get(AgentTask, task_id)
        return _load(row) if row else None


def list_tasks(limit: int = 30, type_: str | None = None, *, include_agenda: bool = True) -> list[dict]:
    with get_session_factory()() as db:
        q = select(AgentTask)
        if type_:
            q = q.where(AgentTask.type == type_)
        rows = db.execute(q.order_by(AgentTask.created_at.desc()).limit(limit)).scalars().all()
        out = [_load(r) for r in rows]

    # 留痕合一（P1-14，2026-09-10）：议程 A/B/C 自动执行也是"系统做过什么"的留痕，
    # 但此前只落在 agent_agenda，任务中心看不到——两处孤岛 = 追溯性缺口。
    # 合并到同一时间线（按创建时间倒序），议程条目标记 read_only。
    if include_agenda and (type_ is None or type_ == "agenda"):
        out = out + list_agenda_tasks(limit)
        out.sort(key=lambda t: str(t.get("created_at") or ""), reverse=True)
    return out[:limit]


def _confirm_local_handle_cancel(task_id: str, type_: str, handle: asyncio.Task) -> None:
    """Close the queued-before-start race only after this process's handle is truly done."""
    if not handle.cancelled():
        return
    try:
        row = get_task(task_id)
        if row is None or row.get("status") in TERMINAL_STATUSES:
            return
        if not row.get("cancel_requested_at"):
            return
        _set_status(task_id, "canceled", finished_at=beijing_now_naive())
        record_audit(
            actor="user", action="task.cancel", target=type_,
            after={"status": "canceled", "confirmed": True, "owner": "local_handle_done"},
            task_id=task_id,
        )
    except Exception as exc:  # startup reconciliation remains the final safety net
        log.warning("local cancel confirmation failed (%s): %s", task_id, exc)


def cancel_task(task_id: str) -> dict | None:
    """Request cancellation durably; only the owning process may confirm ``canceled``.

    If the task is owned by another process, this function leaves it queued/running with
    ``cancel_requested_at`` set.  The owner polls that field and cancels its coroutine.  This
    prevents the old lie where one process wrote ``canceled`` while another kept executing.
    """
    row = get_task(task_id)
    if row is None:
        return None
    if row.get("read_only"):
        return row
    if row["status"] in TERMINAL_STATUSES:
        return row
    persisted, changed = _mark_cancel_requested(task_id)
    if persisted is None:
        return None
    if changed:
        record_audit(actor="user", action="task.cancel.request", target=row["type"],
                     after={"status": row["status"], "cancel_requested": True}, task_id=task_id)
    handle = _HANDLES.get(task_id)
    if row["status"] == "queued" and handle is not None and not handle.done():
        handle.add_done_callback(
            lambda done, tid=task_id, t=row["type"]: _confirm_local_handle_cancel(tid, t, done)
        )
        handle.cancel()
    # Running tasks use cooperative checkpoints.  ``Task.cancel()`` cannot stop a worker that is
    # already inside asyncio.to_thread(), so force-canceling here would make DB state lie while
    # the thread kept running.
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
    """启动时把残留 running 任务标为 failed（重启=进程没了，不假装还在跑）。

    R11（2026-09-14）：**AgentAgenda 一并收敛**。此前只管 AgentTask，议程行停在
    `generating` 就再没有人来收——`generate_agenda` 见 status 非 failed 便直接返回
    同一份空快照，15:45 调度分支的 `existing["status"] == "failed"` 也永不满足
    ⇒ **当天议程永久缺失，且界面上一直显示"生成中"**。这与 AgentTask 是同一类
    「进程死亡留下的状态」，必须同源处理；error 口径也保持一致（Interrupted/retryable）。

    返回值是**两类之和**（任务 + 议程），调用方只用于日志/健康观测。
    """
    with get_session_factory()() as db:
        rows = db.execute(
            select(AgentTask).where(AgentTask.status.in_(("queued", "running")))
        ).scalars().all()
        n = 0
        for r in rows:
            if r.cancel_requested_at is not None:
                r.status = "canceled"
                r.error = None
            else:
                r.status = "failed"
                r.error = json.dumps({"code": "Interrupted", "message": "服务重启，任务中断",
                                      "retryable": True}, ensure_ascii=False)
            r.finished_at = beijing_now_naive()
            n += 1

        from app.models.agent import AgentAgenda

        agendas = db.execute(
            select(AgentAgenda).where(AgentAgenda.status == "generating")
        ).scalars().all()
        for a in agendas:
            a.status = "failed"
            a.error = json.dumps({"code": "Interrupted", "message": "服务重启，议程生成中断",
                                  "retryable": True}, ensure_ascii=False)
            a.finished_at = beijing_now_naive()
            n += 1

        if n:
            db.commit()
    # Never refund a started external call. Only stale never-started reservations are recoverable.
    with contextlib.suppress(Exception):
        from app.services.agent_budget import recover_stale_reservations
        recover_stale_reservations(get_session_factory())
    return n
