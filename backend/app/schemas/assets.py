"""Read-only fund contracts. NAV, disclosures and estimates are different facts."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class AssetInstrument(BaseModel):
    instrument_id: str
    asset_type: Literal["fund"] = "fund"
    fund_type: Literal["fund-otc", "fund-etf", "fund-lof", "fund-reits"]
    market: str
    symbol: str
    source_symbol: str
    currency: str
    name: str
    source: Literal["ths_official"] = "ths_official"


class AssetEvidence(BaseModel):
    endpoint: str
    params: dict[str, str | int]
    received_at: datetime
    payload_sha256: str
    receipt_id: str
    raw_available: bool = False


class AssetReadSection(BaseModel):
    state: Literal["ready", "partial", "unavailable"]
    reason: str | None = None
    missing: list[str] = Field(default_factory=list)
    source: Literal["ths_official", "system_calculation"] = "ths_official"
    evidence: list[AssetEvidence] = Field(default_factory=list)


class FundNavPoint(BaseModel):
    nav_date: date
    unit_nav: float | None = None
    adj_nav: float | None = None


class FundNavSection(AssetReadSection):
    currency: str
    points: list[FundNavPoint] = Field(default_factory=list)
    latest: FundNavPoint | None = None
    published_at: datetime | None = None
    publication_time_known: bool = False
    basis: Literal["official_nav"] = "official_nav"


class FundHolding(BaseModel):
    source_symbol: str
    name: str
    asset_type: str
    weight_pct: float | None = None
    report_date: date | None = None
    published_at: datetime | None = None
    modified_at: datetime | None = None


class FundHoldingsSection(AssetReadSection):
    items: list[FundHolding] = Field(default_factory=list)
    disclosed_weight_pct: float | None = None
    report_date: date | None = None
    current_positions: Literal[False] = False


class FundExposure(BaseModel):
    source_symbol: str
    name: str
    weight_pct: float
    via: list[str]
    report_date: date


class FundLookthroughSection(AssetReadSection):
    source: Literal["system_calculation"] = "system_calculation"
    items: list[FundExposure] = Field(default_factory=list)
    covered_weight_pct: float = 0
    uncovered_weight_pct: float = 100
    scope: Literal["disclosed_equity_one_fund_layer"] = "disclosed_equity_one_fund_layer"
    current_positions: Literal[False] = False


class FundTerm(BaseModel):
    label: str
    value: str
    condition: str | None = None


class FundTermsSection(AssetReadSection):
    manager_name: str | None = None
    fees: list[FundTerm] = Field(default_factory=list)
    dealing_rules: list[FundTerm] = Field(default_factory=list)
    investment_scope: str | None = None
    benchmark: str | None = None
    subscription_status: str | None = None


class FundResearchSection(AssetReadSection):
    source: Literal["system_calculation"] = "system_calculation"
    eligible: bool = False
    checks: list[FundTerm] = Field(default_factory=list)
    disclaimer: str = "仅供基金研究，不构成买卖建议"


class FundEstimateSection(AssetReadSection):
    source: Literal["system_calculation"] = "system_calculation"
    value: float | None = None
    currency: str
    basis: Literal["estimate"] = "estimate"


class FundSnapshot(BaseModel):
    instrument: AssetInstrument
    state: Literal["ready", "partial"]
    received_at: datetime
    nav: FundNavSection
    holdings: FundHoldingsSection
    lookthrough: FundLookthroughSection
    terms: FundTermsSection
    research: FundResearchSection
    estimate: FundEstimateSection


class AssetSearchResult(BaseModel):
    query: str
    asset_type: Literal["fund", "us_equity"]
    state: Literal["ready", "partial", "blocked"]
    items: list[AssetInstrument] = Field(default_factory=list)
    reason: str | None = None
    issues: list[str] = Field(default_factory=list)
    coverage: Literal["source_search_window"] = "source_search_window"
    evidence: list[AssetEvidence] = Field(default_factory=list)


class AssetCapability(BaseModel):
    asset_type: Literal["fund", "us_equity"]
    state: Literal["available", "blocked"]
    reason: str
    capabilities: list[str]
    unavailable: list[str]
    source: str | None = None
    trading: Literal[False] = False


class AssetCapabilities(BaseModel):
    items: list[AssetCapability]
