"""三倍量战法——**按用户规则**的事件驱动核验（一次性分析脚本，不落库、不改线上）。

与 `verify_triple_volume.py` 的区别（关键）：
  前者测的是「三倍量当天收盘无脑买、持有 5 日」——**即社区批评的那种用法**，结果为负超额；
  本脚本测的是**用户给出的规则本身**：
    · 左侧低吸：信号后，价格回踩至 T0 最低价 +1% 以内 **且当日收盘未跌破 T0 最低价** → 当日收盘买入；
      止损 = T0 最低价（**收盘口径**，盘中刺破不算）
    · 右侧突破：信号后，**收盘价突破 T0 最高价** → 当日收盘买入；止损 = 突破当天最低价（收盘口径）
  两者均在「止损触发」或「持有满 N 日」时离场，并统计与实际市场基准的差异。

⚠️ 口径声明（随结论一起引用）：
- 数据 = `daily_k`（未复权）；不剔除除权日；**不含费用/滑点/涨跌停无法成交**（故结果偏乐观）。
- 买入价用**当日收盘**（左侧≈尾盘低吸；右侧突破后用收盘价，比盘中突破更保守）。
- 这是**样本内快速核验**，非完整回测；只说明本窗口内"用户规则 vs 无规则"的差异方向。
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
OBSERVE = 5      # 信号后观察窗（交易日）
HOLD_MAX = 5     # 入场后最长持有（交易日）
TOUCH = 1.01     # 左侧"回踩至最低价附近"= low ≤ T0低 × 1.01


def _q(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    pos = (len(s) - 1) * p / 100
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 2)


def _simulate(bars: list[tuple], entry_idx: int, stop: float) -> float | None:
    """按「止损以收盘价为准」模拟持有，返回收益率%；数据不足 → None。"""
    entry = bars[entry_idx][4]  # close
    if entry <= 0:
        return None
    for j in range(entry_idx + 1, min(entry_idx + 1 + HOLD_MAX, len(bars))):
        if bars[j][4] < stop:          # 收盘跌破 → 该日收盘离场
            return (bars[j][4] / entry - 1) * 100
    end = min(entry_idx + HOLD_MAX, len(bars) - 1)
    if end <= entry_idx:
        return None
    return (bars[end][4] / entry - 1) * 100


def main() -> int:
    con = duckdb.connect(str(DB), read_only=True)
    max_ms = con.execute("select max(date_ms) from daily_k").fetchone()[0]
    min_ms = max_ms - LOOKBACK_DAYS * 86_400_000 * 1.5
    w = "PARTITION BY thscode ORDER BY date_ms"
    sql = f"""
    WITH k AS (
      SELECT thscode, date_ms, open_price, high_price, low_price, close_price, volume,
             ROW_NUMBER() OVER ({w}) AS rn,
             AVG(volume) OVER (PARTITION BY thscode ORDER BY date_ms
                               ROWS BETWEEN {VOL_LOOKBACK} PRECEDING AND 1 PRECEDING) AS avg_prev
      FROM daily_k WHERE date_ms >= {int(min_ms)}
    ), t0 AS (
      SELECT thscode, rn, date_ms, high_price AS h0, low_price AS l0
      FROM k WHERE avg_prev > 0 AND volume >= {VOL_MULTIPLE} * avg_prev AND volume > 0
    )
    SELECT t.thscode, t.date_ms, t.h0, t.l0, k.date_ms, k.open_price, k.high_price, k.low_price, k.close_price
    FROM t0 t
    JOIN k ON k.thscode = t.thscode AND k.rn > t.rn AND k.rn <= t.rn + {OBSERVE + HOLD_MAX}
    ORDER BY t.thscode, t.date_ms, k.date_ms
    """
    rows = con.execute(sql).fetchall()
    # 全市场对照：同期任意 (股票,日) 持有 HOLD_MAX 日的收益
    base_sql = f"""
    WITH k AS (
      SELECT thscode, date_ms, close_price,
             LEAD(close_price,{HOLD_MAX}) OVER ({w}) AS f
      FROM daily_k WHERE date_ms >= {int(min_ms)}
    )
    SELECT (f / close_price - 1) * 100 FROM k WHERE f IS NOT NULL AND close_price > 0
    """
    mkt = [r[0] for r in con.execute(base_sql).fetchall()]
    con.close()

    groups: dict[tuple, list[tuple]] = defaultdict(list)
    meta: dict[tuple, tuple] = {}
    for r in rows:
        key = (r[0], r[1])
        groups[key].append((r[4], r[5], r[6], r[7], r[8]))
        meta[key] = (r[2], r[3])  # h0, l0

    left, right, no_signal = [], [], 0
    for key, bars in groups.items():
        h0, l0 = meta[key]
        l_entry = r_entry = None
        for i, (_, _, high, low, close) in enumerate(bars):
            if l_entry is None and low <= l0 * TOUCH and close >= l0:
                l_entry = i
            if r_entry is None and close > h0:
                r_entry = i
            if l_entry is not None and r_entry is not None:
                break
        if l_entry is None and r_entry is None:
            no_signal += 1
        if l_entry is not None:
            v = _simulate(bars, l_entry, l0)
            if v is not None:
                left.append(v)
        if r_entry is not None:
            v = _simulate(bars, r_entry, bars[r_entry][3])
            if v is not None:
                right.append(v)

    def _stat(name: str, vals: list[float]) -> None:
        if not vals:
            print(f"{name}: 无有效样本")
            return
        print("%s：n=%d | 均值 %+.2f%% | 中位 %+.2f%% | 胜率 %.1f%% | p10 %.2f%% / p90 %.2f%%" % (
            name, len(vals), statistics.mean(vals), _q(vals, 50),
            sum(1 for v in vals if v > 0) / len(vals) * 100, _q(vals, 10), _q(vals, 90)))

    print(f"窗口：最后交易日 {max_ms} 前约 {LOOKBACK_DAYS} 个交易日 | 三倍量信号 {len(groups)} 条")
    print(f"观察 {OBSERVE} 日内：左侧买点触发 {len(left)} 条、右侧买点触发 {len(right)} 条、"
          f"两边都未触发 {no_signal} 条")
    print(f"持有上限 {HOLD_MAX} 日、止损以收盘价为准（盘中刺破不算）")
    print("-" * 78)
    _stat("左侧低吸（回踩守住 T0 低）", left)
    _stat("右侧突破（收盘破 T0 高）", right)
    _stat("全市场基准（任意日持有同周期）", mkt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
