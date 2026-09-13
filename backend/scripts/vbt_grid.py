"""vbt 研究网格加速器（P0-1，§6.25）——运行环境：backend/.venv-research（非生产 venv）。

用法：
    .venv-research/bin/python scripts/vbt_grid.py --symbol 600519 --bars 500 \
        --fast 3:13 --slow 10:60:5 [--benchmark] [--out /tmp/vbt_grid.csv]

做什么：从 marketdb 取复权 OHLC → 双均线网格（A 股预处理：涨跌停门控 + T+1 信号
滞后，见 app/research/vbt_bridge.py）→ vectorbt 列向量化一次算完全部组合；
`--benchmark` 同时跑 naive_grid 真口径对照（T+1 硬约束逐 bar 循环），输出
**耗时对比 + 逐组合近似误差**（vbt 近似口径 vs 真口径的收益差）。

⚠️ 输出的收益是**近似口径**（vbt 无排队成交/部分成交），只用于参数间的相对比较；
绝对收益以 backtest.py / paper engine 硬拦截口径为准。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402

from app.research import vbt_bridge  # noqa: E402

MARKETDB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"


def load_ohlc(symbol: str, bars: int) -> pd.DataFrame:
    """单 symbol 近 N 根复权 OHLC（daily_k × daily_k_adj，与 evaluate.py 同源）。"""
    if not MARKETDB.exists():
        raise SystemExit(f"marketdb 不存在：{MARKETDB}（先跑 scripts/sync_marketdb.py）")
    con = duckdb.connect(str(MARKETDB), read_only=True)
    try:
        rows = con.execute(
            "select a.date_ms, b.open_price, b.high_price, b.low_price, a.close_adj, b.close_price, b.volume "
            "from daily_k_adj as a join daily_k as b using (thscode, date_ms) "
            "where a.thscode = ? order by a.date_ms desc limit ?",
            [f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ", bars],
        ).fetchall()
    finally:
        con.close()
    return vbt_bridge.adj_ohlc(rows)


def grid_signals(df: pd.DataFrame, fasts, slows) -> tuple[pd.DataFrame, pd.DataFrame, list]:
    """双均线网格 → (entries, exits, combos)，列 = (fast, slow) 组合。"""
    combos = [(f, s) for f in fasts for s in slows if s > f]
    cols = [c for c in combos]
    ma_f = {c: df["Close"].rolling(c[0]).mean() for c in combos}
    ma_s = {c: df["Close"].rolling(c[1]).mean() for c in combos}
    entries = pd.DataFrame({c: (ma_f[c] > ma_s[c]) & (ma_f[c].shift(1) <= ma_s[c].shift(1)) for c in cols})
    exits = pd.DataFrame({c: (ma_f[c] < ma_s[c]) & (ma_f[c].shift(1) >= ma_s[c].shift(1)) for c in cols})
    return entries, exits, combos


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="600519")
    ap.add_argument("--bars", type=int, default=500)
    ap.add_argument("--fast", default="3:13")     # start:stop
    ap.add_argument("--slow", default="10:60:5")  # start:stop:step
    ap.add_argument("--benchmark", action="store_true", help="同时跑 naive 真口径对照")
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    import vectorbt as vbt

    fasts = range(*[int(x) for x in args.fast.split(":")])
    slows = range(*[int(x) for x in args.slow.split(":")])
    df = load_ohlc(args.symbol, args.bars)
    if df.empty:
        raise SystemExit(f"{args.symbol} 在 marketdb 无数据")
    entries, exits, combos = grid_signals(df, fasts, slows)
    entries, exits = vbt_bridge.limit_gate(entries, exits, df["Open"], df["Close"])
    entries, exits = vbt_bridge.shift_signals(entries, exits)

    t0 = time.time()
    pfs = vbt.Portfolio.from_signals(df["Close"], entries, exits,
                                     price=df["Open"],  # 开盘成交口径（与真口径对照对齐）
                                     init_cash=args.cash, fees=0.0003, slippage=0.0005)
    t_vbt = time.time() - t0
    rets = pfs.total_return()
    best = rets.idxmax()
    print(f"[vbt] {len(combos)} 组合 · {len(df)} 根 · 耗时 {t_vbt:.2f}s · "
          f"最优 {best} {rets[best]:+.2%} · 中位 {rets.median():+.2%}")

    if args.benchmark:
        t0 = time.time()
        naive = vbt_bridge.naive_grid(df, fasts, slows, cash=args.cash)
        t_naive = time.time() - t0
        naive["key"] = list(zip(naive["fast"].astype(int), naive["slow"].astype(int)))
        ret_map = {tuple(int(x) for x in k): v for k, v in zip(rets.index, rets.values)}
        naive["ret_vbt"] = naive["key"].map(lambda k: ret_map.get(k))
        naive["err_pp"] = (naive["ret_vbt"] - naive["ret"]) * 100
        t1 = time.time()
        vbt.Portfolio.from_signals(df["Close"], entries, exits,
                                   price=df["Open"],
                                   init_cash=args.cash, fees=0.0003, slippage=0.0005)
        t_vbt2 = time.time() - t1
        print(f"[naive 真口径] 耗时 {t_naive:.2f}s | [vbt 首跑] {t_vbt:.2f}s（含 numba 编译）| [vbt 二跑] {t_vbt2:.2f}s")
        print(f"[提速] vs naive 循环 {t_naive / max(t_vbt2, 1e-9):.1f}×（编译后）")
        print(f"[近似误差] |err| 中位 {naive['err_pp'].abs().median():.2f}pp · "
              f"最大 {naive['err_pp'].abs().max():.2f}pp（vbt 近似口径 vs T+1 硬约束）")
        out = naive.drop(columns=["key"])
    else:
        out = pd.DataFrame({"combo": [str(c) for c in rets.index], "ret_vbt": rets.values})
    if args.out:
        out.to_csv(args.out, index=False)
        print(f"已写出 {args.out}")


if __name__ == "__main__":
    main()
