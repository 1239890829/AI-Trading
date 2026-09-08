"""meta 置信层（规则版）（strategy-evolution-plan 方向 3 P1）。

把「综合分 + 市场相位 + 筹码健康 + 红线状态」折叠成一个统一置信语言——
**观察 / 可执行 / 强执行** 三档，替代执行闸门的二值跳变（闸门保留为兜底：
空仓闸门 stand_aside 触发时仍无条件压成仅观察）。

设计取自 López de Prado meta-labeling 的规则版近似：第一层（六维评分引擎）
给方向，第二层（本模块）用环境特征回答「这个信号这次该信多少」。
数据版（≥200 命中样本后树模型学 Pr(正确|环境特征)）为 P2。

降档规则（保守优先，全部显式留痕到 reasons）：
- 红线 veto 或异动风险 penalty > 0 → 最高观察；
- 相位冰点/退潮 → 最高观察（环境不利，情绪策略不出手）；
- 筹码派发警示 → 最高观察（成本结构恶化的量化证据）；
- 六维缺维 → 最高可执行（数据不全不给强执行）；
- score ≥ 75 且全维且无红线且相位 ∈ {修复,发酵,高潮} 且无派发警示 → 强执行；
- score ≥ 60 且无红线 → 可执行；其余 → 观察。
"""

from __future__ import annotations

STRONG_PHASES = {"修复", "发酵", "高潮"}
ADVERSE_PHASES = {"冰点", "退潮"}
STRONG_SCORE = 75.0
EXECUTABLE_SCORE = 60.0
N_DIMS = 6

_LABELS = {"strong": "强执行", "executable": "可执行", "observe": "观察"}


def classify_confidence(
    *,
    score: float | None,
    sub_scores: dict | None,
    phase: str | None,
    chip_signal: str | None = None,
    halt_penalty: float | None = None,
    veto_count: int = 0,
    style_note: str | None = None,
) -> dict:
    """六维合成后的环境特征 → 三档置信（纯函数，供 _score_one 与测试直接调用）。

    :param phase: 市场情绪相位（None 时不因相位降档，但也不满足强执行窗——
                  相位缺失不给强执行，诚实降级）。
    :param style_note: 相位→风格路由的留痕短句（style_router.style_note），
                       挂相位维度扩展（审查 §4.1）；仅追加到 reasons，不影响档位。
    """
    reasons: list[str] = []
    cap = "strong"  # 当前允许的最高档

    if veto_count:
        cap = "observe"
        reasons.append(f"存在 {veto_count} 条一票否决（红线压制）")
    if halt_penalty:
        cap = "observe"
        reasons.append(f"异动风险扣分 {halt_penalty}")
    if phase in ADVERSE_PHASES:
        cap = "observe"
        reasons.append(f"市场相位「{phase}」不利于短线情绪策略")
    if chip_signal == "distribution_warning":
        cap = "observe"
        reasons.append("筹码形态呈派发警示（高位密集+放量滞涨+高获利盘）")
    if len(sub_scores or {}) < N_DIMS:
        if cap == "strong":
            cap = "executable"
        reasons.append(f"评分维度不全（{len(sub_scores or {})}/{N_DIMS}），不给强执行")

    tier = cap
    if tier == "strong":
        if (score or 0.0) < STRONG_SCORE:
            tier = "executable"
            reasons.append(f"综合分 {score} < {STRONG_SCORE}（强执行线）")
        elif phase not in STRONG_PHASES:
            tier = "executable"
            reasons.append(f"相位「{phase if phase else '缺失'}」不在强执行窗（需 修复/发酵/高潮）")
    if tier == "executable" and (score or 0.0) < EXECUTABLE_SCORE:
        tier = "observe"
        reasons.append(f"综合分 {score} < {EXECUTABLE_SCORE}（可执行线）")

    if style_note:
        reasons.append(style_note)

    if not reasons:
        reasons.append("综合分、相位、筹码、红线检查全部通过")
    return {"tier": tier, "label": _LABELS[tier], "reasons": reasons}
