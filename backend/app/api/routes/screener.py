"""全市场选股器端点（Phase 5）。

读端点（无写操作，不挂 B6 token）；TDX 整体不可用 → 502 code=tdx_unavailable。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.core.errors import AppError
from app.schemas.envelope import Envelope
from app.schemas.screener import ScreenerPayload
from app.services.screener_service import ScreenerService

router = APIRouter(tags=["screener"])


@router.get("/screener", response_model=Envelope[ScreenerPayload])
async def screener(
    request: Request,
    change_low: float = Query(default=-2.0, ge=-21.0, le=21.0, description="涨幅带下限 %"),
    change_high: float = Query(default=9.0, ge=-21.0, le=21.0, description="涨幅带上限 %"),
    min_amount_yi: float = Query(default=1.0, ge=0.0, le=500.0, description="最小成交额（亿元）"),
    min_turnover: float = Query(default=2.0, ge=0.0, le=100.0, description="最小换手率 %"),
    exclude_st: bool = Query(default=True, description="排除 ST/退市风险"),
    exclude_bj: bool = Query(default=True, description="排除北交所"),
    exclude_new: bool = Query(default=True, description="排除次新/样本不足（日K<60 根）"),
    limit: int = Query(default=30, ge=1, le=100, description="返回条数（按评分降序）"),
) -> dict:
    """全市场选股器：快照截面过滤 → TDX 日K 技术评分卡（可解释依据 + 失效条件）。

    结果缓存 30 分钟（cached=true 表示命中缓存）。首次调用约 15-25 秒
    （150 只候选逐只拉日K），属重操作。
    """
    svc: ScreenerService | None = getattr(request.app.state, "screener_service", None)
    if svc is None:
        raise AppError("选股器服务未初始化", code="screener_unavailable", status_code=503)
    if change_low > change_high:
        raise AppError("change_low 不能大于 change_high", code="validation_error", status_code=400)
    try:
        payload = await svc.run(
            change_low=change_low, change_high=change_high,
            min_amount_yi=min_amount_yi, min_turnover=min_turnover,
            exclude_st=exclude_st, exclude_bj=exclude_bj,
            exclude_new=exclude_new, limit=limit,
        )
    except RuntimeError as exc:
        raise AppError(
            f"选股器数据底座不可用：{exc}", code="snapshot_unavailable", status_code=502
        ) from exc
    except Exception as exc:
        raise AppError(
            f"选股器计算失败（TDX 日K源可能不可用）：{exc}", code="tdx_unavailable", status_code=502
        ) from exc
    return {"data": payload, "meta": {}}
