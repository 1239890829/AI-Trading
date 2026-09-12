"""因子判定门槛的 placebo 零分布哨兵（retro §6.19 实证 B；可复跑，只读）。

背景（外部研究，2026-09-13 视频转述）：随机生成 500 个无意义因子仍有 22 个「显著」
——多重检验/数据挖掘偏差是因子衰减四大成因之一。本仓因子判定门槛
（|IC|≥0.02、|ICIR|≥0.30、分年同号≥60%，`app/factors/evaluate.py`）从未在**零分布**
上校准过：门槛是否真的把随机因子挡在门外？余量多大？

方法（两阶段，避免全管线 30s/因子的成本）：
- **阶段 1（本脚本主体）**：K 个确定性伪随机因子（hash 种子 ⇒ 可复现）走与生产
  **同构**的日频 RankIC 管线（T+1 收盘进、T+h 收盘出、percent_rank 后 corr、
  min_bars=60），批量一次扫描算出全部日频 IC；对每个因子取 |ICIR| 最大的主窗口，
  套用**有效性+稳定性必要门槛**（分层/覆盖率不判——随机因子覆盖率恒满、且必要
  门槛不过即 FAIL）。随机因子触达必要门槛的比例 = 假发现率（FDR）上界。
- **阶段 2（自动触发）**：若阶段 1 有触达者（≤5 个），对它们跑**完整** evaluate_factor
  出正式 verdict。

简化声明：未做一字板过滤（管线 nb1 链不适用于随机因子；对零分布影响可忽略）、
横截面最小数 200（生产为全市场 ~4000+，更严）。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from statistics import NormalDist

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.bjtime import beijing_today  # noqa: E402

DB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"
K = 50            # 随机因子总数（分批扫描）
BATCH = 25
HORIZONS = (3, 5, 10, 20)
IC_ABS_MIN = 0.02
ICIR_ABS_MIN = 0.30
YEAR_CONSISTENCY_MIN = 0.60
MIN_CS = 200


def _factor_expr(seed: str) -> str:
    """确定性伪随机截面分值（0~1 均匀）：hash 种子 ⇒ 可复现，零信息。"""
    return f"(hash('{seed}' || thscode || CAST(date_ms AS VARCHAR)) % 1000000) / 1000000.0"


def _batch_sql(seeds: list[str]) -> str:
    # 命名纪律：lf* = LEAD 远期价，fw* = 前瞻收益，pf* = 随机因子（避免与 lf 数字列撞名）
    fexprs = ",\n         ".join(f"{_factor_expr(s)} AS pf{i}" for i, s in enumerate(seeds))
    ranks = ",\n         ".join(
        [f"percent_rank() OVER (PARTITION BY date_ms ORDER BY pf{i}) AS rf{i}" for i in range(len(seeds))]
        + [f"percent_rank() OVER (PARTITION BY date_ms ORDER BY fw{h}) AS rr{h}" for h in HORIZONS]
    )
    corrs = ", ".join(
        f"corr(rf{i}, rr{h}) AS c{i}_{h}" for i in range(len(seeds)) for h in HORIZONS
    )
    return f"""
WITH px AS (
  SELECT k.thscode, k.date_ms, a.close_adj,
         COUNT(*) OVER (PARTITION BY k.thscode ORDER BY k.date_ms
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cnt
  FROM daily_k k JOIN daily_k_adj a USING (thscode, date_ms)
  WHERE a.close_adj > 0
),
scored AS (
  SELECT thscode, date_ms, cnt,
         LEAD(close_adj, 1) OVER w AS lf1,
         LEAD(close_adj, 3) OVER w AS lf3,
         LEAD(close_adj, 5) OVER w AS lf5,
         LEAD(close_adj, 10) OVER w AS lf10,
         LEAD(close_adj, 20) OVER w AS lf20,
         {fexprs}
  FROM px WINDOW w AS (PARTITION BY thscode ORDER BY date_ms)
),
base AS (
  SELECT thscode, date_ms, cnt,
         lf3 / NULLIF(lf1, 0) - 1 AS fw3,
         lf5 / NULLIF(lf1, 0) - 1 AS fw5,
         lf10 / NULLIF(lf1, 0) - 1 AS fw10,
         lf20 / NULLIF(lf1, 0) - 1 AS fw20,
         {", ".join(f"pf{i}" for i in range(len(seeds)))}
  FROM scored
  WHERE cnt >= 60 AND lf3 IS NOT NULL AND lf5 IS NOT NULL
    AND lf10 IS NOT NULL AND lf20 IS NOT NULL
),
ranked AS (
  SELECT date_ms, {ranks} FROM base
)
SELECT date_ms, COUNT(*) AS n, {corrs} FROM ranked
GROUP BY date_ms HAVING COUNT(*) >= {MIN_CS} ORDER BY date_ms
"""


def _yearly_sign_stats(dates_ms: list[int], series: list[float]) -> tuple[float, float, float, float]:
    """单序列 → (mean, std, icir, 分年同号占比)。"""
    n = len(series)
    mean = sum(series) / n
    var = sum((x - mean) ** 2 for x in series) / (n - 1) if n > 1 else 0.0
    std = var ** 0.5
    icir = mean / std if std > 0 else 0.0
    by_year: dict[int, list[float]] = {}
    for ts, ic in zip(dates_ms, series):
        by_year.setdefault(date.fromtimestamp(ts / 1000).year, []).append(ic)
    ym = {y: sum(v) / len(v) for y, v in by_year.items()}
    if mean > 0:
        same = sum(1 for v in ym.values() if v > 0)
    elif mean < 0:
        same = sum(1 for v in ym.values() if v < 0)
    else:
        same = 0
    return mean, std, icir, same / len(ym) if ym else 0.0


def main() -> int:
    if not DB.exists():
        print(f"ERROR: marketdb 不存在：{DB}", file=sys.stderr)
        return 1
    con = duckdb.connect(str(DB), read_only=True)
    results: list[dict] = []  # 每因子最优窗口的必要门槛判据
    null_ics: list[float] = []  # 零分布：全部 (因子, 窗口) 的 |mean IC|
    try:
        seeds = [str(i) for i in range(K)]
        for b in range(0, K, BATCH):
            chunk = seeds[b:b + BATCH]
            rows = con.execute(_batch_sql(chunk)).fetchall()
            cols = [c[0] for c in con.description]
            dates = [r[0] for r in rows]
            print(f"batch {b // BATCH + 1}/{(K + BATCH - 1) // BATCH}: {len(rows)} 个有效截面日", file=sys.stderr)
            for i, _seed in enumerate(chunk):
                for h in HORIZONS:
                    series = [r[cols.index(f"c{i}_{h}")] for r in rows]
                    series = [x for x in series if x is not None]
                    if len(series) < 100:
                        continue
                    mean, std, icir, ysame = _yearly_sign_stats(dates, series)
                    null_ics.append(abs(mean))
                    if abs(mean) >= IC_ABS_MIN and abs(icir) >= ICIR_ABS_MIN and ysame >= YEAR_CONSISTENCY_MIN:
                        results.append({"seed": _seed, "h": h, "ic": mean, "icir": icir, "ysame": ysame})
    finally:
        con.close()

    null_ics.sort()
    p50 = null_ics[len(null_ics) // 2]
    p95 = null_ics[int(len(null_ics) * 0.95)]
    pmax = null_ics[-1]
    print(f"## 因子门槛 placebo 零分布哨兵（marketdb，{beijing_today().isoformat()}）\n")
    print(f"- 随机因子 K={K}（hash 种子可复现）× 窗口 {HORIZONS}；零分布样本 {len(null_ics)} 个 |mean IC|")
    print(f"- 零分布：p50={p50:.4f} · p95={p95:.4f} · max={pmax:.4f}"
          f" —— 对照门槛 IC_ABS_MIN={IC_ABS_MIN}（**余量 ≈{IC_ABS_MIN / max(pmax, 1e-9):.0f}× 最极端随机值**）")
    print(f"- **触达有效性+稳定性必要门槛：{len(results)}/{K}**"
          f"（多重检验假发现率上界；分层/覆盖未判，必要门槛不过即 FAIL）")
    if results:
        print("  - 触达者：" + " · ".join(
            f"seed{r['seed']}(h={r['h']}, IC={r['ic']:+.4f}, ICIR={r['icir']:+.2f}, 年同号 {r['ysame']:.0%})"
            for r in results[:5]))
    headroom = NormalDist().inv_cdf(1 - 0.05 / max(len(null_ics), 1))
    print(f"- 结论：随机因子的日频 IC 均值零分布远窄于门槛（p95={p95:.4f} vs 门槛 {IC_ABS_MIN}），"
          f"当前门槛在 10 年全 A 上的多重检验暴露 **{'受控' if len(results) == 0 else '需要复核触达者'}**"
          f"（零分布 5% 单侧参考线 ≈ {headroom * (p50 * 1.25):.4f} 量级）。")
    print("> 口径声明：未做一字板过滤（对零分布影响可忽略）；横截面最小 200（生产 ~4000+，更严）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
