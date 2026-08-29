"""分析器：把采集到的数据变成复盘结论。

**Analyzer 是一个协议，不是具体实现**——这是需求 2（模型可配置切换 + 失败降级）
的结构性前提。规则分析器与 LLM 分析器实现同一个 `analyze()` 签名，
`ModelRouter` 才能在它们之间自由切换与降级。

默认走 `RulesAnalyzer`：
- 零成本、零外部依赖
- 结果**可复现**（同样的输入必然同样的输出），这对"方法论自我迭代"是硬要求——
  如果每次复盘结论都带随机性，就无法判断"哪个版本的复盘方式更有效"
- 后续接入 LLM 只需实现 `analyze()`，不动编排层
"""
from __future__ import annotations

import logging
from typing import Protocol

from app.review.config import MethodologyConfig
from app.review.schemas import (
    DimensionResult,
    ReviewData,
)

log = logging.getLogger(__name__)


class Analyzer(Protocol):
    """分析器协议。规则与 LLM 实现同一签名，便于热切换与降级。"""

    name: str

    def analyze(self, data: ReviewData, method: MethodologyConfig) -> list[DimensionResult]:
        """产出各维度结论。被数据阻断的维度必须返回 status='blocked'。"""
        ...


# ---------------------------------------------------------------- 规则分析器


class RulesAnalyzer:
    """确定性规则分析器（默认）。

    所有阈值来自 `MethodologyConfig.thresholds`，不硬编码——
    否则"调阈值"就得改代码，方法论版本化形同虚设。
    """

    name = "rules"

    def analyze(self, data: ReviewData, method: MethodologyConfig) -> list[DimensionResult]:
        blocked = data.blocked_dimensions()
        out: list[DimensionResult] = []
        for key, builder in (
            ("trades", self._trades),
            ("market", self._market),
            ("system", self._system),
        ):
            dim_cfg = method.dimensions.get(key)
            if dim_cfg and not dim_cfg.enabled:
                continue
            if key in blocked:
                reasons = [g.reason for g in data.all_gaps
                           if g.severity == "block" and g.impact.startswith(key)]
                out.append(DimensionResult(
                    key=key, title=self._TITLES[key], status="blocked",
                    findings=[], judgements=[],
                    evidence={"blocked_reason": reasons},
                    gaps=[g for g in data.all_gaps if g.impact.startswith(key)],
                ))
                continue
            out.append(builder(data, method))
        return out

    _TITLES = {
        "trades": "当日操作评估",
        "market": "市场环境研判",
        "system": "系统表现诊断",
    }

    # ---- 维度 1：当日操作评估 ----

    def _trades(self, data: ReviewData, method: MethodologyConfig) -> DimensionResult:
        th = method.thresholds
        t = data.trading
        findings: list[str] = []
        judgements: list[str] = []
        evidence: dict = {}

        findings.append(f"当日委托 {t.trade_count} 笔")
        if t.trade_count == 0:
            judgements.append("当日无操作，无需评估执行偏差")
            return DimensionResult(
                key="trades", title=self._TITLES["trades"], status="ok",
                findings=findings, judgements=judgements, evidence=evidence,
                gaps=t.gaps,
            )

        # 规则遵守：被拒单
        rejected = [o for o in t.orders if o.status == "rejected" or
                    (o.reason and o.status != "filled")]
        filled = [o for o in t.orders if o.status == "filled"]
        pending = [o for o in t.orders if o.status == "pending"]
        evidence.update({
            "rejected_count": len(rejected),
            "filled_count": len(filled),
            "pending_count": len(pending),
        })

        if rejected:
            findings.append(f"被拒/未成交 {len(rejected)} 笔")
            reasons = [f"{o.symbol}:{o.reason}" for o in rejected if o.reason][:5]
            evidence["reject_reasons"] = reasons
            judgements.append(
                f"存在 {len(rejected)} 笔规则拦截（{'；'.join(reasons)}）——"
                "需确认是规则正确地拦住了冲动交易，还是下单参数本身不合法"
            )

        # 过度交易
        if t.trade_count > th.max_daily_trades_for_discipline:
            judgements.append(
                f"当日 {t.trade_count} 笔超过纪律上限 {th.max_daily_trades_for_discipline} 笔，"
                "存在过度交易倾向"
            )

        # 执行偏差（滑点）：只对已成交单计算
        slippages = []
        for o in filled:
            if o.filled_price is None or not o.price:
                continue
            bps = abs(o.filled_price - o.price) / o.price * 10000
            slippages.append((o.symbol, round(bps, 1)))
        if slippages:
            worst = max(slippages, key=lambda x: x[1])
            evidence["max_slippage_bps"] = worst[1]
            if worst[1] > th.slippage_alert_bps:
                judgements.append(
                    f"{worst[0]} 成交滑点 {worst[1]}bp 超过 {th.slippage_alert_bps}bp 告警线，"
                    "委托价与成交价偏离过大"
                )

        # 盈亏归因
        if t.realized_pnl is not None:
            findings.append(f"当日已实现盈亏 {t.realized_pnl:+.2f}")
            evidence["realized_pnl"] = t.realized_pnl
        floating = [p for p in t.positions if p.pnl is not None]
        if floating:
            total_float = round(sum(p.pnl or 0 for p in floating), 2)
            findings.append(f"持仓浮盈合计 {total_float:+.2f}（{len(floating)} 只）")
            evidence["floating_pnl"] = total_float
            worst_p = min(floating, key=lambda p: p.pnl or 0)
            evidence["worst_position"] = {"symbol": worst_p.symbol, "pnl_pct": worst_p.pnl_pct}
            if (worst_p.pnl_pct or 0) < -5:
                judgements.append(
                    f"{worst_p.symbol} 浮亏 {worst_p.pnl_pct}%，"
                    "需复核买入依据是否已被证伪（对照失效条件）"
                )
        elif t.positions:
            findings.append(f"{len(t.positions)} 只持仓但无最新价，浮盈不可用")

        # 亏损单占比
        if filled:
            loss = sum(1 for o in filled if o.side == "sell" and o.filled_price
                       and o.filled_price < o.price)
            loss_ratio = loss / len(filled)
            evidence["loss_ratio"] = round(loss_ratio, 3)
            if loss_ratio > th.loss_trade_ratio_alert:
                judgements.append(
                    f"卖出亏损占比 {loss_ratio:.0%} 超过 {th.loss_trade_ratio_alert:.0%}，"
                    "择时或选股环节可能存在系统性偏差"
                )

        status = "degraded" if t.gaps else "ok"
        return DimensionResult(
            key="trades", title=self._TITLES["trades"], status=status,
            findings=findings, judgements=judgements, evidence=evidence, gaps=t.gaps,
        )

    # ---- 维度 2：市场环境研判 ----

    def _market(self, data: ReviewData, method: MethodologyConfig) -> DimensionResult:
        m = data.market
        findings: list[str] = []
        judgements: list[str] = []
        evidence: dict = {}

        # 指数
        if m.indices:
            for q in m.indices[:4]:
                findings.append(f"{q.name} {q.close:.2f} ({q.change_pct:+.2f}%)")
            evidence["indices"] = [q.model_dump() for q in m.indices[:6]]
            up = sum(1 for q in m.indices if q.change_pct > 0)
            evidence["index_up_ratio"] = round(up / len(m.indices), 3)
        else:
            findings.append("指数数据缺失")

        # 情绪
        if m.sentiment:
            phase = m.sentiment.get("phase")
            temp = m.sentiment.get("temperature")
            conf = m.sentiment.get("confidence")
            unreliable = m.sentiment.get("phase_unreliable")
            findings.append(f"情绪阶段 {phase}（温度 {temp}，置信度 {conf}）")
            evidence["sentiment"] = {
                "phase": phase, "temperature": temp, "confidence": conf,
                "phase_unreliable": unreliable,
                "heat_level": (m.sentiment.get("heat") or {}).get("level"),
                "earning_level": (m.sentiment.get("earning") or {}).get("level"),
            }
            if unreliable:
                judgements.append(
                    "情绪判定被哨兵标记为不可信（疑似日期串了或数据自指），"
                    "本次结论不作为下一交易日依据"
                )
            elif phase in {"退潮", "冰点"}:
                judgements.append(f"市场处于{phase}期，应降低仓位或空仓，不宜新开接力仓")
            elif phase == "分歧":
                judgements.append("市场分歧期：只做最强前排且严控仓位，回避跟风")
            elif phase == "高潮":
                judgements.append("市场高潮期：溢价充足但随时转折，不追高标")
        else:
            findings.append("情绪数据缺失")

        # 宽度
        if m.breadth:
            findings.append(
                f"涨 {m.breadth.get('up')} / 跌 {m.breadth.get('down')}，"
                f"涨停 {m.breadth.get('limit_up')} / 跌停 {m.breadth.get('limit_down')}"
            )
            evidence["breadth"] = m.breadth
            anom = m.breadth.get("limit_anomaly")
            if anom:
                judgements.append(
                    f"有 {anom} 只个股涨幅超出其限价档位（限价口径存疑），"
                    "涨跌停统计可能失真，需核对 ST / 板块口径"
                )

        # 题材
        if m.theme_summary:
            s = m.theme_summary
            findings.append(
                f"题材 {s.get('theme_count')} 个，梯队断层 {s.get('broken_ladder')} 个"
            )
            evidence["theme_summary"] = s
            broken = s.get("broken_ladder") or 0
            if broken:
                judgements.append(
                    f"{broken} 个题材出现梯队断层（最高板悬空），"
                    "这些题材的接续风险高，不宜追其高位股"
                )

        status = "degraded" if m.gaps else "ok"
        return DimensionResult(
            key="market", title=self._TITLES["market"], status=status,
            findings=findings, judgements=judgements, evidence=evidence, gaps=m.gaps,
        )

    # ---- 维度 3：系统表现诊断 ----

    def _system(self, data: ReviewData, method: MethodologyConfig) -> DimensionResult:
        th = method.thresholds
        findings: list[str] = []
        judgements: list[str] = []
        evidence: dict = {}

        gaps = data.all_gaps
        block_gaps = [g for g in gaps if g.severity == "block"]
        warn_gaps = [g for g in gaps if g.severity == "warn"]

        findings.append(
            f"数据完整度：{len(gaps)} 处缺失（阻断 {len(block_gaps)} / 降级 {len(warn_gaps)}）"
        )
        evidence["gap_count"] = len(gaps)
        evidence["block_gap_count"] = len(block_gaps)
        evidence["gaps"] = [g.model_dump() for g in gaps]

        if block_gaps:
            fields = ", ".join(sorted({g.field for g in block_gaps}))
            judgements.append(
                f"以下数据缺失导致对应维度不可用：{fields}。"
                "在数据补齐前，相关结论不应作为决策依据"
            )

        # 数据源健康
        market_gaps = [g for g in gaps if g.source.startswith(("hub", "snapshot", "build_theme", "market_context"))]
        evidence["data_source_gap_count"] = len(market_gaps)
        if len(market_gaps) >= 2:
            judgements.append(
                f"市场数据出现 {len(market_gaps)} 处缺失，可能是数据源链整体异常，"
                "建议检查 provider 健康状态与交易日历"
            )

        # 信号质量：样本不足时明确不做判定
        sig_samples = (data.market.sentiment or {}).get("pool_today_count")
        evidence["signal_samples"] = sig_samples
        if sig_samples is None:
            findings.append("信号样本量未知，跳过参数失效判定")
        elif sig_samples < th.min_signal_samples:
            findings.append(
                f"信号样本 {sig_samples} 少于 {th.min_signal_samples}，样本不足不做失效判定"
            )
        else:
            findings.append(f"信号样本 {sig_samples}，可进入失效评估")

        # 参数失效的可观测征兆
        sentiment = data.market.sentiment or {}
        if sentiment.get("phase_unreliable"):
            judgements.append(
                "情绪引擎自检测出不可信读数（哨兵触发），"
                "优先排查日期锚定与跨日 join，而不是调阈值"
            )

        status = "blocked" if not gaps and not data.market.sentiment else (
            "degraded" if gaps else "ok"
        )
        return DimensionResult(
            key="system", title=self._TITLES["system"], status=status,
            findings=findings, judgements=judgements, evidence=evidence, gaps=gaps,
        )


# ---------------------------------------------------------------- LLM 分析器（占位）


class LLMAnalyzer:
    """LLM 分析器占位实现。

    需要配置 `ASHARE_REVIEW_LLM_BASE_URL` / `ASHARE_REVIEW_LLM_API_KEY` /
    `ASHARE_REVIEW_LLM_MODEL` 后才可用。未配置时 `is_available()` 返回 False，
    `ModelRouter` 会自动降级到规则分析器。

    之所以留这个壳而不是直接实现：不同厂商的接口形态差异大（OpenAI 兼容 / Anthropic /
    各家国产模型），在没有实际凭证时猜测字段只会产出不可用代码。
    等确定了接口形态再补 `analyze()` 里的调用。
    """

    name = "llm"

    def __init__(self, base_url: str = "", api_key: str = "", model: str = ""):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model or "unknown"

    def is_available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def analyze(self, data: ReviewData, method: MethodologyConfig) -> list[DimensionResult]:
        raise NotImplementedError(
            "LLM 分析器尚未接入：需先提供 LLM 接口的 base_url / api_key / model。"
            "未配置时 ModelRouter 会自动降级到 RulesAnalyzer。"
        )
