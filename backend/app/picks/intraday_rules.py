"""盘中跟踪选股的量化规则库（选股 2.0，docs/summary/stock-strategy.md §5–6）。

## 设计约束（红线级，不可违背）

1. **纯函数、零 IO**。调度（60s 节拍）、数据拉取、提醒分发都在服务层
   （watcher，批次 B）。规则无 IO 才能被批次 D 的回测框架直接回放——
   "回测通过的规则"和"线上跑的规则"必须是同一份代码。
2. **缺数据 ≠ 判定通过**。每个判定项三态：met（满足）/ unmet（不满足）/
   unknown（判不出来）。unknown 绝不计入"已满足"，也不触发证伪，
   只如实呈现"这项判不出来"——沿用项目"判定类字段三态 > 二态"纪律。
3. **阈值集中**。全部常量在本模块顶部，批次 D 回测网格调参只改这里，
   绝不散落在调用方（否则调参回写时必漏）。

## 口径对齐（与既有模块的字符串契约，改动需同步）

- 题材阶段（`theme_service.judge_theme_stage`）：启动 / 发酵 / 高潮 / 分歧 / 退潮
- 市场相位（`sentiment.engine.compute_sentiment`）：冰点 / 修复 / 发酵 / 高潮 / 分歧 / 退潮
  —— **情绪适配的档位不再在本模块手写**：进攻档 = `STRONG_PHASES`、防守档 = `ADVERSE_PHASES`，
  一律 import 自引擎（S2-7 相位副本收编；2026-09-12 收口最后一处遗留副本）
- 梯队角色（`theme_service.classify_role`）：龙头 / 中军 / 跟风
"""
from __future__ import annotations

from app.sentiment.engine import ADVERSE_PHASES as _EBB_PHASES  # S2-7：唯一权威
from app.sentiment.engine import STRONG_PHASES as _ATTACK_PHASES  # S2-7：唯一权威

# ---------------------------------------------------------------- 确认走强（§5.1）
CONFIRM_THEME_PCT_EARLY = 1.5   # 10:00 前板块涨幅确认线（早盘冲高回落常态化，阈值放宽）
CONFIRM_THEME_PCT_LATE = 2.5    # 10:00 后
_EARLY_CUTOFF_MINUTES = 10 * 60
CONFIRM_MIN_LIMIT_UP = 2        # 板块内涨停家数
CONFIRM_HEIGHT_BOARDS = 3       # 或出现 ≥3 板高度股
CONFIRM_LEADER_PCT = 5.0        # 龙头（梯队最高者）涨幅
CONFIRM_VOLUME_RATIO = 1.5      # 量比（板块成交额历史未落库前的降级口径，§5.1#4）
CONFIRM_PROMO_PCTILE = 20.0     # 环境项：promo_1to2 分位下限（低于 = 接力环境证伪）

# ---------------------------------------------------------------- 证伪放弃（§5.2）
FALSIFY_DRAWDOWN_PCT = 2.0      # 较盘中峰值回撤
FALSIFY_NEGATIVE_BEATS = 15     # 转负需连续拍数（60s/拍 ≈ 15 分钟）

# ---------------------------------------------------------------- 买点（§6.3）
PULLBACK_VOLUME_RATIO = 0.8     # 回调企稳的缩量线
BREAKOUT_VOLUME_RATIO = 2.0     # 放量突破
BREAKOUT_STOP_PCT = 3.0         # 突破位回撤止损（%）
RESEAL_STOP_PCT = 2.0           # 回封止损：炸板价下方（%）
POSITION_BASE = 10.0            # 基础仓位（%）
POSITION_CAP = 20.0             # 仓位上限（%）
# 确认强度系数：全部满足且无 unknown → 1.0；满足率 80%/60% → 0.75/0.5；其余 0
_STRENGTH_COEF = {1.0: 1.0, 0.8: 0.75, 0.6: 0.5}


def _chk(key: str, label: str, met: bool | None, threshold: str, detail: str) -> dict:
    """单项判定结果。met=None 表示 unknown（数据缺失），绝不冒充通过或证伪。"""
    return {"key": key, "label": label, "met": met, "threshold": threshold, "detail": detail}


def _theme_threshold(now_minutes: int | None) -> float:
    if now_minutes is None or now_minutes < _EARLY_CUTOFF_MINUTES:
        return CONFIRM_THEME_PCT_EARLY
    return CONFIRM_THEME_PCT_LATE


def compute_volume_ratio(
    vol_today: float | None,
    vol_prev_day: float | None,
    now_minutes: int | None,
) -> float | None:
    """近似量比（§5.1#4 降级口径：板块成交额历史未落库前用，2026-09-02 接线）。

    = 当日累计量 / 昨日全天量 / 已开市时间占比——等价于"当日每分钟均量 vs
    昨日每分钟均量"（240 分钟基准），与 minute_signals 的 breakout 量比同口径。
    已开市占比 clamp ≥1/240（开盘首分钟）防除零；vol_today<=0（未开市/停牌）
    直接 None，不走放大路径。
    缺任一输入（含昨日量 <=0）→ None（unknown 三态，绝不臆造）。
    精确同期基线（TDX 5 分钟累计量，load_vr_baseline）待批次 D 落库后切换；
    本函数纯计算零 IO，输入的量纲一致性（均为股）由调用方保证。
    """
    if not vol_today or not vol_prev_day or vol_prev_day <= 0 or now_minutes is None:
        return None
    am = min(max(now_minutes - 570, 0), 120)   # 9:30–11:30
    pm = min(max(now_minutes - 780, 0), 120)   # 13:00–15:00
    elapsed = min(max((am + pm) / 240, 1 / 240), 1.0)
    return round(vol_today / vol_prev_day / elapsed, 2)


# ---------------------------------------------------------------- 盘前方向排序（§4.2）

#: 业绩/财报结果型题材关键词。这类 tag 是个股财报事实的归因（业绩增长/
#: 半年报增长/预增…），不是市场驱动题材：官方概念目录里没有对应板块指数，
#: 板块涨幅/量比永远无法确认，占坑 top3 只会稀释有效样本——2026-09-02
#: 120 日回测 unmatched 103/360（28.6%）的根因。回测伪预判与线上
#: rank_directions 必须使用同一过滤（同一份规则代码红线）。
PERFORMANCE_TAG_HINTS = (
    "业绩", "中报", "半年报", "年报", "一季报", "三季报", "季报",
    "预增", "预盈", "扭亏",
)


def is_performance_tag(tag: str | None) -> bool:
    """题材 tag 是否为业绩/财报结果型（子串匹配，纯函数）。"""
    return bool(tag) and any(h in tag for h in PERFORMANCE_TAG_HINTS)


def rank_directions(evidences: list[dict], phase: str | None = None) -> list[dict]:
    """盘前预判：证据 → 方向排序，取 top 1–3 由调用方截断。

    Args:
        evidences: 每项 {direction, event_strength, theme_momentum, echelon,
                    defensive, logic}。event_strength 为事件强度和
                    （strength × tier_weight × certainty_weight，可正可负）；
                    theme_momentum/echelon ∈ 0–100；defensive=防守方向（红利银行等）。
        phase: 昨日市场相位（情绪适配项用）。

    Returns:
        按 score 降序的列表副本，附 score 与 basis。输入缺失字段按 0 处理
        （盘前证据天然稀疏，缺事件强度≠没有方向，但会在 basis 里露出来）。

    业绩/财报结果型方向（is_performance_tag 命中）由**调用方**在构造
    evidences 时排除——本函数不做隐式过滤，保持排序器纯打分语义。
    """
    scored = []
    for ev in evidences:
        event = float(ev.get("event_strength") or 0.0)
        momentum = float(ev.get("theme_momentum") or 0.0)
        ech = float(ev.get("echelon") or 0.0)
        defensive = bool(ev.get("defensive"))
        fit = 10.0 if (
            (defensive and phase in _EBB_PHASES)
            # 非防守方向直接用引擎的 STRONG_PHASES（进攻语义：修复/发酵/高潮）——
            # **不再手写字面量**。「修复」曾长期缺席此处、与 STRONG_PHASES 不一致，
            # 是 S2-7 相位副本收编遗漏的最后一处；2026-09-12 改为引用生产常量后，
            # 两模块共享同一口径，**同类分歧不可能再出现**（改动需改引擎一处）。
            #
            # 语义边界（勿误读）：fit 是**相位常量项**，所以本档位只决定
            # 「防守 ↔ 非防守」的**跨组倾斜**（+3.0），**不改变任一组内的相对次序**。
            # 逐相位倾斜由 `tests/test_intraday_rules.py` 钉住。
            #
            # 为什么并入「修复」有据（2026-09-12 核验，`scripts/verify_intraday_phase_fit.py`）：
            # 「进攻篮」（当日涨停池成员）次日**可成交口径**（次日开盘买入、剔除一字板开盘）
            # 市场中性超额——修复 **−0.528%** vs 强势组（发酵+高潮）−0.404%、
            # 弱势组（退潮+冰点）−0.034% ⇒ 修复**明显更接近强势组**。
            # ⚠️ **收盘口径会给出相反结论**（修复 −0.528 → 更接近弱势组），因为
            # 「当日封板买不到」，收盘价把不可成交的封板溢价算成了收益 ——
            # 这正是 KB-STOCK-31 要求「当日封板 ⇒ 必须双口径对照」的原因。
            # 已知边界：该代理是「进攻 vs 大盘」，不是「进攻 vs 防守」（防守方向无成分
            # 可还原——官方题材目录里没有干净的防守行业题材）；且 8 份盘前简报里
            # 防守方向占 top3 席位 **0/24**，故本口径变更的**实际影响面很小**。
            or (not defensive and phase in _ATTACK_PHASES)
        ) else 0.0
        score = round(event * 1.0 + momentum * 0.6 + ech * 0.4 + fit * 0.3, 2)
        basis = [
            f"事件强度 {event}",
            f"题材动能 {momentum}×0.6",
            f"梯队 {ech}×0.4",
            f"情绪适配 {'+' if fit else ''}{fit}",
        ]
        scored.append({**ev, "score": score, "basis": "; ".join(basis)})
    return sorted(scored, key=lambda x: -x["score"])


# ---------------------------------------------------------------- 盘中确认（§5.1）


def confirm_signal(
    *,
    theme_pct: float | None,
    theme_limit_up: int | None,
    theme_max_boards: int | None,
    leader_pct: float | None,
    volume_ratio: float | None,
    promo_percentile: float | None,
    phase: str | None,
    now_minutes: int | None,
    theme_pct_thr: float | None = None,
    volume_ratio_thr: float | None = None,
) -> dict:
    """确认走强：五项全部满足才确认；量能项判不出来时按 0.75 档降级确认。

    Args:
        theme_pct_thr / volume_ratio_thr: 阈值覆盖（批次 D 回测网格注入）。
            None = 用模块常量（线上唯一路径）；回测传入网格值时不走 early/late
            分段（threshold 文案标注"回测网格"）。除回测框架外禁止传值——
            阈值必须集中常量表，散落调用方是调参回写时必漏的坑。

    Returns:
        {confirmed, checks, met_count, unknown_count, strength}。
        strength ∈ {1.0, 0.75, 0.5, 0.0}——仓位系数（§5.3），unknown 会
        压低系数但不清零（比如量比缺失时其余四项全过，仍可给 0.75 提示）。
    """
    thr = theme_pct_thr if theme_pct_thr is not None else _theme_threshold(now_minutes)
    thr_note = "回测网格" if theme_pct_thr is not None else (
        "10:00 前" if thr == CONFIRM_THEME_PCT_EARLY else "10:00 后"
    )
    vr_thr = volume_ratio_thr if volume_ratio_thr is not None else CONFIRM_VOLUME_RATIO
    checks = [
        _chk(
            "theme_pct", "板块涨幅",
            None if theme_pct is None else theme_pct >= thr,
            f"≥{thr}%（{thr_note}）",
            "缺失" if theme_pct is None else f"{theme_pct}%",
        ),
        _chk(
            "theme_height", "板块涨停/高度",
            None if theme_limit_up is None and theme_max_boards is None
            else (theme_limit_up or 0) >= CONFIRM_MIN_LIMIT_UP
            or (theme_max_boards or 0) >= CONFIRM_HEIGHT_BOARDS,
            f"涨停≥{CONFIRM_MIN_LIMIT_UP} 家 或 高度≥{CONFIRM_HEIGHT_BOARDS} 板",
            "缺失" if theme_limit_up is None and theme_max_boards is None
            else f"涨停 {theme_limit_up} 家 / 最高 {theme_max_boards} 板",
        ),
        _chk(
            "leader_pct", "龙头强度",
            None if leader_pct is None else leader_pct >= CONFIRM_LEADER_PCT,
            f"≥{CONFIRM_LEADER_PCT}% 或涨停",
            "缺失" if leader_pct is None else f"{leader_pct}%",
        ),
        _chk(
            "volume_ratio", "量能",
            None if volume_ratio is None else volume_ratio >= vr_thr,
            f"量比≥{vr_thr}",
            "缺失" if volume_ratio is None else f"{volume_ratio}",
        ),
        _chk(
            "environment", "环境",
            None if promo_percentile is None and phase is None
            else (promo_percentile is None or promo_percentile >= CONFIRM_PROMO_PCTILE)
            and (phase is None or phase not in _EBB_PHASES),
            f"promo 分位≥{CONFIRM_PROMO_PCTILE} 且非退潮/冰点",
            f"promo 分位 {'缺失' if promo_percentile is None else promo_percentile}"
            f" / 相位 {phase or '缺失'}",
        ),
    ]
    met = sum(1 for c in checks if c["met"] is True)
    unmet = sum(1 for c in checks if c["met"] is False)
    unknown = sum(1 for c in checks if c["met"] is None)
    # 量能降级确认（§5.1#4）：板块成交额历史未落库前 volume_ratio 恒 unknown，
    # 若按"unknown 视为未满足"处理，confirmed 永假 → 盘中提醒整条死掉
    # （2026-09-02 定案：实现违背自身 docstring 设计，选 b 放宽）。
    # 放宽仅限量能项：量比判不出来且其余四项全部明确满足 → 仍确认，
    # 强度按满足率 4/5 → 0.75（缺失项在提醒第 7/8 段如实标注）。
    # 其余项 unknown 不享受此放宽：环境项是证伪入口，缺证据不下确认结论。
    vr_unknown = any(c["key"] == "volume_ratio" and c["met"] is None for c in checks)
    confirmed = unmet == 0 and (
        met == len(checks) or (vr_unknown and met == len(checks) - 1)
    )
    # 强度系数按全部检查项的满足率算：unknown 与 unmet 同样压低系数（4/5 → 0.75、
    # 3/5 → 0.5），但不区分"明确不满足"与"判不出来"——两者都不该给满强度。
    strength = _STRENGTH_COEF.get(round(met / len(checks), 2), 0.0)
    return {
        "confirmed": confirmed,
        "checks": checks,
        "met_count": met,
        "unmet_count": unmet,
        "unknown_count": unknown,
        "strength": strength,
    }


# ---------------------------------------------------------------- 盘中证伪（§5.2）


def falsify_signal(
    *,
    peak_pct: float | None,
    current_pct: float | None,
    below_zero_beats: int = 0,
    leader_broke_board: bool | None = None,
    theme_limit_down: int | None = None,
    promo_percentile: float | None = None,
    phase: str | None = None,
) -> dict:
    """证伪放弃：任一触发即证伪。返回触发明细（可解释），数据缺失的项跳过。"""
    triggers: list[dict] = []
    if peak_pct is not None and current_pct is not None:
        dd = round(peak_pct - current_pct, 2)
        if dd >= FALSIFY_DRAWDOWN_PCT:
            triggers.append({
                "key": "drawdown",
                "detail": f"较盘中峰值 {peak_pct}% 回撤 {dd} 个百分点（≥{FALSIFY_DRAWDOWN_PCT}）",
            })
    if leader_broke_board and (theme_limit_down or 0) >= 1:
        triggers.append({
            "key": "leader_break",
            "detail": f"龙头炸板且板块内跌停 {theme_limit_down} 家",
        })
    if current_pct is not None and current_pct < 0 and below_zero_beats >= FALSIFY_NEGATIVE_BEATS:
        triggers.append({
            "key": "negative_streak",
            "detail": f"板块涨幅转负已持续 {below_zero_beats} 拍（≥{FALSIFY_NEGATIVE_BEATS} 拍 ≈ 15 分钟）",
        })
    if (promo_percentile is not None and promo_percentile < CONFIRM_PROMO_PCTILE) or (
        phase is not None and phase in _EBB_PHASES
    ):
        triggers.append({
            "key": "environment",
            "detail": f"接力环境证伪（promo 分位 {promo_percentile} / 相位 {phase}）→ 全部方向降级观察",
        })
    return {"falsified": bool(triggers), "triggers": triggers}


# ---------------------------------------------------------------- 追涨/潜伏/观望（§6.1）


def entry_mode(theme_stage: str | None, phase: str | None) -> tuple[str, str]:
    """返回 (模式, 依据)。模式 ∈ 追涨 / 追涨减半 / 潜伏 / 观望。"""
    if phase in _EBB_PHASES:
        return "观望", f"市场相位「{phase}」，仅记录不提醒"
    if theme_stage == "退潮":
        return "观望", "题材阶段「退潮」"
    if theme_stage == "分歧":
        return "潜伏", "题材高位分歧，等回调企稳低吸，不追高"
    if theme_stage == "高潮":
        return "追涨减半", "题材高潮，只做龙头且仓位减半（高位风险）"
    if theme_stage in ("启动", "发酵"):
        return "追涨", f"题材「{theme_stage}」，只做龙头/中军，跟风不追"
    return "观望", f"题材阶段缺失（{theme_stage}），不给出方向性结论"


# ---------------------------------------------------------------- 退潮 vs 趋势结束（§6.2）


def ebb_or_end(
    *,
    promo_percentile: float | None,
    height_gap: int | None,
    leader_below_ma5: bool | None,
    leader_below_ma10: bool | None,
) -> tuple[str, dict]:
    """判定板块是「短暂退潮（可修复）」还是「趋势结束（离场）」或「观望带」。

    三证据：晋级率分位 / 高度断层（最高板−次高板）/ 龙头均线归属。
    全部证据同向才下"趋势结束"的重结论；任一反证即归"可修复"；其余观望带。
    """
    basis: dict = {
        "promo_percentile": promo_percentile,
        "height_gap": height_gap,
        "leader_below_ma5": leader_below_ma5,
        "leader_below_ma10": leader_below_ma10,
    }
    end = (
        promo_percentile is not None and promo_percentile < CONFIRM_PROMO_PCTILE
        and height_gap is not None and height_gap >= 2
        and leader_below_ma10 is True
    )
    if end:
        return "趋势结束", {**basis, "detail": "晋级率低分位 + 高度断层 ≥2 + 龙头破 MA10，三证据同向"}
    recoverable = (
        (promo_percentile is not None and promo_percentile >= 40.0)
        or (height_gap is not None and height_gap <= 1)
        or leader_below_ma5 is False
    )
    if recoverable:
        return "短暂退潮", {**basis, "detail": "晋级率尚可 / 高度未断层 / 龙头仍站 MA5（至少一项反证）"}
    return "观望带", {**basis, "detail": "证据不足以区分退潮与结束，不给方向性结论"}


# ---------------------------------------------------------------- 进场计划（§6.3）


def plan_pullback(
    *, price: float, ma5: float | None, ma10: float | None, volume_ratio: float | None,
    theme_pct: float | None, intraday_low_held: bool | None,
) -> dict:
    """回调企稳（潜伏）：回踩均线缩量 + 分时不破前低 + 板块仍红。"""
    ok, reasons = True, []
    for name, cond in (
        ("量比 <0.8（缩量回踩）", None if volume_ratio is None else volume_ratio < PULLBACK_VOLUME_RATIO),
        ("分时不破前低", intraday_low_held),
        ("板块涨幅仍 >0", None if theme_pct is None else theme_pct > 0),
    ):
        if cond is None:
            ok = False
            reasons.append(f"{name}：判不出来")
        elif not cond:
            ok = False
            reasons.append(f"{name}：不满足")
    stop = None
    if ma10 is not None:
        stop = round(ma10 * 0.99, 2)  # MA10 下方 1%
    buy_low = round(ma5 * 0.98, 2) if ma5 is not None else None
    buy_high = round(ma5 * 1.02, 2) if ma5 is not None else None
    return {
        "mode": "回调企稳", "ready": ok,
        "buy_range": [buy_low, buy_high] if buy_low is not None else None,
        "stop": stop, "unmet": reasons,
        "note": "买入区间=MA5±2%；止损=MA10 下方 1%；缺均线则不给区间",
    }


def plan_breakout(
    *, price: float, platform_high: float | None, volume_ratio: float | None,
    theme_pct: float | None,
) -> dict:
    """放量突破（追涨）：破平台 + 量比 ≥2 + 板块共振。"""
    ok, reasons = True, []
    broke = None if platform_high is None else price >= platform_high
    for name, cond in (
        (f"现价 ≥ 平台高点 {platform_high}", broke),
        (f"量比 ≥{BREAKOUT_VOLUME_RATIO}", None if volume_ratio is None else volume_ratio >= BREAKOUT_VOLUME_RATIO),
        ("板块涨幅 ≥1.5%（共振）", None if theme_pct is None else theme_pct >= CONFIRM_THEME_PCT_EARLY),
    ):
        if cond is None:
            ok = False
            reasons.append(f"{name}：判不出来")
        elif not cond:
            ok = False
            reasons.append(f"{name}：不满足")
    buy = round(platform_high, 2) if platform_high is not None and broke else None
    stop = round(platform_high * (1 - BREAKOUT_STOP_PCT / 100), 2) if platform_high is not None else None
    return {
        "mode": "放量突破", "ready": ok,
        "buy_range": [buy, round(buy * 1.015, 2)] if buy is not None else None,
        "stop": stop, "unmet": reasons,
        "note": "买入区间=突破价+1.5%；止损=平台高点下方 3%",
    }


def plan_reseal(*, limit_price: float | None, broke_price: float | None, sealed: bool) -> dict:
    """龙头回封：炸板后回封，止损=炸板价下方 2%。"""
    ok = bool(sealed and limit_price is not None)
    reasons = [] if ok else ["未回封或涨停价缺失"]
    return {
        "mode": "龙头回封", "ready": ok,
        "buy_range": [limit_price, limit_price] if limit_price is not None else None,
        "stop": round(broke_price * (1 - RESEAL_STOP_PCT / 100), 2) if broke_price else None,
        "unmet": reasons,
        "note": "排板买入；封单比与二次分时不破均价线由 dragon_service.entry_checklist 复核",
    }


def position_size(strength: float) -> float:
    """仓位 = 基础 10% × 确认强度系数，上限 20%。strength 来自 confirm_signal。"""
    return round(min(POSITION_BASE * max(0.0, strength), POSITION_CAP), 1)


# ---------------------------------------------------------------- 八段式提醒（§5.3）


def build_alert(
    *,
    direction: str,
    symbol: str,
    name: str,
    role: str,
    confirm: dict,
    logic: str,
    buy_range: list[float] | None,
    stop: float | None,
    position: float,
    risks: list[str],
    plan_note: str = "",
) -> str:
    """固定格式提醒文本。调用方负责去重与 NotifierRegistry 分发。"""
    met_lines = " · ".join(
        f"{c['label']} {c['detail']}" for c in confirm["checks"] if c["met"] is True
    ) or "无"
    unknown_lines = [c["label"] for c in confirm["checks"] if c["met"] is None]
    br = f"{buy_range[0]}–{buy_range[1]}" if buy_range else "缺关键数据，不给区间"
    lines = [
        f"【盘中机会】方向：{direction}",
        f"1. 个股：{name}（{symbol}）· 梯队角色：{role}",
        f"2. 触发指标：{met_lines}",
        f"3. 逻辑：{logic}",
        f"4. 买入区间：{br}" + (f"（{plan_note}）" if plan_note else ""),
        f"5. 止损：{stop if stop is not None else '缺数据，不给止损位'}",
        f"6. 仓位建议：{position}%（确认强度 {confirm.get('strength')}）",
        f"7. 风险点：{'；'.join(risks) if risks else '无特别风险标注'}",
        "8. 状态：非投资建议，模拟跟踪"
        + (f"；数据缺失项：{'、'.join(unknown_lines)}" if unknown_lines else ""),
    ]
    return "\n".join(lines)
