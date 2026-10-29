"""影子持仓（P0-B）测试：plan_buys 纯函数 + engine scope 隔离 + run_morning 编排。"""
import sys

sys.path.insert(0, ".")

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
import pytest

from app.paper.engine import PaperTradingEngine
from app.core.db import get_session_factory
from app.picks.shadow import ShadowRunner, plan_buys

from app.schemas.market import Quote
from app.core.bjtime import BJ_TZ

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=BJ_TZ)


@pytest.fixture(autouse=True)
def execution_clock(monkeypatch):
    monkeypatch.setattr("app.paper.engine.beijing_now", lambda: NOW)
    monkeypatch.setattr("app.paper.engine._today", lambda: NOW.strftime("%Y%m%d"))
    monkeypatch.setattr("app.paper.execution_guard.known_trade_days", lambda: [NOW.date()])

FIXED_QUOTE = Quote(symbol="600001", price=10.0, open=10.0, limit_up_price=11.0, limit_down_price=9.0,
                    source="t", data_timestamp=NOW)


def _gate_items():
    return [
        {"symbol": "600001", "name": "可买", "state": "normal", "gap_pct": 1.0, "reason": "r",
         "open_price": 10.0},
        {"symbol": "600002", "name": "一字", "state": "blocked", "gap_pct": 9.97, "reason": "r"},
        {"symbol": "600003", "name": "观察", "state": "observe", "gap_pct": 6.0, "reason": "r"},
        {"symbol": "600004", "name": "异常", "state": "anomaly", "gap_pct": -6.0, "reason": "r"},
        {"symbol": "600005", "name": "未知", "state": "unknown", "gap_pct": None, "reason": "r"},
    ]


# ---------------------------------------------------------------- plan_buys 纯函数


def test_plan_buys_only_normal_bucket():
    plan = plan_buys(_gate_items(), cash=1_000_000.0)
    assert [p["symbol"] for p in plan["plans"]] == ["600001"]
    skipped_states = {s["symbol"]: s["state"] for s in plan["skipped"]}
    assert skipped_states == {
        "600002": "blocked", "600003": "observe", "600004": "anomaly", "600005": "unknown",
    }


def test_plan_buys_round_lots_and_budget():
    items = [
        {"symbol": "60000%d" % i, "name": "x", "state": "normal", "gap_pct": 0.0,
         "reason": "r", "open_price": 30.0}
        for i in range(1, 4)
    ]
    plan = plan_buys(items, cash=100_000.0)
    # 每只预算 100000*0.99/3 = 33000 → 30 元/股 → 1100 股 → 整手 1100
    assert all(p["qty"] % 100 == 0 for p in plan["plans"])
    assert plan["plans"][0]["qty"] == 1100


def test_plan_buys_observation_only_skipped():
    items = [{"symbol": "600001", "name": "x", "state": "normal", "gap_pct": 0.0,
              "reason": "r", "open_price": 10.0, "observation_only": True}]
    plan = plan_buys(items, cash=100_000.0)
    assert plan["plans"] == []
    assert plan["skipped"][0]["symbol"] == "600001"
    assert plan["skipped"][0]["observation_only"] is True
    assert plan["skipped"][0]["gate_reason"] == "r"
    assert "仅观察" in plan["skipped"][0]["reason"]


def test_plan_buys_observation_constraint_does_not_hide_a_gate_rejection():
    item = {"symbol": "600001", "state": "blocked", "reason": "竞价接近涨停，禁买",
            "observation_only": True, "open_price": 10.0}
    plan = plan_buys([item], cash=100_000.0)
    assert plan["plans"] == []
    assert plan["skipped"][0]["state"] == "blocked"
    assert plan["skipped"][0]["reason"] == item["reason"]


def test_plan_buys_missing_price_rejected():
    items = [{"symbol": "600001", "name": "x", "state": "normal", "gap_pct": 0.0, "reason": "r"}]
    plan = plan_buys(items, cash=100_000.0)
    assert plan["plans"] == []
    assert any(s["state"] == "unknown" for s in plan["skipped"])


# ---------------------------------------------------------------- engine scope 隔离


def _reset_paper_tables():
    from app.core.db import get_engine
    from app.models.paper import PaperAccount, PaperOrder, PaperPosition
    from app.models.notification_outbox import BuyPointConsumption
    from app.models.alert import AlertEvent, AlertRule
    from app.models.watchlist import Base

    # Matching inspects durable ownership even when this fixture has no owner.
    # Register its real table before create_all instead of borrowing an earlier import.
    Base.metadata.create_all(get_engine(), tables=[AlertRule.__table__, AlertEvent.__table__])
    Base.metadata.create_all(get_engine())
    sf = get_session_factory()
    with sf() as db:
        for m in (BuyPointConsumption, PaperAccount, PaperOrder, PaperPosition):
            db.query(m).delete()
        db.commit()


def _engine(quote_map, scope):
    async def quote_fn(symbol):
        q = quote_map.get(symbol)
        if q is not None:
            return q.model_copy(update={"symbol": symbol})
        return None

    risk = SimpleNamespace(hub=SimpleNamespace(get_quotes=lambda: list(quote_map.values())),
                           check_order=lambda **_: {"allowed": True})
    return PaperTradingEngine(get_session_factory(), quote_fn, scope=scope, risk_engine=risk)


def test_engine_scope_isolation():
    _reset_paper_tables()
    q = FIXED_QUOTE
    main = _engine({"600001": q}, "main")
    shadow = _engine(
        {"600001": q.model_copy(update={"price": 20.0, "open": 20.0,
                                        "limit_up_price": 22.0, "limit_down_price": 18.0})},
        "shadow",
    )

    o1 = asyncio.run(main.place_order("600001", "buy", 10.0, 100))
    assert o1.status == "filled", o1.reason
    o2 = asyncio.run(shadow.place_order("600001", "buy", 20.0, 200))
    assert o2.status == "filled", o2.reason

    # 持仓互不可见：main 只看到自己的 100 股 @10，shadow 200 股 @20
    assert main.positions_with_pnl({})[0]["quantity"] == 100
    assert shadow.positions_with_pnl({})[0]["quantity"] == 200
    assert main.positions_with_pnl({})[0]["cost_price"] == 10.0
    assert shadow.positions_with_pnl({})[0]["cost_price"] == 20.0
    # 账户独立
    assert main.ensure_account().cash < 1_000_000
    assert shadow.ensure_account().cash < 1_000_000
    assert main.ensure_account().id != shadow.ensure_account().id


def test_engine_sell_scope_isolated():
    """S1-1 回归：main / shadow 同持一票时，卖出一方不得触碰另一方，也不得抛异常。

    历史缺陷：卖出路径只按 symbol 查持仓（模型层却是 scope+symbol 唯一）
    ⇒ `one_or_none()` 抛 MultipleResultsFound，被常驻循环吞掉 ⇒ 持仓卡死。
    """
    _reset_paper_tables()
    from app.models.paper import PaperPosition

    main = _engine({"600001": FIXED_QUOTE}, "main")
    shadow = _engine({"600001": FIXED_QUOTE.model_copy(update={"price": 20.0})}, "shadow")
    with main._sf() as db:
        db.add(PaperPosition(scope="main", symbol="600001", quantity=100,
                             frozen_today=0, cost_price=10.0, buy_date="20260901"))
        db.add(PaperPosition(scope="shadow", symbol="600001", quantity=200,
                             frozen_today=0, cost_price=20.0, buy_date="20260901"))
        db.commit()
    shadow_cash_before = shadow.ensure_account().cash
    main_cash_before = main.ensure_account().cash

    o = asyncio.run(main.place_order("600001", "sell", 10.0, 100))
    assert o.status == "filled", o.reason
    # main 清仓；shadow 的 200 股与资金分毫未动
    assert main.positions_with_pnl({}) == []
    assert shadow.positions_with_pnl({})[0]["quantity"] == 200
    assert shadow.ensure_account().cash == shadow_cash_before
    assert main.ensure_account().cash > main_cash_before


def test_fill_sell_rejects_oversell_and_never_goes_negative():
    """S1-1 回归：`match_pending` 直接调用 `_fill_sell`，必须二次校验可卖量。

    同一批可用量被两次挂单卖出时，旧实现会把 quantity 扣成负数并静默 delete
    持仓（卖出款已入账）——等于凭空多出一笔卖出。
    """
    _reset_paper_tables()
    from app.models.paper import PaperOrder, PaperPosition

    eng = _engine({"600001": FIXED_QUOTE}, "main")
    with eng._sf() as db:
        db.add(PaperPosition(scope="main", symbol="600001", quantity=100,
                             frozen_today=0, cost_price=10.0, buy_date="20260901"))
        db.commit()
    # 限价 11 高于现价 10 → 两张挂单（挂单不冻结股份，两者都能过下单校验）
    o1 = asyncio.run(eng.place_order("600001", "sell", 11.0, 100))
    o2 = asyncio.run(eng.place_order("600001", "sell", 11.0, 100))
    assert (o1.status, o2.status) == ("pending", "pending")

    up = _engine({"600001": FIXED_QUOTE.model_copy(update={"price": 12.0, "limit_up_price": 13.2})}, "main")
    assert asyncio.run(up.match_pending()) == 0
    with up._sf() as db:
        st = {o.id: o.status for o in db.query(PaperOrder).all()}
        rej = [o.reason or "" for o in db.query(PaperOrder).filter(PaperOrder.status == "rejected").all()]
    assert st[o1.id] == "filled"
    assert st[o2.id] == "rejected", "超卖挂单必须被拒，而不是静默扣负"
    assert any("可卖数量不足" in r for r in rej)
    assert up.positions_with_pnl({}) == []
    # 只成交一次：资金约 +1194 元（1200 − 费用 5.61），不是两笔
    cash = up.ensure_account().cash
    assert 1_001_000 < cash < 1_002_000, cash


def test_engine_match_pending_scope_isolated():
    _reset_paper_tables()
    q_low = FIXED_QUOTE.model_copy(update={"price": 9.0})
    main = _engine({"600001": FIXED_QUOTE}, "main")
    shadow = _engine({"600001": q_low}, "shadow")
    # main 挂单 9.5（限价低于现价 10 → pending）；shadow 挂同价但其现价 9 → 成交
    o_main = asyncio.run(main.place_order("600001", "buy", 9.5, 100))
    assert o_main.status == "pending"
    o_shadow = asyncio.run(shadow.place_order("600001", "buy", 9.5, 100))
    assert o_shadow.status == "filled"
    left = asyncio.run(main.match_pending())
    assert left == 1  # main 的挂单不被 shadow 价成交（现价 10 > 9.5），仍挂着


def test_collect_trading_excludes_shadow_scope():
    """S1-1 同族回归：复盘 `trades` 段只描述 main 账户，影子持仓/委托不得混入。

    影子账户本身在 ShadowSnapshot 独立采集；此前 trades 三处查询均无 scope，
    影子数据会污染「账户与盈亏 / 操作评估」两个维度且从报表上看不出来。
    """
    _reset_paper_tables()
    from app.core.bjtime import beijing_today
    from app.core.db import get_session_factory
    from app.models.paper import PaperAccount, PaperOrder, PaperPosition
    from app.review.collector import collect_trading

    sf = get_session_factory()
    with sf() as db:
        db.add(PaperAccount(scope="main", cash=1_000.0, initial_cash=1_000.0))
        db.add(PaperAccount(scope="shadow", cash=9_000.0, initial_cash=9_000.0))
        db.add(PaperPosition(scope="main", symbol="600001", quantity=100,
                             cost_price=10.0, buy_date="20260901"))
        db.add(PaperPosition(scope="shadow", symbol="600002", quantity=900,
                             cost_price=30.0, buy_date="20260901"))
        db.add(PaperOrder(scope="main", symbol="600001", side="buy", price=10.0,
                          quantity=100, status="filled"))
        db.add(PaperOrder(scope="shadow", symbol="600002", side="buy", price=30.0,
                          quantity=900, status="filled"))
        db.commit()

    snap = collect_trading(sf, beijing_today(), price_map={"600001": 11.0, "600002": 31.0})
    assert snap.account["cash"] == 1_000.0
    assert [p.symbol for p in snap.positions] == ["600001"]
    assert {o.symbol for o in snap.orders} <= {"600001"}


# ---------------------------------------------------------------- ShadowRunner 编排

def _runner(quote_map, scope="shadow"):
    engine = _engine(quote_map, scope)
    from app.core.db import get_session_factory

    return ShadowRunner(engine, get_session_factory())


def _hub(quote_map):
    async def get_quote(symbol):
        q = quote_map.get(symbol)
        return q.model_copy(update={"symbol": symbol}) if q else None

    return SimpleNamespace(provider=SimpleNamespace(get_quote=get_quote))


def _gate(gate_items):
    return {
        "pick_date": "2026-09-03",
        "items": [dict(i) for i in gate_items],
        "summary": {"total": len(gate_items), "executable": 1},
        "caveats": [],
    }


def test_run_morning_buys_normal_and_skips_gate(tmp_path, monkeypatch):
    _reset_paper_tables()
    from app.picks import shadow as shadow_mod

    monkeypatch.setattr(shadow_mod, "_shadow_dir", lambda: tmp_path)
    quotes = {
        "600001": FIXED_QUOTE,
        "600002": FIXED_QUOTE, "600003": FIXED_QUOTE,
        "600004": FIXED_QUOTE, "600005": FIXED_QUOTE,
    }
    runner = _runner(quotes)
    summary = asyncio.run(runner.run_morning(_hub(quotes), _gate(_gate_items())))
    bought = {b["symbol"]: b for b in summary["bought"]}
    assert bought["600001"]["status"] == "filled"
    # 闸门拦下的不产生订单
    assert "600002" not in bought and "600003" not in bought
    assert {s["symbol"] for s in summary["skipped"]} >= {"600002", "600003", "600004", "600005"}
    # 执行日志落盘
    day = summary["day"]
    log_row = json.loads((tmp_path / f"{day}.json").read_text(encoding="utf-8"))
    assert log_row["bought"][0]["symbol"] == "600001"
    original = (tmp_path / f"{day}.json").read_bytes()
    assert shadow_mod.persist_shadow_skip(day, shadow_mod.SKIP_WINDOW_CLOSED) is None
    assert (tmp_path / f"{day}.json").read_bytes() == original
    # 幂等：同日再跑 skipped
    summary2 = asyncio.run(runner.run_morning(_hub(quotes), _gate(_gate_items())))
    assert summary2["skipped"] == "already_executed"


def test_run_morning_zero_orders_still_protects_its_nonempty_skip_list(tmp_path, monkeypatch):
    _reset_paper_tables()
    from app.picks import shadow as shadow_mod

    monkeypatch.setattr(shadow_mod, "_shadow_dir", lambda: tmp_path)
    runner = _runner({})
    gate = _gate([{"symbol": "600002", "state": "blocked", "reason": "竞价禁买"}])
    summary = asyncio.run(runner.run_morning(_hub({}), gate))
    assert summary["bought"] == summary["sold"] == []
    assert len(summary["skipped"]) == 1 and summary["executed_at"]
    path = tmp_path / f"{summary['day']}.json"
    original = path.read_bytes()
    assert shadow_mod.persist_shadow_skip(summary["day"], shadow_mod.SKIP_WINDOW_CLOSED) is None
    assert path.read_bytes() == original


def test_run_morning_sells_yesterday_position(tmp_path, monkeypatch):
    _reset_paper_tables()
    from app.picks import shadow as shadow_mod

    monkeypatch.setattr(shadow_mod, "_shadow_dir", lambda: tmp_path)
    quotes = {"600001": FIXED_QUOTE.model_copy(update={"price": 11.0, "open": 11.0})}
    runner = _runner(quotes)
    # 预置昨日持仓（frozen 清零=可卖）
    from app.models.paper import PaperPosition

    with runner.engine._sf() as db:
        db.add(PaperPosition(scope="shadow", symbol="600001", quantity=100,
                             frozen_today=0, cost_price=10.0, buy_date="20260903"))
        db.commit()
    # gate 无 normal 票 → 只卖不买
    gate = _gate([{"symbol": "600009", "name": "x", "state": "blocked", "gap_pct": 9.9, "reason": "r"}])
    summary = asyncio.run(runner.run_morning(_hub(quotes), gate))
    assert summary["sold"][0]["symbol"] == "600001"
    assert summary["sold"][0]["status"] == "filled"
    assert runner.engine.positions_with_pnl({}) == []
    assert summary["bought"] == []


@pytest.fixture
def settlement_runner(tmp_path, monkeypatch):
    """Real settlement/matching against a file DB and quotes owned by this test."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.paper import PaperAccount, PaperOrder, PaperPosition
    from app.picks import shadow as shadow_mod

    db_engine = create_engine(f"sqlite:///{tmp_path / 'shadow.db'}",
                              connect_args={"check_same_thread": False})
    for model in (PaperAccount, PaperOrder, PaperPosition):
        model.__table__.create(db_engine)
    sf = sessionmaker(bind=db_engine, expire_on_commit=False)
    quotes, reads = {}, []

    async def quote(symbol):
        reads.append(symbol)
        return quotes.get(symbol)

    async def days():
        return ["20260928", "20260929"]

    engine = PaperTradingEngine(sf, quote, trading_days_fn=days, scope="shadow")
    runner = ShadowRunner(engine, sf)
    monkeypatch.setattr(shadow_mod, "_shadow_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(shadow_mod, "beijing_now", lambda: NOW)
    yield SimpleNamespace(runner=runner, sf=sf, quotes=quotes, reads=reads,
                          hub=SimpleNamespace(provider=SimpleNamespace(get_quote=quote)),
                          logs=tmp_path / "logs")
    db_engine.dispose()


def test_run_morning_settles_old_shadow_positions_before_reading_available(settlement_runner):
    from app.models.paper import PaperAccount, PaperOrder, PaperPosition

    env = settlement_runner
    old_symbols = [f"60000{i}" for i in range(1, 6)]
    for symbol in old_symbols:
        env.quotes[symbol] = FIXED_QUOTE.model_copy(update={"symbol": symbol})
    with env.sf() as db:
        db.add(PaperAccount(scope="shadow", cash=100_000, initial_cash=107_000))
        db.add(PaperAccount(scope="main", cash=50_000, initial_cash=51_000))
        for symbol in old_symbols:
            db.add(PaperPosition(scope="shadow", symbol=symbol, quantity=100,
                                 frozen_today=100, cost_price=10, buy_date="20260922"))
        for scope in ("main", "hunting_shadow"):
            db.add(PaperPosition(scope=scope, symbol="600001", quantity=100,
                                 frozen_today=100, cost_price=10, buy_date="20260922"))
        db.add(PaperPosition(scope="shadow", symbol="600006", quantity=100,
                             frozen_today=100, cost_price=10, buy_date="20260929"))
        db.add(PaperPosition(scope="shadow", symbol="600007", quantity=100,
                             frozen_today=100, cost_price=10, buy_date=""))
        db.commit()
    summary = asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    assert {row["symbol"] for row in summary["sold"]} == set(old_symbols)
    assert all(row["status"] == "filled" for row in summary["sold"])
    assert {row["symbol"] for row in summary["sell_failed"]} == {"600006", "600007"}
    with env.sf() as db:
        positions = {(row.scope, row.symbol): row for row in db.query(PaperPosition).all()}
        assert set(positions) == {("main", "600001"), ("hunting_shadow", "600001"),
                                 ("shadow", "600006"), ("shadow", "600007")}
        assert all(row.quantity == row.frozen_today == 100 for row in positions.values())
        assert db.query(PaperOrder).count() == 5
        assert {row.scope for row in db.query(PaperOrder).all()} == {"shadow"}
        assert db.query(PaperAccount).filter_by(scope="main").one().cash == 50_000


def test_shadow_keeps_completed_sale_when_later_quote_fails(settlement_runner):
    from app.models.paper import PaperOrder, PaperPosition

    env = settlement_runner
    env.quotes["600001"] = FIXED_QUOTE
    with env.sf() as db:
        for symbol in ("600001", "600002"):
            db.add(PaperPosition(scope="shadow", symbol=symbol, quantity=100,
                                 frozen_today=100, cost_price=10, buy_date="20260922"))
        db.commit()

    async def quote(symbol):
        if symbol == "600002":
            raise TimeoutError("quote unavailable")
        return env.quotes.get(symbol)

    hub = SimpleNamespace(provider=SimpleNamespace(get_quote=quote))
    first = asyncio.run(env.runner.run_morning(hub, _gate([])))
    assert [(row["symbol"], row["status"]) for row in first["sold"]] == [("600001", "filled")]
    assert first["sell_failed"][0]["symbol"] == "600002"
    assert "quote unavailable" in first["sell_failed"][0]["reason"]
    second = asyncio.run(env.runner.run_morning(hub, _gate([])))
    saved = json.loads((env.logs / f"{first['day']}.json").read_text())
    assert saved["sold"] == first["sold"] == second["sold"]
    with env.sf() as db:
        assert db.query(PaperOrder).count() == 1
        assert db.query(PaperPosition).one().symbol == "600002"


@pytest.mark.parametrize("quote_state", ["missing", "limit_down"])
def test_shadow_settlement_does_not_bypass_missing_quote_or_limit_down(settlement_runner, quote_state):
    from app.models.paper import PaperPosition

    env = settlement_runner
    with env.sf() as db:
        db.add(PaperPosition(scope="shadow", symbol="600001", quantity=100,
                             frozen_today=100, cost_price=10, buy_date="20260922"))
        db.commit()
    if quote_state == "limit_down":
        env.quotes["600001"] = FIXED_QUOTE.model_copy(update={"price": 9.0})
    summary = asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    if quote_state == "missing":
        assert summary["sold"] == []
        assert summary["sell_failed"][0]["reason"] == "停牌或无行情"
    else:
        assert summary["sold"][0]["status"] == "rejected"
        assert "跌停" in summary["sold"][0]["reason"]
    with env.sf() as db:
        position = db.query(PaperPosition).one()
        assert position.quantity == 100 and position.frozen_today == 0


def test_shadow_settlement_failure_does_not_submit_and_can_retry(settlement_runner, monkeypatch):
    from unittest.mock import AsyncMock
    from app.models.paper import PaperOrder, PaperPosition

    env = settlement_runner
    env.quotes["600001"] = FIXED_QUOTE
    with env.sf() as db:
        db.add(PaperPosition(scope="shadow", symbol="600001", quantity=100,
                             frozen_today=100, cost_price=10, buy_date="20260922"))
        db.commit()
    settle = env.runner.engine.settle_t1
    monkeypatch.setattr(env.runner.engine, "settle_t1", AsyncMock(side_effect=RuntimeError("test failure")))
    summary = asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    assert summary["skipped"] == "t1_settlement_unavailable"
    assert "RuntimeError" in summary["caveat"]
    assert "executed_at" not in summary
    assert env.reads == []
    path = env.logs / f"{summary['day']}.json"
    assert json.loads(path.read_text())["skipped_reason"] == "t1_settlement_unavailable"
    with env.sf() as db:
        assert db.query(PaperOrder).count() == 0
        assert db.query(PaperPosition).one().frozen_today == 100
    monkeypatch.setattr(env.runner.engine, "settle_t1", settle)
    retried = asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    assert retried["sold"][0]["status"] == "filled"


def test_shadow_observation_reason_survives_without_unused_open_quote(settlement_runner):
    env = settlement_runner
    gate = _gate([{"symbol": "600001", "state": "normal", "reason": "开盘溢价正常",
                   "observation_only": True}])
    summary = asyncio.run(env.runner.run_morning(env.hub, gate))
    assert summary["bought"] == [] and summary["sold"] == []
    assert env.reads == []
    assert summary["skipped"][0]["gate_reason"] == "开盘溢价正常"
    assert "仅观察" in summary["skipped"][0]["reason"]


def test_later_empty_beat_keeps_real_sell_and_allows_new_buy(settlement_runner):
    from app.models.paper import PaperOrder, PaperPosition

    env = settlement_runner
    env.quotes["600001"] = FIXED_QUOTE
    env.quotes["600002"] = FIXED_QUOTE.model_copy(update={"symbol": "600002"})
    with env.sf() as db:
        db.add(PaperPosition(scope="shadow", symbol="600001", quantity=100,
                             frozen_today=0, cost_price=10, buy_date="20260922"))
        db.commit()
    first = asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    assert first["sold"][0]["status"] == "filled"
    second = asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    saved = json.loads((env.logs / f"{first['day']}.json").read_text())
    assert len(saved["sold"]) == 1
    assert saved["sold"][0]["status"] == "filled"
    assert len(second["sold"]) == 1
    with env.sf() as db:
        assert db.query(PaperOrder).filter_by(side="sell").count() == 1
    later = asyncio.run(env.runner.run_morning(env.hub, _gate([
        {"symbol": "600002", "state": "normal", "reason": "开盘溢价正常"}])))
    assert later["bought"][0]["status"] == "filled"
    assert len(later["sold"]) == 1
    with env.sf() as db:
        assert db.query(PaperOrder).filter_by(side="sell").count() == 1
        assert db.query(PaperOrder).filter_by(side="buy").count() == 1


@pytest.mark.parametrize("failure", ["limit_down", "order_error"])
def test_repeated_sell_failures_survive_success_and_empty_beats(settlement_runner, monkeypatch, failure):
    from unittest.mock import AsyncMock
    from app.models.paper import PaperOrder, PaperPosition

    env = settlement_runner
    env.quotes["600001"] = FIXED_QUOTE.model_copy(update={"price": 9 if failure == "limit_down" else 10})
    with env.sf() as db:
        db.add(PaperPosition(scope="shadow", symbol="600001", quantity=100,
                             frozen_today=0, cost_price=10, buy_date="20260922"))
        db.commit()
    place_order = env.runner.engine.place_order
    if failure == "order_error":
        monkeypatch.setattr(env.runner.engine, "place_order", AsyncMock(side_effect=RuntimeError("isolated order failure")))
    for _ in range(2):
        asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    env.quotes["600001"] = FIXED_QUOTE
    monkeypatch.setattr(env.runner.engine, "place_order", place_order)
    asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    result = asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    saved = json.loads((env.logs / f"{result['day']}.json").read_text())
    assert sum(row["status"] == "filled" for row in saved["sold"]) == 1
    if failure == "limit_down":
        assert sum(row["status"] == "rejected" and "跌停" in row["reason"] for row in saved["sold"]) == 2
    else:
        assert [row["reason"] for row in saved["sell_failed"]] == ["isolated order failure"] * 2
    with env.sf() as db:
        assert db.query(PaperOrder).filter_by(status="filled").count() == 1


def test_settlement_failure_keeps_an_earlier_sell_receipt(settlement_runner, monkeypatch):
    from unittest.mock import AsyncMock
    from app.models.paper import PaperPosition

    env = settlement_runner
    env.quotes["600001"] = FIXED_QUOTE
    with env.sf() as db:
        db.add(PaperPosition(scope="shadow", symbol="600001", quantity=100,
                             frozen_today=0, cost_price=10, buy_date="20260922"))
        db.commit()
    first = asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    path = env.logs / f"{first['day']}.json"
    before = path.read_bytes()
    monkeypatch.setattr(env.runner.engine, "settle_t1", AsyncMock(side_effect=RuntimeError("isolated settlement failure")))
    failed = asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    assert failed["skipped"] == "t1_settlement_unavailable"
    assert path.read_bytes() == before


@pytest.mark.parametrize("original", ["{unreadable", json.dumps({"day": "2026-09-28", "sold": []}),
    json.dumps({"day": "2026-09-29", "executed_at": NOW.isoformat(), "sold": "unknown"})])
def test_shadow_preserves_unreadable_or_wrong_day_receipts(settlement_runner, original):
    env = settlement_runner
    env.logs.mkdir()
    path = env.logs / "2026-09-29.json"
    path.write_text(original)
    with pytest.raises(ValueError, match="preserving original"):
        asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    assert path.read_text() == original


def test_shadow_replace_failure_does_not_truncate_previous_receipts(settlement_runner, monkeypatch):
    from pathlib import Path

    env = settlement_runner
    env.logs.mkdir()
    path = env.logs / "2026-09-29.json"
    original = json.dumps({"day": "2026-09-29", "executed_at": NOW.isoformat(), "bought": [],
                           "sold": [{"symbol": "600001", "status": "rejected", "reason": "原拒绝"}],
                           "sell_failed": [], "skipped": []})
    path.write_text(original)

    def fail_replace(_source, _target):
        raise OSError("isolated replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="isolated replace failure"):
        asyncio.run(env.runner.run_morning(env.hub, _gate([])))
    assert path.read_text() == original


def test_nontrading_shadow_beat_never_settles_or_submits(settlement_runner, monkeypatch):
    from unittest.mock import AsyncMock
    from datetime import date
    from app.picks import shadow as shadow_mod
    from app.market import trade_calendar

    env = settlement_runner
    sunday = datetime(2026, 10, 4, 9, 30, tzinfo=BJ_TZ)
    monkeypatch.setattr(shadow_mod, "beijing_now", lambda: sunday)
    settle = AsyncMock(side_effect=AssertionError("nontrading settlement"))
    monkeypatch.setattr(env.runner.engine, "settle_t1", settle)

    async def beat():
        stop = asyncio.Event()

        async def days(_provider):
            stop.set()
            return [date(2026, 9, 30)]

        monkeypatch.setattr(trade_calendar, "trading_days", days)
        app = SimpleNamespace(state=SimpleNamespace(hub=env.hub, paper_shadow=env.runner))
        await shadow_mod.shadow_loop(app, stop)

    asyncio.run(beat())
    settle.assert_not_awaited()
    assert env.reads == []
    assert json.loads((env.logs / "2026-10-04.json").read_text())["skipped"] == "not_trading_day"


def test_collect_shadow_for_review(tmp_path, monkeypatch):
    _reset_paper_tables()
    from app.core.db import get_session_factory
    from datetime import date as _date

    from app.picks import shadow as shadow_mod

    monkeypatch.setattr(shadow_mod, "_shadow_dir", lambda: tmp_path)
    # 无日志无账户 → None
    assert shadow_mod.collect_shadow_for_review(get_session_factory(), _date(2026, 9, 3)) is None
    # 有日志 + 有账户
    (tmp_path / "2026-09-03.json").write_text(json.dumps({"day": "2026-09-03", "bought": []}), encoding="utf-8")
    eng = _engine({}, "shadow")
    eng.ensure_account()
    out = shadow_mod.collect_shadow_for_review(get_session_factory(), _date(2026, 9, 3))
    assert out["execution"]["day"] == "2026-09-03"
    assert out["account"]["total_return_pct"] == 0.0
