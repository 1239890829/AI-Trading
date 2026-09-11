"""三倍量战法——真实历史样本快速核验（一次性分析脚本，不落库、不改线上）。

回答的问题（用户 2026-09-10 首问「该方法是否可行可靠及可用」）：
  1. 三倍量 K 线出现频率有多高？（信号是否太稀 / 太滥）
  2. 出现后：次日突破（最高价）的比例是多少？（右侧机会的可得性）
  3. 出现后持有 5 日的收益分布，与"同期全市场平均"相比有无差异？（有无超额）
  4. 回踩到最低价附近且收盘守住的比例？（左侧机会的可得性）

⚠️ 口径声明（必须随结论一起引用）：
- 数据 = marketdb `daily_k`（**未复权**）。成交量本身无需复权；但价格跨除权日会失真——
  本核验不剔除除权日，故绝对收益含少量除权噪声（对"有无差异"的相对比较影响有限）。
- 这是**快速样本统计，不是完整回测**：不含交易费用、滑点、涨跌停无法成交、持仓管理。
- 结论只在本样本窗口内成立，不得外推为"长期有效"。
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "marketdb" / "market.duckdb"
LOOKBACK_DAYS = 250          # 核验窗口（交易日）
VOL_MULTIPLE = 3.0
VOL_LOOKBACK = 5
FWD_DAYS = 5


def _q(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    pos = (len(s) - 1) * p / 100
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (pos - lo), 2)


def main() -> int:
    con = duckdb.connect(str(DB), read_only=True)
    max_ms = con.execute("select max(date_ms) from daily_k").fetchone()[0]
    min_ms = max_ms - LOOKBACK_DAYS * 86_400_000 * 1.5  # 日历日留足（含休市）

    window = (
        "PARTITION BY thscode ORDER BY date_ms"
    )
    lags = ", ".join(
        f"LEAD(close_price,{i}) OVER ({window}) AS f_close_{i}" for i in range(1, FWD_DAYS + 1)
    )
    sql = f"""
    WITH base AS (
      SELECT thscode, date_ms, high_price, low_price, close_price, volume,
             AVG(volume) OVER (PARTITION BY thscode ORDER BY date_ms
                               ROWS BETWEEN {VOL_LOOKBACK} PRECEDING AND 1 PRECEDING) AS avg_prev,
             LAG(close_price,1) OVER ({window}) AS prev_close,
             LEAD(high_price,1) OVER ({window}) AS n_high,
             {lags}
      FROM daily_k
      WHERE date_ms >= {int(min_ms)}
    )
    SELECT
      (volume >= {VOL_MULTIPLE} * avg_prev) AS is_triple,
      CASE WHEN prev_close > 0 THEN (close_price / prev_close - 1) * 100 END AS day_pct,
      CASE WHEN n_high IS NOT NULL THEN (n_high >= high_price) END::INT AS n_breakout,
      CASE WHEN f_close_{FWD_DAYS} IS NOT NULL AND close_price > 0
           THEN (f_close_{FWD_DAYS} / close_price - 1) * 100 END AS fwd5_pct,
      CASE WHEN close_price > 0 THEN (close_price - low_price) / close_price * 100 END AS close_above_low_pct
    FROM base
    WHERE avg_prev IS NOT NULL AND avg_prev > 0 AND volume > 0 AND close_price > 0
    """
    rows = con.execute(sql).fetchall()
    con.close()

    sig, allrows = [], []
    for r in rows:
        allrows.append(r)
        if r[0]:
            sig.append(r)

    def _fwd(rs):
        return [r[3] for r in rs if r[3] is not None]

    fwd_sig, fwd_all = _fwd(sig), _fwd(allrows)
    brk = [r[2] for r in sig if r[2] is not None]
    day = [r[1] for r in sig if r[1] is not None]
    hold = [r[4] for r in sig if r[4] is not None]

    print(f"窗口：最后交易日 {max_ms} 前约 {LOOKBACK_DAYS} 个交易日")
    print(f"总样本行 {len(allrows)}（股票×日），其中三倍量信号 {len(sig)} 条"
          f"（占比 {len(sig) / max(len(allrows), 1) * 100:.3f}%）")
    print()
    print("【三倍量当日】涨跌幅 中位 %.2f%% / 均值 %.2f%%" % (
        statistics.median(day) if day else 0, statistics.mean(day) if day else 0))
    print("【收盘高于当日最低价】平均 %.2f%%（越小=收在当日低位，说明当日冲高回落）"
          % (statistics.mean(hold) if hold else 0))
    print("【次日突破当日最高价】比例 %.1f%%（%d/%d）" % (
        (sum(brk) / len(brk) * 100) if brk else 0, sum(brk), len(brk)))
    print()
    print("【持有 %d 日收益】信号组：均值 %.2f%% / 中位 %.2f%% / n=%d" % (
        FWD_DAYS, statistics.mean(fwd_sig) if fwd_sig else 0, _q(fwd_sig, 50) or 0, len(fwd_sig)))
    print("                 全市场对照：均值 %.2f%% / 中位 %.2f%% / n=%d" % (
        statistics.mean(fwd_all) if fwd_all else 0, _q(fwd_all, 50) or 0, len(fwd_all)))
    if fwd_sig and fwd_all:
        print("                 差值（信号 - 市场）：均值 %+.2f 个百分点" % (
            statistics.mean(fwd_sig) - statistics.mean(fwd_all)))
    print()
    print("信号组 %d 日收益分位：p10 %.2f%% / p25 %.2f%% / p50 %.2f%% / p75 %.2f%% / p90 %.2f%%" % (
        FWD_DAYS, _q(fwd_sig, 10) or 0, _q(fwd_sig, 25) or 0, _q(fwd_sig, 50) or 0,
        _q(fwd_sig, 75) or 0, _q(fwd_sig, 90) or 0))
    win = sum(1 for v in fwd_sig if v > 0) / len(fwd_sig) * 100 if fwd_sig else 0
    win_all = sum(1 for v in fwd_all if v > 0) / len(fwd_all) * 100 if fwd_all else 0
    print("正收益比例：信号组 %.1f%% vs 市场 %.1f%%" % (win, win_all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
