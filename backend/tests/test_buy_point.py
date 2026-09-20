"""盘中买点判定与推送（app/picks/buy_point.py）单测——零网络。

覆盖：
- evaluate_buy_points 多因素判定全分支（命中/置信不足/红线/闸门语义/无区间/
  区间外/涨停区/快照缺价）
- ensure_buy_point_rule 新建 + channels 跟随配置默认
- check_and_dispatch 服务层：每票卡进入 AlertEvent + Outbox，零 direct Feishu IO；
  DB durable dedup、brief 失败/DB 失败顺序、渠道关闭与非交易日/盘外不发
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

    from app.models.notification_outbox import NotificationOutbox
    from app.models.opportunity_learning import OpportunityDecisionSnapshot
    from app.models.watchlist import Base

    assert NotificationOutbox.__table__.name and OpportunityDecisionSnapshot.__table__.name
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


def test_unfresh_execution_snapshot_is_hard_reject():
    """有价格但动作时快照非 ready，必须从命中降级为拒绝，不能自动模拟执行。"""
    from app.picks.buy_point import _reject_unfresh_execution

    hits = [{"item": _item(), "price": 10.5}]
    kept, skips = _reject_unfresh_execution(
        hits, [], {"600000": {"state": "stale", "freshness_reason": "上游停更"}}
    )
    assert kept == []
    assert skips[0]["symbol"] == "600000"
    assert "stale" in skips[0]["reason"] and "只保留参考" in skips[0]["reason"]


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


def _patch_happy_path(monkeypatch, tmp_path, *, items=None, quotes=None, channels="in_app,log,feishu", brief_raises=False):
    """check_and_dispatch 外设 stub；真实通知事实落 tmp SQLite，网络 IO 必须为零。"""
    import app.picks.buy_point as bp
    import app.picks.morning_brief as mb
    import app.picks.position_engine as pe
    import app.picks.watch_ledger as wl
    import app.picks.watcher as w
    from app.core.config import settings
    from app.market import trade_calendar as tc
    from app.repositories.alert_repo import AlertRepository

    items = items if items is not None else [_item(), _item("600001", tier="observe")]
    quotes = quotes if quotes is not None else {"600000": _quote(), "600001": _quote("600001", price=5.5)}
    d = datetime(2026, 9, 8, 10, 30, 0)
    monkeypatch.setattr(tc, "trading_days", async_ok([d.date()]))
    monkeypatch.setattr(tc, "last_trade_date", lambda days, asof: asof)
    monkeypatch.setattr(tc, "in_trading_window", lambda now=None: True)
    monkeypatch.setattr(bp, "beijing_now", lambda: d)
    monkeypatch.setattr(bp, "_today_picks_payload", lambda: {
        "date": "2026-09-08",
        "items": items,
        "meta": {"gate": {"stand_aside": False, "level": "none"}},
    })

    factory = _rule_factory(tmp_path)
    counters = {"direct_io": 0, "brief": 0, "paper": 0, "ledger": 0}
    seen: set[str] = set()
    state = NS(
        hub=NS(provider=NS()),
        alert_repo=AlertRepository(factory),
        snapshot_service=NS(
            snapshot=list(quotes.values()),
            breadth={"up": 1, "down": 2},
            freshness=lambda: NS(
                state="ready", as_of=d, age_seconds=0.0, reason=None, source="sina_market",
            ),
        ),
    )
    app = NS(state=state)
    async def fake_sent(hub, snap):
        return {"temperature": "warm", "phase": "发酵", "indicators": []}

    class FakeFeishu:
        def delivery_target(self):
            return "f" * 64

        async def send_interactive(self, card):
            counters["direct_io"] += 1
            return True

    class FakeRegistry:
        def __init__(self):
            self.feishu = FakeFeishu()

        def get(self, name):
            return self.feishu if name == "feishu" else None

        async def dispatch(self, event, rule, *, exclude=()):
            wanted = json.loads(rule.channels or "[]")
            if "feishu" in wanted and "feishu" not in exclude:
                counters["direct_io"] += 1
            return [ch for ch in wanted if ch in {"in_app", "log"} and ch not in exclude]

    registry = FakeRegistry()
    monkeypatch.setattr("app.services.market_context.compute_market_sentiment", fake_sent)
    monkeypatch.setattr(w, "get_notifier_registry", lambda: registry)
    monkeypatch.setattr(settings, "picks_buy_point_channels", channels)
    monkeypatch.setattr(w, "get_session_factory", lambda: factory)
    monkeypatch.setattr("app.picks.opportunity_learning.get_session_factory", lambda: factory)
    monkeypatch.setattr(mb, "brief_for_today", lambda: ("20260908", {"brief_date": "20260908", "alerts": []}))

    def fake_append(target, alert):
        counters["brief"] += 1
        if brief_raises:
            raise OSError("brief unavailable")
        key = alert.get("key")
        if key in seen:
            return False
        seen.add(key)
        return True

    async def fake_open(*args, **kwargs):
        counters["paper"] += 1

    def fake_sighting(**kwargs):
        counters["ledger"] += 1

    monkeypatch.setattr(mb, "append_alert", fake_append)
    monkeypatch.setattr(pe, "maybe_open", fake_open)
    monkeypatch.setattr(wl, "record_sighting", fake_sighting)
    app._test_factory = factory
    return app, counters, seen


def async_ok(v):
    async def _f(*a, **k):
        return v
    return _f
def test_check_and_dispatch_persists_one_card_and_intent_per_symbol(monkeypatch, tmp_path):
    """每票一张卡进入 AlertEvent/Outbox；本拍不得直接做 Feishu 网络 IO。"""
    import app.picks.buy_point as bp
    from app.models.alert import AlertEvent
    from app.models.notification_outbox import NotificationOutbox

    items = [_item("600000"), _item("600001", tier="strong")]
    quotes = {"600000": _quote("600000"), "600001": _quote("600001", price=10.6)}
    app, counters, _ = _patch_happy_path(monkeypatch, tmp_path, items=items, quotes=quotes)
    dispatched = asyncio.run(bp.check_and_dispatch(app))
    assert [h["item"]["symbol"] for h in dispatched] == ["600000", "600001"]
    assert counters["direct_io"] == 0
    assert counters["paper"] == 2 and counters["brief"] == 2

    with app._test_factory() as db:
        events = db.query(AlertEvent).order_by(AlertEvent.id).all()
        outbox = db.query(NotificationOutbox).order_by(NotificationOutbox.id).all()
    assert len(events) == len(outbox) == 2
    assert len({e.dedup_key for e in events}) == 2
    by_symbol = {e.symbol: json.loads(e.snapshot or "{}") for e in events}
    for h in dispatched:
        sym = h["item"]["symbol"]
        archived = by_symbol[sym]["execution_ref"]
        live = h["execution_contract"]
        assert archived["decision_id"] == live["decision_id"]
        assert archived["decision_version"] == live["decision_version"]
        assert "盘中买点命中 1 只" in json.dumps(by_symbol[sym]["card"], ensure_ascii=False)
        row = next(o for o in outbox if o.event_id == next(e.id for e in events if e.symbol == sym))
        intent = json.loads(row.payload)["intent"]
        assert intent["kind"] == "picks_buy_point"
        assert intent["decision_version"] == live["decision_version"]
def test_check_and_dispatch_blocks_action_when_authoritative_archive_fails(monkeypatch, tmp_path):
    import app.picks.buy_point as bp
    from app.models.alert import AlertEvent
    from app.models.notification_outbox import NotificationOutbox

    app, counters, _ = _patch_happy_path(monkeypatch, tmp_path)

    async def archive_failed(**_kwargs):
        return False

    monkeypatch.setattr(bp, "_archive_notification_decisions", archive_failed)
    assert asyncio.run(bp.check_and_dispatch(app)) == []
    assert counters["direct_io"] == counters["paper"] == counters["brief"] == 0
    with app._test_factory() as db:
        assert db.query(AlertEvent).count() == 0
        assert db.query(NotificationOutbox).count() == 0


def test_check_and_dispatch_dedup_is_db_authoritative(monkeypatch, tmp_path):
    import app.picks.buy_point as bp
    from app.models.alert import AlertEvent
    from app.models.notification_outbox import NotificationOutbox

    app, counters, _ = _patch_happy_path(monkeypatch, tmp_path)
    assert len(asyncio.run(bp.check_and_dispatch(app))) == 1
    assert asyncio.run(bp.check_and_dispatch(app)) == []
    with app._test_factory() as db:
        assert db.query(AlertEvent).count() == 1
        assert db.query(NotificationOutbox).count() == 1
    assert counters["paper"] == 1
    assert counters["brief"] == 1
    assert counters["direct_io"] == 0


def test_brief_failure_does_not_revoke_or_duplicate_durable_intent(monkeypatch, tmp_path):
    import app.picks.buy_point as bp
    from app.models.alert import AlertEvent
    from app.models.notification_outbox import NotificationOutbox

    app, counters, _ = _patch_happy_path(monkeypatch, tmp_path, brief_raises=True)
    assert len(asyncio.run(bp.check_and_dispatch(app))) == 1
    assert asyncio.run(bp.check_and_dispatch(app)) == []
    with app._test_factory() as db:
        assert db.query(AlertEvent).count() == 1
        assert db.query(NotificationOutbox).count() == 1
    assert counters["brief"] == 1 and counters["paper"] == 1


def test_db_failure_happens_before_brief_and_paper(monkeypatch, tmp_path):
    import app.picks.buy_point as bp

    app, counters, _ = _patch_happy_path(monkeypatch, tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("db commit failed")

    monkeypatch.setattr(app.state.alert_repo, "record_trigger_once", broken)
    assert asyncio.run(bp.check_and_dispatch(app)) == []
    assert counters["brief"] == counters["paper"] == counters["direct_io"] == 0
def test_feishu_channel_off_means_no_outbox_and_no_direct_io(monkeypatch, tmp_path):
    import app.picks.buy_point as bp
    from app.models.alert import AlertEvent
    from app.models.notification_outbox import NotificationOutbox

    app, counters, _ = _patch_happy_path(monkeypatch, tmp_path, channels="in_app,log")
    assert len(asyncio.run(bp.check_and_dispatch(app))) == 1
    with app._test_factory() as db:
        event = db.query(AlertEvent).one()
        assert db.query(NotificationOutbox).count() == 0
        assert json.loads(event.delivered_channels) == ["in_app", "log"]
    assert counters["direct_io"] == 0


def test_check_and_dispatch_silent_off_session(monkeypatch, tmp_path):
    import app.picks.buy_point as bp
    from app.market import trade_calendar as tc
    from app.models.alert import AlertEvent

    app, counters, _ = _patch_happy_path(monkeypatch, tmp_path)
    monkeypatch.setattr(tc, "in_trading_window", lambda now=None: False)
    assert asyncio.run(bp.check_and_dispatch(app)) == []
    with app._test_factory() as db:
        assert db.query(AlertEvent).count() == 0
    assert counters["brief"] == counters["paper"] == counters["direct_io"] == 0


# ---------------------------------------------------------------- 条件化审计 A2（§6.25）


def test_evaluate_20cm_halfway_gap_not_limit_zone():
    """创业板 20cm 股 +12%（半程，未触 19% 涨停区下沿）不得被判「触涨停区」。"""
    quotes = {"300001": _quote("300001", price=11.2, change_pct=12.0),
              "600004": _quote("600004", price=11.2, change_pct=9.8)}
    items = [_item("300001", buy_range={"low": 9.0, "high": 11.5}),
             _item("600004", buy_range={"low": 9.0, "high": 11.5})]
    hits, skips = asyncio.run(_ev(items, gate_stand=False, quotes=quotes))
    # 300001 +12% < 19（20cm×0.95）→ 命中；600004 +9.8% ≥ 9.5（主板×0.95）→ skip（原语义不变）
    assert [h["item"]["symbol"] for h in hits] == ["300001"]
    assert [s["symbol"] for s in skips] == ["600004"]
    assert "涨停区" in skips[0]["reason"]
