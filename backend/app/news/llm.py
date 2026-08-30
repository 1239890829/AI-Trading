"""LLM 摘要器占位实现。

与 `app/review/analyzers.py::LLMAnalyzer` 同一套取舍：
不同厂商接口形态差异大（OpenAI 兼容 / Anthropic / 各家国产模型），
在没有实际凭证时猜测字段只会产出不可用代码。等确定接口形态再补 `summarize()`。

需要配置 `ASHARE_NEWS_LLM_BASE_URL` / `ASHARE_NEWS_LLM_API_KEY` /
`ASHARE_NEWS_LLM_MODEL` 后才可用；未配置时 `SummaryRouter` 自动降级到规则摘要器。
"""
from __future__ import annotations


class LLMSummarizer:
    name = "llm"

    def __init__(self, base_url: str = "", api_key: str = "", model: str = ""):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model or "unknown"

    def is_available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def summarize(self, news: list[dict], announcements: list[dict]) -> dict:
        raise NotImplementedError(
            "LLM 摘要器尚未接入：需先提供 LLM 接口的 base_url / api_key / model。"
            "未配置时 SummaryRouter 会自动降级到 RulesSummarizer。"
        )
