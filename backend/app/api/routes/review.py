
from __future__ import annotations

from datetime import date, datetime

from fastapi import Depends, APIRouter, HTTPException, Query, Request

from app.api.deps import require_write_token
from pydantic import BaseModel, Field

from app.review.methodology import (
    evaluate_historical_effectiveness,
    suggest_methodology_changes,
)
from app.review.storage import (
    ALLOWED_STATUSES,
    ActionItemStaleError,
    get_report,
    list_reports,
    update_action_item_status,
)

router = APIRouter(tags=["review"])

_DATE_FMT = "%Y%m%d"


def _service(request: Request):
    svc = getattr(request.app.state, "review", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="复盘服务未就绪")
    return svc


def _parse_trade_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, _DATE_FMT).date()
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"trade_date 需为 {_DATE_FMT} 格式，收到：{raw!r}"
        )


class RunIn(BaseModel):
    trade_date: str | None = Field(None, description=f"可选，{_DATE_FMT}；留空则用上一交易日")
    methodology_version: str | None = Field(None, description="可选，覆盖默认方法论版本")


@router.post("/review/run", dependencies=[Depends(require_write_token)])
async def run_review(body: RunIn, request: Request):
    """手动触发一次盘后复盘（调度器之外的补充入口）。

    调度器只在交易日 15:30 后自动跑"今天"，不补历史；要补跑某天用手动触发。
    返回的 report 已落库 + 落盘。
    """
    svc = _service(request)
    td = _parse_trade_date(body.trade_date)
    report = await svc.run(td, methodology_version=body.methodology_version)
    return {"data": report.model_dump()}


@router.get("/review/reports")
async def review_list(request: Request, limit: int = Query(30, ge=1, le=200)):
    """最近复盘报告列表（结构化摘要）。"""
    svc = _service(request)
    return {"data": list_reports(svc.session_factory, limit=limit)}


@router.get("/review/reports/{trade_date}")
async def review_detail(trade_date: str, request: Request):
    """某交易日完整复盘报告。"""
    svc = _service(request)
    _parse_trade_date(trade_date)  # 仅做格式校验
    report = get_report(svc.session_factory, trade_date)
    if report is None:
        raise HTTPException(status_code=404, detail=f"{trade_date} 无复盘报告")
    return {"data": report.model_dump()}


class ActionItemPatch(BaseModel):
    status: str = Field(..., description="pending | confirmed | applied | rejected | reverted")
    note: str = Field("", description="处置说明；rejected/reverted 必填")
    # id 漂移守卫（2026-09-01）：id 是 SQLite rowid 别名且无 AUTOINCREMENT，
    # 报告重跑删除重建后 id 会被复用甚至跨交易日串号。三元组对不上 = 该 id
    # 已不是调用方看到的那条 → 409 而不是静默写到别的改进项上。
    trade_date: str = Field(..., description="守卫：调用方看到的改进项交易日")
    category: str = Field(..., description="守卫：调用方看到的改进项类别")
    title: str = Field(..., description="守卫：调用方看到的改进项标题")


@router.patch("/review/action-items/{item_id}", dependencies=[Depends(require_write_token)])
async def patch_action_item(item_id: str, body: ActionItemPatch, request: Request):
    """处置单条改进项（PDCA 闭环的落点）。

    改进项只能产出、无法消费时，整个"方法论自我迭代"是空转的——
    2026-09-01 实测 107 条改进项全部 pending、采纳率 0%，
    连带让「采纳率<20% → 该维度疑似产出噪音」的演进建议永远触发且无意义。

    `item_id` 取自 `GET /api/review/reports/{trade_date}` 返回的
    `action_items[].id`（已回填为数据库主键，唯一可寻址）。
    请求体里的 `trade_date/category/title` 是**乐观并发守卫**：
    与表行现状不符时返回 409（报告已重新生成，id 已漂移），客户端刷新重取。
    """
    svc = _service(request)
    try:
        updated = update_action_item_status(
            svc.session_factory, item_id, body.status, body.note,
            expect_trade_date=body.trade_date,
            expect_category=body.category,
            expect_title=body.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ActionItemStaleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    # applied 半自动写回提示（策略进化 P1 方向5）：识别「参数 当前值→建议值」意图，
    # 运行时读当前值生成 diff；不认识/读不到 → unresolved，绝不臆造。
    # 全自动写回被方案排除——实际改文件仍由人执行，这里只负责把证据摆到眼前。
    #
    # S2-11：原先只回 `param_diff`（一串数字，无风险/无后果/无回滚方式），点是点了，
    # 事后无从追问"这次采纳到底要改什么"。现改为回**完整载荷**：
    # `param_diff` 保留（向后兼容，前端已在用），新增 `applied_payload`。
    param_diff: list[dict] = []
    applied_payload: dict = {"available": False}
    if body.status == "applied":
        from app.review.writeback import build_applied_payload, build_param_diff

        text = f"{body.title} {body.note}"
        param_diff = build_param_diff(text)
        applied_payload = build_applied_payload(text)
    return {"data": {**updated, "param_diff": param_diff,
                     "applied_payload": applied_payload}}


@router.get("/review/action-items")
async def list_action_items(
    status: str | None = Query(None, description=f"按状态过滤：{' | '.join(ALLOWED_STATUSES)}"),
    limit: int = Query(100, ge=1, le=500),
    request: Request = None,
):
    """跨报告的改进项清单——"哪些改进项还压着没处置"是这个端点的主用途。

    按 priority 升序（P0 在前）、id 升序，保证高优先级先被看见。
    """
    svc = _service(request)
    if status is not None and status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=422, detail=f"status 非法：{status!r}，允许值 {ALLOWED_STATUSES}"
        )
    from sqlalchemy import select as _select

    from app.review.models import ReviewActionItemRow

    db = svc.session_factory()
    try:
        q = _select(ReviewActionItemRow)
        if status:
            q = q.where(ReviewActionItemRow.status == status)
        rows = db.execute(
            q.order_by(ReviewActionItemRow.priority, ReviewActionItemRow.id).limit(limit)
        ).scalars().all()
        return {
            "data": [{
                "id": r.id, "review_id": r.review_id, "trade_date": r.trade_date,
                "title": r.title, "category": r.category, "priority": r.priority,
                "target": r.target, "proposed_change": r.proposed_change,
                "status": r.status, "resolution_note": r.resolution_note,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            } for r in rows],
        }
    finally:
        db.close()


# /review/compare 与 /review/methodology/versions 已删（2026-09-08 审查 P0-4：
# 前端与 scripts 零调用）。compare_reports 存储层函数保留（复盘对比逻辑可能
# 随猎场板块复用）；available_versions 同理（methodology 配置的读取入口）。

@router.get("/review/effectiveness")
async def review_effectiveness(
    version: str | None = Query(None, description="限定方法论版本；留空统计全部"),
    request: Request = None,
):
    """方法论自我迭代的证据面：各维度改进项的采纳率/回退率 + 演进建议。

    只给建议不自动改——自动改会让"框架演进"变成不可归因的黑箱。
    """
    svc = _service(request)
    stats = evaluate_historical_effectiveness(svc.session_factory, version)
    stats["suggestions"] = suggest_methodology_changes(
        svc.session_factory, version or "all"
    )
    return {"data": stats}
