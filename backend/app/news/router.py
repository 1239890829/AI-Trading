"""摘要器路由：配置化选择 + 失败降级 + 来源可追溯。

与 `app/review/model_router.py` 同构。关键点是**降级必须显式**：
回传 `model={requested, actual, degraded, reason}`，让前端能标出"这摘要来自规则引擎"。
否则读者会以为结论来自 LLM，实际来自关键词匹配——和本项目踩过的自指计算是同一类故障。
"""
from __future__ import annotations

import logging
import time

from app.news.llm import LLMSummarizer
from app.news.rules import RulesSummarizer

log = logging.getLogger(__name__)

FALLBACK = "rules"


class SummaryRouter:
    def __init__(
        self,
        requested: str = FALLBACK,
        llm: LLMSummarizer | None = None,
    ):
        self.requested = (requested or FALLBACK).strip().lower()
        self._rules = RulesSummarizer()
        self._llm = llm or LLMSummarizer()

    def _registry(self) -> dict:
        return {self._rules.name: self._rules, self._llm.name: self._llm}

    def _usage(self, actual: str, chain: list[str], degraded: bool = False, reason: str = "") -> dict:
        return {
            "requested": self.requested,
            "actual": actual,
            "fallback_chain": chain,
            "degraded": degraded,
            "reason": reason,
        }

    def summarize(self, news: list[dict], announcements: list[dict]) -> tuple[dict, dict]:
        """返回 (摘要结果, model 用量信息)。LLM 失败时降级到规则并重试一次。"""
        reg = self._registry()
        chain: list[str] = []

        if self.requested in reg:
            chain.append(self.requested)
            cand = reg[self.requested]
            if self.requested == self._llm.name and not self._llm.is_available():
                reason = "LLM 未配置 base_url/api_key，降级到规则摘要器"
                log.warning("news digest degraded: %s", reason)
                t0 = time.perf_counter()
                result = self._rules.summarize(news, announcements)
                usage = self._usage(self._rules.name, chain, True, reason)
                usage["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                return result, usage
        else:
            reason = f"未知摘要器 '{self.requested}'，降级到 {FALLBACK}"
            log.warning("news digest degraded: %s", reason)
            cand = self._rules
            chain.append(cand.name)
            t0 = time.perf_counter()
            result = cand.summarize(news, announcements)
            usage = self._usage(cand.name, chain, True, reason)
            usage["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            return result, usage

        t0 = time.perf_counter()
        try:
            result = cand.summarize(news, announcements)
            usage = self._usage(cand.name, chain)
        except Exception as exc:
            if cand.name == FALLBACK:
                raise  # 规则摘要器也挂了，不要吞掉
            reason = f"{cand.name} 摘要失败，降级：{exc}"
            log.warning("news digest fallback: %s", reason)
            chain = [*chain, self._rules.name]
            result = self._rules.summarize(news, announcements)
            usage = self._usage(self._rules.name, chain, True, reason)
        usage["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        return result, usage
