
from __future__ import annotations

from datetime import date, datetime

from fastapi import Depends, APIRouter, HTTPException, Query, Request

from app.api.deps import require_write_token
from pydantic import BaseModel, Field

from app.review.config import available_versions
from app.review.methodology import (
    evaluate_historical_effectiveness,
    suggest_methodology_changes,
)
from app.review.storage import (
    compare_reports,
    get_report,
    list_reports,
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


@router.get("/review/compare")
async def review_compare(
    fro: str = Query(..., alias="from", description="起始交易日 YYYYMMDD"),
    to: str = Query(..., description="结束交易日 YYYYMMDD"),
    request: Request = None,
):
    """两日报告对比：看变化（数据缺口修复、情绪迁移、改进项处置），而非绝对值。"""
    svc = _service(request)
    _parse_trade_date(fro)
    _parse_trade_date(to)
    a = get_report(svc.session_factory, fro)
    b = get_report(svc.session_factory, to)
    if a is None or b is None:
        raise HTTPException(
            status_code=404, detail=f"对比需要两端都有报告：from={fro}({'有' if a else '无'}) to={to}({'有' if b else '无'})"
        )
    return {"data": compare_reports(a, b)}


@router.get("/review/methodology/versions")
async def review_versions(request: Request):
    """可用的方法论版本（data/review/methodology/*.yaml + 代码内默认）。"""
    return {"data": available_versions()}


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
