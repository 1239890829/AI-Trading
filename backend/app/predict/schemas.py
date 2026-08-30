"""新题材预判的数据结构（docs/theme-prediction.md §数据结构）。

设计原则与复盘模块一致：
- **证据先行**：每一条判断都必须挂着 EvidenceItem（来源+内容+得分贡献），
  没有证据支撑的结论不允许出现在 verdict 里。
- **可证伪**：fail_conditions 是一等公民——预判必须在什么情况下作废，
  写不出来失效条件的预判就是马后炮，不允许发布。
- **诚实标注**：梯队推演是假设不是结论；成功率是校准区间不是承诺；
  数据缺失显式进 data_gaps，不臆测。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """一条支撑（或削弱）预判的证据。"""

    kind: str  # hot_presence|news_linkage|policy_level|freshness|environment|capital
    source: str  # ths_hot_list|eastmoney_news|ths_limit_up|ths_longhu|market_context
    content: str  # 人可读的依据原文
    symbol: str | None = None
    contribution: float  # 该证据对总分的加权贡献（负值为削弱项）


class EchelonCandidate(BaseModel):
    """梯队推演的单个候选股。推演≠确认，置信度必须显式。"""

    symbol: str
    name: str | None = None
    role: str  # 龙头候选|中军候选|跟风候选
    hot_rank: int | None = None
    basis: str  # 为什么把它放在这个位置
    confidence: str  # high|medium|low


class EntryPlan(BaseModel):
    """一个介入时点的可执行计划。"""

    timing: str  # D1竞价|D1一字排板|D1盘中首板|D2分歧低吸|D2+补涨轮动|一字次日追高(禁)
    condition: str  # 触发条件（可证伪、可核对）
    action: str  # 操作建议（偏向，不构成投资建议）
    est_success: str  # 主观校准区间，如 "55%-65%"
    basis: str  # 该成功率的依据与适用前提
    risk: str


class ThemePrediction(BaseModel):
    """单个题材方向的完整预判。"""

    theme: str
    keywords: list[str] = Field(default_factory=list)
    verdict: str  # 预判成立|可能成立|弱预期|不预判
    score: float  # 0-1 加权总分
    confidence: str  # high|medium|low
    evidence: list[EvidenceItem] = Field(default_factory=list)
    fail_conditions: list[str] = Field(default_factory=list)
    echelon: list[EchelonCandidate] = Field(default_factory=list)
    echelon_note: str = ""
    evolution_path: list[str] = Field(default_factory=list)
    entry_plans: list[EntryPlan] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)


class PredictionReport(BaseModel):
    """一次预判运行的完整报告（落库 payload）。"""

    prediction_id: str = ""
    created_at: str  # UTC ISO
    context: str  # weekend|holiday|pre_market|intraday|evening
    target_date: str  # YYYYMMDD 预判目标的交易日
    trigger: str = "manual"  # manual|review|scheduled
    engine_version: str = "v1"
    market_env: dict = Field(default_factory=dict)  # 情绪阶段等环境快照
    predictions: list[ThemePrediction] = Field(default_factory=list)
    summary: str = ""
    verify: dict | None = None  # 目标日收盘后回填的验证结果
