"""盘中买点判定与推送（app/picks/buy_point.py）单测——零网络。

覆盖：
- evaluate_buy_points 多因素判定全分支（命中/置信不足/红线/闸门语义/无区间/
  区间外/涨停区/快照缺价）
- ensure_buy_point_rule 新建 + channels 跟随配置默认
- check_and_dispatch 服务层：聚合卡显式单发（多票命中只调一次 send_interactive）、
  去重（当日已推的票不再进卡）、非交易日/盘外不发
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest


class NS:
    """简易 namespace（test_watcher 同款）。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _rule_factory(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.watchlist import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'bp.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _item(symbol="600000", tier="executable", **extra):
    base = {
        "symbol": symbol,
        "name": f"股{symbol[-3:]}",
        "score": 82.0,
        "confidence": {"tier": tier, "label": "可执行" if tier == "executable" else tier},
        "vetoes": [],
        "observation_only": False,
        "buy_range": {"low": 10.2, "high": 10.8},
        "stop_loss": {"pct": 5, "price": 10.0},
        "invalidations": ["破分时均线"],
        "bases": {"echelon": "题材龙头"},
    }
    base.update(extra)
    return base


def _quote(symbol="600000", price=10.5, change_pct=2.1, **extra):
    q = {"symbol": symbol, "price": price, "change_pct": change_pct, "prev_close": price / (1 + change_pct / 100)}
    q.update(extra)
    return q


# ---------------------------------------------------------------- 判定器

def test_evaluate_hit_full_factors():
    hits, skips = asyncio.run(_ev([_item()], gate_stand=False, quotes={"600000": _quote()}))
    assert len(hits) == 1 and not skips
    h = hits[0]
    assert h["price"] == 10.5 and h["low"] == 10.2 and h["high"] == 10.8
    assert h["tier_label"] == "可执行"


async def _ev(items, *, gate_stand, quotes):
    from app.picks.buy_point import evaluate_buy_points

    return evaluate_buy_points(items, gate_stand=gate_stand, quotes=quotes)


def test_evaluate_skips_each_factor():
    quotes = {"600000": _quote(), "600001": _quote("600001"), "600002": _quote("600002"),
              "600003": _quote("600003"), "600004": _quote("600004"), "600005": _quote("600005")}
    items = [
        _item("600000", tier="observe"),                      # 置信不足
        _item("600001", vetoes=["R1 停牌前兆"]),               # 红线
        _item("600002", buy_range=None),                      # 无区间不臆造
        _item("600003", buy_range={"low": 11.0, "high": 11.5}),  # 现价不在区间
        _item("600004", buy_range={"low": 9.0, "high": 11.5}),   # 触涨停区
        _item("600005"),                                       # 命中（对照组）
    ]
    quotes["600004"] = _quote("600004", price=11.2, change_pct=9.8)
    hits, skips = asyncio.run(_ev(items, gate_stand=False, quotes=quotes))
    assert [h["item"]["symbol"] for h in hits] == ["600005"]
    assert [s["symbol"] for s in skips] == ["600000", "600001", "600002", "600003", "600004"]
    assert all(s["reason"] for s in skips)  # skip 理由可观测


def test_evaluate_gate_day_requires_followable():
    """闸门日只有 follow_state=followable 才进入买点；observe/blocked 都不推。"""
    good = _item(follow_state="followable")
    bad = _item(follow_state="observe")
    blocked = _item(follow_state="blocked")
    quotes = {"600000": _quote()}
    hits, _ = asyncio.run(_ev([good], gate_stand=True, quotes=quotes))
    assert len(hits) == 1
    hits2, _ = asyncio.run(_ev([bad], gate_stand=True, quotes=quotes))
    assert not hits2
    hits3, _ = asyncio.run(_ev([blocked], gate_stand=True, quotes=quotes))
    assert not hits3


def test_evaluate_non_gate_day_observation_only_skipped():
    _, skips = asyncio.run(_ev([_item(observation_only=True)], gate_stand=False, quotes={"600000": _quote()}))
    assert len(skips) == 1 and "仅观察" in skips[0]["reason"]


def test_evaluate_missing_price_never_hits():
    """快照缺现价 → 不判（缺失 ≠ 可买，三态纪律）。"""
    hits, skips = asyncio.run(_ev([_item()], gate_stand=False, quotes={"600000": {"price": None}}))
    assert not hits and "现价" in skips[0]["reason"]


def test_evaluate_change_pct_missing_self_computed():
    """change_pct 缺失时用 price/prev_close 自算（数据源纪律），不阻断判定。"""
    q = {"price": 10.5, "change_pct": None, "prev_close": 10.0}
    hits, _ = asyncio.run(_ev([_item()], gate_stand=False, quotes={"600000": q}))
    assert len(hits) == 1 and hits[0]["chg"] == pytest.approx(5.0)


# ---------------------------------------------------------------- 规则


def test_ensure_buy_point_rule_follows_config(tmp_path, monkeypatch):
    import json

    from app.core.config import settings
    from app.picks.buy_point import BUY_POINT_RULE_NAME, ensure_buy_point_rule

    monkeypatch.setattr(settings, "picks_buy_point_channels", "in_app,log")
    factory = _rule_factory(tmp_path)
    row = ensure_buy_point_rule(factory)
    assert json.loads(row.channels) == ["in_app", "log"]
    assert row.name == BUY_POINT_RULE_NAME
    # 配置变化 → 同步
    monkeypatch.setattr(settings, "picks_buy_point_channels", "in_app")
    row2 = ensure_buy_point_rule(factory)
    assert json.loads(row2.channels) == ["in_app"]


# ---------------------------------------------------------------- 服务层


def _patch_happy_path(monkeypatch, tmp_path, *, items=None, quotes=None, hits_override=None):
    """check_and_dispatch 的全部外设 stub：日历/payload/快照/情绪/飞书。"""
    import app.picks.buy_point as bp
    from app.core.config import settings
    from app.market import trade_calendar as tc

    items = items if items is not None else [_item(), _item("600001", tier="observe")]
    quotes = quotes if quotes is not None else {"600000": _quote(), "600001": _quote("600001", price=5.5)}

    d = datetime(2026, 9, 8, 10, 30, 0)
    monkeypatch.setattr(tc, "trading_days", async_ok([d.date()]))
    monkeypatch.setattr(tc, "last_trade_date", lambda days, asof: asof)
    monkeypatch.setattr(tc, "in_trading_window", lambda now: True)
    monkeypatch.setattr(bp, "beijing_now", lambda: d)
    monkeypatch.setattr(bp, "_today_picks_payload", lambda: {
        "date": "2026-09-08",
        "items": items,
        "meta": {"gate": {"stand_aside": False, "level": "none"}},
    })
    state = NS(hub=NS(provider=NS()), snapshot_service=NS(snapshot=list(quotes.values()),
                                                           breadth={"up": 1, "down": 2}))
    app = NS(state=state)

    async def fake_sent(hub, snap):
        return {"temperature": "warm", "phase": "发酵", "indicators": []}

    monkeypatch.setattr("app.services.market_context.compute_market_sentiment", fake_sent)

    cards = {"n": 0, "cards": []}

    class FakeFeishu:
        async def send_interactive(self, card):
            cards["n"] += 1
            cards["cards"].append(card)
            return True

    monkeypatch.setattr(bp, "get_notifier_registry", lambda: NS(get=lambda name: FakeFeishu()))
    monkeypatch.setattr(settings, "picks_buy_point_channels", "in_app,log")
    # 简报层 stub（append_alert 依赖当日简报文件；进程内 set 模拟 key 去重，
    # 不触碰真实 BRIEF_DIR）
    import app.picks.morning_brief as mb

    seen: set = set()
    monkeypatch.setattr(mb, "brief_for_today", lambda: ("20260908", {"brief_date": "20260908", "alerts": []}))

    def fake_append(target, alert):
        key = alert.get("key")
        if key in seen:
            return False
        seen.add(key)
        return True

    monkeypatch.setattr(mb, "append_alert", fake_append)
    # 落库隔离：dispatch_alert/ensure_buy_point_rule 的 session_factory 注入 tmp 库
    import app.picks.watcher as w

    factory = _rule_factory(tmp_path)
    monkeypatch.setattr(w, "get_session_factory", lambda: factory)
    return app, cards, seen


def async_ok(v):
    async def _f(*a, **k):
        return v
    return _f


def test_check_and_dispatch_one_card_per_symbol(monkeypatch, tmp_path):
    """用户要求：每只股票一张独立卡片（不汇总）；卡内只含该票，命中数为 1。"""
    import app.picks.buy_point as bp

    items = [_item("600000"), _item("600001", tier="strong")]
    quotes = {"600000": _quote("600000"), "600001": _quote("600001", price=10.6)}
    app, cards, seen = _patch_happy_path(monkeypatch, tmp_path, items=items, quotes=quotes)
    dispatched = asyncio.run(bp.check_and_dispatch(app))
    assert [h["item"]["symbol"] for h in dispatched] == ["600000", "600001"]
    assert cards["n"] == 2  # 逐票单卡
    assert cards["cards"][0] != cards["cards"][1]
    for card in cards["cards"]:
        body = json.dumps(card, ensure_ascii=False)
        assert "盘中买点命中 1 只" in body
        assert card["header"]["template"] == "orange"  # 与每日精选卡同 template


def test_check_and_dispatch_dedup_per_day(monkeypatch, tmp_path):
    """同票第二拍不再发卡（append_alert key 去重）。"""
    import app.picks.buy_point as bp

    app, cards, seen = _patch_happy_path(monkeypatch, tmp_path)
    first = asyncio.run(bp.check_and_dispatch(app))
    second = asyncio.run(bp.check_and_dispatch(app))
    assert len(first) == 1 and not second and cards["n"] == 1


def test_check_and_dispatch_silent_off_session(monkeypatch, tmp_path):
    """非交易时段/非交易日：零分发零发送。"""
    import app.picks.buy_point as bp
    from app.market import trade_calendar as tc

    app, cards, seen = _patch_happy_path(monkeypatch, tmp_path)
    monkeypatch.setattr(tc, "in_trading_window", lambda now: False)
    assert not asyncio.run(bp.check_and_dispatch(app))
    assert cards["n"] == 0


def test_check_and_dispatch_feishu_failure_keeps_records(monkeypatch, tmp_path):
    """飞书发送失败：事件已落库（去重生效），不重试不崩——下票命中仍有机会。"""
    import app.picks.buy_point as bp

    app, cards, seen = _patch_happy_path(monkeypatch, tmp_path)

    class BrokenFeishu:
        async def send_interactive(self, card):
            return False

    monkeypatch.setattr(bp, "get_notifier_registry", lambda: NS(get=lambda name: BrokenFeishu()))
    dispatched = asyncio.run(bp.check_and_dispatch(app))
    assert len(dispatched) == 1  # 落库成功
    assert cards["n"] == 0
    # 下一拍去重生效（不再重复推）
    assert not asyncio.run(bp.check_and_dispatch(app))
