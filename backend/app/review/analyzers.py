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

import json
import logging
from typing import Protocol

import httpx

from app.core.grounding import evidence_pool, grounding_violations
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
            ("picks", self._picks),
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
        "picks": "每日精选对照",
    }

    # ---- 维度 4：每日精选对照（2026-09-04 用户需求：准确率 + 失误归因）----

    #: 失误归因 → 复盘建议方向（六类来自 app.picks.engine.classify_failure）
    _PICK_CATEGORY_HINT = {
        "entry_bad": "买点执行问题为主：复核买入区间纪律与追高拦截",
        "sentiment_misread": "情绪误判为主：复核市场相位判定与推荐时的环境适配",
        "logic_failed": "入选逻辑失效为主：回看失误个股的入选依据与题材阶段判定",
        "missed": "踏空为主：方向对但价格未回买入区间，可评估区间上沿是否过紧",
        "data_issue": "数据缺失型不可评偏多：先修数据链再谈归因",
    }

    def _picks(self, data: ReviewData, method: MethodologyConfig) -> DimensionResult:
        p = data.picks
        findings: list[str] = []
        judgements: list[str] = []
        evidence: dict = {}

        if p is None:
            findings.append("未采集每日精选快照（服务未升级或采集异常），本维度降级")
            return DimensionResult(
                key="picks", title=self._TITLES["picks"], status="degraded",
                findings=findings, judgements=judgements, evidence=evidence, gaps=[],
            )

        if not p.items:
            findings.append(f"复盘日（含此前）无精选组合：{p.combo_date or '无记录'}")
            judgements.append("无推荐可对照。组合缺失属常态（首日部署/未到生成时点），不判失误")
            return DimensionResult(
                key="picks", title=self._TITLES["picks"],
                status="degraded" if p.gaps else "ok",
                findings=findings, judgements=judgements, evidence=evidence, gaps=p.gaps,
            )

        findings.append(
            f"对照组合：{p.combo_date} 生成 {len(p.items)} 只（T-1 生成、T 日持有）"
        )
        if not p.reviews:
            findings.append("逐股归因行缺失：generate_daily_review 未成功执行（见 gaps）")
            return DimensionResult(
                key="picks", title=self._TITLES["picks"], status="degraded",
                findings=findings, judgements=judgements, evidence=evidence, gaps=p.gaps,
            )

        item_by_symbol = {i.symbol: i for i in p.items}
        # 闸门日「仅观察」条目：本就未建议出手，误判归因不适用——单列不计入准确率
        obs_bad = [r for r in p.reviews if (item_by_symbol.get(r.symbol) and item_by_symbol[r.symbol].observation_only) and r.verdict == "bad"]
        scored = [r for r in p.reviews if r not in obs_bad]
        good = [r for r in scored if r.verdict == "good"]
        bad = [r for r in scored if r.verdict == "bad"]
        flat = [r for r in scored if r.verdict == "flat"]
        # 准确率口径（显式）：达成 / (达成+失误)。踏空(missed)与数据缺失(data_issue)
        # 属「不可评」，既不算失误也不算达成——不摊薄也不虚增命中率。
        total_judgeable = len(good) + len(bad)
        acc = round(len(good) / total_judgeable * 100, 1) if total_judgeable else None

        by_cat: dict[str, int] = {}
        for r in bad:
            by_cat[r.reason_category] = by_cat.get(r.reason_category, 0) + 1
        evidence.update({
            "combo_date": p.combo_date,
            "total": len(p.reviews),
            "achieved": len(good),
            "failed": len(bad),
            "na": len(flat),
            "observation_only_excluded": len(obs_bad),
            "accuracy_pct": acc,
            "accuracy_definition": "达成 / (达成+失误)；踏空与数据缺失不计入",
            "by_category": by_cat,
        })
        if acc is not None:
            findings.append(
                f"当日准确率 {acc}%：达成 {len(good)} / 失误 {len(bad)}"
                f"（不可评 {len(flat)}，仅观察剔除 {len(obs_bad)}）"
            )
        else:
            findings.append("当日无可判定样本（全部踏空/数据缺失/仅观察），准确率不适用")

        # 失误个股逐股归因（用户硬要求：判断失误的必须给出原因分析）
        for r in bad:
            findings.append(f"✗ {r.symbol} {r.name or ''}（{r.reason_category}）：{r.note or '无归因注记'}")
        if good:
            findings.append("达成：" + "、".join(f"{r.symbol} {r.name or ''}".strip() for r in good))

        th = method.thresholds
        if acc is not None and total_judgeable >= th.min_signal_samples:
            if acc < th.signal_hit_rate_floor * 100:
                judgements.append(
                    f"准确率 {acc}% 低于信号失效线 {th.signal_hit_rate_floor * 100:.0f}%"
                    "——复核推荐权重与闸门阈值，而不是单点归责个股"
                )
            else:
                judgements.append(f"准确率 {acc}% 在信号失效线之上，推荐体系暂无需大改")
        for cat, hint in self._PICK_CATEGORY_HINT.items():
            if by_cat.get(cat, 0) >= max(1, total_judgeable // 3):
                judgements.append(f"失误 {by_cat[cat]} 只归因「{cat}」：{hint}")
        if obs_bad:
            judgements.append(
                f"{len(obs_bad)} 只为空仓闸门日「仅观察」条目，不计入准确率"
                "——本就未建议出手（闸门日看方向验证，不看个股对错）"
            )

        # 影子持仓小节（P0-B）：空仓闸门的 A/B 对照——影子照常执行最新组合，
        # 其绩效就是"如果不空仓会怎样"的逐日答案
        sh = p.shadow
        if sh is not None and sh.enabled:
            ex = sh.execution or {}
            if not ex:
                findings.append("影子持仓：当日无执行日志（晨窗未执行或服务未运行，见 gaps）")
            else:
                bought = ex.get("bought") or []
                skipped = ex.get("skipped") or []
                filled = [b for b in bought if b.get("status") == "filled"]
                gate = ex.get("gate_summary") or {}
                findings.append(
                    f"影子持仓：买入成交 {len(filled)}/{len(bought)}"
                    f"（闸门拦下 {len(skipped)}：禁买 {gate.get('blocked', '?')}、"
                    f"观察 {gate.get('observe', '?')}、异常 {gate.get('anomaly', '?')}、"
                    f"未知 {gate.get('unknown', '?')}）"
                )
                acc = sh.account or {}
                ret = acc.get("total_return_pct")
                if ret is not None:
                    findings.append(
                        f"影子账户累计收益 {ret:+.2f}%（现金 {acc.get('cash')}）"
                        "——与真实空仓决策对照，量化 gate 的机会成本"
                    )
                for b in skipped[:3]:
                    findings.append(
                        f"闸门跳过：{b.get('symbol')} {b.get('name') or ''}（{b.get('state')}）{b.get('reason') or ''}"
                    )
        elif sh is None:
            findings.append("影子持仓未采集（服务旧版本或采集异常，见 gaps）")

        return DimensionResult(
            key="picks", title=self._TITLES["picks"],
            status="degraded" if p.gaps else "ok",
            findings=findings, judgements=judgements, evidence=evidence, gaps=p.gaps,
        )

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

        # 竞价溢价比（P0 因子）：昨日涨停股今日竞价承接力
        if m.auction_premium:
            summary = m.auction_premium.get("summary") or {}
            caveats = m.auction_premium.get("caveats") or []
            if summary and summary.get("judged"):
                findings.append(
                    f"竞价溢价（昨日涨停 {summary['judged']} 只可判定）："
                    f"中位数 {summary['median_pct']:+.2f}%，"
                    f"弱溢价(<3%)占比 {summary['weak_share']:.0%}，"
                    f"强溢价(≥5%)占比 {summary['strong_share']:.0%}"
                )
                evidence["auction_premium"] = summary
                if summary["weak_share"] >= 0.5:
                    judgements.append(
                        f"昨日涨停股竞价承接弱（弱溢价占比 {summary['weak_share']:.0%}），"
                        "一日游风险高，接力需缩容到最强前排"
                    )
                elif summary["median_pct"] is not None and summary["median_pct"] >= 5:
                    judgements.append("竞价溢价中位数强（≥5%），承接资金积极，但防高开兑现")
            elif caveats:
                findings.append(f"竞价溢价未采集成完：{'；'.join(caveats)}")
        else:
            findings.append("竞价溢价未采集（None）")

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

        # 数据链健康（复盘生成时刻的降级可见化）：熔断 open = 当日部分数据
        # 建立在备源口径上；ths 哨兵 alert = 题材标签可能失效。None = 未采集，
        # 不冒充"健康"（三态纪律）。今天的四类静默失败共同证明：不主动核对
        # 数据链，data_issue 归因永远是盲区。
        ph = data.market.provider_health
        if ph is not None:
            breakers = ph.get("breakers") or {}
            open_breakers = sorted(
                k for k, v in breakers.items() if v.get("state") == "open"
            )
            watch_breakers = sorted(
                k for k, v in breakers.items() if v.get("state") == "watch"
            )
            switches = ph.get("switch_log") or []
            evidence["provider_health"] = {
                "chain": ph.get("chain"),
                "open_breakers": open_breakers,
                "watch_breakers": watch_breakers,
                "last_good": ph.get("last_good") or {},
                "switch_count": len(switches),
            }
            if open_breakers:
                judgements.append(
                    f"复盘时仍有 {len(open_breakers)} 个熔断打开（{'、'.join(open_breakers)}），"
                    "对应方法正降级到备源——核对当日结论所依赖的数据口径与备源差异"
                )
            elif watch_breakers:
                findings.append(
                    f"熔断 watch 状态 {len(watch_breakers)} 个（未达阈值），数据链暂可用但需留意"
                )
            if switches:
                last = switches[-1]
                findings.append(
                    f"复盘前共记录 {len(switches)} 次数据源切换（最近：{last}）"
                )
            sent = ph.get("ths_reason_sentinel") or {}
            sstate = sent.get("state")
            if sstate == "alert":
                judgements.append(
                    "ths 涨停原因哨兵告警中（题材标签可能失效且无备源）——"
                    "题材归因、题材类选股结论需降权，并核对告警时段"
                )
            elif sstate in ("degraded", "probe_failed"):
                findings.append(
                    f"ths 涨停原因哨兵状态 {sstate}，题材标签可靠性待观察"
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


# ---------------------------------------------------------------- LLM 分析器


_LLM_SYSTEM_PROMPT = """\
你是 A 股盘后复盘的研判增强层。输入是规则引擎产出的各维度事实底稿：
findings 是已核实的事实，evidence 是数据依据，rules_judgements 是规则引擎的初步判断。

要求：
1. 只输出一个 JSON 对象，格式：
   {"judgements": {"trades": ["..."], "market": ["..."], "system": ["..."]}}
2. 每个维度给出 1-4 条研判判断，每条一句话；可以深化规则判定（讲清为什么、
   风险在哪、下一步核实什么），但所有事实必须来自底稿，禁止引入底稿之外的
   数据、行情或猜测。
3. 不要复述 findings 原文；不给具体的买卖价格、仓位比例建议。
4. 输入里没有的维度 key 不要输出；某维度没有可判断的内容时输出空数组。
5. 证据不足时弃权（空数组 / 少答）是合法输出，不要为凑结论编造数字。
   每条研判引用的数字必须能在底稿中找到；输出会经接地校验，引用底稿
   之外的带单位数字或含指令性交易建议（如"建议买入""目标价 X"、
   "仓位 N%"）的条目会被逐条丢弃。\
"""


class LLMAnalyzer:
    """LLM 分析器：规则底稿 + LLM 研判增强。

    需要配置 `ASHARE_REVIEW_LLM_BASE_URL` / `ASHARE_REVIEW_LLM_API_KEY` /
    `ASHARE_REVIEW_LLM_MODEL`（OpenAI 兼容接口）后才可用；未配置时
    `is_available()` 返回 False，`ModelRouter` 自动降级到规则分析器。

    原则（沿用本项目"LLM 不直接造数据"的一贯口径）：
    1. `RulesAnalyzer` 先跑出确定性事实底稿——findings / evidence 一概不动
    2. LLM 只重写各维度的 judgements（研判层），且只允许基于底稿发挥
    3. HTTP / 解析失败一律上抛 → ModelRouter 降级规则分析器（degraded 显式）
    4. LLM 漏答的维度保留规则原判；未知维度 key 自然丢弃（只按底稿维度取）
    5. 被 LLM 增强过的维度在 evidence 里打 `llm_enhanced` 标记，报告可追溯
    6. LLM 研判逐条过接地校验（app.core.grounding）：指令性建议 / 引用无据
       数字的条目被丢弃并留痕 `grounding_rejected`；全被拒则回退规则原判
    """

    name = "llm"

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        client: httpx.Client | None = None,
        provider: str = "openai",
        cli_path: str = "",
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model or "unknown"
        self._client = client
        self._provider = provider
        self._cli_path = cli_path

    def is_available(self) -> bool:
        if self._provider == "claude_cli":
            from app.core.llm_client import resolve_cli_path

            return resolve_cli_path(self._cli_path) is not None
        return bool(self.base_url and self.api_key)

    def analyze(self, data: ReviewData, method: MethodologyConfig) -> list[DimensionResult]:
        from app.core.llm_client import chat_completion, extract_json_object

        # 1. 规则底稿：事实与证据的唯一定义源
        draft = RulesAnalyzer().analyze(data, method)

        # 2. 构造 prompt：只喂底稿，不喂原始数据（底稿已做过缺失标注）
        prompt_dims = [
            {
                "key": d.key,
                "status": d.status,
                "findings": d.findings,
                "evidence": d.evidence,
                "rules_judgements": d.judgements,
            }
            for d in draft
        ]
        messages = [
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"trade_date": data.trade_date, "dimensions": prompt_dims},
                    ensure_ascii=False,
                ),
            },
        ]
        content = chat_completion(
            self.base_url, self.api_key, self.model, messages, client=self._client,
            provider=self._provider, cli_path=self._cli_path,
        )

        # 3. 解析 + 校验：结构不对就上抛（路由层降级），绝不半信半疑地采用
        payload = extract_json_object(content)
        raw = payload.get("judgements")
        if not isinstance(raw, dict):
            raise ValueError("LLM 回复缺少 judgements 对象")

        out: list[DimensionResult] = []
        for d in draft:
            vals = raw.pop(d.key, None)
            if not isinstance(vals, list):
                out.append(d)  # 漏答 / 类型不对 → 保留规则原判
                continue
            # 3b. 接地校验（评分-动作一致性的 LLM 侧落点）：证据池 = 该维度
            # 底稿全文（findings/judgements/evidence）。被拒条目逐条丢弃并
            # 留痕，不整体上抛——LLM 大部分合规、个别越权时仍采用合规部分。
            pool = evidence_pool(*d.findings, *d.judgements, extra=d.evidence)
            cleaned: list[str] = []
            rejected: list[dict] = []
            for v in vals:
                if not isinstance(v, str) or not v.strip():
                    continue
                t = v.strip()
                viol = grounding_violations(t, pool)
                if viol:
                    rejected.append({"text": t, "violations": viol})
                    continue
                cleaned.append(t)
            if rejected:
                log.warning(
                    "LLM 研判 %d 条未通过接地校验，已丢弃：%s", len(rejected), rejected
                )
            if not cleaned:
                out.append(d)  # 全被拒 → 保留规则原判，不冒充 LLM 增强
                continue
            ev = {**d.evidence, "llm_enhanced": True}
            if rejected:
                ev["grounding_rejected"] = rejected
            out.append(d.model_copy(update={"judgements": cleaned, "evidence": ev}))
        if raw:  # 剩下的 key 底稿里没有 → 丢弃，但必须留痕
            log.warning("LLM 回复含未知维度 key，已丢弃：%s", sorted(raw))
        return out
