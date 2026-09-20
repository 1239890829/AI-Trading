"""AI 控制台端点（方案 docs/summary/ai-evolution.md P0）。

首批只暴露 **L0 只读/生成类**任务（review / data_check），写类任务与参数变更在
P1 参数配置模块接入后按同一套状态机/审计机制扩展。

端点：
- GET  /agent/task-types    可创建任务类型清单（前端表单用，含风险等级与说明）
- POST /agent/tasks         创建任务（写操作 → require_write_token）
- GET  /agent/tasks         任务列表（?type=&limit=）
- GET  /agent/tasks/{id}    任务详情（含步骤轨迹）
- POST /agent/tasks/{id}/cancel   取消（写操作）
- POST /agent/tasks/{id}/resolve  处置待办（P1-36：告警 escalate 待办，写操作）
- GET  /agent/audit         执行层审计（?target=&task_id=&limit=）
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import require_write_token
from app.services import agent_tasks as at
from app.services import agent_params as params_svc
from app.services import alert_triage as at_triage
from app.services import evolution as evo

router = APIRouter(tags=["agent"])


class TaskCreateIn(BaseModel):
    type: str = Field(..., description="任务类型，见 GET /agent/task-types")
    params: dict = Field(default_factory=dict, description="任务入参（各类型字段见类型清单）")


@router.get("/agent/task-types")
async def agent_task_types():
    """可**创建**的任务类型（有 handler、点了会真跑）。

    登记类条目（mutation 变更留痕 / escalation 告警升级待办）不在此列——它们由
    服务侧产生，没有执行体，出现在这里只会建出必然失败的任务。
    """
    return {"data": at.creatable_task_types()}


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


class TaskResolveIn(BaseModel):
    outcome: str = Field(..., description="done=已处置 / dismissed=判定无需处理")
    note: str = Field(default="", description="处置说明（可选，留痕用）")


@router.post("/agent/tasks/{task_id}/resolve", dependencies=[Depends(require_write_token)])
async def resolve_task(task_id: str, body: TaskResolveIn):
    """处置 needs_confirm 待办（P1-36：告警 escalate 落任务中心后的关闭入口）。"""
    try:
        row = at.resolve_task(task_id, body.outcome, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
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


class ParamChangeIn(BaseModel):
    key: str = Field(..., description="参数 key（白名单见 GET /agent/params）")
    after: str = Field(..., description="新值（字符串形式；非法值 422）")
    source_type: str = Field("manual", description="manual / review_action_item / ai_suggestion")
    source_id: str = Field("", description="来源 ID（如改进项 id）")
    evidence: dict | None = Field(None, description="采纳依据 {sample_days, ic, win_rate}")


class RollbackIn(BaseModel):
    """回滚入参（P1-15）：归因是必填语义。不传 = manual（人工判断、未说明）。"""

    reason_code: str = Field("manual", description="回滚归因 code（枚举见 /agent/params/rollback-reasons）")
    note: str = Field("", description="备注（code=other 时应当填写）")


@router.get("/agent/params")
async def list_params():
    """参数白名单与当前生效值（覆盖层优先于静态配置）。"""
    return {"data": params_svc.list_params()}


@router.get("/agent/params/changes")
async def list_param_changes(key: str | None = None, limit: int = Query(30, ge=1, le=200)):
    return {"data": params_svc.list_changes(limit=limit, key=key)}


@router.get("/agent/params/survival")
async def param_survival():
    """变更存活率 + 回滚归因分布（P1-15）。

    样本不足时 `insufficient=True` 并给 note——**样本=1 的存活率是巧合不是指标**，
    接口照实返回数字但同时标注不可用，免得界面上出现一个看着像结论的数字。
    """
    return {"data": params_svc.survival_stats()}


@router.get("/agent/params/rollback-reasons")
async def param_rollback_reasons():
    """回滚归因枚举（前端下拉用；**封闭集合**，不收自由文本 code）。"""
    return {"data": [
        {"code": code, "label": label}
        for code, label in params_svc.ROLLBACK_REASONS.items()
    ]}


@router.post("/agent/params/change", dependencies=[Depends(require_write_token)])
async def propose_param_change(body: ParamChangeIn):
    """生成变更单（draft，不生效）。值域非法 422；与当前值相同 422。"""
    try:
        return {"data": params_svc.propose(
            body.key, body.after,
            source_type=body.source_type, source_id=body.source_id,
            evidence=body.evidence,
        )}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/agent/params/changes/{change_id}/apply", dependencies=[Depends(require_write_token)])
async def apply_param_change(change_id: int):
    """生效变更单：写运行时覆盖层（免重启）+ 审计 + 变更留痕任务（mutation_source=user）。"""
    try:
        return {"data": params_svc.apply_change(change_id, mutation_source="user")}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/agent/params/changes/{change_id}/rollback", dependencies=[Depends(require_write_token)])
async def rollback_param_change(change_id: int, body: RollbackIn | None = None):
    """回滚变更单：恢复到 before（覆盖层同步还原）+ 记录**归因**。

    归因 code 非法 → 422（封闭集合，不静默落 other）。
    """
    body = body or RollbackIn()
    try:
        return {"data": params_svc.rollback_change(
            change_id, reason_code=body.reason_code, note=body.note,
        )}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/agent/agenda")
async def get_agenda(date: str | None = None):
    """今日（或指定日）进化议程；不存在返回 data=null。

    meta.scheduler_status：调度器 liveness（last_tick_at/age）——data=null 时
    先看调度器是否活着（2026-09-09 事故：调度器 NameError 每 tick 崩溃，
    议程静默不生成，无任何日志可取证）。
    """
    return {"data": evo.get_agenda(date), "meta": {"scheduler_status": evo.scheduler_status()}}


@router.get("/agent/agendas")
async def list_agendas(limit: int = Query(14, ge=1, le=60)):
    return {"data": evo.list_agendas(limit=limit)}


@router.post("/agent/agenda/run", dependencies=[Depends(require_write_token)])
async def run_agenda(request: Request):
    """手动触发一轮进化（正常由 15:45 scheduler 自动跑；此处为降级兜底）。"""
    return {"data": await evo.run_evolution_now(app=request.app)}


@router.get("/agent/experiments")
async def list_experiments(limit: int = Query(30, ge=1, le=200)):
    """实验记录本 + 影子队列（P1-4）：A 类变更的影子评估状态与后置验证记录。"""
    from app.services import agent_params, experiments

    return {
        "data": experiments.list_experiments(limit=limit),
        "shadow_queue": agent_params.list_shadow_changes(),
    }


@router.get("/agent/audit")
async def list_audit(
    target: str | None = None,
    task_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
):
    return {"data": at.list_audit(limit=limit, target=target, task_id=task_id)}


# ---------------------------------------------------------------- 元评估周报（P2-①）
# 正常运行由 evolution_scheduler 周五盘后自动生成（幂等）；此端点为降级备选。


@router.get("/agent/meta-review")
def get_meta_review_status():
    from app.services import meta_review

    return {"data": {"done": meta_review.meta_review_done(),
                     "path": str(meta_review.meta_review_path())}}


@router.post("/agent/meta-review/run", dependencies=[Depends(require_write_token)])
async def run_meta_review():
    from app.services import meta_review

    return {"data": await asyncio.to_thread(meta_review.generate_meta_review)}


# ---------------------------------------------------------------- 知识库/仓库浏览（2026-09-09 用户指令⑤）
def _docs_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[4] / "docs"


#: 文档分层口径（与 `docs/kb/07-doc-curation.md` §7 分层模型对齐）。
#: **为什么要分层**：面板原先是 `rglob("*.md")` 无差别扫描 docs/ ⇒ 79 份里只有 11 份是
#: canonical 知识库，且 22 份归档件与 13 份逐日日志被平铺并列 —— 用户无法从界面分辨
#: 「这是现行规则」还是「这是历史结论」，**与 kb/07「状态语义不得混用」的既有纪律冲突**。
#: 分层只影响**默认呈现**，不减少可见内容（折叠区仍可展开、搜索仍跨全部）。
_DOC_TIER_DIRS: dict[str, tuple[str, ...]] = {
    "canonical": ("kb",),                                              # L0 唯一权威
    "current": ("", "summary", "system", "data", "product", "strategy",
                "ai", "review", "research"),                           # L1/L2 现役
    "history": ("archive",),                                           # 只读历史
    "timeline": ("daily-review", "evolution", "repo-watch", "push-templates"),  # L4 时间序列
}


def _tier_of(top_dir: str) -> str:
    """顶层目录名 → 分层标识。**未登记的新目录按 `current` 处理**——
    出方向是「多显示一点」，而不是把新内容静默藏进折叠区（失败要可见）。"""
    for tier, dirs in _DOC_TIER_DIRS.items():
        if top_dir in dirs:
            return tier
    return "current"


@router.get("/agent/kb/tree")
async def kb_tree() -> dict:
    """知识库文档树（AI 控制台「知识库/仓库」面板数据源）。

    docs/ 下全部 .md；每份带 `tier`（canonical/current/history/timeline）供前端分层呈现；
    KB 文件额外解析其包含的 KB-ID 列表（[[KB-XXX]] 关联跳转用）。
    """
    import re

    root = _docs_root()
    files: list[dict] = []
    if root.exists():
        for p in sorted(root.rglob("*.md")):
            rel = p.relative_to(root).as_posix()
            dir_key = p.parent.relative_to(root).as_posix() if p.parent != root else ""
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                text = ""
            kb_ids = sorted(set(re.findall(r"KB-(?:STOCK|TRADE|ENG|DEC)-\d+", text)))
            files.append(
                {
                    "path": rel,
                    "name": p.name,
                    "dir": dir_key,
                    "tier": _tier_of(dir_key.split("/")[0] if dir_key else ""),
                    "size": p.stat().st_size,
                    "kb_ids": kb_ids,
                }
            )
    return {"data": {"root": "docs", "files": files}, "meta": {}}


@router.get("/agent/kb/file")
async def kb_file(path: str = Query(..., description="docs/ 相对路径，仅 .md")) -> dict:
    """读单篇 Markdown。路径白名单：resolve 后必须仍在 docs/ 内（防目录穿越）。"""
    root = _docs_root().resolve()
    target = (root / path).resolve()
    if not str(target).startswith(str(root)) or target.suffix != ".md" or not target.is_file():
        raise HTTPException(status_code=404, detail="文档不存在或路径非法")
    try:
        return {"data": {"path": path, "content": target.read_text(encoding="utf-8")}, "meta": {}}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取失败: {exc}")
