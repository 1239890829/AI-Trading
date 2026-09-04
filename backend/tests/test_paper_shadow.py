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
