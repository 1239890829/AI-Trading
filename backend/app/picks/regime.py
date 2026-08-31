"""炒作阶段（CONTEXT.md: Speculation Regime）—— 五维权重的时序选择器。

为什么需要它：A 股的炒作主导逻辑随财报披露节奏切换。业绩披露密集期，市场
买的是"业绩超预期"——基本面与真金白银的财务数据主导；披露完成后的空窗期，
没有新增业绩信息可交易，资金转向炒情绪、炒题材、炒传闻与超预期想象空间，
此时若还按基本面权重打分，会系统性地错过妖股与龙头（它们常常基本面一塌糊涂）。

所以 Regime 不是第七个评分维度，而是**权重选择器**——同一个候选，在不同
Regime 下会得到不同的综合分，这才是"适时切换判断逻辑"。

判定 = 财报日历（主） + 真实业绩事件密度（校验）：
- 日历给出先验：A 股披露窗口是硬日历（可解释、可复现）
- 密度做校正：防止日历与当年实际情况脱节（如延后披露、预告提前密集）
"""

from __future__ import annotations

from datetime import date

#: A 股定期报告披露窗口（月）。依据是《上市公司信息披露管理办法》的法定时限：
#: 年报 4/30 前、一季报 4/30 前、半年报 8/31 前、三季报 10/31 前，
#: 加上业绩预告/快报在披露前的提前释放，故窗口起始月早于截止月。
EARNINGS_WINDOW_MONTHS = {
    1,  # 年报业绩预告密集期
    2,  # 年报预告 + 快报
    3,  # 年报正式披露启动
    4,  # 年报 + 一季报双密集（法定截止 4/30）
    7,  # 半年报预告密集期
    8,  # 半年报正式披露（法定截止 8/31）
    10,  # 三季报（法定截止 10/31）
}

#: 业绩类事件的识别关键词（用于密度校验）。命中即视为"业绩驱动"证据。
EARNINGS_KEYWORDS = (
    "业绩",
    "预增",
    "预减",
    "预盈",
    "预亏",
    "净利",
    "营收",
    "营业收入",
    "年报",
    "季报",
    "中报",
    "半年报",
    "快报",
    "扭亏",
    "分红",
    "送转",
)

#: 密度校验阈值：活跃事件中业绩类占比 ≥ 此值才认定"实际处于业绩驱动期"
EARNINGS_RATIO_HIGH = 0.30
#: 密度校验阈值：占比 ≤ 此值才认定"实际处于空窗期"
EARNINGS_RATIO_LOW = 0.12

REGIME_EARNINGS = "业绩驱动期"
REGIME_SPECULATIVE = "业绩空窗期"

#: 六维权重表（含梯队维度，六项之和恒为 1.0）
WEIGHTS_BY_REGIME: dict[str, dict[str, float]] = {
    # 业绩驱动期：基本面主导，情绪让位（买的是真业绩与超预期）
    REGIME_EARNINGS: {
        "sentiment": 0.10,
        "news": 0.22,
        "tech": 0.18,
        "fundamental": 0.25,
        "capital": 0.10,
        "echelon": 0.15,
    },
    # 业绩空窗期：情绪与题材梯队主导，基本面近乎让位（买的是想象空间与接力）
    REGIME_SPECULATIVE: {
        "sentiment": 0.25,
        "news": 0.20,
        "tech": 0.18,
        "fundamental": 0.05,
        "capital": 0.12,
        "echelon": 0.20,
    },
}

BALANCED_WEIGHTS = {
    "sentiment": 0.18,
    "news": 0.22,
    "tech": 0.20,
    "fundamental": 0.15,
    "capital": 0.12,
    "echelon": 0.13,
}


def is_earnings_keyword(text: str | None) -> bool:
    """文本是否属业绩类（事件标题/类型的关键词命中）。"""
    if not text:
        return False
    return any(k in text for k in EARNINGS_KEYWORDS)


def earnings_event_ratio(event_texts: list[str]) -> float:
    """活跃事件中业绩类事件的占比（0~1）。空列表返回 0（无证据不臆断）。"""
    if not event_texts:
        return 0.0
    hits = sum(1 for t in event_texts if is_earnings_keyword(t))
    return hits / len(event_texts)


def detect_regime(
    *,
    today: date,
    earnings_ratio: float | None = None,
    event_count: int = 0,
) -> dict:
    """判定炒作阶段并给出对应权重。

    :param today: 交易日
    :param earnings_ratio: 活跃事件中业绩类占比（None = 无事件数据，只靠日历）
    :param event_count: 活跃事件总数（样本太小时不做密度否决）
    :return: {regime, weights, basis, calendar_window, earnings_ratio}
    """
    calendar_window = today.month in EARNINGS_WINDOW_MONTHS
    basis: list[str] = []

    if calendar_window:
        basis.append(f"{today.month} 月处定期报告披露窗口（法定截止：年报/一季报 4·30、半年报 8·31、三季报 10·31）")
    else:
        basis.append(f"{today.month} 月处业绩披露空窗期（上一轮披露已结束）")

    # 密度校验：只在样本足够时启用，避免 2 条事件就翻转结论
    ratio_usable = earnings_ratio is not None and event_count >= 5
    if ratio_usable and earnings_ratio is not None:
        basis.append(f"活跃事件中业绩类占比 {earnings_ratio:.0%}（样本 {event_count} 条）")
        if calendar_window and earnings_ratio <= EARNINGS_RATIO_LOW:
            basis.append(f"占比 ≤{EARNINGS_RATIO_LOW:.0%}，日历虽在窗口但实际无业绩驱动证据 → 按空窗期处理")
            regime = REGIME_SPECULATIVE
        elif not calendar_window and earnings_ratio >= EARNINGS_RATIO_HIGH:
            basis.append(f"占比 ≥{EARNINGS_RATIO_HIGH:.0%}，空窗期出现业绩事件密集 → 按业绩驱动期处理")
            regime = REGIME_EARNINGS
        else:
            regime = REGIME_EARNINGS if calendar_window else REGIME_SPECULATIVE
    else:
        if earnings_ratio is None:
            basis.append("无事件样本，密度校验跳过（仅按日历判定）")
        else:
            basis.append(f"事件样本仅 {event_count} 条（<5），密度校验不足以推翻日历")
        regime = REGIME_EARNINGS if calendar_window else REGIME_SPECULATIVE

    return {
        "regime": regime,
        "weights": dict(WEIGHTS_BY_REGIME[regime]),
        "basis": "；".join(basis),
        "calendar_window": calendar_window,
        "earnings_ratio": earnings_ratio,
    }


def weights_for(regime: str | None) -> dict[str, float]:
    """按 regime 取权重；未知/缺失时退回平衡权重（绝不静默用错表）。"""
    return dict(WEIGHTS_BY_REGIME.get(regime or "", BALANCED_WEIGHTS))
