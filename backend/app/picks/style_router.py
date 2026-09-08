"""相位→风格路由表（审查报告 §4.1，P1-2 规则版）。

六相位 → 当日默认风格 + 六维权重偏移。与 regime.py 的关系是**叠加不是替代**：
regime 按财报日历选基础权重表（业绩驱动期/空窗期），本模块再按市场情绪相位
做当日微调——同一天两个选择器先后生效：weights = offsets(regime_weights)。

设计依据（docs/system-audit-20260908.md §4.1）：
- 发酵/高潮 → 题材进攻：情绪策略最顺的环境，题材梯队（echelon）与情绪维加权，
  趋势/基本面让位（此时买的是接力与想象空间，不是安全边际）；
- 冰点/退潮 → 趋势防守（低位反转优先）：情绪策略系统性失效期，趋势与基本面
  加权、题材梯队减权（退潮期的龙头也难独善，见 echelon.STAGE_ADJUST）；
- 分歧 → 防守微调：有热度但赚钱效应走坏，小幅向趋势倾斜、梯队减权；
- 修复 → 均衡：无偏移（基准态）。

纪律（延续 09-04 文档：改阈值必附参数扫描）：
- 偏移幅度全部 ≤0.06（初版保守），且**进配置可覆盖**（settings.picks_style_offsets_json，
  JSON {相位: {维度: delta}}）；非法配置显式抛错，绝不静默回退默认值。
- 偏移是否真的提升胜率待数据验证：factor_ic_review.py 月度 IC 复核 +
  影子持仓 A/B（可跟名单）积累样本后再定稿；本表是**规则版起点**，不是结论。

合成规则（apply_style_offsets）：逐维相加 → 截断到 [0.02, 0.45] → 归一化回和 1.0。
截断下限 0.02 保证没有维度被彻底灭声（八维打开成独立因子后每维都要能留痕）。
"""
from __future__ import annotations

import json

#: 六维权重的合法维度（与 regime.WEIGHTS_BY_REGIME 同一套键）
DIMS = ("sentiment", "news", "tech", "fundamental", "capital", "echelon")

#: 偏移幅度上限（绝对值）：初版保守，IC 复核后再议放开
OFFSET_MAX = 0.06
#: 归一化后单维权重的下/上界：防止单维被灭声或一家独大
WEIGHT_FLOOR = 0.02
WEIGHT_CEIL = 0.45

#: 默认路由表：相位 → (风格名, 偏移)。基础说明在 _BASIS。
#: 发酵/高潮共用「题材进攻」（审查报告原文把两相位并为一条规则）。
DEFAULT_ROUTES: dict[str, tuple[str, dict[str, float]]] = {
    "发酵": ("题材进攻", {"echelon": 0.06, "sentiment": 0.04, "tech": -0.05, "fundamental": -0.05}),
    "高潮": ("题材进攻", {"echelon": 0.06, "sentiment": 0.04, "tech": -0.05, "fundamental": -0.05}),
    "冰点": ("趋势防守", {"tech": 0.06, "fundamental": 0.05, "sentiment": -0.05, "echelon": -0.05}),
    "退潮": ("趋势防守", {"tech": 0.06, "fundamental": 0.05, "sentiment": -0.05, "echelon": -0.05}),
    "分歧": ("防守微调", {"tech": 0.03, "echelon": -0.03}),
    "修复": ("均衡", {}),
}

_BASIS = {
    "题材进攻": "发酵/高潮期情绪策略最顺：题材梯队与情绪维加权，趋势/基本面让位",
    "趋势防守": "冰点/退潮期情绪策略系统性失效：趋势与基本面加权、题材梯队减权（低位反转优先）",
    "防守微调": "分歧期有热度但赚钱效应走坏：小幅向趋势倾斜、梯队减权",
    "均衡": "修复期环境中性：按基础权重执行，不加偏移",
}


def _load_override() -> dict[str, dict[str, float]]:
    """读配置覆盖（settings.picks_style_offsets_json）。非法配置显式抛错。"""
    from app.core.config import settings

    raw = (settings.picks_style_offsets_json or "").strip()
    if not raw:
        return {}
    data = json.loads(raw)  # 非法 JSON → 抛 ValueError（fail fast，不静默回退）
    if not isinstance(data, dict):
        raise ValueError("picks_style_offsets_json 必须是 {相位: {维度: delta}} 对象")
    out: dict[str, dict[str, float]] = {}
    for phase, offsets in data.items():
        if not isinstance(offsets, dict):
            raise ValueError(f"相位「{phase}」的偏移必须是对象")
        for dim, delta in offsets.items():
            if dim not in DIMS:
                raise ValueError(f"未知维度「{dim}」（合法：{DIMS}）")
            delta_f = float(delta)
            if abs(delta_f) > OFFSET_MAX:
                raise ValueError(f"偏移 {delta_f} 超出 ±{OFFSET_MAX} 上限（初版纪律）")
            out.setdefault(phase, {})[dim] = delta_f
    return out


def route_style(phase: str | None) -> dict:
    """相位 → 当日风格路由（纯逻辑；配置覆盖在此合并）。

    :param phase: 市场情绪相位（六相位；None/未知 = 相位缺失，不路由、显式留痕）
    :return: {phase, style, label, offsets, basis, routed}
             routed=False 表示相位缺失或未知（未应用任何偏移，诚实降级）。
    """
    override = _load_override()
    if phase in DEFAULT_ROUTES:
        style_name, default_offsets = DEFAULT_ROUTES[phase]
        offsets = dict(default_offsets)
        offsets.update(override.get(phase, {}))  # 配置显式覆盖同名维度
        basis = _BASIS[style_name]
        if override.get(phase):
            basis += "（含配置覆盖）"
        return {"phase": phase, "style": style_name, "label": style_name,
                "offsets": offsets, "basis": basis, "routed": True}
    # 相位缺失（None）或未知值：不路由——绝不拿未知相位硬套风格
    return {"phase": phase, "style": "均衡", "label": "均衡", "offsets": {},
            "basis": f"相位缺失或未知（{phase!r}），不应用风格偏移（诚实降级）", "routed": False}


def apply_style_offsets(weights: dict[str, float], offsets: dict[str, float]) -> dict[str, float]:
    """基础权重 + 相位偏移 → 归一化权重（和恒 1.0；截断防灭声/独大）。"""
    merged = {d: float(weights.get(d, 0.0)) + float(offsets.get(d, 0.0)) for d in DIMS}
    merged = {d: min(max(v, WEIGHT_FLOOR), WEIGHT_CEIL) for d, v in merged.items()}
    total = sum(merged.values())
    if total <= 0:  # 理论不可达（floor 保底），防御式兜底
        return {d: round(1.0 / len(DIMS), 4) for d in DIMS}
    return {d: round(v / total, 4) for d, v in merged.items()}


def style_note(style: dict | None) -> str | None:
    """风格路由 → meta_confidence 的留痕短句（无偏移时返回 None 不打扰理由列表）。"""
    if not style or not style.get("offsets"):
        return None
    brief = "、".join(
        f"{d}{delta:+.2f}" for d, delta in sorted(style["offsets"].items(), key=lambda kv: -abs(kv[1]))
    )
    return f"当日风格「{style['label']}」（相位{style.get('phase') or '缺失'}）：{brief}"
