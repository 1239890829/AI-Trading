from __future__ import annotations

from pydantic import BaseModel, Field


class DigestItem(BaseModel):
    """单条新闻/公告 + 规则摘要结果。"""

    title: str
    date: str | None = None
    url: str | None = None
    source: str | None = None
    type: str | None = None  # 公告专有（如"半年度报告摘要"）

    importance: str = Field(description="重要度分级：高 / 中 / 普通 / 低")
    importance_score: int = Field(description="重要度加权分（可解释：由命中项累加）")
    importance_reasons: list[str] = Field(default_factory=list)

    sentiment: str = Field(description="消息面情绪：偏正面 / 偏负面 / 分歧 / 中性")
    sentiment_reasons: list[str] = Field(default_factory=list)

    digest: str = Field(description="事实摘要（只截取原文，不润色不补全）")
    digest_source: str = Field(description="摘要来源，说明用的是正文还是标题、以及为什么")

    numbers: list[str] = Field(default_factory=list, description="关键数字（百分比/金额）")


class DigestModel(BaseModel):
    """摘要器用量与降级信息。降级必须显式——否则读者会误以为摘要来自 LLM。"""

    requested: str
    actual: str
    fallback_chain: list[str] = Field(default_factory=list)
    degraded: bool = False
    reason: str = ""
    latency_ms: int = 0


class NewsDigestPayload(BaseModel):
    symbol: str
    news: list[DigestItem] = Field(default_factory=list)
    announcements: list[DigestItem] = Field(default_factory=list)
    model: DigestModel
    # 数据源三态：None=正常；非 None=该侧数据源失败已降级（原因显式透出，
    # 绝不静默空列表——读者会误以为"没新闻"而非"新闻源挂了"）
    news_error: str | None = None
    announcements_error: str | None = None
