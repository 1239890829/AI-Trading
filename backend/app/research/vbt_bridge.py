"""vbt 研究加速桥（P0-1，§6.25）：vectorbt 的 A 股约束预处理层（纯 pandas，零 vbt 依赖）。

角色边界（2026-09-13 trading 分组复评裁定）：vectorbt 是**离线研究加速器**
（100 组参数网格实测首跑 1.9s / 二跑 ≈0s，数量级优于逐组循环），不是生产链——
它的撮合假设是「信号当 bar 收盘成交、无 T+1、无涨跌停、无整手」。
本模块用三件纯 pandas 的事把 vbt 的输入贴到 A 股语义（全部为**近似口径**，
与 paper engine 的硬拦截语义有已知偏差，消费方必须在输出里标注）：

1. :func:`adj_ohlc`——marketdb ``daily_k × daily_k_adj`` → 复权 OHLC
   （close_adj/close_price 比例法，与 evaluate.py 的 close_adj 同源同口径）；
2. :func:`limit_gate`——涨跌停门控：开盘价触及涨停线禁买、触及跌停线禁卖
   （保守过滤，与 KB-STOCK-31「可成交口径」同思想）；
3. :func:`shift_signals`——T+1 近似：T 收盘出信号 → T+1 才可成交；
   卖出信号再滞后一根 ⇒ 最早 T+2 卖出（排除当日买卖的 T+0 回路）；
4. :func:`naive_grid`——逐组循环的**真口径**对照实现（T+1 硬约束 + 涨跌停门 +
   整手），基准计时与 vbt 近似误差都用它对账。

vbt_free 刻意保持：backend venv（无 numba）即可 import 并单测；
真正调用 vbt 的只有 ``scripts/vbt_grid.py``（研究 venv `.venv-research`）。
"""
from __future__ import annotations

import pandas as pd

LIMIT_EPS = 0.002          # 涨跌停判定容差（与 backtest.py limit_eps 同思想）
LOT_SIZE = 100             # A 股整手
DEFAULT_FEE = 0.0003       # 佣金万三（单向）


# ---------------------------------------------------------------- 数据整形


def adj_ohlc(rows: list[tuple]) -> pd.DataFrame:
    """marketdb 行 (date_ms, open, high, low, close, close_adj, volume) → 复权 OHLCV。

    比例法：adj_x = x × close_adj/close_price；close_price=0（异常行）剔除。
    索引 = 北京日期（date_ms 按日锚，UTC+8 归一）。
    """
    recs = []
    for date_ms, o, h, low, c, ca, v in rows:
        if not c or not ca:
            continue
        k = ca / c
        ts = pd.Timestamp(date_ms, unit="ms", tz="UTC") + pd.Timedelta(hours=8)
        recs.append((ts.date(), o * k, h * k, low * k, ca, v))
    df = pd.DataFrame(recs, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    if df.empty:
        return df.set_index("Date")
    return df.drop_duplicates("Date").set_index("Date").sort_index()


# ---------------------------------------------------------------- 信号预处理


def limit_gate(
    entries: pd.DataFrame, exits: pd.DataFrame,
    open_: pd.Series | pd.DataFrame, close: pd.Series | pd.DataFrame,
    limit_pct: float = 0.10,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """涨跌停门控（就地过滤，不改传入对象）。

    开盘 ≥ 昨收×(1+limit_pct−eps) 的 bar 禁买（一字/开盘即板买不进）；
    开盘 ≤ 昨收×(1−limit_pct+eps) 的 bar 禁卖。prev_close 取 close.shift(1)。
    """
    prev = close.shift(1)
    buy_block = open_ >= prev * (1 + limit_pct - LIMIT_EPS)
    sell_block = open_ <= prev * (1 - limit_pct + LIMIT_EPS)
    # DataFrame & Series 会按列对齐（列名是组合元组，必炸）——走 numpy 行广播
    b = buy_block.reindex(entries.index).fillna(True).to_numpy()[:, None]
    s = sell_block.reindex(exits.index).fillna(True).to_numpy()[:, None]
    e_in = pd.DataFrame(entries.to_numpy() & ~b, index=entries.index, columns=entries.columns)
    e_out = pd.DataFrame(exits.to_numpy() & ~s, index=exits.index, columns=exits.columns)
    return e_in, e_out


def shift_signals(
    entries: pd.DataFrame, exits: pd.DataFrame, entry_lag: int = 1, exit_lag: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """T+1 近似：T 收盘出信号 → T+1 开盘成交（须配合 vbt ``price=open`` 使用）。

    entry/exit 同滞后一根：入场 bar 当日若同时出现出场信号，naive 语义下该出场
    要到 T+2 才执行（T+1 守卫）——vbt 近似无法表达这一根的时间差，属已知残差
    来源（量级见 vbt_grid --benchmark 逐组合对账）。"""
    return (entries.shift(entry_lag, fill_value=False),
            exits.shift(exit_lag, fill_value=False))


# ---------------------------------------------------------------- 真口径对照（逐组循环）


def naive_backtest(
    df: pd.DataFrame, entries: pd.Series, exits: pd.Series,
    *, cash: float = 100_000.0, fee: float = DEFAULT_FEE, limit_pct: float = 0.10,
) -> dict:
    """单标的 T+1 硬约束回测（对照口径）：逐 bar 循环。

    语义与 paper engine 对齐的子集：T 收盘出信号 → T+1 开盘可成交；当日买入的
    股份当日不可卖（held_since 守卫）；开盘触板禁买/禁卖；整手 100 股；
    佣金双边。返回 {ret, trades, days}。
    """
    o = df["Open"].values; c = df["Close"].values
    dates = df.index
    prev_c = pd.Series(c, index=df.index).shift(1).values
    ent = entries.reindex(df.index).fillna(False).values
    ext = exits.reindex(df.index).fillna(False).values
    pos = 0; cash_left = cash; last_buy_day = -1
    trades = 0
    equity = cash
    for i in range(1, len(df)):  # 第 0 根无昨收，跳过
        price_open = o[i]
        # 卖出判定：先于买入（腾资金），T+1 守卫 = last_buy_day < i
        if pos > 0 and ext[i] and last_buy_day < i:
            if not (prev_c[i] and price_open <= prev_c[i] * (1 - limit_pct + LIMIT_EPS)):
                cash_left += pos * price_open * (1 - fee)
                trades += 1; pos = 0
        if pos == 0 and ent[i]:
            if not (prev_c[i] and price_open >= prev_c[i] * (1 + limit_pct - LIMIT_EPS)):
                size = int(cash_left / (price_open * (1 + fee)) / LOT_SIZE) * LOT_SIZE
                if size >= LOT_SIZE:
                    pos = size
                    cash_left -= size * price_open * (1 + fee)
                    last_buy_day = i
        if pos > 0:
            equity = cash_left + pos * c[i]
    if pos > 0:
        equity = cash_left + pos * c[-1]
        trades += 1  # 期末强平计一笔
    return {"ret": round(equity / cash - 1, 6), "trades": trades, "days": len(dates)}


def naive_grid(
    df: pd.DataFrame, fast_range, slow_range, *, cash: float = 100_000.0, fee: float = DEFAULT_FEE,
) -> pd.DataFrame:
    """双均线网格的真口径基线（逐组 naive_backtest）。slow_range 须大于 fast。"""
    out = []
    for f in fast_range:
        for s in slow_range:
            if s <= f:
                continue
            ma_f = df["Close"].rolling(f).mean()
            ma_s = df["Close"].rolling(s).mean()
            ent = (ma_f > ma_s) & (ma_f.shift(1) <= ma_s.shift(1))
            ext = (ma_f < ma_s) & (ma_f.shift(1) >= ma_s.shift(1))
            r = naive_backtest(df, ent, ext, cash=cash, fee=fee)
            out.append({"fast": f, "slow": s, **r})
    return pd.DataFrame(out)
