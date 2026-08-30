"""新题材预判 API：周末/盘后跑预判，目标日收盘后自动验证回填。

预判是概率假设不是结论——所有端点输出都带证据链、失效条件与风险提示
（红线 3：不输出确定性买卖结论）。
"""
from __future__ import annotations

from fastapi import Depends, APIRouter, HTTPException, Query, Request

from app.api.deps import require_write_token
from pydantic import BaseModel, Field

from app.core.db import get_session_factory
from app.predict import storage
from app.predict.service import run_prediction, verify_predictions

router = APIRouter(tags=["predict"])


def _deps(request: Request):
    hub = getattr(request.app.state, "hub", None)
    snap = getattr(request.app.state, "snapshot_service", None)
    if not (hub and snap):
        raise HTTPException(status_code=503, detail="应用尚未就绪")
    return hub, snap, get_session_factory()


class PredictIn(BaseModel):
    """定向预判：theme_hint + keywords（如 房地产 / ["住房", "地产", "房产中介", "楼市"]）。
    两者都留空 → 自动从热榜个股新闻聚类发现候选主题（启发式，置信度封顶）。"""

    theme_hint: str | None = Field(None, description="题材方向名，如「房地产」")
    keywords: list[str] = Field(default_factory=list, description="题材关键词（新闻/涨停原因匹配用）")


@router.post("/predict/run", dependencies=[Depends(require_write_token)])
async def run(body: PredictIn, request: Request):
    """跑一次新题材预判（周末/节假日/盘后）。

    - 定向模式：theme_hint + keywords → 完整评分卡 + 梯队推演 + 介入计划
    - 自动模式：留空 → 热榜新闻聚类发现候选主题（结果标注 heuristic）
    报告落库落盘（同一目标日覆盖）。
    """
    hub, snap, sf = _deps(request)
    if body.theme_hint and not body.keywords:
        raise HTTPException(status_code=422, detail="定向预判需要同时提供 keywords（新闻/涨停原因匹配用）")
    try:
        report = await run_prediction(
            hub, snap, sf,
            theme_hint=body.theme_hint,
            keywords=body.keywords or None,
            trigger="manual",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"预判运行失败：{exc}")
    return {"data": report.model_dump()}


@router.get("/predict/predictions")
async def list_all(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    hub, snap, sf = _deps(request)
    return {"data": storage.list_reports(sf, limit)}


@router.get("/predict/predictions/{target_date}")
async def detail(target_date: str, request: Request):
    _, _, sf = _deps(request)
    report = storage.get_report(sf, target_date)
    if not report:
        raise HTTPException(status_code=404, detail=f"目标日 {target_date} 无预判报告")
    return {"data": report.model_dump()}


@router.post("/predict/verify/{target_date}", dependencies=[Depends(require_write_token)])
async def verify(target_date: str, request: Request):
    """目标日收盘后验证预判（题材成立?/人气兑现?/梯队对照?）并回填命中率。

    复盘 Agent 在 15:30 自动调用；此端点供手动补验/重放（幂等，已验证直接返回）。
    """
    hub, snap, sf = _deps(request)
    try:
        verify = await verify_predictions(hub, snap, sf, target_date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"验证失败：{exc}")
    if verify is None:
        raise HTTPException(status_code=404, detail=f"目标日 {target_date} 无预判报告")
    return {"data": verify}


@router.get("/predict/stats")
async def stats(request: Request):
    """预判命中率分层统计（按 verdict/context）。样本外 ≥20 条前仅供参考。"""
    _, _, sf = _deps(request)
    return {"data": storage.hit_stats(sf)}
