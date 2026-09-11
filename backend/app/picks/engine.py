"""每日精选引擎（CONTEXT.md: Daily Picks；grill-with-docs 五决策落地）。

设计原则：
1. **规则版多角色**（TradingAgents 编排思想的规则落地）：五个"分析师"各自产出
   0-100 子评分 + basis，合成器加权求和 + 一票否决。全程可解释，LLM 接入后
   按维度逐个增强（子评分接口不变）。
2. **参考仓库择优**（docs/github-stars-trading-analysis.md）：TradingAgents 的
   角色分工与决策日志、ai-hedge-fund 的 mandate 解耦（已落地回测）、
   daily_stock_analysis 的每日节奏；其"买卖点位/确定性结论"风格一律不引（红线 3）。
3. **组合稳定**（换股门槛）：新候选综合分超出被换成员 ≥15 分才替换；
   盘中仅硬性失效（炸板/跌停/黑天鹅）提前移除。
4. 维度数据全部来自既有管线：情绪=情绪引擎+题材合力；消息=EventCard 方向命中；
   技术=tech_score.score_stock（防飞刀口径）；基本面=财务摘要；资金=资金流+快照。

参考仓库 → 本项目能力映射（择优依据，防"主观臆测选股"）：
- 消息因子：EventCard 方向词典（本系统独有，外部仓库无 A 股事件结构化）
- 技术指标：score_stock 八维卡（前端 analyze 同口径，带防飞刀；v2 含形态、
  v3 起含 RPS 全市场横截面）+ easy_tdx 缠论候选（远期）
- 情绪：情绪引擎（防自指哨兵）——TradingAgents 无此概念，是 A 股特色维度
- 资金：资金流净额+快照量能（daily_stock_analysis 用免费源做同类事的垂直版）
- 基本面：估值/财务快照（ai-hedge-fund 的 fundamentals 分析师角色对应）
"""

from __future__ import annotations

from typing import Any

#: 第一版五维权重（历史组合已持久化此表，复盘回溯时仍按它还原当时的口径）。
#: **新六维权重由 `app.picks.regime.weights_for` 按炒作阶段提供**——
#: Regime 是权重选择器而非评分维度：业绩驱动期基本面主导，业绩空窗期
#: 情绪与题材梯队主导（CONTEXT.md: Speculation Regime）。新增维度 echelon
#: 见 `app.picks.echelon`（个股在题材天梯中的地位，与题材阶段联合读取）。
WEIGHTS = {
    "sentiment": 0.20,   # 情绪面：市场阶段 + 题材合力
    "news": 0.25,        # 消息面：EventCard 方向命中（利好/利空/强度）
    "tech": 0.25,        # 技术面：score_stock 八维卡（v3 起含形态+RPS）
    "fundamental": 0.15, # 基本面：估值与盈利趋势
    "capital": 0.15,     # 资金面：净流入/量能/龙虎榜
}
REPLACE_THRESHOLD = 15.0
#: 组合**容量上限**（不是"每天必须凑满"的目标数）。够格的标的不足时名单就该更短——
#: 名额由质量决定，不由常量决定（2026-09-10 用户要求：不硬凑、也不过多）。
MAX_PICKS = 5
#: **入选门槛**（综合分下限）：低于此分一律不入选，名额不兜底。2026-09-10 加。
#:
#: 为什么锚在 50：六维综合分以 50 为中性锚点——各维"无事件/数据缺失/相位缺失"的
#: 兜底分都是 50（见 score_news / score_capital / score_sentiment），且权重和为 1，
#: 所以 **≥50 ⇔ 多维证据整体净正面**，<50 ⇔ 没有一只维度站得住。这与空仓闸门
#: 同属一套逻辑（gate.py：「没有赚钱效应的市场里，任何精选都是硬凑，硬凑的结果是
#: 大面」），只是粒度从"整天要不要出手"下到"单只要不要进组合"。
#:
#: 与 REPLACE_THRESHOLD 的分工（不可互相替代）：
#: - 换股门槛管**换不换**——同池成员之间的相对分差（防小幅波动换股）；
#: - 入选门槛管**够不够格**——绝对质量线（防拿低分凑满名额）。
#:
#: 参数标定：50 有语义锚点、无回测标定。调参唯一入口是本常量。已知取舍——门槛附近
#: 可能出现"今天 49 出、明天 51 进"的抖动，正解是引入回差（hysteresis，与换股门槛
#: 同源的防抖手法），但回差幅度需要回放实证（rejected 表 30 交易日样本，约 2026-10
#: 中旬到齐）才能定，**不预先臆造第二个常量**。
MIN_PICK_SCORE = 50.0
#: 每日最多换入几只。稳定性的真正保障——分差门槛在涨停股主导的候选池下
#: 拦不住换股（梯队分差动辄 30+），必须再设数量上限。详见函数 docstring。
MAX_SWAPS_PER_DAY = 2
BUY_RANGE_PCT = 0.03  # 买入范围：现价 ±3%

#: 默认值哨兵：把「未传参」与「显式传 None」区分开。
#: `max_swaps=None` 在语义上是"不限换股"（首次建仓/回放对照组在用），
#: 不能拿它兼职表示"未传参"——否则运行时覆盖层一启用就会把"不限"变成"上限"。
_UNSET: Any = object()


def _live(key: str, default: Any) -> Any:
    """运行时覆盖优先（AI 控制台参数白名单，P1-15），否则代码常量。

    覆盖层为空 ⇒ 行为与改动前**完全一致**（`runtime_params.get` 回落默认值）。
    读在**调用时**而不是签名默认值里：签名默认在导入时求值，覆盖层永远读不到。
    """
    from app.core import runtime_params

    return runtime_params.get(key, default)


def effective_limits() -> dict:
    """当前实际生效的组合节奏参数（运行时覆盖优先）——供 meta 留痕与测试断言。

    为什么单独暴露：`picks.py` 写进 meta 的 `replace_threshold` / `min_pick_score`
    必须是**生效值**而不是代码常量，否则调参后复盘看到的仍是旧数（口径漂移）。
    """
    return {
        "replace_threshold": _live("picks_replace_threshold", REPLACE_THRESHOLD),
        "max_swaps_per_day": _live("picks_max_swaps_per_day", MAX_SWAPS_PER_DAY),
        "min_pick_score": _live("picks_min_pick_score", MIN_PICK_SCORE),
    }

REASON_CATEGORIES = {
    "event_expired": "事件失效（利好证伪/落地即出货）",
    "board_receding": "板块退潮（题材合力消散）",
    "market_drag": "大盘拖累（系统性下行）",
    "data_issue": "数据源问题（行情/消息卡顿导致误判）",
    "news_gap": "消息卡顿（关键消息未及时入库）",
    "logic_failed": "入选逻辑失效（技术/资金依据证伪）",
    "gone_well": "走势健康（符合或超预期）",
    "entry_bad": "买点不对（未按买入范围介入，追高被套）",
    "sentiment_misread": "情绪误判（持有期市场相位转弱）",
    "missed": "未介入（全天价格高于买入区间，踏空而非失误）",
}


# ---- 事件可信度权重（选股 2.0 §3 消息面增强：tier × certainty，2026-09-02）----
# tier：1=官方/监管 … 5=自媒体传闻；certainty：done=已落地 / proposed=提议中 / rumor=传闻
TIER_WEIGHTS = {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.4, 5: 0.2}
CERTAINTY_WEIGHTS = {"done": 1.0, "proposed": 0.6, "rumor": 0.3}


def event_weight(source_tier: int | None, certainty: str | None) -> float:
    """单条事件的可信度权重。缺失字段按中性档（tier 3 / done）处理——
    权重缺失默认取中间值，但会在索引层留下"未加权"的痕迹，不冒充已判。"""
    return TIER_WEIGHTS.get(int(source_tier or 3), 0.6) * CERTAINTY_WEIGHTS.get(
        str(certainty or "done"), 0.6
    )


def score_sentiment(
    market_phase: str | None,
    theme_up_ratio: float | None,
    promo_percentile: float | None = None,
) -> tuple[float, str]:
    """情绪面：市场阶段（情绪引擎）为主、题材内涨跌家数比为辅、接力环境分位修正。

    market_phase ∈ 冰点/修复/发酵/高潮/分歧/退潮（sentiment 引擎输出）。
    P0-3b 后 phase 本身已按近 120 交易日历史分位校准（绝对阈值死档已消除），
    这里再把 `promo_1to2` 的**历史分位**作为接力环境修正项（±10 分）——
    晋级率处在历史低位时，即使相位看着还行，题材接力也应压分。
    """
    if market_phase:
        phase_score = {"修复": 80, "发酵": 90, "高潮": 70, "分歧": 55, "退潮": 30, "冰点": 25}[market_phase]
        parts = [f"市场阶段「{market_phase}」→ {phase_score} 分"]
        score = phase_score * 0.7  # 有相位：相位主导，题材比例只做微调
    else:
        parts = ["市场阶段缺失 → 中性 50 分"]
        score = 50.0  # 缺失=中性，不打折（"缺失=中性"的承诺不能被权重打折）
    if promo_percentile is not None:
        # 分位 0–100，中位 50 → ±10 分修正
        adj = round((promo_percentile - 50) * 0.2, 1)
        score = max(0, min(100, score + adj))
        parts.append(f"晋级率历史分位 {promo_percentile} → {'+' if adj >= 0 else ''}{adj}")
    if theme_up_ratio is not None:
        # 题材内涨家数占比 0-1 → ±15 分修正
        adj = round((theme_up_ratio - 0.5) * 30, 1)
        score = max(0, min(100, score + adj))
        parts.append(f"题材涨家占比 {round(theme_up_ratio * 100)}% → {'+' if adj >= 0 else ''}{adj}")
    return round(score, 1), "；".join(parts)


def score_news(bull_events: int, bear_events: int, top_title: str | None, top_direction: str | None) -> tuple[float, str]:
    """消息面：活跃 EventCard 方向命中的**加权强度和**。

    输入 bull/bear 是强度和（事件索引层已乘 `event_weight`：source_tier 越权威、
    certainty 越确定权重越高，见下方常量），不再是裸条数——
    一条 tier1 官方落地政策的权重远高于三条 tier5 传闻。
    利空权重更高（突发利空的反身性更强）；无事件=中性 50 分（不臆测）。
    top 事件标题进 basis 供卡片「关联消息」。
    """
    if bull_events == 0 and bear_events == 0:
        return 50.0, "无活跃事件命中，消息面中性"
    score = max(0, min(100, 50 + bull_events * 18 - bear_events * 25))
    parts = [f"利好事件 {bull_events} 条 / 利空事件 {bear_events} 条 → {score} 分"]
    if top_title:
        parts.append(f"主事件：{top_title}（{top_direction or '方向待判'}）")
    return round(score, 1), "；".join(parts)


def score_tech(tech_card: dict | None) -> tuple[float, str]:
    """技术面：score_stock 八维卡总分（0-100 已归一），basis 用其信号摘要。"""
    if tech_card is None:
        return 50.0, "技术样本不足（<60 根日K），中性处理"
    return round(float(tech_card.get("score") or 50), 1), tech_card.get("summary") or "技术评分卡"


def score_fundamental(
    pe_ttm: float | None,
    revenue_growth: float | None,
    profit_yoy: float | None = None,
    roe: float | None = None,
    gross_margin: float | None = None,
) -> tuple[float, str]:
    """基本面：估值 + 盈利趋势的粗规则（第一版；LLM 接入后由基本面分析师增强）。

    - PE：0<pe≤30 → 70 分带；30-60 → 55；>60 或负 → 35（亏损/高估）
    - 营收增速（如可得）：>20% +15 / 0-20% +8 / 负 -10
    - 净利同比（2026-09-01 补，东财 SJLTZ）：>20% +5 / <0 −8（增收不增利或利润下滑）
    - ROE 加权（2026-09-01 补，WEIGHTAVG_ROE）：≥15% +8 / 8-15% +4 / <0 −8
    - 毛利率（2026-09-01 补，XSMLL；行业差异大，只加分不扣分）：≥40% +6 / 20-40% +3
    """
    parts: list[str] = []
    score = 50.0
    if pe_ttm is not None and pe_ttm > 0:
        if pe_ttm <= 30:
            score = 70.0
            parts.append(f"PE {round(pe_ttm, 1)}（≤30 合理带）")
        elif pe_ttm <= 60:
            score = 55.0
            parts.append(f"PE {round(pe_ttm, 1)}（30-60 中性带）")
        else:
            score = 35.0
            parts.append(f"PE {round(pe_ttm, 1)}（偏高）")
    elif pe_ttm is not None and pe_ttm <= 0:
        # 负 PE = TTM 净利润为负（亏损），与"数据缺失"含义完全不同。
        # 原实现并入 else 输出"PE 缺失，估值中性"，会让人误以为没取到数据，
        # 实际是取到了、但公司处于亏损——损失了这条可解释信息。
        parts.append(f"PE {round(pe_ttm, 1)}（TTM 亏损，估值维度不适用）")
    else:
        parts.append("PE 缺失，估值中性")
    if revenue_growth is not None:
        if revenue_growth > 20:
            score = min(100, score + 15)
            parts.append(f"营收增速 +{round(revenue_growth, 1)}%")
        elif revenue_growth >= 0:
            score = min(100, score + 8)
            parts.append(f"营收增速 +{round(revenue_growth, 1)}%")
        else:
            score = max(0, score - 10)
            parts.append(f"营收增速 {round(revenue_growth, 1)}%")
    if profit_yoy is not None:
        if profit_yoy > 20:
            score = min(100, score + 5)
            parts.append(f"净利同比 +{round(profit_yoy, 1)}%")
        elif profit_yoy < 0:
            score = max(0, score - 8)
            if revenue_growth is not None and revenue_growth > 0:
                parts.append(f"净利同比 {round(profit_yoy, 1)}%（增收不增利警讯）")
            else:
                parts.append(f"净利同比 {round(profit_yoy, 1)}%（利润下滑）")
    if roe is not None:
        if roe >= 15:
            score = min(100, score + 8)
            parts.append(f"ROE {round(roe, 1)}%（≥15 优质）")
        elif roe >= 8:
            score = min(100, score + 4)
            parts.append(f"ROE {round(roe, 1)}%")
        elif roe < 0:
            score = max(0, score - 8)
            parts.append(f"ROE {round(roe, 1)}%（亏损）")
    if gross_margin is not None:
        if gross_margin >= 40:
            score = min(100, score + 6)
            parts.append(f"毛利率 {round(gross_margin, 1)}%（≥40 高毛利）")
        elif gross_margin >= 20:
            score = min(100, score + 3)
            parts.append(f"毛利率 {round(gross_margin, 1)}%")
    return round(score, 1), "；".join(parts)


def score_capital(net_inflow: float | None, volume_ratio: float | None, on_lhb: bool) -> tuple[float, str]:
    """资金面：主力净流入（亿）+ 量能 + 龙虎榜。

    - 净流入：≥2 亿 +30 / 0.5-2 亿 +18 / −0.5-0.5 亿 中性 / <−1 亿 −20
    - 量比：≥1.5 +12 / 0.8-1.5 +5 / <0.5 −8（缩量）
    - 龙虎榜上榜 +8（有公开资金关注；不区分买卖净额，basis 注明）
    """
    parts: list[str] = []
    score = 50.0
    if net_inflow is not None:
        yi = net_inflow / 1e8
        if yi >= 2:
            score += 30
            parts.append(f"主力净流入 {round(yi, 2)} 亿")
        elif yi >= 0.5:
            score += 18
            parts.append(f"主力净流入 {round(yi, 2)} 亿")
        elif yi < -1:
            score -= 20
            parts.append(f"主力净流出 {round(abs(yi), 2)} 亿")
        else:
            parts.append(f"资金净额 {round(yi, 2)} 亿（中性）")
    if volume_ratio is not None:
        if volume_ratio >= 1.5:
            score += 12
            parts.append(f"量比 {volume_ratio}（放量）")
        elif volume_ratio >= 0.8:
            score += 5
            parts.append(f"量比 {volume_ratio}")
        elif volume_ratio < 0.5:
            score -= 8
            parts.append(f"量比 {volume_ratio}（显著缩量）")
    if on_lhb:
        score += 8
        parts.append("龙虎榜上榜（有公开资金关注）")
    return round(max(0, min(100, score)), 1), "；".join(parts) if parts else "资金数据缺失，中性"


def synthesize(sub: dict[str, float], weights: dict[str, float] | None = None, vetoes: list[str] | None = None) -> tuple[float, list[str]]:
    """加权合成 + 一票否决。返回 (综合分, 否决说明列表)。

    否决不直接归零（保留可解释性），而是 ×0.4 重罚并显式记录——
    风控引擎「强空」状态、ST/退市风险警示等触发。
    """
    w = weights or WEIGHTS
    total = sum(w.get(k, 0) * v for k, v in sub.items())
    reasons = []
    for v in vetoes or []:
        total *= 0.4
        reasons.append(f"一票否决：{v}（综合分 ×0.4）")
    return round(max(0, min(100, total)), 1), reasons


def apply_replacement_threshold(
    prev_symbols: list[str],
    ranked: list[dict],
    threshold: float | None = None,
    max_picks: int = MAX_PICKS,
    max_swaps: int | None = _UNSET,
    min_score: float | None = None,
) -> tuple[list[dict], list[dict]]:
    """入选门槛 + 换股门槛 + 每日换股上限。返回 (新组合 ≤max_picks, 换股记录 [{out, in, delta}])。

    三道约束，各有分工（2026-09-10 补入选门槛）：

    - **入选门槛**（``min_score``，绝对质量线）：低于线的候选一律不进，
      **留任成员同样受约束**——门槛的语义是「在组合里就必须够格」，昨天在列不是
      豁免理由，否则「今日名单」会退化成「昨日名单的惯性延续」。名额因此**不兜底**：
      够格几只就是几只，不足 max_picks 就留空，绝不拿低分凑数
      （与盘中 `top_watch_stocks` 的「unknown/低不入选」同一套三态纪律）。
    - **换股门槛**（分数差）：防止小幅波动引发无谓换股
    - **每日换股上限**（数量）：这才是稳定性的真正保障。涨停股的梯队分
      （龙头 88 分）远高于非涨停成员（领涨 70/同步 52/滞涨 35），分差动辄
      30+，15 分门槛形同虚设——回放实测纯门槛策略日均换手仍达 60%。
      限制每日换入只数后，组合才会真正"精挑细选并保持一致性"。

    :param min_score: 入选门槛（综合分下限）。语义与标定见 MIN_PICK_SCORE。
    :param threshold / max_swaps / min_score: 三者默认取**运行时生效值**
        （控制台参数白名单 P1-15 的覆盖层优先，否则回落代码常量）。
        注意 `max_swaps=None` 仍是"**不限换股**"，与"未传参"用哨兵 `_UNSET` 区分。
    :param max_swaps: 每日最多换入几只（None = 不限）。首次建仓（prev 为空）不受限。
    """
    if threshold is None:
        threshold = _live("picks_replace_threshold", REPLACE_THRESHOLD)
    if min_score is None:
        min_score = _live("picks_min_pick_score", MIN_PICK_SCORE)
    if max_swaps is _UNSET:
        max_swaps = _live("picks_max_swaps_per_day", MAX_SWAPS_PER_DAY)
    by_symbol = {c["symbol"]: c for c in ranked}
    kept: list[dict] = []
    replaced: list[dict] = []
    for sym in prev_symbols:
        c = by_symbol.get(sym)
        if c is not None and c["score"] >= min_score:
            kept.append(c)
    kept = sorted(kept, key=lambda c: -c["score"])[:max_picks]

    capped = max_swaps if (max_swaps is not None and prev_symbols) else None
    added = 0
    for c in ranked:
        if c["score"] < min_score:
            # 不够格者既不补位也不换入（`ranked` 若已按分降序，后续必然也不够格）
            continue
        if capped is not None and added >= capped:
            break
        if any(c["symbol"] == k["symbol"] for k in kept):
            continue
        if len(kept) < max_picks:
            kept.append(c)
            added += 1
        else:
            # 已满：只与组合内最弱者比，超出 threshold 才换（组合稳定性的机制保证）
            weakest = min(kept, key=lambda k: k["score"])
            if c["score"] >= weakest["score"] + threshold:
                kept.remove(weakest)
                replaced.append(
                    {"out": weakest["symbol"], "in": c["symbol"], "delta": round(c["score"] - weakest["score"], 1)}
                )
                kept.append(c)
                added += 1
    kept = sorted(kept, key=lambda c: -c["score"])[:max_picks]
    return kept, replaced


def build_buy_range(price: float, support: float | None, resistance: float | None) -> dict:
    """买入范围（CONTEXT.md: Buy Range）：现价 ±3% 与技术位的交集提示。

    区间 = [max(现价×0.97, 支撑), min(现价×1.03, 压力)]；无技术位时退化为 ±3%。
    纯提示，不构成买卖建议。
    """
    lo = price * (1 - BUY_RANGE_PCT)
    hi = price * (1 + BUY_RANGE_PCT)
    if support is not None and support > 0:
        lo = max(lo, support)
    if resistance is not None and resistance > 0:
        hi = min(hi, resistance)
    if lo > hi:  # 技术位与现价区间无交集：回到纯 ±3%，basis 说明
        lo, hi = price * (1 - BUY_RANGE_PCT), price * (1 + BUY_RANGE_PCT)
    return {"low": round(lo, 2), "high": round(hi, 2), "basis": "现价 ±3%，参考支撑/压力位收敛；不构成买卖建议"}


def classify_review(excess_pct: float, note_hint: str | None = None) -> tuple[str, str]:
    """复盘归类：超额收益 + 走坏原因启发式（第一版规则，LLM 接入后增强）。

    :param excess_pct: 个股当日涨跌幅 − 上证涨跌幅（百分点）
    """
    if excess_pct >= 2:
        return "good", "超额为正且显著，走势健康"
    if excess_pct <= -2:
        # 无更细数据时默认归「入选逻辑失效」，具体归因由复盘角色结合事件/板块数据在 note 补充
        cat = "logic_failed"
        if note_hint:
            cat = note_hint if note_hint in REASON_CATEGORIES else "logic_failed"
        return "bad", REASON_CATEGORIES[cat]
    return "flat", "与大盘同步，无显著超额"


def review_entry_quality(
    *,
    buy_range: dict | None,
    day_open: float | None,
    day_high: float | None,
    day_low: float | None,
    day_close: float | None,
    observation_only: bool = False,
) -> dict:
    """买点质量：把「选错了」与「选对了但买点不对」分开。

    复盘最有价值的区分正是这一条——同一只票，按买入范围介入是赚的、追高介入
    是亏的，前者是执行问题，后者才是选股问题。混在一起统计会污染迭代方向。

    :param observation_only: 该标的是否为空仓闸门日的「仅观察」条目。闸门日
        本就不给买入范围，此时不适用买点评析——必须区分于"数据缺失导致不可评"。
    :return: {filled, entry_cost, entry_pnl_pct, open_pnl_pct, advantage_pct, basis}
             filled=None 表示不可评（无买入范围或行情缺失）
    """
    if not buy_range or None in (day_open, day_high, day_low, day_close):
        if observation_only:
            basis = "空仓闸门日：本就未给出买入范围（不建议出手），不适用买点评析"
        else:
            basis = "无买入范围或行情缺失 → 买点质量不可评"
        return {
            "filled": None,
            "entry_cost": None,
            "entry_pnl_pct": None,
            "open_pnl_pct": None,
            "advantage_pct": None,
            "basis": basis,
        }
    high_edge = float(buy_range["high"])
    if day_low > high_edge:
        return {
            "filled": False,
            "entry_cost": None,
            "entry_pnl_pct": None,
            "open_pnl_pct": round((day_close - day_open) / day_open * 100, 2) if day_open else None,
            "advantage_pct": None,
            "basis": f"全天最低 {day_low} 高于买入区间上沿 {high_edge} → 按纪律未介入（踏空）",
        }
    # 可介入：开盘在区间内按开盘价，否则按上沿（保守成本）
    cost = day_open if day_open <= high_edge else high_edge
    entry_pnl = round((day_close - cost) / cost * 100, 2) if cost else None
    open_pnl = round((day_close - day_open) / day_open * 100, 2) if day_open else None
    return {
        "filled": True,
        "entry_cost": round(cost, 2),
        "entry_pnl_pct": entry_pnl,
        "open_pnl_pct": open_pnl,
        "advantage_pct": round(entry_pnl - open_pnl, 2) if (entry_pnl is not None and open_pnl is not None) else None,
        "basis": f"按买入范围介入成本 {cost}（区间上沿 {high_edge}），收益 {entry_pnl}%；"
        f"开盘追入收益 {open_pnl}%",
    }


def classify_failure(
    *,
    excess_pct: float | None,
    entry: dict,
    market_phase: str | None = None,
) -> tuple[str, str]:
    """走坏原因归类（第一版规则；LLM 接入后按维度增强，接口不变）。

    判定优先级：踏空 → 买点不对 → 情绪误判 → 逻辑失效。
    区分依据是**可观测的事实**，不是猜测：
    - 买点不对：盘中冲高超过买入区间上沿 3% 以上，收盘却回落至区间下方（典型追高即套）
    - 情绪误判：持有期市场相位处于退潮/冰点（个股再强也难逆势）
    - 基准缺失（excess_pct=None，2026-09-01 评审 B21）：归因挂起记 data_issue，
      绝不把个股涨幅冒充超额（否则归因统计被系统性污染）
    """
    if entry.get("filled") is False:
        return "missed", f"{entry['basis']}；非选股失误，属踏空"
    if excess_pct is None:
        return "data_issue", "市场基准缺失，超额收益无法计算——归因挂起（诚实降级，不猜测）"
    if excess_pct <= -2:
        if entry.get("filled") and (entry.get("advantage_pct") or 0) > 1.5:
            return (
                "entry_bad",
                f"按买入范围介入优于追高 {entry['advantage_pct']}pct —— 属买点执行问题，非选股逻辑失效",
            )
        if market_phase in ("退潮", "冰点"):
            return (
                "sentiment_misread",
                f"持有期市场相位「{market_phase}」，系统性下行压过个股逻辑 —— 属情绪误判",
            )
        return "logic_failed", "超额显著为负且无踏空/买点/情绪解释，视为入选逻辑失效"
    if excess_pct >= 2:
        return "gone_well", "超额为正且显著，走势健康"
    return "gone_well", "与大盘同步，无显著超额"
