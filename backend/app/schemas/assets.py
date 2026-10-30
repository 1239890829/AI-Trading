"""Read-only fund contracts. NAV, disclosures and estimates are different facts."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.asset_evidence import AssetEvidence
from app.schemas.fund_public import FundPublicProfile, FundPublicReference


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


class AssetReadSection(BaseModel):
    state: Literal["ready", "partial", "unavailable"]
    reason: str | None = None
    missing: list[str] = Field(default_factory=list)
    source: Literal["ths_official", "system_calculation"] = "ths_official"
    evidence: list[AssetEvidence] = Field(default_factory=list)


class FundCatalogMembership(BaseModel):
    group_key: str
    group_position: int
    row_position: int


class FundCatalogGroup(BaseModel):
    source_key: str
    source_position: int
    source_rows: int
    unique_symbols: int


class FundCatalogCandidate(BaseModel):
    source_symbol: str
    source_name: str | None = None
    state: Literal["candidate", "conflict", "out_of_scope"]
    source_position: int
    memberships: list[FundCatalogMembership] = Field(default_factory=list)
    occurrence_count: int
    quota_raw: str | None = None
    quota_status: Literal["reported", "unlimited", "unknown", "conflict"]
    quota_unit: None = None
    year_raw: str | None = None
    classify: list[str] | None = None
    missing: list[str] = Field(default_factory=list)
    conflicting_fields: list[str] = Field(default_factory=list)


class FundCatalogExcluded(BaseModel):
    group_key: str | None = None
    group_position: int | None = None
    row_position: int | None = None
    source_symbol: str | None = None
    reason: str


class FundCatalogCoverage(BaseModel):
    source_rows: int = 0
    unique_symbols: int = 0
    of_symbols: int = 0
    candidate_symbols: int = 0
    out_of_scope_symbols: int = 0
    conflict_symbols: int = 0
    placeholder_count: int = 0
    excluded_rows: int = 0
    complete_source_response: bool = False


class FundCatalog(BaseModel):
    state: Literal["ready", "partial", "unavailable"]
    source: Literal["ths_official"] = "ths_official"
    tab: Literal["meiguo"] = "meiguo"
    purchase: Literal["all", "source_buyable"]
    groups: list[FundCatalogGroup] = Field(default_factory=list)
    items: list[FundCatalogCandidate] = Field(default_factory=list)
    excluded: list[FundCatalogExcluded] = Field(default_factory=list)
    coverage: FundCatalogCoverage
    source_as_of: None = None
    evidence: list[AssetEvidence] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    scope: Literal["source_meiguo_catalog_not_investment_mandate"] = "source_meiguo_catalog_not_investment_mandate"
    scope_note: str = "同花顺美国栏目含全球、新兴市场等基金；栏目归属不等于正式投资范围，不代表全部为纯美国敞口。"
    purchase_note: str = "源可购仅表示出现在该次筛选结果；未出现或限额为0不据此判定不可购，实际规则以公告及渠道为准。"
    recommendation: Literal[False] = False


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
    source: Literal["ths_official", "mixed_public_sources"] = "ths_official"
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
    public_profile: FundPublicProfile | None = None
    reference: FundPublicReference | None = None


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
