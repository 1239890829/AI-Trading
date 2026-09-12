"""事件→板块传导链知识表（docs/summary/architecture-design.md §3 传导层，2026-09-07）。

与 extract.py 的 ENTITY_ALIASES（1 词→1 题材）互补：别名表管「直接点名」，
本表管「一级事件→多板块多级传导」。全部人工维护、basis 必带，direction 不猜
——产业链/海外实体类事件本体即利好源头给 +1；宏观类方向引用回测结论
（docs/summary/data-market.md），证据不足时 strength=1（弱）或 0（待判）。

三类链路：
1. 产业链多级：厄尔尼诺 → 种植/磷化工/化肥/电力/电网（强度=专业弹性排序的
   工程化表达：化肥直接进利润 2 > 种植 2 > 电力/电网逻辑间接 1）
2. 海外实体：OpenAI/GPT → AI应用/AI智能体/算力（海外产品事件→A股题材映射）
3. 宏观量化：非农 → 方向由意外差决定；P0 按标题方向词二分，词表未命中显式 0

⚠️ 定位声明（2026-09-10，KB-DEC-019 反固化条款）：
本表是**映射索引**（关键词 → 题材，供检索），**不是已验证的行情规律**。
其中带数值的「弹性/强度排序」（如化肥 2 > 电力 1）源自**单日单案例观察**
（KB-STOCK-01：09-09 全链涨停一天），**属未验证印象**——保留为可解释性依据，
**不得当结论引用、不得据此做跨题材外推**；要升格为"战术"须走
`app/research/strategy_verify.py` 的数据验证（准入闸门见 KB-DEC-019）。

纪律：
- target 全部使用官方目录名（theme 表实测核对 2026-09-07，零臆造）；
- 每组命中一次（同组多触发词不重复产行）；跨组各自独立；
- matched_by="chain" 与 name/alias/source 区分，basis 写明链路出处；
- target_type="market" 是新增类型（A股大盘），下游全部按类型显式过滤、
  未知类型自动忽略（grep 核实 routes/picks/brief 消费方），不影响存量行为。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_NFP_BASIS = "宏观传导链 chains.nfp（docs/summary/data-market.md 10年116期回测，t 不显著，仅提示不作规则）"


def _theme_row(target: str, direction: int, strength: int, chain: str, basis: str) -> dict:
    return {
        "target_type": "theme",
        "target": target,
        "direction": direction,
        "strength": strength,
        "chain": chain,
        "basis": f"传导链表 chains（人工维护）：{basis}",
        "matched_by": "chain",
    }


def _market_row(direction: int, strength: int, chain: str, basis: str) -> dict:
    return {
        "target_type": "market",
        "target": "A股大盘",
        "direction": direction,
        "strength": strength,
        "chain": chain,
        "basis": f"传导链表 chains（人工维护）：{basis}",
        "matched_by": "chain",
    }


# ---- 1. 产业链多级：厄尔尼诺/拉尼娜/极端天气（hotspot §3.2 实测传导链）----
# ⚠️ 强度值（2/1）是人工给的「弹性印象」，源自单日单案例（KB-STOCK-01），**未经数据验证**；
#    仅用于排序展示与可解释性，不得当结论引用（KB-DEC-019 反固化条款）。
_EL_NINO_ROWS = [
    _theme_row("磷化工", 1, 2, "厄尔尼诺→主产区减产→施肥需求+磷肥涨价直接进利润→弹性最强", "厄尔尼诺农业链（弹性排序：化肥>种植>电力）"),
    _theme_row("化肥", 1, 2, "厄尔尼诺→农产品减产涨价→钾肥等化肥涨价直接进利润", "厄尔尼诺农业链"),
    _theme_row("农业种植", 1, 2, "厄尔尼诺→主产区天气异常→农产品减产涨价→种植弹性", "厄尔尼诺农业链（国内主粮自给、进口依赖大的是大豆/糖）"),
    _theme_row("绿色电力", 1, 1, "厄尔尼诺→极端天气→用电负荷+水电来水波动→电力（逻辑最间接，靠极端天气兑现）", "厄尔尼诺电力链"),
    _theme_row("智能电网", 1, 1, "厄尔尼诺→负荷波动放大→电网投资/特高压景气", "厄尔尼诺电网链"),
]
_EL_NINO_KEYS = ("厄尔尼诺", "拉尼娜", "极端天气", "干旱", "寒潮", "超强台风")

# ---- 2. 海外实体：OpenAI/GPT → A股 AI 链（hotspot §3.4 实测传导链）----
_GPT_ROWS = [
    _theme_row("AI应用", 1, 2, "海外大模型迭代→应用落地加速（办公/金融/内容）", "海外AI产品链"),
    _theme_row("AI智能体", 1, 2, "GPT 类 Agent「从问答到直接做事」→智能体应用加速", "海外AI产品链"),
    _theme_row("东数西算(算力)", 1, 1, "大模型训练/推理→算力需求底层受益", "海外AI产品链"),
]
_GPT_KEYS = ("OpenAI", "GPT", "ChatGPT", "Sora")

# ---- 3. 宏观：非农（方向动态判定）+ 加息/降息（常识弱方向）----
_NFP_STRONG = re.compile(r"超预期|强于预期|高于预期|意外强劲|大幅好于")
_NFP_WEAK = re.compile(r"不及预期|低于预期|弱于预期|意外疲软|大幅下滑")

_NFP_OBSERVE_ROWS = [
    _theme_row("东数西算(算力)", 0, 1, "隔夜美股科技定价→A股算力链开盘情绪观察（历史：非农日振幅+24~33%）", "非农情绪链（方向取决于意外差，未知则 0）"),
    _theme_row("苹果概念", 0, 1, "隔夜美股科技定价→A股果链开盘情绪观察", "非农情绪链（方向取决于意外差，未知则 0）"),
]


def _nfp_rows(title: str) -> list[dict]:
    """非农方向：强意外→A股偏空+高波；弱意外→偏强；词表未命中显式 0（不猜）。"""
    if _NFP_STRONG.search(title):
        direction = -1
        chain = "劳动市场意外强→宽松预期收缩→次日A股偏弱+波动放大（深A日内-0.28%；振幅+24~33%，均不显著，仅提示）"
    elif _NFP_WEAK.search(title):
        direction = 1
        chain = "劳动市场意外弱→宽松预期回升→次日A股偏强（深A +0.63%，n=18，t=1.78 不显著，仅提示）"
    else:
        direction = 0
        chain = "方向取决于意外差（标题未给意外方向），P0 不猜——待实际数据回填"
    return [_market_row(direction, 1, chain, _NFP_BASIS)] + [dict(r) for r in _NFP_OBSERVE_ROWS]


def _rate_rows(title: str) -> list[dict]:
    """加息/降息：常识弱方向（strength=1，实际取决于当时主导矛盾）；议息未决议显式 0。"""
    basis = "宏观常识链 chains.rate（方向随主导矛盾切换，仅提示）"
    if "降息" in title or "降准" in title:
        return [_market_row(1, 1, "流动性宽松→无风险利率下行→风险偏好回升→A股偏多", basis)]
    if "加息" in title:
        return [_market_row(-1, 1, "流动性收紧→美元/美债利率上行→A股承压", basis)]
    return [_market_row(0, 1, "议息决议未出/未含方向词，方向不定——显式待判", basis)]


_RATE_KEYS = ("加息", "降息", "降准", "议息", "FOMC", "美联储决议")

# 非农触发词（原先写死在 match_chains 的 `if "非农" in text` 里）。
# 抽出来是为了让反向索引与正向匹配**共用同一份**——写死一处、另抄一处必然漂移。
_NFP_KEYS = ("非农",)


# ------------------------------------------------ 链目录（正向/反向的**唯一真相源**，P2-5）

# 每组一条：触发词 keys + 该链指向什么。
# ⚠️ `direction` 为 None 表示**方向不固定**（依赖事件正文里的意外差/主导矛盾），
#    反向检索必须如实说"方向待判"，不能给一个看着像结论的方向。
# ⚠️ 强度/弹性数字沿用表头声明：人工维护的**映射索引**，非已验证规律（KB-DEC-019）。
CHAIN_GROUPS: tuple[dict, ...] = (
    {
        "id": "el_nino",
        "label": "厄尔尼诺/拉尼娜农业链",
        "keys": _EL_NINO_KEYS,
        "rows": _EL_NINO_ROWS,
        "direction_note": "方向固定为 +1（事件本体即利好源头）；强度为人工弹性排序",
    },
    {
        "id": "gpt",
        "label": "海外 AI 产品链",
        "keys": _GPT_KEYS,
        "rows": _GPT_ROWS,
        "direction_note": "方向固定为 +1（海外产品事件→A股题材映射）",
    },
    {
        "id": "nfp",
        "label": "非农宏观传导链",
        "keys": _NFP_KEYS,
        "rows": (),
        "direction_note": (
            "**方向不固定**：由标题里的意外差决定——强意外→偏空、弱意外→偏多、"
            "未给方向→显式 0（不猜）。历史回测 t 不显著，仅提示级"
        ),
    },
    {
        "id": "rate",
        "label": "加息/降息流动性链",
        "keys": _RATE_KEYS,
        "rows": (),
        "direction_note": (
            "**方向随主导矛盾切换**：降息/降准→+1、加息→−1、未含方向词→显式 0；"
            "strength 恒为 1（常识弱方向）"
        ),
    },
)


def chain_keywords() -> list[str]:
    """全部触发词（供"未命中时给示例"，避免用户/模型无从下手）。"""
    out: list[str] = []
    for g in CHAIN_GROUPS:
        out += [k for k in g["keys"] if k not in out]
    return out


def find_chains(keyword: str) -> list[dict]:
    """关键词 → 相关传导链（**反向索引**，P2-5 的 `chain|keyword=`）。

    与 `match_chains` 的分工（两者语义不同，不可互相替代）：
    - `match_chains(title)` **正向**：给一条新闻标题，返回它触发哪些链（抽取时用）；
    - `find_chains(keyword)` **反向**：给一个词，返回它关联哪些链（助手检索时用）。

    双向包含匹配：关键词可以是触发词本身（"厄尔尼诺"），也可以带着上下文
    （"美国非农数据" → 命中"非农"），还可命中链名（"农业链"）。

    返回每组的 `direction_note` 与静态 target 行；**动态链（非农/利率）不带
    具体方向**——方向取决于正文，此处无从判定，如实说明而非给个默认值。
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    out: list[dict] = []
    for g in CHAIN_GROUPS:
        hit: str | None = None
        for k in g["keys"]:
            if k in kw or kw in k:
                hit = k
                break
        if hit is None and (kw in g["label"] or g["label"] in kw):
            hit = g["label"]
        if hit is None:
            continue
        rows = [
            {"target": r["target"], "target_type": r["target_type"],
             "direction": r["direction"], "strength": r["strength"]}
            for r in g["rows"]
        ]
        out.append({
            "id": g["id"],
            "label": g["label"],
            "matched_keyword": hit,
            "targets": rows,
            "direction_note": g["direction_note"],
            "basis": "传导链表 chains（人工维护的映射索引，非已验证行情规律）",
        })
    return out


def _judge_text(title: str, summary: str | None) -> str:
    """与 extract._judge_text 同义（复制实现避免循环导入：extract import chains）。

    retro P0-4：标题被截断时触发词（如「厄尔尼诺」「非农」）可能落在摘要里。
    """
    s = (summary or "").strip()
    if s and s not in title:
        return f"{title} {s}"
    return title


def el_nino_rows() -> list[dict]:
    """厄尔尼诺/拉尼娜链的题材行（**唯一真相源**）。

    retro §6.2 P1-32：气候一阶源（`app/market/climate.py`）把触发条件从「新闻
    命中关键词」升级为「ONI 指数越线」，但**链条内容必须只有一份**——所以由本
    函数暴露，而不是在 climate 模块里复制一张同构表（复制必然漂移）。
    返回浅拷贝，调用方可安全地补 `basis` 后缀。
    """
    return [dict(r) for r in _EL_NINO_ROWS]


def match_chains(title: str, summary: str | None = None) -> list[dict]:
    """标题+摘要 → 传导链方向行（纯函数，与 extract_directions 行同构）。

    每组命中一次；组内触发词取第一个命中（不重复产行）；跨组独立累加。
    宏观两组（非农/利率）行内 direction 依赖判定文本动态判定。

    触发词与组序都取自 `CHAIN_GROUPS`（与反向 `find_chains` 同源）——此前每个
    组各写一个 `if ... in text` 且词表散在各处，加组要改三处、必然漏。
    """
    text = _judge_text(title, summary)
    out: list[dict] = []
    for g in CHAIN_GROUPS:
        hit = next((k for k in g["keys"] if k in text), None)
        if hit is None:
            continue
        if g["id"] == "nfp":
            out += _nfp_rows(text)
        elif g["id"] == "rate":
            out += _rate_rows(text)
        else:
            out += [{**r, "basis": f"{r['basis']}；触发词「{hit}」"} for r in g["rows"]]
    return out


# ---------------------------------------------------------------- 宏观日历（G6，零外呼先验规则）

def _nth_weekday(year: int, month: int, weekday: int, nth: int = 1) -> date:
    """某月第 nth 个周 X（weekday: 0=周一…4=周五）。"""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (nth - 1))


def _us_dst(d: date) -> bool:
    """美国夏令时（3月第2个周日 ~ 11月第1个周日）粗判定——只用于非农公布时刻文案。"""
    dst_start = _nth_weekday(d.year, 3, 6, 2)  # 周日=6
    dst_end = _nth_weekday(d.year, 11, 6, 1)
    return dst_start <= d < dst_end


def macro_calendar_note(today: date) -> str | None:
    """非农日历提醒（docs/summary/data-market.md 决议「日历提醒类标注 ✓ 可做」）。

    先验规则：非农 = 每月第一个周五（北京时间夏令时 20:30 / 冬令时 21:30 公布）。
    返回简报提示文案；与规则无关的日子返回 None（显式缺失，不凑话）。
    特殊情形（节假日顺延公布）判不出 → None，属设计内缺失。
    """
    nfp_day = _nth_weekday(today.year, today.month, 4, 1)
    if today == nfp_day:
        t = "20:30" if _us_dst(today) else "21:30"
        return (
            f"宏观日历：今晚 {t} 美国{today.month}月非农公布。历史回测（10年116期）："
            "意外强→次日A股偏弱+波动放大（深A日内-0.28%/振幅+24~33%，t 不显著，提示级）；"
            "方向由意外差决定，公布前不给确定性结论。"
        )
    if nfp_day < today <= nfp_day + timedelta(days=3):
        # 周五公布 → +3 = 下周一（首个 A 股定价日）；周六/周日简报不生成，窗口覆盖防顺延
        return (
            "宏观日历：非农已公布，最近的一个 A 股交易日为其定价日，波动或放大"
            "（历史回测：非农次日振幅 +24~33%，提示级）。复盘 A 股实际反应与意外差方向是否一致。"
        )
    return None


# ------------------------------------------------ 财经日历·高信号事件筛选（P1-8 残余，2026-09-10）

# ⚠️ 为什么不用源数据自带的「重要性」星号直接过滤（2026-09-10 实测）：
# 百度财经日历的 star 只有 1/2 两档，且 **star=2 里混着大量日内噪音**——
# 「上期所每日仓单变动-铜/原油」「COMEX 黄金库存」「SPDR 黄金持仓」「美联储资产负债表」
# 全部标 2。实测 2026-09-10 共 109 条、star=2 有 28 条，其中真正值得进简报的不足 6 条。
# ⇒ 采用**地区分侧的关键词白名单 ∧ 噪音词排除**，白名单即过滤器（star 仅作组内择优选条）。
_CN_KEYS: tuple[tuple[str, str], ...] = (
    ("CPI", "CPI"),
    ("PPI", "PPI"),
    ("GDP", "GDP"),
    ("PMI", "PMI"),
    ("社会融资", "社融"),
    ("新增人民币贷款", "新增贷款"),
    ("M0", "货币供应"),
    ("M1", "货币供应"),
    ("M2", "货币供应"),
    ("工业增加值", "工业增加值"),
    ("社会消费品零售", "社零"),
    ("固定资产投资", "固投"),
    ("住宅销售价格", "70城房价"),
    ("进出口", "进出口"),
    ("贸易帐", "贸易帐"),
    ("外汇储备", "外储"),
    ("LPR", "LPR"),
)

# 美国侧只保留「隔夜就能给 A 股开盘定情绪」的一档；非农另有先验日历链（macro_calendar_note），
# 此处并列只为给预期/前值，不重复下结论。
_US_KEYS: tuple[tuple[str, str], ...] = (
    ("非农", "非农"),
    ("ADP", "ADP就业"),
    ("初请失业金", "初请失业金"),
    ("CPI", "CPI"),
    ("PPI", "PPI"),
    ("核心PCE", "核心PCE"),
    ("利率决议", "利率决议"),
    ("零售销售", "零售销售"),
    ("GDP", "GDP"),
    ("ISM", "ISM"),
    ("PMI", "PMI"),
)

# 噪音词：命中的一律不进简报。前 6 个是每日/每周固定披露（仓单/持仓/库存/竞拍），
# 后 2 个是「周度高频点」——如「红皮书商业零售销售」「ADP 就业周度发布变动」，
# 虽含 零售销售/ADP 关键词，但发布频率高、对 A 股无定价意义（实测 2026-09-09 误收）。
_NOISE_KEYS: tuple[str, ...] = (
    "资产负债表", "持仓", "库存", "仓单", "竞拍", "拍卖", "周度", "红皮书",
)

_MACRO_REGIONS: tuple[str, tuple[tuple[str, str], ...]] = (("中国", _CN_KEYS), ("美国", _US_KEYS))

# 同主题同星级时的择条偏好（越靠前越优先）：M1 > M2 > M0 > 年率 > 当月 > 月率 > 年初至今。
# 例：CPI 年率/月率 同日两条同星级 → 取年率；M0/M1/M2 同日 → 取 M1（A 股更看 M1/M2）。
# ⚠️ 数字型 token 必须排在「年率」之前，否则年率会把 M0/M1/M2 拉平（实测踩过）。
# 未命中任何词 → 排在偏好序列之后（并列时保持先到者）。
_PREFER_ORDER: tuple[str, ...] = ("M1", "M2", "M0", "年率", "当月", "月率", "年初至今")


def _prefer_rank(title: str) -> int:
    up = title.upper()
    for i, tok in enumerate(_PREFER_ORDER):
        if tok.upper() in up:
            return i
    return len(_PREFER_ORDER)


def _match_macro_key(title: str, keys: tuple[tuple[str, str], ...]) -> str | None:
    """标题命中白名单 → 主题名；ASCII 键按大小写不敏感匹配（源标题混用 M2/m2）。"""
    up = title.upper()
    for key, label in keys:
        needle = key.upper()
        if needle in up:
            return label
    return None


def select_macro_events(rows: list[dict], *, limit: int = 6) -> list[dict]:
    """财经日历原始行 → 简报用高信号事件（纯函数，零 IO）。

    规则（依 2026-09-10 真实数据定）：地区 ∈ {中国, 美国} ∧ 命中该侧白名单
    ∧ 不命中噪音词；同一 (地区, 主题) 只留一条——先比重要性星级，再比
    `_PREFER_ORDER` 择条偏好（年率 > 月率、M1 > M2 > M0），仍并列则保持先到者。
    排序按公布时刻；``time`` 缺失的行排最后（不臆造时间）。
    """
    best: dict[tuple[str, str], dict] = {}
    for r in rows or []:
        region = (r.get("region") or "").strip()
        title = (r.get("event") or "").strip()
        if not title:
            continue
        label = None
        for name, keys in _MACRO_REGIONS:
            if region == name:
                label = _match_macro_key(title, keys)
                break
        if not label:
            continue
        if any(n in title for n in _NOISE_KEYS):
            continue
        star = r.get("importance")
        star = star if isinstance(star, int) else 0
        key = (region, label)
        cand = (star, -_prefer_rank(title))
        prev = best.get(key)
        if prev is not None and (prev["_star"], prev["_prefer"]) >= cand:
            continue
        best[key] = {
            "_star": star,
            "_prefer": cand[1],
            "region": region,
            "label": label,
            "event": title,
            "time": r.get("time") or None,
            "actual": r.get("actual"),
            "forecast": r.get("forecast"),
            "previous": r.get("previous"),
            "importance": star or None,
        }
    out = sorted(best.values(), key=lambda x: (x["time"] is None, x["time"] or "", x["region"]))
    for item in out:
        item.pop("_star", None)
        item.pop("_prefer", None)
        item["line"] = macro_event_line(item)  # 格式化单点收口：前端/卡片只渲染不拼接
    return out[:limit] if limit > 0 else out


def _fmt_val(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def macro_event_line(ev: dict) -> str:
    """事件 → 单行文案（格式化单点收口：简报前端与推送卡片共用同一字符串）。

    数值缺失显式跳过该段，不写「None」也不补 0（三态纪律）。
    """
    parts: list[str] = []
    pub = _fmt_val(ev.get("actual"))
    exp = _fmt_val(ev.get("forecast"))
    prev = _fmt_val(ev.get("previous"))
    # 「未公布」是源数据的占位文案，不是数值 —— 当缺失处理，避免读成已公布值
    pub = None if pub == "未公布" else pub
    if pub:
        parts.append(f"公布 {pub}")
    else:
        if exp:
            parts.append(f"预期 {exp}")
        if prev:
            parts.append(f"前值 {prev}")
    vals = f"（{' · '.join(parts)}）" if parts else ""
    head = f"{ev['time']} " if ev.get("time") else ""
    return f"{head}{ev['region']}·{ev['label']}{vals}　{ev['event']}"
