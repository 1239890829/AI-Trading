"""P1-3 池内条件 IC 评估（一次性研究，2026-09-09）

背景：docs/summary/factor-system.md 全市场 IC 明确警示「全市场 IC ≠ 选股池
条件 IC」——tech_score 只作用于强势候选池，「趋势维全市场 T+5 反转」≠「池内
失效」。P1-3 = 用条件 IC 回答「哪些因子在我们实际选股的池子里有区分度」。

三口径（同日截面样本差异仅在池过滤，复用 evaluate._base_cte 基建）：
- full     全市场（基准，与 evaluate 主流程同源）
- pool_zb  涨停池：当日 ret1 ≥ 涨停线-0.3%（打板/接力「已强池」）
- pool_lb  临板池：ret1 ∈ [5%, 涨停线-0.5%)（强势未板，临板雷达语义）
涨停线按 thscode 前缀 30/68→20%、8/4/92→30%、其余→10%。

统计：T+5 前瞻 RankIC，每日截面 ≥30 样本才计；输出 mean/ICIR/IC>0 占比，
与 full 并列——**池内 |mean IC| 与正占比明显高于 full → 该因子有条件价值**。

数据：marketdb daily_k；fwd5 已由 base LAG 对齐 T+1 收盘进。耗时偏重（base CTE
含 20 日窗口聚合），实测后再决定是否扩全量 26 因子。
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb

from app.factors.evaluate import _base_cte  # 复用既有 base 链（与因子无关，仅签名占位）
from app.factors.library import FACTOR_BY_NAME, FactorDef

DB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"
LOOKBACK_DAYS = 180  # ~130 交易日

# 每类别一代表（覆盖动量/趋势/非流动性/量比/突破/频率动量/实体/量价）
REPRESENT = ["mom20", "beta20", "amihud20", "amt_ratio", "max20", "cntp20", "kmid2", "corr_pv20"]

_LINE = ("CASE WHEN thscode LIKE '30%' OR thscode LIKE '68%' THEN 0.20 "
         "WHEN thscode LIKE '8%' OR thscode LIKE '4%' OR thscode LIKE '92%' THEN 0.30 "
         "ELSE 0.10 END")

_POOL_WHERE = {
    "full": "1=1",
    "pool_zb": f"ret1d >= {_LINE} - 0.003",
    "pool_lb": f"ret1d >= 0.05 AND ret1d < {_LINE} - 0.005",
    # lag1 口径：池判定用 prev_ret（昨日涨停）
    "zb_lag1": f"prev_ret >= {_LINE} - 0.003",
}

_CTE = _base_cte  # 别名：签名兼容（内部不引用 factor）


def _sql(f: FactorDef, pool: str) -> str:
    return f"""{_CTE(f)},
scored AS (
    SELECT thscode, date_ms, ret1 AS ret1d,
           ({f.expr}) AS fv,
           f5 / NULLIF(f1, 0) - 1 AS fwd5
    FROM lvl4
    WHERE cnt >= {f.min_bars}
      AND nb1_high IS NOT NULL AND nb1_low IS NOT NULL AND nb1_high > nb1_low
),
ranked AS (
    SELECT *,
           percent_rank() OVER (PARTITION BY date_ms ORDER BY fv) AS rf,
           percent_rank() OVER (PARTITION BY date_ms ORDER BY fwd5) AS rr5
    FROM scored
    WHERE fv IS NOT NULL AND fwd5 IS NOT NULL AND {_POOL_WHERE[pool]}
),
daily AS (
    SELECT date_ms, COUNT(*) AS n, corr(rf, rr5) AS ic5
    FROM ranked GROUP BY date_ms
)
SELECT date_ms, n, ic5 FROM daily WHERE n >= 30 ORDER BY date_ms"""


def _sql_lag1(f: FactorDef, pool: str) -> str:
    """可执行口径（lag1）：观测行 D 决策时点可知的是 D-1 的池状态与因子值。

    池判定用 D-1 ret1（昨日涨停）；因子排序用 D-1 因子（LAG 滞后一行）；
    fwd5 = base f1/f5 锚（D+1 收盘进）→ 决策 D 日开盘、无 lookahead。
    """
    return f"""{_CTE(f)},
scored AS (
    SELECT thscode, date_ms, ret1 AS ret1d,
           ({f.expr}) AS fv,
           f5 / NULLIF(f1, 0) - 1 AS fwd5
    FROM lvl4
    WHERE cnt >= {f.min_bars}
      AND nb1_high IS NOT NULL AND nb1_low IS NOT NULL AND nb1_high > nb1_low
),
lagged AS (
    SELECT *,
           LAG(fv) OVER (PARTITION BY thscode ORDER BY date_ms) AS fv_prev,
           LAG(ret1d) OVER (PARTITION BY thscode ORDER BY date_ms) AS prev_ret
    FROM scored
),
ranked AS (
    SELECT *,
           percent_rank() OVER (PARTITION BY date_ms ORDER BY fv_prev) AS rf,
           percent_rank() OVER (PARTITION BY date_ms ORDER BY fwd5) AS rr5
    FROM lagged
    WHERE fv_prev IS NOT NULL AND fwd5 IS NOT NULL AND {_POOL_WHERE[pool]}
),
daily AS (
    SELECT date_ms, COUNT(*) AS n, corr(rf, rr5) AS ic5
    FROM ranked GROUP BY date_ms
)
SELECT date_ms, n, ic5 FROM daily WHERE n >= 30 ORDER BY date_ms"""


def _stats(rows: list[tuple]) -> tuple[int, float | None, float | None, float | None]:
    ics = [r[2] for r in rows if r[2] is not None]
    if len(ics) < 5:
        return len(ics), None, None, None
    m = sum(ics) / len(ics)
    var = sum((x - m) ** 2 for x in ics) / max(len(ics) - 1, 1)
    sd = var ** 0.5
    return len(ics), m, (m / sd if sd > 0 else None), sum(x > 0 for x in ics) / len(ics)


def main(names: list[str] | None = None) -> None:
    sel = names or REPRESENT
    con = duckdb.connect(str(DB), read_only=True)
    mx = con.execute("select max(date_ms) from daily_k").fetchone()[0]
    print(f"数据截止 {datetime.fromtimestamp(mx/1000):%Y-%m-%d} | "
          f"口径: full/涨停池/临板池/zb_lag1(昨日涨停池·T-1因子·可执行)")
    print(f"{'因子':12s} {'口径':9s} {'样本日':>5s} {'meanIC':>9s} {'ICIR':>7s} {'IC>0':>6s}")
    for name in sel:
        f = FACTOR_BY_NAME[name]
        for pool in ("full", "pool_zb", "pool_lb", "zb_lag1"):
            t0 = time.time()
            sql = _sql_lag1(f, pool) if pool == "zb_lag1" else _sql(f, pool)
            rows = con.execute(sql).fetchall()
            days, m, ir, pos = _stats(rows)
            fm = "  --" if m is None else f"{m:+.4f}"
            fi = "  --" if ir is None else f"{ir:+.2f}"
            fp = "  --" if pos is None else f"{pos*100:4.0f}%"
            line = f"{name:12s} {pool:9s} {days:5d} {fm:>9s} {fi:>7s} {fp:>6s}  ({time.time()-t0:.0f}s)"
            print(line)
        print()
    con.close()
    return None


if __name__ == "__main__":
    main(sys.argv[1:] or None)
