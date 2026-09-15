"""梯队角色、梯队完整度与题材阶段（纯函数）。

`backend/app/services/theme_service.py` 的内部切片（IMP-005 批 4，2026-09-15 从 1383 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与前置注释与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app.services.theme_service` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

#: 中军（权重股）流通市值门槛：100 亿。参考业界口径「百亿/千亿级、容纳大资金」。
MIDDLE_WEIGHT_MIN_CAP = 10_000_000_000.0  # 100 亿元


#: 补涨的触发高度：题材内已出现 3 板及以上，龙头打出空间后的低位票才算补涨。
REPAIR_MIN_LEADER_BOARDS = 3


# ---------------------------------------------------------------- 纯函数：角色判定


def classify_role(
    *,
    boards: int,
    theme_max_boards: int,
    market_max_boards: int,
    prev_boards: int | None,
    float_market_cap: float | None,
    seal_phase_value: str | None,
    break_count: int | None,
) -> str:
    """判定个股在题材天梯中的角色。

    判定顺序即优先级，先命中者胜出（业界口径：龙头定高度、中军定深度、跟风定卖点）。

    - ``反包``    ：昨日 ≥2 板、今日回到 1 板 —— 中间必然断过板后又封回
    - ``空间板``  ：题材内 ≥3 板且为全市场最高板（市场情绪标杆）
    - ``龙头``    ：题材内连板最高（≥2 板）
    - ``中军``    ：流通市值 ≥100 亿的大容量票，不追求连板高度
    - ``补涨``    ：首板且题材已出现 ≥3 板（龙头打出空间后的低位替代）
    - ``跟风``    ：首板但封板晚（午后/尾盘）或盘中开过板
    - ``首板``    ：其余当日首次涨停
    """
    if prev_boards is not None and prev_boards >= 2 and boards <= 1:
        return "反包"
    if boards >= 3 and market_max_boards and boards >= market_max_boards:
        return "空间板"
    if boards >= 2 and boards >= theme_max_boards:
        return "龙头"
    if float_market_cap and float_market_cap >= MIDDLE_WEIGHT_MIN_CAP:
        return "中军"
    if boards <= 1 and theme_max_boards >= REPAIR_MIN_LEADER_BOARDS:
        return "补涨"
    if boards <= 1 and (seal_phase_value in ("午后", "尾盘") or (break_count or 0) > 0):
        return "跟风"
    if boards <= 1:
        return "首板"
    return "跟风"


#: 角色展示排序权重（同一连板高度内按此排序）
ROLE_ORDER = {
    "空间板": 0,
    "龙头": 1,
    "中军": 2,
    "反包": 3,
    "补涨": 4,
    "跟风": 5,
    "首板": 6,
    "断板": 7,
}


# ---------------------------------------------------------------- 纯函数：梯队与强度


def echelon_completeness(levels: dict[int, int], max_boards: int) -> float:
    """梯队完整度：0~1。

    只看最高板高度会掩盖「断层」——4 板下面直接是首板、中间 2/3 板全空，
    说明梯队不健康。这里统计 2..max_boards 各档是否有票承接到。

    只有首板（max_boards<=1）时返回 0：没有承接，谈不上梯队。
    """
    if max_boards <= 1:
        return 0.0
    present = sum(1 for lv in range(2, max_boards + 1) if (levels.get(lv) or 0) > 0)
    return round(present / (max_boards - 1), 3)


def judge_theme_stage(
    *,
    limit_up_count: int,
    max_boards: int,
    prev_limit_up_count: int | None,
    prev_max_boards: int | None,
    reopen_rate: float,
    completeness: float,
    premium_median: float | None,
) -> tuple[str, list[str]]:
    """判断题材所处阶段，返回 ``(阶段, 判定依据)``。

    判定顺序：退潮 → 分歧 → 高潮 → 发酵 → 启动。先命中者胜出。

    **分歧必须排在高潮之前**：上一轮把市场判成「高潮」的教训就是让热度指标
    （家数/高度）压过赚钱效应。题材级不能重蹈覆辙——涨停家数再多，
    只要接力亏钱（溢价转负）或高度回落，就不是高潮。
    """
    basis: list[str] = []

    # 退潮：涨停骤减（较前日腰斩且已不足 3 家），或最高板塌陷
    if prev_limit_up_count is not None and prev_limit_up_count >= 4:
        if limit_up_count <= 2 or limit_up_count * 2 <= prev_limit_up_count:
            basis.append(f"涨停家数由 {prev_limit_up_count} 骤降至 {limit_up_count}")
            return "退潮", basis
    if prev_max_boards is not None and prev_max_boards >= 3 and max_boards < prev_max_boards:
        if limit_up_count <= 2:
            basis.append(f"最高连板由 {prev_max_boards} 板塌陷至 {max_boards} 板")
            return "退潮", basis

    # 分歧：有热度但承接力或赚钱效应走坏（可否决高潮）
    if limit_up_count >= 3:
        if premium_median is not None and premium_median < 0:
            basis.append(f"昨日涨停股今日中位溢价 {premium_median:.2f}%（接力亏钱）")
            return "分歧", basis
        if prev_max_boards is not None and max_boards < prev_max_boards:
            basis.append(f"最高连板由 {prev_max_boards} 板回落至 {max_boards} 板")
            return "分歧", basis
        if reopen_rate >= 0.3:
            basis.append(f"开板率 {reopen_rate:.0%} 偏高，封板不牢")
            return "分歧", basis

    # 高潮：家数多 + 高度高 + 封板牢
    if limit_up_count >= 6 and max_boards >= 5 and reopen_rate <= 0.2:
        basis.append(
            f"涨停 {limit_up_count} 家、最高 {max_boards} 板、开板率仅 {reopen_rate:.0%}"
        )
        return "高潮", basis

    # 发酵：有家数、有高度、梯队能承接
    if limit_up_count >= 3 and max_boards >= 3 and completeness >= 0.5:
        basis.append(
            f"涨停 {limit_up_count} 家、最高 {max_boards} 板、梯队完整度 {completeness:.0%}"
        )
        return "发酵", basis

    # 启动：刚起势
    if limit_up_count >= 2 or max_boards >= 2:
        basis.append(f"涨停 {limit_up_count} 家、最高 {max_boards} 板，梯队尚未成型")
        return "启动", basis

    basis.append(f"仅 {limit_up_count} 只涨停且均为首板")
    return "启动", basis
