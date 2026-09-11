"""影子持仓（P0-B）测试：plan_buys 纯函数 + engine scope 隔离 + run_morning 编排。"""
import sys

sys.path.insert(0, ".")

import asyncio
import json

from app.paper.engine import PaperTradingEngine
from app.core.db import get_session_factory
from app.picks.shadow import ShadowRunner, plan_buys

from app.schemas.market import Quote

FIXED_QUOTE = Quote(symbol="600001", price=10.0, open=10.0, limit_up_price=11.0, limit_down_price=9.0, source="t")


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


def test_plan_buys_missing_price_rejected():
    items = [{"symbol": "600001", "name": "x", "state": "normal", "gap_pct": 0.0, "reason": "r"}]
    plan = plan_buys(items, cash=100_000.0)
    assert plan["plans"] == []
    assert any(s["state"] == "unknown" for s in plan["skipped"])


# ---------------------------------------------------------------- engine scope 隔离


def _reset_paper_tables():
    from app.core.db import get_engine
    from app.models.paper import PaperAccount, PaperOrder, PaperPosition
    from app.models.watchlist import Base

    Base.metadata.create_all(get_engine())
    sf = get_session_factory()
    with sf() as db:
        for m in (PaperAccount, PaperOrder, PaperPosition):
            db.query(m).delete()
        db.commit()


def _engine(quote_map, scope):
    async def quote_fn(symbol):
        q = quote_map.get(symbol)
        if q is not None:
            return q.model_copy(update={"symbol": symbol})
        return None

    return PaperTradingEngine(get_session_factory(), quote_fn, scope=scope)


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
    from datetime import date as _date

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

    snap = collect_trading(sf, _date.today(), price_map={"600001": 11.0, "600002": 31.0})
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


from types import SimpleNamespace  # noqa: E402


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
    # 幂等：同日再跑 skipped
    summary2 = asyncio.run(runner.run_morning(_hub(quotes), _gate(_gate_items())))
    assert summary2["skipped"] == "already_executed"


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
