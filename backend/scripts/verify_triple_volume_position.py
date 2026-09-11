"""三倍量战法——**位置分层**核验（一次性分析脚本，不落库、不改线上）。

动机：前一轮按用户规则核验，左侧/右侧买点均负超额（−0.32% / −0.44%，胜率 33.5% / 35.9%
vs 市场 46.8%）。但**用户规则里没有"位置前提"**，而社区成熟版本反复强调：
「高位三倍量 = 主力派发，低位三倍量 = 吸筹」——两者被混在一起统计，很可能是负结果的原因之一。

本脚本把三倍量信号按「信号日前 20 日累计涨幅」分层，检验该假设是否成立：
  · 低位组：prev20_pct < LOW_THRESHOLD（放量前没有明显涨幅）
  · 高位组：prev20_pct ≥ LOW_THRESHOLD（放量前已大涨）
若低位组显著优于高位组（甚至转正），则「位置过滤」是有效改造方向；
若两组都差，则该假设在本样本内不成立。

⚠️ 同上轮口径：未复权、不剔除权、不含费用滑点、收盘口径买入、样本内快速核验。
"""
from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "marketdb" / "market.duckdb"
LOOKBACK_DAYS = 250
VOL_MULTIPLE = 3.0
VOL_LOOKBACK = 5
OBSERVE = 5
HOLD_MAX = 5
TOUCH = 1.01
LOW_THRESHOLD = 10.0   # 信号日前 20 日累计涨幅 < 10% 视为"低位/横盘后放量"


def _q(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    pos = (len(s) - 1) * p / 100
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 2)


def _simulate(bars: list[tuple], entry_idx: int, stop: float) -> float | None:
    entry = bars[entry_idx][4]
    if entry <= 0:
        return None
    for j in range(entry_idx + 1, min(entry_idx + 1 + HOLD_MAX, len(bars))):
        if bars[j][4] < stop:
            return (bars[j][4] / entry - 1) * 100
    end = min(entry_idx + HOLD_MAX, len(bars) - 1)
    if end <= entry_idx:
        return None
    return (bars[end][4] / entry - 1) * 100


def main() -> int:
    con = duckdb.connect(str(DB), read_only=True)
    max_ms = con.execute("select max(date_ms) from daily_k").fetchone()[0]
    min_ms = max_ms - (LOOKBACK_DAYS + 40) * 86_400_000 * 1.5
    w = "PARTITION BY thscode ORDER BY date_ms"
    sql = f"""
    WITH k AS (
      SELECT thscode, date_ms, open_price, high_price, low_price, close_price, volume,
             ROW_NUMBER() OVER ({w}) AS rn,
             LAG(close_price,20) OVER ({w}) AS c20,
             AVG(volume) OVER (PARTITION BY thscode ORDER BY date_ms
                               ROWS BETWEEN {VOL_LOOKBACK} PRECEDING AND 1 PRECEDING) AS avg_prev
      FROM daily_k WHERE date_ms >= {int(min_ms)}
    ), t0 AS (
      SELECT thscode, rn, date_ms, high_price AS h0, low_price AS l0,
             CASE WHEN c20 > 0 THEN (close_price / c20 - 1) * 100 END AS prev20
      FROM k WHERE avg_prev > 0 AND volume >= {VOL_MULTIPLE} * avg_prev AND volume > 0
    )
    SELECT t.thscode, t.date_ms, t.h0, t.l0, t.prev20,
           k.date_ms, k.open_price, k.high_price, k.low_price, k.close_price
    FROM t0 t
    JOIN k ON k.thscode = t.thscode AND k.rn > t.rn AND k.rn <= t.rn + {OBSERVE + HOLD_MAX}
    WHERE t.date_ms >= {int(max_ms - LOOKBACK_DAYS * 86_400_000 * 1.5)}
    ORDER BY t.thscode, t.date_ms, k.date_ms
    """
    rows = con.execute(sql).fetchall()
    con.close()

    groups: dict[tuple, list[tuple]] = defaultdict(list)
    meta: dict[tuple, tuple] = {}
    for r in rows:
        key = (r[0], r[1])
        groups[key].append((r[5], r[6], r[7], r[8], r[9]))
        meta[key] = (r[2], r[3], r[4])

    buckets: dict[str, dict[str, list[float]]] = {
        "低位（前20日涨幅<%d%%）" % int(LOW_THRESHOLD): {"left": [], "right": []},
        "高位（前20日涨幅≥%d%%）" % int(LOW_THRESHOLD): {"left": [], "right": []},
        "位置未知": {"left": [], "right": []},
    }
    counts = {k: 0 for k in buckets}
    for key, bars in groups.items():
        h0, l0, p20 = meta[key]
        name = "位置未知" if p20 is None else (
            "低位（前20日涨幅<%d%%）" % int(LOW_THRESHOLD) if p20 < LOW_THRESHOLD
            else "高位（前20日涨幅≥%d%%）" % int(LOW_THRESHOLD))
        counts[name] += 1
        l_entry = r_entry = None
        for i, (_, _, _, low, close) in enumerate(bars):
            if l_entry is None and low <= l0 * TOUCH and close >= l0:
                l_entry = i
            if r_entry is None and close > h0:
                r_entry = i
            if l_entry is not None and r_entry is not None:
                break
        if l_entry is not None:
            v = _simulate(bars, l_entry, l0)
            if v is not None:
                buckets[name]["left"].append(v)
        if r_entry is not None:
            v = _simulate(bars, r_entry, bars[r_entry][3])
            if v is not None:
                buckets[name]["right"].append(v)

    def _stat(label: str, vals: list[float]) -> str:
        if not vals:
            return f"{label}: 无有效样本"
        return "%s：n=%d | 均值 %+.2f%% | 中位 %+.2f%% | 胜率 %.1f%%" % (
            label, len(vals), statistics.mean(vals), _q(vals, 50),
            sum(1 for v in vals if v > 0) / len(vals) * 100)

    print(f"窗口：最后交易日 {max_ms} 前约 {LOOKBACK_DAYS} 个交易日")
    print(f"分层阈值：信号日前 20 日累计涨幅 {LOW_THRESHOLD}%")
    print("=" * 78)
    for name, d in buckets.items():
        print(f"■ {name} —— 信号 {counts[name]} 条")
        print("   " + _stat("左侧低吸", d["left"]))
        print("   " + _stat("右侧突破", d["right"]))
    print("=" * 78)
    print("对照：全市场任意日持有同周期 ≈ 均值 +0.06% / 胜率 46.8%（前一轮实测）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
