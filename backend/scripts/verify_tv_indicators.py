"""TradingView 流行指标主张的本仓实证（retro §6.22，KB-STOCK-33；可复跑，只读）。

对象（外部流行主张，2026-09-13 转述）：①EMA200 大周期过滤提升突破质量；②MACD 零轴
上方金叉更强（tech_score 已实现该假设，本脚本为其补实证背书）；③三重共振
（MACD 金叉 ∧ RSI<30 ∧ 放量）优于单条件（Pine 示例买入条件）；④ADX≥25 强趋势过滤
（"只做强趋势，过滤 90% 震荡"）。Supertrend 多参数共振不单独验证——与③的"共振
是否增信息"问题同构，由 C 组结论外推。

方法：marketdb 10y 全 A → pandas 分组算指标（EMA/MACD/RSI14-Wilder/ADX14-Wilder，
与主流平台同参数）→ 事件研究（触发 vs 同日全市场中位数中性化，strategy_verify 同法）。
口径：T+1/T+3 = 复权收盘比（信号强度口径）；双口径（全样本 / 剔主板当日近涨停 ≥9.5%）；
分年度同号（参照 YEAR_CONSISTENCY_MIN=0.60）。样本 <120 组不谈方向。
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.bjtime import beijing_today  # noqa: E402

DB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"
HH_WINDOW = 10
LIMIT_PCT = 0.095
YEAR_CONSISTENCY_MIN = 0.60


def load_df() -> pd.DataFrame:
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute(
        """
        SELECT k.thscode, k.date_ms, k.high_price AS high, k.low_price AS low,
               k.close_price AS close_raw, k.volume, a.close_adj,
               COUNT(*) OVER (PARTITION BY k.thscode ORDER BY k.date_ms
                              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cnt
        FROM daily_k k JOIN daily_k_adj a USING (thscode, date_ms)
        WHERE k.volume > 0 AND a.close_adj > 0
        ORDER BY k.thscode, k.date_ms
        """
    ).fetchdf()
    con.close()
    df["d"] = pd.to_datetime(df["date_ms"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.date
    df["ret0"] = df.groupby("thscode")["close_adj"].pct_change()
    for h in (1, 3):
        # fwd_h：T 收盘进 → T+h 收盘出（相邻交易日步进，无日历缺口问题）
        df[f"fwd{h}"] = df.groupby("thscode")["close_adj"].shift(-h) / df["close_adj"] - 1
    return df


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def add_indicators(g: pd.DataFrame) -> pd.DataFrame:
    """单股指标：EMA200 / MACD(12,26,9) / RSI14(Wilder) / ADX14(Wilder) / v5 量比。"""
    c = g["close_adj"]
    g["ema200"] = c.ewm(span=200, adjust=False, min_periods=200).mean()
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    g["dif"], g["dea"] = dif, dea
    g["macd_cross_up"] = (dif.shift(1) <= dea.shift(1)) & (dif > dea)
    # RSI14（Wilder）
    chg = c.diff()
    up, dn = chg.clip(lower=0), (-chg).clip(lower=0)
    rs = wilder(up, 14) / wilder(dn, 14).replace(0, np.nan)
    g["rsi14"] = 100 - 100 / (1 + rs)
    # ADX14（Wilder）：+DM/-DM → TR 平滑 → DX → ADX
    up_move = g["high"].diff()
    down_move = -g["low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=g.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=g.index)
    tr = pd.concat([
        g["high"] - g["low"],
        (g["high"] - c.shift(1)).abs(),
        (g["low"] - c.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = wilder(tr, 14).replace(0, np.nan)
    pdi = 100 * wilder(plus_dm, 14) / atr
    mdi = 100 * wilder(minus_dm, 14) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    g["adx14"] = wilder(dx.fillna(0), 14)
    # v5 量比（与评分/验证同基线：过去 5 日均量，不含当日；g 已是单股时序）
    g["v5"] = g["volume"] / g["volume"].shift(1).rolling(5).mean()
    g["hh10"] = c.shift(1).rolling(HH_WINDOW).max()
    return g


def stats(sub: pd.DataFrame, col: str) -> dict:
    xs = sub[col].dropna()
    n = len(xs)
    if n < 30:
        return {"n": n}
    m = xs.mean()
    t = m / xs.std(ddof=1) * np.sqrt(n) if xs.std(ddof=1) else 0.0
    yearly = sub.dropna(subset=[col]).groupby("year")[col].mean()
    pos = (yearly > 0).sum() if m > 0 else (yearly < 0).sum()
    return {"n": n, "m": m, "t": t, "years_pos": f"{pos}/{len(yearly)}"}


def neutralize(df: pd.DataFrame, col: str) -> pd.Series:
    """T+h 收益减同日全市场中位数（市场中性，strategy_verify 同法）。"""
    return df[col] - df.groupby("d")[col].transform("median")


def report(df: pd.DataFrame) -> None:
    # 防爆过滤（与其他验证脚本同口径）：|fwd|≥50% 视为复权数据错误（退市残值比率爆炸），
    # 不滤会把 std 炸成 inf、t 归零
    df = df[(df["cnt"] >= 60) & df["fwd1"].abs().lt(0.5) & df["fwd3"].abs().lt(0.5)].copy()
    df["year"] = pd.to_datetime(df["d"]).dt.year
    for h in (1, 3):
        df[f"ex{h}"] = neutralize(df, f"fwd{h}")

    def line(name: str, sub: pd.DataFrame, n_min: int = 120) -> str:
        s1, s3 = stats(sub, "ex1"), stats(sub, "ex3")
        if s1.get("n", 0) < n_min:
            return f"| {name} | {s1.get('n', 0):,} | — | — | — | — |"
        return (f"| {name} | {s1['n']:,} | {s1['m'] * 100:+.3f}% | {s1['t']:+.1f} | "
                f"{s3['m'] * 100:+.3f}% | {s3['t']:+.1f} |")

    print(f"## TradingView 流行指标实证（marketdb 10y，{beijing_today().isoformat()}）\n")
    print(f"- 事件基线：突破 = T 收盘 > 近 {HH_WINDOW} 日最高收盘；MACD 金叉 = DIF 上穿 DEA；"
          "超额 = T+h 收盘收益 − 同日全市场中位；口径 = 信号强度（T 收盘进）\n")

    for label, base in (("全样本", df), ("剔除当日近涨停（主板）",
                          df[~((df["ret0"] >= LIMIT_PCT) & ~df["thscode"].str.startswith(("30", "68")))])):
        b = base[base["close_adj"] > base["hh10"]]
        cu = base[base["macd_cross_up"]]
        reso = base[base["macd_cross_up"] & (base["rsi14"] < 30) & (base["v5"] > 1)]
        print(f"### {label}\n")
        print("| 组 | n | T+1 超额 | t | T+3 超额 | t |")
        print("|---|---|---|---|---|---|")
        print("**(A) EMA200 大周期过滤（突破事件分层）**")
        print(line("突破 & close>EMA200", b[b["close_adj"] > b["ema200"]]))
        print(line("突破 & close<EMA200", b[b["close_adj"] <= b["ema200"]]))
        print("**(B) MACD 金叉 × 零轴位置**")
        print(line("金叉 & DIF>0（零轴上）", cu[cu["dif"] > 0]))
        print(line("金叉 & DIF≤0（零轴下）", cu[cu["dif"] <= 0]))
        print("**(C) 三重共振 vs 单条件**")
        print(line("MACD金叉 ∧ RSI<30 ∧ 放量", reso))
        print(line("仅 MACD 金叉（对照）", cu))
        print("**(D) ADX 趋势强度过滤（突破事件分层）**")
        print(line("突破 & ADX≥25", b[b["adx14"] >= 25]))
        print(line("突破 & ADX<25", b[b["adx14"] < 25]))
        print()

    # 分年度稳健性（全样本口径的关键组）
    print("### 分年度 T+1 超额同号（全样本口径）\n")
    key_groups = {
        "突破 & close>EMA200": df[(df["close_adj"] > df["hh10"]) & (df["close_adj"] > df["ema200"])],
        "金叉 & DIF>0": df[df["macd_cross_up"] & (df["dif"] > 0)],
        "三重共振": df[df["macd_cross_up"] & (df["rsi14"] < 30) & (df["v5"] > 1)],
        "突破 & ADX≥25": df[(df["close_adj"] > df["hh10"]) & (df["adx14"] >= 25)],
    }
    for name, sub in key_groups.items():
        s = stats(sub, "ex1")
        if s.get("n", 0) >= 120:
            flag = "✅" if int(s["years_pos"].split("/")[0]) / int(s["years_pos"].split("/")[1]) >= YEAR_CONSISTENCY_MIN else "⚠️"
            print(f"- {flag} {name}：{s['years_pos']} 年度 T+1 超额与全期同号")

    print("\n> 口径声明：指标参数与主流平台一致（EMA200 / MACD 12,26,9 / RSI14 / ADX14）；"
          "Wilder 平滑（ewm alpha=1/n）与 TradingView 默认一致；收益为信号强度口径（不含费用）；"
          "ST 未还原；事件非独立（t 作组间相对比较）；Supertrend 多参数共振与 (C) 同构未单独验证。")


def main() -> int:
    if not DB.exists():
        print(f"ERROR: marketdb 不存在：{DB}", file=sys.stderr)
        return 1
    print("① 拉取日 K（10y 全 A）...", file=sys.stderr)
    df = load_df()
    print(f"   {len(df):,} 行", file=sys.stderr)
    print("② 分组算指标（EMA200/MACD/RSI14/ADX14/v5）...", file=sys.stderr)
    df = (
        df.groupby("thscode", group_keys=False, sort=False)[
            ["thscode", "date_ms", "d", "high", "low", "close_adj", "volume", "cnt", "ret0", "fwd1", "fwd3"]
        ]
        .apply(add_indicators)
    )
    print("③ 事件研究...", file=sys.stderr)
    report(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
