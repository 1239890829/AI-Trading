"""tech_score 八维权重 IC 复核（策略进化 P1 方向 3：优胜劣汰的证据层）。

方法论（qlib/米筐因子分析口径）：
- 近 60 个交易日，逐日横截面：tech_score 各维度分 vs **T+5 前向收益**
  的 Spearman 秩相关（IC）；
- 汇总 mean IC / ICIR（=mean/std）/ IC>0 占比；
- 判定线：|mean IC| ≥ 0.05 有效；0.02~0.05 弱；< 0.02 疑似噪音（与
  strategy-evolution-plan §0 qlib 行一致）。

边界（诚实声明）：
- **rps / liquidity 两维剔除**——它们是横截面分位输入（脚本只在抽样集上
  算不出全市场 RPS），喂 None 会恒为中性 0.5，秩相关无定义；
- bars 用 daily_k_adj 的 close 替换 raw close（QFQ 口径），OHLC 其余字段
  保留 raw——除权日当天 pattern/kdj 可能轻微失真，样本占比极小；
- **产出是证据报告 + 权重调整提案，绝不自动改权重**（改权重必须递增
  SCORER_VERSION 并走人工 confirm——方案 §方向 3 纪律）。

用法（cwd 必须 backend/）：
    .venv/bin/python scripts/factor_ic_review.py [--sample 600] [--fwd 5]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"
REVIEW_DATES = 60
MIN_SECTION = 30  # 单日横截面样本下限，不足则该日跳过（不臆造 IC）

#: 有效线 / 弱线（与 qlib 滚动 IC 监控口径对齐）
IC_STRONG = 0.05
IC_WEAK = 0.02


def _rank(vals: list[float]) -> list[float]:
    """平均秩（并列取均值），Spearman 标准做法。"""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    rx, ry = _rank(xs), _rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)


def _verdict(mean_ic: float) -> str:
    a = abs(mean_ic)
    if a >= IC_STRONG:
        return "有效" if mean_ic > 0 else "有效（负向——降权/反用候选）"
    if a >= IC_WEAK:
        return "弱"
    return "疑似噪音"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sample", type=int, default=600, help="抽样股票数（确定性等步抽样）")
    ap.add_argument("--fwd", type=int, default=5, help="前向收益持有期（交易日）")
    args = ap.parse_args()

    con = duckdb.connect(str(DB_PATH), read_only=True)
    t0 = time.monotonic()

    # 抽样：有足够历史（≥180 根）的股票中等步抽样，确定性可复跑
    elig = [r[0] for r in con.execute(
        "SELECT thscode FROM daily_k GROUP BY thscode HAVING COUNT(*) >= 180 ORDER BY thscode"
    ).fetchall()]
    if not elig:
        print("marketdb 无可用股票", file=sys.stderr)
        return 2
    step = max(1, len(elig) // args.sample)
    symbols = elig[::step][: args.sample]

    bars_by_sym: dict[str, list[dict]] = {}
    for sym in symbols:
        rows = con.execute(
            "SELECT date_ms, open_price, high_price, low_price, close_price, volume "
            "FROM daily_k WHERE thscode = ? ORDER BY date_ms", [sym]
        ).fetchall()
        bars_by_sym[sym] = [
            {"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
            for r in rows
        ]
    adj_by_sym: dict[str, dict[int, float]] = {}
    for sym in symbols:
        rows = con.execute(
            "SELECT date_ms, close_adj FROM daily_k_adj WHERE thscode = ? ORDER BY date_ms", [sym]
        ).fetchall()
        adj_by_sym[sym] = {r[0]: r[1] for r in rows if r[1]}

    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT date_ms FROM daily_k ORDER BY date_ms"
    ).fetchall()]
    con.close()

    from bisect import bisect_right

    from app.market.tech_score import score_stock

    review_dates = dates[-(REVIEW_DATES + args.fwd): -args.fwd]
    fwd_pos = {d: i for i, d in enumerate(dates)}
    ts_index = {sym: [b["ts"] for b in bars] for sym, bars in bars_by_sym.items()}

    dims = ["trend", "macd", "kdj", "rsi", "volume", "pattern"]
    ics: dict[str, list[float]] = {k: [] for k in dims}
    skipped_dates = 0

    for t in review_dates:
        scores: dict[str, list[float]] = {k: [] for k in dims}
        fwds: list[float] = []
        for sym in symbols:
            bars = bars_by_sym[sym]
            # 窗口：截至 t 的最后 120 根（bisect 定位，避免逐票全量扫描）
            end = bisect_right(ts_index[sym], t)
            if end == 0:
                continue
            win = bars[max(0, end - 120): end]
            if win[-1]["ts"] != t:
                continue
            idx = fwd_pos.get(t)
            if idx is None or idx + args.fwd >= len(dates):
                continue
            t_fwd = dates[idx + args.fwd]
            adj = adj_by_sym.get(sym) or {}
            c0, c1 = adj.get(t), adj.get(t_fwd)
            if not c0 or not c1:
                continue
            try:
                card = score_stock(win)
            except Exception:  # noqa: BLE001  单票评分失败跳过，不中断整截面
                continue
            if card is None:
                continue
            d = card["dimensions"]
            for k in dims:
                v = d.get(k)
                if v is not None:
                    scores[k].append(float(v))
            fwds.append(c1 / c0 - 1)

        if len(fwds) < MIN_SECTION:
            skipped_dates += 1
            continue
        for k in dims:
            if len(scores[k]) == len(fwds) and len(scores[k]) >= MIN_SECTION:
                ic = _spearman(scores[k], fwds)
                if ic is not None:
                    ics[k].append(ic)

    # 汇总
    rows = []
    for k in dims:
        vals = ics[k]
        if not vals:
            rows.append({"dim": k, "days": 0, "mean_ic": None, "icir": None,
                         "pos_rate": None, "verdict": "样本不足"})
            continue
        mean = sum(vals) / len(vals)
        std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        rows.append({
            "dim": k, "days": len(vals), "mean_ic": round(mean, 4),
            "icir": round(mean / std, 3) if std else None,
            "pos_rate": round(sum(1 for v in vals if v > 0) / len(vals), 3),
            "verdict": _verdict(mean),
        })

    lines = [
        "# tech_score 权重 IC 复核（{date}）".format(date=time.strftime("%Y-%m-%d")),
        "",
        f"- 样本：{len(symbols)} 只（等步抽样，可复跑）× 近 {len(review_dates)} 交易日"
        f"（实际有效 {len(review_dates) - skipped_dates} 日，截面 <{MIN_SECTION} 只的 {skipped_dates} 日已跳过）",
        f"- 前向收益：T+{args.fwd}；口径：Spearman 秩相关，QFQ 收盘",
        "- 剔除维度：rps / liquidity（横截面分位输入，抽样集算不出全市场口径，喂常数无意义）",
        f"- 判定线：|IC|≥{IC_STRONG} 有效；{IC_WEAK}~{IC_STRONG} 弱；<{IC_WEAK} 疑似噪音",
        "",
        "| 维度 | 有效日数 | mean IC | ICIR | IC>0 占比 | 判定 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['dim']} | {r['days']} | {r['mean_ic']} | {r['icir']} | {r['pos_rate']} | {r['verdict']} |"
        )
    lines += [
        "",
        "## 权重提案（人工 confirm，绝不自动改）",
        "",
        "- 判定「有效」且方向为正的维度：维持或考虑上调（上调必须递增 SCORER_VERSION 并走 walk-forward）。",
        "- 判定「疑似噪音」的维度：优先怀疑**实现质量**（该维是否经常输出常数/中性档），其次才是降权。",
        "- 判定「负向有效」的维度：先复核口径方向是否写反，确认后再议反向或降权。",
        "",
        f"耗时 {time.monotonic() - t0:.1f}s；脚本：scripts/factor_ic_review.py",
    ]
    out_file = Path(__file__).resolve().parents[2] / "docs" / f"factor-ic-review-{time.strftime('%Y%m%d')}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入 {out_file}")
    for r in rows:
        print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
