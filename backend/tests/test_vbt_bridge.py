"""vbt_bridge 纯函数单测（P0-1，§6.25）——backend venv 可跑（零 vbt 依赖）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.research import vbt_bridge as vb


def _df(n=30, price=10.0):
    idx = pd.bdate_range("2026-01-01", periods=n)
    close = np.full(n, price)
    df = pd.DataFrame({
        "Open": close, "High": close, "Low": close, "Close": close,
        "Volume": 1_000_000.0,
    }, index=idx)
    return df


# ---------------------------------------------------------------- adj_ohlc


def test_adj_ohlc_ratio_and_skip():
    rows = [
        (1704067200000, 10.0, 11.0, 9.0, 10.0, 5.0, 1000.0),   # 1:1 → adj=5
        (1704153600000, 20.0, 22.0, 18.0, 20.0, 10.0, 2000.0),  # 1:1 → adj=10
        (1704240000000, 0.0, 0.0, 0.0, 0.0, 0.0, 1000.0),      # close=0 → 剔除
    ]
    out = vb.adj_ohlc(rows)
    assert len(out) == 2
    assert out.iloc[0]["Close"] == 5.0 and out.iloc[1]["Open"] == 10.0  # 20×(10/20)
    assert out.index.is_monotonic_increasing


# ---------------------------------------------------------------- limit_gate


def test_limit_gate_blocks_buy_at_limit_open():
    df = _df()
    df.loc[df.index[5], "Open"] = df["Close"].iloc[4] * 1.099 + 0.001  # 开盘≈涨停
    entries = pd.DataFrame({"(5, 20)": True}, index=df.index)
    exits = pd.DataFrame({"(5, 20)": False}, index=df.index)
    e_in, e_out = vb.limit_gate(entries, exits, df["Open"], df["Close"])
    assert bool(e_in.iloc[5, 0]) is False            # 涨停开盘禁买
    assert bool(e_in.iloc[6, 0]) is True             # 正常 bar 不受影响


def test_limit_gate_blocks_sell_at_floor_open():
    df = _df()
    df.loc[df.index[5], "Open"] = df["Close"].iloc[4] * 0.901 - 0.001  # 开盘≈跌停
    exits = pd.DataFrame({"(5, 20)": True}, index=df.index)
    e_in, e_out = vb.limit_gate(pd.DataFrame({"(5, 20)": True}, index=df.index), exits,
                                df["Open"], df["Close"])
    assert bool(e_out.iloc[5, 0]) is False           # 跌停开盘禁卖


# ---------------------------------------------------------------- shift_signals


def test_shift_signals_t_plus_one():
    idx = pd.bdate_range("2026-01-01", periods=5)
    entries = pd.DataFrame({"a": [True, False, True, False, False]}, index=idx)
    exits = pd.DataFrame({"a": [False, True, False, False, False]}, index=idx)
    e_in, e_out = vb.shift_signals(entries, exits)
    # T 收盘信号 → T+1 生效
    assert list(e_in["a"]) == [False, True, False, True, False]
    assert list(e_out["a"]) == [False, False, True, False, False]


# ---------------------------------------------------------------- naive_backtest


def test_naive_backtest_t1_guard_and_lot():
    """T 收盘出信号 → T+1 开盘买；整手向下取整；当日买不可当日卖。"""
    n = 10
    idx = pd.bdate_range("2026-01-01", periods=n)
    # 价格 9.9 → 10 万现金约可买 1010 股 → 整手 1000 股
    price = np.full(n, 9.9)
    df = pd.DataFrame({"Open": price, "High": price, "Low": price, "Close": price,
                       "Volume": 1.0}, index=idx)
    ent = pd.Series([False, True] + [False] * 8, index=idx)   # 1 日收盘信号 → 2 日开盘买
    ext = pd.Series([False] * 8 + [True, False], index=idx)   # 8 日收盘信号 → 9 日开盘卖
    r = vb.naive_backtest(df, ent, ext, cash=10_000.0, fee=0.0)
    assert r["trades"] == 1   # 一个完成回环（trades 计回环数，非成交笔数）
    assert r["ret"] == 0.0    # 同价进出、零费用 → 持平


def test_naive_backtest_cannot_sell_same_day():
    """T+1 硬约束：买入 bar 的同 bar 卖出信号不得当日执行（用价差使结果可判别）。"""
    n = 6
    idx = pd.bdate_range("2026-01-01", periods=n)
    open_ = np.full(n, 9.9)
    close = np.full(n, 9.9)
    close[2:] = 11.0                       # 买入后价格上行到 11
    df = pd.DataFrame({"Open": open_, "High": np.maximum(open_, close),
                       "Low": np.minimum(open_, close), "Close": close,
                       "Volume": 1.0}, index=idx)
    ent = pd.Series([False, True, False, False, False, False], index=idx)   # bar1 收盘买 → bar2 开盘成交
    ext = pd.Series([False, True, False, False, False, False], index=idx)   # bar1 同 bar 卖出信号
    r = vb.naive_backtest(df, ent, ext, cash=10_000.0, fee=0.0)
    # T+1 守卫生效 ⇒ 持仓到期末以 close=11 强平 → ret = (100 + 1000×11)/10000 − 1 = +11%
    # 若同日卖出被允许（在 9.9 平仓）⇒ ret = 0——两者可判别
    assert r["ret"] == pytest.approx(0.11, abs=1e-6)
    assert r["trades"] == 1


def test_naive_grid_shape():
    df = _df(60)
    out = vb.naive_grid(df, range(3, 6), range(10, 21, 5))
    assert {"fast", "slow", "ret", "trades"} <= set(out.columns)
    assert (out["slow"] > out["fast"]).all()
