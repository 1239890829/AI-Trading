"""题材梯队看板：把涨停池从「平铺列表」重组为「题材容器 + 连板天梯」。

设计原则
--------
1. **题材归因以同花顺官方口径为准**：ths `limit_up_pool.reason` 是「+」分隔的题材串，
   东财涨停池没有这个字段。字段缺失时该股归入「未分类」，不臆造题材。
2. **所有可证伪的判断都必须能从数据反推**：角色、阶段、健康度都是规则化输出，
   并在响应里附带 `basis`（判定依据），便于人工复核与回测。
3. **纯计算与 IO 分离**：本模块上半部分是不依赖网络/数据库的纯函数（可直接单测），
   `build_theme_board` 才做 IO 编排。

数据源分工（详见 docs/data-sources.md §3）
------------------------------------------
- 题材归因 / 连板数 / 封单额：ths limit_up_pool
- 换手率 / 开板次数 / 末封时间 / 流通市值 / 成交额 / 行业：东财 push2ex
- 板块涨跌幅 / 涨跌家数 / 成交额 / 主力净流入 / 领涨股：东财 push2delay 板块列表
- 题材连续活跃天数：近 N 日 ths 涨停池（自建，衡量「资金是否持续」）
- 接力赚钱效应（溢价）：前一日题材涨停股 + 全市场快照

已知边界（不可臆造）
--------------------
- 板块的 3/5/10 日涨跌幅原为东财字段序推断；2026-08-31 起由路由层用同花顺
  官方板块 K 线交叉验证并替换（board.multi_day_verified=true，推断值保留在
  chg_*_inferred 供审计，见 market.py _verify_board_multi_day）。
  目录服务不可用/题材不在目录时保留推断值并如实标注。
- 炸板池无法按题材归属（东财/ths 炸板池都不带题材），题材级只能用「开板过的涨停股占比」
  近似，全市场炸板率另行给出作为背景。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date, timedelta

from app.market import board_flow
from app.services.dragon_service import (
    dragon_score,
    news_persistence,
    stock_sentiment,
    theme_core,
)

log = logging.getLogger(__name__)


def _pick_provider(provider, class_name: str):
    """从 composite provider 链里取出指定实现。

    题材归因只有同花顺提供 ``reason``，开板/换手/流通市值只有东财提供；
    走 composite 会在每个失败源上串行重试（每源 8s 超时 × 4 源），
    回溯 5 日就能把请求拖到 3 分钟以上并压垮事件循环。这里直接定位到具体 provider。
    """
    members = getattr(provider, "providers", None)
    if isinstance(members, (list, tuple)):
        return next((p for p in members if type(p).__name__ == class_name), None)
    return provider if type(provider).__name__ == class_name else None

# ---------------------------------------------------------------- 题材归一化词典

#: 原始标签 → 归一化题材名。只合并明确同义的，避免过度合并掩盖真实结构。
#: 新增词条时必须同步 tests/test_theme_service.py，并说明合并理由。
THEME_ALIASES: dict[str, str] = {
    # ⚠️ 官方名称纪律（2026-09-08 用户指令）：题材名称必须逐字使用官方标准名，
    # 严禁自创/翻译/简写。本表只允许两类条目：
    #   1) value 是 **题材目录中真实存在的官方概念名**（如「黄金概念」「液冷服务器」
    #      「数据中心(AIDC)」「央企国企改革」「转基因」——逐字含括号）；
    #   2) 原始标签本身已在目录中 → 直接删除映射让其直通（如「业绩增长」「算力租赁」
    #      「人形机器人」「国企改革」等 ths 原发标签）。
    # 曾有 10 个自创目标名（业绩驱动/黄金珠宝/国资改革/液冷/算力/机器人/数据中心/
    # 重组/控制权变更/次新股/PTFE）被全部清除——它们在同花顺 App 里搜不到。
    # 官方无统一概念的近似标签（如业绩类 16 种表述）**不做归并**：名称准确性优先，
    # 碎片化由 official_matches 官方挂靠 + formation 分级兜底。
    # 黄金系：炒作时同涨同跌，归到官方「黄金概念」
    "黄金": "黄金概念",
    "黄金概念": "黄金概念",
    "珠宝加工": "黄金概念",
    "黄金租赁": "黄金概念",
    "艺术珠宝": "黄金概念",
    # 液冷：官方概念名是「液冷服务器」
    "AI液冷": "液冷服务器",
    # 数据中心：官方概念名逐字为「数据中心(AIDC)」
    "数据中心业务": "数据中心(AIDC)",
    "数据中心交换机": "数据中心(AIDC)",
    # 国资：官方概念名为「央企国企改革」
    "央国企改革": "央企国企改革",
    "央企改革": "央企国企改革",
    # 转基因：与种业同源，官方「转基因」概念存在
    "转基因玉米": "转基因",
}

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

#: 中军（权重股）流通市值门槛：100 亿。参考业界口径「百亿/千亿级、容纳大资金」。
MIDDLE_WEIGHT_MIN_CAP = 10_000_000_000.0  # 100 亿元

#: 补涨的触发高度：题材内已出现 3 板及以上，龙头打出空间后的低位票才算补涨。
REPAIR_MIN_LEADER_BOARDS = 3

#: 封板时间分档（用于判断封板质量与跟风程度）
SEAL_PHASES = [
    (0, "早盘"),      # < 10:00
    (100000, "上午"),  # 10:00 - 11:30
    (113000, "午后"),  # 13:00 - 14:00
    (140000, "尾盘"),  # >= 14:00
]

UNCLASSIFIED = "未分类"


# ---------------------------------------------------------------- 纯函数：题材解析


def parse_theme_tags(reason: str | None) -> list[str]:
    """拆分 ths 涨停原因为题材标签列表。

    ths 口径是「+」分隔的多题材串，如 ``"黄金珠宝+珠宝加工+客户拓展"``。
    空值/无内容返回空列表（由调用方归入「未分类」），不猜。
    """
    if not reason:
        return []
    tags: list[str] = []
    for part in str(reason).split("+"):
        tag = part.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def normalize_theme(tag: str) -> str:
    """把原始题材标签归一到规范名；未收录的原样返回。"""
    t = tag.strip()
    return THEME_ALIASES.get(t, t)


def seal_phase(first_seal_time: str | None) -> str | None:
    """封板时间档位：早盘 / 上午 / 午后 / 尾盘。

    输入形如 ``"09:33"`` 或 ``"093300"``；无法解析返回 None（不臆造档位）。
    """
    if not first_seal_time:
        return None
    digits = "".join(ch for ch in str(first_seal_time) if ch.isdigit())
    if len(digits) < 4:
        return None
    # "10:30" → "1030" 需补零成 HHMMSS 再比较，否则 1030 < 100000 会全部落进「早盘」
    hhmmss = int(digits.ljust(6, "0")[:6])
    phase = SEAL_PHASES[0][1]
    for threshold, label in SEAL_PHASES:
        if hhmmss >= threshold:
            phase = label
    return phase


def _parse_hhmmss(ts: str | None) -> int | None:
    """``"09:33"`` / ``"093300"`` → HHMMSS 整数；无法解析 → None。"""
    if not ts:
        return None
    digits = "".join(ch for ch in str(ts) if ch.isdigit())
    if len(digits) < 4:
        return None
    return int(digits.ljust(6, "0")[:6])


# 官方涨停情绪场景 12 口径：10:00 前首封视为「早封」
EARLY_SEAL_CUTOFF_HHMMSS = 100000


def early_seal_rate(hhmmss_values: list[int | None]) -> float | None:
    """题材早封率：首封时间 ≤10:00 的成员占比。

    时间缺失的成员从分母剔除（样本缺失≠非早封）；全部缺失 → None
    （三态纪律：没有样本不冒充 0%）。
    """
    valid = [v for v in hhmmss_values if v is not None]
    if not valid:
        return None
    return round(sum(1 for v in valid if v <= EARLY_SEAL_CUTOFF_HHMMSS) / len(valid), 4)


def seal_retention_rate(pairs: list[tuple[float | None, float | None]]) -> float | None:
    """题材封单留存：Σ当前封单 / Σ盘中最高封单（官方场景 12 口径）。

    成对参与：当前封单与最高封单**都非空**且 max>0 的成员才进聚合；
    无有效样本 → None。留存率≈1 封得实（收盘=全天最高），趋 0 说明
    尾盘炸板/撤单多。max_seal_money 仅 ths 主源提供（东财池无此字段），
    样本不足时显式 None，不冒充。盘中调用时当前封单是实时值，指标
    仅收盘口径有意义——消费方须标注。
    """
    num = den = 0.0
    for cur, mx in pairs:
        if cur is not None and mx is not None and mx > 0:
            num += cur
            den += mx
    if den <= 0:
        return None
    return round(num / den, 4)


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


#: 分级展示排序（前端配色与排序参考）
TIER_ORDER = {"领涨": 0, "强势": 1, "活跃": 2, "观察": 3}


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


def seal_quality_score(seal_dist: dict[str, int], total: int) -> float:
    """封板质量：早盘封板占比高 = 资金坚决。0~1。"""
    if total <= 0:
        return 0.0
    weights = {"早盘": 1.0, "上午": 0.7, "午后": 0.4, "尾盘": 0.2}
    score = sum(weights.get(k, 0.3) * v for k, v in seal_dist.items())
    return round(score / total, 3)


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


# ---------------------------------------------------------------- IO 编排


def _median(values: list[float]) -> float | None:
    """中位数；空列表返回 None。均值会被少数极端值拉偏，情绪类指标一律用中位数。"""
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


async def build_theme_board(
    provider,
    trade_date: date,
    *,
    lookback_days: int = 5,
    snapshot_map: dict[str, dict] | None = None,
    fetch_snapshot=None,
) -> dict:
    """构建题材梯队看板。

    :param provider: composite provider（需提供 get_limit_up_pool / get_limit_break_pool）
    :param trade_date: 交易日（必须是交易日，非交易日的日期回退问题见 data-sources.md）
    :param lookback_days: 回溯天数，用于「连续活跃天数」与「前一日溢价」
    :param snapshot_map: symbol -> {"change_pct": float}；为空则溢价相关指标为 None
    :param fetch_snapshot: 可选回调，用于惰性获取快照（测试友好）
    """
    if snapshot_map is None and fetch_snapshot is not None:
        try:
            snapshot_map = await fetch_snapshot()
        except Exception as exc:  # pragma: no cover - 防御
            log.warning("snapshot unavailable for theme board: %s", exc)
            snapshot_map = None

    # ---- 1. 当日涨停池（主源 ths：带题材归因）----
    try:
        today_pool = await provider.get_limit_up_pool(trade_date)
    except Exception as exc:
        raise RuntimeError(f"涨停池获取失败：{exc}") from exc
    if not today_pool:
        return {
            "trade_date": trade_date.isoformat(),
            "themes": [],
            "summary": {"limit_up_total": 0, "theme_count": 0, "note": "当日无涨停数据"},
        }

    # ---- 2~5 并发：东财增强维度 / 板块指标 / 历史涨停池 / 炸板率 / 竞价强弱 ----
    # 串行会在慢源上叠加耗时（曾把请求拖到 3 分钟并压垮事件循环）。
    enhance, board_index, history, market_break_rate, auction_gaps = await asyncio.gather(
        _em_enhancement_map(provider, trade_date),
        _board_index(),
        _load_history(provider, trade_date, lookback_days),
        _market_break_rate(provider, trade_date, len(today_pool)),
        _auction_gaps(provider, trade_date, today_pool),
    )
    # history 由近及远，第 0 个就是最近的前一交易日
    prev_pool = history[0][1] if history else None

    prev_boards_map: dict[str, int] = {}
    prev_theme_stats: dict[str, dict] = {}
    # 昨日各题材的涨停股集合：溢价必须按题材算，用全市场口径会让所有题材溢价雷同
    prev_theme_symbols: dict[str, set[str]] = defaultdict(set)
    if prev_pool:
        for rec in prev_pool:
            prev_boards_map[rec.symbol] = rec.consecutive_boards or 0
            for th in [normalize_theme(t) for t in parse_theme_tags(rec.reason)] or [UNCLASSIFIED]:
                prev_theme_symbols[th].add(rec.symbol)
        prev_theme_stats = _theme_stats(prev_pool)

    # 按交易日由近及远排列（含当日），供「连续活跃天数」与「每日涨停家数」使用
    ordered_days: list[tuple[str, list]] = [(trade_date.isoformat(), today_pool)]
    ordered_days += [(d.isoformat(), p) for d, p in history]

    daily_counts: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for day_label, pool in ordered_days:
        for theme, st in _theme_stats(pool).items():
            daily_counts[theme].append((day_label, st["count"]))

    # 连续活跃天数：从当日往前，题材每天都出现涨停才累加（衡量资金是否持续）
    active_days: dict[str, int] = defaultdict(int)
    for theme in {normalize_theme(t) for r in today_pool for t in parse_theme_tags(r.reason)}:
        cnt = 0
        for _, pool in ordered_days:
            tags = {normalize_theme(t) for r in pool for t in parse_theme_tags(r.reason)}
            if theme in tags:
                cnt += 1
            else:
                break
        active_days[theme] = cnt

    # ---- 7. 断板股：昨日连板、今日不在涨停池 ----
    market_max_boards = max((r.consecutive_boards or 0) for r in today_pool)

    # 题材 → 成员（梯队联动归属：每只票只归属一个主题材，见 assign_primary_themes。
    # 旧实现按标签全量塞入，同一梯队被拆散到多张卡片、题材重复计数，实测拆散率 22%）
    primary_of = assign_primary_themes(today_pool)
    theme_members: dict[str, list] = defaultdict(list)
    stock_themes: dict[str, list[str]] = defaultdict(list)
    for rec in today_pool:
        tags = parse_theme_tags(rec.reason)
        # 归一化后必须去重：同一只票可能同时带「黄金+珠宝加工」，两者都归到「黄金珠宝」，
        # 不去重会导致同一 symbol 在题材卡片里出现两次，触发 React key 冲突。
        themes = []
        for t in tags:
            nt = normalize_theme(t)
            if nt not in themes:
                themes.append(nt)
        if not themes:
            themes = [UNCLASSIFIED]
        stock_themes[rec.symbol] = themes
        theme_members[primary_of[rec.symbol]].append(rec)

    cards: list[dict] = []
    for theme, members in theme_members.items():
        cards.append(
            _build_card(
                theme=theme,
                members=members,
                stock_themes=stock_themes,
                enhance=enhance,
                board_index=board_index,
                prev_boards_map=prev_boards_map,
                prev_theme_stats=prev_theme_stats,
                market_max_boards=market_max_boards,
                active_days=active_days.get(theme, 1),
                daily_counts=daily_counts.get(theme, []),
                market_break_rate=market_break_rate,
                snapshot_map=snapshot_map,
                prev_theme_symbols=prev_theme_symbols.get(theme, set()),
                auction_gaps=auction_gaps,
            )
        )

    cards.sort(key=lambda c: -c["strength_score"])

    # ---- 7. 断板股：昨日连板、今日不在涨停池 ----
    today_symbols = {r.symbol for r in today_pool}
    broken = []
    if prev_pool:
        for rec in prev_pool:
            if (rec.consecutive_boards or 0) >= 2 and rec.symbol not in today_symbols:
                broken.append(
                    {
                        "symbol": rec.symbol,
                        "name": rec.name,
                        "prev_boards": rec.consecutive_boards,
                        "themes": [normalize_theme(t) for t in parse_theme_tags(rec.reason)],
                        "role": "断板",
                    }
                )

    return {
        "trade_date": trade_date.isoformat(),
        "prev_trade_date": (prev_pool[0].trade_date.isoformat() if prev_pool else None),
        "themes": cards,
        "broken_ladder": broken,
        "summary": {
            "limit_up_total": len(today_pool),
            "theme_count": len(cards),
            "market_max_boards": market_max_boards,
            "market_break_rate": market_break_rate,
            "top_theme": cards[0]["theme"] if cards else None,
        },
        "caveats": [
            "题材归因来自同花顺官方涨停原因，非交易时段数据为最近交易日收盘口径",
            "板块 3/5/10 日涨跌幅为东财字段序推断，未经 K 线交叉验证（board_multi_day_verified=false）",
            "炸板池不带题材字段，题材级只能用「开板过的涨停股占比」近似",
        ],
    }


# ---------------------------------------------------------------- 内部辅助


async def _em_enhancement_map(provider, trade_date: date) -> dict[str, dict]:
    """取东财涨停池的增强维度。失败时返回空 dict（降级而非报错）。"""
    target = _pick_provider(provider, "EastmoneyProvider")
    if target is None:
        return {}
    try:
        rows = await target.get_limit_up_pool(trade_date)
    except Exception as exc:
        log.warning("eastmoney limit-up enhancement unavailable: %s", exc)
        return {}
    return {r.symbol: r for r in rows}


#: 东财板块名的常见后缀，匹配前剥离（"黄金概念" 与 "黄金珠宝" 实际同源）
BOARD_SUFFIXES = ("概念", "板块", "指数", "产业")


def _strip_board_suffix(name: str) -> str:
    for suf in BOARD_SUFFIXES:
        if name.endswith(suf) and len(name) > len(suf) + 1:
            return name[: -len(suf)]
    return name


def match_board(theme: str, index: dict[str, dict]) -> dict | None:
    """把题材名匹配到东财板块。

    ths 题材标签体系与东财板块名体系不同（"转基因玉米" vs "转基因"、
    "黄金珠宝" vs "黄金概念"），精确匹配命中率极低，必须做剥离后缀后的双向包含匹配；
    多命中时取板块名最长的（更具体）。
    """
    if not theme or not index:
        return None
    if theme in index:
        return index[theme]
    t = _strip_board_suffix(theme)
    if t in index:
        return index[t]
    best: dict | None = None
    best_len = 0
    for bname, row in index.items():
        b = _strip_board_suffix(bname)
        if len(b) < 2 or len(t) < 2:
            continue
        if t == b:
            return row
        if t in b or b in t:
            if len(b) > best_len:
                best, best_len = row, len(b)
    return best


async def _board_index() -> dict[str, dict]:
    """板块指标索引：题材名 → 板块指标。行业优先占位，概念补足。

    走 board_flow 唯一入口（板块数据治理规则：任何模块不得自行请求东财板块
    接口），并复用其盘中 30s 缓存。main_net_inflow 以元为单位
    （main_net_yi×1e8），保持 news_persistence「净流入 X 元」的展示口径不变。
    失败返回空 dict → match_board 返回 None → 资金维度按缺失不参与判定（三态）。
    """
    index: dict[str, dict] = {}
    for kind in ("industry", "concept"):
        rows, errs = await board_flow.get_board_list(kind)
        if rows is None:
            log.warning("board list(%s) unavailable: %s", kind, "; ".join(errs))
            continue
        for row in rows:
            name = row.get("name")
            if not name:
                continue
            yi = row.get("main_net_yi")
            mapped = {
                "board_code": row.get("board_code"),
                "name": name,
                "kind": row.get("kind"),
                "change_pct": row.get("change_pct"),
                "main_net_inflow": yi * 1e8 if yi is not None else None,
                "main_net_ratio": row.get("main_net_ratio"),
            }
            if kind == "industry":
                index[name] = mapped
            else:
                index.setdefault(name, mapped)
    return index


async def _load_history(provider, trade_date: date, lookback_days: int) -> list[tuple[date, list]]:
    """并发回溯历史涨停池，返回 [(date, pool), ...]（由近及远）。

    只用同花顺：题材归因与连板数只有它的口径一致。
    并发而非串行——串行时单个慢源会把整体拖到分钟级。
    """
    ths = _pick_provider(provider, "ThsFuyaoProvider")
    if ths is None:
        return []
    dates = [trade_date - timedelta(days=i) for i in range(1, lookback_days + 1)]
    results = await asyncio.gather(
        *[ths.get_limit_up_pool(d) for d in dates], return_exceptions=True
    )
    out: list[tuple[date, list]] = []
    for d, res in zip(dates, results):
        if isinstance(res, Exception) or not res:
            log.debug("limit-up pool %s unavailable: %s", d, res)
            continue
        out.append((d, list(res)))
    return out


async def _market_break_rate(provider, trade_date: date, limit_up_count: int) -> float | None:
    """全市场炸板率 = 炸板数 / (涨停数 + 炸板数)。"""
    ths = _pick_provider(provider, "ThsFuyaoProvider")
    if ths is None:
        return None
    try:
        broken = await ths.get_limit_break_pool(trade_date)
    except Exception as exc:
        log.debug("limit-break pool unavailable: %s", exc)
        return None
    if not broken or limit_up_count <= 0:
        return None
    return round(len(broken) / (len(broken) + limit_up_count), 4)


async def _auction_gaps(provider, trade_date: date, pool: list) -> dict[str, float] | None:
    """当日集合竞价高开幅度 map（B2：龙头打分竞价强弱维度的数据源）。

    双守卫，任一不满足返回 None（打分缺口中性）：
    - trade_date 必须是**今天**——ths 竞价端点只有当日数据，历史回看若套上
      今日竞价就是跨日污染（本项目数据源日期回退的坑踩过多次）；
    - provider 需实现 get_auction_snapshot（mock 桩/旧链可能没有）。
    分批 ≤100 只（ths 单次上限）；非就绪条目（not_ready/suspended）跳过不标 gap。
    """
    if trade_date != date.today():
        return None
    fn = getattr(provider, "get_auction_snapshot", None)
    if fn is None:
        return None
    symbols: list[str] = []
    for rec in pool:
        if rec.symbol not in symbols:
            symbols.append(rec.symbol)
    out: dict[str, float] = {}
    for i in range(0, len(symbols), 100):
        batch = symbols[i : i + 100]
        try:
            rows = await fn(batch, stage="final")
        except Exception as exc:  # noqa: BLE001 - 竞价缺失只降级不阻断看板
            log.warning("auction snapshot unavailable: %s", exc)
            continue
        for r in rows or []:
            status = r.get("data_status")
            pct = r.get("auction_pct")
            if status not in (None, "ready", "final") or pct is None:
                continue
            try:
                out[r["symbol"]] = float(pct)
            except (TypeError, ValueError):
                continue
    return out or None


def _theme_stats(pool: list) -> dict[str, dict]:
    """统计一批涨停池中各题材的 (家数, 最高板)。"""
    stats: dict[str, dict] = {}
    for rec in pool:
        tags = parse_theme_tags(rec.reason)
        themes = [normalize_theme(t) for t in tags] or [UNCLASSIFIED]
        for th in themes:
            st = stats.setdefault(th, {"count": 0, "max_boards": 0})
            st["count"] += 1
            st["max_boards"] = max(st["max_boards"], rec.consecutive_boards or 0)
    return stats


def _build_card(
    *,
    theme: str,
    members: list,
    stock_themes: dict[str, list[str]],
    enhance: dict[str, dict],
    board_index: dict[str, dict],
    prev_boards_map: dict[str, int],
    prev_theme_stats: dict[str, dict],
    market_max_boards: int,
    active_days: int,
    daily_counts: list[tuple[str, int]],
    market_break_rate: float | None,
    snapshot_map: dict[str, dict] | None,
    prev_theme_symbols: set[str],
    auction_gaps: dict[str, float] | None = None,
) -> dict:
    """构建单张题材卡片。"""
    theme_max_boards = max((r.consecutive_boards or 0) for r in members)

    # ---- 连板天梯 ----
    ladder: list[dict] = []
    levels: dict[int, int] = defaultdict(int)
    seal_dist: dict[str, int] = defaultdict(int)
    reopen_count = 0
    turnover_rates: list[float] = []
    amounts: list[float] = []
    caps: list[float] = []
    has_middle_weight = False
    # 封单质量（A2 下半）：早封率样本 / 留存率成对样本
    early_samples: list[int | None] = []
    retention_pairs: list[tuple[float | None, float | None]] = []

    for rec in members:
        boards = rec.consecutive_boards or 0
        levels[boards] += 1
        em = enhance.get(rec.symbol)
        cap = (em.float_market_cap if em else None)
        bc = (em.break_count if em else None) or 0
        if bc > 0:
            reopen_count += 1
        fst = (em.first_seal_time if em else None) or rec.first_seal_time
        phase = seal_phase(fst)
        early_samples.append(_parse_hhmmss(fst))
        retention_pairs.append((rec.seal_amount, rec.max_seal_money))
        if phase:
            seal_dist[phase] += 1
        if em and em.turnover_rate:
            turnover_rates.append(em.turnover_rate)
        if em and em.amount:
            amounts.append(em.amount)
        if cap:
            caps.append(cap)
            if cap >= MIDDLE_WEIGHT_MIN_CAP:
                has_middle_weight = True

        role = classify_role(
            boards=boards,
            theme_max_boards=theme_max_boards,
            market_max_boards=market_max_boards,
            prev_boards=prev_boards_map.get(rec.symbol),
            float_market_cap=cap,
            seal_phase_value=phase,
            break_count=bc,
        )
        # 梯队联动归属后，卡片成员就是「主题材为本题材」的股票（assign_primary_themes），
        # 不再有从属行——旧版在这里按 rank_index 判 is_primary，导致同一梯队拆散展示。
        primary = True
        # 龙头前瞻打分 + 个股情绪：在首板/二板阶段就给出偏向，而不是等涨到高位
        # 再用高度倒推（那是结果归因）。第二高判定用于区分前排与跟风。
        second_highest = boards == theme_max_boards - 1 and theme_max_boards >= 3
        dragon = dragon_score(
            boards=boards,
            seal_amount=rec.seal_amount,
            float_market_cap=cap,
            first_seal_time=fst,
            break_count=(em.break_count if em else None),
            turnover_rate=(em.turnover_rate if em else None),
            is_theme_highest=(boards == theme_max_boards and theme_max_boards >= 2),
            is_second_highest=second_highest,
            boards_stat=rec.boards_stat,
            auction_gap_pct=(auction_gaps or {}).get(rec.symbol),
        )
        senti = stock_sentiment(
            boards=boards,
            seal_amount=rec.seal_amount,
            float_market_cap=cap,
            first_seal_time=fst,
            last_seal_time=(em.last_seal_time if em else None) or rec.last_seal_time,
            break_count=(em.break_count if em else None),
            turnover_rate=(em.turnover_rate if em else None),
            is_theme_highest=(boards == theme_max_boards and theme_max_boards >= 2),
            is_primary_theme=bool(primary),
            boards_stat=rec.boards_stat,
        )
        ladder.append(
            {
                "symbol": rec.symbol,
                "name": rec.name,
                "role": role,
                "is_primary": bool(primary),
                "other_themes": [t for t in stock_themes.get(rec.symbol, []) if t != theme],
                "boards": boards,
                "boards_stat": rec.boards_stat,
                "seal_amount": rec.seal_amount,
                "break_count": bc,
                "turnover_rate": (em.turnover_rate if em else None),
                "float_market_cap": cap,
                "amount": (em.amount if em else None),
                "industry_board": (em.industry_board if em else None),
                "first_seal_time": fst,
                "last_seal_time": (em.last_seal_time if em else None) or rec.last_seal_time,
                "seal_phase": phase,
                "change_pct": rec.change_pct,
                "dragon": dragon,
                "sentiment": senti,
            }
        )

    ladder.sort(key=lambda x: (-(x["boards"] or 0), ROLE_ORDER.get(x["role"], 9), -(x["seal_amount"] or 0)))

    # ---- 强度指标 ----
    total = len(members)
    reopen_rate = round(reopen_count / total, 4) if total else 0.0
    completeness = echelon_completeness(dict(levels), theme_max_boards)
    seal_quality = seal_quality_score(dict(seal_dist), sum(seal_dist.values()))

    # 接力赚钱效应：前一日「该题材」涨停股今日涨跌幅中位数。
    # 必须用题材子集而非全市场——全市场口径会让所有题材溢价都等于大盘中位数，
    # 完全失去区分度（曾因此所有题材都显示 1.9%）。
    premium_median: float | None = None
    premium_samples = 0
    if snapshot_map and prev_theme_symbols:
        vals: list[float] = []
        for sym in prev_theme_symbols:
            row = snapshot_map.get(sym)
            if not row:
                continue
            pct = row.get("change_pct")
            if pct is None:
                continue
            vals.append(float(pct))
        if vals:
            premium_median = round(_median(vals) or 0.0, 2)
            premium_samples = len(vals)

    prev_stat = prev_theme_stats.get(theme)
    stage, stage_basis = judge_theme_stage(
        limit_up_count=total,
        max_boards=theme_max_boards,
        prev_limit_up_count=(prev_stat or {}).get("count"),
        prev_max_boards=(prev_stat or {}).get("max_boards"),
        reopen_rate=reopen_rate,
        completeness=completeness,
        premium_median=premium_median,
    )

    score = theme_strength_score(
        limit_up_count=total,
        max_boards=theme_max_boards,
        completeness=completeness,
        reopen_rate=reopen_rate,
        seal_quality=seal_quality,
        active_days=active_days,
    )

    tier, tier_basis = strength_tier(
        formation=formation_level(total),
        stage=stage,
        max_boards=theme_max_boards,
        reopen_rate=reopen_rate,
        premium_median=premium_median,
    )
    sort_basis = (
        f"涨停 {total} 家 · 最高 {theme_max_boards} 板 · 完整度 {completeness:.0%}"
        f" · 开板率 {reopen_rate:.0%} · 活跃 {active_days} 天"
        + (f" · 接力溢价 {premium_median:+.2f}%" if premium_median is not None else "")
    )

    note, risks = theme_health_note(
        theme=theme,
        stage=stage,
        limit_up_count=total,
        max_boards=theme_max_boards,
        completeness=completeness,
        missing_levels=[lv for lv in range(2, theme_max_boards) if (levels.get(lv) or 0) <= 0],
        has_middle_weight=has_middle_weight,
        reopen_rate=reopen_rate,
        premium_median=premium_median,
    )
    formation = formation_level(total)

    board = match_board(theme, board_index)
    ladder_leader = next((r for r in ladder if r["role"] in ("空间板", "龙头")), None)
    middle_weights = [r for r in ladder if r["role"] == "中军"]
    candidates = [r for r in ladder if r["role"] in ("补涨", "反包")]

    # 炒作内核 + 持续性：回答「这个题材在炒什么」和「还值不值得跟」。
    # 位置判据此处先用 active_days 代理；路由层用 ths 官方板块 K 线覆盖 chg_5d 后
    # 会调 apply_position_with_5d 把位置升级为真实区间涨幅（P1-6，见 market.py）。
    reasons = [r.reason for r in members if r.reason]
    core = theme_core(theme, reasons)
    persistence = news_persistence(
        theme=theme,
        core_type=core["type"],
        limit_up_count=total,
        active_days=active_days,
        board_change_pct=(board or {}).get("change_pct"),
        main_net_inflow=(board or {}).get("main_net_inflow"),
        has_second_board=theme_max_boards >= 2,
        reasons=reasons,
    )

    return {
        "theme": theme,
        "raw_tags": sorted({t for r in members for t in parse_theme_tags(r.reason)}) or [],
        "is_unclassified": theme == UNCLASSIFIED,
        "strength_score": score,
        "strength_tier": tier,
        "tier_basis": tier_basis,
        "sort_basis": sort_basis,
        "stage": stage,
        "stage_basis": stage_basis,
        "formation": formation,
        "health_note": note,
        "risks": risks,
        "core": core,
        "persistence": persistence,
        "board": board,
        "board_matched": board is not None,
        "performance": {
            "limit_up_count": total,
            "max_boards": theme_max_boards,
            "echelon_levels": {str(k): v for k, v in sorted(levels.items())},
            "echelon_completeness": completeness,
            "has_succession": theme_max_boards >= 2,
            "reopen_rate": reopen_rate,
            "seal_success_rate": round(1 - reopen_rate, 4),
            "market_break_rate": market_break_rate,
            "seal_time_distribution": dict(seal_dist),
            "seal_quality": seal_quality,
            "early_seal_rate": early_seal_rate(early_samples),
            "seal_retention": seal_retention_rate(retention_pairs),
            "turnover_median": round(_median(turnover_rates), 2) if turnover_rates else None,
            "amount_total": round(sum(amounts), 2) if amounts else None,
            "float_cap_median": round(_median(caps), 2) if caps else None,
            "has_middle_weight": has_middle_weight,
            "active_days": active_days,
            "daily_limit_up_counts": daily_counts,
            "premium_median": premium_median,
            "premium_samples": premium_samples,
        },
        "ladder": ladder,
        "leaders": {
            "main": (
                {
                    "symbol": ladder_leader["symbol"],
                    "name": ladder_leader["name"],
                    "boards": ladder_leader["boards"],
                    "role": ladder_leader["role"],
                }
                if ladder_leader
                else None
            ),
            "middle_weights": [
                {"symbol": r["symbol"], "name": r["name"], "boards": r["boards"]} for r in middle_weights
            ],
            "candidates": [
                {
                    "symbol": r["symbol"],
                    "name": r["name"],
                    "boards": r["boards"],
                    "role": r["role"],
                    "reason": ("连板中断后再度封板" if r["role"] == "反包" else "龙头打出空间后的低位替代"),
                }
                for r in candidates
            ],
        },
    }
