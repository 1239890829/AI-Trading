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

# S2-7：相位集合唯一权威在 sentiment 引擎。保留本地名（它们各自承载不同语义，
# 见下），但取值必须来自同一处——此前两个文件各写一份字面量，任一处漏相位就是
# 「闸门撤区间、仓位引擎照给分」的交易信号级不一致。
from app.sentiment.engine import ADVERSE_PHASES as _ADVERSE
from app.sentiment.engine import SEVERE_PHASES as _SEVERE

#: 情绪相位直接触发（这两个相位下赚钱效应最差）
WEAK_PHASES = _ADVERSE
#: 该相位直接升级为强预警（市场几乎无机会）
SEVERE_PHASES = _SEVERE

#: 涨停晋级率下限：首板→二板的成功率低于此值，说明接力没人接。
#: ⚠️ **仅在历史分位不可用时兜底**（见 PROMO_PCTL_FLOOR）。
PROMO_FLOOR = 0.30
#: 炸板率上限：封板不牢，打板即被埋。⚠️ 同上，分位不可用时才用。
BREAK_RATE_CEIL = 0.35

#: **分位口径的异常线（2026-09-10 P1-31 引入，优先于上面的绝对经验值）**。
#: 为什么必须分位：绝对经验值对不上本项目的实际分布（与 `sentiment/calibration.py`
#: 同一教训）——实测近 **241 个交易日**的 `promo_1to2`：中位仅 14.3%、p80 仅 19.4%、
#: p90 仅 22.9%，而 `PROMO_FLOOR=0.30` 被写成"低于 30% 就算接力无人接" ⇒
#: **近似恒真**（近 9 个交易日 8 天命中），规则退化成每轮必加的那 1 条理由，
#: 还把 KB-DEC-014 的「≥2 条理由 → 撤区间」档位判断带偏。
#: 分位口径下"异常"才真的是异常：晋级率落在历史后 10% / 炸板率落在历史前 10%。
PROMO_PCTL_FLOOR = 10.0
BREAK_PCTL_CEIL = 90.0

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
    promotion_1to2_pctl: float | None = None,
    break_rate: float | None = None,
    break_rate_pctl: float | None = None,
    limit_down: int | None = None,
    prev_zt_median_pct: float | None = None,
    phase_unreliable: bool = False,
) -> dict:
    """评估是否触发空仓闸门。

    :param phase: 情绪相位（冰点/修复/发酵/高潮/分歧/退潮）
    :param promotion_1to2: 首板→二板晋级率（0~1）
    :param promotion_1to2_pctl: 该晋级率在历史样本中的分位（0–100）。给了就按分位判，
        没给才回落到 `PROMO_FLOOR` 绝对经验值（并在理由里写明用的是哪个口径）
    :param break_rate: 炸板率（0~1）
    :param break_rate_pctl: 该炸板率的历史分位（0–100），同上
    :param limit_down: 跌停家数
    :param prev_zt_median_pct: 昨日涨停股今日中位溢价（百分点）
    :param phase_unreliable: 情绪判定是否自指不可信（不可信时降级为提示而非结论）
    :return: {stand_aside, level, reasons, advice, phase, strip_buy_range, signals}
    """
    reasons: list[str] = []

    if phase in WEAK_PHASES:
        reasons.append(f"情绪相位「{phase}」——赚钱效应处于周期低位")
    # 晋级率：分位口径优先（"异常弱"= 历史后 10%），分位不可用才回落绝对经验值
    if promotion_1to2 is not None:
        if promotion_1to2_pctl is not None:
            if promotion_1to2_pctl < PROMO_PCTL_FLOOR:
                reasons.append(
                    f"首板晋级率 {promotion_1to2:.0%} 处于历史 {promotion_1to2_pctl:.0f} 分位"
                    f"（< {PROMO_PCTL_FLOOR:.0f} 分位线）——接力异常弱"
                )
        elif promotion_1to2 < PROMO_FLOOR:
            reasons.append(
                f"首板晋级率 {promotion_1to2:.0%} < {PROMO_FLOOR:.0%}（接力无人接；"
                "历史分位不可用，按业界经验值判）"
            )
    # 炸板率：分位口径优先（"异常高"= 历史前 10%）
    if break_rate is not None:
        if break_rate_pctl is not None:
            if break_rate_pctl > BREAK_PCTL_CEIL:
                reasons.append(
                    f"炸板率 {break_rate:.0%} 处于历史 {break_rate_pctl:.0f} 分位"
                    f"（> {BREAK_PCTL_CEIL:.0f} 分位线）——封板异常不牢"
                )
        elif break_rate >= BREAK_RATE_CEIL:
            reasons.append(
                f"炸板率 {break_rate:.0%} ≥ {BREAK_RATE_CEIL:.0%}（封板不牢；"
                "历史分位不可用，按业界经验值判）"
            )
    if limit_down is not None and limit_down >= LIMIT_DOWN_CEIL:
        reasons.append(f"跌停 {limit_down} 家 ≥ {LIMIT_DOWN_CEIL} 家（系统性风险）")
    if prev_zt_median_pct is not None and prev_zt_median_pct < PREV_ZT_MEDIAN_FLOOR:
        reasons.append(
            f"昨日涨停股今日中位溢价 {prev_zt_median_pct:.2f}% < {PREV_ZT_MEDIAN_FLOOR:.2f}%（接力亏钱）"
        )

    if phase_unreliable and reasons:
        # 判定本身不可信时不升级为强预警，但把不确定性明示出来（诚实原则）
        reasons.append("⚠️ 情绪判定自指检查未通过，上述结论置信度下调")

    # 原始输入 + 实际使用的口径（percentile / absolute / missing）留痕。
    #
    # ⚠️ **未触发路径也必须带**（2026-09-16 修）：原先只在触发分支返回，于是
    # 「复核后已解除」时没有任何输入可比——而"当前晋级率多少"正是解释结论为何
    # 变化的关键（实测场景：生成时 8%/9 分位 → 复核 36%/62 分位）。缺了它，用户
    # 只能看到"生成时说退潮、现在说高潮"两个标签，无从判断该信哪个。
    # 语义上也是对称的：「为什么这天触发了」与「为什么这天没触发」必须同样可回答。
    signals = {
        "promotion_1to2": promotion_1to2,
        "promotion_1to2_pctl": promotion_1to2_pctl,
        "break_rate": break_rate,
        "break_rate_pctl": break_rate_pctl,
        "limit_down": limit_down,
        "prev_zt_median_pct": prev_zt_median_pct,
        "promo_caliber": (
            "percentile" if promotion_1to2_pctl is not None
            else "absolute" if promotion_1to2 is not None else "missing"
        ),
        "break_caliber": (
            "percentile" if break_rate_pctl is not None
            else "absolute" if break_rate is not None else "missing"
        ),
    }

    if not reasons:
        return {
            "stand_aside": False, "level": "none", "reasons": [], "advice": ADVICE_NONE,
            "phase": phase, "strip_buy_range": False, "signals": signals,
        }

    severe = (phase in SEVERE_PHASES) or len(reasons) >= 2
    level = "strong" if severe else "mild"
    return {
        "stand_aside": True,
        "level": level,
        "reasons": reasons,
        "advice": ADVICE_STRONG if severe else ADVICE_MILD,
        "disclaimer": DISCLAIMER,
        # 相位原样带出：`level` 是「理由条数」的计数产物，不足以表达信号性质——
        # 「退潮」单条只有 1 条理由（level=mild），却是 regime 级判断，
        # 与「晋级率 29% 擦线」这种量化阈值临界完全不同。下游分档判据需要它。
        "phase": phase,
        # 分档结论**在此算出并落库**（唯一真相源）：后端 apply_gate_to_picks 与前端
        # 横幅都只读这个字段，不在各自那一侧重算一遍规则（重算 = 两份口径必然漂移）。
        "strip_buy_range": (phase in STRIP_PHASES) or len(reasons) >= STRIP_MIN_REASONS,
        "signals": signals,
    }


#: 会**撤除买入范围**的相位（分档口径，2026-09-10 用户拍板）：判定见 should_strip_buy_range。
STRIP_PHASES = _ADVERSE
#: 撤除买入范围的理由条数下限（多条叠加 = 独立信号相互印证，不再是单点擦线）
STRIP_MIN_REASONS = 2


def should_strip_buy_range(gate: dict) -> bool:
    """闸门是否**撤除买入范围**（分档判据，2026-09-10 用户拍板）。

    为什么不用 ``level``：level 由「理由条数 ≥2」决定，是**计数产物**——
    「退潮」单条只算 1 条理由 ⇒ level=mild，但它是 **regime 级相位判断**；
    「首板晋级率 12% 落在历史 6 分位」也是 1 条理由 ⇒ 同为 mild，却是**单指标异常**。
    两者信号性质完全不同，用同一档位处理就会把「市场在山腰」和「指标差 1 个点」
    混为一谈。因此判据按**信号性质**定义：

    - **撤区间**：相位 ∈ {退潮, 冰点}（regime 判断），或 ≥2 条理由叠加（多信号印证）；
    - **只提示**：单条指标异常（晋级率/炸板率/昨日溢价）——保留横幅与
      「控制仓位、减少出手频率」建议，但不否定当天的全部机会。

    实测依据：近 9 个交易日 `stand_aside` 命中 **9/9**（strong 6 / mild 3），
    其中 3 天仅凭单条擦线触发；旧口径一律撤区间 ⇒ 盘前卡片**全期从不显示买入区间**，
    「仅观察」沦为常驻背景、闸门失去区分度（用户看不出哪天真的该收手）。
    """
    if not gate.get("stand_aside"):
        return False
    if "strip_buy_range" in gate:
        # 新生成的行自带结论（唯一真相源在 evaluate_stand_aside）
        return bool(gate["strip_buy_range"])
    # 兼容 2026-09-10 之前生成并落库的旧行（无该字段）：按同口径现场推导
    if gate.get("phase") in STRIP_PHASES:
        return True
    return len(gate.get("reasons") or []) >= STRIP_MIN_REASONS


def apply_gate_to_picks(picks: list[dict], gate: dict) -> list[dict]:
    """闸门触发时对组合的处理：**分档**——撤除档撤买入范围 + 三态分层；提示档只提示。

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

    **分级口径（2026-09-10 用户拍板）**：撤除买入范围由 `should_strip_buy_range`
    判定——**相位级信号（退潮/冰点）或多条理由叠加**才撤；单条量化阈值擦线只提示。
    详见该函数 docstring 的实测依据。
    """
    if not should_strip_buy_range(gate):
        # 非撤除档：标的层保持原样——保留买入范围、不写 observation_only、不打三态 tag。
        # 三态 tag 与买入范围是同一件事的两面，绝不能出现「有买入范围 + 标注仅观察」。
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
