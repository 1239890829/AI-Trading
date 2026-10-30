"""Public product descriptions and source-reported holdings references, not fund NAV forecasts."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.asset_evidence import AssetEvidence


FundPublicEvidence = AssetEvidence


class FundPublicField(BaseModel):
    key: str
    label: str
    value: str
    source: Literal["eastmoney_fund", "huaan_fund"]
    source_url: str
    source_as_of: datetime | None = None


class FundPublicProfile(BaseModel):
    source_symbol: str
    state: Literal["ready", "partial", "unavailable"]
    fields: list[FundPublicField] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    evidence: list[FundPublicEvidence] = Field(default_factory=list)
    document_scope: Literal["public_product_description_not_verified_regulatory_document"] = (
        "public_product_description_not_verified_regulatory_document"
    )
    recommendation: Literal[False] = False


class FundReferencePoint(BaseModel):
    as_of: datetime
    percent_pct: float | None = None
    reference_value: float | None = None


class FundReferenceCurve(BaseModel):
    key: Literal["nav_pct", "nav2_pct"]
    label: str
    latest_pct: float | None = None
    points: list[FundReferencePoint] = Field(default_factory=list)


class FundPublicReference(BaseModel):
    source_symbol: str
    state: Literal["ready", "partial", "stale", "unavailable"]
    scope: Literal["reported_top_holdings_reference"] = "reported_top_holdings_reference"
    source: Literal["sina_fund"] = "sina_fund"
    warning: str = ""
    scope_note: str = (
        "上一季度前十大重仓的来源参考，不是整只基金当日涨跌或净值估算。"
        "两个口径由源方分别提供，口径差异未说明，不互算、不择优。"
    )
    source_as_of: datetime | None = None
    trading_date: date | None = None
    is_current_day: bool = False
    age_seconds: float | None = None
    disclosed_nav: float | None = None
    disclosed_nav_date: date | None = None
    heavy_stock_us: bool | None = None
    curves: list[FundReferenceCurve] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    evidence: list[FundPublicEvidence] = Field(default_factory=list)
    estimated_fund_nav: None = None
    estimated_fund_percent: None = None
