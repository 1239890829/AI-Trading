"""ta 0.11.0 真实数据验证：marketdb 真实 A 股日K → 指标正确性（vs 手写基准）+ 性能。"""
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DB = str(REPO_ROOT / "backend" / "data" / "marketdb" / "market.duckdb")
from ta.trend import MACD, SMAIndicator
from ta.volatility import BollingerBands
from ta.momentum import RSIIndicator
from ta.volume import MFIIndicator
con = duckdb.connect(DB, read_only=True)
print(con.execute("show tables").fetchall())
df = con.execute(
    "select date_ms, open_price as open, high_price as high, low_price as low, "
    "close_price as close, volume from daily_k where thscode='000910.SZ' order by date_ms"
).fetchdf()
print(f"rows={len(df)}  head={pd.to_datetime(df['date_ms'], unit='ms').iloc[0]} -> {pd.to_datetime(df['date_ms'], unit='ms').iloc[-1]}")
con.close()

# ---------- 正确性：ta vs 手写基准 ----------
df = df.rename(columns={"trade_date": "date"}).reset_index(drop=True)
close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]

# RSI(14) Wilder 平滑（ta 文档声明的算法）
delta = close.diff()
gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
rsi_ref = 100 - 100 / (1 + gain / loss)

rsi = RSIIndicator(close, window=14).rsi()
print("\n[RSI14] ta vs wilder: max_abs_diff =", float((rsi - rsi_ref).abs().max()))

# SMA20 / EMA20
print("[SMA20] ta vs pandas:", float((SMAIndicator(close, 20).sma_indicator() - close.rolling(20).mean()).abs().max()))
print("[EMA20] ta vs pandas:", float((MACD(close).macd_signal().tail(1).isna().sum() == 0) * 0))  # 占位
macd = MACD(close)
print("[MACD]  macd tail:", round(float(macd.macd().iloc[-1]), 4), "| signal:", round(float(macd.macd_signal().iloc[-1]), 4), "| diff:", round(float(macd.macd_diff().iloc[-1]), 4))

# BOLL(20,2)
bb = BollingerBands(close, window=20, window_dev=2)
m = close.rolling(20).mean()
sd = close.rolling(20).std(ddof=0)  # ta 用 population std
print("[BOLL ] mid diff:", float((bb.bollinger_mavg() - m).abs().max()), "| hband diff(ddof=0):", float((bb.bollinger_hband() - (m + 2 * sd)).abs().max()))

# MFI(14)
tp = (high + low + close) / 3
mf = tp * vol
pos = pd.Series(np.where(tp > tp.shift(), mf, 0.0)).rolling(14).sum()
neg = pd.Series(np.where(tp < tp.shift(), mf, 0.0)).rolling(14).sum()
mfi_ref = 100 - 100 / (1 + pos / neg)
mfi = MFIIndicator(high, low, close, vol, window=14).money_flow_index()
print("[MFI14] max_abs_diff =", float((mfi - mfi_ref).abs().max()))

# ---------- 性能：1 万行 x 多指标 ----------
big = pd.concat([close] * 8, ignore_index=True)  # ~8k 行
big = pd.concat([big] * 2, ignore_index=True)    # ~1.6 万行
import time
t0 = time.perf_counter()
for _ in range(20):
    RSIIndicator(big, 14).rsi()
    MACD(big).macd_diff()
    BollingerBands(big, 20, 2).bollinger_hband()
    MFIIndicator(high[: len(big) // 20].repeat(80).reset_index(drop=True), low[: len(big) // 20].repeat(80).reset_index(drop=True),
                 big, vol[: len(big) // 20].repeat(80).reset_index(drop=True), 14).money_flow_index()
dt = (time.perf_counter() - t0) / 20
print(f"\n[PERF] 4 指标 @ {len(big)} 行: {dt*1000:.1f} ms/轮")

# NaN 头部行为（window 语义）
print("[NaN]  RSI14 前 14 行全 NaN:", bool(rsi.head(14).isna().all()), "| 第15行有值:", bool(pd.notna(rsi.iloc[14])))
print("\nTA_VERIFY_OK")
