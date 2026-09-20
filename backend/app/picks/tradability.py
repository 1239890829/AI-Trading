"""可参与性判定与题材联动挖掘（2026-09-15 用户选股口径升级）。

**政策**（用户 2026-09-15 指令，原文要点）：

> 不要把开盘就已涨停的个股选入猎场，因为这类股票根本无法买入参与，
> 单纯为了拉高胜率这样做没有意义。所有加入猎场的个股必须是投资者实际可以参与的。
> 开盘即涨停的个股只能作为参考信息：分析它们所属的板块或题材概念，
> 若多数涨停个股集中在同一板块或同一题材概念，则进一步挖掘该板块或题材概念内
> 其他尚未涨停、但有联动机会的可参与个股，并将这些个股加入猎场。

**病灶**：猎场「盘中跟踪」的候选此前逐条取自 `build_theme_board` 的**涨停梯队**
（`intraday_opportunity.assemble` 的 `stocks`），而 `certainty` 判「高」的必要证据是
封单额／首封时间／炸板次数——**全部是封板后的量**。判据越硬（封得越实），越买不到：
名单天然全是已封板个股，统计上的胜率来自「事后已知谁封死了」，对操盘无指导意义。

**两个场景的"不可参与"判据不同（不是同一个函数换参数）**：

| 场景 | 不可参与的定义 | 理由 |
|---|---|---|
| 盘中（当日实时） | 当前封在涨停板（`sealed=True`） | 当前无法参与，后续若开板须按新快照重评 |
| 历史/盘后 | 首封时间、炸板次数等 | 只作历史身份与复盘证据，不能替代 current 状态 |

**开盘即涨停**（`is_open_sealed`）：首封时间 ≤ 09:30:00，覆盖集合竞价一字板
（09:25 竞价即封）与开盘秒板。它是强历史特征，但不能据此预言全天持续封板；
当前是否仍封必须由带版本的实时/准实时快照判定。

**题材联动挖掘**：不新建数据源——官方题材容器用**成分重叠挂靠**定位
（与 `services/official_match` 同口径同常量：命中 ≥2 只、成分 ≤300 只，
避免"融资融券"这类全市场性大概念），容器内未涨停成分股 × 全市场快照筛选。
这样「卡片上显示的官方概念」与「挖掘用的容器」必然是同一个东西。

**纯函数**：本模块零 IO。官方成分倒排、全市场快照一律由调用方取好后传入
（取数与判定分开，判定才可直测、可回放）。

⚠️ **阈值是经验初值，未经回测校准**（与 `board_surge` 同纪律）：调参时以 basis
文案里的证据回放历史样本，统计可参与组的 T+1 胜率后定稿。
"""
from __future__ import annotations

from app.picks.halt_risk import board_of
from app.picks.pre_limit_radar import board_limit_pct, pre_limit_floor, seal_threshold
from app.services.official_match import MAX_CONCEPT_SIZE, MIN_HITS
from app.services.theme_service import parse_hhmmss

# ---------------------------------------------------------------- 可调参数（集中一处）

#: 开盘即涨停判定线：首封 ≤ 09:30:00（含集合竞价一字板与开盘秒板）
OPEN_SEAL_CUTOFF_HHMMSS = 93000
#: 集合竞价结束时刻（仅用于依据文案措辞）：竞价 09:25 撮合，成交时间戳落在
#: 09:25:00–09:25:59，个别源记到 09:26 —— 取 09:26 作为"竞价即封"的上界。
AUCTION_END_HHMMSS = 92600

#: 「涨停集中」的题材判据：题材内涨停家数下限 + 占当日涨停总数比例下限
MIN_THEME_LIMIT_UPS = 3
MIN_THEME_SHARE = 0.10
#: 一次最多挖掘的主题材数（按涨停家数降序）
MAX_THEMES = 3
#: 每个题材最多补入的联动候选数（**展示容量**：猎场题材卡片/盘中跟踪列表）
PER_THEME = 8
#: 盘后组合候选池的每题材配额 —— 刻意小于展示容量：候选池有 40 席上限、
#: 深度评分只有 24 席，配额必须让"可参与联动股"进得去又挤不空其他来源。
#: 3 题材 × 4 = 最多 12 只，占候选池 30%、深评席位的一半。
CANDIDATE_PER_THEME = 4

#: 联动迹象：当日涨幅下沿（%）与中档线（%）
LINKAGE_MIN_PCT = 1.0
LINKAGE_MID_PCT = 3.0
#: 流动性门槛：当日成交额（元）——低成交额的票"可参与"但接了卖不掉
LINKAGE_MIN_AMOUNT = 3.0e7

#: 单个题材最多挂靠的官方容器数（第一个用于挖掘，其余留作展示口径）
MAX_CONTAINERS = 2

#: 题材阶段基座：这两档下不给联动候选高置信（与 `certainty` 的基座口径一致）
WEAK_STAGES = ("退潮", "分歧")


# ---------------------------------------------------------------- 板块权限（账户级约束）

#: 账户当前只开**沪深主板** —— 用户 2026-09-15 明确「创业板的不进，只有主板的权限现在」。
#:
#: 权限变了**只改这一处**：全部消费点（盘中可参与候选 / 盘后组合 / 涨停梯队参考区 /
#: 猎场台账 / 临板雷达提醒）都走 `is_tradable()` 同一个判据，不存在"改了一半"的中间态。
#: 想放开某一板块，把它的键加进来即可（键见 `BOARD_LABELS`）。
TRADABLE_BOARD_KEYS = frozenset({"sh_main", "sz_main", "st"})

#: 板块键 → 中文（仅用于依据文案；未知键回退原键，不臆造）
BOARD_LABELS = {
    "sh_main": "沪市主板",
    "sz_main": "深市主板",
    "st": "主板ST",
    "gem": "创业板",
    "star": "科创板",
    "bse": "北交所",
    "sh_b": "沪市B股",
    "sz_b": "深市B股",
}


def board_key(symbol: str, name: str | None = None) -> str:
    """板块键。**分类单点 = `halt_risk.board_of`**（涨限/基准指数/异动阈值同源），
    本函数只**追加** B 股识别（沪 900xxx / 深 200xxx）——`board_of` 的兜底分支会把
    它们归成 `sz_main`，而 B 股需单独账户权限，属"不可参与"。

    刻意**不**把 B 股塞进 `board_of`：那是被涨限/异动/基准指数共用的判据，
    为一个与它们无关的口径去改它，等于让"改一处影响三处"。
    """
    sym = str(symbol or "")
    if sym.startswith("900"):
        return "sh_b"
    if sym.startswith("200"):
        return "sz_b"
    return board_of(sym, name)


def is_tradable(symbol: str, name: str | None = None) -> bool:
    """该代码是否在**账户可交易板块**内（当前 = 沪深主板，含主板 ST）。

    与 `assess()`（可参与性）分工不同、**判据也不可互替**：
    `assess` 回答“当前是否封板、能否进入参与评估”（不等价于成交保证），
    `is_tradable` 回答"这个板块我有没有权限买"（账户属性，与盘面无关）。
    两者都为真，才是"真正可操作"。
    """
    return board_key(symbol, name) in TRADABLE_BOARD_KEYS


def board_label(symbol: str, name: str | None = None) -> str:
    """板块中文名（依据文案用）。未知板块回退原键——不臆造中文名。"""
    key = board_key(symbol, name)
    return BOARD_LABELS.get(key, key)


# ---------------------------------------------------------------- 开盘即涨停


def is_open_sealed(first_seal_time: str | None) -> bool | None:
    """开盘即涨停（首封 ≤ 09:30）三态判定。

    返回 `None` 表示**不可判**（时间缺失/无法解析）——三态纪律：判不了不冒充
    "非开盘即封"，否则会把"数据缺失"当成"可以参与"。
    """
    t = parse_hhmmss(first_seal_time)
    if t is None:
        return None
    return t <= OPEN_SEAL_CUTOFF_HHMMSS


def _hhmm(first_seal_time: str | None) -> str | None:
    """首封时间 → ``HH:MM`` 展示串；不可解析返回 None（不臆造）。"""
    t = parse_hhmmss(first_seal_time)
    if t is None:
        return None
    return f"{t // 10000:02d}:{t % 10000 // 100:02d}"


def assess(*, sealed: bool | None, first_seal_time: str | None = None) -> dict:
    """当前可参与性三态；首封时间只作历史证据，不能替代 current 状态。

    `sealed=True/False` 必须来自当前有效时点的盘口/快照；`None` 就是 current unknown。
    即使 09:25 首封，也不能据此预言“全天封死”——真实涨停池存在开板→再封。
    """
    seal_t = parse_hhmmss(first_seal_time)
    hhmm = _hhmm(first_seal_time)
    if sealed is True:
        if seal_t is not None and seal_t <= OPEN_SEAL_CUTOFF_HHMMSS:
            when = "竞价即封" if seal_t <= AUCTION_END_HHMMSS else "开盘即封"
            return {
                "level": "不可参与",
                "basis": f"当前仍封在涨停板（{when}，首封 {hhmm}）——当前不可参与；后续若开板须按新快照重评",
            }
        return {
            "level": "不可参与",
            "basis": f"当前仍封在涨停板（首封 {hhmm or '时间未知'}）——当前不可参与；后续状态需按新快照重评",
        }
    if sealed is False:
        return {
            "level": "可参与",
            "basis": "当前未封板，可进入参与评估；未核盘口深度/排队，不保证成交",
        }
    history = f"（今日首封 {hhmm}）" if hhmm else ""
    return {
        "level": "unknown",
        "basis": f"当前封板状态无可信时点证据{history}，不可用首封历史替代 current 判定",
    }


# ---------------------------------------------------------------- 涨停下沿/跑道


def seal_metrics(symbol: str, name: str = "", pct: float | None = None) -> dict:
    """涨停幅 / 封板线 / 距封板跑道（委托 `pre_limit_radar` 的单点实现，不重写）。"""
    limit = board_limit_pct(symbol, name)
    out: dict = {"limit_pct": limit, "seal_line": round(seal_threshold(limit), 2),
                 "pre_limit_floor": pre_limit_floor(limit)}
    if pct is not None:
        out["runway_pct"] = round(seal_threshold(limit) - float(pct), 2)
        out["in_pre_limit"] = pre_limit_floor(limit) <= float(pct) < seal_threshold(limit)
    return out


# ---------------------------------------------------------------- 涨停集中度


def is_concentrated(
    limit_up_count: int,
    limit_up_total: int | None,
    *,
    min_count: int = MIN_THEME_LIMIT_UPS,
    min_share: float = MIN_THEME_SHARE,
) -> bool:
    """「多数涨停集中在同一题材」判据（联动挖掘的准入条件）。

    家数 ≥ `min_count`；占比判据仅在分母可得时生效（`limit_up_total` 为 None/0
    ⇒ 数据缺失，不拿"占比 0%"误杀，只按家数判——三态纪律：判不了的那一维不计入）。
    """
    if limit_up_count < min_count:
        return False
    if limit_up_total and limit_up_total > 0:
        return (limit_up_count / limit_up_total) >= min_share
    return True


def theme_focus(
    theme_stats: dict[str, dict],
    *,
    limit_up_total: int,
    max_themes: int = MAX_THEMES,
) -> list[dict]:
    """从涨停池题材聚合里挑出「涨停集中」的主题材（纯函数）。

    :param theme_stats: `limit_up_context()["themes"]`（`{题材: {symbols, max_boards, ...}}`）
    :param limit_up_total: 当日涨停总数（占比分母）；≤0 时占比判据不参与（只按家数）

    判据：涨停家数 ≥ :data:`MIN_THEME_LIMIT_UPS` **且** 占比 ≥ :data:`MIN_THEME_SHARE`
    （分母可得时）。按家数降序、其次最高连板降序取前 `max_themes`。

    占比条件的作用是排除"全市场只有 3 家涨停、其中 3 家同题材"这种极端日——
    那天谈不上"多数涨停集中在同一题材"，硬挖只会得到噪音。
    """
    out: list[dict] = []
    for theme, st in (theme_stats or {}).items():
        symbols = st.get("symbols") or []
        count = len(symbols)
        if not is_concentrated(count, limit_up_total):
            continue
        share = (count / limit_up_total) if limit_up_total > 0 else None
        out.append(
            {
                "theme": theme,
                "symbols": list(symbols),
                "count": count,
                "share": round(share, 4) if share is not None else None,
                "max_boards": st.get("max_boards") or 0,
                "basis": (
                    f"题材内涨停 {count} 家"
                    + (f"（占当日涨停 {share * 100:.0f}%）" if share is not None else "")
                    + f" · 最高 {st.get('max_boards') or 0} 板"
                ),
            }
        )
    out.sort(key=lambda t: (-t["count"], -t["max_boards"]))
    return out[:max_themes]


def index_views(
    symbol_index: dict[str, list[tuple[str, str]]],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """`symbol → [(概念 code, name)]` 倒排 → ``(概念成分规模表, code → 成分 symbol)``。

    规模表是"全市场性大概念"过滤的输入（口径与 `official_match` 一致）；
    反向表是挖掘的取数入口。一次遍历同时得到两者，不重复扫成分表。
    """
    sizes: dict[str, int] = {}
    members: dict[str, list[str]] = {}
    for symbol, concepts in (symbol_index or {}).items():
        for code, _name in concepts:
            sizes[code] = sizes.get(code, 0) + 1
            members.setdefault(code, []).append(symbol)
    return sizes, members


def resolve_containers(
    member_symbols: set[str],
    symbol_index: dict[str, list[tuple[str, str]]],
    concept_sizes: dict[str, int],
    *,
    max_concept_size: int = MAX_CONCEPT_SIZE,
    min_hits: int = MIN_HITS,
    max_containers: int = MAX_CONTAINERS,
) -> list[dict]:
    """题材成员 → 官方题材容器（成分重叠挂靠，命中降序）。

    同一个题材在**两套口径**下名字不同：涨停原因动态标签（「功能糖」）vs 概念板块
    目录（「代糖概念」）。直接按名字匹配对动态标签完全失效（`official_match` 的
    根因分析），故沿用**成分重叠**反向挂靠——容器命中该题材 ≥`min_hits` 只成员即成立。

    同时过滤 `max_concept_size` 以上的全市场性概念（融资融券/深股通…）：这类容器
    挖出来的"联动股"与题材毫无关系。
    """
    counts: dict[tuple[str, str], int] = {}
    for sym in member_symbols or set():
        for code, name in symbol_index.get(sym, []):
            if concept_sizes.get(code, 10**9) <= max_concept_size:
                counts[(code, name)] = counts.get((code, name), 0) + 1
    hits = [
        {"code": code, "name": name, "hits": n}
        for (code, name), n in counts.items()
        if n >= min_hits
    ]
    hits.sort(key=lambda x: (-x["hits"], x["code"]))
    return hits[:max_containers]


# ---------------------------------------------------------------- 联动候选


def linkage_confidence(
    *, theme_limit_ups: int, theme_stage: str | None, pct: float | None, limit_pct: float
) -> dict:
    """联动置信度三态（高/中/低/unknown）+ 依据。

    与 `intraday_opportunity.certainty` **刻意不同**：certainty 的证据是封板质量，
    而联动候选按 current 快照**当前未封板**，即使今日曾封板也不能沿用封单质量冒充当前状态——它的延续预期来自题材基座
    （阶段 + 成建制家数）与个股相对涨停位的位置（跑道是否已收窄到临板区）。
    """
    if pct is None or theme_stage is None:
        return {
            "level": "unknown",
            "basis": f"题材阶段{'缺失' if theme_stage is None else theme_stage}或涨幅缺失，联动置信无法判定",
        }
    if theme_stage in WEAK_STAGES:
        return {"level": "低", "basis": f"题材{theme_stage}——基座不支持延续，联动预期弱"}
    if theme_limit_ups < MIN_THEME_LIMIT_UPS:
        return {"level": "低", "basis": f"题材内仅 {theme_limit_ups} 家涨停，未成建制"}
    if pct >= pre_limit_floor(limit_pct):
        return {
            "level": "高",
            "basis": (
                f"题材{theme_stage} · 涨停 {theme_limit_ups} 家 · 已进临板区"
                f"（距封板 {round(seal_threshold(limit_pct) - pct, 2)}pct）"
            ),
        }
    if pct >= LINKAGE_MID_PCT:
        return {
            "level": "中",
            "basis": f"题材{theme_stage} · 涨停 {theme_limit_ups} 家 · 涨幅 {pct:.1f}% 有跟进迹象",
        }
    return {
        "level": "低",
        "basis": f"题材{theme_stage} · 涨停 {theme_limit_ups} 家 · 个股涨幅 {pct:.1f}% 联动迹象弱",
    }


def linkage_candidates(
    *,
    container: dict | None,
    member_symbols: list[str],
    ever_sealed_symbols: set[str] | None = None,
    snapshot_by: dict[str, dict] | None = None,
    theme_limit_ups: int,
    theme_stage: str | None,
    per_theme: int = PER_THEME,
    stats: dict | None = None,
    audit_rows: list[dict] | None = None,
    snapshot_state: str | None = None,
    snapshot_as_of: str | None = None,
) -> list[dict]:
    """题材容器内当前未封板的联动候选。

    `ever_sealed_symbols` 只表示“今日曾进入涨停池”的历史身份，**不能**直接当当前封板态。
    对这类票，只有带时点且 `snapshot_state=ready` 的当前快照才能证明已经开板；
    stale/degraded/无 as-of 一律 unknown。
    """
    ever_sealed_symbols = set(ever_sealed_symbols or set())
    snapshot_by = snapshot_by or {}
    out: list[dict] = []
    blocked: dict[str, int] = {}
    missing_quote = 0
    opened_after_seal = 0
    current_sealed_count = 0
    current_unknown = 0
    seen: set[str] = set()

    def audit(code: str, *, candidate: str, hard_gate: str, reason: str,
              row: dict | None = None, facts: dict | None = None) -> None:
        if audit_rows is None:
            return
        quote = row or {}
        audit_rows.append({
            "symbol": code, "name": str(quote.get("name") or ""),
            "candidate_decision": candidate, "hard_gate_decision": hard_gate,
            "reason": reason, "price": quote.get("price"),
            "change_pct": quote.get("change_pct"), "amount": quote.get("amount"),
            "board": board_label(code, str(quote.get("name") or "")) if code else None,
            "facts": facts or {},
        })

    for sym in member_symbols or []:
        code = str(sym)
        if code in seen:
            continue
        seen.add(code)
        if not code.isdigit() or len(code) != 6:
            continue
        row = snapshot_by.get(code)
        if not row:
            missing_quote += 1
            audit(code, candidate="unknown", hard_gate="unknown", reason="全市场快照缺失，无法判定",
                  facts={"quote_present": False, "ever_sealed": code in ever_sealed_symbols,
                         "snapshot_state": snapshot_state or "unknown", "version": snapshot_as_of})
            continue
        name = str(row.get("name") or "")
        if not is_tradable(code, name):
            label = board_label(code, name); blocked[label] = blocked.get(label, 0) + 1
            audit(code, candidate="rejected", hard_gate="rejected", reason=f"账户无{label}交易权限", row=row,
                  facts={"quote_present": True, "board_tradable": False, "ever_sealed": code in ever_sealed_symbols})
            continue
        pct = row.get("change_pct")
        if not isinstance(pct, (int, float)):
            audit(code, candidate="unknown", hard_gate="unknown", reason="涨幅缺失，无法判定当前封板状态", row=row,
                  facts={"quote_present": True, "board_tradable": True, "ever_sealed": code in ever_sealed_symbols,
                         "snapshot_state": snapshot_state or "unknown", "version": snapshot_as_of})
            continue
        pct = float(pct)
        limit = board_limit_pct(code, name)
        ever = code in ever_sealed_symbols
        if ever and (snapshot_state != "ready" or not snapshot_as_of):
            current_unknown += 1
            audit(code, candidate="unknown", hard_gate="unknown",
                  reason="今日曾封板，但当前快照无可信时点，不能据旧报价声称已开板", row=row,
                  facts={"quote_present": True, "board_tradable": True, "ever_sealed": True,
                         "current_sealed": None, "snapshot_state": snapshot_state or "unknown",
                         "version": snapshot_as_of})
            continue
        current_sealed = pct >= seal_threshold(limit)
        seal_state = {
            "ever_sealed": ever, "current_sealed": current_sealed,
            "snapshot_state": snapshot_state or "unknown",
            "version": snapshot_as_of,
        }
        if current_sealed:
            current_sealed_count += 1
            audit(code, candidate="rejected", hard_gate="rejected", reason="当前仍在封板线，不能参与", row=row,
                  facts={"quote_present": True, "board_tradable": True, "change_pct": pct,
                         "seal_line": seal_threshold(limit), **seal_state})
            continue
        if pct < LINKAGE_MIN_PCT:
            audit(code, candidate="rejected", hard_gate="passed", reason=f"涨幅 {pct:.2f}% 低于联动下沿", row=row,
                  facts={"quote_present": True, "board_tradable": True, "change_pct": pct,
                         "linkage_min_pct": LINKAGE_MIN_PCT, **seal_state})
            continue
        amount = row.get("amount")
        amount_f = float(amount) if isinstance(amount, (int, float)) and amount > 0 else 0.0
        if amount_f < LINKAGE_MIN_AMOUNT:
            audit(code, candidate="rejected", hard_gate="passed", reason=f"成交额 {amount_f:.0f} 低于流动性门槛", row=row,
                  facts={"quote_present": True, "board_tradable": True, "change_pct": pct,
                         "linkage_min_pct": LINKAGE_MIN_PCT, "amount": amount_f,
                         "amount_min": LINKAGE_MIN_AMOUNT, **seal_state})
            continue
        if ever:
            opened_after_seal += 1
            tradability = {
                "level": "可参与",
                "basis": "今日曾封板后当前开板，可进入参与评估；未核盘口深度/排队，不保证成交",
            }
        else:
            tradability = {
                "level": "可参与",
                "basis": "当前未封在涨停板，可进入参与评估；未核盘口深度/排队，不保证成交",
            }
        metrics = seal_metrics(code, name, pct)
        conf = linkage_confidence(theme_limit_ups=theme_limit_ups, theme_stage=theme_stage, pct=pct, limit_pct=limit)
        out.append({
            "symbol": code, "name": name, "board": board_label(code, name),
            "change_pct": round(pct, 2), "price": row.get("price"), "amount": amount_f or None,
            "turnover_rate": row.get("turnover_rate"), **metrics,
            "tradability": tradability, "seal_state": seal_state, "linkage": conf,
            "basis": (
                ("今日曾封板后已开板重评；" if ever else "")
                + f"题材内涨停 {theme_limit_ups} 家形成集中，本股当前未封板（{pct:.1f}%，距封板 {metrics['runway_pct']}pct）——"
                + ("已进临板区" if metrics.get("in_pre_limit") else "可参与观察")
            ),
            "container": (container or {}).get("name"), "container_code": (container or {}).get("code"),
        })
        audit(code, candidate="included", hard_gate="passed", reason=tradability["basis"], row=row,
              facts={"quote_present": True, "board_tradable": True, "change_pct": pct,
                     "linkage_min_pct": LINKAGE_MIN_PCT, "amount": amount_f,
                     "amount_min": LINKAGE_MIN_AMOUNT, "seal_line": seal_threshold(limit), **seal_state})
    if stats is not None:
        stats["excluded_board"] = sum(blocked.values())
        stats["excluded_board_labels"] = dict(sorted(blocked.items(), key=lambda kv: -kv[1]))
        stats["missing_quote"] = missing_quote
        stats["members"] = len(seen)
        stats["opened_after_seal"] = opened_after_seal
        stats["current_sealed"] = current_sealed_count
        stats["current_unknown"] = current_unknown
    out.sort(key=lambda c: (-c["change_pct"], -(c["amount"] or 0.0)))
    return out[:per_theme]


def attach_tradability(
    stocks: list[dict], snapshot_by: dict[str, dict] | None = None, *,
    snapshot_state: str | None = None, snapshot_as_of: str | None = None,
) -> None:
    """给曾封板参考行补当前可参与性；历史身份与当前状态分离。"""
    for s in stocks or []:
        if not isinstance(s, dict):
            continue
        sym = str(s.get("symbol") or ""); name = str(s.get("name") or "")
        s["board"] = board_label(sym, name); s["tradable"] = is_tradable(sym, name)
        first_seal = s.get("first_seal_time")
        row = (snapshot_by or {}).get(sym) or {}
        pct = row.get("change_pct")
        if snapshot_state != "ready" or not snapshot_as_of or not isinstance(pct, (int, float)):
            s["seal_state"] = {
                "ever_sealed": True, "current_sealed": None,
                "snapshot_state": snapshot_state or "unknown", "version": snapshot_as_of,
            }
            s["tradability"] = {
                "level": "unknown",
                "basis": "今日曾封板；当前无可信时点快照，无法判定是否仍封板",
            }
            continue
        limit = board_limit_pct(sym, name); sealed = float(pct) >= seal_threshold(limit)
        s["seal_state"] = {
            "ever_sealed": True, "current_sealed": sealed,
            "snapshot_state": snapshot_state, "version": snapshot_as_of,
        }
        if sealed:
            s["tradability"] = assess(sealed=True, first_seal_time=first_seal)
        else:
            s["tradability"] = {
                "level": "可参与",
                "basis": f"今日曾封板（首封 {_hhmm(first_seal) or '时间未知'}）后当前开板（{float(pct):.1f}%），可进入参与评估；未核盘口深度/排队，不保证成交",
            }
