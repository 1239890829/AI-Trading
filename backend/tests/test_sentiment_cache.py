"""共享情绪缓存槽（P1-3，2026-09-11）。

**病根**：`compute_market_sentiment` 是全项目最重的读路径之一（全市场宽度 +
最近两个交易日涨停池 + 炸板池 + 指标库分位校准）。此前它有四个各持取数逻辑的
消费方——市场页情绪卡、介入条件清单的相位、猎场相位路由（自建
`picks.live_sentiment` 槽）、事件排序上下文（自建 build 闭包）——风控引擎更是
每次 `refresh()` 全量重算。同一个 60s 窗口内，同一个值被算了 N 遍。

**本文件钉住的契约**：
1. 一个槽（`market.sentiment`）、一次计算、多处消费；
2. 槽里存**领域对象**而非展示信封（信封是展示层形状，各端点自包）；
3. 降级姿势由消费方决定，缓存层**不吞异常**（`CalendarUnavailable` 必须能冒出来）；
4. 异常不写缓存（TTLCache 契约），下一次调用重试而不是把失败固化 60s。
"""

from __future__ import annotations

import asyncio

import pytest

import app.services.market_context as mc
from app.api.routes import market_sentiment as sentiment_route
from app.api.routes import market_themes as themes_route
from app.api.routes import picks as picks_route
from app.events.ranking import collect_rank_context
from app.risk.engine import RiskEngine

def _run(coro):
    return asyncio.run(coro)


class _Hub:
    """QuoteHub 替身：`_meta()` 要 provider.name / is_stale / last_success_refresh，
    `collect_rank_context` 要 get_quotes。缺 `freshness()` → 走 getattr 回退路径，
    结果按 Freshness 规则派生为 unknown（不假装 ready），与生产降级一致。
    """

    def __init__(self):
        self.provider = type("P", (), {"name": "mock", "realtime": True})()
        self.last_success_refresh = None
        self.stale_after = 10.0
        self.indices: dict = {}

    def is_stale(self) -> bool:
        return True

    def get_quotes(self, symbols):
        return []


HUB = _Hub()


class _State:
    """进程级单例替身：`cache_on` 会往它身上 setattr 挂缓存。"""

    def __init__(self, hub=HUB, breadth=None):
        self.hub = hub
        self.snapshot_service = type("Snap", (), {"breadth": breadth})()
        self.theme_catalog = None


class _Request:
    def __init__(self, state):
        self.app = type("App", (), {"state": state})()


def _patch(monkeypatch, counter, *, raises=None):
    """把情绪计算打桩成计数器。

    `market_context` 内部按**模块全局名**解析，所以打 `mc.compute_market_sentiment`
    即可命中 `get_cached_sentiment`；`risk/engine.py` 是模块级 `from ... import`，
    名字绑定在它自己的模块上，需单独打一次。
    """
    async def _fake(hub, snapshot_service, **kwargs):
        counter["n"] += 1
        if raises is not None:
            raise raises
        return {"phase": "高潮", "temperature": 60, "call": counter["n"]}

    monkeypatch.setattr(mc, "compute_market_sentiment", _fake)
    import app.risk.engine as re_mod

    monkeypatch.setattr(re_mod, "compute_market_sentiment", _fake, raising=False)
    return _fake


# ---------------------------------------------------------------- 槽的基本契约


def test_two_calls_within_ttl_compute_once(monkeypatch):
    state, counter = _State(), {"n": 0}
    _patch(monkeypatch, counter)

    a = _run(mc.get_cached_sentiment(state, HUB))
    b = _run(mc.get_cached_sentiment(state, HUB))

    assert counter["n"] == 1, "同一 60s 窗口内第二次调用必须命中缓存"
    assert a is b, "缓存返回的应是同一个对象，不该每次重建"
    assert a["phase"] == "高潮"


def test_slot_is_scoped_to_the_holder(monkeypatch):
    """槽挂在 holder 上：传不同的 state 就是两个槽。

    这条不是「功能」，而是把**契约**写死——调用方必须一律传 `app.state`，
    传 `hub` / 局部对象等于另开一槽，合一的收益当场归零。
    """
    counter = {"n": 0}
    _patch(monkeypatch, counter)

    _run(mc.get_cached_sentiment(_State(), HUB))
    _run(mc.get_cached_sentiment(_State(), HUB))

    assert counter["n"] == 2


def test_failure_is_not_cached(monkeypatch):
    """`CalendarUnavailable` 不得被固化 60s——否则一次日历抖动会让相位空窗一分钟。"""
    from app.services.market_context import CalendarUnavailable

    state, counter = _State(), {"n": 0}
    _patch(monkeypatch, counter, raises=CalendarUnavailable("日历不可用"))

    with pytest.raises(CalendarUnavailable):
        _run(mc.get_cached_sentiment(state, HUB))

    _patch(monkeypatch, counter)  # 上游恢复
    assert _run(mc.get_cached_sentiment(state, HUB))["phase"] == "高潮"
    assert counter["n"] == 2, "失败那次不该被缓存，恢复后应重新计算"


# ---------------------------------------------------------------- 多消费方共用


def test_all_consumers_share_one_compute(monkeypatch):
    """市场页 / 介入条件清单 / 猎场相位路由 / 事件排序 / 风控：五次消费，一次计算。

    这是 P1-3 的核心回归位。任一消费方将来重新自建取数逻辑，这里立刻变红。
    """
    state, counter = _State(), {"n": 0}
    _patch(monkeypatch, counter)
    req = _Request(state)

    envelope = _run(sentiment_route.market_sentiment(req, HUB))          # 市场页情绪卡
    phase = _run(themes_route._market_phase_cached(req, HUB))          # 介入条件清单
    routed = _run(picks_route._live_style_routing(req, HUB, None))     # 猎场相位路由
    ctx = _run(collect_rank_context(state, [], []))                    # 事件排序
    engine = RiskEngine(hub=HUB, snapshot_service=state.snapshot_service,
                        session_factory=None, app_state=state)
    _run(engine.refresh())                                             # 风控刷新

    assert counter["n"] == 1, f"应只算一次，实际 {counter['n']} 次"
    assert envelope["data"]["phase"] == "高潮"
    assert phase == "高潮"
    assert routed["phase_source"] == "live"
    assert ctx.phase == "高潮"
    assert engine.state in ("数据不足", "进攻", "防守", "中性")  # 只要求它跑通


def test_picks_no_longer_owns_a_separate_slot(monkeypatch):
    """`picks.live_sentiment` 槽必须彻底消失（P1-3 前它每次多算一遍）。"""
    state, counter = _State(), {"n": 0}
    _patch(monkeypatch, counter)

    _run(picks_route._live_style_routing(_Request(state), HUB, None))

    assert not hasattr(state, "_ttl_cache_picks.live_sentiment")
    assert counter["n"] == 1


# ---------------------------------------------------------------- 降级姿势归消费方


def test_market_sentiment_route_wraps_own_envelope(monkeypatch):
    """槽里是领域对象，信封由端点自包——`meta` 属于本次响应，不属于缓存。"""
    state, counter = _State(), {"n": 0}
    _patch(monkeypatch, counter)

    out = _run(sentiment_route.market_sentiment(_Request(state), HUB))

    assert set(out) == {"data", "meta"}
    assert out["data"]["phase"] == "高潮"
    assert out["meta"]["provider"] is not None


def test_market_sentiment_route_maps_calendar_unavailable_to_503(monkeypatch):
    from fastapi import HTTPException

    from app.services.market_context import CalendarUnavailable

    state, counter = _State(), {"n": 0}
    _patch(monkeypatch, counter, raises=CalendarUnavailable("快照未就绪"))

    with pytest.raises(HTTPException) as ei:
        _run(sentiment_route.market_sentiment(_Request(state), HUB))
    assert ei.value.status_code == 503


def test_entry_checklist_degrades_to_none_not_500(monkeypatch):
    """同一份异常，介入条件清单的姿势是「相位未判定」而非 503——降级归消费方。"""
    state, counter = _State(), {"n": 0}
    _patch(monkeypatch, counter, raises=RuntimeError("boom"))

    assert _run(themes_route._market_phase_cached(_Request(state), HUB)) is None


def test_rank_context_records_degradation(monkeypatch):
    """事件排序同样不抛：情绪失败只记 degraded，其余因子照常参与。"""
    state, counter = _State(), {"n": 0}
    _patch(monkeypatch, counter, raises=RuntimeError("boom"))

    ctx = _run(collect_rank_context(state, [], []))

    assert ctx.phase is None
    assert any("情绪不可用" in d for d in ctx.degraded)


def test_picks_style_routing_falls_back_to_stored_snapshot(monkeypatch):
    """猎场路由失败 → 用生成时刻快照兜底，并显式标注来源（三态纪律）。"""
    from app.services.market_context import CalendarUnavailable

    state, counter = _State(), {"n": 0}
    _patch(monkeypatch, counter, raises=CalendarUnavailable("快照未就绪"))
    stored = {"style": "题材进攻", "routed": True}

    out = _run(picks_route._live_style_routing(_Request(state), HUB, stored))

    assert out["style"] == "题材进攻"
    assert out["phase_source"] == "unavailable"
    assert "快照未就绪" in out["phase_note"]


# ---------------------------------------------------------------- 风控无 state 时


def test_risk_engine_without_app_state_computes_directly(monkeypatch):
    """单测/脚本直接构造 RiskEngine（无 app.state）时退回直算，不为了缓存造假 state。"""
    counter = {"n": 0}
    _patch(monkeypatch, counter)
    snap = type("Snap", (), {"breadth": None})()

    engine = RiskEngine(hub=HUB, snapshot_service=snap, session_factory=None)
    _run(engine.refresh())

    assert counter["n"] == 1
