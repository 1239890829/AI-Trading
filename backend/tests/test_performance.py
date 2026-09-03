"""绩效指标模块测试：已知答案用例（手算锚定）+ 配对逻辑 + 目标函数契约。"""
import pytest

from app.market.backtest import Trade
from app.market.performance import (
    PROFIT_FACTOR_CAP,
    TradePair,
    compute_performance,
    objective_direction,
    objective_value,
    pair_trades_ts,
)

approx = pytest.approx


# ---------------------------------------------------------------- 曲线类

def _perf(equity, pairs=None):
    return compute_performance(equity, pairs)


def test_max_drawdown_known_answer():
    # peak=120，谷 90 → dd=0.25；回撤持续 1 根后新低点才重置
    perf = _perf([100.0, 120.0, 90.0, 110.0])
    assert perf["max_drawdown"] == 0.25
    assert perf["max_drawdown_days"] == 1


def test_total_and_annual_244_bars():
    # 244 根（years=1）：annual == total
    n = 245  # equity 含起点 → 口径 years = n/244
    equity = [100.0 * (1.21) ** (i / (n - 1)) for i in range(n)]
    perf = _perf(equity)
    assert perf["total_return"] == 0.21
    # years = 245/244 → annual = 1.21^(244/245)-1（引擎口径，比 total 略小）
    expect_annual = 1.21 ** (244 / 245) - 1
    assert perf["annual_return"] == approx(expect_annual, rel=1e-6)


def test_sharpe_zero_when_constant_returns():
    # daily 收益严格恒定（×2 等比在二进制浮点下精确）→ sd=0 → sharpe/sortino=0
    equity = [100.0, 200.0, 400.0, 800.0, 1600.0]
    perf = _perf(equity)
    assert perf["sharpe"] == 0.0
    assert perf["sortino"] == 0.0


def test_var_cvar_known_answer():
    # 100 段日收益：10 段 -10%，90 段 0% → var95（5% 分位）=-10%，cvar95（最差 5 段均值）=-10%
    equity = [100.0]
    for i in range(100):
        r = -0.10 if i < 10 else 0.0
        equity.append(equity[-1] * (1 + r))
    perf = _perf(equity)
    assert perf["var_95_daily"] == -0.10
    assert perf["cvar_95_daily"] == -0.10


def test_ulcer_and_recovery():
    # equity [100,100,90,100]：dd% 序列 [0,0,10,0] → UI=5；recovery=total%/mdd%
    perf = _perf([100.0, 100.0, 90.0, 100.0])
    assert perf["ulcer_index"] == 5.0
    # total = 0%，mdd = 10% → recovery = 0
    assert perf["recovery_factor"] == 0.0


def test_insufficient_equity_fallback():
    perf = _perf([100.0])
    assert perf["total_return"] == 0.0
    assert perf["p_value"] == 1.0
    perf2 = _perf([0.0, 10.0])
    assert perf2["total_return"] == 0.0  # equity[0]<=0 兜底


# ---------------------------------------------------------------- 交易类

def _pairs(pnls: list[float]) -> list[TradePair]:
    return [
        TradePair(
            entry_i=i, exit_i=i + 1, entry_ts=f"d{i}", exit_ts=f"d{i + 1}",
            cost=1000.0, proceeds=1000.0 * (1 + p), pnl=1000.0 * p, pnl_pct=p,
        )
        for i, p in enumerate(pnls)
    ]


def test_trade_metrics_known_answer():
    pnls = [0.01, -0.02, 0.03, 0.04, -0.05]
    perf = _perf([100.0, 101.0, 99.0, 102.0, 106.0, 100.7], _pairs(pnls))
    assert perf["n_trades"] == 5
    assert perf["win_rate"] == 0.6  # pnl>=0 计胜
    assert perf["profit_factor"] == approx(0.08 / 0.07)
    assert perf["expectancy_pct"] == approx(0.2)  # mean 0.002 ×100
    assert perf["expectancy_ratio"] == approx((0.08 / 3) / (0.07 / 2))
    assert perf["avg_win_pct"] == approx(8.0 / 3)
    assert perf["avg_loss_pct"] == approx(-3.5)
    assert perf["best_trade_pct"] == 4.0
    assert perf["worst_trade_pct"] == -5.0
    assert perf["max_win_streak"] == 2
    assert perf["max_loss_streak"] == 1
    assert perf["avg_holding_bars"] == 1.0


def test_profit_factor_capped_when_no_losses():
    perf = _perf([100.0, 101.0], _pairs([0.01]))
    assert perf["profit_factor"] == PROFIT_FACTOR_CAP


def test_sqn_and_pvalue_directions():
    # 全部同额盈利：sd=0 → sqn=0；p=0（mean>0 显著）
    perf = _perf([100.0, 110.0], _pairs([0.01] * 10))
    assert perf["sqn"] == 0.0
    assert perf["p_value"] == 0.0
    # mean=0.02 sd=0.01 N=10：t≈6.32 → p < 0.001
    pnls = [0.01, 0.03] * 5
    perf2 = _perf([100.0, 110.0], _pairs(pnls))
    assert perf2["p_value"] < 0.001
    assert perf2["sqn"] > 0


# ---------------------------------------------------------------- 配对

def test_pair_trades_ts_matches_engine_semantics():
    trades = [
        Trade("t1", "t1", "buy", 10.0, 10.0, 100, 2.0, True),
        Trade("t2", "t2", "sell", 11.0, 11.0, 100, 3.0, True),
        Trade("t3", "t3", "sell", 9.0, 9.0, 0, 0.0, False, "limit_down"),  # 拒单不入对
        Trade("t4", "t4", "buy", 12.0, 12.0, 100, 2.0, True),
        Trade("t5", "t5", "sell", 12.5, 12.5, 100, 3.0, True),
    ]
    ts_index = {f"t{i}": i for i in range(1, 6)}
    pairs = pair_trades_ts(trades, ts_index)
    assert len(pairs) == 2
    p0, p1 = pairs
    assert p0.cost == 10.0 * 100 + 2.0
    assert p0.proceeds == 11.0 * 100 - 3.0
    assert p0.pnl_pct == approx(p0.proceeds / p0.cost - 1)
    assert (p0.entry_i, p0.exit_i) == (1, 2)
    assert (p1.entry_i, p1.exit_i) == (4, 5)


# ---------------------------------------------------------------- 目标函数契约

def test_objective_direction_and_unknown():
    assert objective_direction("max_drawdown") == "min"
    assert objective_direction("sharpe") == "max"
    m = {"max_drawdown": 0.15, "sharpe": 1.2}
    assert objective_value(m, "max_drawdown") == 0.15
    try:
        objective_value(m, "nope")
        raised = False
    except ValueError:
        raised = True
    assert raised
