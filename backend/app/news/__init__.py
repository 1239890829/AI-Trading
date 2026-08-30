"""新闻/公告摘要：规则先行 + LLM 可插拔降级。

架构与 `app/review/` 同源（ModelRouter + LLMAnalyzer 占位）：
- 规则摘要器**永远可用**，不依赖任何外部凭证
- LLM 摘要器是增强层，配置后才启用，失败自动降级
- **降级必须显式**：`model.actual` / `model.degraded` / `model.reason` 会回传，
  否则读者会以为摘要来自 LLM，实际来自规则——这是"看起来合理但来源错了"的老毛病

红线提醒：输出只做「重要度分级 + 消息面情绪 + 事实摘要 + 关键数字」，
不含任何买卖建议（AGENTS.md 红线 3）。
"""
from app.news.router import SummaryRouter
from app.news.rules import RulesSummarizer
from app.news.llm import LLMSummarizer

__all__ = ["SummaryRouter", "RulesSummarizer", "LLMSummarizer"]
