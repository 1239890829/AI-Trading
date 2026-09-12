"""事件抽取规则引擎（architecture-design §1，E1 最小闭环）。

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
from datetime import datetime

from app.events.chains import match_chains
from app.core.bjtime import BJ_TZ, beijing_now_naive  # S2-8 时区收敛

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

#: 方向词典（v2，2026-09-07 G3 修订）：动词/事件词 → 方向。题材相关的国产替代对冲规则单列。
#: v2 实测驱动（hotspot-pipeline §3 四条样例）：「受益」缺失导致厄尔尼诺→电力/化肥
#: 命中题材却 direction=0；「进展」缺失导致长存论文消息方向判不出。
#: v3（2026-09-09，北京十五五规划案例驱动）：政策/规划类利好措辞此前完全缺失——
#: 「北京：加快发展商业航天产业」既判不出 category=policy（半衰期按 other 48h 而非 336h），
#: 也判不出利好方向，导致政策利好进系统后是条僵尸事件。补规划/印发/扶持类政务动词。
#: ⚠️ 宽词风险：「推动/支持/建设」单独出现不构成利好定论——故仅当标题含政策主体
#: （规划/印发/国务院/部委/十五五等）时才按利好计，见 _POLICY_BODY。
_POSITIVE = re.compile(r"量产|首发|突破|获批|中标|签约|预增|涨价|回购|增持|大单|订单|合作|受益|进展|改善|回暖"
                       r"|加快发展|大力发展|加快推进|培育|扶持|补贴|减税|降费|专项资金")
_NEGATIVE = re.compile(r"禁止|禁令|制裁|限制|管制|处罚|调查|减持|利空|大跌|暴跌|下挫|跳水|重挫")
#: 禁令/制裁类利空对「国产替代属性」题材反而是利好（方向成对分析的规则化表达）
_FLIP_POSITIVE = re.compile(r"国产|自主|自研|信创|替代|可控")
_FLIP_NEGATIVE = re.compile(r"租赁|海外|转口|出海")
_RUMOR = re.compile(r"据悉|传闻|市场消息|知情人士|小作文")
#: v2：论文/实验室类措辞 = 研究阶段而非落地（hotspot §3.3：论文级消息不宜给确定性方向）
_PROPOSED = re.compile(r"拟|将|计划|寻求|考虑|酝酿|讨论|草案|论文|实验室|理论上|研究表明|学术")
_OPINION = re.compile(r"或|恐|有望|预计|分析人士|机构认为|或将其")
#: 政策主体词（2026-09-09 补）：出现这些词才把「支持/推动/建设」类宽泛表述算作政策事件，
#: 避免把普通公司新闻误判成政策利好（category 决定半衰期，误判会让短消息长期滞留）。
_POLICY_BODY = re.compile(r"规划|印发|国务院|发改委|工信部|科技部|财政部|商务部|证监会|央行|政治局|常委会|部委"
                          r"|十五五|十四五|指导意见|实施方案|若干措施|政策|纲要|条例|试点示范")
_CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    # 2026-09-09 修订：原 policy 正面只认「禁止/制裁/监管」类（利空监管），
    # 「印发规划/加快发展」这类正面产业政策完全漏判 → category=other。
    ("policy", re.compile(r"禁止|禁令|制裁|限制|管制|监管|法案|关税|商务部|证监会|财政部"
                          r"|规划|印发|国务院|发改委|工信部|科技部|政治局|十五五|十四五"
                          r"|指导意见|实施方案|若干措施|纲要|条例|试点示范")),
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


def _judge_text(title: str, summary: str | None) -> str:
    """判定文本 = 标题 + 摘要（摘要非空且非标题子串时拼接）。

    retro P0-4：东财快讯标题常被截断（如丢掉「印发《…规划》」），政策主体/
    方向词落在摘要里 → 只用标题判 category/方向会漏判。摘要参与判定；
    「摘要就是标题复述」时用子串判据跳过（不重复拼，语义清晰）。
    """
    s = (summary or "").strip()
    if s and s not in title:
        return f"{title} {s}"
    return title


def classify_category(title: str, summary: str | None = None) -> str:
    text = _judge_text(title, summary)
    for name, pattern in _CATEGORIES:
        if pattern.search(text):
            return name
    return "other"


def title_fingerprint(title: str) -> str:
    """归一化标题指纹：去空白/标点后 sha1——多源转载同一事件只注册一次。"""
    normalized = re.sub(r"[\s\u3000：:，,。.!！?？\"'（）()【】\[\]、·]+", "", title or "")
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 判定状态机（2026-09-09）

#: 待判收敛时限（小时）：超过仍未判出方向 → 收敛为「中性」，不再无限期停留待判。
#: 取值理由：政策/公司类消息的机会窗口通常在一个交易日内，6h 覆盖盘中+盘后一段；
#: 传闻类半衰期仅 2h，会被 expired 先行接管。
PENDING_TIMEOUT_HOURS = 6

JUDGE_STATUS_LABEL: dict[str, str] = {
    "judged": "已判定",
    "pending": "待判",
    "neutral": "中性（待判超时收敛）",
    "expired": "已过期",
}


def judge_state(
    published_at: "datetime | None",
    directions: "list[dict] | None",
    *,
    now: "datetime | None" = None,
    half_life_hours: "int | None" = None,
) -> dict:
    """事件判定状态机（纯函数，读时派生——不落库、免迁移）。

    状态流转：
    - 入库即由规则引擎判定一次（同步，judged_at=published_at）；
    - 有任一 direction≠0 → **judged**（已判定，方向已定）；
    - 无方向行且未超时 → **pending**（待判，仍可被 LLM/人工二次判定覆盖）；
    - 无方向行且超过 PENDING_TIMEOUT_HOURS → **neutral**（收敛为中性）；
      这是「避免长期停留待判」的关键一跳：宁可显式说"判不出、按中性"，
      也不让一条事件永远挂在待判队列里污染展示与统计（三态纪律的时态版）。
    - 任一状态只要 age ≥ half_life → **expired**（过期不再参与评分）。

    :returns: {status, judged_at, reason, age_hours}
    """
    dirs = directions or []
    # 2026-09-09 时区口径统一：事件时间为北京 naive（见 beijing_now_naive docstring）。
    # 传入 aware 时间统一转北京；naive 直接视为北京（不再按 UTC 解释）。
    _BJ = BJ_TZ
    now = now or beijing_now_naive()
    pub = published_at
    if pub is not None and pub.tzinfo is not None:
        pub = pub.astimezone(_BJ).replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.astimezone(_BJ).replace(tzinfo=None)
    age_h = round(((now - pub).total_seconds() / 3600), 2) if pub else None

    has_dir = any(int(d.get("direction") or 0) != 0 for d in dirs)
    judged_at = pub

    if half_life_hours and age_h is not None and age_h >= float(half_life_hours):
        return {"status": "expired", "judged_at": judged_at, "age_hours": age_h,
                "reason": f"超过半衰期 {half_life_hours}h（age {age_h}h）→ 过期"}
    if has_dir:
        words = sorted({d.get("chain") or "" for d in dirs if int(d.get("direction") or 0) != 0})
        return {"status": "judged", "judged_at": judged_at, "age_hours": age_h,
                "reason": "规则引擎已判出方向：" + "；".join(w for w in words if w)[:120]}
    if age_h is not None and age_h >= PENDING_TIMEOUT_HOURS:
        from datetime import timedelta as _td

        settled_at = pub + _td(hours=PENDING_TIMEOUT_HOURS) if pub else now
        return {"status": "neutral", "judged_at": settled_at, "age_hours": age_h,
                "reason": f"待判超过 {PENDING_TIMEOUT_HOURS}h 未出方向 → 收敛为中性（不计入利好/利空）"}
    return {"status": "pending", "judged_at": judged_at, "age_hours": age_h,
            "reason": "规则引擎未命中方向词，待判（超时将自动收敛为中性）"}


_NAME_SUFFIX = re.compile(r"(概念|板块|产业|指数)$")


def _name_stem(name: str) -> str:
    """目录名词干：去「概念/板块/产业/指数」后缀（「芯片概念」→「芯片」）。

    短词干（<2 字）不参与匹配——单字/双字会造成大面积误命中（如「银行」）。
    """
    stem = _NAME_SUFFIX.sub("", name or "").strip()
    return stem if len(stem) >= 2 else ""


def extract_directions(title: str, theme_names: list[str], summary: str | None = None) -> list[dict]:
    """标题+摘要 → 方向映射行（纯函数）。

    实体匹配：官方目录题材名包含命中（优先）+ 人工别名表（matched_by=alias）。
    方向：利好/利空动词词典 + 禁令类对国产替代题材的对冲规则；无动词命中 →
    direction=0（关联待判）。每行带 basis。

    判定文本 = _judge_text(title, summary)：摘要参与实体与方向词匹配
    （retro P0-4，标题截断漏判的补偿）。
    """
    text = _judge_text(title, summary)
    matched: dict[str, dict] = {}
    for name in theme_names:
        if not name:
            continue
        if name in text:
            matched.setdefault(name, {"target": name, "matched_by": "name", "hit": name})
            continue
        # 2026-09-09：目录名带「概念/板块/产业/指数」后缀时，标题里通常只有词干
        # （「美国…对中国芯片出口管制」命中不了目录名「芯片概念」）→ 补词干匹配。
        # basis 里写明 matched_by=name-stem，与整名命中区分，便于审计误命中。
        stem = _name_stem(name)
        if stem and stem != name and stem in text:
            matched.setdefault(name, {"target": name, "matched_by": "name-stem", "hit": stem})
    for keyword, name in ENTITY_ALIASES.items():
        if keyword in text and name not in matched:
            matched[name] = {"target": name, "matched_by": "alias", "hit": keyword}

    negative = _NEGATIVE.search(text)
    positive = _POSITIVE.search(text)
    rows: list[dict] = []
    for name, info in matched.items():
        direction, strength = 0, 1
        chain = ""
        basis = f"文本命中题材「{info['target']}」（{info['matched_by']}:{info['hit']}）"
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


def extract_symbol_direction(title: str, symbol: str, summary: str | None = None) -> dict | None:
    """新闻来源标的的方向行（target_type="symbol"）。

    为什么必须有这一行：新闻是**按标的拉取**的（source_symbol 就是权威关联），
    但此前 extract_directions 只产 theme 方向行，而选股消息面评分只认
    symbol 方向行（防题材过度外推）——两段各自合理，拼起来互斥，导致
    消息面评分永远中性（2026-08-31 实测：事件 46 条、组合成员零命中）。

    方向判定复用同一套利好/利空词典；无动词命中 → direction=0（关联待判，
    仍然计数命中但不加分不扣分）。判定文本 = _judge_text(title, summary)
    （retro P0-4）。
    """
    if not symbol or not symbol.isdigit() or len(symbol) != 6:
        return None
    text = _judge_text(title, summary)
    negative = _NEGATIVE.search(text)
    positive = _POSITIVE.search(text)
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
        chain = "来源标的关联（文本无方向词，待判）"
    return {
        "target_type": "symbol",
        "target": symbol,
        "direction": direction,
        "strength": strength,
        "chain": chain,
        "basis": f"新闻来源标的 {symbol}（东财按标的拉取，权威关联）；{chain}",
        "matched_by": "source",
    }


def extract_board_direction(
    title: str, theme_name: str, board_name: str, board_code: str, summary: str | None = None
) -> dict:
    """东财板块关联方向行（target_type="theme"，matched_by="board"）。

    retro P0-3 后半：快讯 ``stockList`` 里 ``90.BKxxxx``（板块代码）映射成
    ths 题材名，让「只有板块代码、无个股代码」的快讯（实测约 35%）也能关联
    到系统题材——否则这类快讯只有 source_symbol=None、又没题材词，是条僵尸事件。

    方向判定复用同一套利好/利空词典 + 国产替代对冲规则；无动词命中 →
    direction=0（关联待判，不猜方向）。与 extract_directions 的差异仅在于
    关联来源是 stockList 的板块代码（权威已知），而非标题文本命中。
    """
    text = _judge_text(title, summary)
    negative = _NEGATIVE.search(text)
    positive = _POSITIVE.search(text)
    direction, strength = 0, 1
    chain = ""
    if negative:
        word = negative.group(0)
        if _FLIP_POSITIVE.search(theme_name):
            direction, strength = 1, 2
            chain = f"「{word}」限制外部供给 → 国产替代需求抬升"
        elif _FLIP_NEGATIVE.search(theme_name):
            direction, strength = -1, 2
            chain = f"「{word}」直接约束该题材"
        else:
            direction, strength = -1, 2
            chain = f"「{word}」对该题材构成压力"
    elif positive:
        word = positive.group(0)
        direction, strength = 1, 2
        chain = f"「{word}」直接利好该题材"
    return {
        "target_type": "theme",
        "target": theme_name,
        "direction": direction,
        "strength": strength,
        "chain": chain,
        "basis": (
            f"快讯关联东财板块「{board_name}」（stockList 90.{board_code}）→ 题材「{theme_name}」；"
            f"{chain or '文本无方向词，关联待判'}"
        ),
        "matched_by": "board",
    }


def _dedupe_chain_rows(rows: list[dict], chain_rows: list[dict]) -> list[dict]:
    """合并传导链行，与已有 target 语义重复的跳过。

    「互相包含」判定零依赖（events 不 import services 层）：alias「算力」⊂
    链行「东数西算(算力)」视为同一关联，保留先到的 alias/name 行。
    """
    out = list(rows)
    for cr in chain_rows:
        if any(_dedupe_same(cr["target"], r["target"]) for r in out):
            continue
        out.append(cr)
    return out


def _dedupe_same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (a in b or b in a)


def dedupe_directions(directions: list[dict]) -> list[dict]:
    """按 ``(target_type, target)`` 精确去重，**保留先到者**（2026-09-10 修）。

    为什么必须有：``event_direction`` 上有 ``UNIQUE(event_id, target_type, target)``，
    而一条事件的关联来源有多个（文本命中题材名 / 传导链 / 来源标的 / 东财板块映射），
    同一题材可能被**两个来源同时命中**——实测：「南非七月份黄金产量同比下降7.4%」
    经 name-stem 命中「黄金概念」，东财板块「黄金」又映射到同一个「黄金概念」⇒
    两行 target 完全相同。撞唯一约束后 `EventStore.add_event` 的兜底把它判为
    「并发重复」，但该事件本就是新的（fingerprint 查不到）→ ``raise`` ⇒
    **整条快讯丢失，且每轮快讯轮询重试都失败**（消息面维度的静默缺口）。

    保留先到者：调用顺序为 文本命中/传导链 → 来源标的 → 板块映射，即优先级
    「文本证据 > 板块代码推断」，与 `_dedupe_chain_rows` 的「保留先到的 alias/name 行」
    一致。**只在没有文本命中时板块行才独自生效**（retro P0-3 的「只有板块代码」那
    ~35% 快讯不受影响）。

    去重键用**精确相等**而非 `_dedupe_same` 的包含判定：唯一约束是精确比较，
    这里的目标是「不撞约束」，不是「合并语义相近的题材」（后者会误丢合法关联）。
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for d in directions:
        key = (str(d.get("target_type") or ""), str(d.get("target") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _demote_if_proposed(directions: list[dict], certainty: str) -> None:
    """论文/实验室级（certainty=proposed）事件整体不给高分（hotspot §3.3/§5）。

    原地修改：direction 保留（含 0 行——关联事实不变），strength 一律压到 1。
    """
    if certainty == "proposed":
        for d in directions:
            d["strength"] = min(int(d.get("strength") or 1), 1)


def build_event(title: str, *, source: str | None = None, url: str | None = None,
                summary: str | None = None, published_at: datetime | None = None,
                source_symbol: str | None = None, is_announcement: bool = False,
                theme_names: list[str] | None = None,
                board_themes: list[dict] | None = None) -> dict:
    """新闻行 → EventCard + directions 组合 dict（纯函数入口）。

    ``board_themes``：东财板块代码映射出的题材关联（retro P0-3 后半），
    元素形如 ``{"theme_name": ..., "board_name": ..., "board_code": ...}``，
    各生成一行 matched_by="board" 方向行。
    """
    fact_kind, certainty = classify_certainty(title)
    category = classify_category(title, summary)
    base_rows = extract_directions(title, theme_names or [], summary)
    directions = _dedupe_chain_rows(base_rows, match_chains(title, summary))
    if source_symbol:
        symbol_row = extract_symbol_direction(title, source_symbol, summary)
        if symbol_row is not None:
            directions.append(symbol_row)
    for bt in board_themes or []:
        directions.append(extract_board_direction(
            title, bt["theme_name"], bt["board_name"], bt["board_code"], summary
        ))
    # 收口去重（2026-09-10）：板块行可能与文本命中行落到**同一个题材**，而
    # event_direction 有 UNIQUE(event_id, target_type, target) —— 不去重就会
    # 撞约束并让整条事件丢失（详见 dedupe_directions docstring）。
    directions = dedupe_directions(directions)
    _demote_if_proposed(directions, certainty)
    return {
        "fingerprint": title_fingerprint(title),
        "title": title.strip(),
        "url": url,
        "summary": (summary or "").strip() or None,
        "source": source or "",
        "source_tier": classify_source_tier(source, is_announcement=is_announcement),
        "published_at": published_at or beijing_now_naive(),
        "fact_kind": fact_kind,
        "certainty": certainty,
        "category": category,
        "half_life_hours": HALF_LIFE.get(category, HALF_LIFE["other"]),
        "source_symbol": source_symbol,
        "status": "active",
        "directions": directions,
    }
