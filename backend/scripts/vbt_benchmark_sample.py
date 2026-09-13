"""分层随机篮子基准（P0-1 补强，§6.25）：单票对照偏差的修正实验。

动机（2026-09-13 用户指正）：vbt 近似误差的对照此前只用了 600519（全场最贵）与
000001（超低波大行）两个极端个例——n=1/2 无法区分「口径误差」与「个股特异性」。
本脚本做**分层随机篮子**：按价格三分层随机抽样（seed 固定可复现），每票同一组
预处理信号喂两个引擎（修掉此前 vbt 用门控+滞后信号、naive 用原始信号的**信号
不一致 bug**），输出：

- 逐票：近似误差中位 |err|、Spearman 保序相关、前 20% 重合率；
- 汇总：误差分布（全篮子分位）、**误差 vs 价格水平**相关（验证整手假设）、
  分层明细（低价/中价/高价）；
- 裁定输入：vbt 近似口径「粗筛可用性」的最终判定依据。

运行环境：backend/.venv-research。
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.research import vbt_bridge  # noqa: E402
from scripts.vbt_grid import grid_signals, load_ohlc  # noqa: E402

MARKETDB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"


def pick_universe(bars: int, n: int, seed: int) -> list[tuple[str, float]]:
    """有足够历史的股票里按**最新价格三分层**随机抽样（低价 ≤10 / 中价 10-50 / 高价 >50）。

    返回 [(symbol, last_price)]。分层的原因：近似误差的第一假设是整手粒度
    （= 价格×100/现金占比），必须覆盖价格谱系而不是全随机（否则中等价格占多数、
    两端样本不足）。
    """
    con = duckdb.connect(str(MARKETDB), read_only=True)
    try:
        rows = con.execute(
            "with l as (select thscode, count(*) as n, "
            "  arg_max(b.close_price, a.date_ms) as last_close "
            "from daily_k_adj a join daily_k b using (thscode, date_ms) "
            "group by thscode having count(*) >= ?) "
            "select thscode, last_close from l", [bars]
        ).fetchall()
    finally:
        con.close()
    buckets: dict[str, list] = {"low": [], "mid": [], "high": []}
    for code, px in rows:
        if not px or px <= 0:
            continue
        key = "low" if px <= 10 else ("mid" if px <= 50 else "high")
        buckets[key].append((code, float(px)))
    rng = random.Random(seed)
    per = n // 3
    out: list[tuple[str, float]] = []
    for key in ("low", "mid", "high"):
        pool = buckets[key]
        rng.shuffle(pool)
        out.extend(pool[:per])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=39)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--bars", type=int, default=2400)
    ap.add_argument("--fast", default="3:13")
    ap.add_argument("--slow", default="10:60:5")
    ap.add_argument("--cash", type=float, default=1_000_000.0)
    args = ap.parse_args()

    import vectorbt as vbt

    fasts = range(*[int(x) for x in args.fast.split(":")])
    slows = range(*[int(x) for x in args.slow.split(":")])
    universe = pick_universe(args.bars, args.n, args.seed)
    print(f"篮子：{len(universe)} 票（价格三分层 seed={args.seed}）· "
          f"网格 {len(list(fasts)) * len(list(slows))} 组 · {args.bars} 根 · 现金 {args.cash:,.0f}")

    rows: list[dict] = []
    t_all = time.time()
    for i, (symbol, last_px) in enumerate(universe, 1):
        code = symbol.split(".")[0]
        try:
            df = load_ohlc(code, args.bars)
            entries, exits, combos = grid_signals(df, fasts, slows)
            entries, exits = vbt_bridge.limit_gate(entries, exits, df["Open"], df["Close"])
            entries, exits = vbt_bridge.shift_signals(entries, exits)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(universe)}] {code} 跳过：{exc}")
            continue
        # 同一组信号喂两个引擎（修正此前信号不一致 bug）
        t0 = time.time()
        pfs = vbt.Portfolio.from_signals(df["Close"], entries, exits, price=df["Open"],
                                         init_cash=args.cash, fees=0.0003)
        ret_vbt = {tuple(int(x) for x in k): float(v)
                   for k, v in zip(pfs.total_return().index, pfs.total_return().values)}
        t_vbt = time.time() - t0
        errs: list[float] = []
        pair_rets: list[tuple[float, float]] = []
        for c in combos:
            nb = vbt_bridge.naive_backtest(df, entries[c], exits[c],
                                           cash=args.cash, fee=0.0003)
            errs.append(abs(ret_vbt.get(c, float("nan")) - nb["ret"]) * 100)
            pair_rets.append((nb["ret"], ret_vbt.get(c, np.nan)))
        true_rets = pd.Series([p[0] for p in pair_rets])
        vbt_rets = pd.Series([p[1] for p in pair_rets])
        sp = float(true_rets.rank().corr(vbt_rets.rank()))
        k = max(1, len(true_rets) // 5)
        overlap = len(set(true_rets.nlargest(k).index) & set(vbt_rets.nlargest(k).index)) / k
        med_err = float(np.nanmedian(errs))
        rows.append({"symbol": code, "last_px": last_px, "median_abs_err_pp": med_err,
                     "max_abs_err_pp": float(np.nanmax(errs)), "spearman": sp,
                     "top20_overlap": overlap, "vbt_seconds": round(t_vbt, 3)})
        print(f"  [{i}/{len(universe)}] {code} px={last_px:.0f} · |err|中位 {med_err:.2f}pp · "
              f"Spearman {sp:.2f} · 重合 {overlap:.0%}")

    d = pd.DataFrame(rows)
    low = d[d["last_px"] <= 10]; mid = d[(d["last_px"] > 10) & (d["last_px"] <= 50)]
    high = d[d["last_px"] > 50]
    price_corr = d["last_px"].rank().corr(d["median_abs_err_pp"].rank())
    lines = [
        "# vbt 近似口径：分层随机篮子基准（修正单票偏差 + 信号一致性 bug）", "",
        f"- 篮子 {len(d)} 票（低/中/高价格 {len(low)}/{len(mid)}/{len(high)}）· "
        f"网格 {len(list(fasts))*len(list(slows))} 组 × {args.bars} 根 · 总耗时 {time.time()-t_all:.0f}s",
        f"- 误差 |err|（pp）：全篮子中位 **{d['median_abs_err_pp'].median():.2f}** · "
        f"P90 {d['median_abs_err_pp'].quantile(0.9):.2f} · 最大 {d['max_abs_err_pp'].max():.2f}",
        f"- 保序 Spearman：中位 **{d['spearman'].median():.3f}** · "
        f"P10 {d['spearman'].quantile(0.1):.3f} · 最差 {d['spearman'].min():.3f}",
        f"- 前 20% 重合率：中位 {d['top20_overlap'].median():.0%}（随机基线 20%）",
        f"- 误差 vs 价格秩相关 = **{price_corr:.3f}**"
        f"（>0 支持「整手粒度主导」假设，<0/≈0 则口径语义主导）",
        "",
        "| 层 | 票数 | \|err\|中位 | Spearman 中位 | 前20%重合 |",
        "|---|---|---|---|---|",
        f"| 低价 ≤10 | {len(low)} | {low['median_abs_err_pp'].median():.2f} | "
        f"{low['spearman'].median():.3f} | {low['top20_overlap'].median():.0%} |",
        f"| 中价 10-50 | {len(mid)} | {mid['median_abs_err_pp'].median():.2f} | "
        f"{mid['spearman'].median():.3f} | {mid['top20_overlap'].median():.0%} |",
        f"| 高价 >50 | {len(high)} | {high['median_abs_err_pp'].median():.2f} | "
        f"{high['spearman'].median():.3f} | {high['top20_overlap'].median():.0%} |",
        "",
        "## 篮子明细",
        d.sort_values("median_abs_err_pp").to_markdown(index=False),
    ]
    text = "\n".join(lines)
    print(text)
    out = Path(__file__).resolve().parents[2] / ".workbuddy" / "artifacts" / "repo-eval-20260913" / "findings" / "vbt-basket-benchmark.md"
    out.write_text(text, encoding="utf-8")
    print(f"\n已存 {out}")


if __name__ == "__main__":
    main()
