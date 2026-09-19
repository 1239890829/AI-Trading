"""akquant 0.3.58 真实数据验证：回测链路 + talib 指标质量（vs ta/wilder）+ 因子引擎 + akshare 联动。"""
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DB = str(REPO_ROOT / "backend" / "data" / "marketdb" / "market.duckdb")
con = duckdb.connect(DB, read_only=True)
df = con.execute(
    "select date_ms, open_price as open, high_price as high, low_price as low, "
    "close_price as close, volume from daily_k where thscode='000910.SZ' order by date_ms"
).fetchdf()
df["date"] = pd.to_datetime(df["date_ms"], unit="ms").dt.tz_localize(None)
con.close()
print(f"marketdb rows={len(df)}")

# ---------- 1. akquant.talib 指标质量（Rust 后端 vs 手写 Wilder）----------
import akquant as aq

close_np = df["close"].to_numpy(dtype=np.float64)
print("\n[AQ-RSI] attr RSI exists:", hasattr(aq, "RSI"))

# 手写 Wilder 基准（TA-Lib C 语义：前14行 SMA 预热起步）
delta = df["close"].diff()
up = delta.clip(lower=0.0)
dn = -delta.clip(upper=0.0)
up14 = up.iloc[1:15].mean()
dn14 = dn.iloc[1:15].mean()
rsi_wilder = [np.nan] * 15
ag, dg = up14, dn14
for d_up, d_dn in zip(up.iloc[15:], dn.iloc[15:], strict=False):
    ag = (ag * 13 + d_up) / 14
    dg = (dg * 13 + d_dn) / 14
    rsi_wilder.append(100.0 - 100.0 / (1 + ag / dg) if dg != 0 else 100.0)
rsi_ref = pd.Series(rsi_wilder + [np.nan] * (len(df) - len(rsi_wilder)), index=df.index)

from ta.momentum import RSIIndicator as TaRSI

from akquant.talib import RSI as AqRSI  # wrapper 函数（顶层 aq.RSI 是 Rust sink 类）

rsi_ta = TaRSI(df["close"], 14).rsi()
rsi_aq_py = AqRSI(df["close"], 14, backend="python")
rsi_aq_rust = AqRSI(df["close"], 14, backend="rust")
print(f"[RSI] wrapper 返回类型: python={type(rsi_aq_py).__name__} rust={type(rsi_aq_rust).__name__}")

def _as_series(x):
    return x if isinstance(x, pd.Series) else pd.Series(np.asarray(x, dtype=np.float64))

for name, s in (("ta", rsi_ta), ("akquant.py", _as_series(rsi_aq_py)), ("akquant.rs", _as_series(rsi_aq_rust))):
    tail = (s - rsi_ref).iloc[200:]
    head = (s - rsi_ref).iloc[15:60].abs().max()
    print(f"[RSI vs TA-Lib C 基准] {name:11s} 200行后 max_abs_diff={float(tail.abs().max()):.4f} | 头部(15-60行)偏差={float(head):.2f}")

# ---------- 2. 完整回测：marketdb 数据 → Strategy 子类 ----------
class MeanRev(Strategy := __import__("akquant").Strategy):
    def on_bar(self, bar):
        pos = self.get_position(bar.symbol)
        if pos == 0 and bar.close < bar.open:  # 阴线买入
            self.buy(symbol=bar.symbol, quantity=100)
        elif pos > 0 and bar.close > bar.open:  # 阳线卖出
            self.close_position(symbol=bar.symbol)

bt_data = df[["date", "open", "high", "low", "close", "volume"]].copy()
t0 = time.perf_counter()
result = aq.run_backtest(data=bt_data, strategy=MeanRev, initial_cash=100_000.0)
dt = time.perf_counter() - t0
print(f"\n[BACKTEST] {len(bt_data)} bars in {dt*1000:.1f} ms")
print("[BACKTEST] result attrs:", [a for a in dir(result) if not a.startswith("_")][:25])
for k in ("total_return", "annual_return", "sharpe", "max_drawdown", "win_rate", "trade_count"):
    if hasattr(result, k):
        print(f"  {k} = {getattr(result, k)}")

# ---------- 3. 因子表达式引擎 ----------
try:
    print("\n[FACTOR] module:", [a for a in dir(aq.factor) if not a.startswith("_")][:10])
except Exception as e:
    print("factor import err:", e)

# ---------- 4. 参数优化（小网格）----------
try:
    import akquant.optimize as opt
    print("\n[OPT] attrs:", [a for a in dir(opt) if not a.startswith("_")][:10])
except Exception as e:
    print("opt err:", e)

# ---------- 5. akshare 联动（README 路径实测）----------
try:
    import akshare as ak
    ak_df = ak.stock_zh_a_daily(symbol="sh600000", start_date="20250212", end_date="20260212")
    print(f"\n[AKSHARE] stock_zh_a_daily rows={len(ak_df)} cols={list(ak_df.columns)[:8]}")
    class Ak(Strategy):
        def on_bar(self, bar):
            pos = self.get_position(bar.symbol)
            if pos == 0 and bar.close > bar.open:
                self.buy(symbol=bar.symbol, quantity=100)
            elif pos > 0 and bar.close < bar.open:
                self.close_position(symbol=bar.symbol)
    r2 = aq.run_backtest(data=ak_df, strategy=Ak, initial_cash=100_000.0)
    print("[AKSHARE->BACKTEST] ok, total_return:", getattr(r2, "total_return", "n/a"))
except Exception as e:
    print("[AKSHARE] err:", type(e).__name__, str(e)[:200])

print("\nAKQUANT_VERIFY_OK")
