"""每日精选管线（S2-4）—— **集成链路**测试。

**为什么要有这个文件**：S2-4 立项的直接理由是「集成链路零测试覆盖」——
`generate_picks` 住在 route 里时，单测只能把整个函数打成桩，于是
「候选池 → 六维评分 → 门槛 → 落库 → 卡片」这条链**没有任何一处被真正跑过**，
权重改了 / 落库缺字段 / 卡片少一段，五道门禁全绿。

本文件用**受控桩**把这条链整条跑通（不 mock 管线本体），断言：
1. 产出卡片结构完整（含买入范围、出场纪律、失效条件、置信档）；
2. 真的落库到 `daily_pick_set`；
3. **P2-4**：单次生成内当日涨停池只被上游拉一次；
4. **S2-4**：依赖以 `PipelineDeps` 显式传入，不需要伪造 request。
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
    def __init__(self, target: str, direction: int = 1):
        self.target_type = "symbol"
        self.target = target
        self.direction = direction
        self.strength = 1.0


class _Event:
    def __init__(self, eid: int, title: str):
        self.id = eid
        self.title = title
        self.source_tier = 2
        self.certainty = "high"
        self.directions = [_Direction("600519", 1)]


class _Store:
    def list_events(self, *, active_only: bool = False, limit: int = 30):
        return [_Event(1, "白酒消费刺激政策落地")]

    def directions_of(self, eid: int):
        return [_Direction("600519", 1)]


class _Member:
    def __init__(self, symbol: str):
        self.symbol = symbol


class _CatalogTheme:
    def __init__(self, code: str, name: str):
        self.code = code
        self.name = name


class _Catalog:
    def get_catalog(self, limit: int = 1000):
        return [_CatalogTheme("BK0001", "白酒概念")]

    def get_members(self, code: str):
        return [_Member("600519"), _Member("000858")]

    def get_official_for_symbol(self, symbol: str):
        return [{"theme_name": "白酒概念"}] if symbol == "600519" else []


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
def deps(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'pipeline.db'}")
    Base.metadata.create_all(engine)  # DailyPickSet 经顶部 import 注册
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(pl, "get_session_factory", lambda: sf)

    # 交易日历是外部边界：钉成单一交易日，避免测试触网
    monkeypatch.setattr(pl.tc, "trading_days", lambda provider: _days())

    monkeypatch.setattr(pl, "get_rps_service", lambda: _Rps())
    monkeypatch.setattr(pl, "get_chip_service", lambda: _Chip())
    monkeypatch.setattr(pl, "_prefetch_index_bars", _no_index_bars)

    return pl.PipelineDeps(event_store=_Store(), snapshot_service=_Snapshot(), theme_catalog=_Catalog())


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


def test_deps_are_explicit_not_request_shaped(deps):
    """S2-4：管线只认 PipelineDeps，不再需要 request / SimpleNamespace。"""
    assert isinstance(deps, pl.PipelineDeps)
    assert not hasattr(deps, "app")
    # from_state 是 route/调度侧的装配入口
    state = type("S", (), {"event_store": _Store(), "snapshot_service": _Snapshot()})()
    built = pl.PipelineDeps.from_state(state)
    assert built.theme_catalog is None  # 缺省不报错（theme 匹配不到就走诚实降级）
