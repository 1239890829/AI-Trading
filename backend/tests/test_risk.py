from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.risk.config import get_params
from app.risk.engine import RiskEngine
from app.risk.state_classifier import classify_market_state


def test_classify_strong_bull():
    indices = {"000001": {"price": 3600, "change_pct": 1.5}}
    breadth = {"up": 3500, "down": 1500, "total": 5000, "limit_up": 80, "limit_down": 3}
    sentiment = {"phase": "高潮", "temperature": 75, "confidence": "高"}
    state, reasons = classify_market_state(indices, breadth, sentiment)
    assert state == "强势多头"
    assert any("涨跌比" in r for r in reasons)


def test_classify_panic():
    indices = {"000001": {"price": 3400, "change_pct": -3.5}}
    breadth = {"up": 500, "down": 4500, "total": 5000, "limit_up": 5, "limit_down": 80}
    sentiment = {"phase": "冰点", "temperature": 20, "confidence": "中"}
    state, _ = classify_market_state(indices, breadth, sentiment)
    assert state == "恐慌/极端波动"


def test_classify_insufficient_data():
    state, reasons = classify_market_state({}, None, None)
    assert state == "数据不足"
    assert "市场宽度或情绪数据尚未就绪" in reasons


def test_position_params_exist_for_all_states():
    for s in ("强势多头", "震荡偏多", "震荡", "震荡偏空", "下跌趋势", "恐慌/极端波动", "数据不足"):
        p = get_params(s)
        assert 0 < p.single_stock_max_pct <= p.total_position_max_pct <= 1.0


def test_check_order_blocks_buy_in_bear():
    engine = RiskEngine(hub=None, snapshot_service=None, session_factory=None)
    engine._state = "下跌趋势"
    engine._params = get_params("下跌趋势")
    result = engine.check_order(
        symbol="600519",
        side="buy",
        price=1000,
        quantity=100,
        account={"cash": 1_000_000, "total": 1_000_000, "total_equity": 1_000_000, "initial_cash": 1_000_000},
        positions=[],
        quote={"quality": "high", "amount": 1e9},
    )
    assert result["allowed"] is False
    assert any("下跌趋势" in r for r in result["reasons"])


def test_check_order_enforces_single_stock_limit():
    engine = RiskEngine(hub=None, snapshot_service=None, session_factory=None)
    engine._state = "强势多头"
    engine._params = get_params("强势多头")
    result = engine.check_order(
        symbol="600519",
        side="buy",
        price=1000,
        quantity=1000,
        account={"cash": 1_000_000, "total": 1_000_000, "total_equity": 1_000_000, "initial_cash": 1_000_000},
        positions=[],
        quote={"quality": "high", "amount": 1e9},
    )
    # 1000 股 × 1000 元 = 1_000_000，单票上限 30%，所以被拒绝
    assert result["allowed"] is False
    assert any("单票仓位上限" in r for r in result["reasons"])


def test_check_order_allows_small_buy():
    engine = RiskEngine(hub=None, snapshot_service=None, session_factory=None)
    engine._state = "震荡偏多"
    engine._params = get_params("震荡偏多")
    result = engine.check_order(
        symbol="600519",
        side="buy",
        price=100,
        quantity=100,
        account={"cash": 1_000_000, "total": 1_000_000, "total_equity": 1_000_000, "initial_cash": 1_000_000},
        positions=[],
        quote={"quality": "high", "amount": 1e9},
    )
    assert result["allowed"] is True


def test_total_position_falls_back_to_cost_price_not_order_price():
    """回归：持仓缺实时价时必须回退成本价。

    早期实现回退到「本次订单价」，把 600519 的 200 股按 11.50 计成 2300 元，
    总仓位被低估成 0%，70% 上限形同虚设。
    """
    engine = RiskEngine(hub=None, snapshot_service=None, session_factory=None)
    engine._state = "震荡偏多"
    engine._params = get_params("震荡偏多")
    positions = [{
        "symbol": "600519", "quantity": 200, "available": 200,
        "cost_price": 1297.4, "last_price": None, "pnl": None, "pnl_pct": None,
    }]
    # 买另一只票 57.5 万；600519 成本市值 25.948 万，合计 83% > 70% 上限
    result = engine.check_order(
        symbol="000001", side="buy", price=11.5, quantity=50000,
        account={"cash": 1_000_000, "total": 1_000_000, "total_equity": 1_000_000,
                 "initial_cash": 1_000_000},
        positions=positions, quote={"quality": "high", "amount": 1e9},
    )
    assert result["allowed"] is False
    assert any("总仓位上限" in r for r in result["reasons"])
    # 关键断言：当前仓位应按成本价算成 26%，而不是订单价算成的 0%
    assert any("当前 26%" in r for r in result["reasons"])


def test_max_qty_is_cap_not_echo_of_quantity():
    """max_qty 是「本次最多可下单股数」，不受是否放行影响。"""
    engine = RiskEngine(hub=None, snapshot_service=None, session_factory=None)
    engine._state = "震荡偏多"
    engine._params = get_params("震荡偏多")
    result = engine.check_order(
        symbol="600519", side="buy", price=1000, quantity=100,
        account={"cash": 1_000_000, "total": 1_000_000, "total_equity": 1_000_000,
                 "initial_cash": 1_000_000},
        positions=[], quote={"quality": "high", "amount": 1e9},
    )
    assert result["allowed"] is True
    # 单票上限 25% → 25 万元 → @1000 元 = 250 股 → 向下取整 2 手 = 200 股
    # （总仓位 70% = 700 股，现金 1000 股，三者取小故受单票上限约束）
    assert result["max_qty"] == 200


def test_drawdown_protection_surfaces_missing_initial_cash():
    """缺 initial_cash 必须显式告警，不能静默退化成永不触发。"""
    engine = RiskEngine(hub=None, snapshot_service=None, session_factory=None)
    engine._state = "震荡偏多"
    engine._params = get_params("震荡偏多")
    result = engine.check_order(
        symbol="600519", side="buy", price=100, quantity=100,
        account={"cash": 1_000_000, "total": 1_000_000, "total_equity": 1_000_000},
        positions=[], quote={"quality": "high", "amount": 1e9},
    )
    assert any("回撤保护未生效" in w for w in result["warnings"])


def test_risk_state_api():
    with TestClient(app) as client:
        resp = client.get("/api/risk/state")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["state"] in {
            "强势多头", "震荡偏多", "震荡", "震荡偏空", "下跌趋势", "恐慌/极端波动", "数据不足"
        }
        assert "params" in data
        assert "single_stock_max_pct" in data["params"]


def test_risk_check_order_api():
    with TestClient(app) as client:
        resp = client.post("/api/risk/check-order", json={
            "symbol": "600519",
            "side": "buy",
            "price": 100,
            "quantity": 100,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "allowed" in data
        assert isinstance(data["reasons"], list)
