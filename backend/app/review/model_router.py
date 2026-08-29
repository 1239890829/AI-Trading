"""模型路由：配置化选择分析器 + 失败降级 + 成本记录。

需求 2 的三件事在这里落地：
1. **配置化切换**：`ASHARE_REVIEW_MODEL=rules|llm`
2. **失败降级**：LLM 不可用或调用抛错 → 自动降级到规则分析器
3. **记录实际所用模型与成本**：`ModelUsage.requested` vs `actual` 分开存

关键设计：**降级必须是显式的**。`degraded=True` + `reason` 会写进报告，
否则读者会以为结论来自 LLM，而实际上它来自规则引擎——
这个"看起来合理但来源错了"的问题，和本项目踩过的自指计算是同一类故障。
"""
from __future__ import annotations

import logging
import time

from app.review.analyzers import Analyzer, LLMAnalyzer, RulesAnalyzer
from app.review.config import MethodologyConfig
from app.review.schemas import DimensionResult, ModelUsage, ReviewData

log = logging.getLogger(__name__)

FALLBACK_ANALYZER = "rules"


class ModelRouter:
    """分析器路由。默认规则引擎，LLM 可用时优先，失败降级。"""

    def __init__(
        self,
        requested: str = FALLBACK_ANALYZER,
        llm: LLMAnalyzer | None = None,
    ):
        self.requested = (requested or FALLBACK_ANALYZER).strip().lower()
        self._rules = RulesAnalyzer()
        self._llm = llm or LLMAnalyzer()

    def _registry(self) -> dict[str, Analyzer]:
        return {self._rules.name: self._rules, self._llm.name: self._llm}

    def resolve(self) -> tuple[Analyzer, ModelUsage]:
        """选出实际可用的分析器，并说明为什么。"""
        chain: list[str] = []
        reg = self._registry()

        # 首选：配置指定的
        if self.requested in reg:
            chain.append(self.requested)
            cand = reg[self.requested]
            if self.requested == self._llm.name and not self._llm.is_available():
                reason = "LLM 未配置 base_url/api_key，降级到规则分析器"
                log.warning("review model degraded: %s", reason)
                return self._rules, ModelUsage(
                    requested=self.requested, actual=self._rules.name,
                    fallback_chain=chain, degraded=True, reason=reason,
                )
            return cand, ModelUsage(
                requested=self.requested, actual=cand.name, fallback_chain=chain,
            )

        # 配置值未知 → 直接降级
        reason = f"未知分析器 '{self.requested}'，降级到 {FALLBACK_ANALYZER}"
        log.warning("review model degraded: %s", reason)
        return self._rules, ModelUsage(
            requested=self.requested, actual=self._rules.name,
            fallback_chain=chain, degraded=True, reason=reason,
        )

    def analyze(
        self, data: ReviewData, method: MethodologyConfig
    ) -> tuple[list[DimensionResult], ModelUsage]:
        """执行分析。LLM 抛错时降级到规则分析器并重试一次。"""
        analyzer, usage = self.resolve()
        t0 = time.perf_counter()
        try:
            dims = analyzer.analyze(data, method)
        except Exception as exc:
            if analyzer.name == FALLBACK_ANALYZER:
                raise  # 规则分析器也挂了，不要吞掉
            reason = f"{analyzer.name} 分析失败，降级：{exc}"
            log.warning("review analyzer fallback: %s", reason)
            usage = usage.model_copy(update={
                "actual": self._rules.name,
                "fallback_chain": [*usage.fallback_chain, self._rules.name],
                "degraded": True,
                "reason": reason,
            })
            dims = self._rules.analyze(data, method)
        usage.latency_ms = int((time.perf_counter() - t0) * 1000)
        return dims, usage
