"""Ordinary authenticated asset reads; registration/policy belongs to app assembly."""
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from app.assets.service import AssetReadService
from app.core.errors import AppError

router = APIRouter(prefix="/assets", tags=["assets"])


def get_asset_service(request: Request) -> AssetReadService:
    service = getattr(request.app.state, "asset_service", None)
    if service is None:
        raise AppError("基金读取服务未启用", code="asset_source_disabled", status_code=503)
    return service


@router.get("/capabilities")
async def capabilities(service: AssetReadService = Depends(get_asset_service)) -> dict:
    return {"data": service.capabilities().model_dump(mode="json")}


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=80),
                 asset_type: Literal["fund", "us_equity"] = "fund", limit: int = Query(default=20, ge=1, le=20),
                 service: AssetReadService = Depends(get_asset_service)) -> dict:
    return {"data": (await service.search(q, asset_type, limit)).model_dump(mode="json")}


@router.get("/funds/{code}/snapshot")
async def snapshot(code: str, range: Literal["month", "year"] = "year",
                   service: AssetReadService = Depends(get_asset_service)) -> dict:
    return {"data": (await service.snapshot(code, range)).model_dump(mode="json")}
