"""闭环交易「持仓/离场/验证」测试（2026-09-09 用户指令）。

覆盖：仓位引擎的阶段上限/闸门/角色权重/容量守卫；离场引擎的止损/移动止盈/
连板持有/弱转强；台账 D+1 持续性验证。
"""

import asyncio
import json
from datetime import datetime

from app.picks import exit_engine as ee
from app.picks import position_engine as pe


# ---------------------------------------------------------------- 仓位引擎

def test_phase_caps_matrix():
    assert pe.phase_caps("发酵", None) == (0.65, 2, pe.phase_caps("发酵", None)[2])
    assert pe.phase_caps("冰点", None)[:2] == (0.10, 1)
    # 闸门 strong → 0 仓；mild → 减半
    cap, max_pos, note = pe.phase_caps("发酵", {"stand_aside": True, "level": "strong"})
    assert cap == 0.0 and max_pos == 0
    cap, max_pos, _ = pe.phase_caps("发酵", {"stand_aside": True, "level": "mild"})
    assert abs(cap - 0.325) < 1e-9 and max_pos == 1


def test_role_weight():
    assert pe.role_weight("龙头") == 0.55
    assert pe.role_weight("中军") == 0.40
    assert pe.role_weight(None) == 0.30


def _fake_engine(positions, cash=1_000_000.0, orders=None):
    """假 PaperTradingEngine：记录下单、返回固定持仓视图。"""
    placed = []

    class _Fake:
        scope = "main"

        def positions_with_pnl(self, price_map):
            return positions

        def ensure_account(self):
            class _A:
                initial_cash = cash

            return _A()

        async def place_order(self, symbol, side, price, quantity):
            placed.append({"symbol": symbol, "side": side, "price": price, "quantity": quantity})
            from types import SimpleNamespace

            return SimpleNamespace(status="filled", symbol=symbol, filled_price=price, reason=None)

    return _Fake(), placed


def _app(engine):
    return type("A", (), {"state": type("S", (), {"paper": engine, "hub": object()})()})()


def test_maybe_open_opens_with_role_weight(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    monkeypatch.setattr(pe, "_today_gate_and_phase", lambda: ({}, "发酵"))
    engine, placed = _fake_engine([])
    out = asyncio.run(pe.maybe_open(_app(engine), symbol="600001", name="龙头股",
                                    trigger="buy_point", price=10.0, role="龙头"))
    assert out["opened"] is True
    # 1M × 65% × 55% = 357500 → 357 手 = 35700 股 @ 10 元
    assert placed and placed[0]["quantity"] == 35700 and placed[0]["side"] == "buy"


def test_maybe_open_guards(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    monkeypatch.setattr(pe, "_today_gate_and_phase", lambda: ({}, "发酵"))
    # 已持仓 → 跳过
    engine, placed = _fake_engine([{"symbol": "600001", "quantity": 100}])
    out = asyncio.run(pe.maybe_open(_app(engine), symbol="600001", name="x",
                                    trigger="buy_point", price=10.0))
    assert out["opened"] is False and "已持仓" in out["reason"]
    assert not placed
    # 只数满（发酵 max 2）→ 跳过
    engine2, placed2 = _fake_engine([{"symbol": "600002", "quantity": 100},
                                     {"symbol": "600003", "quantity": 100}])
    out2 = asyncio.run(pe.maybe_open(_app(engine2), symbol="600009", name="y",
                                     trigger="buy_point", price=10.0))
    assert out2["opened"] is False and "已满" in out2["reason"]
    # 闸门 strong → 0 仓
    monkeypatch.setattr(pe, "_today_gate_and_phase",
                        lambda: ({"stand_aside": True, "level": "strong"}, "发酵"))
    engine3, placed3 = _fake_engine([])
    out3 = asyncio.run(pe.maybe_open(_app(engine3), symbol="600010", name="z",
                                     trigger="buy_point", price=10.0))
    assert out3["opened"] is False and "封零" in out3["reason"]
    # pre_limit 不在开仓白名单（嗅到≠买入）
    engine4, placed4 = _fake_engine([])
    out4 = asyncio.run(pe.maybe_open(_app(engine4), symbol="600011", name="w",
                                     trigger="pre_limit", price=10.0))
    assert out4["opened"] is False and "白名单" in out4["reason"]
    assert not placed4


# ---------------------------------------------------------------- 离场引擎

def test_exit_stop_loss_and_sealed_hold():
    # 硬止损（默认 5.5%）
    pos = {"symbol": "600001", "quantity": 100, "available": 100, "cost_price": 10.0, "buy_date": "20260909"}
    rule = ee._rule_for(pos, 9.4, -6.0, 10.0, None, "20260909")
    assert rule and rule["action"] == "exit" and "止损" in rule["reason"]
    # 封板 → hold（绝不早止盈）
    rule2 = ee._rule_for(pos, 11.0, 10.0, 10.0, None, "20260909")
    assert rule2 and rule2["action"] == "hold"


def test_exit_trailing_rule():
    # 峰值 +20% 回撤 9% → 龙头（trail 8%）不触发？20-9=11 仍高于武装线，回撤 8% 线：峰值 12 → 12×0.92=11.04；价格 10.9 ≤ 11.04 → 触发
    assert ee.trailing_rule(10.0, 10.9, 12.0, "龙头") is not None
    # 回撤不足 → None
    assert ee.trailing_rule(10.0, 11.5, 12.0, "龙头") is None
    # 峰值未武装（<+15%）→ None
    assert ee.trailing_rule(10.0, 10.5, 11.0, "龙头") is None


def test_exit_wave_detection():
    # 昨收低于成本（走弱日）+ 今日 +4% → 二浪启动
    pos = {"symbol": "600002", "quantity": 100, "available": 100, "cost_price": 10.0, "buy_date": "20260907"}
    rule = ee._rule_for(pos, 10.5, 5.0, 9.8, None, "20260909")
    assert rule and rule["action"] == "wave" and "二浪" in rule["reason"]
    # 买入当日不判波浪
    rule2 = ee._rule_for(pos, 10.5, 5.0, 9.8, None, "20260907")
    assert rule2 is None or rule2["action"] != "wave"


# ---------------------------------------------------------------- D+1 验证

def test_validate_previous_day(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.watchlist import Base
    from app.picks import watch_ledger as wl

    engine = create_engine(f"sqlite:///{tmp_path / 'v.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(wl, "get_session_factory", lambda: sf)
    monkeypatch.setattr(wl, "beijing_now", lambda: datetime(2026, 9, 9, 15, 0, 0))

    with sf() as db:
        db.add(wl.WatchLedger(trade_date="2026-09-08", symbol="600001", name="甲",
                              layer="pre_limit", entry_price=10.0, entry_time="09:40:00",
                              reason=json.dumps({"gate": "pre_limit"}, ensure_ascii=False)))
        db.add(wl.WatchLedger(trade_date="2026-09-08", symbol="600002", name="乙",
                              layer="pre_limit", entry_price=20.0, entry_time="09:41:00",
                              reason=json.dumps({"d1": {"pct": 1.0}}, ensure_ascii=False)))  # 已验证幂等
        db.commit()

    n = wl.validate_previous_day({"600001": 10.5, "600002": 20.5}, sf)
    assert n == 1  # 600002 已有 d1 → 只写 600001
    with sf() as db:
        row = db.query(wl.WatchLedger).filter(wl.WatchLedger.symbol == "600001").first()
        d1 = json.loads(row.reason)["d1"]
        assert d1["pct"] == 5.0 and d1["grade"] == "仍强"
