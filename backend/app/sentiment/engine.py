"""市场情绪引擎 v2（full.md §5.5）。

## 为什么重写

v1 在 2026-08-29（周六）把市场判成「高潮 / 温度 89.2 / 置信度高」，依据是
"昨日涨停股今日均值 +10.38%、翻红率 100%、再涨停率 100%"。这三个数字是自指计算的
产物：东财涨停池传入非交易日会静默回退到最近交易日，于是"昨日池"和"今日快照"指向
同一天，涨停股查自己涨停那天的收盘价恒等于 +10%。

根因在 `docs/sentiment.md`「历史误判案例库」已完整定位，并已修复。
v2 把复盘结论固化成代码与测试，让同一类错误无法再静默发生。

## v2 的三条设计原则

1. **日期必须由交易日历锚定，不接受 `date.today()` 猜测。**
   调用方必须传入经 `trade_calendar` 校验的 `trade_date` / `prev_trade_date`。

2. **赚钱效应可否决热度。**
   热度（涨停家数/连板高度/炸板率）回答"市场热不热"，赚钱效应（晋级率/昨涨停中位
   收益/翻红率/跌停家数）回答"你参不参与得了"。两者分轴打分后查矩阵定阶段——热度
   再高，只要赚钱效应不行，就只能是「分歧」，不能是「高潮」。这是上一轮最主要的教训。

3. **自指与哨兵检查内置。**
   "翻红率 100%""再涨停率 100%""中位数 ≥9.5%"这类在真实市场不可能出现的读数，
   一旦出现即判定结论不可信，强制降置信度并在 `self_check` 中列明——而不是照常输出。

## 阈值来源与边界

阈值取自公开实战口径（见 docs/sentiment.md「历史误判案例库」多源交叉），
**未经本项目历史数据分位校准**（当前仅有 2 日全市场快照，样本不足以校准）。
所有阈值集中在 `HEAT_BANDS` / `EARNING_BANDS`，可配置、可审计、可替换。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import median

from app.market import price_rules

PHASE_ORDER = ["冰点", "修复", "发酵", "高潮", "分歧", "退潮"]

# ---------------------------------------------------------------- 阈值配置

# (上界, 得分, 标签) 升序；上界为 None 表示"及以上"。值 < 上界 即命中。
HEAT_BANDS = {
    # 涨停家数（封单法）。业界：≤20 冰点 / 40–80 升温 / ≥80 狂热
    "limit_up": [(25, -1, "<25家"), (40, 0, "25–39家"), (60, 1, "40–59家"),
                 (80, 2, "60–79家"), (None, 3, "≥80家")],
    # 最高连板高度。业界：≤2 冰点 / 4–6 升温 / ≥7 狂热（≥6 一说，源间分歧，取严者）
    "max_board": [(3, -1, "≤2板"), (5, 0, "3–4板"), (7, 1, "5–6板"), (None, 2, "≥7板")],
    # 炸板率。业界：≤20% 狂热 / 20–30% 升温 / ≥40% 冰点或退潮
    "break_rate": [(0.20, 1, "≤20%"), (0.35, 0, "20–35%"), (None, -1, ">35%")],
}

EARNING_BANDS = {
    # 1进2 晋级率 —— 业界公认最敏感的单指标：≤25% 冰点/退潮，≥40% 升温
    "promo_1to2": [(0.15, -2, "<15%"), (0.25, -1, "15–25%"),
                   (0.40, 1, "25–40%"), (None, 2, "≥40%")],
    # 昨日涨停股今日涨跌幅**中位数**（不用均值：少数大涨会把均值拉起来）
    "median_pct": [(0.0, -2, "<0%"), (2.0, -1, "0–2%"), (4.0, 0, "2–4%"), (None, 2, ">4%")],
    # 翻红率
    "red_rate": [(0.50, -1, "<50%"), (0.70, 0, "50–70%"), (None, 1, "≥70%")],
    # 跌停家数：退潮的核心识别信号，v1 完全缺失
    "limit_down": [(5, 0, "<5家"), (10, -1, "5–9家"), (None, -2, "≥10家")],
}

# 热度原始分 → 档位（0 低 / 1 偏低 / 2 中 / 3 高）
HEAT_LEVEL_CUTS = [(1, 0), (3, 1), (5, 2), (99, 3)]
# 赚钱效应原始分 → 档位（0 亏钱 / 1 偏弱 / 2 健康 / 3 强）
EARNING_LEVEL_CUTS = [(-2, 0), (0, 1), (3, 2), (99, 3)]

LEVEL_LABELS = {
    "heat": ["低迷", "偏低", "中性", "高热"],
    "earning": ["亏钱", "偏弱", "健康", "强劲"],
}

# 阶段矩阵 [earning_level][heat_level]；heat 0–3，earning 0–3
# 关键格：earning=1(偏弱) 且 heat=2/3 → 分歧（热度高位但赚钱跟不上）
_PHASE_MATRIX = [
    # heat: 0      1      2      3
    ["冰点", "冰点", "退潮", "退潮"],  # earning 0 亏钱
    ["冰点", "退潮", "分歧", "分歧"],  # earning 1 偏弱
    ["修复", "修复", "发酵", "高潮"],  # earning 2 健康
    ["修复", "修复", "发酵", "高潮"],  # earning 3 强劲
]

# 温度权重（合计 1.0；赚钱效应占一半，热度占一半）
_TEMPERATURE_WEIGHTS = {
    "promo": 0.25, "median": 0.25, "limit_up": 0.15,
    "height": 0.15, "breadth": 0.10, "break": 0.10,
}

# 哨兵阈值：真实市场不可能出现的读数，出现即说明数据口径错了
_SENTINEL_RE_LIMIT = 0.999      # 再涨停率 100%
_SENTINEL_RED_RATE = 0.999      # 翻红率 100%
_SENTINEL_MEDIAN = 9.5          # 昨涨停今日中位 ≥9.5%（几乎等于涨停）


# ---------------------------------------------------------------- 工具


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _band(value: float | None, bands: list[tuple]) -> tuple[int, str]:
    """按分档表取值。value 为 None 返回 (0, "缺失")——缺项不惩罚也不奖励。"""
    if value is None:
        return 0, "缺失"
    for upper, points, label in bands:
        if upper is None or value < upper:
            return points, label
    return bands[-1][1], bands[-1][2]


def _level(raw: int, cuts: list[tuple]) -> int:
    for upper, lvl in cuts:
        if raw < upper:
            return lvl
    return cuts[-1][1]


def _limit_pct(symbol: str, name: str | None) -> float:
    # 2026-09-07 R1 收口：与 breadth 同源，判定单点在 price_rules.limit_pct。
    return price_rules.limit_pct(symbol, name)


def _boards_of(pool) -> dict[str, int]:
    return {r.symbol: max(1, int(r.consecutive_boards or 1)) for r in pool}


# ---------------------------------------------------------------- 指标计算


def promotion_rates(pool_today, pool_yesterday) -> dict:
    """晋级率与存活率。

    - 1进2 = 昨日 1 板股中今日达到 ≥2 板的比例（业界公认最敏感的接力指标）
    - 2进3 = 昨日 2 板股中今日达到 ≥3 板的比例
    - 高位存活 = 昨日 ≥3 板股中今日仍在涨停池的比例（龙头梯队是否塌方）
    - 全池存活 = 昨日涨停股今日仍在涨停池的比例
    """
    b_today = _boards_of(pool_today)
    b_yest = _boards_of(pool_yesterday)

    def rate(pred, ok) -> tuple[float | None, int, int]:
        base = [s for s, b in b_yest.items() if pred(b)]
        if not base:
            return None, 0, 0
        hit = [s for s in base if ok(s)]
        return round(len(hit) / len(base), 3), len(hit), len(base)

    r12, h12, b12 = rate(lambda b: b == 1, lambda s: b_today.get(s, 0) >= 2)
    r23, h23, b23 = rate(lambda b: b == 2, lambda s: b_today.get(s, 0) >= 3)
    rhi, hhi, bhi = rate(lambda b: b >= 3, lambda s: s in b_today)
    rall, hall, ball = rate(lambda b: True, lambda s: s in b_today)

    return {
        "promo_1to2": r12, "promo_1to2_hit": h12, "promo_1to2_base": b12,
        "promo_2to3": r23, "promo_2to3_hit": h23, "promo_2to3_base": b23,
        "high_survival": rhi, "high_survival_hit": hhi, "high_survival_base": bhi,
        "pool_survival": rall, "pool_survival_hit": hall, "pool_survival_base": ball,
    }


def prev_zt_performance(pool_yesterday, snapshot: list[dict]) -> dict:
    """昨日涨停股今日表现：均值/中位/翻红率/再涨停率 + 按板位分层。

    分层收益是「分歧」的直证：若高标中位收益远高于首板，说明资金抱团少数票、
    首板大面积一日游，属于典型的分化结构。
    """
    snap = {r["symbol"]: r for r in snapshot}
    perfs: list[float] = []
    red = re_limit = 0
    by_board: dict[str, list[float]] = {"首板": [], "2板": [], "≥3板": []}

    for rec in pool_yesterday:
        row = snap.get(rec.symbol)
        if not row or row.get("change_pct") is None:
            continue
        pct = float(row["change_pct"])
        perfs.append(pct)
        if pct > 0:
            red += 1
        if pct >= _limit_pct(rec.symbol, row.get("name")) - 0.15:
            re_limit += 1
        b = max(1, int(rec.consecutive_boards or 1))
        by_board["首板" if b == 1 else ("2板" if b == 2 else "≥3板")].append(pct)

    n = len(perfs)

    def _stats(v: list[float]) -> dict | None:
        if not v:
            return None
        return {
            "count": len(v),
            "avg_pct": round(sum(v) / len(v), 2),
            "median_pct": round(median(v), 2),
        }

    return {
        "sample": n,
        "avg_pct": round(sum(perfs) / n, 2) if n else None,
        "median_pct": round(median(perfs), 2) if n else None,
        "red_rate": round(red / n, 3) if n else None,
        "re_limit_rate": round(re_limit / n, 3) if n else None,
        # 均值-中位背离度：正值说明少数大涨拉高了均值，多数人体感更差
        "skew_pct": round(sum(perfs) / n - median(perfs), 2) if n else None,
        "by_board": {k: v for k, v in ((k, _stats(x)) for k, x in by_board.items()) if v},
    }


def self_check(prev_perf: dict, trade_date: date, prev_trade_date: date) -> list[str]:
    """自指与哨兵检查。

    真实市场不可能出现的读数一旦出现，说明上游数据口径错了（最常见：非交易日回退
    导致"昨日池 × 昨日快照"的自指计算）。这类错误的特点是**数字看着完全合理**，
    所以必须显式检查，不能靠人眼发现。
    """
    issues: list[str] = []
    if trade_date and prev_trade_date and trade_date <= prev_trade_date:
        issues.append(f"日期未严格递增（T={trade_date} ≤ T-1={prev_trade_date}），存在自指风险")
    if (prev_perf.get("re_limit_rate") or 0) >= _SENTINEL_RE_LIMIT:
        issues.append("再涨停率=100%，真实市场不可能；疑似非交易日回退导致的自指计算")
    if (prev_perf.get("red_rate") or 0) >= _SENTINEL_RED_RATE:
        issues.append("翻红率=100%，真实市场不可能；疑似自指计算")
    if (prev_perf.get("median_pct") or 0) >= _SENTINEL_MEDIAN:
        issues.append(
            f"昨涨停今日中位 {prev_perf.get('median_pct')}% 接近涨停阈值，疑似自指计算"
        )
    return issues


# ---------------------------------------------------------------- 分轴打分


def heat_axis(limit_up: int, max_board: int, break_rate: float | None, bands: dict | None = None) -> dict:
    """热度轴：市场热不热。不回答"能不能赚到"。

    bands 缺省用模块默认（业界经验值）；配置化覆盖见 band_config.load_bands。
    """
    b = bands or HEAT_BANDS
    p_lu, l_lu = _band(limit_up, b["limit_up"])
    p_mb, l_mb = _band(max_board, b["max_board"])
    p_br, l_br = _band(break_rate, b["break_rate"])
    raw = p_lu + p_mb + p_br
    lvl = _level(raw, HEAT_LEVEL_CUTS)
    return {
        "raw": raw,
        "level": lvl,
        "label": LEVEL_LABELS["heat"][lvl],
        "basis": [
            f"涨停家数 {limit_up}（{l_lu}，{p_lu:+d}）",
            f"最高板 {max_board}（{l_mb}，{p_mb:+d}）",
            f"炸板率 {break_rate if break_rate is not None else '缺失'}（{l_br}，{p_br:+d}）",
        ],
    }


def earning_axis(
    promo_1to2: float | None,
    median_pct: float | None,
    red_rate: float | None,
    limit_down: int | None,
    bands: dict | None = None,
) -> dict:
    """赚钱效应轴：接力盘能不能赚到钱。可否决热度轴。bands 覆盖同 heat_axis。"""
    b = bands or EARNING_BANDS
    p_p, l_p = _band(promo_1to2, b["promo_1to2"])
    p_m, l_m = _band(median_pct, b["median_pct"])
    p_r, l_r = _band(red_rate, b["red_rate"])
    p_d, l_d = _band(limit_down, b["limit_down"])
    raw = p_p + p_m + p_r + p_d
    lvl = _level(raw, EARNING_LEVEL_CUTS)
    return {
        "raw": raw,
        "level": lvl,
        "label": LEVEL_LABELS["earning"][lvl],
        "basis": [
            f"1进2晋级率 {promo_1to2 if promo_1to2 is not None else '缺失'}（{l_p}，{p_p:+d}）",
            f"昨涨停今日中位 {median_pct if median_pct is not None else '缺失'}%（{l_m}，{p_m:+d}）",
            f"翻红率 {red_rate if red_rate is not None else '缺失'}（{l_r}，{p_r:+d}）",
            f"跌停 {limit_down if limit_down is not None else '缺失'}家（{l_d}，{p_d:+d}）",
        ],
    }


def decide_phase(
    heat_level: int,
    earning_level: int,
    *,
    max_board: int = 0,
    limit_up: int = 0,
    height_dropped: bool = False,
) -> tuple[str, str]:
    """查阶段矩阵，再修「冰点 / 退潮」这一对最容易混的边界。

    热度再高，赚钱效应不行也只能给「分歧」，不能是「高潮」——这是本函数存在的原因。

    冰点与退潮都表现为亏钱，区别在**市场还有没有热度残留**：
    退潮是从高位杀下来（最高板回落 / 跌停扩散 / 涨停家数尚未腰斩），
    冰点是已经跌无可跌（最高板 ≤2 且涨停 <25 家）。
    只看当期热度档位会把"正在杀跌的中位市场"误判成冰点，从而漏掉退潮。
    """
    phase = _PHASE_MATRIX[earning_level][heat_level]
    if phase == "冰点" and earning_level == 0 and (
        max_board > 2 or limit_up >= 25 or height_dropped
    ):
        phase = "退潮"
    return phase, (
        f"热度{LEVEL_LABELS['heat'][heat_level]}（级{heat_level}）"
        f" × 赚钱效应{LEVEL_LABELS['earning'][earning_level]}（级{earning_level}）"
    )


# ---------------------------------------------------------------- 主入口


def compute_sentiment(
    *,
    breadth: dict,
    pool_today,
    pool_yesterday,
    snapshot: list[dict],
    trade_date: date,
    prev_trade_date: date,
    max_board_prev: int | None = None,
    break_count: int | None = None,
    bands: dict | None = None,
) -> dict:
    """计算市场情绪。所有日期参数必须由 `trade_calendar` 校验后传入。

    bands: {"heat": {...}, "earning": {...}} 可选覆盖（P0-3 配置化）；
    缺省用模块默认（业界经验值）。
    """
    prev = prev_zt_performance(pool_yesterday, snapshot)
    promo = promotion_rates(pool_today, pool_yesterday)

    boards_today = list(_boards_of(pool_today).values())
    max_board = max(boards_today) if boards_today else 0
    ladder: dict[str, int] = {}
    for b in boards_today:
        key = "5板+" if b >= 5 else f"{b}板"
        ladder[key] = ladder.get(key, 0) + 1
    ladder = {
        k: ladder[k]
        for k in sorted(ladder, key=lambda x: int(x.replace("板+", "").replace("板", "")))
    }

    limit_up = len(pool_today)  # 封单法：以涨停池为准
    limit_down = breadth.get("limit_down")
    # 炸板率优先用真实炸板池家数；没有则退回"价格法 − 封单法"差值，并标注为近似
    if break_count is not None:
        break_rate = round(break_count / max(limit_up + break_count, 1), 3)
        break_note = "真实炸板池"
    else:
        breaks = max(0, (breadth.get("limit_up") or 0) - limit_up)
        break_rate = round(breaks / max((breadth.get("limit_up") or 0) + breaks, 1), 3) or None
        break_note = "近似（价格法−封单法）"

    heat = heat_axis(limit_up, max_board, break_rate, (bands or {}).get("heat"))
    earn = earning_axis(
        promo["promo_1to2"], prev["median_pct"], prev["red_rate"], limit_down,
        (bands or {}).get("earning"),
    )
    height_dropped = bool(max_board_prev and max_board < max_board_prev)
    phase, phase_basis = decide_phase(
        heat["level"],
        earn["level"],
        max_board=max_board,
        limit_up=limit_up,
        height_dropped=height_dropped,
    )
    if height_dropped:
        phase_basis += f"；最高板由 {max_board_prev} 回落至 {max_board}（断板）"

    # ---- 自指 / 哨兵检查：任何一条命中，结论即不可信 ----
    issues = self_check(prev, trade_date, prev_trade_date)
    unreliable = bool(issues)

    # ---- 温度 ----
    up = breadth.get("up") or 0
    down = breadth.get("down") or 0
    s_promo = _clamp((promo["promo_1to2"] if promo["promo_1to2"] is not None else 0.1) / 0.5 * 100)
    s_median = _clamp(((prev["median_pct"] if prev["median_pct"] is not None else -3) + 5) / 10 * 100)
    s_lu = _clamp(limit_up / 100 * 100)
    s_height = _clamp(max_board / 7 * 100)
    s_breadth = _clamp(up / max(up + down, 1) * 100)
    s_break = _clamp(100 - (break_rate or 0.35) * 200)
    temperature = round(
        _TEMPERATURE_WEIGHTS["promo"] * s_promo
        + _TEMPERATURE_WEIGHTS["median"] * s_median
        + _TEMPERATURE_WEIGHTS["limit_up"] * s_lu
        + _TEMPERATURE_WEIGHTS["height"] * s_height
        + _TEMPERATURE_WEIGHTS["breadth"] * s_breadth
        + _TEMPERATURE_WEIGHTS["break"] * s_break,
        1,
    )

    # ---- 置信度 ----
    caveats: list[str] = []
    if prev["sample"] < 10:
        caveats.append(f"昨日涨停样本仅 {prev['sample']} 只，赚钱效应类指标置信度受限")
    if not breadth.get("total"):
        caveats.append("全市场快照缺失，宽度与跌停类指标不可用")
    if break_note.startswith("近似"):
        caveats.append("炸板率为价格法/封单法差值近似，未接真实炸板池")
    if limit_down is None:
        caveats.append("跌停家数缺失，退潮识别信号不完整")
    confidence = "中"
    if not caveats:
        agree = (temperature >= 55) == (earn["level"] >= 2)
        confidence = "高" if agree else "中"
    if caveats:
        confidence = "低"
    if unreliable:
        confidence = "低"

    # ---- 阶段切换条件（把今晚的判断变成明早能对账的东西）----
    switch = {
        "冰点": "1进2 回升至 ≥25% 且涨停 ≥25 家 → 修复",
        "修复": "1进2 ≥25% 且昨涨停中位 >2% → 发酵；中位再转负 → 冰点",
        "发酵": "1进2 ≥40% 且中位 >4% 且涨停 ≥80 家 → 高潮；1进2 <25% → 分歧",
        "高潮": "1进2 跌破 25% 或昨涨停中位 <2% → 分歧；最高板断板且跌停 ≥10 家 → 退潮",
        "分歧": "1进2 回升 ≥25% 且中位 >2% → 发酵；最高板断板且中位 <0% → 退潮",
        "退潮": "涨停 <25 家且最高板 ≤2 板 → 冰点；1进2 回升 ≥25% → 修复",
    }[phase]

    verify_next = [
        f"1进2 晋级率：今日 {promo['promo_1to2']}（回升 ≥25% 印证修复，继续 <25% 印证{phase}）",
        f"昨涨停今日中位：今日 {prev['median_pct']}%（转负则降级）",
        f"最高板：今日 {max_board} 板（断板即退潮信号）",
        f"涨停家数：今日 {limit_up} 家（跌破 60 且炸板率 >30% 确认退潮）",
    ]
    if promo["promo_1to2_base"]:
        verify_next.append(
            f"待晋级基数：昨日 1 板 {promo['promo_1to2_base']} 只，"
            f"明日需 ≥{int(promo['promo_1to2_base'] * 0.25) + 1} 只晋级才能修复"
        )

    def _pct(v: float | None) -> str:
        """比率 → 百分比字符串（展示层口径）。promotion dict 里的原始小数不动——
        分位校准、阶段切换阈值、推送卡片都消费小数口径，只有 indicators 是给人看的。"""
        return "--" if v is None else f"{v * 100:.1f}%"

    indicators = [
        {"name": "涨停家数", "value": limit_up, "note": "封单法（东财涨停池）"},
        {"name": "连板高度", "value": f"{max_board}板", "note": f"昨日最高 {max_board_prev}板" if max_board_prev else ""},
        {"name": "连板家数", "value": sum(1 for b in boards_today if b >= 2), "note": "≥2板"},
        {"name": "首板家数", "value": sum(1 for b in boards_today if b == 1), "note": ""},
        {"name": "1进2 晋级率", "value": _pct(promo["promo_1to2"]),
         "note": f"{promo['promo_1to2_hit']}/{promo['promo_1to2_base']}"},
        {"name": "2进3 晋级率", "value": _pct(promo["promo_2to3"]),
         "note": f"{promo['promo_2to3_hit']}/{promo['promo_2to3_base']}"},
        {"name": "高位存活率", "value": _pct(promo["high_survival"]),
         "note": f"≥3板 {promo['promo_2to3_hit'] and ''}{promo['high_survival_hit']}/{promo['high_survival_base']}"},
        {"name": "昨日涨停今日中位", "value": prev["median_pct"], "note": f"样本{prev['sample']}只"},
        {"name": "昨日涨停今日均值", "value": prev["avg_pct"], "note": f"与中位背离 {prev['skew_pct']}pct"},
        {"name": "翻红率", "value": _pct(prev["red_rate"]), "note": ""},
        {"name": "再涨停率", "value": _pct(prev["re_limit_rate"]), "note": ""},
        {"name": "炸板率", "value": _pct(break_rate), "note": break_note},
        {"name": "跌停家数", "value": limit_down if limit_down is not None else "缺失", "note": ""},
        {"name": "上涨/下跌", "value": f"{up}/{down}", "note": ""},
    ]

    return {
        "phase": phase,
        "phase_unreliable": unreliable,
        "temperature": temperature,
        "confidence": confidence,
        "phase_basis": phase_basis,
        "heat": heat,
        "earning": earn,
        "reasons": heat["basis"] + earn["basis"],
        "promotion": promo,
        "prev_perf": prev,
        "ladder": ladder,
        "indicators": indicators,
        "self_check": issues,
        "misjudge_caveats": caveats,
        "switch_conditions": switch,
        "verify_next": verify_next,
        "trade_date": trade_date.isoformat() if trade_date else None,
        "prev_trade_date": prev_trade_date.isoformat() if prev_trade_date else None,
        "judged_at": datetime.now(timezone.utc).isoformat(),
    }
