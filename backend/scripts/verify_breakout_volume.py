"""放量突破确认条件（BREAKOUT_VOLUME_RATIO=2.0）的日线近似验证（P2-33②，可复跑，只读）。

背景：`picks/intraday_rules.py` 的 `BREAKOUT_VOLUME_RATIO=2.0` 用「量比 ≥2」确认突破，
是**盘中触发器**口径——本脚本用**日线近似**回答它的核心假设：「量比 ≥2 的突破，
后续收益是否显著优于量比不足的突破」。盘中口径（分钟级）与本近似分开判（P2-33）。

口径（marketdb 10y，与 KB-STOCK-32 同源）：
- 触发组 = 突破（T 收盘 > 近 10 日最高收盘）∧ 量比 v5 ≥ 2.0；对照组 = 突破 ∧ v5 < 2.0；
  v5 = 当日量 / 过去 5 日均量（不含当日，与评分侧量比同基线）；
- 收益 = T 收盘进 → T+1 / T+3 收盘出（**信号强度口径**）；超额 = 个股收益 − 同日全市场
  中位数（strategy_verify 的「同日市场中性」做法）；
- 双口径：全样本 + 剔除当日近涨停（主板 ret0 ≥ 9.5%，不可建仓，P1-22 教训）；
- 分层：v5 ∈ [2, 2.5) / [2.5, 3) / ≥3 —— 检验「越极端越差」是否在突破子集内复现。

判读纪律：样本 ≥120 且 t 显著才谈方向；分年度同号参照 YEAR_CONSISTENCY_MIN=0.60。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.bjtime import beijing_today  # noqa: E402

DB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"
BREAKOUT_VOLUME_RATIO = 2.0  # 与 intraday_rules 同值（import 会拖入依赖链，此处显式对齐并自证）
HH_WINDOW = 10               # 突破参照窗口（近 10 日最高收盘）


def _base_sql(exclude_limit: bool) -> str:
    where = "AND NOT (ret0 >= 0.095 AND board = 'main')" if exclude_limit else ""
    return f"""
WITH px AS (
  SELECT k.thscode, k.date_ms, k.volume, a.close_adj,
         COUNT(*) OVER (PARTITION BY k.thscode ORDER BY k.date_ms
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cnt
  FROM daily_k k JOIN daily_k_adj a USING (thscode, date_ms)
  WHERE k.volume > 0 AND a.close_adj > 0
),
feat AS (
  SELECT thscode, date_ms, close_adj, cnt,
         close_adj / LAG(close_adj) OVER w - 1 AS ret0,
         LEAD(close_adj, 1) OVER w / close_adj - 1 AS fwd1,
         LEAD(close_adj, 3) OVER w / close_adj - 1 AS fwd3,
         MAX(close_adj) OVER (PARTITION BY thscode ORDER BY date_ms
             ROWS BETWEEN {HH_WINDOW} PRECEDING AND 1 PRECEDING) AS hh10,
         volume / NULLIF(AVG(volume) OVER (PARTITION BY thscode ORDER BY date_ms
             ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING), 0) AS v5,
         CASE WHEN thscode LIKE '30%' OR thscode LIKE '68%' OR thscode LIKE '%.BJ'
              THEN 'growth' ELSE 'main' END AS board
  FROM px WINDOW w AS (PARTITION BY thscode ORDER BY date_ms)
),
base AS (
  SELECT * FROM feat
  WHERE cnt >= 60 AND v5 IS NOT NULL AND hh10 IS NOT NULL
    AND fwd1 IS NOT NULL AND fwd3 IS NOT NULL AND abs(fwd1) < 0.5
    {where}
),
withbase AS (
  SELECT b.*, m.m1, m.m3,
         b.fwd1 - m.m1 AS ex1, b.fwd3 - m.m3 AS ex3
  FROM base b JOIN (
    SELECT date_ms, median(fwd1) AS m1, median(fwd3) AS m3 FROM base GROUP BY date_ms
  ) m USING (date_ms)
)
"""


def _group_stats(con: duckdb.DuckDBPyConnection, where: str, exclude_limit: bool) -> dict:
    rows = con.execute(_base_sql(exclude_limit) + f"""
      SELECT year, COUNT(*) AS n, AVG(ex1) AS m1, stddev_samp(ex1) AS s1,
             AVG(ex3) AS m3, stddev_samp(ex3) AS s3
      FROM (SELECT *, year FROM (SELECT *, to_timestamp(date_ms/1000)::DATE AS d,
             year(d) AS year FROM withbase))
      WHERE {where}
      GROUP BY year ORDER BY year
    """).fetchall()
    return {y: {"n": n, "m1": m1, "s1": s1, "m3": m3, "s3": s3} for y, n, m1, s1, m3, s3 in rows}


def _t(m: float | None, s: float | None, n: int) -> float:
    if m is None or not s or n < 2:
        return 0.0
    return m / s * math.sqrt(n)


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.3f}%"


def _run(con: duckdb.DuckDBPyConnection, exclude_limit: bool) -> dict:
    out: dict = {}
    for name, where in (
        ("突破 & 量比≥2.0（触发组）", f"close_adj > hh10 AND v5 >= {BREAKOUT_VOLUME_RATIO}"),
        ("突破 & 量比<2.0（对照）", f"close_adj > hh10 AND v5 < {BREAKOUT_VOLUME_RATIO}"),
        ("非突破（全市场其余）", "close_adj <= hh10"),
    ):
        rows = con.execute(_base_sql(exclude_limit) + f"""
          SELECT COUNT(*) AS n, AVG(ex1) AS m1, stddev_samp(ex1) AS s1,
                 AVG(ex3) AS m3, stddev_samp(ex3) AS s3
          FROM (SELECT *, to_timestamp(date_ms/1000)::DATE AS d FROM withbase)
          WHERE {where}
        """).fetchone()
        n, m1, s1, m3, s3 = rows
        out[name] = {"n": n, "m1": m1, "t1": _t(m1, s1, n), "m3": m3, "t3": _t(m3, s3, n)}
    # 分层：极端量在突破子集内是否复现「越极端越差」
    tiers = con.execute(_base_sql(exclude_limit) + f"""
      SELECT CASE WHEN v5 >= 3 THEN 'v5>=3' WHEN v5 >= 2.5 THEN 'v5 2.5-3' ELSE 'v5 2-2.5' END AS tier,
             COUNT(*) AS n, AVG(ex1) AS m1, stddev_samp(ex1) AS s1
      FROM (SELECT *, to_timestamp(date_ms/1000)::DATE AS d FROM withbase)
      WHERE close_adj > hh10 AND v5 >= {BREAKOUT_VOLUME_RATIO}
      GROUP BY tier ORDER BY tier
    """).fetchall()
    out["tiers"] = [
        {"tier": t, "n": n, "m1": m1, "t1": _t(m1, s, n)} for t, n, m1, s in tiers
    ]
    out["yearly_trigger"] = _group_stats(con, f"close_adj > hh10 AND v5 >= {BREAKOUT_VOLUME_RATIO}", exclude_limit)
    return out


def main() -> int:
    if not DB.exists():
        print(f"ERROR: marketdb 不存在：{DB}", file=sys.stderr)
        return 1
    con = duckdb.connect(str(DB), read_only=True)
    try:
        print(f"## 放量突破确认条件日线近似验证（marketdb，{beijing_today().isoformat()}）\n")
        print(f"- 触发近似 = 突破（T 收盘 > 近 {HH_WINDOW} 日最高收盘）∧ 量比(5日基线) ≥ "
              f"{BREAKOUT_VOLUME_RATIO}；收益 = T 收盘进 → T+1/T+3 收盘出，同日市场中位数中性化\n")
        for label, exclude in (("全样本", False), ("剔除当日近涨停（主板）", True)):
            r = _run(con, exclude)
            print(f"### {label}\n")
            print("| 组 | 样本 | T+1 超额 | t | T+3 超额 | t |")
            print("|---|---|---|---|---|---|")
            for name in ("突破 & 量比≥2.0（触发组）", "突破 & 量比<2.0（对照）", "非突破（全市场其余）"):
                s = r[name]
                print(f"| {name} | {s['n']:,} | {_fmt(s['m1'])} | {s['t1']:+.1f} | {_fmt(s['m3'])} | {s['t3']:+.1f} |")
            print("\n触发组按量比分层：")
            for t in r["tiers"]:
                print(f"- {t['tier']}: n={t['n']:,} · T+1 超额 {_fmt(t['m1'])}（t={t['t1']:+.1f}）")
            ys = r["yearly_trigger"]
            pos = sum(1 for v in ys.values() if (v["m1"] or 0) > 0)
            print(f"- 触发组分年度 T+1 超额同号（正）年份：**{pos}/{len(ys)}**\n")
        print("> 口径声明：日线近似 ≠ 盘中触发器（实际买点在 T 日盘中、量比实时计算）；"
              "ST 未还原；北交所 30% 板未单列。结论只标注实证事实，规则去留另行拍板。")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
