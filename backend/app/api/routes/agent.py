"""AI 控制台端点（方案 docs/ai-agent-console-plan.md P0）。

首批只暴露 **L0 只读/生成类**任务（review / data_check），写类任务与参数变更在
P1 参数配置模块接入后按同一套状态机/审计机制扩展。

端点：
- GET  /agent/task-types    任务类型清单（前端表单用，含风险等级与说明）
- POST /agent/tasks         创建任务（写操作 → require_write_token）
- GET  /agent/tasks         任务列表（?type=&limit=）
- GET  /agent/tasks/{id}    任务详情（含步骤轨迹）
- POST /agent/tasks/{id}/cancel  取消（写操作）
- GET  /agent/audit         执行层审计（?target=&task_id=&limit=）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import require_write_token
from app.services import agent_tasks as at
from app.services import alert_triage as at_triage

router = APIRouter(tags=["agent"])


class TaskCreateIn(BaseModel):
    type: str = Field(..., description="任务类型，见 GET /agent/task-types")
    params: dict = Field(default_factory=dict, description="任务入参（各类型字段见类型清单）")


@router.get("/agent/task-types")
async def agent_task_types():
    return {"data": [
        {"type": k, "label": v["label"], "risk": v["risk"], "desc": v["desc"]}
        for k, v in at.TASK_TYPES.items()
    ]}


@router.post("/agent/tasks", dependencies=[Depends(require_write_token)])
async def create_task(body: TaskCreateIn):
    try:
        return {"data": at.create_task(body.type, body.params)}
    except ValueError as exc:      # 未知类型
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:    # 同类型互斥
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/agent/tasks")
async def list_tasks(type: str | None = None, limit: int = Query(30, ge=1, le=200)):
    return {"data": at.list_tasks(limit=limit, type_=type)}


@router.get("/agent/tasks/{task_id}")
async def get_task(task_id: str):
    row = at.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"data": row}


@router.post("/agent/tasks/{task_id}/cancel", dependencies=[Depends(require_write_token)])
async def cancel_task(task_id: str):
    row = at.cancel_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"data": row}


@router.get("/agent/triage")
async def list_triage(verdict: str | None = None, limit: int = Query(50, ge=1, le=500)):
    """告警判读历史（notify/ignore/escalate；model 字段区分 LLM 与规则兜底）。"""
    return {"data": at_triage.list_triage(limit=limit, verdict=verdict)}


@router.get("/agent/triage/pending")
async def pending_bubbles(limit: int = Query(5, ge=1, le=20)):
    """悬浮球待提醒：只有 AI 判为"值得提醒"且未确认的才出现（不刷屏）。"""
    return {"data": at_triage.pending_bubbles(limit=limit)}


@router.post("/agent/triage/{triage_id}/ack", dependencies=[Depends(require_write_token)])
async def ack_triage(triage_id: int):
    if not at_triage.ack_triage(triage_id):
        raise HTTPException(status_code=404, detail="判读记录不存在")
    return {"data": {"ok": True}}


@router.post("/agent/triage/run", dependencies=[Depends(require_write_token)])
async def run_triage(limit: int = Query(20, ge=1, le=100)):
    """手动触发一轮判读（默认由后台 worker 每 30s 自动跑）。"""
    return {"data": await at_triage.triage_pending(limit=limit)}


@router.get("/agent/audit")
async def list_audit(
    target: str | None = None,
    task_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
):
    return {"data": at.list_audit(limit=limit, target=target, task_id=task_id)}
