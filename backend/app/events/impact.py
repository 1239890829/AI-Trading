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
