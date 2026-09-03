"""Walk-Forward 多窗验证测试：窗口切分、样本内选参方向、参数冻结（防泄露）、OOS 拼接。"""
import pytest

from app.market.backtest import BacktestConfig, build_strategy
from app.market.walkforward import walk_forward


def _mk_bars(n: int, *, drift: float = 0.002, amp: float = 0.02, period: float = 9.0) -> list[dict]:
    """确定性合成日K：趋势 + 正弦震荡（无随机源，CI 与本机逐位一致）。"""
    import math as _m

    bars = []
    price = 100.0
    for i in range(n):
        close = 100.0 * (1 + drift) ** i * (1 + amp * _m.sin(i / period))
        o = price
        bars.append({
            "ts": f"d{i:04d}",
            "open": o,
            "high": max(o, close) * 1.004,
            "low": min(o, close) * 0.996,
            "close": close,
            "volume": 1_000_000.0,
        })
        price = close
    return bars


def _factory(params: dict):
    return build_strategy("ma_cross", params)


def test_window_split_anchored():
    bars = _mk_bars(600)
    rep = walk_forward(
        bars, _factory, {"fast": [5], "slow": [20]},
        BacktestConfig(), strategy_id="ma_cross", n_windows=3,
    )
    assert len(rep.windows) == 3
    # n=600 → min_train=300, step=(600-300)//3=100
    assert rep.windows[0].train_len == 300
    assert rep.windows[0].test_len == 100
    assert rep.windows[0].test_start_ts == "d0300"
    assert rep.windows[1].test_start_ts == "d0400"
    assert rep.windows[2].test_start_ts == "d0500"
    assert rep.windows[2].test_end_ts == "d0599"  # 末窗吃满
    assert sum(rep.param_stability.values()) == 3


def test_oos_metrics_and_stability():
    bars = _mk_bars(600)
    rep = walk_forward(
        bars, _factory, {"fast": [3, 5], "slow": [20, 30]},
        BacktestConfig(), strategy_id="ma_cross", n_windows=2,
    )
    assert rep.oos_metrics, "OOS 指标必须产出"
    assert "total_return" in rep.oos_metrics
    assert "max_drawdown" in rep.oos_metrics
    # 每窗 OOS 段来自真实下跌/震荡数据时 mdd 应为正（有波动必有回撤记录）
    assert rep.oos_metrics["max_drawdown"] >= 0.0
    assert set(rep.param_stability) <= {"{'fast': 3, 'slow': 20}", "{'fast': 5, 'slow': 20}",
                                        "{'fast': 3, 'slow': 30}", "{'fast': 5, 'slow': 30}"}


def test_best_params_frozen_for_oos():
    """防泄露核心断言：test 段指标 = train 选出的参数在 test 段重放的结果。

    独立重放一遍（同参数、同 test 输入），指标必须与窗口记录逐位一致——
    若 walk_forward 在 test 段用了别的参数（如按 test 表现挑参）即暴露。
    """
    bars = _mk_bars(600)
    rep = walk_forward(
        bars, _factory, {"fast": [3, 8], "slow": [20]},
        BacktestConfig(), strategy_id="ma_cross", n_windows=2, warmup=30,
    )
    w = rep.windows[0]
    # 用该窗 best_params 在相同 test 输入上独立重放
    from app.market.backtest import run_backtest
    test_start = 300  # 同 _mk_bars(600) 的 min_train
    wstart = test_start - 30
    replay = run_backtest(bars[wstart:450], build_strategy("ma_cross", w.best_params), BacktestConfig())
    cut = test_start - wstart
    seg = replay.equity[cut:]
    # 独立重放的 test 段总收益与窗口记录一致（round 6 对 round 6）
    from app.market.performance import compute_performance
    expect = compute_performance(seg, [])
    assert w.test_metrics["total_return"] == expect["total_return"]
    assert w.test_metrics["max_drawdown"] == expect["max_drawdown"]


def test_train_objective_matches_grid_best():
    """样本内选参方向：objective_train 必须是该参数在 train 段网格里的最优值。"""
    bars = _mk_bars(600)
    rep = walk_forward(
        bars, _factory, {"fast": [3, 8], "slow": [20]},
        BacktestConfig(), strategy_id="ma_cross", n_windows=2,
        objective="max_drawdown",
    )
    from app.market.backtest import run_backtest
    train_bars = bars[:300]
    mdds = {}
    for fast in (3, 8):
        r = run_backtest(train_bars, build_strategy("ma_cross", {"fast": fast, "slow": 20}), BacktestConfig())
        mdds[fast] = r.extra_metrics["max_drawdown"]
    best = min(mdds.values())  # min 方向
    assert rep.windows[0].objective_train == best


def test_grid_too_large_raises():
    bars = _mk_bars(600)
    grid = {f"p{i}": [1, 2, 3, 4, 5] for i in range(5)}  # 5^5=3125 >> 24
    with pytest.raises(ValueError, match="超上限"):
        walk_forward(bars, _factory, grid, BacktestConfig(), n_windows=2)


def test_sample_too_short_raises():
    with pytest.raises(ValueError, match="样本不足"):
        walk_forward(_mk_bars(140), _factory, {}, BacktestConfig(), n_windows=2)


def test_window_too_short_raises():
    # n=240 → min_train=120, step=(240-120)//3=40 < 60 → 报错
    with pytest.raises(ValueError, match="test 段"):
        walk_forward(_mk_bars(240), _factory, {}, BacktestConfig(), n_windows=3)


def test_unknown_objective_raises():
    bars = _mk_bars(600)
    with pytest.raises(ValueError, match="未知优化目标"):
        walk_forward(bars, _factory, {"fast": [5]}, BacktestConfig(), objective="nope")


def test_min_window_single():
    """n_windows=1 也应工作：等价于一次 train/test 二分。"""
    bars = _mk_bars(400)
    rep = walk_forward(bars, _factory, {"fast": [5]}, BacktestConfig(), n_windows=1)
    assert len(rep.windows) == 1
    assert rep.windows[0].test_end_ts == "d0399"
