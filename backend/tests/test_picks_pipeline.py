"""每日精选管线（S2-4）—— **集成链路**测试。

**为什么要有这个文件**：S2-4 立项的直接理由是「集成链路零测试覆盖」——
`generate_picks` 住在 route 里时，单测只能把整个函数打成桩，于是
「候选池 → 六维评分 → 门槛 → 落库 → 卡片」这条链**没有任何一处被真正跑过**，
权重改了 / 落库缺字段 / 卡片少一段，五道门禁全绿。

本文件用**受控桩**把这条链整条跑通（不 mock 管线本体），断言：
1. 产出卡片结构完整（含买入范围、出场纪律、失效条件、置信档）；
2. 真的落库到 `daily_pick_set`；
3. **P2-4**：单次生成内当日涨停池只被上游拉一次；
4. **S2-4**：依赖以 `PipelineDeps` 显式传入，不需要伪造 request；
5. **P-3（09-12 评审批次 3）**：两处题材取数只许走批量——题材成分 `member_symbols_bulk`
   恰一次、官方题材反查 `official_for_symbols_bulk` 恰一次，**逐只单查的调用数必须为 0**。
   这两条看的是**查询次数**而非行为，故桩带调用计数（同 `test_board_fund_api.py` 范式）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.daily_pick import DailyPickSet
from app.models.watchlist import Base
from app.schemas.market import Quote
from app.services import picks_pipeline as pl

TRADE_DAY = date(2026, 9, 10)
PREV_DAY = date(2026, 9, 9)


# ---------------------------------------------------------------- 桩


class _PoolRec:
    def __init__(self, symbol: str, boards: int, reason: str, chg: float = 10.0):
        self.symbol = symbol
        self.consecutive_boards = boards
        self.break_count = 0
        self.first_seal_time = "09:35"
        self.float_market_cap = 8.0e9
        self.amount = 3.0e8
        self.reason = reason
        self.change_pct = chg


class _Direction:
    def __init__(self, target: str, direction: int = 1, target_type: str = "symbol"):
        self.target_type = target_type
        self.target = target
        self.direction = direction
        self.strength = 1.0


class _Event:
    def __init__(self, eid: int, title: str, target: str = "600519", target_type: str = "symbol"):
        self.id = eid
        self.title = title
        self.source_tier = 2
        self.certainty = "high"
        self.directions = [_Direction(target, 1, target_type)]


class _Store:
    """桩：**不提供 `directions_of`**。

    这是刻意的——管线改从 `row.directions` 读（`list_events` 已 selectinload，
    见 P-2「循环内重查」修复）。若有人改回 `store.directions_of(row.id)`，
    这里会立刻 `AttributeError` 精确变红，而不是静默退回 N+1。

    另带 `list_events` **调用计数**（G-2，2026-09-12 评审批次 5）：管线的活跃事件是
    **取数单点**（候选池题材反查 / regime 的 ev_texts / 消息命中索引三处复用同一份），
    计数 > 1 就说明又退回"同一条查询跑多遍 + 每遍同步占事件循环"。
    """

    def __init__(self) -> None:
        self.list_events_calls = 0

    def list_events(self, *, active_only: bool = False, limit: int = 30):
        self.list_events_calls += 1
        return [_Event(1, "白酒消费刺激政策落地")]


class _Member:
    def __init__(self, symbol: str):
        self.symbol = symbol


class _CatalogTheme:
    def __init__(self, code: str, name: str):
        self.code = code
        self.name = name


#: 桩成分表：**刻意不排序**——「批量与单查同序」是靠两端各自排序拿到的性质
#: （`member_symbols_bulk` 的 `order_by(theme_code, symbol)` 对 `get_members` 的
#: `order_by(symbol)`），桩若直接返回原序就测不出这一点。
_MEMBERS: dict[str, list[str]] = {"BK0001": ["600519", "000858"]}


def _official(symbol: str) -> list[dict]:
    """`600519` 属「白酒概念」，其余为空——与生产返回的键集完全一致。"""
    if symbol != "600519":
        return []
    return [{"theme_code": "BK0001", "theme_name": "白酒概念", "source": "ths_official"}]


class _Catalog:
    """题材目录桩，**带调用计数**。

    P-3 的验收看的是「查询次数」而非行为：批量化后逐只单查的计数必须为 0。
    因此本桩**同时提供单查与批量两条路**，由用例断言单查计数为 0。

    为什么必须补齐批量方法（2026-09-12 实测教训）：桩若缺 `member_symbols_bulk` /
    `official_for_symbols_bulk`，`AttributeError` 会被 `candidate_pool` 与批预取处的
    `except Exception: log.warning(...)` 吞掉，**改回逐只 N+1 的旧实现测试照样全绿**
    ——「桩缺方法」不构成守卫（同 `_Store` 那条注释的结论）。
    """

    def __init__(self):
        self.member_bulk_calls = 0
        self.member_single_calls = 0
        self.official_bulk_calls = 0
        self.official_single_calls = 0

    def get_catalog(self, limit: int = 1000):
        return [_CatalogTheme("BK0001", "白酒概念")]

    def get_members(self, code: str):
        self.member_single_calls += 1
        return [_Member(s) for s in sorted(_MEMBERS.get(code, []))]

    def member_symbols_bulk(self, codes: list[str]):
        """镜像真实实现：**只返回有成分的题材**，且按 symbol 定序（`[:30]` 截断依赖次序）。"""
        self.member_bulk_calls += 1
        out: dict[str, list[str]] = {}
        for c in codes:
            syms = sorted(_MEMBERS.get(c, []))
            if syms:
                out[c] = syms
        return out

    def get_official_for_symbol(self, symbol: str, *, apply_manual: bool = True):
        self.official_single_calls += 1
        return _official(symbol)

    def official_for_symbols_bulk(self, symbols: list[str], *, apply_manual: bool = True):
        """镜像真实实现的**两条关键契约**：未命中的 symbol 返回空列表（不是缺键）。"""
        self.official_bulk_calls += 1
        return {s: _official(s) for s in dict.fromkeys(symbols)}


class _ThemeStore:
    """只返回**一条题材方向**事件——用于把候选池的事件路钉在「题材反查」分支上。"""

    def list_events(self, *, active_only: bool = False, limit: int = 30):
        return [_Event(7, "白酒消费刺激政策落地", target="白酒概念", target_type="theme")]


class _Snapshot:
    breadth = {"up": 3000, "down": 1800, "limit_down": 3, "limit_up": 60}
    snapshot = []
    last_success = None

    def freshness(self):
        from app.core.freshness import Freshness

        return Freshness.from_age(as_of=None, fresh_within=180, source="snapshot",
                                  missing_reason="测试桩：无成功刷新时间")


class _Provider:
    """腾讯风格桩：`name == "tencent"` 让 quote_enrich 直接用它。"""

    name = "tencent"

    def __init__(self):
        self.pool_dates: list[date] = []

    async def get_limit_up_pool(self, d):
        self.pool_dates.append(d)
        return [
            _PoolRec("600519", 3, "白酒概念+消费刺激"),
            _PoolRec("000858", 1, "白酒概念"),
        ]

    async def get_limit_break_pool(self, d):
        return [_PoolRec("300001", 1, "白酒概念", -2.0)]

    async def get_hot_stock_list(self, period):
        return [{"symbol": "600519"}]

    async def get_quotes(self, symbols):
        return [
            Quote(
                symbol=s, source="tencent",
                name="贵州茅台" if s == "600519" else "五粮液",
                price=1500.0 if s == "600519" else 120.0, change_pct=9.9 if s == "600519" else 3.2,
                amount=3.0e9, pe_ttm=30.0, pb=8.0, total_mktcap_yi=19000.0, float_mktcap_yi=19000.0,
            )
            for s in symbols
        ]

    async def get_market_overview(self):
        idx = type("I", (), {"symbol": "sh000001", "change_pct": 0.8})()
        return type("O", (), {"indices": [idx]})()

    async def get_kline(self, symbol, period, start, end):
        # 60 根单调上行的日 K：足够算 MA5/MA10 与 ATR14
        return [
            {"open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i, "volume": 1e6}
            for i in range(60)
        ]

    async def get_financials(self, symbol, n):
        return [{"revenue_yoy": 15.0, "profit_yoy": 18.0, "roe": 22.0, "gross_margin": 0.91}]

    async def get_capital_flow(self, symbol, days):
        return [{"net_main": 2.5e8}]


class _EmptyProvider:
    """热股榜恒空——用于把候选池的**另外两路来源**（涨停池 / 热股榜）置空。"""

    name = "tencent"

    async def get_hot_stock_list(self, period):
        return []


class _BareHub:
    """只保留热股榜接口的极简 hub：`candidate_pool` 不碰 quote/kline。"""

    def __init__(self):
        self.provider = _EmptyProvider()


class _Hub:
    def __init__(self):
        self.provider = _Provider()


class _Rps:
    def snapshot(self):
        return {}

    def freshness(self):
        return {"available": False, "stale": False, "reason": "测试桩"}


class _Chip:
    def distribution(self, symbol):
        return {"available": False, "reason": "测试桩"}


# ---------------------------------------------------------------- 夹具


@pytest.fixture()
def catalog() -> _Catalog:
    """单独暴露桩实例，供用例断言「查询次数」（P-3 验收看次数，不看行为）。"""
    return _Catalog()


@pytest.fixture()
def store() -> _Store:
    """单独暴露桩实例，供用例断言「同一条查询只跑一次」（G-2 取数单点）。"""
    return _Store()


@pytest.fixture()
def deps(tmp_path, monkeypatch, catalog, store):
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline.db'}")
    Base.metadata.create_all(engine)  # DailyPickSet 经顶部 import 注册
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(pl, "get_session_factory", lambda: sf)

    # 交易日历是外部边界：钉成单一交易日，避免测试触网
    monkeypatch.setattr(pl.tc, "trading_days", lambda provider: _days())

    monkeypatch.setattr(pl, "get_rps_service", lambda: _Rps())
    monkeypatch.setattr(pl, "get_chip_service", lambda: _Chip())
    monkeypatch.setattr(pl, "_prefetch_index_bars", _no_index_bars)

    return pl.PipelineDeps(event_store=store, snapshot_service=_Snapshot(), theme_catalog=catalog)


async def _days():
    # 两个交易日：情绪引擎需要 anchor + prev，否则抛 CalendarUnavailable
    return [PREV_DAY, TRADE_DAY]


async def _no_index_bars(hub):
    return {}


def _run(deps, hub, **kw):
    return asyncio.run(pl.generate_picks_pipeline(deps, hub, today=TRADE_DAY.isoformat(), **kw))


# ---------------------------------------------------------------- 用例


def _benign_gate(**_kw):
    """不触发的闸门（本测试关注**链路完整性**，不关注闸门判定——闸门有专项测试）。"""
    return {
        "stand_aside": False, "level": "none", "reasons": [], "phase": None,
        "strip_buy_range": False, "observation_only": False,
    }


def test_pipeline_produces_full_card_and_persists(deps, monkeypatch):
    """整条链跑通：产出卡片结构完整 + 真的落库。

    闸门置为不触发，以便断言**含买入范围**的完整卡片形态；闸门触发时的降级
    形态由下一个用例覆盖。
    """
    monkeypatch.setattr(pl, "evaluate_stand_aside", _benign_gate)
    hub = _Hub()
    out = _run(deps, hub)

    data = out["data"]
    assert data["date"] == TRADE_DAY.isoformat()
    assert data["items"], "候选池非空时必须产出卡片"

    card = data["items"][0]
    for key in (
        "symbol", "name", "price", "score", "sub_scores", "bases", "buy_range",
        "echelon_role", "risk_tier", "stop_loss", "exit_discipline", "invalidations",
        "halt_risk", "confidence",
    ):
        assert key in card, f"卡片缺字段 {key}"
    # 六维齐全（缺一维说明评分链断了）
    assert set(card["sub_scores"]) == {"tech", "news", "fundamental", "capital", "sentiment", "echelon"}
    assert card["buy_range"] is not None

    meta = data["meta"]
    assert "market_phase" in meta  # 允许降级为 None，但字段必须在
    assert meta["candidate_count"] >= 1
    assert meta["limit_up_count"] == 2

    with pl._db() as db:
        from sqlalchemy import select

        row = db.execute(select(DailyPickSet)).scalar_one()
    assert row.date == TRADE_DAY.isoformat()
    assert json.loads(row.items)[0]["symbol"] == card["symbol"]


def test_tech_score_exception_is_not_reported_as_missing_data(deps, monkeypatch):
    """技术评分**自身异常**时，依据文案必须指向「评分异常」，不得写成「K线数据缺失」。

    背景（2026-09-14 审查批次 B4）：原实现把「取数失败」与「评分自身抛错的 bug」
    合用一个 `except`，两者都写成「K线数据缺失，中性」——依据文案把排查引向数据源
    （错方向），且 `dicts` 被一并清空，连带 ATR / 均线（出场纪律的输入）一起退化。

    **回退即红**：恢复共用 except 后 `bases["tech"]` 变成「K线数据缺失，中性」。
    """
    monkeypatch.setattr(pl, "evaluate_stand_aside", _benign_gate)

    def _boom(*_a, **_k):
        raise TypeError("fake scoring bug")

    monkeypatch.setattr(pl, "score_stock", _boom)
    out = _run(deps, _Hub())

    items = out["data"]["items"]
    assert items, "评分异常不该让候选整体消失（降级为中性 50 继续出卡片）"
    for it in items:
        tech = it["bases"]["tech"]
        assert "技术评分异常" in tech, tech
        assert "K线数据缺失" not in tech, f"评分 bug 被写成取数失败（排查方向被带偏）：{tech}"
    # 数据**其实取到了**（桩的 get_kline 正常）：不应因评分抛错而连带丢 ATR/均线
    assert any(it["stop_loss"] is not None for it in items), "评分异常连带清空了 ATR/均线输入"


def test_gate_strips_buy_range_and_keeps_record(deps):
    """闸门触发档（真实判据）：撤买入区间但**记录仍保留**（复盘要能看见）。

    与 KB-DEC-014 的「撤区间 = 相位级或 ≥2 条理由」同源，这里只钉住
    「卡片不出现 buy_range」与「条目没被删掉」两条不变量。
    """
    hub = _Hub()
    out = _run(deps, hub)
    gate = out["data"]["meta"]["gate"]
    if not gate.get("stand_aside") or not gate.get("strip_buy_range"):
        pytest.skip("桩数据未触发撤区间档——判据由 test_picks_risk_gate 专项覆盖")
    assert out["data"]["items"], "闸门只撤区间，不删记录"
    assert all("buy_range" not in it for it in out["data"]["items"])


def test_limit_up_pool_fetched_once_per_run(deps, monkeypatch):
    """**P2-4 回归位**：单次生成内**当日**涨停池只被上游拉一次。

    旧实现拉 3 次（候选池 / 梯队上下文 / 情绪引擎），每次都是真实 HTTP。
    注意按**日期**计数：昨日池（情绪引擎的 prev 轴）本就该单独取一次，不算重复。
    变异验证：把 `_fetch_limit_up_pool` 的结果丢掉、改回各处自取 → 本断言失败。
    """
    hub = _Hub()
    _run(deps, hub)
    assert hub.provider.pool_dates.count(TRADE_DAY) == 1
    assert hub.provider.pool_dates.count(PREV_DAY) == 1  # 昨日池仅情绪引擎取一次


def test_pipeline_reuses_prefetched_pool_in_sentiment(deps):
    """情绪引擎确实复用了预取的池（而不是又打了一次上游）。"""
    hub = _Hub()
    out = _run(deps, hub)
    # limit_up_count 与候选池来源都建立在同一个池上：2 只
    assert out["data"]["meta"]["limit_up_count"] == 2


def test_candidate_pool_reads_directions_from_loaded_relation(deps, monkeypatch):
    """**P-2 回归位**：候选池从 `row.directions`（已 `selectinload`）读方向，不循环重查库。

    为什么必须单独钉一条（2026-09-12 实测教训）：

    只把 `_Store.directions_of` 删掉**不构成守卫**。把实现改回
    `store.directions_of(row.id)` 后，该调用抛 `AttributeError`，却被
    `candidate_pool` 顶部的 `except Exception: log.warning(...)` 吞掉，
    候选池仍由涨停池/热股榜兜底 → 集成用例**照样全绿**（实测 `5 passed`）。
    ⇒ 异常吞没会让「桩缺方法」这种天然守卫失效，必须另设一条不依赖该异常路径的断言。

    做法：把涨停池与热股榜**都置空**，让事件方向成为唯一来源——
    改回旧写法 ⇒ 事件路静默断 ⇒ 池空 ⇒ 精确变红。同时断言事件路未告警，
    这样「异常被吞」本身也会被指名，而不是只表现为一个空的列表。
    """
    warnings: list[str] = []
    monkeypatch.setattr(pl.log, "warning", lambda msg, *a: warnings.append(msg % a if a else msg))

    pool = asyncio.run(pl.candidate_pool(_BareHub(), _Store(), _Catalog(), limit_up_pool=[]))

    assert [p["symbol"] for p in pool] == ["600519"], (
        "事件方向没进候选池——多半是回退成 store.directions_of 后异常被吞"
    )
    assert pool[0] == {"symbol": "600519", "from": "event", "prio": 1}
    assert not [w for w in warnings if w.startswith("picks candidate: events failed")], (
        f"事件路被异常吞掉了（异常吞没会同时让守卫失效）：{warnings}"
    )


def test_candidate_pool_theme_members_go_through_bulk_only(monkeypatch):
    """**P-3① 回归位**：题材方向的成分股只许走**一次批量查询**，不得逐题材单查。

    旧实现是循环内 `svc.get_members(code)`——每个题材一个独立 session，且这些
    **同步** SQLite 调用落在 async 函数里（与 P-2 同族，会阻塞事件循环）。
    改用既有 `member_symbols_bulk`（P0-2 已为另一处消费方补齐「与 `get_members`
    同序」的性质），故本用例同时钉两件事：**次数**（单查必须为 0）与**次序**
    （`[:30]` 截断依赖 bulk 与单查同序）。

    桩带计数 ⇒ 改回逐题材单查后 `member_bulk_calls` 变 0、`member_single_calls`
    由 0 变 2 ⇒ 精确变红。
    """
    monkeypatch.setattr(pl.log, "warning", lambda msg, *a: None)
    cat = _Catalog()

    pool = asyncio.run(pl.candidate_pool(_BareHub(), _ThemeStore(), cat, limit_up_pool=[]))

    # 次序即断言：批量版按 symbol 定序 ⇒ 000858 排在 600519 之前
    assert [p["symbol"] for p in pool] == ["000858", "600519"]
    assert {p["from"] for p in pool} == {"event_theme"}
    assert cat.member_bulk_calls == 1, "题材成分必须走**一次**批量查询"
    assert cat.member_single_calls == 0, "不得逐题材调 get_members（N 次独立 session）"


def test_candidate_pool_unknown_theme_name_triggers_no_query(monkeypatch):
    """目录里查不到的题材名：整条分支跳过（不臆造归属），且**不触发任何成分查询**。"""
    monkeypatch.setattr(pl.log, "warning", lambda msg, *a: None)
    cat = _Catalog()

    class _UnknownThemeStore:
        def list_events(self, *, active_only: bool = False, limit: int = 30):
            return [_Event(8, "未知题材事件", target="目录里没有的题材", target_type="theme")]

    pool = asyncio.run(pl.candidate_pool(_BareHub(), _UnknownThemeStore(), cat, limit_up_pool=[]))

    assert pool == []
    assert cat.member_bulk_calls == 0 and cat.member_single_calls == 0


def test_deep_scan_prefetches_official_themes_once(deps, catalog, monkeypatch):
    """**P-3② 回归位**：逐候选「股票 → 官方题材」反查只许**一次批量查询**。

    旧实现在 `_theme_benchmark` 函数体内调 `svc.get_official_for_symbol(symbol)`
    ——每只 2 次同步 SQLite（成员 + 人工纠错），24 只候选 ≈ 48 次落在 async 循环里。
    现在进循环前一次 `official_for_symbols_bulk`，循环内退化为内存查表。

    等价性（批量 == 逐只）由
    `test_theme_catalog.py::test_official_for_symbols_bulk_matches_single_read`
    直接 A/B 对照钉住；本用例只钉**调用路径**：批量恰一次、单查恰好零次，
    且没有走异常兜底——批预取的失败路径会 `log.warning("picks official themes
    bulk failed")`，与 P-2 那条注释同一个坑：**异常被吞会让次数断言失去意义**。
    """
    warnings: list[str] = []
    monkeypatch.setattr(pl.log, "warning", lambda msg, *a: warnings.append(msg % a if a else msg))
    monkeypatch.setattr(pl, "evaluate_stand_aside", _benign_gate)

    _run(deps, _Hub())

    assert catalog.official_bulk_calls == 1, "官方题材反查必须走**一次**批量查询"
    assert catalog.official_single_calls == 0, "不得逐候选调 get_official_for_symbol"
    assert not [w for w in warnings if "picks official themes bulk failed" in w], (
        f"批量反查被异常兜底了（异常吞没会同时让次数断言失去意义）：{warnings}"
    )


def test_active_events_fetched_once_per_pipeline(deps, store, monkeypatch):
    """**G-2 回归位**：一次管线里活跃事件只许取一次（取数单点）。

    同一条 `store.list_events(active_only=True, limit=30)` 此前在管线里跑了**三遍**
    ——候选池的题材反查、regime 的 `ev_texts`、消息命中索引各一遍。三遍都是**同步
    SQLite**，且管线是由调度（15:00+）与 `POST /api/picks/generate` **在事件循环上**
    直接 await 的 ⇒ 既白读两遍，又把同一段阻塞排了三次（连同 QuoteHub 的秒级行情
    推送一起停摆）。

    现在管线开头预取一次、下游三处复用（与 `limit_up_pool` 的 P2-4 取数单点同型）；
    `_build_event_hits_index` 随之从"接 store 自己查"改为"接事件行"的纯内存函数。

    断言看**次数**不看行为：桩缺计数就测不出"又查了两遍"（同 `_Catalog` 那条教训）。
    """
    monkeypatch.setattr(pl, "evaluate_stand_aside", _benign_gate)

    _run(deps, _Hub())

    assert store.list_events_calls == 1, (
        f"管线里活跃事件应**只取一次**，实际 {store.list_events_calls} 次"
        "（多了 = 又退回「同一条同步查询跑多遍」）"
    )


def test_theme_benchmark_is_pure_and_prefers_strongest_theme():
    """`_theme_benchmark` 抽成纯函数的直测：题材归属由参数传入，且取**最强**题材。

    原签名带 `svc`/`symbol` 并在函数体内查库 ⇒ 无法直测（必须起 DB）。抽纯后：
    ① 传入多个所属题材时取当日均涨幅最高者；
    ② 一个都匹配不上时**回退大盘**（诚实降级，不臆造题材归属）。
    """
    lu_ctx = {
        "themes": {
            "白酒概念": {"changes": [3.0, 5.0]},
            "次高端": {"changes": [9.0]},
            "无量题材": {"changes": []},
        }
    }
    best, name = pl._theme_benchmark(
        themes=[{"theme_code": "BK0001", "theme_name": "白酒概念"},
                {"theme_code": "BK0002", "theme_name": "次高端"}],
        lu_ctx=lu_ctx, market_pct=1.2,
    )
    assert (best, name) == (9.0, "次高端")

    fallback, none_name = pl._theme_benchmark(themes=None, lu_ctx=lu_ctx, market_pct=1.2)
    assert (fallback, none_name) == (1.2, None), "匹配不到所属题材时回退大盘，不臆造归属"


def test_deps_are_explicit_not_request_shaped(deps):
    """S2-4：管线只认 PipelineDeps，不再需要 request / SimpleNamespace。"""
    assert isinstance(deps, pl.PipelineDeps)
    assert not hasattr(deps, "app")
    # from_state 是 route/调度侧的装配入口
    state = type("S", (), {"event_store": _Store(), "snapshot_service": _Snapshot()})()
    built = pl.PipelineDeps.from_state(state)
    assert built.theme_catalog is None  # 缺省不报错（theme 匹配不到就走诚实降级）


# ---------------------------------------------------------------- 可参与性口径（2026-09-15 用户指令）


class _SealRec:
    """涨停池记录桩：**首封时间可变**——本组用例的判定全看它。"""

    def __init__(self, symbol: str, first_seal_time: str, boards: int = 1):
        self.symbol = symbol
        self.name = symbol
        self.consecutive_boards = boards
        self.break_count = 0
        self.first_seal_time = first_seal_time
        self.float_market_cap = 8.0e9
        self.amount = 3.0e8
        self.reason = "白酒概念"
        self.change_pct = 10.0


def _lu_ctx(n_up: int = 3, n_filler: int = 0) -> dict:
    """涨停板生态上下文桩：n_up 家同属「白酒概念」，n_filler 家属其它题材。

    n_filler 用来把"占当日涨停比例"压到阈值以下——集中度判据有两维，
    只造一维的 fixture 会让另一维静默失去覆盖。
    """
    syms = [f"6000{i:02d}" for i in range(n_up)]
    rec = {"first_seal_time": "09:25:00", "consecutive_boards": 1}
    records = {s: dict(rec) for s in syms}
    records.update({f"3000{i:02d}": dict(rec) for i in range(n_filler)})
    return {
        "records": records,
        "market_max_boards": 1,
        "themes": {
            "白酒概念": {
                "symbols": syms, "max_boards": 1, "changes": [10.0] * n_up,
                "levels": {1: n_up},
            }
        },
    }


def test_candidate_pool_drops_open_sealed_and_audits_it():
    """开盘即涨停（首封 ≤09:30）的涨停池成员**不进候选池**，且明细必须留痕。

    留痕不是可选项：没有 `excluded_open_sealed`，"今天名单里为什么没有那只一字板"
    在复盘时就成了一句无法核对的解释（与本文件 ⑤b 出列留痕同源纪律）。
    """
    pool = [
        _SealRec("600111", "09:25:00"),  # 竞价一字板——全天买不进
        _SealRec("600112", "09:31:00"),  # 09:30 之后封板：当日曾有参与窗口
        _SealRec("600113", "14:20:00"),  # 尾盘板
    ]
    audit: dict = {}
    got = asyncio.run(
        pl.candidate_pool(_BareHub(), _Store(), None, limit_up_pool=pool, audit=audit)
    )
    syms = [p["symbol"] for p in got]
    assert "600111" not in syms, "开盘即涨停被选进了候选池——这正是本轮要修的病"
    assert {"600112", "600113"} <= set(syms), "非开盘即封的涨停股不该被误伤"
    exc = audit["excluded_open_sealed"]
    assert exc["count"] == 1
    assert exc["items"][0] == {"symbol": "600111", "name": "600111",
                               "first_seal_time": "09:25:00"}


def test_candidate_pool_puts_linkage_sources_first():
    """**装配顺序独立于取数顺序**：联动股取数最晚拿到，却排在最前。

    这不是排序洁癖——候选池 cap 40、深评 cap 24，来源靠后就等于被截断掉。
    而题材联动的取数天然发生在事件/涨停池之后（要先算出题材集中度），
    故顺序必须由 `_SOURCE_ORDER` 显式给定，不能靠 dict 插入序。
    """
    linkages = [{"symbol": "000001", "basis": "联动甲"}, {"symbol": "000002"}]
    got = asyncio.run(
        pl.candidate_pool(
            _BareHub(), _Store(), None,
            limit_up_pool=[_SealRec("600113", "14:20:00")], linkages=linkages,
        )
    )
    # theme_linkage(0) → event(1) → limit_up(2)；同档内保持插入序
    assert [p["symbol"] for p in got] == ["000001", "000002", "600519", "600113"]
    assert got[0]["from"] == "theme_linkage" and got[0]["prio"] == 2


def test_candidate_pool_records_linkage_absence():
    """未预取联动股（linkages=None）时来源缺席必须**显式记账**，不能静默少一路。"""
    audit: dict = {}
    asyncio.run(
        pl.candidate_pool(_BareHub(), _Store(), None, limit_up_pool=[], audit=audit)
    )
    assert "未预取" in audit["theme_linkage"]["note"]
    assert audit["sources"] == {"event": 1}


def test_mine_theme_linkage_degrades_honestly():
    """三态降级：无集中题材 / 目录不可用 / 快照不可用 —— 各自给**原因**，不臆造。"""
    snap = [{"symbol": "600000", "name": "甲", "change_pct": 3.0, "amount": 1.0e8}]

    # ① 家数不足（2 < 3）
    got = asyncio.run(pl.mine_theme_linkage(object(), _lu_ctx(2), snap))
    assert got["items"] == [] and "集中阈值" in got["note"]

    # ② 家数够但占当日涨停比例不足（3/43 ≈ 7% < 10%）—— 极端日不硬挖
    got = asyncio.run(pl.mine_theme_linkage(object(), _lu_ctx(3, n_filler=40), snap))
    assert got["items"] == [] and "集中阈值" in got["note"]

    # ③ 题材目录服务不可用
    got = asyncio.run(pl.mine_theme_linkage(None, _lu_ctx(3), snap))
    assert got["items"] == [] and "目录服务不可用" in got["note"]

    # ④ 全市场快照不可用：可参与性依赖实时盘口 ⇒ 跳过而不是拿"没有盘口"当"可参与"
    got = asyncio.run(pl.mine_theme_linkage(object(), _lu_ctx(3), []))
    assert got["items"] == [] and "快照不可用" in got["note"]


def test_mine_theme_linkage_end_to_end(monkeypatch):
    """端到端：涨停集中题材 → 成分重叠定容器 → 容器内未涨停成分 → 可参与候选。

    这里把 `build_theme_index` 打成桩（它是同步 SQLite 全表读，属外部边界），
    其余全走真实实现——保证钉住的是"筛选链"本身，而不是一个被 mock 掉的过程。
    """
    from app.picks import board_surge

    index = {
        "600000": [("BK0001", "白酒概念")],
        "600001": [("BK0001", "白酒概念")],
        "600002": [("BK0001", "白酒概念")],
        # 同容器内的**未涨停**成分（要挖的就是它）
        "600009": [("BK0001", "白酒概念")],
    }
    monkeypatch.setattr(
        board_surge, "build_theme_index", lambda *a, **k: (index, {"BK0001": "白酒概念"})
    )
    snap = [
        {"symbol": "600009", "name": "联动甲", "change_pct": 6.8, "amount": 1.0e8, "price": 12.0},
        {"symbol": "600003", "name": "非成分", "change_pct": 8.0, "amount": 1.0e8, "price": 9.0},
    ]
    out = asyncio.run(pl.mine_theme_linkage(object(), _lu_ctx(3), snap))

    assert out["note"] is None
    # 600000/600001/600002 在涨停池里（"尚未涨停"判据）；600003 不是容器成分 ⇒ 均不入选
    assert [c["symbol"] for c in out["items"]] == ["600009"]
    c = out["items"][0]
    assert c["tradability"]["level"] == "可参与"
    assert c["linkage"]["level"] == "高"  # 6.8% 已进主板临板区
    t = out["themes"][0]
    assert t["theme"] == "白酒概念" and t["container"] == "白酒概念"
    assert t["container_code"] == "BK0001" and t["candidates"] == 1


def test_pipeline_meta_records_tradability_policy(deps):
    """meta 落 `tradability_policy`：口径、剔除明细、联动结果、来源计数四件套齐全。"""
    out = _run(deps, _Hub())
    pol = out["data"]["meta"]["tradability_policy"]
    assert pol["open_seal_cutoff"] == "09:30"
    assert pol["excluded_open_sealed"]["count"] == 0  # 桩池首封 09:35，非开盘即封
    assert "theme_linkage" in pol and "candidate_sources" in pol


def test_assemble_card_carries_tradability_and_source():
    """卡片必须透出「可参与性」与「入选来源」——否则用户无法核对新口径是否生效。"""
    card = pl.assemble_card(
        {
            "symbol": "600001", "name": "甲", "price": 10.0, "change_pct": 3.0,
            "score": 70.0, "sub_scores": {}, "bases": {}, "vetoes": [],
            "echelon_role": "首板", "theme": "白酒概念", "theme_stage": "发酵",
            "boards": None, "related_events": [],
            "tradability": {"level": "可参与", "basis": "未封在涨停板，报价可成交"},
            "source": "theme_linkage",
            "source_basis": "题材内涨停 4 家形成集中，本股尚未涨停（3.0%）",
        }
    )
    assert card["tradability"]["level"] == "可参与"
    assert card["source"] == "theme_linkage" and "尚未涨停" in card["source_basis"]


def test_pipeline_excludes_boards_without_permission(deps):
    """**账户权限**（2026-09-15 用户「只有主板的权限现在」）：非主板不进组合。

    过滤点刻意选在**既有的可交易性收口**（与「剔 ST/退」同一处）而不是各来源入口——
    候选池有 4 路来源，逐一过滤就是 4 份判据；一处即全覆盖。

    构造方式：让**上游涨停池**里带一只创业板票（最贴近真实的进入路径），
    而不是在 `get_quotes` 里改名——后者会因 `quotes` 的键与原 symbol 不匹配
    而在更早的"无行情"分支被拦掉，测不到板块过滤本身。
    """
    from app.picks.tradability import is_tradable

    class _MixedProvider(_Provider):
        async def get_limit_up_pool(self, d):
            self.pool_dates.append(d)
            return [
                _PoolRec("600519", 3, "白酒概念+消费刺激"),
                _PoolRec("000858", 1, "白酒概念"),
                _PoolRec("300750", 1, "锂电池"),      # 创业板：无交易权限
            ]

        async def get_quotes(self, symbols):
            out = []
            for s in symbols:
                out.append(
                    Quote(
                        symbol=s, source="tencent",
                        name={"600519": "贵州茅台", "000858": "五粮液",
                              "300750": "宁德时代"}.get(s, f"票{s}"),
                        price=100.0, change_pct=3.2, amount=3.0e9,
                        pe_ttm=30.0, pb=8.0, total_mktcap_yi=1000.0, float_mktcap_yi=1000.0,
                    )
                )
            return out

    class _MixedHub:
        def __init__(self):
            self.provider = _MixedProvider()

    out = _run(deps, _MixedHub())
    pol = out["data"]["meta"]["tradability_policy"]
    assert pol["tradable_boards"].startswith("沪市主板")
    # 创业板票必须出现在剔除明细里（否则"组合里为什么没有它"无从核对）
    excluded = {i["symbol"]: i["board"] for i in pol["excluded_board"]["items"]}
    assert excluded.get("300750") == "创业板"
    assert pol["excluded_board"]["count"] >= 1
    # 组合与落选名单里都不得出现非主板
    for item in out["data"]["items"]:
        assert is_tradable(item["symbol"]), item
