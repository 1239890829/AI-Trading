"""梯队地位与题材天梯（CONTEXT.md: Echelon Role）—— 选股联合研判的个股级证据。

为什么单独成模块：五维评分（情绪/消息/技术/基本面/资金）都是**个股自身**的
属性，无法回答"它在题材里排第几"。而 A 股打板/接力生态里，同一只票在不同
题材阶段的价值完全不同——4 板龙头在发酵期是核心，在退潮期是最后一棒。
所以梯队地位是**独立于五维的第六个证据维度**，且必须与题材天梯阶段联合读取。

判定口径沿用题材看板的 `classify_role`（同一套语言，避免两套术语打架）：
- 涨停股：直接由 `classify_role` 判定（需要连板数/封板时间/炸板数/市值）
- 非涨停股（趋势股、情绪股）：题材看板没有它的位置——用**相对题材基准的超额**
  推导类梯队地位（领涨/同步/滞涨），大市值且中上的归为中军

参考仓库择优（docs/github-stars-trading-analysis.md）：
TradingAgents 的多角色分工思想在此落地为"个股角色 × 题材阶段"的联合判读，
而非单看个股指标；其"买卖点位"结论风格一律不引（红线 3）。
"""

from __future__ import annotations

from app.services.theme_service import (
    MIDDLE_WEIGHT_MIN_CAP,
    classify_role,
    echelon_completeness,
    judge_theme_stage,
    seal_phase,
)

#: 角色基础分（0-100）：个股在题材天梯中的地位，与题材阶段无关的部分
ROLE_BASE_SCORE: dict[str, float] = {
    "空间板": 95.0,  # 全市场最高板，情绪标杆
    "龙头": 88.0,  # 题材内连板最高
    "反包": 78.0,  # 断板后重新封回，资金二次进场
    "中军": 72.0,  # 大容量权重票，定题材深度
    "领涨": 70.0,  # 非涨停：显著跑赢所属题材基准
    "补涨": 65.0,  # 龙头打出空间后的低位替代
    "首板": 58.0,  # 当日首次涨停
    "同步": 52.0,  # 非涨停：与题材基准同步
    "跟风": 45.0,  # 封板晚或开过板
    "滞涨": 35.0,  # 非涨停：跑输题材基准
    "断板": 20.0,  # 连板中断
}

#: 题材阶段 → 地位分调节系数。联合研判的核心：个股再强，题材退潮也要打折。
STAGE_ADJUST: dict[str, float] = {
    "启动": 1.05,  # 刚起势，先手溢价
    "发酵": 1.10,  # 最健康的加速段
    "高潮": 0.95,  # 高度已高，接力风险大于收益 → 不因家数多而加分
    "分歧": 0.75,  # 有热度但赚钱效应走坏
    "退潮": 0.55,  # 高度塌陷/涨停骤减，龙头亦难独善
}

#: 非涨停股相对题材基准的超额分档（百分点）
LEAD_EXCESS = 3.0  # ≥ +3% 视为领涨
LAG_EXCESS = -2.0  # ≤ -2% 视为滞涨

#: 梯队完整度对地位分的影响区间（缺承接地爆发的题材，龙头也难持续）
COMPLETENESS_MIN_FACTOR = 0.85


def classify_non_limit_up_role(
    *,
    excess_pct: float | None,
    float_market_cap: float | None = None,
) -> tuple[str, str]:
    """非涨停股的类梯队地位：相对所属题材基准的超额。

    :param excess_pct: 个股当日涨跌幅 − 所属题材基准涨跌幅（百分点）
    :param float_market_cap: 流通市值（元），用于识别中军
    """
    if excess_pct is None:
        return "同步", "无题材基准可比 → 归为同步（不臆造地位）"
    if excess_pct >= LEAD_EXCESS:
        if float_market_cap and float_market_cap >= MIDDLE_WEIGHT_MIN_CAP:
            return "中军", f"跑赢题材基准 {excess_pct:+.2f}pct 且流通市值达中军门槛"
        return "领涨", f"跑赢题材基准 {excess_pct:+.2f}pct"
    if excess_pct <= LAG_EXCESS:
        return "滞涨", f"跑输题材基准 {excess_pct:+.2f}pct"
    if float_market_cap and float_market_cap >= MIDDLE_WEIGHT_MIN_CAP:
        return "中军", f"与题材基准同步（{excess_pct:+.2f}pct）且流通市值达中军门槛"
    return "同步", f"与题材基准同步（{excess_pct:+.2f}pct）"


def classify_echelon_role(
    *,
    is_limit_up: bool,
    consecutive_boards: int | None = None,
    theme_max_boards: int = 0,
    market_max_boards: int = 0,
    prev_boards: int | None = None,
    float_market_cap: float | None = None,
    first_seal_time: str | None = None,
    break_count: int | None = None,
    excess_pct: float | None = None,
) -> tuple[str, str]:
    """个股梯队地位（涨停股走精确判定，非涨停股走超额近似）。

    :return: (role, basis)
    """
    if is_limit_up:
        role = classify_role(
            boards=consecutive_boards or 1,
            theme_max_boards=theme_max_boards,
            market_max_boards=market_max_boards,
            prev_boards=prev_boards,
            float_market_cap=float_market_cap,
            seal_phase_value=seal_phase(first_seal_time),
            break_count=break_count,
        )
        detail = []
        if consecutive_boards:
            detail.append(f"{consecutive_boards} 板")
        if theme_max_boards:
            detail.append(f"题材最高 {theme_max_boards} 板")
        if break_count:
            detail.append(f"炸板 {break_count} 次")
        basis = f"涨停池精确判定（{'、'.join(detail) or '首板'}）"
        return role, basis
    role, basis = classify_non_limit_up_role(
        excess_pct=excess_pct, float_market_cap=float_market_cap
    )
    return role, f"非涨停：{basis}"


def theme_ladder_health(
    *,
    limit_up_count: int,
    max_boards: int,
    prev_limit_up_count: int | None = None,
    prev_max_boards: int | None = None,
    reopen_rate: float = 0.0,
    levels: dict[int, int] | None = None,
    premium_median: float | None = None,
) -> dict:
    """题材天梯健康度（题材级证据，个股地位的读取上下文）。

    :param levels: {连板数: 家数}，用于梯队完整度（检测断层）
    :return: {stage, stage_basis, completeness, adjust}
    """
    completeness = echelon_completeness(levels or {}, max_boards)
    stage, stage_basis = judge_theme_stage(
        limit_up_count=limit_up_count,
        max_boards=max_boards,
        prev_limit_up_count=prev_limit_up_count,
        prev_max_boards=prev_max_boards,
        reopen_rate=reopen_rate,
        completeness=completeness,
        premium_median=premium_median,
    )
    return {
        "stage": stage,
        "stage_basis": stage_basis,
        "completeness": completeness,
        "adjust": STAGE_ADJUST.get(stage, 1.0),
    }


def score_echelon(
    *,
    role: str,
    stage: str | None = None,
    completeness: float | None = None,
) -> tuple[float, str]:
    """梯队地位评分（0-100）= 角色基础分 × 题材阶段系数 × 梯队完整度系数。

    三者缺一不可：只看角色会选出退潮期的最后一棒，只看阶段会把滞涨票当选龙头。
    """
    base = ROLE_BASE_SCORE.get(role, 50.0)
    adjust = STAGE_ADJUST.get(stage or "", 1.0)
    factor = COMPLETENESS_MIN_FACTOR + (1 - COMPLETENESS_MIN_FACTOR) * (
        completeness if completeness is not None else 0.5
    )
    score = base * adjust * factor
    parts = [f"角色「{role}」基础 {base:.0f} 分"]
    if stage:
        parts.append(f"题材阶段「{stage}」× {adjust}")
    if completeness is not None:
        parts.append(f"梯队完整度 {completeness:.0%} × {factor:.2f}")
    return round(max(0.0, min(100.0, score)), 1), "；".join(parts)
