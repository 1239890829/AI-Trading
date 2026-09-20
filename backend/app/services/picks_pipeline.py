"""每日精选（daily picks）管线的服务层 —— 从 `api/routes/picks.py` 抽离。

**为什么抽（S2-4，2026-09-11 架构审查根因 C「业务逻辑住在边缘」）**

旧形态：`generate_picks` 单函数 255 行完整管线住在 route 里，于是
`app/picks/picks_autogen.py`（常驻调度）必须**反向 import 路由**并伪造
`SimpleNamespace(app=app)` 当 request 用。两个后果：

1. 管线无法被脚本 / 回测 / 助手复用——想复用只能伪造一个 request；
2. **集成链路零测试覆盖**：单测只能把 `generate_picks` 整个打成桩。

现在：管线住在服务层，依赖以 `PipelineDeps` **显式声明**（取代隐式的
`request.app.state` 读取），route 与调度器都只是它的调用方。依赖方向单向：
`api/routes` → `services` → `picks/*`，反向依赖由 `tests/test_import_lint.py` 守卫。

**顺带解决 P2-4**：旧实现里「当日涨停池」在单次生成内被拉 3 次
（候选池 1 次 + 梯队上下文 1 次 + `compute_market_sentiment` 1 次，每次都是真实
上游 HTTP）。现在由 `_fetch_limit_up_pool()` **取一次**，其余消费方接收结果。
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select

from app.core.db import get_session_factory
from app.events.store import EventStore
from app.market import trade_calendar as tc
from app.market.chip import get_chip_service
from app.market.tech_score import score_stock, sma
from app.picks.chip_signal import chip_basis_text, evaluate_chip_signal
from app.picks.echelon import classify_echelon_role, score_echelon
from app.picks.engine import (
    MAX_PICKS,
    apply_replacement_threshold,
    build_buy_range,
    effective_limits,
    event_weight,
    score_capital,
    score_fundamental,
    score_news,
    score_sentiment,
    score_tech,
    synthesize,
)
from app.picks.gate import apply_gate_to_picks, evaluate_stand_aside
from app.picks.halt_risk import (
    BENCHMARK_INDEX,
    assess,
    benchmark_symbol,
    board_of,
    risk_labels,
    veto_reasons,
)
from app.picks.meta_confidence import classify_confidence
from app.picks.regime import detect_regime, earnings_event_ratio, weights_for
from app.picks.risk import build_invalidations, exit_discipline, risk_tier_of, stop_loss_reference
from app.picks.rps import get_rps_service
from app.picks.style_router import apply_style_offsets, route_style, style_note
from app.picks.tradability import (
    CANDIDATE_PER_THEME,
    MIN_THEME_LIMIT_UPS,
    MIN_THEME_SHARE,
    # 取别名：`assess` 已被 halt_risk（停牌/异动风险评估）占用，两者语义不同
    # （异动风险 vs 可参与性），同名会静默覆盖——pyflakes 当场拦下。
    assess as assess_tradability,
    board_label,
    index_views,
    is_open_sealed,
    is_tradable,
    linkage_candidates,
    resolve_containers,
    theme_focus,
)
from app.services.quote_enrich import fill_valuation
from app.services.quote_hub import QuoteHub
from app.core.bjtime import beijing_now

log = logging.getLogger(__name__)

CANDIDATE_CAP = 40      # 候选池上限（深度评分前）
DEEP_DIVE_CAP = 24      # 深度评分上限（每只要拉 K 线/财务/资金流）
CONCURRENCY = 6

#: 候选池装配顺序（来源优先级）。**不是**插入顺序的同义反复：本轮新增的
#: 「题材联动」来源必须在最前占位（见 `candidate_pool` ②b 的配额说明），
#: 而它的取数天然发生在其他来源之后 ⇒ 用一张显式顺序表解耦「取数顺序」与
#: 「装配顺序」。同档内保持插入序（`sorted` 稳定），故既有来源的相对次序零变化。
_SOURCE_ORDER = {
    "theme_linkage": 0,  # ① 可参与的题材联动股（2026-09-15 用户指令，最优先）
    "event": 1,          # ② 活跃事件直接命中的个股
    "event_theme": 1,    # ② 事件题材成分（同档，保持插入序）
    "limit_up": 2,       # ③ 当日涨停池
    "hot": 3,            # ④ 热股榜
    "carryover": 9,      # 昨日组合成员由调用方另行追加，不参与这里的排序
}


# ---------------------------------------------------------------- 依赖契约


@dataclass(frozen=True)
class PipelineDeps:
    """管线依赖的**显式契约**（取代 `request.app.state` 的隐式读取）。

    route 走 `from_state(request.app.state)`；调度器 / 脚本 / 测试直接构造——
    这才是「管线可复用」的实际含义：不必再伪造一个 request 才能跑。
    """

    event_store: EventStore
    snapshot_service: Any
    theme_catalog: Any = None

    @classmethod
    def from_state(cls, state: Any) -> PipelineDeps:
        # snapshot_service 取宽容读法：管线对它的缺失**已有显式降级路径**
        # （情绪段 try/except → market_phase=None 并留 warning，见 ③），
        # 而调度器不该因为一个可选装配项缺失就整轮崩掉。event_store 相反——
        # 候选池与消息面全靠它，缺了必然产出错误结果，故保持严格读取。
        return cls(
            event_store=state.event_store,
            snapshot_service=getattr(state, "snapshot_service", None),
            theme_catalog=getattr(state, "theme_catalog", None),
        )


def _db():
    # Session 实例（支持 with 自动 close）；sessionmaker 本身不支持 with（AGENTS.md 6.3）
    return get_session_factory()()


# ---------------------------------------------------------------- 数据准备


async def _batch_quotes(hub: QuoteHub, symbols: list[str]) -> dict[str, Any]:
    """腾讯批量快照（与个股行情同源），50 只/批。返回 symbol→Quote。
    2026-09-07 R3 收口：实现单点在 quote_enrich.fetch_quotes_batched。"""
    from app.services.quote_enrich import fetch_quotes_batched

    return await fetch_quotes_batched(hub, symbols)


async def _fetch_limit_up_pool(hub: QuoteHub) -> tuple[date | None, list]:
    """当日涨停池 —— **管线内唯一取数点**（P2-4）。

    旧实现三处各拉一次（候选池 / 梯队上下文 / 情绪引擎），每次都是真实上游
    HTTP；这里取一次，下游全部接收结果。返回 `(交易日, 池)`；任一步失败返回
    `(None, [])` 并留 warning——**空池与失败在调用侧都走诚实降级**（不臆造）。
    """
    try:
        days = await tc.trading_days(hub.provider)
        td = tc.last_trade_date(days)
        if not td:
            return None, []
        return td, list(await hub.provider.get_limit_up_pool(td))
    except Exception as exc:
        log.warning("picks: limit-up pool failed: %s", exc)
        return None, []


async def candidate_pool(
    hub: QuoteHub,
    store: EventStore,
    svc,
    *,
    limit_up_pool: list | None = None,
    active_events: list | None = None,
    linkages: list[dict] | None = None,
    audit: dict | None = None,
) -> list[dict]:
    """候选池 = 题材联动可参与股 ∪ 活跃事件标的池 ∪ 当日涨停池 ∪ 热股榜 top，去重剔 ST，cap 40。

    各路来源天然覆盖不同侧面：**题材联动是"可参与"的针对性来源**（2026-09-15
    用户指令：开盘即涨停的个股买不进，只作题材集中度的参考信息，转而挖掘该题材内
    当前未封板、通过联动/流动性门槛的评估候选），事件池是消息源，涨停池是大幅拉升的极端
    表现，热股榜是关注度信号。装配顺序见 `_SOURCE_ORDER`（联动股最前）。

    :param active_events: 活跃事件行（`store.list_events` 的结果）。管线内**预取一次
        复用**（与 `limit_up_pool` 同型，P2-4 的取数单点原则）；缺省 None 时本函数
        自己取（独立调用方/测试的兼容路径）。传入 `[]` 表示"已取过、结果为空"，
        **不等于**缺省——不会触发重复取数。
    :param linkages: 题材联动候选（`mine_theme_linkage` 的产物，管线预取）。缺省 None
        = 未预取 → 本来源缺席（**不静默**：审计里显式记 note，见下）。
    :param audit: 装配审计字典（就地写入）。补这一个出参而不是改返回类型：
        「哪几只被剔除、各来源进来几只」是口径可核对的前提，而返回类型是既有契约
        （route / 调度 / 多处测试都在用）。记录项：
        `sources`（各来源计数）、`excluded_open_sealed`（被剔除的开盘即涨停明细）、
        `theme_linkage`（题材集中度与补入结果）。
    """
    symbols: dict[str, dict] = {}
    # G-2（2026-09-12 评审批次 5）：本函数由 async 管线直接 await（调度 15:00+ 与
    # `POST /api/picks/generate` 手动触发两条路径都**在事件循环上**），函数体内每一处
    # 同步 SQLite/磁盘读都排在循环里（连同 QuoteHub 的秒级行情推送一起停摆）。
    # 故逐处搬进线程池；守卫与判据见 `tests/test_event_loop_no_block.py`（新增的
    # pipeline/watcher 覆盖）。`get_catalog` 自带 session（每次调用新建并关闭），
    # 整体搬线程不跨线程复用 session，是安全的——这一点对下面几处同样成立。
    _catalog = await asyncio.to_thread(svc.get_catalog, limit=1000) if svc is not None else []
    name_to_code = {t.name: t.code for t in _catalog}

    # ① 活跃事件：symbol 方向直接收；theme 方向反查官方成分（cap 30/题材）
    try:
        rows = (
            active_events
            if active_events is not None
            else await asyncio.to_thread(store.list_events, active_only=True, limit=30)
        )
        # P-3①（2026-09-12 评审批次 3）：先把本批事件要查的题材代码**按同一遍历顺序去重收集**，
        # 再用**既有**批量接口一次性取回，替代循环内的逐题材 `svc.get_members(code)`
        # （每个题材一个独立 session）。复用的是既有挂载点 `member_symbols_bulk`——它的
        # docstring 已写明"排序后与 `get_members` 同序，批量替换单查才是**行为等价**的"
        # （2026-09-11 P0-2 已为另一处消费方补齐该性质），所以这里不是新写一个批量查询。
        # 行为等价要点：`symbols` 是 setdefault 建的**有序列**，最终 `out` 按插入序取到
        # `CANDIDATE_CAP` 为止 ⇒ **遍历顺序必须逐字保留**。故下面仍是「按 row → 按 direction」
        # 的原顺序，只是把取数提前；`[:30]` 截断落在与 `get_members` 同序的列表上。
        wanted: list[str] = []
        if svc is not None:
            seen_codes: set[str] = set()
            for row in rows:
                for d in row.directions:
                    if d.target_type != "theme":
                        continue
                    code = name_to_code.get(d.target)
                    if code and code not in seen_codes:
                        seen_codes.add(code)
                        wanted.append(code)
        members_by_code = (
            await asyncio.to_thread(svc.member_symbols_bulk, wanted)
            if (svc is not None and wanted)
            else {}
        )
        for row in rows:
            # 直接读关系属性，**不要再调 `store.directions_of(row.id)`**：`list_events` 内部
            # 已 `selectinload(EventCard.directions)`（store.py:156），行虽 detached 但方向
            # 已在内存里；旧写法会**逐事件重开 session 再查一遍同一条 event_direction**
            # ⇒ 30 个事件 = 30 次冗余查询，且这些同步 SQLite 调用位于 async 函数内，
            # 会阻塞事件循环（2026-09-12 评审 P-2）。等价性：同一张表同一条件、返回同一
            # 模型（EventDirection），且本处只用 target_type/target 做 setdefault，
            # 与行序无关（uq_event_direction 保证同一事件内 target 不重复）。
            for d in row.directions:
                if d.target_type == "symbol" and d.target.isdigit() and len(d.target) == 6:
                    symbols.setdefault(d.target, {"from": "event", "prio": 1})
                elif d.target_type == "theme" and svc is not None:
                    code = name_to_code.get(d.target)
                    if not code:
                        continue
                    for sym in (members_by_code.get(code) or [])[:30]:
                        symbols.setdefault(sym, {"from": "event_theme", "prio": 1})
    except Exception as exc:
        log.warning("picks candidate: events failed: %s", exc)

    # ①b 题材联动挖掘结果（2026-09-15 用户指令）—— 由调用方预取（同 P2-4 取数单点）。
    # 插到最前由 `_SOURCE_ORDER` 决定，不靠插入顺序（取数天然发生在其他来源之后）。
    n_linkage = 0
    if linkages is None:
        if audit is not None:
            audit["theme_linkage"] = {
                "note": "未预取（linkages=None）——题材联动来源本轮缺席，不臆造",
                "themes": [],
                "added": 0,
            }
    else:
        for c in linkages:
            sym = str(c.get("symbol") or "")
            if sym and sym not in symbols:
                symbols[sym] = {"from": "theme_linkage", "prio": 2, "linkage": c}
                n_linkage += 1

    # ② 当日涨停池（突发大幅拉升的极端表现）—— 由调用方预取（P2-4）
    # ⚠️ **开盘即封成员不通过“原始涨停池来源”直接晋级**：首封 ≤09:30 是强历史特征，
    # 但不是 current 状态。若它随后真实开板，必须经 ①b 的带时点 theme_linkage
    # 重新通过“当前未封板 + 联动/流动性/权限”门槛后才能进入候选；不能靠涨停池身份自动授权。
    # 判据委托 `picks/tradability.is_open_sealed` 单点实现。
    excluded: list[dict] = []
    for r in limit_up_pool or []:
        if is_open_sealed(getattr(r, "first_seal_time", None)) is True:
            excluded.append(
                {
                    "symbol": r.symbol,
                    "name": getattr(r, "name", None),
                    "first_seal_time": getattr(r, "first_seal_time", None),
                }
            )
            continue
        symbols.setdefault(r.symbol, {"from": "limit_up", "prio": 1})

    # ③ 热股榜 top 20（关注度信号，B1 同源）
    try:
        for s in (await hub.provider.get_hot_stock_list("day"))[:20]:
            symbols.setdefault(s["symbol"], {"from": "hot", "prio": 0})
    except Exception as exc:
        log.warning("picks candidate: hot list failed: %s", exc)

    # 装配：按来源优先级排序后截断。`sorted` 稳定 ⇒ 同档内保持插入序，
    # 既有来源（事件 → 事件题材 → 涨停池 → 热榜）的相对次序与改动前**逐字一致**，
    # 唯一变化是联动股整体前移（这正是本轮规则的目的）。
    ordered = sorted(symbols.items(), key=lambda kv: _SOURCE_ORDER.get(kv[1]["from"], 3))
    out = []
    for sym, meta in ordered:
        if not sym.isdigit() or len(sym) != 6:
            continue
        out.append({"symbol": sym, **meta})
        if len(out) >= CANDIDATE_CAP:
            break

    if audit is not None:
        sources: dict[str, int] = {}
        for item in out:
            sources[item["from"]] = sources.get(item["from"], 0) + 1
        audit["sources"] = sources
        audit["excluded_open_sealed"] = {
            "count": len(excluded),
            "items": excluded[:20],  # 明细留痕有界（不把 40 行塞进 meta）
        }
        tl = audit.setdefault("theme_linkage", {"themes": [], "added": 0})
        tl["added"] = n_linkage
    return out


def limit_up_context(pool: list) -> dict:
    """涨停板生态上下文（联合研判的题材级证据）。纯函数——池由调用方传入。

    返回：
    - records: {symbol: {连板数/炸板数/首封时间/流通市值/涨停原因}}
    - market_max_boards: 全市场最高连板（判定"空间板"必需）
    - themes: {题材标签: {symbols, max_boards, changes, levels}}（题材天梯健康度输入）
    """
    out: dict[str, Any] = {"records": {}, "market_max_boards": 0, "themes": {}}

    from app.services.theme_service import parse_theme_tags

    boards_all: list[int] = []
    for r in pool or []:
        boards = r.consecutive_boards or 1
        boards_all.append(boards)
        out["records"][r.symbol] = {
            "consecutive_boards": boards,
            "break_count": r.break_count,
            "first_seal_time": r.first_seal_time,
            "float_market_cap": r.float_market_cap,
            "amount": r.amount,
            "reason": r.reason,
            "change_pct": r.change_pct,
        }
        for tag in parse_theme_tags(r.reason):
            t = out["themes"].setdefault(
                tag, {"symbols": [], "max_boards": 0, "changes": [], "levels": {}}
            )
            t["symbols"].append(r.symbol)
            t["max_boards"] = max(t["max_boards"], boards)
            if r.change_pct is not None:
                t["changes"].append(r.change_pct)
            t["levels"][boards] = t["levels"].get(boards, 0) + 1
    out["market_max_boards"] = max(boards_all) if boards_all else 0
    return out


async def mine_theme_linkage(
    svc,
    lu_ctx: dict,
    snapshot_rows: list[dict] | None,
    *,
    per_theme: int = CANDIDATE_PER_THEME,
    snapshot_state: str | None = None,
    snapshot_as_of: str | None = None,
) -> dict:
    """题材联动挖掘：涨停集中的题材 → 该题材内**当前未封板**、通过门槛的评估候选。

    **为什么需要**（2026-09-15 用户指令）：开盘即涨停的个股全天买不进，把它选进
    组合只是拿"事后已知的极强标的"抬高名义胜率。它们的正确用法是**参考信息**——
    它们揭示当日资金集中的方向；既然该方向上多数个股已被封死，就在同一个官方题材
    容器里找**当前未封板**且通过联动/流动性门槛的票：资金外溢的第一落点；进入评估不等于保证成交。

    **为什么用"成分重叠"而不是题材名匹配**：涨停原因是同花顺的动态标签（「功能糖」），
    官方概念是目录名（「代糖概念」），名字对不上。按成分重叠反向定位容器是既有的
    `official_match` 口径（同一组常量），挖出来的容器与卡片上显示的官方概念必然是
    同一个——不会出现"卡片说代糖、挖掘用功能糖"这种口径分裂。

    诚实降级（三态纪律）：快照不可用 / 无题材达到集中阈值 / 无官方容器 —— 全部
    返回 `note` 说明原因并给空结果，**不臆造**候选（判不了不冒充可参与）。

    :returns: ``{"themes": [...], "items": [...], "note": str|None}``。
        `items` 元素为 `linkage_candidates` 的产物（含 basis / tradability），
        由 `candidate_pool` 组装进候选池。
    """
    out: dict = {"themes": [], "items": [], "note": None}
    stats = (lu_ctx or {}).get("themes") or {}
    records = (lu_ctx or {}).get("records") or {}
    if not stats or not records:
        out["note"] = "当日无涨停池题材数据——联动挖掘跳过"
        return out

    focus = theme_focus(stats, limit_up_total=len(records))
    if not focus:
        out["note"] = (
            f"无题材达到集中阈值（题材内涨停 ≥{MIN_THEME_LIMIT_UPS} 家"
            f"且占当日涨停 ≥{MIN_THEME_SHARE:.0%}）——不硬挖"
        )
        return out
    if svc is None:
        out["note"] = "题材目录服务不可用——无官方成分可挖"
        return out
    if not snapshot_rows:
        out["note"] = "全市场快照不可用——可参与性依赖实时盘口，跳过而不臆造"
        return out

    # G-2：同步 SQLite 全表读（theme_member 7 万行）→ 线程池，不占事件循环
    from app.picks.board_surge import build_theme_index

    symbol_index, _names = await asyncio.to_thread(build_theme_index)
    sizes, members_by_code = index_views(symbol_index)
    snapshot_by = {r["symbol"]: r for r in snapshot_rows if r.get("symbol")}
    ever_sealed = set(records)

    items: list[dict] = []
    themes_out: list[dict] = []
    for f in focus:
        containers = resolve_containers(set(f["symbols"]), symbol_index, sizes)
        if not containers:
            themes_out.append(
                {**f, "container": None, "container_code": None, "candidates": 0,
                 "note": "无官方容器可挂靠（成分重叠 < 2）"}
            )
            continue
        container = containers[0]
        cands = linkage_candidates(
            container=container,
            member_symbols=members_by_code.get(container["code"]) or [],
            ever_sealed_symbols=ever_sealed,
            snapshot_by=snapshot_by,
            snapshot_state=snapshot_state,
            snapshot_as_of=snapshot_as_of,
            theme_limit_ups=f["count"],
            theme_stage=_theme_stage_of(lu_ctx, f["theme"]).get("stage"),
            per_theme=per_theme,
        )
        items.extend(cands)
        themes_out.append(
            {
                **f,
                "container": container["name"],
                "container_code": container["code"],
                "container_hits": container["hits"],
                "candidates": len(cands),
                "note": None if cands else "容器内无可参与成分（未涨停但涨幅/成交额/快照均达标者为 0）",
            }
        )
    out["themes"] = themes_out
    out["items"] = items
    if not items:
        out["note"] = "题材集中已识别，但容器内无合格可参与成分（见 themes[].note）"
    log.info(
        "picks 题材联动：集中题材 %s，补入可参与候选 %d 只",
        [t["theme"] for t in themes_out], len(items),
    )
    return out


def _theme_benchmark(
    *, themes: list[dict] | None, lu_ctx: dict, market_pct: float | None
) -> tuple[float | None, str | None]:
    """个股所属题材的当日基准涨幅（优先最强题材，匹配不到回退大盘）。

    **纯函数**（2026-09-12 评审批次 3 / P-3②）：题材归属由调用方
    （`deep_score_candidates`）批量预取后传入，本函数不再自己查库。原实现签名带
    `svc` 并在函数体内 `svc.get_official_for_symbol(symbol)`，于是**每个候选**
    2 次同步 SQLite 查询（成员 + 人工纠错）都落在 async 循环体里（24 候选 ≈ 48 次），
    与 P-2/P-3① 同族。抽成纯函数后既可直测，也让"取数"与"判定"分开。

    回退大盘是诚实降级：宁可用确定性高的弱基准，也不臆造题材归属。
    """
    best: float | None = None
    best_name: str | None = None
    for t in themes or []:
        name = t.get("theme_name")
        stat = (lu_ctx.get("themes") or {}).get(name)
        if not stat or not stat.get("changes"):
            continue
        avg = sum(stat["changes"]) / len(stat["changes"])
        if best is None or avg > best:
            best, best_name = avg, name
    if best is not None:
        return best, best_name
    return market_pct, None


def _atr_pct(bars: list[dict]) -> float | None:
    """ATR14 / 最新收盘（百分数）。样本不足或字段缺失返回 None（不臆造波动率）。"""
    if len(bars) < 15:
        return None
    trs: list[float] = []
    prev_close: float | None = None
    for b in bars[-15:]:
        h, l, c = b.get("high"), b.get("low"), b.get("close")
        if h is None or l is None or c is None:
            return None
        if prev_close is not None:
            trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        prev_close = c
    if len(trs) < 14 or not prev_close:
        return None
    return round(sum(trs[-14:]) / 14 / prev_close * 100, 2)


def _ma_value(bars: list[dict], n: int) -> float | None:
    """n 日均线最新值（失效条件的客观参照）。"""
    closes = [b.get("close") for b in bars]
    if len(closes) < n or any(c is None for c in closes):
        return None
    series = sma([float(c) for c in closes], n)
    last = series[-1] if series else None
    return round(last, 2) if last is not None else None


def _primary_theme_of(lu_ctx: dict, symbol: str, fallback: str | None = None) -> str | None:
    """个股的主题材（取其在涨停池中所属的最强题材；非涨停股走 fallback）。"""
    best: str | None = None
    best_boards = -1
    for tag, st in (lu_ctx.get("themes") or {}).items():
        if symbol in st["symbols"] and st["max_boards"] > best_boards:
            best, best_boards = tag, st["max_boards"]
    return best or fallback


def _theme_stage_of(lu_ctx: dict, theme_name: str | None) -> dict:
    """题材天梯健康度。题材不可得时返回空上下文（不臆造阶段）。"""
    st = (lu_ctx.get("themes") or {}).get(theme_name) if theme_name else None
    if not st:
        return {"stage": None, "completeness": None, "adjust": 1.0, "stage_basis": []}
    from app.picks.echelon import theme_ladder_health

    return theme_ladder_health(
        limit_up_count=len(st["symbols"]),
        max_boards=st["max_boards"],
        reopen_rate=0.0,  # 封板率需炸板池逐题材统计，第一版不细分（basis 已标注）
        levels=st["levels"],
    )


def _prev_combo_symbols() -> list[str]:
    """上一份组合的成员（换股门槛与 carryover 都依赖它）。"""
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        row = db.execute(
            select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(1)
        ).scalar_one_or_none()
        if not row:
            return []
        try:
            return [i["symbol"] for i in json.loads(row.items)]
        except Exception:  # noqa: BLE001
            # 返回 [] 会被下游当成「上一份组合为空」⇒ 换股门槛与 carryover 一起失效
            # （等价于放开换手约束）。这里保留原返回语义但**必须留下痕迹**，
            # 否则数据损坏与「确实没选过股」两者在观测上无法区分。
            log.warning("上一份组合解析失败（换股门槛将按空组合处理）date=%s", row.date, exc_info=True)
            return []


def parse_pick_meta(raw: str | None) -> dict:
    """组合 meta（权重/炒作阶段/空仓闸门）解析。损坏时返回空字典而不是 500。"""
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _build_event_hits_index(
    rows: list,
) -> dict[str, tuple[float, float, str | None, str | None, int]]:
    """一次遍历活跃事件 → 按 symbol 索引消息命中（评审 B1）。

    返回 {symbol: (利好强度和, 利空强度和, 主事件标题, 主方向文案, 关联数)}。
    强度和已乘 `event_weight(source_tier, certainty)`（选股 2.0 §3）：
    tier1 官方落地政策 ≈ 25 条 tier5 自媒体传闻的权重，消息面不再被
    同质化的条数淹没。list_events 已 selectinload 预加载方向行（detached
    后仍可安全访问），索引构建零额外查询——原实现每候选股重复全量扫事件表
    （24 只深评 × 每次约 31 次查询）；关联数含 direction=0（"来源关联、
    方向待判"也是证据，丢掉它会让消息面对有新闻但无方向词的标的显示"无命中"）。

    ⚠️ **入参是事件行本身，不是 store**（2026-09-12 评审批次 5 / G-2 改）：
    同一次管线里 `candidate_pool` / 本函数 / `ev_texts` 此前**各自跑了同一条
    `store.list_events(active_only=True, limit=30)`，同一查询三遍**——既白读两遍，
    又因为是同步 SQLite 而三度占用事件循环。改为调用方预取一次后，本函数是
    **纯内存函数**（零 IO，可安全留在循环上，无需 to_thread）。
    """
    index: dict[str, dict] = {}
    try:
        for row in rows:
            w = event_weight(row.source_tier, row.certainty)
            for d in row.directions:
                if d.target_type != "symbol" or not d.target:
                    continue  # 题材方向的个股传导第一版不计入单股消息分（防过度外推）
                agg = index.setdefault(
                    d.target,
                    {"bull": 0, "bear": 0, "linked": 0, "top_title": None, "top_dir": None, "pending_title": None},
                )
                agg["linked"] += 1
                if d.direction == 1:
                    agg["bull"] += d.strength * w
                elif d.direction == -1:
                    agg["bear"] += d.strength * w
                if agg["top_title"] is None and d.direction != 0:
                    agg["top_title"] = row.title
                    agg["top_dir"] = "利好" if d.direction == 1 else "利空"
                if agg["pending_title"] is None and d.direction == 0:
                    agg["pending_title"] = row.title
    except Exception as exc:
        log.warning("picks event index failed: %s", exc)
        return {}
    out: dict[str, tuple[int, int, str | None, str | None, int]] = {}
    for sym, agg in index.items():
        # 无方向词时给出关联标题（证据可见）
        out[sym] = (agg["bull"], agg["bear"], agg["top_title"] or agg["pending_title"], agg["top_dir"], agg["linked"])
    return out


async def _prefetch_index_bars(hub: QuoteHub) -> dict[str, list[dict]]:
    """预取各板块基准指数日 K（偏离值计算的分母）。

    ⚠️ 必须在并发评分**之前**一次性取完复用：24 只候选股各拉一次指数 = 请求量
    翻 5 倍，而腾讯源有熔断（实测连续请求直接 502「熔断冷却中 19s」），
    会把整个选股流程拖垮。指数当日不变，取一次足够。

    取不到时对应板块留空列表——`assess` 会把偏离值类规则降级为「不可评」
    并显式标注，绝不拿个股涨幅冒充偏离值。
    """
    # key 用**指数代码**而非板块名：查询侧是 `index_bars.get(benchmark_symbol(board, sym))`，
    # 而 benchmark_symbol 返回的是代码。用板块名作 key 会全部 miss → 偏离值恒为 None
    # （静默降级成"指数数据缺失"，看不出是 key 写错）。
    out: dict[str, list[dict]] = {}
    for board, sym in BENCHMARK_INDEX.items():
        try:
            bars = await hub.provider.get_kline(sym, "1d", None, None)
            out[sym] = [b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in bars][-250:]
        except Exception as exc:
            log.warning("picks: index kline failed (%s): %s", sym, exc)
            out[sym] = []
    return out


async def deep_score_candidates(
    deep: list[dict],
    *,
    hub: QuoteHub,
    svc,
    lu_ctx: dict,
    market_pct: float | None,
    market_phase: str | None,
    quotes: dict,
    weights: dict,
    event_hits_index: dict[str, tuple[float, float, str | None, str | None, int]],
    concurrency: int,
    promo_percentile: float | None = None,
    index_bars: dict[str, list[dict]] | None = None,
    style: dict | None = None,
) -> list[dict]:
    """④ 逐只深度评分（并发；每只独立异常兜底）。

    从 generate_picks 拆出（全项目审查 T6：主函数 320 行 → 流水线编排 +
    本函数）。输入候选已带 name/price/change_pct/amount。

    :param index_bars: 各板块基准指数日 K（`_prefetch_index_bars` 预取），
        供停牌核查/异动风险评估计算偏离值。
    """
    sem = asyncio.Semaphore(concurrency)
    index_bars = index_bars or {}
    # RPS 全市场截面（一次查询、服务内当日缓存；marketdb 未建/陈旧 → {} →
    # score_stock 的 rps 维自动取中性 0.5，不臆造分位）。同步 DuckDB 查询
    # 放线程池，不占事件循环。
    rps_svc = get_rps_service()
    rps_map = await asyncio.to_thread(rps_svc.snapshot)
    # 三态诚实：RPS 为空时把**真实原因**带到卡片依据里（缺仓 / 陈旧 / 窗口不足
    # 语义完全不同；统一写"仓未建"会让"仓在但停更 6 个交易日"被误读为没数据源）
    rps_note = None
    if not rps_map:
        fr = await asyncio.to_thread(rps_svc.freshness)
        if fr.get("stale"):
            rps_note = (f"RPS 数据陈旧（库内最新 {fr['latest']}，滞后 {fr['lag']} 个交易日），"
                        "中性处理——修复：scripts/sync_marketdb.py")
        elif not fr.get("available"):
            rps_note = (f"RPS 未覆盖（{fr.get('reason') or 'marketdb 仓未建/未回补'}），"
                        "中性处理")

    # P-3②（2026-09-12 评审批次 3）：逐候选「股票 → 官方题材」反查此前是
    # `get_official_for_symbol(sym)` **每只 2 次同步 SQLite 查询**（成员 + 人工纠错），
    # 24 只候选 ≈ 48 次落在 async 循环体里——既浪费又阻塞事件循环（与 P-2 同族）。
    # 改为**进循环前批量取一次**，循环内退化为纯内存查表。
    # 等价性由 `test_theme_catalog.py::test_official_for_symbols_bulk_matches_single_read`
    # 直接 A/B 对照钉住（含 override 生效 / 过期两种情形）。
    # 失败语义保持与逐只版一致：查不到 → 该只回退大盘，不臆造题材归属。
    themes_by_symbol: dict[str, list[dict]] = {}
    if svc is not None:
        try:
            # G-2：批量反查仍是同步 SQLite（2 次查询）→ 线程池（判据见 candidate_pool 顶部）
            themes_by_symbol = await asyncio.to_thread(svc.official_for_symbols_bulk, [c["symbol"] for c in deep])
        except Exception as exc:
            log.warning("picks official themes bulk failed: %s", exc)
            themes_by_symbol = {}

    async def _score_one(c: dict) -> dict | None:
        sym = c["symbol"]
        async with sem:
            sub: dict[str, float] = {}
            bases: dict[str, str] = {}
            # 技术（防飞刀口径 score_stock，v3 含 RPS 横截面）
            # 两段各自兜底（2026-09-14）：**取数失败**与**评分自身异常**是两回事。
            # 原实现共用一个 `except`，把评分代码的 bug 也写成「K线数据缺失，中性」——
            # 依据文案把排查引向数据源（错方向），且 `dicts` 被一并清空，
            # 连带 ATR / 均线（出场纪律的输入）一起退化成 None。
            dicts: list[dict] = []
            try:
                bars = await hub.provider.get_kline(sym, "1d", None, None)
                dicts = [b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in bars][-250:]
            except Exception as exc:  # noqa: BLE001
                s_tech, b_tech = 50.0, f"K线数据缺失（{type(exc).__name__}），中性"
            else:
                try:
                    s_tech, b_tech = score_tech(
                        score_stock(dicts, rps=rps_map.get(sym), rps_note=rps_note)
                    )
                except Exception as exc:  # noqa: BLE001
                    s_tech, b_tech = 50.0, f"技术评分异常（{type(exc).__name__}），中性处理"
                    log.warning("picks tech score failed for %s", sym, exc_info=True)
            # 出场纪律的输入：ATR（止损宽度）与均线（失效条件参照）
            atr_pct = _atr_pct(dicts)
            ma5 = _ma_value(dicts, 5)
            ma10 = _ma_value(dicts, 10)
            sub["tech"], bases["tech"] = s_tech, b_tech
            # 消息（B1：查预构建索引，O(1)——不再逐候选扫事件表）
            bull, bear, top_title, top_dir, linked = event_hits_index.get(sym, (0, 0, None, None, 0))
            sub["news"], bases["news"] = score_news(bull, bear, top_title, top_dir)
            if bull == bear == 0 and linked:
                # 有关联但无方向词：诚实说"命中了但待判"，而不是"无命中"
                bases["news"] = (
                    f"命中 {linked} 条关联事件（标题无方向词，方向待判），消息面中性；"
                    f"最近：「{(top_title or '')[:40]}」"
                )
            # 基本面：成长性/盈利质量来自财务报告（营收增速、净利同比、ROE、毛利率——
            # normalizer 早已提取这四个字段，2026-09-01 起评分全部消费），估值来自行情快照
            rev = None
            profit = None
            roe_v = None
            gm = None
            try:
                fin = await hub.provider.get_financials(sym, 4)
                if fin:
                    latest = fin[0] if isinstance(fin, list) else fin
                    d_ = latest if isinstance(latest, dict) else getattr(latest, "__dict__", {})
                    rev = d_.get("revenue_yoy")
                    profit = d_.get("profit_yoy")
                    roe_v = d_.get("roe")
                    gm = d_.get("gross_margin")
            except Exception as exc:
                log.warning("picks financials %s failed: %s", sym, exc)
            # ⚠️ PE 需要现价，财务报告里本来就没有（此前从 financials 取 pe_ttm → 恒 None）。
            # 估值应取自行情快照；链首 ths 不带该字段，用 fill_valuation 从腾讯补。
            pe = None
            q_snap = quotes.get(sym)
            if q_snap is not None:
                if q_snap.pe_ttm is None:
                    q_snap = await fill_valuation(hub.provider, q_snap)
                pe = q_snap.pe_ttm if q_snap is not None else None
            sub["fundamental"], bases["fundamental"] = score_fundamental(
                pe, rev, profit_yoy=profit, roe=roe_v, gross_margin=gm
            )
            # 资金
            net_inflow = None
            try:
                flow = await hub.provider.get_capital_flow(sym, 5)
                if flow:
                    last = flow[-1] if isinstance(flow, list) else flow
                    d_ = last if isinstance(last, dict) else getattr(last, "__dict__", {})
                    # ⚠️ 新浪资金流字段名是 net_main（主力净额，元），不是 net_amount。
                    # 此前写错字段名 → 恒为 None → 资金面永远显示"数据缺失"，
                    # 被静默降级掩盖成了"数据源问题"（2026-08-31 修复）。
                    net_inflow = d_.get("net_main")
            except Exception as exc:
                log.warning("picks capital flow %s failed: %s", sym, exc)
            sub["capital"], bases["capital"] = score_capital(net_inflow, None, on_lhb=False)
            # 情绪（全局相位；题材涨家占比第一版缺省；promo 历史分位为接力环境修正，
            # 选股 2.0 §3——分位来自 P0-3b 校准库，缺失时不修正、basis 如实呈现）
            sub["sentiment"], bases["sentiment"] = score_sentiment(
                market_phase, None, promo_percentile=promo_percentile
            )

            # 梯队（第六维）：个股在题材天梯中的地位 × 题材阶段，联合读取。
            # 没有这一维，退潮期的最后一棒会和发酵期的真龙头拿同样分。
            benchmark, theme_name = _theme_benchmark(
                themes=themes_by_symbol.get(sym) or [],
                lu_ctx=lu_ctx, market_pct=market_pct,
            )
            excess = (
                round(c["change_pct"] - benchmark, 2)
                if c["change_pct"] is not None and benchmark is not None
                else None
            )
            theme_name = _primary_theme_of(lu_ctx, sym, theme_name)
            theme_ctx = _theme_stage_of(lu_ctx, theme_name)
            lu = lu_ctx["records"].get(sym)
            if lu:
                role, role_basis = classify_echelon_role(
                    is_limit_up=True,
                    consecutive_boards=lu["consecutive_boards"],
                    theme_max_boards=(
                        (lu_ctx["themes"].get(theme_name) or {}).get("max_boards")
                        or lu["consecutive_boards"]
                    ),
                    market_max_boards=lu_ctx["market_max_boards"],
                    float_market_cap=lu["float_market_cap"],
                    first_seal_time=lu["first_seal_time"],
                    break_count=lu["break_count"],
                )
            else:
                role, role_basis = classify_echelon_role(
                    is_limit_up=False,
                    float_market_cap=None,  # 快照无流通市值字段，不臆造（诚实降级为同步/领涨）
                    excess_pct=excess,
                )
            s_ech, b_ech = score_echelon(
                role=role,
                stage=theme_ctx["stage"],
                completeness=theme_ctx["completeness"],
            )
            sub["echelon"] = s_ech
            bases["echelon"] = (
                f"{role_basis}；{b_ech}"
                + (f"；题材「{theme_name}」" if theme_name else "；未匹配到题材（按个股独立评估）")
            )

            # 停牌核查 / 异动风险（docs/summary/stock-strategy.md 第一批）：
            # 只依据已取到的个股日 K + 预取的指数日 K，零新增数据源。
            # 红线进 veto（×0.4 重罚并显式记录），黄线在合成后按扣分扣减。
            # ⚠️ R3（当前停牌）暂不接：picks 流程没有可靠的 trading_status 来源，
            # 硬猜会把"当日无成交"误判成停牌。接口已留，第二批接入（见模块 docstring）。
            halt = assess(
                symbol=sym,
                name=c.get("name"),
                bars=dicts,
                index_bars=index_bars.get(benchmark_symbol(board_of(sym, c.get("name")), sym), []),
            )
            # 筹码形态（P1 派发/吸筹规则化）：marketdb CYQ 近似 × 量价组合。
            # 只留痕不进权重——先在理由中积累样本，滚动验证胜率后再议进权重
            # （strategy-evolution-plan §方向2）。DuckDB 同步查询丢线程池；
            # ChipService 内置 TTLCache（30min），候选重复不重复查库。
            chip = await asyncio.to_thread(get_chip_service().distribution, sym)
            chip_sig = evaluate_chip_signal(chip, dicts)
            bases["chip"] = chip_basis_text(chip_sig, chip)
            score, vetoes = synthesize(sub, weights=weights, vetoes=veto_reasons(halt))
            if halt["penalty"]:
                score = round(max(0.0, score - halt["penalty"]), 1)
            # meta 置信层（P1 规则版）：综合分+相位+筹码+红线 → 三档置信
            # （替代 gate 二值跳变的统一置信语言；gate 保留为兜底）。
            # 相位维度扩展（审查 §4.1）：当日风格路由结果挂进置信理由留痕。
            confidence = classify_confidence(
                score=score,
                sub_scores=sub,
                phase=market_phase,
                chip_signal=chip_sig.get("signal"),
                halt_penalty=halt.get("penalty") or 0.0,
                veto_count=len(vetoes),
                style_note=style_note(style),
            )
            return {
                "symbol": sym, "name": c["name"], "price": c["price"], "change_pct": c["change_pct"],
                "halt_risk": halt,
                "halt_risk_labels": risk_labels(halt),
                # 可参与性（2026-09-15 用户指令）：候选池已剔除"开盘即涨停"，故这里
                # 正常路径恒为「可参与」——它是**保证**的透出，而不是事后补的标签。
                # 尾盘板等非开盘即封的涨停股走 sealed=True 分支，依据文案写明首封时间。
                "tradability": assess_tradability(
                    sealed=bool(lu), first_seal_time=(lu or {}).get("first_seal_time")
                ),
                # 入选来源与来源依据：题材联动股要能让用户看见"它是从哪个题材挖出来的"
                "source": c.get("from"),
                "source_basis": (c.get("linkage") or {}).get("basis"),
                # 估值此前**只用于基本面打分，没有透出到卡片**——选股页因此永远看不到 PE，
                # 而个股详情页有（走 /api/quotes 的 fill_valuation）。同一标的两个口径不一致。
                "pe_ttm": pe,
                "pb": getattr(q_snap, "pb", None) if q_snap is not None else None,
                "score": score, "sub_scores": sub, "bases": bases, "vetoes": vetoes,
                "related_events": [top_title] if top_title else [],
                "echelon_role": role,
                "echelon_basis": bases["echelon"],
                "theme": theme_name,
                "theme_stage": theme_ctx["stage"],
                # 连板高度（gate 可跟判据的第一要素；非涨停股 None，不臆造）
                "boards": lu["consecutive_boards"] if lu else None,
                "atr_pct": atr_pct,
                "ma5": ma5,
                "ma10": ma10,
                # 筹码信号（派发警示/启动观察，None=未触发）与三档置信
                "chip_signal": chip_sig,
                "confidence": confidence,
            }

    results = await asyncio.gather(*[_score_one(c) for c in deep])
    return [r for r in results if r is not None]


def assemble_card(k: dict) -> dict:
    """⑥ 单只入选标的的卡片组装（风险档位 + 买入范围 + 出场纪律 + 失效条件）。"""
    role = k.get("echelon_role") or ""
    tier = risk_tier_of(role)
    ma5, ma10 = k.get("ma5"), k.get("ma10")
    # 买入范围的技术位收敛：均线在现价下方作支撑、上方作压力
    support = min([v for v in (ma5, ma10) if v and v < k["price"]], default=None)
    resistance = max([v for v in (ma5, ma10) if v and v > k["price"]], default=None)
    return {
        "symbol": k["symbol"],
        "name": k["name"],
        "price": k["price"],
        "change_pct": k["change_pct"],
        # 估值透出（可能为 None：数据源未提供，前端按"暂无+原因"展示，不臆造）
        "pe_ttm": k.get("pe_ttm"),
        "pb": k.get("pb"),
        "score": k["score"],
        "sub_scores": k["sub_scores"],
        "bases": k["bases"],
        "vetoes": k["vetoes"],
        "buy_range": build_buy_range(k["price"], support, resistance),
        "echelon_role": role,
        "echelon_basis": k.get("echelon_basis", ""),
        "theme": k.get("theme"),
        "theme_stage": k.get("theme_stage"),
        "boards": k.get("boards"),
        "risk_tier": tier,
        "stop_loss": stop_loss_reference(
            price=k["price"], tier=tier, atr_pct=k.get("atr_pct")
        ),
        "exit_discipline": exit_discipline(tier),
        "invalidations": build_invalidations(
            role=role,
            tier=tier,
            theme_stage=k.get("theme_stage"),
            ma_value=(ma5 if tier in ("龙头博弈", "情绪低位") else (ma10 or ma5)),
            event_titles=k["related_events"],
        ),
        "themes": [k["theme"]] if k.get("theme") else [],
        "related_events": k["related_events"],
        # 停牌核查 / 异动风险（第一批：R1/R2 红线 + Y1/Y2/Y3 黄线 + P1/P2 仓位约束）
        "halt_risk": k.get("halt_risk"),
        "halt_risk_labels": k.get("halt_risk_labels") or [],
        # 筹码信号（派发警示/启动观察）+ meta 三档置信（规则版）
        "chip_signal": k.get("chip_signal"),
        "confidence": k.get("confidence"),
        # 可参与性（2026-09-15 用户指令）+ 入选来源（题材联动股可追溯）
        "tradability": k.get("tradability"),
        "source": k.get("source"),
        "source_basis": k.get("source_basis"),
    }


# ---------------------------------------------------------------- 管线主体


def _persist_picks(
    today: str, items: list, meta: dict, replaced: list, rejected: list
) -> None:
    """组合落库（同日重复生成 = 覆盖当日行）。

    抽成独立同步函数是为了**能整体搬线程池**（G-2，2026-09-12 评审批次 5）：
    原先这段挂在 async 管线的末尾，同步 SQLite 读改写 + 两次大对象 `json.dumps`
    都排在事件循环上。抽出来后调用方一次 `to_thread` 包住，语义零变化。
    """
    with _db() as db:
        from app.models.daily_pick import DailyPickSet

        row = db.execute(select(DailyPickSet).where(DailyPickSet.date == today)).scalar_one_or_none()
        payload = {
            "items": json.dumps(items, ensure_ascii=False),
            "meta": json.dumps(meta, ensure_ascii=False),
            "replaced": json.dumps(replaced, ensure_ascii=False),
            "rejected": json.dumps(rejected, ensure_ascii=False),
        }
        if row is None:
            db.add(DailyPickSet(date=today, **payload))
        else:
            for key, val in payload.items():
                setattr(row, key, val)
        db.commit()
    # 需求 7 收尾（merged_into_picks 此前「有字段无接线」）：组合定稿后，把当日
    # 盘中跟踪台账中进入组合的行打合并标记，猎场台账面板可显示「已入精选」。
    # 失败只记日志——合并标记是展示增强，不应让组合落库整体失败。
    try:
        from app.picks.watch_ledger import mark_merged_into_picks

        mark_merged_into_picks(today, [it.get("symbol") for it in items if isinstance(it, dict)])
    except Exception as exc:  # noqa: BLE001
        log.warning("watch ledger merge marking failed: %s", exc)


async def generate_picks_pipeline(
    deps: PipelineDeps,
    hub: QuoteHub,
    *,
    today: str | None = None,
) -> dict:
    """生成今日组合（T 日收盘后跑，产出 T+1 组合；重复生成覆盖当日行）。

    :param today: 归属日（`YYYY-MM-DD`）。缺省取北京今日；常驻调度传入它自己
        判定窗口用的那个日期，避免"调度按北京日判窗口、管线按本机日写入"
        这类跨日口径分裂（KB-TRADE-02）。
    """
    store = deps.event_store
    svc = deps.theme_catalog
    today = today or beijing_now().date().isoformat()

    # ① 当日涨停池（**单次取数**，下游三处复用；P2-4）
    td, limit_up_pool = await _fetch_limit_up_pool(hub)

    # ①a 涨停板生态上下文（梯队地位判定的题材级证据）—— 纯函数，池已取。
    # ⚠️ 位置在候选池**之前**（2026-09-15 起）：题材联动挖掘要用它的题材集中度
    # 定位容器。此前它在候选池之后（①c），只是因为它当时的唯一消费方是深评。
    lu_ctx = limit_up_context(limit_up_pool)

    # ①b 题材联动挖掘（2026-09-15 用户指令）：把"开盘即涨停"的参考价值兑现成
    # 可参与评估候选——涨停集中的题材内，当前未封板、有联动机会的官方成分股。
    # 全市场快照只用于**筛选**（谁可参与），最终价格仍由 ② 的批量行情同源提供。
    # 快照不可用 → 该来源诚实缺席（note 写进 meta），不臆造。
    snapshot_rows = list(getattr(deps.snapshot_service, "snapshot", None) or [])
    snapshot_state = "unknown"
    snapshot_as_of = None
    try:
        fresh_fn = getattr(deps.snapshot_service, "freshness", None)
        fresh = fresh_fn() if callable(fresh_fn) else None
        snapshot_state = getattr(fresh, "state", None) or "unknown"
        as_of = getattr(fresh, "as_of", None) or getattr(deps.snapshot_service, "last_success", None)
        snapshot_as_of = as_of.isoformat() if hasattr(as_of, "isoformat") else (str(as_of) if as_of else None)
    except Exception:  # noqa: BLE001 — 快照事实拿不到就保持 unknown，不伪造 current
        snapshot_state, snapshot_as_of = "unknown", None
    linkage = await mine_theme_linkage(
        svc, lu_ctx, snapshot_rows,
        snapshot_state=snapshot_state, snapshot_as_of=snapshot_as_of,
    )
    audit: dict = {"theme_linkage": {"themes": linkage.get("themes") or [],
                                     "note": linkage.get("note")}}

    # ①c 候选池
    # G-2 / 取数单点（P2-4 同型）：活跃事件**取一次**（同步 SQLite + selectinload →
    # 线程池），下游三处复用（候选池题材反查 / regime 的 ev_texts / 消息命中索引）。
    # 此前同一条查询在本管线里跑了**三遍**。失败 → 空表，下游各自诚实降级（不臆造）。
    try:
        active_events = await asyncio.to_thread(store.list_events, active_only=True, limit=30)
    except Exception as exc:
        log.warning("picks active events failed: %s", exc)
        active_events = []

    candidates = await candidate_pool(
        hub, store, svc,
        limit_up_pool=limit_up_pool,
        active_events=active_events,
        linkages=linkage.get("items") or [],
        audit=audit,
    )

    # ①d 昨日组合成员兜底纳入（carryover）：
    # 组合稳定性要求 incumbent 有"被重新评估的权利"——否则一只票今天没涨停、
    # 没上热榜、事件又过期，就会被静默踢出，组合天天大换血（跨日回放实测：
    # 纯涨停股候选池下日均换手 60%）。纳入后它仍要重新评分，分数不够照样被换，
    # 只是不再因为"没进榜"而消失。
    # G-2：同步 SQLite（DailyPickSet 最近一行）→ 线程池（判据同 candidate_pool 顶部注释）
    prev_symbols = await asyncio.to_thread(_prev_combo_symbols)
    have = {c["symbol"] for c in candidates}
    for s in prev_symbols:
        if s not in have:
            candidates.append({"symbol": s, "from": "carryover", "prio": 1})
    carryover_set = set(prev_symbols) - have

    # ①e 市场基准（上证当日涨跌幅）：题材基准匹配不到时的诚实回退
    market_pct = None
    try:
        ov = await hub.provider.get_market_overview()
        for i in getattr(ov, "indices", None) or []:
            if getattr(i, "symbol", "") in ("000001", "sh000001"):
                market_pct = getattr(i, "change_pct", None)
    except Exception as exc:
        log.warning("picks: market overview failed: %s", exc)

    # ② 批量快照 + 预筛（剔 ST/退/无行情/**无交易权限板块**）
    quotes = await _batch_quotes(hub, [c["symbol"] for c in candidates])
    deep: list[dict] = []
    board_excluded: list[dict] = []
    for c in candidates:
        q = quotes.get(c["symbol"])
        if q is None or q.price is None or q.price <= 0:
            continue
        name = q.name or ""
        if "ST" in name.upper() or "退" in name:
            continue
        # 板块权限（用户 2026-09-15「只有主板的权限现在」）：创业板/科创板/北交所/B 股
        # 进不了组合 —— 与"买不进"同义。判据单点 = `picks/tradability.is_tradable`。
        # 放在**这里**而不是各来源入口：候选池有 4 路来源，逐一过滤 = 四份判据；
        # 这里是与 ST/退 同一处的**既有可交易性收口**，一处即全覆盖。
        if not is_tradable(c["symbol"], name):
            board_excluded.append(
                {"symbol": c["symbol"], "name": name,
                 "board": board_label(c["symbol"], name), "from": c.get("from")}
            )
            continue
        c.update({"name": name, "price": q.price, "change_pct": q.change_pct, "amount": q.amount})
        c["_prio"] = c.get("prio", 0) * 1000 + (q.change_pct or 0)
        deep.append(c)
    # 昨日成员优先进入深度评估：它们已经有仓位逻辑在身，不该因涨幅不高被截断
    deep.sort(key=lambda c: (-(1 if c["symbol"] in carryover_set else 0), -c["_prio"]))
    deep = deep[:DEEP_DIVE_CAP]

    # ③ 全局情绪（一次）—— 涨停池复用 ① 的结果（P2-4）
    market_phase = None
    sent: dict = {}
    try:
        from app.services.market_context import compute_market_sentiment

        sent = await compute_market_sentiment(
            hub,
            deps.snapshot_service,
            limit_up_pool=limit_up_pool,
            limit_up_date=td,
        ) or {}
        market_phase = sent.get("phase")
    except Exception as exc:
        log.warning("picks sentiment failed: %s", exc)

    if market_phase is None:
        # 2026-09-09：相位缺失会让 style_routing 不路由——当日评分丢掉风格偏移，
        # 且写入 meta 后定格全天。最常见原因是快照 breadth 未就绪（含刚重启补跑），
        # 等一拍再取一次，比让全天评分裸奔便宜得多；仍失败则诚实留 None。
        await asyncio.sleep(5)
        try:
            sent = await compute_market_sentiment(
                hub,
                deps.snapshot_service,
                limit_up_pool=limit_up_pool,
                limit_up_date=td,
            ) or {}
            market_phase = sent.get("phase")
        except Exception as exc:  # noqa: BLE001
            log.warning("picks sentiment retry failed: %s", exc)

    # ③b 炒作阶段（Regime）：财报日历 + 业绩事件密度 → 六维权重表。
    # 业绩空窗期必须把基本面权重让给情绪与题材梯队，否则系统性错过妖股。
    # 复用管线开头的预取结果（此前这里是第三次同查询）；失败在预取处已降级为空表
    ev_texts = [e.title for e in active_events]
    regime = detect_regime(
        today=date.fromisoformat(today),
        earnings_ratio=earnings_event_ratio(ev_texts) if ev_texts else None,
        event_count=len(ev_texts),
    )
    weights = weights_for(regime["regime"])
    # ③b' 相位→风格路由（审查报告 §4.1）：在 regime 基础权重上按市场情绪相位
    # 做当日微调（叠加不替代）；偏移表配置可覆盖，路由结果随 meta 留痕。
    style = route_style(market_phase)
    weights = apply_style_offsets(weights, style["offsets"])

    # ③c 空仓闸门：情绪转弱时主动提示规避（红线 3：只提示，不下指令）
    break_rate = None
    try:
        if td:
            breaks = await hub.provider.get_limit_break_pool(td)
            n_zt, n_br = len(lu_ctx["records"]), len(breaks or [])
            break_rate = round(n_br / max(n_zt + n_br, 1), 3)
    except Exception as exc:
        log.warning("picks gate: break pool failed: %s", exc)
    limit_down = None
    try:
        limit_down = (deps.snapshot_service.breadth or {}).get("limit_down")
    except Exception:
        limit_down = None

    # 当日分位（2026-09-10 修正 + P1-31）：
    # ①**必须用当日实测值算分位**——`sent.calibration.percentile` 是「历史库最后一行」
    #   的分位，而库里最后一行是上一个交易日（backfill 刻意不回补今天），拿它当
    #   "今天的分位"等于每天用昨天的位置描述今天，库一停更就变成用上个月的位置；
    # ②闸门（晋级率/炸板率）与情绪面评分共用同一处计算，避免两个口径各自漂移。
    # 样本不足/值缺失 → None，闸门自动回落绝对经验值并在理由里写明（不静默）。
    promo_1to2 = (sent.get("promotion") or {}).get("promo_1to2")
    promo_pctl = None
    break_pctl = None
    try:
        from app.sentiment import metric_history

        # G-2：`percentile_of_value` 要读整份情绪历史文件（同步磁盘读），两次调用
        # 合并成一次线程跳（两个分位一起算），不占事件循环。
        promo_pctl = (
            await asyncio.to_thread(metric_history.percentile_of_value, "promo_1to2", promo_1to2) or {}
        ).get("percentile")
        break_pctl = (
            await asyncio.to_thread(metric_history.percentile_of_value, "break_rate", break_rate) or {}
        ).get("percentile")
    except Exception as exc:
        log.warning("picks percentile_of_value failed: %s", exc)

    gate = evaluate_stand_aside(
        phase=market_phase,
        promotion_1to2=promo_1to2,
        promotion_1to2_pctl=promo_pctl,
        break_rate=break_rate,
        break_rate_pctl=break_pctl,
        limit_down=limit_down,
        prev_zt_median_pct=(sent.get("prev_perf") or {}).get("median_pct"),
        phase_unreliable=bool(sent.get("phase_unreliable")),
    )
    if gate["stand_aside"]:
        log.warning("picks gate triggered (%s): %s", gate["level"], "；".join(gate["reasons"]))

    # ③d 消息命中索引（B1）：一次遍历活跃事件按 symbol 建索引——
    # 原实现每候选股在并发任务里重复全量扫事件表（24×~31 次同步查询）
    # ③e 基准指数日 K（停牌核查/异动的偏离值分母）：一次性预取，供全部候选复用。
    # 24 只候选各拉一次会触发腾讯熔断，必须在这里取完。
    index_bars = await _prefetch_index_bars(hub)
    # 纯内存（行已预取，见管线开头）——不再需要 to_thread，也不再重复查库
    event_hits_index = _build_event_hits_index(active_events)

    # 晋级率历史分位（选股 2.0 §3）：**当日值**在历史样本中的位置（见上方 ③c 说明）。
    # 库样本不足时为空 → 情绪面修正项自动缺席，basis 如实呈现。
    promo_pct = promo_pctl

    # ④ 逐只深度评分（并发；T6 拆分至 deep_score_candidates）
    ranked = await deep_score_candidates(
        deep,
        hub=hub,
        svc=svc,
        lu_ctx=lu_ctx,
        market_pct=market_pct,
        market_phase=market_phase,
        quotes=quotes,
        weights=weights,
        event_hits_index=event_hits_index,
        concurrency=CONCURRENCY,
        promo_percentile=promo_pct,
        index_bars=index_bars,
        style=style,
    )
    ranked.sort(key=lambda r: -r["score"])

    # ⑤ 入选门槛 + 换股门槛（昨日组合；prev_symbols 已在 ①b 载入，此处不重复查库）
    #    入选门槛（MIN_PICK_SCORE）保证「够格几只就是几只」，MAX_PICKS 只是容量上限。
    #    三档阈值默认取运行时生效值（控制台参数白名单 P1-15 的覆盖层优先）。
    kept, replaced = apply_replacement_threshold(prev_symbols, ranked)
    limits = effective_limits()

    # ⑤a 落选者落库（消融验证 P3 数据地基，2026-09-01 用户批准启动）：
    # 深评过但未进组合的候选（分数不够/门槛拦截/上限截断），精简摘要 + tech 分——
    # 30 个交易日积累后，tech-only 对照回放回答「六维组合是否优于单维筛选」
    kept_symbols = {k["symbol"] for k in kept}
    rejected = [
        {
            "symbol": r["symbol"],
            "name": r["name"],
            "score": r["score"],
            "tech": (r.get("sub_scores") or {}).get("tech"),
            "rank": i + 1,
        }
        for i, r in enumerate(ranked)
        if r["symbol"] not in kept_symbols
    ][:20]

    # ⑤b 出列留痕（2026-09-10）：昨日成员没进今日名单的，分两种归因——
    # ① 跌破入选门槛（质量下滑，有分数可证）② 未入选（掉出候选池/被更强候选换掉/名额截断）。
    # 不写这条，「名单变短」在复盘里就成了无解释的数字变化。
    score_of = {r["symbol"]: r.get("score") for r in ranked}
    swapped_out = {r["out"] for r in replaced}
    removed = []
    for sym in prev_symbols:
        if sym in kept_symbols or sym in swapped_out:
            continue
        sc = score_of.get(sym)
        if sc is not None and sc < limits["min_pick_score"]:
            reason = f"跌破入选门槛（综合分 {sc} < {limits['min_pick_score']}）"
        elif sc is None:
            reason = "掉出候选池（今日未进入深度评分）"
        else:
            reason = "未入选（被更强候选换掉或容量截断）"
        removed.append({"symbol": sym, "score": sc, "reason": reason})

    # ⑥ 卡片组装（含风险档位与出场纪律参考）+ 空仓闸门处理 + 持久化
    items = [assemble_card(k) for k in kept]
    items = apply_gate_to_picks(items, gate)
    meta = {
        "weights": weights,
        "regime": regime,
        "style_routing": style,
        "gate": gate,
        "replace_threshold": limits["replace_threshold"],
        "max_swaps_per_day": limits["max_swaps_per_day"],
        "market_phase": market_phase,
        "candidate_count": len(candidates),
        "deep_dives": len(deep),
        # max_picks 是容量上限、min_pick_score 是入选门槛：两者共同决定
        # 「今日名单 = 达到门槛者，最多 5 只」，不是「每天凑满 5 只」（2026-09-10）
        "max_picks": MAX_PICKS,
        "min_pick_score": limits["min_pick_score"],
        "kept_count": len(items),
        "removed": removed,
        "market_pct": market_pct,
        "limit_up_count": len(lu_ctx["records"]),
        "market_max_boards": lu_ctx["market_max_boards"],
        # 可参与性口径留痕（2026-09-15 用户指令）：被剔除的开盘即涨停明细 +
        # 题材联动挖掘结果 + 候选池各来源计数。没有这三项，"为什么今天名单里
        # 没有某只票"在复盘时就成了无解释的数字变化（与本文件 ⑤b 同源纪律）。
        "tradability_policy": {
            "open_seal_cutoff": "09:30",
            "excluded_open_sealed": audit.get("excluded_open_sealed") or {"count": 0, "items": []},
            "theme_linkage": audit.get("theme_linkage") or {},
            "candidate_sources": audit.get("sources") or {},
            # 板块权限（2026-09-15 用户「只有主板的权限」）：可交易板块白名单 +
            # 被剔除明细。它解释了"为什么候选池明明有 40 只、深评只有十几只"。
            "tradable_boards": "沪市主板 / 深市主板（含主板 ST）",
            "excluded_board": {"count": len(board_excluded), "items": board_excluded[:20]},
        },
        "generated_at": beijing_now().isoformat(),
    }
    # G-2：落库是同步 SQLite 写（+ 大对象 json.dumps）→ 线程池（判据见 candidate_pool 顶部）
    await asyncio.to_thread(_persist_picks, today, items, meta, replaced, rejected)
    return {"data": {"date": today, "items": items, "replaced": replaced, "meta": meta}, "meta": {}}
