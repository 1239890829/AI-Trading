"""封板质量与题材健康度（纯函数）。

`backend/app/services/theme_service.py` 的内部切片（IMP-005 批 4，2026-09-15 从 1383 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与前置注释与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app.services.theme_service` 统一转发，因此引用方零改动。
"""
from __future__ import annotations


from .formation import (
    formation_level,
)
def seal_quality_score(seal_dist: dict[str, int], total: int) -> float:
    """封板质量：早盘封板占比高 = 资金坚决。0~1。"""
    if total <= 0:
        return 0.0
    weights = {"早盘": 1.0, "上午": 0.7, "午后": 0.4, "尾盘": 0.2}
    score = sum(weights.get(k, 0.3) * v for k, v in seal_dist.items())
    return round(score / total, 3)


# ---------------------------------------------------------------- 纯函数：健康度


def theme_health_note(
    *,
    theme: str,
    stage: str,
    limit_up_count: int,
    max_boards: int,
    completeness: float,
    missing_levels: list[int],
    has_middle_weight: bool,
    reopen_rate: float,
    premium_median: float | None,
) -> tuple[str, list[str]]:
    """生成「一句话梯队健康度 + 风险点」。

    这是看板的结论层：必须能直接回答「梯队是否健康、风险在哪」。

    ``missing_levels`` 由调用方从实际梯队档位算出。早前的实现在这里自己
    用 ``range(2, max_boards + 1)`` 生成缺失档位，结果把「最高板所在档位」
    （按定义必然有票）也算成缺失，输出「7 板下方 2~7 板档位缺失」这种自相矛
    盾的文案。
    """
    risks: list[str] = []
    healthy_points: list[str] = []

    if max_boards <= 1:
        risks.append("全部为首板，无二板以上承接，属于一日游结构")
    elif missing_levels:
        span = (
            f"{missing_levels[0]}~{missing_levels[-1]}" if len(missing_levels) > 1
            else f"{missing_levels[0]}"
        )
        risks.append(f"梯队断层（{max_boards} 板下方 {span} 板无承接）")
    else:
        healthy_points.append(f"梯队完整（1~{max_boards} 板均有承接）")

    if not has_middle_weight:
        risks.append("无百亿级中军，纯小票结构，持续度存疑")
    else:
        healthy_points.append("有中军权重承接")

    formation = formation_level(limit_up_count)
    if formation == "个股行情":
        # 1 只票的题材标签不是题材。这里必须说清楚，否则用户会误以为发现了新题材。
        risks.insert(0, f"仅 {limit_up_count} 只涨停，属个股独立行情而非题材，不构成梯队")
    elif formation != "成建制":
        risks.insert(0, f"涨停 {limit_up_count} 家，{formation}，梯队尚未成建制")

    if reopen_rate >= 0.3:
        risks.append(f"开板率 {reopen_rate:.0%}，封板不牢")

    if premium_median is not None and premium_median < 0:
        risks.append(f"昨日涨停股今日中位溢价 {premium_median:.2f}%，接力亏钱")
    elif premium_median is not None and premium_median >= 3:
        healthy_points.append(f"接力赚钱效应良好（中位溢价 {premium_median:.2f}%）")

    if not risks:
        note = f"{theme}：{stage}期，梯队健康——" + "、".join(healthy_points) + "。"
    else:
        verdict = "梯队健康" if len(risks) <= 1 else "梯队存在结构缺陷"
        note = f"{theme}：{stage}期，{verdict}——" + "；".join(risks) + "。"
        if healthy_points:
            note += "（支撑点：" + "、".join(healthy_points) + "）"
    return note, risks
