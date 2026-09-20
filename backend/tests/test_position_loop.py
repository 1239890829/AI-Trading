"""闭环交易「持仓/离场/验证」测试（2026-09-09 用户指令）。

覆盖：仓位引擎的阶段上限/闸门/角色权重/容量守卫；离场引擎的止损/移动止盈/
连板持有/弱转强；台账 D+1 持续性验证。
"""

import asyncio
import json
from datetime import datetime

import pytest

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


def _fake_engine(positions, cash=1_000_000.0, orders=None, *,
                 order_status="filled", frozen=0.0):
    """假 PaperTradingEngine：记录下单、返回固定持仓/挂单视图。

    **方法集必须与真实引擎对齐**（KB-ENG-65 形态一）：`maybe_open` 对
    `pending_orders` / `frozen_cash` 的调用失败会被上层的 `except` 吞成
    "读取失败"，桩里缺方法时用例照样全绿，但走的是**异常兜底**而非正常路径。
    故此处补齐两个方法，并由 `order_status` 控制撮合结果（filled / pending / rejected），
    使「未成交」分支可被端到端驱动。
    """
    placed = []

    class _Fake:
        scope = "main"

        def positions_with_pnl(self, price_map):
            return positions

        def pending_orders(self, side=None):
            return [o for o in (orders or []) if side is None or o.get("side") == side]

        def frozen_cash(self):
            return frozen

        def ensure_account(self):
            class _A:
                initial_cash = cash

            return _A()

        async def place_order(self, symbol, side, price, quantity):
            placed.append({"symbol": symbol, "side": side, "price": price, "quantity": quantity})
            from types import SimpleNamespace

            return SimpleNamespace(
                status=order_status, symbol=symbol,
                filled_price=price if order_status == "filled" else None,
                reason=None, id=1,
            )

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


# ------------------------------------------------- R09 挂单 ≠ 已开仓（2026-09-14）

def _plan_file(tmp_path):
    return tmp_path / f"{pe.beijing_now().date().isoformat()}.json"


def _patch_alerts(monkeypatch, tmp_path):
    """拦截开仓通知出口——`maybe_open` 是**函数内 import**，patch 模块属性即生效。

    不拦的后果有两条：①「不得发通知」这条断言无法成立；② `append_alert` 会写到
    真实 `data/` 目录（测试污染，R21 同源）。故这里同时钉住出口与目标路径。
    """
    import app.picks.morning_brief as mb

    alerts: list[tuple] = []
    monkeypatch.setattr(mb, "append_alert", lambda *a, **k: alerts.append((a, k)))
    monkeypatch.setattr(mb, "brief_for_today", lambda: (tmp_path / "brief.json", None))
    return alerts


def test_maybe_open_pending_is_not_treated_as_filled(tmp_path, monkeypatch):
    """R09：撮合返回 `pending`（限价未达现价）时**不得**记为已开仓。

    旧实现只排除 `rejected`，`pending` 一路走到 `plan["decisions"] += {action:"open"}`
    + `position_open` 通知 + `opened=True` ⇒ 用户以为已建仓，实际只是挂了张限价单。
    回退即红：action 必须是可区分的 `open_pending`，返回值 `opened` 必须为 False。
    """
    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    monkeypatch.setattr(pe, "_today_gate_and_phase", lambda: ({}, "发酵"))
    alerts = _patch_alerts(monkeypatch, tmp_path)
    engine, placed = _fake_engine([], order_status="pending")

    out = asyncio.run(pe.maybe_open(_app(engine), symbol="600001", name="甲",
                                    trigger="buy_point", price=10.0, role="龙头"))

    assert placed and placed[0]["side"] == "buy", "单子确实发出去了（不能因噎废食）"
    assert out["opened"] is False, "挂单未成交 ⇒ 没有开仓"
    assert out["pending"] is True and "等待撮合" in out["reason"]
    plan = json.loads(_plan_file(tmp_path).read_text(encoding="utf-8"))
    assert [d["action"] for d in plan["decisions"]] == ["open_pending"]
    assert alerts == [], "挂单未成交不得发开仓通知（用户会以为已建仓）"


def test_maybe_open_filled_path_still_records_open(tmp_path, monkeypatch):
    """反向对照：真成交时行为不变（action 仍为 open、通知照发）。

    与上一条配对——只有一侧的断言无法区分「修好了」与「整条路径被关掉了」。
    """
    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    monkeypatch.setattr(pe, "_today_gate_and_phase", lambda: ({}, "发酵"))
    alerts = _patch_alerts(monkeypatch, tmp_path)
    engine, _ = _fake_engine([])  # order_status 默认 filled

    out = asyncio.run(pe.maybe_open(_app(engine), symbol="600001", name="甲",
                                    trigger="buy_point", price=10.0, role="龙头"))

    assert out["opened"] is True
    plan = json.loads(_plan_file(tmp_path).read_text(encoding="utf-8"))
    assert [d["action"] for d in plan["decisions"]] == ["open"]
    assert len(alerts) == 1 and alerts[0][0][1]["kind"] == "position_open"


def test_maybe_open_persists_same_decision_identity_into_fill(tmp_path, monkeypatch):
    """IMP-006：自动模拟成交必须引用上游同一 decision/version，成交价另记 order fill。"""
    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    monkeypatch.setattr(pe, "_today_gate_and_phase", lambda: ({}, "发酵"))
    alerts = _patch_alerts(monkeypatch, tmp_path)
    engine, _ = _fake_engine([])
    ctx = {
        "decision_id": "OD-demo",
        "decision_version": "ODV-demo-v2",
        "reference_entry": {"price": 9.8, "semantics": "reference_only_not_fill"},
        "executable_snapshot": {"price": 10.0, "state": "ready", "semantics": "action_time_quote_not_fill"},
    }

    out = asyncio.run(pe.maybe_open(
        _app(engine), symbol="600001", name="甲", trigger="buy_point",
        price=10.0, role="龙头", decision_context=ctx,
    ))

    assert out["opened"] is True
    assert out["decision_id"] == "OD-demo" and out["decision_version"] == "ODV-demo-v2"
    plan = json.loads(_plan_file(tmp_path).read_text(encoding="utf-8"))
    row = plan["decisions"][0]
    assert row["decision_id"] == "OD-demo" and row["decision_version"] == "ODV-demo-v2"
    assert row["reference_price"] == 9.8
    assert row["execution_snapshot_price"] == 10.0
    assert "reference_entry" not in row and "executable_snapshot" not in row
    assert row["price"] == 10.0 and row["order_id"] == 1
    # position_open 提醒只引用同一决策身份；成交事实由 order_id/filled price 独立表达。
    meta = alerts[0][0][1]["meta"]
    assert meta["decision_id"] == "OD-demo" and meta["decision_version"] == "ODV-demo-v2"
    assert meta["order_id"] == 1


def test_maybe_open_skips_symbol_already_pending(tmp_path, monkeypatch):
    """同一标的已挂单未成交 ⇒ 不再重复挂单（旧实现可反复挂出多张单）。"""
    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    monkeypatch.setattr(pe, "_today_gate_and_phase", lambda: ({}, "发酵"))
    _patch_alerts(monkeypatch, tmp_path)
    engine, placed = _fake_engine([], orders=[{"symbol": "600001", "side": "buy",
                                               "price": 9.5, "quantity": 35700}])

    out = asyncio.run(pe.maybe_open(_app(engine), symbol="600001", name="甲",
                                    trigger="buy_point", price=10.0, role="龙头"))

    assert out["opened"] is False and "已挂单未成交" in out["reason"]
    assert "35700" in out["reason"], "理由要带上既有挂单的量价，便于排查"
    assert not placed, "重复挂单必须被拦在最外层"


def test_maybe_open_pending_occupies_slot(tmp_path, monkeypatch):
    """未决挂单**占用持仓名额**：发酵档 max=2，2 张在挂 ⇒ 第 3 只不许再挂。"""
    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    monkeypatch.setattr(pe, "_today_gate_and_phase", lambda: ({}, "发酵"))
    _patch_alerts(monkeypatch, tmp_path)
    engine, placed = _fake_engine([], orders=[
        {"symbol": "600002", "side": "buy", "price": 9.0, "quantity": 100},
        {"symbol": "600003", "side": "buy", "price": 9.0, "quantity": 100},
    ])

    out = asyncio.run(pe.maybe_open(_app(engine), symbol="600009", name="丙",
                                    trigger="buy_point", price=10.0))

    assert out["opened"] is False and "已满" in out["reason"]
    assert "2/2" in out["reason"], "名额口径 = 持仓 + 未决挂单"
    assert not placed


def test_maybe_open_pending_sell_does_not_occupy_slot(tmp_path, monkeypatch):
    """反向：**卖出**挂单不占名额（挂卖单是在离场，不是建仓）。"""
    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    monkeypatch.setattr(pe, "_today_gate_and_phase", lambda: ({}, "发酵"))
    _patch_alerts(monkeypatch, tmp_path)
    engine, placed = _fake_engine([], orders=[
        {"symbol": "600002", "side": "sell", "price": 11.0, "quantity": 100},
        {"symbol": "600003", "side": "sell", "price": 11.0, "quantity": 100},
    ])

    out = asyncio.run(pe.maybe_open(_app(engine), symbol="600009", name="丙",
                                    trigger="buy_point", price=10.0))

    assert out["opened"] is True and placed


def test_maybe_open_exposure_includes_frozen_cash(tmp_path, monkeypatch):
    """敞口口径 = 持仓市值 + **买入挂单冻结额**（R01 同源）。

    发酵档 65%、初始 100 万 ⇒ 阀值 65 万。零持仓但冻结 65 万时，实际已投满，
    旧实现（只看持仓市值）会判成「敞口 0%」继续加仓。
    """
    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    monkeypatch.setattr(pe, "_today_gate_and_phase", lambda: ({}, "发酵"))
    _patch_alerts(monkeypatch, tmp_path)
    engine, placed = _fake_engine([], frozen=650_000.0)

    out = asyncio.run(pe.maybe_open(_app(engine), symbol="600009", name="丙",
                                    trigger="buy_point", price=10.0))

    assert out["opened"] is False and "总敞口" in out["reason"]
    assert not placed


def test_maybe_open_refuses_when_pending_read_fails(tmp_path, monkeypatch):
    """挂单读失败必须 fail-closed（与持仓读取同源，风控不降级）。

    桩**不提供** `pending_orders` ⇒ 调用抛 AttributeError ⇒ 必须被上层吞成
    "读取失败"并拒绝开仓。旧实现没有这道判据，同样输入会直接下单放行。
    """
    monkeypatch.setattr(pe, "_PLAN_DIR", tmp_path)
    monkeypatch.setattr(pe, "_today_gate_and_phase", lambda: ({}, "发酵"))
    _patch_alerts(monkeypatch, tmp_path)

    class _NoPending:
        scope = "main"
        placed = False

        def positions_with_pnl(self, _q):
            return []

        def ensure_account(self):
            return type("A", (), {"initial_cash": 1_000_000.0})()

        async def place_order(self, *a, **k):
            _NoPending.placed = True
            raise AssertionError("挂单读不到时不得下单")

    out = asyncio.run(pe.maybe_open(_app(_NoPending()), symbol="600009", name="丙",
                                    trigger="buy_point", price=10.0))

    assert out["opened"] is False and "挂单读取失败" in out["reason"]
    assert _NoPending.placed is False


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


# ---------------------------------------------------------------- S1-2 读取降级可见性

def test_real_positions_failure_is_three_state(monkeypatch):
    """S1-2 回归：读失败必须与「确实无持仓」可区分，且连续失败计数累加。

    旧实现 `except Exception: return {}` 连日志都没有 ⇒ 一次 SQLite 锁超时
    就让整轮真实持仓止损检查静默跳过，用户与看板都只看到「今天没有信号」。
    """
    import app.core.db as core_db

    monkeypatch.setattr(core_db, "get_session_factory",
                        lambda: (_ for _ in ()).throw(RuntimeError("db locked")))
    ee._REAL_READ.update(state="unknown", as_of=None, age_seconds=None, reason=None, failures=0)

    assert ee._real_positions() == {}
    st = ee.real_position_read_state()
    assert st["state"] == "failed"
    assert "RuntimeError" in st["reason"] and st["failures"] == 1
    assert st["as_of"] is not None

    ee._real_positions()
    assert ee.real_position_read_state()["failures"] == 2  # 连续失败可见


def test_real_positions_empty_is_not_failed():
    """空结果（确实无持仓）与失败必须落到不同状态——否则降级信号天天误报。"""
    from sqlalchemy import text

    from app.core.db import get_engine
    from app.models.real_position import RealTrade
    from app.models.watchlist import Base

    # 导入本身就是目的：模型须先进 Base.metadata 才会被 create_all 建表（pyflakes 不认 # noqa，
    # 所以顺手断言表名——既用掉这个名字，也把「导入是刻意的」写进测试）。
    assert RealTrade.__tablename__ == "real_trade"
    # 测试库是 sqlite:///:memory:（conftest），此处只建表、不碰磁盘上的生产库
    Base.metadata.create_all(get_engine())
    with get_engine().connect() as conn:
        conn.execute(text("DELETE FROM real_trade"))
        conn.commit()

    ee._REAL_READ.update(state="unknown", failures=0)
    out = ee._real_positions()
    st = ee.real_position_read_state()
    assert out == {} and st["state"] in {"empty", "ok"} and st["state"] != "failed"
    assert st["failures"] == 0


def test_evaluate_once_surfaces_read_degradation(monkeypatch):
    """S1-2 回归：两路持仓读取失败时，`evaluate_once` 必须产出可见降级信号。

    failed 时不能只是 `positions = []` 静默继续——自动离场/硬止损/真实持仓提醒
    全被跳过，必须进 fired（供哨兵观测）并在通知中心留痕。
    """
    import app.core.db as core_db

    # 两路都不碰真库（`_sf` 与 `get_session_factory` 双双注入失败），故无需建表。
    class _Engine:
        """两路读取都必须**确定性失败**，不许依赖「表恰好还没建」这种副作用。

        2026-09-11 踩：原写法 `_sf = real_sf`（真实可用 sessionmaker）配 patch
        `core_db.get_session_factory` —— 只拦到真实持仓那一路；模拟持仓走 `engine._sf()`
        照常成功。于是**单跑**（表未建，模拟那路也抛 no such table）出 2 条 degraded、
        **全量跑**（前序用例已 create_all 建好表）只出 1 条 ⇒ 通过与否取决于执行顺序。
        现改为 `_sf` 自身抛错，两路必然降级，与运行顺序无关。
        """

        scope = "main"

        def _sf(self):  # 与 PaperEngine 的 sessionmaker 同名属性，调用即失败
            raise RuntimeError("db locked")

        async def settle_t1(self):
            """R04 起 `evaluate_once` 先结算 T+1（且走 `_sf`）⇒ 本桩必须同样
            **确定性失败**：若省掉这个方法，AttributeError 会顶替真实的读取失败原因，
            用例仍绿却是为错的理由（KB-ENG-65 假绿形态㈠「桩缺方法」）。"""
            raise RuntimeError("db locked")

        async def place_order(self, *a, **k):  # pragma: no cover - 本用例不应触发下单
            raise AssertionError("读取失败时不应有下单路径")

    app = type("A", (), {"state": type("S", (), {"paper": _Engine(),
                                                  "snapshot_service": None})()})()

    monkeypatch.setattr(ee, "load_plan", lambda: {"peaks": {}})
    monkeypatch.setattr(ee, "save_plan", lambda plan: None)
    monkeypatch.setattr(ee, "_picks_combos", lambda: {})
    monkeypatch.setattr(core_db, "get_session_factory",
                        lambda: (_ for _ in ()).throw(RuntimeError("db locked")))
    ee._NOTIFIED.clear()
    ee._PAPER_READ.update(state="unknown", failures=0)
    ee._REAL_READ.update(state="unknown", failures=0)
    notified: list[tuple] = []
    monkeypatch.setattr(ee, "_notify", lambda *a, **k: notified.append((a, k)))

    fired = asyncio.run(ee.evaluate_once(app))

    degraded = [f for f in fired if f["action"] == "degraded"]
    assert len(degraded) == 2, fired  # 模拟 + 真实 两路各一条
    assert ee._PAPER_READ["state"] == "failed" and ee._REAL_READ["state"] == "failed"
    assert [c[0][3] for c in notified] == ["position_monitor_degraded"] * 2
    assert any("止损已跳过" in c[0][4] for c in notified)


# ------------------------------------------------- R09 离场挂单 ≠ 已离场（2026-09-14）

def test_exit_pending_order_is_not_recorded_as_exit(tmp_path, monkeypatch):
    """R09：卖单挂起（限价未达现价）**不得**记为「已离场」。

    `evaluate_once` 的卖价取自本轮快照，而 `place_order` 内部会重新取一次
    实时行情 —— 两刻之间价格下滑就会挂单（卖：限价 > 现价）。旧实现与仓位
    引擎同型（只排除 rejected），于是**持仓还在持仓表里，却被记成已离场**：
    写 `exits`、清峰值、发「自动离场」通知，复盘与峰值轨迹全部对不上。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.paper import PaperPosition
    from app.models.watchlist import Base

    db_engine = create_engine(f"sqlite:///{tmp_path / 'ex.db'}")
    Base.metadata.create_all(db_engine)
    sf = sessionmaker(bind=db_engine)
    with sf() as db:
        db.add(PaperPosition(scope="main", symbol="600001", quantity=100,
                             frozen_today=0, cost_price=10.0, buy_date="20260901"))
        db.commit()

    orders: list[dict] = []

    class _Engine:
        scope = "main"

        def _sf(self):
            # 真实引擎里 `_sf` 是 sessionmaker，调用即得 Session；
            # 调用方写作 `with engine._sf() as db:` ⇒ 桩必须返回 Session 而非 sessionmaker。
            return sf()

        async def settle_t1(self):
            """R04 起 `evaluate_once` 先结算 T+1；本桩持仓 `frozen_today=0`，无事可做
            ⇒ 显式空实现（缺方法会以 AttributeError 冒充"读取失败"，用例绿得没有根据）。"""
            return 0

        async def place_order(self, symbol, side, price, quantity):
            from types import SimpleNamespace

            orders.append({"symbol": symbol, "side": side, "price": price, "quantity": quantity})
            return SimpleNamespace(status="pending", symbol=symbol,
                                   filled_price=None, reason=None, id=1)

    ss = type("SS", (), {"snapshot": [
        {"symbol": "600001", "name": "甲", "price": 9.0, "change_pct": -6.0},
    ]})()
    app = type("A", (), {"state": type("S", (), {"paper": _Engine(),
                                                "snapshot_service": ss})()})()

    plan = {"peaks": {"600001": 12.0}, "decisions": [], "exits": []}
    monkeypatch.setattr(ee, "load_plan", lambda: plan)
    monkeypatch.setattr(ee, "save_plan", lambda p: None)
    monkeypatch.setattr(ee, "_picks_combos", lambda: {})
    monkeypatch.setattr(ee, "_real_positions", lambda: {})
    ee._NOTIFIED.clear()
    ee._PAPER_READ.update(state="unknown", failures=0)
    ee._REAL_READ.update(state="empty", failures=0)
    notified: list[tuple] = []
    monkeypatch.setattr(ee, "_notify", lambda *a, **k: notified.append((a, k)))

    fired = asyncio.run(ee.evaluate_once(app))

    assert orders and orders[0]["side"] == "sell" and orders[0]["quantity"] == 100
    assert plan["exits"] == [], "挂单未成交不得写离场记录（持仓仍在）"
    assert plan["peaks"].get("600001") == 12.0, "挂单未成交不得清峰值轨迹"
    kinds = [c[0][3] for c in notified]
    assert "position_exit" not in kinds, "不得发「自动离场」通知"
    assert "position_exit_pending" in kinds, "要留痕「挂单受理未成交」，不能静默"
    assert any(f["action"] == "exit_pending" for f in fired)


def test_exit_filled_order_still_records_exit(tmp_path, monkeypatch):
    """反向对照：真成交时离场记录照写（与上一条配对，防「整条路径被关掉」）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.paper import PaperPosition
    from app.models.watchlist import Base

    db_engine = create_engine(f"sqlite:///{tmp_path / 'ex2.db'}")
    Base.metadata.create_all(db_engine)
    sf = sessionmaker(bind=db_engine)
    with sf() as db:
        db.add(PaperPosition(scope="main", symbol="600001", quantity=100,
                             frozen_today=0, cost_price=10.0, buy_date="20260901"))
        db.commit()

    class _Engine:
        scope = "main"

        def _sf(self):
            # 真实引擎里 `_sf` 是 sessionmaker，调用即得 Session；
            # 调用方写作 `with engine._sf() as db:` ⇒ 桩必须返回 Session 而非 sessionmaker。
            return sf()

        async def settle_t1(self):
            # R04 起 `evaluate_once` 先结算 T+1。本桩持仓 frozen_today=0 ⇒ 结算无事可做，
            # 返回 0 即可；**不可省掉此方法**——省掉后 AttributeError 会被
            # `except Exception` 兜成「模拟持仓读取失败」，用例仍绿却是为错的理由（KB-ENG-65 假绿形态㈠）。
            return 0

        async def place_order(self, symbol, side, price, quantity):
            from types import SimpleNamespace

            return SimpleNamespace(status="filled", symbol=symbol,
                                   filled_price=price, reason=None, id=1)

    ss = type("SS", (), {"snapshot": [
        {"symbol": "600001", "name": "甲", "price": 9.0, "change_pct": -6.0},
    ]})()
    app = type("A", (), {"state": type("S", (), {"paper": _Engine(),
                                                "snapshot_service": ss})()})()

    plan = {"peaks": {"600001": 12.0}, "decisions": [], "exits": []}
    monkeypatch.setattr(ee, "load_plan", lambda: plan)
    monkeypatch.setattr(ee, "save_plan", lambda p: None)
    monkeypatch.setattr(ee, "_picks_combos", lambda: {})
    monkeypatch.setattr(ee, "_real_positions", lambda: {})
    ee._NOTIFIED.clear()
    ee._PAPER_READ.update(state="unknown", failures=0)
    ee._REAL_READ.update(state="empty", failures=0)
    notified: list[tuple] = []
    monkeypatch.setattr(ee, "_notify", lambda *a, **k: notified.append((a, k)))

    fired = asyncio.run(ee.evaluate_once(app))

    assert len(plan["exits"]) == 1 and plan["exits"][0]["symbol"] == "600001"
    assert plan["peaks"].get("600001") is None, "成交后峰值轨迹应清除"
    assert [c[0][3] for c in notified] == ["position_exit"]
    assert any(f["action"] == "exit" for f in fired)


def test_role_of_accepts_pending_open(tmp_path):
    """`open_pending` 也要能查出角色：挂单成交后不会再补写 `action="open"`，
    不认它这批持仓就静默掉到默认止损档（放宽），而它正是最初被触发的那批标的。"""
    plan = {"decisions": [{"symbol": "600001", "action": "open_pending", "role": "龙头"}]}
    assert ee._role_of(plan, "600001") == "龙头"
    # 非开仓类 action 不得误认
    assert ee._role_of({"decisions": [{"symbol": "600001", "action": "exit", "role": "龙头"}]},
                       "600001") is None


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


# ------------------------------ R07 真实持仓监护与页面同一事实视图（2026-09-14）
#
# 缺陷：`_real_positions` 另写了一份「净现金投入」成本算法（买入加、卖出减
# `fill_price×qty`，**不计费用**、卖出不按摊薄成本结转），且**完全不读**
# `RealPositionOverride` ⇒ 止损提醒线与持仓页面口径分裂：
#   买 1000@10、卖 400@12 → 页面剩余成本 10.00，监护算 8.67（5200/600）。
# 提醒线被"算低"的后果是**该提醒的时候不提醒**（真实持仓只提醒、不下单，
# 故影响面限于提醒，但仍会让人错过离场窗口）。
#
# 判据刻意分两层（KB-ENG-65 形态二：只做 A/B 等价对照钉不住"两路共用的
# 判据本身失效"）：**① 两路一致**（同一账本喂 API 与监护）+ **② 绝对数值**
# （按摊薄成本定义独立算出的期望值，并显式断言不等于旧口径的值）。


def _seed_and_view(trades, override=None):
    """把合成流水（+可选人工覆盖）写进测试库，返回 (API 视图, 监护视图)。

    两个视图必须来自**同一账本**——这正是 R07 的验收判据原文
    「同一合成账本同时喂 API 与监护，数量/成本/覆盖状态一致」。
    """
    from sqlalchemy import text

    from app.core.db import get_engine, get_session_factory
    from app.models.watchlist import Base
    from app.services.real_position_service import load_positions

    engine = get_engine()
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM real_trade"))
        conn.execute(text("DELETE FROM real_position_override"))
        conn.commit()

    sf = get_session_factory()
    with sf() as db:
        for t in trades:
            db.add(t)
        if override is not None:
            db.add(override)
        db.commit()

    api = {p.symbol: p for p in load_positions(sf)}
    ee._REAL_READ.update(state="unknown", failures=0)
    monitor = ee._real_positions()
    return api, monitor


def _rt(symbol, side, fill_price, quantity, fee=0.0, traded_at="2026-09-10", name=None):
    from app.models.real_position import RealTrade

    return RealTrade(symbol=symbol, name=name, side=side, fill_price=fill_price,
                     quantity=quantity, fee=fee, traded_at=traded_at)


def test_monitor_cost_matches_api_on_partial_sell_with_fees():
    """部分卖出 + 含手续费：监护必须与页面同为摊薄成本，且**不是**净现金投入。"""
    api, mon = _seed_and_view([
        _rt("600105", "buy", 10.0, 1000, fee=5.0, name="平安银行"),
        _rt("600105", "sell", 12.0, 400, fee=3.0, traded_at="2026-09-11"),
    ])

    # 摊薄成本定义独立算一遍：成本总额 10×1000+5 = 10005；卖出按摊薄成本
    # 结转 10005/1000×400 = 4002 ⇒ 剩 6003 / 600 股 = 10.005
    expected = round((10.0 * 1000 + 5.0 - (10005.0 / 1000) * 400) / 600, 4)
    assert expected == 10.005

    assert api["600105"].quantity == 600 and api["600105"].avg_cost == expected
    # ① 两路一致
    assert mon["600105"]["quantity"] == api["600105"].quantity
    assert mon["600105"]["cost"] == pytest.approx(api["600105"].avg_cost)
    # ② 绝对值（独立算出的期望值），并排除旧口径
    assert mon["600105"]["cost"] == pytest.approx(expected)
    assert mon["600105"]["cost"] != pytest.approx(8.6667, abs=1e-3)  # 旧净现金投入值
    assert mon["600105"]["overridden"] is False


def test_monitor_respects_manual_override():
    """登记人工覆盖后，监护必须看覆盖值——旧实现**完全忽略覆盖**。"""
    from app.models.real_position import RealPositionOverride

    api, mon = _seed_and_view(
        [_rt("600105", "buy", 10.0, 1000, name="平安银行")],
        override=RealPositionOverride(symbol="600105", quantity=200, total_cost=2200.0),
    )

    assert api["600105"].overridden is True
    assert mon["600105"]["quantity"] == 200          # 不是流水算出的 1000
    assert mon["600105"]["cost"] == pytest.approx(11.0)  # 2200/200
    assert mon["600105"]["overridden"] is True
    assert mon["600105"]["cost"] != pytest.approx(10.0, abs=1e-3)  # 不是流水成本


def test_monitor_cost_resets_after_clear_then_rebuy():
    """清仓再买：摊薄成本应归零重算（8.00），净现金投入会算成 4.00。"""
    api, mon = _seed_and_view([
        _rt("600105", "buy", 10.0, 1000, name="平安银行"),
        _rt("600105", "sell", 12.0, 1000, traded_at="2026-09-11"),
        _rt("600105", "buy", 8.0, 500, traded_at="2026-09-12"),
    ])

    assert api["600105"].quantity == 500 and api["600105"].avg_cost == pytest.approx(8.0)
    assert mon["600105"]["quantity"] == api["600105"].quantity
    assert mon["600105"]["cost"] == pytest.approx(8.0)
    assert mon["600105"]["cost"] != pytest.approx(4.0, abs=1e-3)  # 旧口径 (10000-12000+4000)/500


def test_override_with_zero_cost_is_flagged_not_silently_unmonitored():
    """覆盖把总成本记成 0：`cost` 置 None 并**保留在视图里**（不静默消失）。

    若拿 0 参与计算，判据退化成 `price <= 0`、正价格恒不成立 ⇒ 该持仓
    **永不触发**止损且外面看不出来。故：① `cost is None` 供消费方显式跳过；
    ② 仍留在持仓视图里（三态记账的 `state=ok`，且不因排除而漏出止盈标的池）。
    """
    from app.models.real_position import RealPositionOverride

    api, mon = _seed_and_view(
        [_rt("600105", "buy", 10.0, 600, name="平安银行")],
        override=RealPositionOverride(symbol="600105", quantity=600, total_cost=0.0),
    )

    assert api["600105"].avg_cost == pytest.approx(0.0)   # 覆盖层的真实取值
    assert mon["600105"]["quantity"] == 600
    assert mon["600105"]["cost"] is None                  # ① 显式"不可用"
    assert ee._REAL_READ["state"] == "ok"                 # ② 不是 empty
    assert ee._REAL_READ["failures"] == 0


def test_monitor_keeps_three_state_empty_vs_failed(monkeypatch):
    """R07 改动不得破坏 S1-2 三态：无持仓 → empty；读失败 → failed。"""
    from sqlalchemy import text

    from app.core.db import get_engine

    engine = get_engine()
    from app.models.watchlist import Base

    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM real_trade"))
        conn.execute(text("DELETE FROM real_position_override"))
        conn.commit()

    ee._REAL_READ.update(state="unknown", failures=0)
    assert ee._real_positions() == {}
    assert ee._REAL_READ["state"] == "empty" and ee._REAL_READ["failures"] == 0

    import app.core.db as core_db

    monkeypatch.setattr(core_db, "get_session_factory",
                        lambda: (_ for _ in ()).throw(RuntimeError("db locked")))
    assert ee._real_positions() == {}
    assert ee._REAL_READ["state"] == "failed" and ee._REAL_READ["failures"] == 1


def test_zero_cost_real_position_fires_no_alert_and_others_still_do(monkeypatch):
    """消费端判空（R07）：`cost=None` 不得触发提醒，**且不影响其他持仓照常提醒**。

    只断言 `_real_positions` 返回 `cost is None` 是不够的——行为终点是"发不发提醒"。
    本用例同时钉住三件事：① 零成本那条不报；② 同一轮里成本正常的持仓**该报还报**
    （否则断言可能是恒真的空转）；③ 提醒文案能区分成本来源（人工覆盖 / 流水摊薄）。
    若把消费端的判空删掉，`None * (1-stop)` 会抛 TypeError 打挂整轮 → 本用例变红。
    """
    from sqlalchemy import text

    from app.core.db import get_engine, get_session_factory
    from app.models.real_position import RealPositionOverride
    from app.models.watchlist import Base

    engine_db = get_engine()
    Base.metadata.create_all(engine_db)
    with engine_db.connect() as conn:
        conn.execute(text("DELETE FROM real_trade"))
        conn.execute(text("DELETE FROM real_position_override"))
        conn.commit()

    sf = get_session_factory()
    with sf() as db:
        db.add(_rt("600105", "buy", 10.0, 600, name="甲"))       # 覆盖把成本记成 0
        db.add(_rt("600519", "buy", 10.0, 1000, name="乙"))      # 流水摊薄，成本 10.00
        db.add(_rt("600036", "buy", 10.0, 1000, name="丙"))      # 人工覆盖，成本 11.00
        db.add(RealPositionOverride(symbol="600105", quantity=600, total_cost=0.0))
        db.add(RealPositionOverride(symbol="600036", quantity=100, total_cost=1100.0))
        db.commit()

    class _Engine:
        scope = "main"
        _sf = staticmethod(sf)

        async def settle_t1(self):
            """R04 起 `evaluate_once` 先结算 T+1。本用例只有真实持仓（模拟仓为空），
            结算无事可做 ⇒ 显式空实现（缺方法会以 AttributeError 冒充读取失败）。"""
            return 0

        async def place_order(self, *a, **k):  # pragma: no cover - 真实持仓只提醒不下单
            raise AssertionError("真实持仓不应有下单路径")

    # 三只现价都低于自身成本 —— 只有"成本可用"的那两只应当收到提醒
    snap = [
        {"symbol": "600105", "name": "甲", "price": 9.0, "change_pct": -3.0},
        {"symbol": "600519", "name": "乙", "price": 9.0, "change_pct": -3.0},
        {"symbol": "600036", "name": "丙", "price": 10.0, "change_pct": -2.0},
    ]
    app = type("A", (), {"state": type("S", (), {
        "paper": _Engine(),
        "snapshot_service": type("Snap", (), {"snapshot": snap})(),
    })()})()

    monkeypatch.setattr(ee, "load_plan", lambda: {"peaks": {}})
    monkeypatch.setattr(ee, "save_plan", lambda plan: None)
    monkeypatch.setattr(ee, "_picks_combos", lambda: {})
    ee._NOTIFIED.clear()
    ee._REAL_READ.update(state="unknown", failures=0)
    ee._PAPER_READ.update(state="unknown", failures=0)
    notified: list[tuple] = []
    monkeypatch.setattr(ee, "_notify", lambda *a, **k: notified.append((a, k)))

    fired = asyncio.run(ee.evaluate_once(app))

    alerts = {c[0][1]: c[0][4] for c in notified if c[0][3] == "real_exit_alert"}
    # ② 该报的照报（成本 10.00 与 11.00 都在止损线下）
    assert sorted(alerts) == ["600036", "600519"], (fired, alerts)
    # ① 零成本那条不报、也没把整轮打挂
    assert "600105" not in alerts
    assert not any(f["symbol"] == "600105" for f in fired)
    # ③ 成本来源可见
    assert "人工覆盖" in alerts["600036"] and "11.00" in alerts["600036"]
    assert "流水摊薄" in alerts["600519"] and "10.00" in alerts["600519"]
    # 文案引导「登记卖出流水」而非删除历史流水（R07 替代方案原文）
    assert "登记卖出流水" in alerts["600519"]
