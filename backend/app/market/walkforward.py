"""Walk-Forward 多窗验证（freqtrade/qlib 调研采纳项）。

动机：单段回测的"漂亮指标"极易是参数过拟合——参数恰好在整段历史上
表现好。Walk-Forward 把历史切成滚动窗口：每窗在**样本内（train）**对参数
网格回测选出最优参数，再到**样本外（test）**验证该参数的真实表现；
全窗 OOS 拼接就是"如果按这套流程实盘"的诚实估计。

窗口方案：anchored（锚定扩张）——train 从头开始逐窗变长，test 依次后移。
参数选择目标可插拔（objective）：默认 **max_drawdown 最小化**
（freqtrade MaxDrawDownHyperOptLoss 的采纳口径），另有 annual_return /
sharpe / calmar / profit_factor。

防泄露纪律（docs/backtest-rules.md）：
- 每窗参数**只由 train 段回测决定**；test 段回测时参数已冻结；
- test 段回测带 warmup 预热段（默认 30 根，供均线类策略有历史可看），
  但指标只取 test_start 之后的净值子段——预热段的表现不计入 OOS；
- OOS 汇总为逐窗 test 子段日收益的拼接（不跨窗平均指标）。

结果为统计事实，不构成买卖建议（红线 3）。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from app.market.backtest import BacktestConfig, Strategy, run_backtest
from app.market.performance import (
    TradePair,
    compute_performance,
    objective_direction,
    objective_value,
)

MIN_TRAIN_BARS = 60  # 引擎最低样本
MIN_TEST_BARS = 60  # OOS 段同样要够引擎跑（含 warmup 后净值子段仍须 ≥2）
MAX_GRID_COMBOS = 24  # 参数网格组合上限（防笛卡尔积爆炸）
DEFAULT_WARMUP = 30  # test 段回测预热根数（覆盖 ma 类最大默认周期）


@dataclass
class WFWindow:
    """单窗结果：样本内选参 + 样本外验证。"""

    train_len: int
    test_len: int
    test_start_ts: str
    test_end_ts: str
    best_params: dict
    objective_train: float  # 最优参数在 train 段的目标值
    train_metrics: dict  # 最优参数 train 段指标（28 项）
    test_metrics: dict  # 同参数 test 段指标（28 项，含 warmup 预热的回测、净值子段）


@dataclass
class WalkForwardReport:
    strategy_id: str
    objective: str
    windows: list[WFWindow] = field(default_factory=list)
    oos_metrics: dict = field(default_factory=dict)  # 全窗 OOS 拼接（曲线类为主）
    param_stability: dict = field(default_factory=dict)  # {"params_str": 被选窗数}
    notes: list[str] = field(default_factory=list)


def _grid_combos(param_grid: dict) -> list[dict]:
    if not param_grid:
        return [{}]
    keys = list(param_grid)
    combos = [
        dict(zip(keys, vals))
        for vals in itertools.product(*(param_grid[k] for k in keys))
    ]
    if len(combos) > MAX_GRID_COMBOS:
        raise ValueError(
            f"参数网格组合数 {len(combos)} 超上限 {MAX_GRID_COMBOS}（缩小网格）"
        )
    return combos


def _objective_better(a: float, b: float, direction: str) -> bool:
    """a 是否优于 b。max_drawdown 方向为 min（freqtrade MaxDrawDown 口径）。"""
    if direction == "min":
        return a < b
    return a > b


def walk_forward(
    bars: list[dict],
    strategy_factory,
    param_grid: dict,
    cfg: BacktestConfig | None = None,
    *,
    strategy_id: str = "",
    n_windows: int = 4,
    objective: str = "max_drawdown",
    warmup: int = DEFAULT_WARMUP,
) -> WalkForwardReport:
    """anchored Walk-Forward。

    strategy_factory(params) -> Strategy：与 app.market.backtest.build_strategy
    同构（由调用方绑定 strategy_id）。
    """
    cfg = cfg or BacktestConfig()
    n = len(bars)
    if n < 2 * MIN_TRAIN_BARS + MIN_TEST_BARS:
        raise ValueError(
            f"Walk-Forward 样本不足：{n} 根（至少 {2 * MIN_TRAIN_BARS + MIN_TEST_BARS} 根）"
        )
    if n_windows < 1:
        raise ValueError("n_windows 须 ≥1")
    direction = objective_direction(objective)  # 未知目标在此抛 ValueError
    combos = _grid_combos(param_grid)

    min_train = max(MIN_TRAIN_BARS, int(n * 0.5))
    step = (n - min_train) // n_windows
    if step < MIN_TEST_BARS:
        raise ValueError(
            f"每窗 test 段仅 {step} 根（<{MIN_TEST_BARS}）：加大样本或减少 n_windows"
        )

    windows: list[WFWindow] = []
    oos_returns: list[float] = []
    param_stability: dict[str, int] = {}

    for k in range(n_windows):
        test_start = min_train + k * step
        test_end = n if k == n_windows - 1 else min_train + (k + 1) * step
        train_bars = bars[:test_start]

        # ---- 样本内：参数网格逐个回测，按目标方向选最优 ----
        best_params: dict | None = None
        best_value = 0.0
        best_train_metrics: dict = {}
        for params in combos:
            strategy: Strategy = strategy_factory(params)
            rep = run_backtest(train_bars, strategy, cfg)
            m = rep.extra_metrics
            v = objective_value(m, objective)
            if best_params is None or _objective_better(v, best_value, direction):
                best_params, best_value, best_train_metrics = params, v, m
        assert best_params is not None  # combos 非空

        param_stability[str(best_params)] = param_stability.get(str(best_params), 0) + 1

        # ---- 样本外：同参数在 train+warmup 预热 + test 段回测，指标只取 test 子段 ----
        wstart = max(0, test_start - warmup)
        test_input = bars[wstart:test_end]
        strategy = strategy_factory(best_params)
        rep = run_backtest(test_input, strategy, cfg)
        cut = test_start - wstart  # 预热段在输入里的偏移
        seg = rep.equity[cut:]
        seg_pairs = [p for p in rep.pairs if p.entry_i >= cut]
        # 配对 index 平移到子段坐标（avg_holding_bars 口径不变）
        seg_pairs = [
            TradePair(
                entry_i=p.entry_i - cut, exit_i=p.exit_i - cut,
                entry_ts=p.entry_ts, exit_ts=p.exit_ts,
                cost=p.cost, proceeds=p.proceeds, pnl=p.pnl,
                pnl_pct=p.pnl_pct, reason=p.reason,
            )
            for p in seg_pairs
        ]
        test_metrics = compute_performance(seg, seg_pairs)
        if len(seg) < 2:
            test_metrics = {**test_metrics, "insufficient_test": 1}

        windows.append(
            WFWindow(
                train_len=len(train_bars),
                test_len=test_end - test_start,
                test_start_ts=str(bars[test_start]["ts"]),
                test_end_ts=str(bars[test_end - 1]["ts"]),
                best_params=best_params,
                objective_train=round(best_value, 6),
                train_metrics=best_train_metrics,
                test_metrics=test_metrics,
            )
        )
        if len(seg) >= 2:
            oos_returns.extend(
                seg[i] / seg[i - 1] - 1.0 for i in range(1, len(seg)) if seg[i - 1] > 0
            )

    # ---- OOS 拼接：日收益序列 → 重构净值（起点 1.0）→ 全套曲线指标 ----
    oos_equity = [1.0]
    for r in oos_returns:
        oos_equity.append(oos_equity[-1] * (1.0 + r))
    oos_metrics = compute_performance(oos_equity, None)

    notes = [
        f"anchored 扩张窗口 ×{n_windows}；样本内选参目标={objective}（{direction} 方向）；"
        f"test 段回测带 {warmup} 根预热、净值子段不计预热段",
        "OOS 汇总为逐窗 test 子段日收益拼接（不做跨窗指标平均）；交易统计以窗为单位",
        "参数稳健性：各窗选中参数的分布——集中说明参数稳定，分散说明该策略对参数敏感（过拟合信号）",
        "结果为统计事实，不构成买卖建议",
    ]
    return WalkForwardReport(
        strategy_id=strategy_id,
        objective=objective,
        windows=windows,
        oos_metrics=oos_metrics,
        param_stability=param_stability,
        notes=notes,
    )
