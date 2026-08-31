"""事件抽取规则引擎（linkage-design §4.3-4.4，E1 最小闭环）。

原则（延续项目范式）：
- **规则先行、可解释**：每个判定带 basis；LLM 只是后续增强层（app/news/llm.py
  同款未接入策略），未接入前全部规则路径可用。
- **不臆造**：题材实体只在官方目录名 / 显式人工别名表内匹配；方向词典没有
  命中动词的实体给 direction=0（关联待判），不猜利好利空。
- **方向成对分析**：同一事件可对 A 题材 +1、对 B 题材 -1（多条规则各自命中）。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

# ---------------------------------------------------------------- 人工维护表（显式，非静默推断）

#: 实体别名表：标题关键词 → 官方目录题材名。人工维护（维护成本可控、可审计），
#: 命中时 basis 会写明 matched_by=alias，与目录名直接命中区分。
#: 注意宽词（如"黄金"）会带来误命中风险——但方向词典未命中时 direction=0 只记关联，
#: 且 alias 命中全部可审计，风险可控。
ENTITY_ALIASES: dict[str, str] = {
    "LPDDR": "存储芯片",
    "DRAM": "存储芯片",
    "HBM": "存储芯片",
    "NAND": "存储芯片",
    "存储涨价": "存储芯片",
    "英伟达": "算力",
    "算力租赁": "算力租赁",
    "CPO": "CPO",
    "信创": "信创",
    "低空经济": "低空经济",
    "固态电池": "固态电池",
    "创新药": "创新药",
    "黄金": "黄金概念",
    "贵金属": "黄金概念",
    "稀土": "稀土永磁",
}

#: 方向词典（v1）：动词/事件词 → 方向。题材相关的国产替代对冲规则单列。
_POSITIVE = re.compile(r"量产|首发|突破|获批|中标|签约|预增|涨价|回购|增持|大单|订单|合作")
_NEGATIVE = re.compile(r"禁止|禁令|制裁|限制|管制|处罚|调查|减持|利空|大跌|暴跌|下挫|跳水|重挫")
#: 禁令/制裁类利空对「国产替代属性」题材反而是利好（方向成对分析的规则化表达）
_FLIP_POSITIVE = re.compile(r"国产|自主|自研|信创|替代|可控")
_FLIP_NEGATIVE = re.compile(r"租赁|海外|转口|出海")
_RUMOR = re.compile(r"据悉|传闻|市场消息|知情人士|小作文")
_PROPOSED = re.compile(r"拟|将|计划|寻求|考虑|酝酿|讨论|草案")
_OPINION = re.compile(r"或|恐|有望|预计|分析人士|机构认为|或将其")
_CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    ("policy", re.compile(r"禁止|禁令|制裁|限制|管制|监管|法案|关税|商务部|证监会|财政部")),
    ("statement", re.compile(r"讲话|发言|鹰派|鸽派|演讲|表态|听证")),
    ("data", re.compile(r"CPI|GDP|PMI|财报|业绩预告|库存|进出口|非农")),
    ("rumor", re.compile(r"据悉|传闻|市场消息|知情人士")),
    ("corporate", re.compile(r"量产|中标|签约|获批|回购|增持|减持|业绩|公告|复牌|停牌")),
]
#: 半衰期模板（小时）：政策 336 / 发言 48 / 数据 24 / 传闻 2 / 公司 72 / 其他 48
HALF_LIFE: dict[str, int] = {
    "policy": 336,
    "statement": 48,
    "data": 24,
    "rumor": 2,
    "corporate": 72,
    "other": 48,
}


def classify_source_tier(source: str | None, *, is_announcement: bool = False) -> int:
    """来源分级 1-5：官方公告 5 / 一线权威 4 / 主流财经 3 / 聚合转载 2 / 自媒体 1。

    Provider 来源无法区分原发与转载时按主流财经（3）处理——宁可保守。
    """
    if is_announcement:
        return 5
    s = (source or "").lower()
    if any(k in s for k in ("证券时报", "财联社", "新华社", "央视", "上证报", "中国证券报", "券商中国")):
        return 4
    if any(k in s for k in ("eastmoney", "东财", "同花顺", "ths", "新浪", "sina", "腾讯")):
        return 3
    return 2


def classify_certainty(title: str) -> tuple[str, str]:
    """(fact_kind, certainty)：事实/解读/传闻 三档 + 已落地/拟议/传闻 三档。"""
    if _RUMOR.search(title):
        return "rumor", "rumor"
    if _PROPOSED.search(title):
        return "fact", "proposed"
    if _OPINION.search(title):
        return "opinion", "done"
    return "fact", "done"


def classify_category(title: str) -> str:
    for name, pattern in _CATEGORIES:
        if pattern.search(title):
            return name
    return "other"


def title_fingerprint(title: str) -> str:
    """归一化标题指纹：去空白/标点后 sha1——多源转载同一事件只注册一次。"""
    normalized = re.sub(r"[\s\u3000：:，,。.!！?？\"'（）()【】\[\]、·]+", "", title or "")
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def extract_directions(title: str, theme_names: list[str]) -> list[dict]:
    """标题 → 方向映射行（纯函数）。

    实体匹配：官方目录题材名包含命中（优先）+ 人工别名表（matched_by=alias）。
    方向：利好/利空动词词典 + 禁令类对国产替代题材的对冲规则；无动词命中 →
    direction=0（关联待判）。每行带 basis。
    """
    matched: dict[str, dict] = {}
    for name in theme_names:
        if name and name in title:
            matched.setdefault(name, {"target": name, "matched_by": "name", "hit": name})
    for keyword, name in ENTITY_ALIASES.items():
        if keyword in title and name not in matched:
            matched[name] = {"target": name, "matched_by": "alias", "hit": keyword}

    negative = _NEGATIVE.search(title)
    positive = _POSITIVE.search(title)
    rows: list[dict] = []
    for name, info in matched.items():
        direction, strength = 0, 1
        chain = ""
        basis = f"标题命中题材「{info['target']}」（{info['matched_by']}:{info['hit']}）"
        if negative:
            word = negative.group(0)
            if _FLIP_POSITIVE.search(name):
                direction, strength = 1, 2
                chain = f"「{word}」限制外部供给 → 国产替代需求抬升"
                basis = f"命中利空词「{word}」+ 题材具国产替代属性（{_FLIP_POSITIVE.pattern}）→ 对冲为利好"
            elif _FLIP_NEGATIVE.search(name):
                direction, strength = -1, 2
                chain = f"「{word}」直接约束该题材"
                basis = f"命中利空词「{word}」+ 题材具受约束属性（{_FLIP_NEGATIVE.pattern}）→ 利空"
            else:
                direction, strength = -1, 2
                chain = f"「{word}」对该题材构成压力"
                basis = f"命中利空词「{word}」→ 利空"
        elif positive:
            word = positive.group(0)
            direction, strength = 1, 2
            chain = f"「{word}」直接利好该题材"
            basis = f"命中利好词「{word}」→ 利好"
        rows.append({
            "target_type": "theme",
            "target": name,
            "direction": direction,
            "strength": strength,
            "chain": chain,
            "basis": basis,
            "matched_by": info["matched_by"],
        })
    rows.sort(key=lambda r: (-abs(r["direction"]) * r["strength"], r["target"]))
    return rows


def extract_symbol_direction(title: str, symbol: str) -> dict | None:
    """新闻来源标的的方向行（target_type="symbol"）。

    为什么必须有这一行：新闻是**按标的拉取**的（source_symbol 就是权威关联），
    但此前 extract_directions 只产 theme 方向行，而选股消息面评分只认
    symbol 方向行（防题材过度外推）——两段各自合理，拼起来互斥，导致
    消息面评分永远中性（2026-08-31 实测：事件 46 条、组合成员零命中）。

    方向判定复用同一套利好/利空词典；无动词命中 → direction=0（关联待判，
    仍然计数命中但不加分不扣分）。
    """
    if not symbol or not symbol.isdigit() or len(symbol) != 6:
        return None
    negative = _NEGATIVE.search(title)
    positive = _POSITIVE.search(title)
    direction, strength = 0, 1
    chain = ""
    if negative:
        word = negative.group(0)
        direction, strength = -1, 2
        chain = f"来源标的新闻命中利空词「{word}」→ 利空"
    elif positive:
        word = positive.group(0)
        direction, strength = 1, 2
        chain = f"来源标的新闻命中利好词「{word}」→ 利好"
    else:
        chain = "来源标的关联（标题无方向词，待判）"
    return {
        "target_type": "symbol",
        "target": symbol,
        "direction": direction,
        "strength": strength,
        "chain": chain,
        "basis": f"新闻来源标的 {symbol}（东财按标的拉取，权威关联）；{chain}",
        "matched_by": "source",
    }


def build_event(title: str, *, source: str | None = None, url: str | None = None,
                published_at: datetime | None = None, source_symbol: str | None = None,
                is_announcement: bool = False, theme_names: list[str] | None = None) -> dict:
    """新闻行 → EventCard + directions 组合 dict（纯函数入口）。"""
    fact_kind, certainty = classify_certainty(title)
    category = classify_category(title)
    return {
        "fingerprint": title_fingerprint(title),
        "title": title.strip(),
        "url": url,
        "source": source or "",
        "source_tier": classify_source_tier(source, is_announcement=is_announcement),
        "published_at": published_at or datetime.now(timezone.utc),
        "fact_kind": fact_kind,
        "certainty": certainty,
        "category": category,
        "half_life_hours": HALF_LIFE.get(category, HALF_LIFE["other"]),
        "source_symbol": source_symbol,
        "status": "active",
        "directions": extract_directions(title, theme_names or [])
        + ([extract_symbol_direction(title, source_symbol)] if source_symbol else []),
    }
