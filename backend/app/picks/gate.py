"""空仓闸门（CONTEXT.md: Stand-aside Gate）—— 情绪转弱时的主动风控。

为什么需要它：选股系统的默认行为是"每天都要选出 5 只"，这在情绪退潮期是
灾难——没有赚钱效应的市场里，任何精选都是硬凑，硬凑的结果是大面。所以必须
有一个能**主动说"今天不该出手"**的机制，这比多选出一只好票更有价值。

设计原则（红线 3）：闸门输出的是**风险提示与规避建议**，不是"清仓指令"；
触发时组合记录仍然生成（保留复盘归因能力），但标注「仅观察」且不给出买入范围。

触发条件为多条件 OR，任一命中即预警；命中数决定预警级别。所有阈值集中在此，
便于周末复盘后人工微调（调整须走人工确认，不自动漂移）。
"""

from __future__ import annotations

#: 情绪相位直接触发（这两个相位下赚钱效应最差）
WEAK_PHASES = ("退潮", "冰点")
#: 该相位直接升级为强预警（市场几乎无机会）
SEVERE_PHASES = ("冰点",)

#: 涨停晋级率下限：首板→二板的成功率低于此值，说明接力没人接
PROMO_FLOOR = 0.30
#: 炸板率上限：封板不牢，打板即被埋
BREAK_RATE_CEIL = 0.35
#: 跌停家数上限：系统性风险信号
LIMIT_DOWN_CEIL = 15
#: 昨日涨停股今日中位溢价下限（百分点）：接力亏钱效应
PREV_ZT_MEDIAN_FLOOR = -1.0

ADVICE_STRONG = "市场情绪明显转弱，建议空仓观望，切忌盲目出手"
ADVICE_MILD = "市场情绪偏弱，建议控制仓位、减少出手频率"
ADVICE_NONE = "市场情绪未见系统性风险，按组合纪律执行即可"

DISCLAIMER = "风险提示为规则化判读结果，不构成买卖建议"


def evaluate_stand_aside(
    *,
    phase: str | None = None,
    promotion_1to2: float | None = None,
    break_rate: float | None = None,
    limit_down: int | None = None,
    prev_zt_median_pct: float | None = None,
    phase_unreliable: bool = False,
) -> dict:
    """评估是否触发空仓闸门。

    :param phase: 情绪相位（冰点/修复/发酵/高潮/分歧/退潮）
    :param promotion_1to2: 首板→二板晋级率（0~1）
    :param break_rate: 炸板率（0~1）
    :param limit_down: 跌停家数
    :param prev_zt_median_pct: 昨日涨停股今日中位溢价（百分点）
    :param phase_unreliable: 情绪判定是否自指不可信（不可信时降级为提示而非结论）
    :return: {stand_aside, level, reasons, advice}
    """
    reasons: list[str] = []

    if phase in WEAK_PHASES:
        reasons.append(f"情绪相位「{phase}」——赚钱效应处于周期低位")
    if promotion_1to2 is not None and promotion_1to2 < PROMO_FLOOR:
        reasons.append(f"首板晋级率 {promotion_1to2:.0%} < {PROMO_FLOOR:.0%}（接力无人接）")
    if break_rate is not None and break_rate >= BREAK_RATE_CEIL:
        reasons.append(f"炸板率 {break_rate:.0%} ≥ {BREAK_RATE_CEIL:.0%}（封板不牢）")
    if limit_down is not None and limit_down >= LIMIT_DOWN_CEIL:
        reasons.append(f"跌停 {limit_down} 家 ≥ {LIMIT_DOWN_CEIL} 家（系统性风险）")
    if prev_zt_median_pct is not None and prev_zt_median_pct < PREV_ZT_MEDIAN_FLOOR:
        reasons.append(
            f"昨日涨停股今日中位溢价 {prev_zt_median_pct:.2f}% < {PREV_ZT_MEDIAN_FLOOR:.2f}%（接力亏钱）"
        )

    if phase_unreliable and reasons:
        # 判定本身不可信时不升级为强预警，但把不确定性明示出来（诚实原则）
        reasons.append("⚠️ 情绪判定自指检查未通过，上述结论置信度下调")

    if not reasons:
        return {"stand_aside": False, "level": "none", "reasons": [], "advice": ADVICE_NONE}

    severe = (phase in SEVERE_PHASES) or len(reasons) >= 2
    level = "strong" if severe else "mild"
    return {
        "stand_aside": True,
        "level": level,
        "reasons": reasons,
        "advice": ADVICE_STRONG if severe else ADVICE_MILD,
        "disclaimer": DISCLAIMER,
    }


def apply_gate_to_picks(picks: list[dict], gate: dict) -> list[dict]:
    """闸门触发时对组合的处理：三态分层 + 撤掉买入范围（记录仍保留）。

    三态（审查报告 §4.2「堵疏结合」：observation_only 单档 → 分层）：
    - **blocked 禁买**：命中一票否决（红线）或异动风险扣分——连想都不要想；
    - **followable 可跟**：满足龙头判据（连板高度 ≥2 + 梯队地位 + 题材催化 +
      无红线）的标的——从「仅观察」升级为「可跟」tag。纪律不破：仍不给买入
      范围、observation_only 保持 True（影子/复盘等下游消费语义不变）；参与
      须经影子持仓先验证（picks-intraday-fusion-assessment §3.3 原则）；
    - **observe 仅观察**：其余标的（闸门日默认档，与历史行为一致）。

    可跟判据的取舍（对照审查报告「连板高度 + 梯队 role + 题材催化」）：
    - 连板高度 boards ≥2：首板在退潮期被埋概率最高，不够格；
    - 梯队地位 ∈ {空间板, 龙头, 反包, 中军, 领涨}：跟风/补涨/滞涨/断板不配；
    - 题材催化 theme 非空：无题材归属的逆势票不在「可跟」语义内；
    - 三个条件同时满足（AND），且无红线/异动扣分。
    旧数据兼容：字段缺失（boards=None/role 缺）按不满足判据处理 → observe。
    """
    if not gate.get("stand_aside"):
        return picks
    out = []
    for p in picks:
        item = dict(p)
        item["observation_only"] = True
        item.pop("buy_range", None)
        state, reasons = _follow_state_of(item)
        item["follow_state"] = state
        item["follow_reasons"] = reasons
        out.append(item)
    return out


#: 可跟档的梯队地位白名单（ROLE_BASE_SCORE 高段：有真实天梯地位的角色）
FOLLOW_ROLES = {"空间板", "龙头", "反包", "中军", "领涨"}
#: 可跟档的连板高度下限（首板不够格：闸门日追首板是典型的接飞刀）
FOLLOW_MIN_BOARDS = 2

_STATE_LABELS = {"blocked": "禁买", "observe": "仅观察", "followable": "可跟"}


def _follow_state_of(item: dict) -> tuple[str, list[str]]:
    """单票三态判定（纯函数）：红线一票否决 > 可跟判据 > 默认观察。"""
    reasons: list[str] = []
    vetoes = item.get("vetoes") or []
    halt_penalty = (item.get("halt_risk") or {}).get("penalty") or 0.0
    if vetoes:
        reasons.append(f"命中 {len(vetoes)} 条一票否决（红线压制，禁买）")
        return "blocked", reasons
    if halt_penalty:
        reasons.append(f"异动风险扣分 {halt_penalty}（禁买）")
        return "blocked", reasons

    boards = item.get("boards")
    role = item.get("echelon_role") or ""
    theme = item.get("theme")
    if boards is None:
        # 连板高度缺失（非涨停/旧数据）≠ 满足判据：缺失不升级（诚实降级）
        reasons.append("连板高度缺失（非涨停或旧数据），不满足可跟判据")
    elif boards < FOLLOW_MIN_BOARDS:
        reasons.append(f"连板高度 {boards} < {FOLLOW_MIN_BOARDS}")
    if role not in FOLLOW_ROLES:
        reasons.append(f"梯队角色「{role or '缺失'}」不在可跟白名单")
    if not theme:
        reasons.append("无题材归属（题材催化缺失）")
    if not reasons:
        reasons.append(
            f"龙头判据全满足：{boards} 板 + 角色「{role}」+ 题材「{theme}」；"
            "可跟 ≠ 可买：不给买入范围，参与须经影子持仓先验证"
        )
        return "followable", reasons
    reasons.append("龙头判据未全满足，保持仅观察")
    return "observe", reasons


def follow_state_label(state: str | None) -> str:
    """三态 → 中文标签（None/未知 → 空串，调用方按无闸门处理）。"""
    return _STATE_LABELS.get(state or "", "")
