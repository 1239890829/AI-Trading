"""题材内核 · 个股情绪 · 龙头打分 · 介入时机。

## 这个模块回答什么

涨停池和题材看板解决"发生了什么"，本模块解决"怎么判断、怎么参与"：

- **个股情绪**（`stock_sentiment`）：单只票的情绪强弱，四维可解释
- **龙头打分**（`dragon_score`）：在**首板/二板阶段**前瞻识别龙头相，而不是等它
  涨到 5 板再用高度倒推（那是结果归因，不是识别）
- **介入时机**（`entry_checklist`）：针对"等确认往往已涨一轮"的痛点，给出需要
  同时满足的信号清单、价位锚与失效条件
- **题材内核**（`theme_core`）：这个题材到底在炒什么（政策/业绩/外围/传闻…）
- **消息持续性**（`news_persistence`）：新题材值不值得追，四问验证

## 阈值来源与诚实边界

阈值全部来自公开实战口径的**多源交叉**（雪球/淘股吧/东财/格隆汇，见
`docs/theme-sentiment-methodology.md`）。必须说明三点：

1. **源间存在分歧**，已在该函数 docstring 中标注，取交集的保守值。
2. **未经本项目历史数据回测校准**——当前全市场快照仅 2 天，样本不足以做统计验证。
   这些打分是**可解释的规则**，不是经过验证的胜率模型。
3. 输出的是**偏向 + 依据 + 失效条件**，不是买卖结论（AGENTS.md 红线 3）。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 常量

# 封单比 = 封单额 / 流通市值。业界：>1% 强 / >3% 非常强 / ≥5% 极强
# 源间一致度较高，仅"极强"档有 5% 与 10% 两说，取 5%。
SEAL_RATIO_BANDS = [
    (0.005, -2, "封单薄(<0.5%)"),
    (0.01, 0, "一般(0.5–1%)"),
    (0.03, 1, "强(1–3%)"),
    (0.05, 2, "非常强(3–5%)"),
    (None, 3, "极强(≥5%)"),
]

# 首封时间。业界共识度高：9:30–9:35 上板最有龙头相，14:50 后尾盘板回避。
SEAL_TIME_BANDS = [
    ("093500", 3, "早盘龙头相(≤9:35)"),
    ("100000", 2, "早盘(9:35–10:00)"),
    ("103000", 1, "上午(10:00–10:30)"),
    ("140000", 0, "午后(10:30–14:00)"),
    ("143000", -1, "尾盘临近(14:00–14:30)"),
    (None, -2, "尾盘偷袭(>14:30，回避)"),
]

# 换手率。源间分歧：一说 5–20%，一说 15–30% 为佳；<3%"死亡换手"与 >20%"爆量
# 不参与"两条是一致的。取交集 5–20% 为健康区。
TURNOVER_BANDS = [
    (3.0, -2, "死亡换手(<3%，疑似庄股控盘)"),
    (5.0, 0, "偏低(3–5%)"),
    (20.0, 2, "健康(5–20%)"),
    (30.0, -1, "爆量分歧(20–30%)"),
    (None, -2, "筹码松动(>30%)"),
]

# 流通市值。**源间分歧最大**：20–80 亿 / 50–200 亿 / 20–50 亿三说。
# 取三者交集倾向的 30–150 亿为最优区，两端递减。
CAP_BANDS = [
    (20e8, -1, "过小(<20亿，易被量化砸烂)"),
    (30e8, 1, "偏小(20–30亿)"),
    (150e8, 2, "适中(30–150亿)"),
    (300e8, 1, "偏大(150–300亿)"),
    (None, -1, "过大(>300亿，拉升吃力)"),
]

# 龙头分级
DRAGON_GRADES = [
    (12, "龙头相"),
    (8, "强势候选"),
    (4, "观察"),
    (-99, "杂毛/回避"),
]

# 炒作内核关键词。顺序=优先级，先命中者胜出。
THEME_CORE_RULES = [
    ("业绩兑现", ("业绩", "净利", "预增", "扭亏", "中报", "年报", "季报", "分红", "送转")),
    ("政策驱动", ("政策", "规划", "补贴", "试点", "标准", "条例", "会议", "十五五", "碳中和", "国补")),
    ("资产重组", ("重组", "并购", "借壳", "控制权", "要约", "资产注入", "股权转让")),
    ("外围传导", ("美股", "隔夜", "海外", "英伟达", "美联储", "外盘", "出口", "关税", "地缘")),
    ("产业趋势", ("算力", "液冷", "光模块", "固态电池", "机器人", "创新药", "AI", "储能", "半导体")),
    ("涨价周期", ("涨价", "提价", "报价", "供需", "库存", "黄金", "有色", "稀土")),
    # ⚠️ 这里原本还有「或」「拟」两个关键词，2026-08-29 移除：
    # 「控制权拟变更」「或增资」这类表述在涨停原因里极常见，等于给事件传闻
    # 开了万能匹配，几乎任何题材都会被带上"弱内核"标签。
    ("事件传闻", ("传闻", "消息", "疑似", "未证实", "市场传闻")),
]

# 内核 → 持续性倾向（业界共识：能兑现业绩的最硬，纯概念的最弱）
# 值域 -1(弱) ~ +1(强)
THEME_CORE_PERSISTENCE = {
    "业绩兑现": 1, "政策驱动": 0, "产业趋势": 1, "涨价周期": 0,
    "资产重组": -1, "外围传导": -1, "事件传闻": -1, "无法归因": None,
}

# 「位置已高」的活跃天数门槛：题材连续活跃到这一天数，再出利好更可能是兑现借口。
# 取 4 而非更大的数，是因为 A 股题材的典型生命周期里，第 4 天起龙头往往已到
# 三板以上，此时接力盘的对手盘从跟风资金变成了前排获利盘。
ACTIVE_DAYS_HIGH = 4

_BOARDS_STAT_RE = re.compile(r"(\d+)\s*天\s*(\d+)\s*板")


# ---------------------------------------------------------------- 工具


def _band(value, bands, *, higher_is_better=True):
    """通用分档。value 为 None → (0, "缺失")。"""
    if value is None:
        return 0, "缺失"
    for upper, points, label in bands:
        if upper is None or value < upper:
            return points, label
    return bands[-1][1], bands[-1][2]


def _hhmmss(t: str | None) -> str | None:
    """把 '9:35' / '09:35:00' / '093500' 归一成 6 位 HHMMSS。

    ⚠️ 不能直接对整串补零：'9:35' 去标点后是 '935'，`ljust`/`zfill` 都会得到错误
    结果（'935000' 会被读成 93:50，'000935' 读成 00:09:35）。必须先按位数判断
    这是 HHMM 还是 HHMMSS，再分别补零。
    """
    if not t:
        return None
    digits = re.sub(r"\D", "", str(t))
    if not digits:
        return None
    if len(digits) <= 4:  # HMM / HHMM：补秒
        return digits.zfill(4) + "00"
    return digits.zfill(6)[:6]


def seal_ratio(seal_amount: float | None, float_market_cap: float | None) -> float | None:
    """封单比 = 封单额 ÷ 流通市值。标准化的封板强度，比绝对封单额可比。"""
    if not seal_amount or not float_market_cap:
        return None
    return round(seal_amount / float_market_cap, 5)


def has_repair(boards_stat: str | None, consecutive_boards: int | None) -> bool:
    """是否为反包（板史天数 > 连板数，说明中途断过板又封回来）。

    '5天3板' 表示 5 个交易日里涨停 3 次，中途断过 → 今日封板属反包/弱转强。
    """
    if not boards_stat:
        return False
    m = _BOARDS_STAT_RE.search(boards_stat)
    if not m:
        return False
    days, boards = int(m.group(1)), int(m.group(2))
    return days > boards and boards >= (consecutive_boards or 1)


# ---------------------------------------------------------------- 个股情绪


def stock_sentiment(
    *,
    boards: int | None,
    seal_amount: float | None = None,
    float_market_cap: float | None = None,
    first_seal_time: str | None = None,
    last_seal_time: str | None = None,
    break_count: int | None = None,
    turnover_rate: float | None = None,
    is_theme_highest: bool = False,
    is_primary_theme: bool = True,
    boards_stat: str | None = None,
) -> dict:
    """个股情绪四维判定：封板质量 / 量价配合 / 结构位置 / 筹码松动。

    返回 level: 强 / 偏强 / 分歧 / 弱。每一维都给出依据，缺失维度显式标注。

    注意：**这是截面判断，不含分时承接**。盘中分时（是否破均价线、回踩是否缩量）
    需要分钟线数据，见 `entry_checklist` 的盘中观察项。
    """
    ratio = seal_ratio(seal_amount, float_market_cap)
    p_ratio, l_ratio = _band(ratio, SEAL_RATIO_BANDS)
    t = _hhmmss(first_seal_time)
    p_time, l_time = (0, "缺失") if t is None else _band(t, SEAL_TIME_BANDS, higher_is_better=False)
    # 炸板次数缺失必须记 0 分而不是当作"0 次"白送 +2：不知道 ≠ 没炸过
    if break_count is None:
        p_break, l_break, bc = 0, "炸板次数缺失", 0
    else:
        bc = break_count
        p_break = 2 if bc == 0 else (0 if bc == 1 else -2)
        l_break = f"炸板{bc}次"

    seal_quality = p_ratio + p_time + p_break

    p_turn, l_turn = _band(turnover_rate, TURNOVER_BANDS)
    vol_price = p_turn

    b = boards or 1
    pos = 3 if is_theme_highest else (1 if b >= 2 else 0)
    l_pos = "题材最高板" if is_theme_highest else (f"{b}板" if b >= 2 else "首板")

    repair = has_repair(boards_stat, boards)
    structure = (1 if repair else 0) + (0 if is_primary_theme else -1)

    total = seal_quality + vol_price + pos + structure
    if total >= 8:
        level = "强"
    elif total >= 4:
        level = "偏强"
    elif total >= 0:
        level = "分歧"
    else:
        level = "弱"

    risks: list[str] = []
    if bc >= 2:
        risks.append(f"炸板 {bc} 次，封板不牢，次日承接存疑")
    if turnover_rate is not None and turnover_rate > 20:
        risks.append(f"换手 {turnover_rate}% 爆量，筹码松动")
    if turnover_rate is not None and turnover_rate < 3:
        risks.append("换手不足 3%，疑似庄股控盘，易闪崩")
    if t and t > "143000":
        risks.append("尾盘封板（>14:30），主力犹豫，次日溢价弱")
    if ratio is not None and ratio < 0.01:
        risks.append(f"封单比仅 {ratio:.2%}，封单薄")
    if not is_primary_theme:
        risks.append("该股主属性不在此题材，属蹭概念，跟风属性强")

    return {
        "level": level,
        "score": total,
        "seal_ratio": ratio,
        "is_repair": repair,
        "basis": [
            f"封板质量：封单比 {l_ratio}（{p_ratio:+d}）、{l_time}（{p_time:+d}）、{l_break}（{p_break:+d}）",
            f"量价配合：换手 {l_turn}（{p_turn:+d}）",
            f"结构位置：{l_pos}（{pos:+d}）",
            f"结构属性：{'反包弱转强（+1）' if repair else '连续板'}、"
            f"{'主属性' if is_primary_theme else '非主属性（-1）'}",
        ],
        "risks": risks,
    }


# ---------------------------------------------------------------- 龙头打分


def dragon_score(
    *,
    boards: int | None,
    seal_amount: float | None = None,
    float_market_cap: float | None = None,
    first_seal_time: str | None = None,
    break_count: int | None = None,
    turnover_rate: float | None = None,
    is_theme_highest: bool = False,
    is_second_highest: bool = False,
    boards_stat: str | None = None,
) -> dict:
    """龙头前瞻打分：在首板/二板阶段识别"像不像龙头"。

    **为什么不用连板高度定龙头**：高度是结果。等一只票涨到 5 板再认定它是龙头，
    恰恰就是用户说的"等确认往往已涨一轮"。本函数在 1–2 板阶段就能给出偏向，
    打分的每一维都是当天盘中可见的客观数据。

    返回 score / grade / basis / missing（缺失维度清单，便于判断置信度）。
    """
    ratio = seal_ratio(seal_amount, float_market_cap)
    p_ratio, l_ratio = _band(ratio, SEAL_RATIO_BANDS)

    t = _hhmmss(first_seal_time)
    p_time, l_time = (0, "缺失") if t is None else _band(t, SEAL_TIME_BANDS, higher_is_better=False)

    p_turn, l_turn = _band(turnover_rate, TURNOVER_BANDS)
    p_cap, l_cap = _band(float_market_cap, CAP_BANDS)

    # 同上：缺失 ≠ 0 次，不能白送分
    if break_count is None:
        bc, p_break, l_bc = 0, 0, "炸板次数缺失(0)"
    else:
        bc = break_count
        p_break = 2 if bc == 0 else (0 if bc == 1 else -2)
        l_bc = f"炸板 {bc} 次（{p_break:+d}）"

    if is_theme_highest:
        p_pos, l_pos = 3, "题材最高板"
    elif is_second_highest:
        p_pos, l_pos = 1, "题材次高板"
    else:
        p_pos, l_pos = 0, "非前排"

    repair = has_repair(boards_stat, boards)
    p_repair = 1 if repair else 0

    total = p_ratio + p_time + p_turn + p_cap + p_break + p_pos + p_repair
    grade = next(label for cut, label in DRAGON_GRADES if total >= cut)

    missing = [
        name
        for name, ok in (
            ("封单比", ratio is not None),
            ("封板时间", t is not None),
            ("换手率", turnover_rate is not None),
            ("流通市值", float_market_cap is not None),
        )
        if not ok
    ]

    return {
        "score": total,
        "grade": grade,
        "seal_ratio": ratio,
        "break_count": bc,
        "is_repair": repair,
        "basis": [
            f"封单比 {l_ratio}（{p_ratio:+d}）",
            f"封板时间 {l_time}（{p_time:+d}）",
            f"换手率 {l_turn}（{p_turn:+d}）",
            f"流通市值 {l_cap}（{p_cap:+d}）",
            l_bc,
            f"题材地位 {l_pos}（{p_pos:+d}）",
            f"{'反包弱转强（+1）' if repair else '非反包（0）'}",
        ],
        "missing": missing,
    }


# ---------------------------------------------------------------- 介入时机


def entry_checklist(
    *,
    symbol: str,
    name: str | None = None,
    boards: int | None = None,
    seal_amount: float | None = None,
    float_market_cap: float | None = None,
    first_seal_time: str | None = None,
    turnover_rate: float | None = None,
    break_count: int | None = None,
    theme: str | None = None,
    theme_stage: str | None = None,
    dragon: dict | None = None,
    market_phase: str | None = None,
) -> dict:
    """介入条件清单：解决"等确认往往已涨一轮，追进去又被套"。

    设计要点：
    - 不给"明天买 X"，而是给**必须同时满足的信号清单**——信号不全就不该出手，
      这本身就是对"追高被套"的防御。
    - 区分三层：市场层（环境允许不允许）→ 题材层（题材处在什么阶段）→ 个股层
      （这只票自身封板质量够不够）。三层都过才谈价位。
    - 必须给出**失效条件**：什么情况说明判断错了，而不是无限期等待。

    红线：输出的是偏向与条件，不是买卖指令。
    """
    grade = (dragon or {}).get("grade", "")
    ratio = seal_ratio(seal_amount, float_market_cap)
    t = _hhmmss(first_seal_time)
    b = boards or 1

    # --- 市场层 ---
    market_block = market_phase in {"退潮", "冰点"}
    market_note = {
        "退潮": "市场处于退潮期，任何接力都是逆势，首选空仓",
        "冰点": "市场冰点，涨停多为脉冲，不具备接力环境",
        "分歧": "市场分歧期，只做最强前排，且必须严控仓位",
        "高潮": "市场高潮，溢价充足但随时可能转折，不追高标",
        "发酵": "市场发酵期，接力环境较好",
        "修复": "市场修复期，可小仓位试错前排",
    }.get(market_phase or "", "市场阶段未知，需先确认环境")

    # --- 题材层 ---
    stage_block = theme_stage in {"退潮"}
    stage_note = {
        "启动": "题材刚启动，可用首板/一进二试错，仓位宜小",
        "发酵": "题材发酵期是性价比最高的参与窗口",
        "高潮": "题材已高潮，追高性价比低，宜等分歧后低吸",
        "分歧": "题材分歧，只看龙头与中军，回避跟风杂毛",
        "退潮": "题材退潮，不应介入",
    }.get(theme_stage or "", "题材阶段未知")

    # --- 个股层条件（次日需要看到的信号）---
    conditions: list[str] = []
    if b == 1:
        conditions.append("竞价高开 3%–7% 且竞价成交额占前日封板成交额 ≥5%（有新资金进场）")
        conditions.append("开盘 30 分钟内不破开盘价，回踩缩量、拉升放量")
    else:
        conditions.append("竞价高开 3%–7%（高开 >8% 或一字板属风险信号，获利盘抛压剧增）")
        conditions.append("分时回踩不破均价线，且均价线持续上移")
    conditions.append("封板瞬间买一出现连续大单，封单在数秒至十余秒内被快速消化（盘口强度）")
    conditions.append("所属题材当日再有 ≥2 只跟风涨停（板块合力未散）")
    if b >= 3:
        conditions.append("高位股需额外确认：题材最高板未断板，且无监管问询/异动公告")

    avoid: list[str] = []
    if t and t > "143000":
        avoid.append("前日为尾盘偷袭板（>14:30），次日溢价弱，不参与")
    if turnover_rate is not None and turnover_rate > 20:
        avoid.append(f"前日换手 {turnover_rate}% 爆量，筹码松动")
    if (break_count or 0) >= 2:
        avoid.append(f"前日炸板 {break_count} 次，封板不牢")
    if ratio is not None and ratio < 0.01:
        avoid.append(f"前日封单比仅 {ratio:.2%}，封单薄")
    if market_block:
        avoid.append(market_note)
    if stage_block:
        avoid.append(stage_note)

    # --- 失效条件：什么情况说明判断错了 ---
    invalidation = [
        "次日竞价低开 ≥3% 且 15 分钟内不能翻红 → 接力失败，判断作废",
        "盘中跌破分时均价线且不能快速收回 → 承接不足",
        "所属题材最高板断板 → 梯队塌方，立即降级",
        "板块内跟风股批量炸板 → 合力瓦解",
    ]

    # --- 时间窗口 ---
    timing = (
        "竞价 9:15–9:25 观察委买与高开幅度；"
        "早盘 9:30–10:00 确认承接后再决策；"
        "10:30 后未上板的二板不走强，放弃；"
        "14:30 后不参与尾盘博弈"
    )

    return {
        "symbol": symbol,
        "name": name,
        "theme": theme,
        "boards": b,
        "dragon_grade": grade,
        "market_layer": {"phase": market_phase, "blocked": market_block, "note": market_note},
        "theme_layer": {"stage": theme_stage, "blocked": stage_block, "note": stage_note},
        "conditions": conditions,
        "avoid": avoid,
        "invalidation": invalidation,
        "timing": timing,
        "note": (
            "以上为条件清单而非买卖建议：信号未同时满足就不构成介入理由；"
            "任一失效条件出现即推翻判断。价格区间需结合个股市值、题材阶段与自身仓位确定。"
        ),
    }


# ---------------------------------------------------------------- 题材内核


def _match_cores(text: str) -> list[str]:
    """一段文本命中的全部内核类型（不取首个，保留全部以便投票）。"""
    return [core for core, kws in THEME_CORE_RULES if any(k in text for k in kws)]


def theme_core(theme: str | None, reasons: list[str] | None = None) -> dict:
    """题材炒作内核归因：这个题材到底在炒什么。

    内核决定了**资金愿意给多久的耐心**：业绩兑现型可以被反复做，事件传闻型
    大多一日游。归不出来就明说"无法归因"，不硬套。

    ⚠️ 2026-08-29 重写原实现，原逻辑有两个叠加缺陷：

    1. **把全部成分股的涨停原因拼成一个大字符串，再按规则表顺序返回首个命中**。
       实测把「算力」「创新药」都归成了「业绩兑现」——因为 41 条原因里只要有
       一条含"业绩"二字就命中，而"业绩兑现"在规则表里排第一。
       **任意一只符合 ≠ 这个题材符合**，但原逻辑分不清这两件事。
    2. 规则表里的关键词含「或」「拟」这类高频字，等于给"事件传闻"开了万能匹配。

    现在改为：**题材名优先（它是最权威的分类标签），名未命中时按成员原因多数票**，
    并回报票型分布 `votes`，让归因结论可复核而不是黑箱。
    """
    reasons = [r.strip() for r in (reasons or []) if r and r.strip()]
    name = (theme or "").strip()
    if not name and not reasons:
        return {"type": "无法归因", "persistence": None, "confidence": "低",
                "votes": {}, "members": 0,
                "note": "无题材名与涨停原因，无法归因"}

    # 成员投票：逐条 reason 独立归因，一条 reason 命中多类时每类各记一票
    votes: dict[str, int] = {}
    for r in reasons:
        for c in _match_cores(r):
            votes[c] = votes.get(c, 0) + 1

    name_hits = _match_cores(name) if name else []
    if name_hits:
        core = name_hits[0]
        basis = f"题材名命中：{next((k for core2, kws in THEME_CORE_RULES if core2 == core for k in kws if k in name), core)}"
        # 名称命中但成员票压倒性地指向别处 → 说明标签与实质背离，降置信度
        rival = max((v, c) for c, v in votes.items() if c != core) if any(
            c != core for c in votes) else (0, None)
        confidence = "中" if not rival[1] or rival[0] <= len(reasons) / 2 else "低"
        if confidence == "低":
            basis += f"；但成员原因中「{rival[1]}」占 {rival[0]}/{len(reasons)}，标签与实质可能背离"
    elif votes:
        core = max(votes.items(), key=lambda kv: (kv[1], -list(
            c for c, _ in THEME_CORE_RULES).index(kv[0])))[0]
        share = votes[core] / len(reasons)
        confidence = "中" if share >= 0.5 else "低"
        basis = f"成员原因多数票 {votes[core]}/{len(reasons)}（占比 {share:.0%}）"
    else:
        return {"type": "无法归因", "persistence": None, "confidence": "低",
                "votes": votes, "members": len(reasons),
                "note": "题材名与原因均未命中已知内核类型"}

    return {
        "type": core,
        "persistence": THEME_CORE_PERSISTENCE.get(core),
        "confidence": confidence,
        "votes": votes,
        "members": len(reasons),
        "note": basis,
    }


def news_persistence(
    *,
    theme: str | None = None,
    core_type: str | None = None,
    limit_up_count: int = 0,
    active_days: int = 0,
    board_change_pct: float | None = None,
    main_net_inflow: float | None = None,
    has_second_board: bool = False,
    reasons: list[str] | None = None,
) -> dict:
    """消息驱动型新题材的持续性评估（三证据 + 一风险 + 一票否决）。

    四问来自业界共识：
    ① 能不能兑现成收入利润（业绩 > 政策 > 概念）
    ② 板块是不是已经提前涨过一大段（位置决定风险）
    ③ 资金是不是真金白银认可（多只同步 + 放量，而非个别脉冲）
    ④ 是顶层政策级还是公司级（级别决定生命周期）

    **为什么 ② 不计入 evidence，而是单独作为风险封顶**：
    ② 和 ④ 都挂在「已活跃天数」上但方向相反——连涨既证明有持续性，也意味着
    位置高。若把它们塞进同一个计数器，总分对这个变量几乎不敏感（实测首日启动
    与连涨 6 天都得 2 分），评级就糊了。所以这里把**证据轴**（①③④，回答
    「这事是不是真的」）和**风险轴**（②，回答「现在进还有没有空间」）分开：
    evidence 决定够不够格，position_risk 只封顶不给分。

    再叠加一票否决：**无二板承接的题材，无论消息多大，都按一日游处理**。
    """
    checks: list[dict] = []

    # ① 内核质量
    core = core_type or theme_core(theme, reasons).get("type")
    p = THEME_CORE_PERSISTENCE.get(core or "")
    checks.append({
        "q": "能否兑现为收入利润",
        "axis": "evidence",
        "value": core,
        "pass": p is not None and p > 0,
        "note": {"业绩兑现": "有实际订单/利润支撑，最硬",
                 "产业趋势": "有产业数据与订单验证，较硬",
                 "政策驱动": "方向性利好，需等配套细则与时间表",
                 "涨价周期": "看报价能否稳住，预期缓和后易兑现",
                 "资产重组": "落地不确定，停复牌与过会风险高",
                 "外围传导": "国内产业未必同步，冲高回落风险大",
                 "事件传闻": "无实质落地，一日游概率最高",
                 "无法归因": "无法判断，按最弱处理"}.get(core or "", ""),
    })

    # ② 位置（是不是已经提前涨过一大段）
    #
    # ⚠️ 这里刻意不用东财板块的 f109/f110（5 日 / 10 日涨幅）。这两个字段的多周期
    # 口径是字段序推断出来的，本项目一直没能交叉验证；2026-08-29 实测确认东财
    # K 线在本机取不到（push2his 全系列域名不可达，push2delay 的 klines 恒为空），
    # 因此**无法验证的字段不能当判据**。改用我们自己从涨停池快照算出的
    # 「题材连续活跃天数」——可追溯、可复盘。
    #
    # 也不能用板块「当日」涨幅代替：新题材第一天板块本来就大涨，那是启动而不是
    # 「提前涨过」，用它判位置会把所有刚启动的题材一律误杀。
    position_high = active_days >= ACTIVE_DAYS_HIGH
    checks.append({
        "q": "是否已提前涨过一大段（位置）",
        "axis": "risk",
        "value": f"题材连续活跃 {active_days} 天"
                 + (f"，板块当日 {board_change_pct}%" if board_change_pct is not None else ""),
        "pass": not position_high,
        "note": "低位出利好才有博弈价值；已连涨 "
                f"{ACTIVE_DAYS_HIGH} 天以上的高位利好常是资金兑现的借口。"
                "注意本项与第④项方向相反是刻意的：连涨既证明有持续性，也意味着位置高",
    })

    # ③ 资金认可度（题材内多只同步 + 主力真金白银）
    # main_net_inflow 为 None 时按「缺失不参与判定」处理，既不奖也不罚——
    # 把它当 0 会把「数据没取到」误判成「主力在出货」。
    broad = limit_up_count >= 3
    if main_net_inflow is None:
        money_pass = broad
        inflow_txt = "主力净流入缺失（不参与判定）"
    else:
        money_pass = broad and main_net_inflow > 0
        inflow_txt = f"主力净流入 {main_net_inflow} 元"
    checks.append({
        "q": "资金是否真金白银认可",
        "axis": "evidence",
        "value": f"题材内涨停 {limit_up_count} 只；{inflow_txt}",
        "pass": money_pass,
        "note": "仅个别标的脉冲、多数无响应 = 游资短线套利，不宜追；"
                "涨停家数多但主力净流出 = 拉抬出货嫌疑",
    })

    # ④ 持续性（是否有二板承接 + 多日活跃）
    sustained = has_second_board and active_days >= 2
    checks.append({
        "q": "是否有二板承接（跨越 1–3 日验证窗口）",
        "axis": "evidence",
        "value": f"有二板={has_second_board}，连续活跃 {active_days} 天",
        "pass": sustained,
        "note": "消息放出后 1–3 个交易日是核心验证窗口；无二板承接基本是一日游",
    })

    # ---- 评级：证据定资格，风险只封顶 ----
    # evidence 只统计①③④（位置项 ② 是风险轴，不进计数器，见 docstring）
    evidence = sum(1 for c in checks if c["pass"] and c["axis"] == "evidence")
    position_risk = position_high

    if has_second_board is False and limit_up_count <= 2:
        grade, veto = "一日游", True
    elif evidence >= 3:
        grade, veto = ("主线·位置偏高" if position_risk else "主线候选"), False
    elif evidence == 2:
        grade, veto = ("次主线/观察·位置偏高" if position_risk else "次主线/观察"), False
    else:
        grade, veto = "一日游风险高", False

    return {
        "grade": grade,
        "core_type": core,
        "evidence": evidence,
        "evidence_total": 3,
        "position_risk": position_risk,
        "veto": veto,
        "checks": checks,
        "note": (
            "持续性评估用于决定「值不值得跟踪」，不预测涨幅。"
            "消息面交易的常见误区：把政策立项等同于订单落地、把单日脉冲当成主线反转。"
            "evidence 与 position_risk 需分开看：证据足只说明题材是真的，"
            "不代表当前位置还有空间。"
        ),
    }
