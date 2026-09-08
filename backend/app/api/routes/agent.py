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


@router.get("/agent/audit")
async def list_audit(
    target: str | None = None,
    task_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
):
    return {"data": at.list_audit(limit=limit, target=target, task_id=task_id)}
