"""风险档位与出场纪律参考（CONTEXT.md: Risk Tier / Invalidation）。

借鉴 `freqtrade`（53.9k★，用户 GitHub trading 分组）的核心思路：那个项目真正
值钱的不是选币，而是**出场纪律**——止损、跟踪止盈、ROI 分档表、仓位管理。
"确保能盈利"在规则层面只能靠这个实现：选对只是入场，出场才决定盈亏。

本模块把它的三个构件映射到 A 股语境（参数按 A 股波动特征重设，不是照搬）：

- **止损参考位**：固定档位百分比 与 1.5×ATR% 取大者（波动大的票给足空间，
  避免被正常波动扫出），再 clamp 到 3%~12%
- **跟踪止盈**：自持仓期最高点回撤 X% 即视为趋势终结（freqtrade trailing stop）
- **ROI 分档表**：达到目标收益后的分批减仓参考（freqtrade ROI table）

红线 3：以上全部是"参考纪律"与"条件陈述"，不是操作指令，不构成买卖建议。
"""

from __future__ import annotations

RISK_TIERS = ("龙头博弈", "趋势跟随", "情绪低位")

#: 梯队地位 → 风险档位。龙头/空间板/反包是高位博弈，中军与领涨是波段，
#: 补涨/跟风/首板/滞涨是低位反抽博弈（对了快、错了更快）。
TIER_BY_ROLE: dict[str, str] = {
    "空间板": "龙头博弈",
    "龙头": "龙头博弈",
    "反包": "龙头博弈",
    "中军": "趋势跟随",
    "领涨": "趋势跟随",
    "同步": "趋势跟随",
    "补涨": "情绪低位",
    "跟风": "情绪低位",
    "首板": "情绪低位",
    "滞涨": "情绪低位",
    "断板": "情绪低位",
}

#: 档位参数（A 股经验值；周末复盘可调，调整须人工确认）
TIER_PARAMS: dict[str, dict] = {
    "龙头博弈": {
        "stop_pct": 0.07,  # 高位博弈，止损从严
        "trailing_pct": 0.08,  # 回撤 8% 视为趋势终结
        "roi_ladder": [(0.10, "减半仓"), (0.20, "再减半")],
        "ma_line": 5,  # 收盘跌破 5 日线即失效
        "note": "高位接力，错了要快；仓位宜小",
    },
    "趋势跟随": {
        "stop_pct": 0.08,  # 波段给足空间
        "trailing_pct": 0.10,
        "roi_ladder": [(0.15, "减 1/3 仓"), (0.30, "再减 1/3")],
        "ma_line": 10,
        "note": "波段持有，容忍回撤",
    },
    "情绪低位": {
        "stop_pct": 0.04,  # 反抽博弈，破位即走
        "trailing_pct": 0.05,
        "roi_ladder": [(0.07, "减半仓")],
        "ma_line": 5,
        "note": "低位反抽，快进快出",
    },
}

STOP_PCT_FLOOR = 0.03
STOP_PCT_CEIL = 0.12
ATR_MULTIPLIER = 1.5  # 止损至少容纳 1.5 倍 ATR 的波动

DISCLAIMER = "以上为规则化参考纪律，不构成买卖建议"


def risk_tier_of(role: str) -> str:
    """梯队地位 → 风险档位（未识别的角色一律按最保守的「情绪低位」处理）。"""
    return TIER_BY_ROLE.get(role, "情绪低位")


def stop_loss_reference(
    *,
    price: float | None,
    tier: str,
    atr_pct: float | None = None,
) -> dict | None:
    """止损参考位：max(档位基准, 1.5×ATR%) 再 clamp 到 3%~12%。

    :param atr_pct: ATR14 / 现价（百分数），None 时纯用档位基准
    """
    if not price or price <= 0:
        return None
    params = TIER_PARAMS.get(tier, TIER_PARAMS["情绪低位"])
    base = params["stop_pct"]
    basis = [f"档位基准 {base:.0%}"]
    pct = base
    if atr_pct and atr_pct > 0:
        atr_stop = atr_pct / 100.0 * ATR_MULTIPLIER
        basis.append(f"1.5×ATR {atr_pct:.2f}% = {atr_stop:.1%}")
        pct = max(pct, atr_stop)
    pct = max(STOP_PCT_FLOOR, min(STOP_PCT_CEIL, pct))
    return {
        "pct": round(pct * 100, 2),
        "price": round(price * (1 - pct), 2),
        "basis": f"{'、'.join(basis)} → 取 {pct:.1%}（clamp {STOP_PCT_FLOOR:.0%}~{STOP_PCT_CEIL:.0%}）",
    }


def exit_discipline(tier: str) -> dict:
    """跟踪止盈与 ROI 分档参考（freqtrade trailing stop / ROI table 的 A 股映射）。"""
    params = TIER_PARAMS.get(tier, TIER_PARAMS["情绪低位"])
    return {
        "trailing_pct": round(params["trailing_pct"] * 100, 1),
        "roi_ladder": [
            {"gain_pct": round(g * 100, 1), "action": a} for g, a in params["roi_ladder"]
        ],
        "note": params["note"],
        "disclaimer": DISCLAIMER,
    }


def build_invalidations(
    *,
    role: str,
    tier: str,
    theme_stage: str | None = None,
    theme_max_boards: int = 0,
    prev_theme_max_boards: int | None = None,
    ma_value: float | None = None,
    event_titles: list[str] | None = None,
) -> list[str]:
    """失效条件（CONTEXT.md: Invalidation）——入选逻辑被证伪的客观条件。

    每条都是可事后核验的客观陈述，触发即说明"这只票不再是它被选中的那个样子"。
    """
    out: list[str] = []
    if theme_stage in ("退潮", "分歧"):
        out.append(f"所属题材进入「{theme_stage}」阶段，梯队合力消散")
    if prev_theme_max_boards and theme_max_boards < prev_theme_max_boards:
        out.append(
            f"题材最高板由 {prev_theme_max_boards} 板回落至 {theme_max_boards} 板（高度塌陷）"
        )
    params = TIER_PARAMS.get(tier, TIER_PARAMS["情绪低位"])
    ma_line = params["ma_line"]
    if ma_value:
        out.append(f"收盘跌破 {ma_line} 日线（当前 {ma_value}）")
    else:
        out.append(f"收盘跌破 {ma_line} 日线")
    if role in ("龙头", "空间板", "反包"):
        out.append("龙头炸板且尾盘未能回封")
    if tier == "情绪低位":
        out.append("两日内未能兑现反抽（时间成本失效）")
    for t in (event_titles or [])[:2]:
        out.append(f"关联事件「{t}」被证伪或到期")
    return out
