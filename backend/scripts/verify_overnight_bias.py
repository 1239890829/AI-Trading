"""P1-34 实证核验：隔夜海外四输入 → A 股次日方向（是否成立、阈值敏感性、分年稳定性）。

⚠️ 本脚本 **import `app.market.overnight_bias` 的规则与常量**（`SPECS` / `evaluate`），
因此「核验得到的数字」与「线上实际跑的规则」不可能漂移——改常量即改核验口径。

一阶输入（**变化率一律自算**，不采信数据源自带的涨跌幅字段）：
  ① 纳指 .IXIC   ② 费城半导体 .SOX   ③ 美债 10Y 收益率   ④ 离岸 USDCNH
目标变量：上证指数 T 日 ①隔夜跳空（今开/昨收−1）②全天涨跌（今收/昨收−1）。

对齐口径：A 股 T 日开盘前**最新可得**的隔夜数据 = 日期**严格小于** T 的最后一行
（美股 T−1 日收盘于北京时间 T 日凌晨；离岸 CNH 为 24h 交易，按日线标签近似）。

纪律（KB-DEC-018）：产出是**实测分布**，不是「美债利率↑→A 股承压」式外推结论。
若某输入无稳定关系，如实报告「无边际信息」，不得用叙事强行确认为有效。

用法：cd backend && .venv/bin/python scripts/verify_overnight_bias.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import math

import akshare as ak
import pandas as pd
from curl_cffi import requests as ccr

from app.market import overnight_bias as ob

SINA_FX_DAYK = (
    "https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var%20_fx_{sym}=/"
    "NewForexService.getDayKLine?symbol=fx_{sym}"
)
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}


# ------------------------------------------------------------------ 取数

def us_index(symbol: str) -> pd.Series:
    df = ak.index_us_stock_sina(symbol=symbol)
    s = df.set_index(pd.to_datetime(df["date"]))["close"].astype(float)
    return s[~s.index.duplicated()].sort_index()


def us_treasury_10y() -> pd.Series:
    df = ak.bond_zh_us_rate(start_date="20140101")
    d = pd.to_datetime(df["日期"])
    s = pd.Series(df["美国国债收益率10年"].astype(float).values, index=d)
    return s.dropna().sort_index()


def usdcnh() -> pd.Series:
    resp = ccr.get(SINA_FX_DAYK.format(sym="susdcnh"), headers=SINA_HEADERS,
                   impersonate="chrome110", timeout=20)
    resp.raise_for_status()
    text = resp.text
    body = text[text.index('("') + 2: text.rindex('")')]
    idx, vals = [], []
    for chunk in body.split("|"):
        parts = chunk.split(",")
        if len(parts) < 5 or not parts[4].strip():
            continue
        idx.append(pd.to_datetime(parts[0]))
        vals.append(float(parts[4]))
    s = pd.Series(vals, index=pd.DatetimeIndex(idx))
    return s[~s.index.duplicated()].sort_index()


def a_share_index() -> pd.DataFrame:
    df = ak.stock_zh_index_daily(symbol="sh000001").copy()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index().astype(float)


# ------------------------------------------------------------------ 统计工具

def t_stat(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return None
    return m / math.sqrt(var / n)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    vy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return cov / (vx * vy) if vx and vy else None


def spearman(xs: list[float], ys: list[float]) -> float | None:
    def rank(vs: list[float]) -> list[float]:
        order = sorted(range(len(vs)), key=lambda i: vs[i])
        rk = [0.0] * len(vs)
        for pos, i in enumerate(order):
            rk[i] = float(pos + 1)
        return rk

    return pearson(rank(xs), rank(ys))


def fmt(v, nd=3):
    return "—" if v is None else f"{v:+.{nd}f}"


def change_of(spec, pts: list[dict]) -> float:
    """复用模块内的口径（单点收口）——核验与生产不可能算出两个数。"""
    return ob.change_of(spec, pts[-2]["close"], pts[-1]["close"])


# ------------------------------------------------------------------ 主流程

def main() -> None:
    print("=" * 100)
    print("P1-34 实证核验：隔夜海外四输入 → A 股次日方向")
    print("=" * 100)

    raw = {
        "nasdaq": us_index(".IXIC"),
        "sox": us_index(".SOX"),
        "us10y": us_treasury_10y(),
        "usdcnh": usdcnh(),
    }
    a = a_share_index()
    for k, s in raw.items():
        print(f"  取数 {k:8s} n={len(s):6d}  {s.index[0].date()} ~ {s.index[-1].date()}")

    a_open_chg = (a["open"] / a["close"].shift(1) - 1).dropna()
    a_day_chg = (a["close"] / a["close"].shift(1) - 1).dropna()

    # 对齐：A 股 T 日取「日期严格小于 T」的最后两个有效点（前一个用于算变化）
    rows = []
    for d in a_open_chg.index:
        if d not in a_day_chg.index:
            continue
        rec = {"date": d, "open_chg": float(a_open_chg[d]), "day_chg": float(a_day_chg[d])}
        series: dict[str, list[dict]] = {}
        ok = True
        for k, s in raw.items():
            prior = s[s.index < d].dropna()
            if len(prior) < 2:
                ok = False
                break
            series[k] = [
                {"date": prior.index[-2].date().isoformat(), "close": float(prior.iloc[-2])},
                {"date": prior.index[-1].date().isoformat(), "close": float(prior.iloc[-1])},
            ]
        if not ok:
            continue
        rec["series"] = series
        for spec in ob.SPECS:
            rec[spec.key] = change_of(spec, series[spec.key])
        rows.append(rec)

    df = pd.DataFrame(rows)
    print(f"\n对齐样本：n={len(df)}  {df['date'].min().date()} ~ {df['date'].max().date()}")
    last = df.iloc[-1]
    print("输入最新可得日（末行）：" + " / ".join(
        f"{k} {last['series'][k][-1]['date']}" for k in raw))

    print("\n" + "-" * 100)
    print("① 单输入与 A 股开盘跳空 / 全天涨跌的相关性（★ = 模块启用权重>0）")
    print("-" * 100)
    for spec in ob.SPECS:
        k = spec.key
        xs = df[k].tolist()
        star = "★" if spec.weight > 0 else " "
        print(f" {star}{spec.label:22s} vs 开盘: pearson={fmt(pearson(xs, df['open_chg'].tolist()))} "
              f"spearman={fmt(spearman(xs, df['open_chg'].tolist()))} | "
              f"vs 全天: pearson={fmt(pearson(xs, df['day_chg'].tolist()))} "
              f"spearman={fmt(spearman(xs, df['day_chg'].tolist()))}")

    print("\n" + "-" * 100)
    print("② 按输入方向分组（目标 = A 股当日涨跌，正=上涨；『平』= 落在模块死区内）")
    print("-" * 100)
    for spec in ob.SPECS:
        k = spec.key
        chg = df[k].tolist()
        df[f"{k}_chg"] = chg
        n_up = sum(1 for c in chg if c > spec.dead_zone)
        n_mid = sum(1 for c in chg if abs(c) <= spec.dead_zone)
        n_dn = sum(1 for c in chg if c < -spec.dead_zone)
        med = sorted(abs(c) for c in chg)[len(chg) // 2]
        print(f"  [自检] {spec.label:20s} 升{n_up:5d} 平{n_mid:5d} 降{n_dn:5d} "
              f"|Δ|中位={med:.3f}{spec.unit}")
        buckets: dict[str, list[float]] = {"输入升": [], "输入平": [], "输入降": []}
        for c, t in zip(chg, df["day_chg"]):
            key = "输入升" if c > spec.dead_zone else ("输入降" if c < -spec.dead_zone else "输入平")
            buckets[key].append(t)
        parts = []
        for name in ("输入升", "输入平", "输入降"):
            vs = buckets[name]
            if not vs:
                parts.append(f"{name}: n=0")
                continue
            win = sum(1 for v in vs if v > 0) / len(vs)
            parts.append(f"{name}: n={len(vs):4d} 均值{sum(vs)/len(vs)*100:+.3f}% 胜率{win*100:.1f}%")
        print(f"  {spec.label:22s} " + " | ".join(parts))

    print("\n" + "-" * 100)
    print("③ 死区敏感性（各输入按**自身**死区的 0.5×/1×/2× 缩放，看升−降 全天均值差）")
    print("-" * 100)
    for mult in (0.5, 1.0, 2.0):
        line = []
        for spec in ob.SPECS:
            k, dz = spec.key, spec.dead_zone * mult
            up = [t for c, t in zip(df[f"{k}_chg"], df["day_chg"]) if c > dz]
            dn = [t for c, t in zip(df[f"{k}_chg"], df["day_chg"]) if c < -dz]
            if len(up) >= 20 and len(dn) >= 20:
                diff = (sum(up) / len(up) - sum(dn) / len(dn)) * 100
                line.append(f"{k} Δ={diff:+.3f}pp(n={len(up)}/{len(dn)})")
        print(f"  {mult:>3.1f}×  " + " | ".join(line))

    print("\n" + "-" * 100)
    print("④ 线上规则实测（**直接调 `overnight_bias.evaluate`**，与生产同一份代码）")
    print("-" * 100)
    stances, scores, day, opn = [], [], [], []
    for r in df.itertuples():
        res = ob.evaluate(getattr(r, "series"), brief_date=r.date.date())
        stances.append(res["stance"])
        scores.append(res["score"])
        day.append(r.day_chg)
        opn.append(r.open_chg)
    sdf = pd.DataFrame({"date": df["date"], "stance": stances, "score": scores,
                        "day": day, "open": opn})
    print("  三态占比：", end="")
    print(" | ".join(f"{s}: {c} ({c/len(sdf)*100:.1f}%)"
                     for s, c in sdf["stance"].value_counts().items()))
    print(f"  未判定：{sdf['stance'].isna().sum()}")
    print(f"  {'档位':6s} {'n':>5s} {'全天均值':>10s} {'全天胜率':>9s} {'开盘均值':>10s} {'开盘胜率':>9s}")
    for st in ("走强", "中性", "承压"):
        g = sdf[sdf["stance"] == st]
        if g.empty:
            continue
        print(f"  {st:6s} {len(g):5d} {g['day'].mean()*100:+9.3f}% "
              f"{(g['day'] > 0).mean()*100:8.1f}% {g['open'].mean()*100:+9.3f}% "
              f"{(g['open'] > 0).mean()*100:8.1f}%")
    up = sdf[sdf["stance"] == "走强"]["day"].tolist()
    dn = sdf[sdf["stance"] == "承压"]["day"].tolist()
    upo = sdf[sdf["stance"] == "走强"]["open"].tolist()
    dno = sdf[sdf["stance"] == "承压"]["open"].tolist()
    if up and dn:
        print(f"  走强−承压：全天 {(sum(up)/len(up)-sum(dn)/len(dn))*100:+.3f}pp "
              f"(t={fmt(t_stat(up))} / {fmt(t_stat(dn))})")
    if upo and dno:
        win_up = sum(1 for v in upo if v > 0) / len(upo)
        win_dn = sum(1 for v in dno if v > 0) / len(dno)
        print(f"             开盘 {(sum(upo)/len(upo)-sum(dno)/len(dno))*100:+.3f}pp "
              f"(开盘胜率 走强 {win_up*100:.1f}% vs 承压 {win_dn*100:.1f}%)")

    print("\n" + "-" * 100)
    print("⑤ 分年度稳定性（走强组 − 承压组，全天口径）")
    print("-" * 100)
    sdf["year"] = sdf["date"].dt.year
    print(f"  {'年份':6s} {'走强n':>6s} {'走强均值':>10s} {'承压n':>6s} {'承压均值':>10s} {'差值(pp)':>10s}")
    pos_years = tot_years = 0
    for y, g in sdf.groupby("year"):
        p = g[g["stance"] == "走强"]["day"]
        n = g[g["stance"] == "承压"]["day"]
        if len(p) >= 10 and len(n) >= 10:
            d = (p.mean() - n.mean()) * 100
            tot_years += 1
            pos_years += 1 if d > 0 else 0
            print(f"  {y:<6d} {len(p):6d} {p.mean()*100:9.3f}% {len(n):6d} {n.mean()*100:9.3f}% {d:+9.3f}")
    print(f"  方向一致性：{pos_years}/{tot_years} 年 走强组优于承压组")


if __name__ == "__main__":
    main()
