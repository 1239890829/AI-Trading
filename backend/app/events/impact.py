"""事件影响力视图（system-review §六.4 用户拍板落地）。

四级分类（拍板口径）：国际时事 / 国家政策 / 市场热点 / 原材料涨价。
三级影响力（拍板口径）：
- L1 必上：政策发布（已落地/拟议非传闻）、行业级涨价、国际重大事件（一线权威以上）
- L2 选上：龙头个股业绩预告/重大合同等——有方向、事实类、带标的链
- L3 不上：日常经营、人事变动、无实质影响的公告/传闻/纯解读

实现为既有 EventCard 之上的**派生视图**：不重建抽取管道，复用 classify_category /
source_tier / certainty / directions，只补分类映射与分级规则（纯函数可测）。
"""

from __future__ import annotations

import re

#: 原材料涨价（优先级最高：与政策/国际重叠时归材料，如「出口关税推动涨价」）
_MATERIAL = re.compile(r"涨价|提价|价格上调|上调[\s\S]{0,6}价格|涨价函")
#: 国际时事关键词
_INTERNATIONAL = re.compile(
    r"美国|美联储|海外|国际|全球|地缘|欧盟|日本|韩国|印度|中东|俄罗斯|乌克兰|外围|美元"
)
#: 人事变动/日常经营 → L3
_PERSONNEL = re.compile(r"人事|辞职|聘任|任免|离职|更名|办公地址|澄清|风险提示| 问询 ")

FOUR_LABEL = {
    "international": "国际时事",
    "policy": "国家政策",
    "hot": "市场热点",
    "material": "原材料涨价",
}

# ---------------------------------------------------------------- 事件标签（2026-09-04 任务③）
# 四级分类只回答「事件从哪来」，不回答「事件是什么类型」——业绩/公告/异动/资金
# 是同花顺等产品的常用维度，当前体系覆盖不足，按标题规则派生多标签（一条事件可挂多个）。

_TAG_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("业绩", re.compile(r"中报|年报|季报|业绩|净利|营收|亏损|预增|预减|扭亏|同比[增下]")),
    ("公告", re.compile(r"定增|募资|回购|增持|减持|重组|中标|批复|同意注册|招股|分红|股权激励|股东")),
    ("异动", re.compile(r"涨停|炸板|连板|封板|异动|龙虎榜|直线|天梯|创历史新高|新高")),
    ("资金", re.compile(r"主力资金|净流入|净流出|加仓|融资|北向|资金大手笔|机构持仓")),
    ("行业", re.compile(r"概念|板块|行业|产业链|上游|下游")),
]

TAG_LABELS = ["业绩", "公告", "异动", "资金", "行业"]


def derive_tags(title: str | None, category: str | None) -> list[str]:
    """派生事件标签（多标签）。纯规则、可测；无命中 → 空列表（不臆造）。"""
    t = title or ""
    tags = [name for name, pat in _TAG_PATTERNS if pat.search(t)]
    if not tags and category == "corporate":
        tags = ["公告"]  # 公司类事件缺标签时归公告（保守兜底，不编业绩/异动）
    return tags


def classify_four(title: str | None, category: str | None) -> str:
    """既有六分类（policy/statement/data/rumor/corporate/other）→ 拍板四分类。"""
    t = title or ""
    if _MATERIAL.search(t):
        return "material"
    if category == "policy":
        return "policy"
    if _INTERNATIONAL.search(t):
        return "international"
    return "hot"


def impact_level(title: str | None, *, four: str, certainty: str | None,
                 fact_kind: str | None, source_tier: int | None, n_directions: int) -> str:
    """影响力三级。L3 不上（默认不出现在 Tab 中），绝不把传闻拔高成 L1。"""
    t = title or ""
    if _PERSONNEL.search(t):
        return "L3"
    rumor = certainty == "rumor"
    if four == "material" and not rumor:
        return "L1"
    if four == "policy" and not rumor:
        return "L1"
    if four == "international" and not rumor and (source_tier or 0) >= 3:
        return "L1"
    # L2：事实类且抽出了方向/标的链（业绩预告、重大合同、行业事件等）
    if fact_kind == "fact" and n_directions > 0:
        return "L2"
    return "L3"
