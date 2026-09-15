"""成建制分级与题材强弱（纯函数：成建制门槛 / 打折系数 / 强弱分级 / 强度评分）。

`backend/app/services/theme_service.py` 的内部切片（IMP-005 批 4，2026-09-15 从 1383 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与前置注释与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app.services.theme_service` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 成建制门槛

#: 题材「成建制」分级。看板的核心价值是找**成梯队**的题材，
#: 单只票的题材标签不是题材（实测 8/28：211 个标签里有 47+ 个只对应 1 只涨停股，
#: 若不分级，这些伪题材会凭「最高板数」冲到排序前面，把真梯队挤下去）。
FORMATION_LEVELS = [
    (5, "成建制"),   # >= 5 家涨停
    (3, "初步成形"),  # 3~4 家
    (2, "零散"),     # 2 家
    (0, "个股行情"),  # 1 家：只是该股的题材标签，不构成题材
]


def formation_level(limit_up_count: int) -> str:
    """按涨停家数判定题材成建制程度。"""
    for threshold, label in FORMATION_LEVELS:
        if limit_up_count >= threshold:
            return label
    return FORMATION_LEVELS[-1][1]


#: 未成建制的题材在排序时被打折的系数。家数越少、折扣越狠，
#: 保证「1 只票的 7 板标签」不会压过「6 只票的 3 板真梯队」。
FORMATION_DAMPING = {
    "成建制": 1.0,
    "初步成形": 0.85,
    "零散": 0.65,
    "个股行情": 0.45,
}


# ---------------------------------------------------------------- 纯函数：强弱分级


def strength_tier(
    *,
    formation: str,
    stage: str,
    max_boards: int,
    reopen_rate: float,
    premium_median: float | None,
) -> tuple[str, str]:
    """题材强弱分级：回答「今天最强、最确定、最有参与机会的是谁」。

    与 strength_score 的分工：score 是连续量（排序用），tier 是离散档位
    （视觉标识用）。tier 必须规则化可解释，每档附带判定依据。

    判定顺序：领涨 → 强势 → 活跃 → 观察。赚钱效应是一票否决项——
    接力亏钱（溢价为负）的题材无论家数多都不能进「领涨/强势」
    （与 judge_theme_stage 的分歧优先原则同源，防止热度压过赚钱效应）。
    """
    losing = premium_median is not None and premium_median < 0
    firm_seal = reopen_rate < 0.3

    if formation == "成建制" and stage in ("高潮", "发酵") and firm_seal and not losing:
        return "领涨", f"成建制+{stage}+封板牢（开板率 {reopen_rate:.0%}）"
    if stage in ("高潮", "发酵") and not losing:
        return "强势", f"{stage}期，赚钱效应仍在"
    if formation == "成建制" and stage == "分歧" and max_boards >= 3 and not losing:
        return "强势", "成建制题材高位分歧，高度未塌"
    if max_boards >= 2 and stage != "退潮":
        tier = "活跃"
        if losing:
            return tier, f"最高 {max_boards} 板，但接力溢价为负，只看不动"
        return tier, f"最高 {max_boards} 板，梯队初成"
    if losing:
        return "观察", f"接力溢价 {premium_median:.2f}%，接力亏钱"
    return "观察", f"{formation}·{stage}，暂无梯队结构"


def theme_strength_score(
    *,
    limit_up_count: int,
    max_boards: int,
    completeness: float,
    reopen_rate: float,
    seal_quality: float,
    active_days: int,
) -> float:
    """题材综合强度分（用于横向排序）。各项权重可解释。

    最后乘**成建制折扣**：家数权重本身有上限（10 家封顶 = 100 分），
    单只票可以靠「最高 7 板」拿到 56 分，足以压过 3~6 只票的真梯队。
    折扣让排序回到「先成建制、再看高度」的直觉上。
    """
    raw = (
        min(limit_up_count, 10) * 10              # 家数：成建制程度
        + min(max_boards, 8) * 8                  # 高度：空间
        + completeness * 15                       # 梯队完整度
        + (1 - min(reopen_rate, 1.0)) * 10        # 封板牢固程度
        + seal_quality * 8                        # 封板时间质量
        + min(active_days, 5) * 5                 # 连续活跃天数（资金持续性）
    )
    return round(raw * FORMATION_DAMPING[formation_level(limit_up_count)], 1)
