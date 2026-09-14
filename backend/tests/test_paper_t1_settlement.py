"""R04 · T+1 的日期锚点与解冻（2026-09-14）。

为什么值得独立成文件：R04 不是一个函数写错，而是**解冻语义被拆成了两半**——
写入路径（`place_order`）会结算、只读路径（离场监护 / 持仓快照）不结算，
而 `available = quantity - frozen_today` 是**派生值**。
于是"这笔持仓今天能不能卖"在两条路径上给出不同答案：
下单路径放行、监护路径判"当日买入不可卖"并**静默跳过硬止损**。

本文件一律**不手改 `frozen_today` 来模拟结算**（R04 验收标准明确要求）：
所有用例都靠"推进今天 + 注入交易日历"驱动真实的 `settle_t1` / `_unfreeze`。
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import app.paper.engine as pe

#: 含**未来交易日**的日历 —— 这正是 R04 的复现条件：
#: 旧实现取「今天之后的第一个交易日」当解冻日，再判 `today >= 它`，永不成立。
CAL = ["20260910", "20260911", "20260914", "20260915", "20260916"]


def _mk_engine(tmp_path, *, days=CAL, name="t1.db", scope="main"):
    """真引擎 + 真 SQLite（tmp 文件）：T+1 是状态机，桩掉库就测不到状态迁移。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.watchlist import Base
    from app.paper.engine import PaperTradingEngine

    db_engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(db_engine)
    sf = sessionmaker(bind=db_engine)

    async def quote_fn(symbol):  # pragma: no cover - 本文件不解冻外的行情需求
        return None

    async def days_fn():
        if isinstance(days, Exception):
            raise days
        return list(days)

    engine = PaperTradingEngine(sf, quote_fn, None if days is None else days_fn, scope=scope)
    return engine, sf


def _seed(sf, *, buy_date: str, qty: int = 100, frozen: int | None = None,
          cost: float = 10.0, symbol: str = "600519", scope: str = "main") -> None:
    from app.models.paper import PaperPosition

    with sf() as db:
        db.add(PaperPosition(scope=scope, symbol=symbol, quantity=qty,
                             frozen_today=qty if frozen is None else frozen,
                             cost_price=cost, buy_date=buy_date))
        db.commit()


def _snap(sf, symbol: str = "600519", scope: str = "main") -> dict:
    from app.models.paper import PaperPosition

    with sf() as db:
        p = db.query(PaperPosition).filter(
            PaperPosition.scope == scope, PaperPosition.symbol == symbol
        ).one()
        return {"quantity": p.quantity, "frozen": p.frozen_today,
                "available": p.available, "buy_date": p.buy_date}


@pytest.fixture(autouse=True)
def _reset_fallback_log():
    pe._T1_FALLBACK_LOGGED.clear()
    yield
    pe._T1_FALLBACK_LOGGED.clear()


# ---------------------------------------------------------------- 锚点（日历可用）


def test_releases_yesterday_but_not_today_even_with_future_calendar(tmp_path, monkeypatch):
    """R04 主判据：日历**含未来交易日**时，昨日买入必须解冻、今日买入必须仍冻结。

    旧实现在此必然失败：`_next_trading_day()` 取到 20260915（未来），
    `today(20260914) >= 20260915` 恒 False ⇒ **昨日仓位永不解冻**。
    """
    engine, sf = _mk_engine(tmp_path)
    monkeypatch.setattr(pe, "_today", lambda: "20260914")
    _seed(sf, symbol="600519", buy_date="20260911")   # 上周五买入
    _seed(sf, symbol="600000", buy_date="20260914")   # 今日买入

    assert asyncio.run(engine.settle_t1()) == 1
    assert _snap(sf, "600519")["frozen"] == 0
    assert _snap(sf, "600519")["available"] == 100
    assert _snap(sf, "600000")["frozen"] == 100, "同日买入绝不解冻（T+1 红线）"


def test_holiday_gap_uses_calendar_not_natural_day(tmp_path, monkeypatch):
    """节假日：买入日后的下一个**交易日**才解冻。

    交易日历 = 09-30 与 10-09（10-01~10-08 长假），买入 09-30：
    - 10-01（假期中）解冻**不得**发生（日历里 10-01 不是交易日）；
    - 10-09（节后首个交易日）必须解冻。
    """
    days = ["20260930", "20261009"]
    engine, sf = _mk_engine(tmp_path, days=days)
    _seed(sf, buy_date="20260930")

    monkeypatch.setattr(pe, "_today", lambda: "20261001")
    assert asyncio.run(engine.settle_t1()) == 0, "长假中不得解冻"
    assert _snap(sf)["frozen"] == 100

    monkeypatch.setattr(pe, "_today", lambda: "20261009")
    assert asyncio.run(engine.settle_t1()) == 1
    assert _snap(sf)["frozen"] == 0


def test_friday_to_monday_releases_on_monday(tmp_path, monkeypatch):
    """周五买入 → 周一解冻（最常被引用的 T+1 场景）。"""
    engine, sf = _mk_engine(tmp_path)
    monkeypatch.setattr(pe, "_today", lambda: "20260911")  # 周五
    _seed(sf, buy_date="20260911")
    assert asyncio.run(engine.settle_t1()) == 0

    monkeypatch.setattr(pe, "_today", lambda: "20260914")  # 下周一
    assert asyncio.run(engine.settle_t1()) == 1
    assert _snap(sf)["available"] == 100


def test_calendar_dates_are_accepted_in_both_real_shapes(tmp_path, monkeypatch):
    """日历两种真实来源的**形态**都要能吃：YYYYMMDD 字符串（ths）与 `date`（trade_calendar）。

    旧实现写 `d > today`（today 是字符串），传 `date` 时抛 TypeError 并被
    `except Exception` 吞成"日历不可用" ⇒ **静默**退回自然日口径。见 `_as_ymd` 注释。
    """
    from datetime import date

    engine, sf = _mk_engine(tmp_path, days=[date(2026, 9, 11), date(2026, 9, 14)])
    monkeypatch.setattr(pe, "_today", lambda: "20260914")
    _seed(sf, buy_date="20260911", frozen=100)

    assert asyncio.run(engine.settle_t1()) == 1
    assert _snap(sf)["frozen"] == 0

    # 反向：ISO 形态（"2026-09-14"）同样要能识别
    engine2, sf2 = _mk_engine(tmp_path, days=["2026-09-11", "2026-09-14"], name="t1b.db")
    _seed(sf2, buy_date="20260911", frozen=100)
    assert asyncio.run(engine2.settle_t1()) == 1


# ---------------------------------------------------------------- 显式降级（日历不可用）


def test_calendar_unavailable_degrades_to_natural_day_with_warning(tmp_path, monkeypatch, caplog):
    """日历不可用 → **显式**降级为自然日口径并告警；同日买入仍不解冻（红线不受影响）。"""
    engine, sf = _mk_engine(tmp_path, days=RuntimeError("ths calendar down"))
    _seed(sf, symbol="600519", buy_date="20260911")
    _seed(sf, symbol="600000", buy_date="20260914")
    monkeypatch.setattr(pe, "_today", lambda: "20260914")

    with caplog.at_level(logging.WARNING, logger="app.paper.engine"):
        assert asyncio.run(engine.settle_t1()) == 1
    assert _snap(sf, "600519")["frozen"] == 0
    assert _snap(sf, "600000")["frozen"] == 100
    assert "降级为自然日口径" in caplog.text, "降级必须可见（不能静默改口径）"


def test_missing_calendar_injection_also_warns(tmp_path, monkeypatch, caplog):
    """完全未注入日历（`trading_days_fn=None`）同样按降级告警 —— 生产默认态。"""
    engine, sf = _mk_engine(tmp_path, days=None)
    _seed(sf, buy_date="20260911")
    monkeypatch.setattr(pe, "_today", lambda: "20260914")

    with caplog.at_level(logging.WARNING, logger="app.paper.engine"):
        assert asyncio.run(engine.settle_t1()) == 1
    assert "降级为自然日口径" in caplog.text


def test_empty_buy_date_is_never_unlocked(tmp_path, monkeypatch, caplog):
    """买入日缺失 ⇒ 不以无锚点的日期解冻（保守），仅计数告警。"""
    engine, sf = _mk_engine(tmp_path)
    _seed(sf, buy_date="")
    monkeypatch.setattr(pe, "_today", lambda: "20260914")

    with caplog.at_level(logging.WARNING, logger="app.paper.engine"):
        assert asyncio.run(engine.settle_t1()) == 0
    assert _snap(sf)["frozen"] == 100
    assert "无有效买入日" in caplog.text


# ---------------------------------------------------------------- 与下单/监护的合流


def test_add_position_keeps_yesterday_qty_sellable(tmp_path, monkeypatch):
    """连续加仓：昨日 100 股在今日仍可卖，今日新增的 100 股才冻结。

    判据是 `frozen == 100`（不是 200）—— 旧实现把 `frozen_today += qty` 与
    `buy_date = today` 一起写，两批次被混成一个"今天买的 200 股"。
    """
    engine, sf = _mk_engine(tmp_path)
    monkeypatch.setattr(pe, "_today", lambda: "20260914")
    _seed(sf, qty=100, frozen=100, buy_date="20260911")

    async def quote_fn(symbol):
        from app.schemas.market import Quote

        return Quote(symbol=symbol, price=10.0, limit_up_price=11.0,
                     limit_down_price=9.0, source="t")

    engine._quote_fn = quote_fn
    order = asyncio.run(engine.place_order("600519", "buy", 10.0, 100))
    assert order.status == "filled", getattr(order, "reason", None)

    snap = _snap(sf)
    assert snap["quantity"] == 200
    assert snap["frozen"] == 100, "昨日批次不得被今日加仓重新冻结"
    assert snap["available"] == 100


def test_exit_monitor_sells_yesterday_position_without_manual_settlement(tmp_path, monkeypatch):
    """**R04 的终点判据**：昨日买入的持仓触发止损时，监护必须真的下单卖出。

    旧行为：监护只读 `available`，而解冻只在下单路径发生 ⇒ `available` 恒 0 ⇒
    走「T+1 当日买入不可卖——下一交易日自动执行」分支，**硬止损被静默跳过**
    （且因为不会再有下单，它会一轮一轮跳下去）。
    """
    import app.picks.exit_engine as ee

    engine, sf = _mk_engine(tmp_path)
    monkeypatch.setattr(pe, "_today", lambda: "20260914")
    _seed(sf, qty=100, frozen=100, cost=10.0, buy_date="20260911")

    async def quote_fn(symbol):
        from app.schemas.market import Quote

        return Quote(symbol=symbol, price=9.0, limit_up_price=11.0,
                     limit_down_price=8.0, source="t")

    engine._quote_fn = quote_fn

    snap = type("SS", (), {"snapshot": [
        {"symbol": "600519", "name": "甲", "price": 9.0, "change_pct": -10.0},
    ]})()
    app = type("A", (), {"state": type("S", (), {
        "paper": engine, "snapshot_service": snap,
    })()})()

    plan = {"peaks": {"600519": 10.0}, "decisions": [], "exits": []}
    monkeypatch.setattr(ee, "load_plan", lambda: plan)
    monkeypatch.setattr(ee, "save_plan", lambda p: None)
    monkeypatch.setattr(ee, "_picks_combos", lambda: {})
    monkeypatch.setattr(ee, "_real_positions", lambda: {})
    notified: list[tuple] = []
    monkeypatch.setattr(ee, "_notify", lambda *a, **k: notified.append((a, k)))
    ee._NOTIFIED.clear()
    ee._PAPER_READ.update(state="unknown", failures=0)
    ee._REAL_READ.update(state="empty", failures=0)

    fired = asyncio.run(ee.evaluate_once(app))

    assert any(f["action"] == "exit" for f in fired), fired
    assert not any(f["action"] == "exit_deferred" for f in fired), (
        "昨日买入不是「当日买入」，不得走递延分支"
    )
    # 生产在 `quantity <= 0` 时**删行**（paper/engine.py:558-559）⇒ 清仓的判据是「行消失」，
    # 而不是 quantity==0（用 quantity 判会 NoResultFound，那是在测一个不存在的中间态）。
    from app.models.paper import PaperPosition

    with sf() as db:
        remaining = db.query(PaperPosition).filter(
            PaperPosition.scope == "main", PaperPosition.symbol == "600519"
        ).count()
    assert remaining == 0, "止损必须真的清仓"
    assert plan["exits"] and plan["exits"][0]["qty"] == 100


def test_positions_endpoint_settles_before_reading(tmp_path, monkeypatch):
    """**读路径的判断面**：`_positions_with_live` 取快照前必须先结算。

    前一条用例钉住"监护会卖"，这一条钉住"界面看到的可卖量也是真的"。
    两者是同一个缺陷的两个观测面：`available` 是派生值，**只修写下单路径**
    会让监护（读路径 ①）与界面（读路径 ②）各自停在旧冻结态。
    """
    import app.api.routes.paper as pr

    engine, sf = _mk_engine(tmp_path)
    monkeypatch.setattr(pe, "_today", lambda: "20260914")
    _seed(sf, qty=100, frozen=100, cost=10.0, buy_date="20260911")

    hub = type("H", (), {"get_quotes": staticmethod(lambda: [])})()
    request = type("R", (), {"app": type("A", (), {"state": type("S", (), {
        "paper": engine, "hub": hub,
    })()})()})()

    _, positions = asyncio.run(pr._positions_with_live(request))

    assert positions and positions[0]["available"] == 100, (
        "读快照前未结算 ⇒ available 恒 0，界面显示成「当日买入不可卖」"
    )
    assert _snap(sf)["frozen"] == 0, "结算必须落库，不能只在返回值里修"


def test_read_path_settlement_failure_does_not_block_positions(tmp_path, monkeypatch):
    """结算失败**不阻断读**（但必须留痕）——读持仓是只读操作，不得因结算异常整页报错。"""
    import app.api.routes.paper as pr

    engine, sf = _mk_engine(tmp_path)
    _seed(sf, qty=100, frozen=100, cost=10.0, buy_date="20260911")

    async def boom():
        raise RuntimeError("db locked")

    monkeypatch.setattr(engine, "settle_t1", boom)

    hub = type("H", (), {"get_quotes": staticmethod(lambda: [])})()
    request = type("R", (), {"app": type("A", (), {"state": type("S", (), {
        "paper": engine, "hub": hub,
    })()})()})()

    _, positions = asyncio.run(pr._positions_with_live(request))

    assert positions and positions[0]["symbol"] == "600519"
    assert positions[0]["available"] == 0, "结算失败时用旧冻结态（保守，不是放行）"


def test_settlement_survives_engine_restart(tmp_path, monkeypatch):
    """进程重启：锚点来自**持久化状态**（buy_date + 日历+今天），不靠进程内记忆。"""
    engine, sf = _mk_engine(tmp_path)
    monkeypatch.setattr(pe, "_today", lambda: "20260911")
    _seed(sf, qty=100, frozen=100, buy_date="20260911")
    assert asyncio.run(engine.settle_t1()) == 0

    # "重启"：同一份库、全新引擎实例（内存态一律丢失）
    reborn, sf2 = _mk_engine(tmp_path)
    assert sf2 is not sf
    monkeypatch.setattr(pe, "_today", lambda: "20260914")
    assert asyncio.run(reborn.settle_t1()) == 1
    assert _snap(sf2)["frozen"] == 0
