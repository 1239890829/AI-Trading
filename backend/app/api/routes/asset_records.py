"""Ordinary-device records: fund manual holdings and US watch-only identities."""
from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.deps import require_write_token
from app.core.db import get_session_factory
from app.core.errors import AppError
from app.core.inflight_reads import LocalReadCapacityExceeded
from app.repositories.asset_records import RecordConflict, list_records, remove_record, save_record

router = APIRouter(prefix="/assets", tags=["assets"])
_ID_PATTERN = r"^(?:fund:OF:[0-9]{6}:[A-Z]{3}|us_equity:US:[A-Z][A-Z0-9.-]{0,26}:USD)$"
_FUND_SYMBOL = re.compile(r"^[0-9]{6}\.OF$")
_US_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,23}\.(?:OQ|N|AM)$")


class RecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_symbol: str = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=80)
    is_watched: bool = True
    units: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=8)
    cost_total: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=8)
    note: str | None = Field(default=None, max_length=300)
    expected_version: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def complete_manual_basis(self):
        is_fund = bool(_FUND_SYMBOL.fullmatch(self.source_symbol))
        is_us = bool(_US_SYMBOL.fullmatch(self.source_symbol))
        if not is_fund and not is_us:
            raise ValueError("需要来源确认的完整基金或美股代码")
        if is_us and (self.units is not None or self.cost_total is not None):
            raise ValueError("美股当前仅支持自选查看，不记录或执行持仓")
        if (self.units is None) != (self.cost_total is None):
            raise ValueError("份额与同币种总成本必须同时填写或同时清空")
        if self.units == 0 and self.cost_total != 0:
            raise ValueError("零份额的总成本必须为零")
        if not self.is_watched and self.units is None:
            raise ValueError("未关注且没有持有信息的记录应直接删除")
        return self


@router.get("/records")
async def records(asset_type: Literal["fund", "us_equity"] = "fund"):
    return {"data": await asyncio.to_thread(list_records, get_session_factory(), asset_type)}


@router.put("/records/{instrument_id}", dependencies=[Depends(require_write_token)])
async def put_record(body: RecordInput, request: Request,
                     instrument_id: str = Path(pattern=_ID_PATTERN)):
    service = getattr(request.app.state, "asset_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="资产来源尚未配置，原记录未变")
    is_us = instrument_id.startswith("us_equity:")
    expected_type = "us_equity" if is_us else "fund"
    if is_us != bool(_US_SYMBOL.fullmatch(body.source_symbol)):
        raise HTTPException(status_code=422, detail="资产命名空间与代码不一致，原记录未变")
    try:
        instrument = await (service.resolve_us(body.source_symbol) if is_us else service.resolve_fund(body.source_symbol))
    except (AppError, LocalReadCapacityExceeded):
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=503, detail="正式资产身份读取失败，原记录未变") from None
    data = instrument.model_dump(mode="json") if hasattr(instrument, "model_dump") else dict(instrument)
    if (data.get("instrument_id") != instrument_id or data.get("asset_type") != expected_type
            or data.get("source_symbol") != body.source_symbol
            or not re.fullmatch(r"[A-Z]{3}", str(data.get("currency") or ""))
            or is_us and data.get("currency") != "USD"):
        raise HTTPException(status_code=422, detail="记录身份或币种与正式来源不一致，原记录未变")
    try:
        result = await asyncio.to_thread(save_record, get_session_factory(), data,
                                        body.model_dump(), body.expected_version)
    except RecordConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"data": result}


@router.delete("/records/{instrument_id}", dependencies=[Depends(require_write_token)])
async def delete_record(instrument_id: str = Path(pattern=_ID_PATTERN),
                        expected_version: int = Query(ge=1)):
    try:
        result = await asyncio.to_thread(remove_record, get_session_factory(), instrument_id, expected_version)
    except RecordConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"data": result}
