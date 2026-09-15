"""梯队联动归属：每只涨停股只归属一个主题材（纯函数）。

`backend/app/services/theme_service.py` 的内部切片（IMP-005 批 4，2026-09-15 从 1383 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与前置注释与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app.services.theme_service` 统一转发，因此引用方零改动。
"""
from __future__ import annotations


from .normalize import (
    normalize_theme,
    parse_theme_tags,
)
UNCLASSIFIED = "未分类"


# ---------------------------------------------------------------- 纯函数：梯队联动归属


def assign_primary_themes(records: list) -> dict[str, str]:
    """梯队联动归属：每只涨停股**只归属一个**主题材。

    为什么不能按静态标签硬套：一只票的涨停原因常带 3~5 个题材标签，
    旧实现把它塞进每个标签对应的卡片，导致同一梯队被拆散、题材重复计数
    （2026-08-28 实测拆散率 22%）；且主属性按「成员总数」判定时，
    「业绩驱动」这类 41 只的大杂烩会把「创新药 3 连板梯队」整队吸收——
    3 板龙头在自己的题材里是龙头，被吸走后沦为别家「跟风」。

    用户原则：**当日形成涨停梯队的个股必定归属同一题材板块**。
    归属判据（依次比较，取最优候选，全部可从数据反推）：

    1. **连板密度** = 题材内连板(≥2板)成员数 / 题材内涨停成员数。
       这是「当日真实联动」的度量：3 只里 1 只 3 板（密度 0.33）说明这批票
       在并肩打高度；41 只里 10 只连板（密度 0.24）说明是松散堆料。
    2. 题材涨停家数（同密度下成建制优先）。
    3. 题材最高连板（同家数下高度优先）。
    4. 是否为该股涨停原因的**首个标签**（ths reason 首标签通常即主属性，
       作为最终 tiebreaker 保证结果确定性）。

    约束：**家数 ≥2 的题材才有资格吸走成员**。1 只票的「题材」是个股行情，
    它的存在本身就依赖这只股票——让它当主属性会造出无数伪题材卡片。
    全部候选都是 1 家时，回退为该股首个标签（结果仍是唯一的）。

    :return: symbol -> 主题材名（规范化后）
    """
    # 先按原始标签口径统计每个题材的成员结构（不依赖归属结果，无循环依赖）
    stats: dict[str, dict] = {}
    for rec in records:
        boards = rec.consecutive_boards or 0
        for t in [normalize_theme(x) for x in parse_theme_tags(rec.reason)] or [UNCLASSIFIED]:
            st = stats.setdefault(t, {"count": 0, "lianban": 0, "max_boards": 0})
            st["count"] += 1
            if boards >= 2:
                st["lianban"] += 1
            st["max_boards"] = max(st["max_boards"], boards)

    primary: dict[str, str] = {}
    for rec in records:
        tags = [normalize_theme(x) for x in parse_theme_tags(rec.reason)]
        candidates = []
        for t in tags:
            if t not in candidates:
                candidates.append(t)
        if not candidates:
            primary[rec.symbol] = UNCLASSIFIED
            continue

        # 家数 ≥2 的题材才有吸成员资格；全是个股行情时保留原候选
        eligible = [t for t in candidates if stats[t]["count"] >= 2]
        pool_c = eligible or candidates

        def rank_key(t: str) -> tuple:
            st = stats[t]
            density = st["lianban"] / st["count"] if st["count"] else 0.0
            is_first_tag = 1 if t == candidates[0] else 0
            return (density, st["count"], st["max_boards"], is_first_tag)

        primary[rec.symbol] = max(pool_c, key=rank_key)
    return primary
