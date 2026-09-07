"""因子评估引擎（docs/factor-library-design.md §4-5）。

口径（防泄露，代码级）：
- signal at T 收盘；entry = T+1 收盘（f1），exit = T+h 收盘（f3/f5/f10/f20）——
  保守执行口径，剔除隔夜跳空虚增；fwd_close_1（T 收盘进）仅作损耗对比，不参与判定；
- T+1 一字板（nb1_high = nb1_low）样本剔除——涨停一字买不进；
- 每日截面 <30 只的 IC 不计入聚合（小截面失真）；
- 复权口径：因子与前瞻收益全用 close_adj；gap/range20 例外（未复权 + 过滤/稀释，
  见 library.py note）。

三层判定（三态纪律，PASS / CONDITIONAL / FAIL，绝不静默放行）：
- PASS        = 有效 + 稳定 + 分层 + 覆盖全过 → 入库；
- CONDITIONAL = 有效 + 稳定 + 覆盖过、分层弱 → 仅辅助维度；
- FAIL        = 其余，留档（负 IC 亦是信息）。
去重：与已入库因子主窗口 IC 序列 |corr| ≥ 0.70 → 标 redundant（不自动淘汰，
由人工在通过清单里取舍）。

纯 DuckDB SQL（backend venv 无 pandas）；评估为离线批任务，不在请求路径。
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.factors.library import (
    FACTORS,
    HORIZON_CLOSE,
    HORIZONS_EXEC,
    FactorDef,
)
from app.market.trading_status import beijing_now

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- 准入阈值（文档 §4 对齐）
IC_ABS_MIN = 0.02          # |RankIC 均值| 下限
ICIR_ABS_MIN = 0.30        # |ICIR|（IC 均值/IC 标准差，非年化）下限
YEAR_CONSISTENCY_MIN = 0.60  # 分年 IC 与全期同号年份占比下限
LS_ANN_MIN = 0.03          # Q5-Q1 多空年化差下限（与 IC 同号）
SAME_DIR_PAIRS_MIN = 3     # 五分位相邻 4 对中至少 3 对与多空方向一致
COVERAGE_MIN = 0.90        # 截面覆盖率下限
IC_CORR_DEDUP = 0.70       # 因子间 IC 相关去重阈值
MIN_CROSS_SECTION = 30     # 每日截面最少股票数
DAYS_PER_YEAR = 243        # A 股年均交易日（年化用）
ROLLING_WINDOW_DAYS = 250  # 滚动衰减监控窗口（约 1 年，制度 §6.1/§7.2）

#: 数据质量硬结论（2026-09-07 marketdb 实测，docs/factor-library-design.md §1.4）
DATA_QUALITY_NOTES = {
    "survivorship": (
        "universe 为当前在市股票全历史（实测最后交易日早于全库最新 90 天的股票 = 0 只），"
        "期间退市股不在库——全期多头端收益存在系统性高估风险，分年结论以近 3 年更可信。"
    ),
    "adjust": (
        "daily_k_adj 仅复权收盘（无复权开高低）：因子/前瞻收益用 close_adj；"
        "gap/range20 为未复权口径（|gap|>25% 过滤 / 20 均稀释），小额分红污染保留。"
    ),
    "turnover": "无流通股本数据，换手率以成交额代理（amt_ratio/liq20/amihud20）。",
    "entry": "前瞻收益 entry = T+1 收盘（保守口径）；T+1 一字板样本剔除（买不进）；ST/停牌未单独过滤。",
    "st_limit": "无 ST 标记，ST 股（±5% 涨跌幅、流动性差）混在样本内，未剔除。",
}


# ---------------------------------------------------------------- 统计小件（标准库）
def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _std(xs: list[float]) -> float | None:
    """样本标准差（手写——本机 homebrew 3.11 的 statistics.stdev 对 float 列表崩）。"""
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


# ---------------------------------------------------------------- SQL 生成
def _base_cte(factor: FactorDef) -> str:
    """完整 base 链（IC 与五分位两处共用）：
    k(join) → lvl1(锚点/前瞻/累计行数) → lvl2(日收益) → lvl3(窗口统计)
    → lvl4(守卫列)。列名契约见 library.py docstring。
    """
    return """
WITH k AS (
    SELECT a.thscode, a.date_ms, a.close_adj,
           b.open_price, b.high_price, b.low_price, b.close_price, b.turnover, b.volume
    FROM daily_k_adj AS a
    JOIN daily_k AS b USING (thscode, date_ms)
),
lvl1 AS (
    SELECT thscode, date_ms, close_adj, open_price, high_price, low_price,
           close_price, turnover, volume,
           LAG(close_adj, 1)   OVER w AS c1,
           LAG(close_adj, 5)   OVER w AS c5,
           LAG(close_adj, 10)  OVER w AS c10,
           LAG(close_adj, 20)  OVER w AS c20,
           LAG(close_adj, 60)  OVER w AS c60,
           LAG(close_adj, 120) OVER w AS c120,
           LAG(close_price, 1) OVER w AS pc1_raw,
           LAG(volume, 1)      OVER w AS vol1,
           ROW_NUMBER() OVER (PARTITION BY thscode ORDER BY date_ms) AS rn,
           LEAD(close_adj, 1)  OVER w AS f1,
           LEAD(close_adj, 3)  OVER w AS f3,
           LEAD(close_adj, 5)  OVER w AS f5,
           LEAD(close_adj, 10) OVER w AS f10,
           LEAD(close_adj, 20) OVER w AS f20,
           LEAD(high_price, 1) OVER w AS nb1_high,
           LEAD(low_price, 1)  OVER w AS nb1_low,
           COUNT(*) OVER (PARTITION BY thscode ORDER BY date_ms
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cnt
    FROM k
    WINDOW w AS (PARTITION BY thscode ORDER BY date_ms)
),
lvl2 AS (
    SELECT *,
           close_adj / NULLIF(c1, 0) - 1 AS ret1,
           -- 真实波幅 TR（未复权）：隔夜跳空部分超过 ±25% 判为除权断层 → NULL（与 gap 因子同口径）
           CASE
             WHEN pc1_raw IS NULL THEN NULL
             WHEN abs(high_price - pc1_raw) > pc1_raw * 0.25
               OR abs(low_price - pc1_raw) > pc1_raw * 0.25 THEN NULL
             ELSE greatest(high_price - low_price,
                           abs(high_price - pc1_raw),
                           abs(low_price - pc1_raw))
           END AS tr_f
    FROM lvl1
),
lvl3 AS (
    SELECT *,
           stddev_samp(ret1) OVER r20c AS vola_raw,
           COUNT(ret1) OVER r20c AS vola_n,
           AVG(turnover) OVER (PARTITION BY thscode ORDER BY date_ms
                               ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS amt20_prev,
           AVG(turnover) OVER r20c AS amt20_cur,
           AVG(abs(ret1) / NULLIF(turnover, 0)) OVER r20c AS amihud_raw,
           COUNT(abs(ret1) / NULLIF(turnover, 0)) OVER r20c AS amihud_n,
           AVG((high_price - low_price) / NULLIF(close_price, 0)) OVER r20c AS range_raw,
           COUNT((high_price - low_price) / NULLIF(close_price, 0)) OVER r20c AS range_n,
           -- 2026-09-07 扩展（qlib Alpha158 / TA-Lib 候选因子注入列）：
           AVG(close_adj) OVER r20c AS ma20_adj,
           stddev_samp(close_adj) OVER r20c AS std20_raw,
           COUNT(close_adj) OVER r20c AS w20_n,
           regr_slope(close_adj, rn) OVER r20c AS slope20,
           regr_intercept(close_adj, rn) OVER r20c AS icept20,
           regr_r2(close_adj, rn) OVER r20c AS rsqr20,
           MAX(high_price) OVER r20c AS max20_h,
           MIN(low_price) OVER r20c AS min20_l,
           arg_max(rn, high_price) OVER r20c AS imax_rn20,
           AVG(CASE WHEN ret1 > 0 THEN 1.0 ELSE 0.0 END) OVER r20c AS cntp20,
           SUM(CASE WHEN ret1 > 0 THEN ret1 ELSE 0.0 END) OVER r20c AS sump20_num,
           SUM(abs(ret1)) OVER r20c AS sump20_den,
           corr(close_adj, ln(volume + 1)) OVER r20c AS corr_pv20,
           corr(ret1, ln(volume / NULLIF(vol1, 0) + 1)) OVER r20c AS cord20_raw,
           AVG(volume) OVER r20c AS vma20_raw,
           stddev_samp(volume) OVER r20c AS vstd20_raw,
           stddev_samp(abs(ret1) * volume) OVER r20c AS wvma20_num,
           AVG(abs(ret1) * volume) OVER r20c AS wvma20_den,
           SUM(CASE WHEN volume > vol1 THEN volume - vol1 ELSE 0.0 END) OVER r20c AS vol_pos20,
           SUM(CASE WHEN volume < vol1 THEN vol1 - volume ELSE 0.0 END) OVER r20c AS vol_neg20,
           AVG(tr_f) OVER r14c AS atr14_raw,
           COUNT(tr_f) OVER r14c AS atr14_n
    FROM lvl2
    WINDOW r20c AS (PARTITION BY thscode ORDER BY date_ms
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
           r14c AS (PARTITION BY thscode ORDER BY date_ms
                    ROWS BETWEEN 13 PRECEDING AND CURRENT ROW)
),
lvl4 AS (
    SELECT *,
           CASE WHEN vola_n >= 20 THEN vola_raw END AS vola20_w,
           CASE WHEN amihud_n >= 20 THEN amihud_raw END AS amihud20_w,
           CASE WHEN range_n >= 20 THEN range_raw END AS range20_w,
           CASE WHEN w20_n >= 20 THEN std20_raw END AS std20_w,
           CASE WHEN atr14_n >= 14 THEN atr14_raw END AS atr14_w
    FROM lvl3
)"""


def _base_sql(factor: FactorDef) -> str:
    """单因子日 IC 全流程：base 链 → scored(因子+fwd+一字过滤) → ranked → daily。"""
    horizons_sql = ", ".join(f"corr(rf, rr{h}) AS ic{h}" for h in HORIZONS_EXEC)
    rank_sql = ", ".join(
        f"percent_rank() OVER (PARTITION BY date_ms ORDER BY fwd_exec_{h}) AS rr{h}"
        for h in HORIZONS_EXEC
    )
    fwd_sql = ", ".join(
        f"f{h} / NULLIF(f1, 0) - 1 AS fwd_exec_{h}" for h in HORIZONS_EXEC
    )
    return f"""{_base_cte(factor)},
scored AS (
    SELECT thscode, date_ms,
           ({factor.expr}) AS f,
           {fwd_sql},
           f1 / NULLIF(close_adj, 0) - 1 AS fwd_close_{HORIZON_CLOSE}
    FROM lvl4
    WHERE cnt >= {factor.min_bars}
      AND nb1_high IS NOT NULL AND nb1_low IS NOT NULL AND nb1_high > nb1_low
),
ranked AS (
    SELECT *,
           percent_rank() OVER (PARTITION BY date_ms ORDER BY f) AS rf,
           {rank_sql},
           percent_rank() OVER (PARTITION BY date_ms ORDER BY fwd_close_{HORIZON_CLOSE}) AS rrc1
    FROM scored
    WHERE f IS NOT NULL
),
daily AS (
    SELECT date_ms, COUNT(*) AS n, {horizons_sql},
           corr(rf, rrc1) AS icc{HORIZON_CLOSE}
    FROM ranked
    GROUP BY date_ms
)
SELECT * FROM daily ORDER BY date_ms
"""


def _quintile_sql(factor: FactorDef, horizon: int) -> str:
    """主窗口五分位（复用完整 base 链——gap/range20 的表达式依赖 lvl3/lvl4 列）：
    全期（yr='ALL'）+ 分年。avg 为逐样本等权（日间不等权，近似）。"""
    return f"""{_base_cte(factor)},
scored AS (
    SELECT thscode, date_ms,
           ({factor.expr}) AS f,
           f{horizon} / NULLIF(f1, 0) - 1 AS fwd
    FROM lvl4
    WHERE cnt >= {factor.min_bars}
      AND nb1_high IS NOT NULL AND nb1_low IS NOT NULL AND nb1_high > nb1_low
),
ranked AS (
    SELECT *,
           ntile(5) OVER (PARTITION BY date_ms ORDER BY f) AS q
    FROM scored
    WHERE f IS NOT NULL AND fwd IS NOT NULL
)
SELECT 'ALL' AS yr, q, AVG(fwd) AS avg_fwd, COUNT(*) AS n FROM ranked GROUP BY q
UNION ALL
SELECT strftime(to_timestamp(date_ms / 1000), '%Y') AS yr, q, AVG(fwd) AS avg_fwd, COUNT(*) AS n
FROM ranked GROUP BY 1, 2
ORDER BY yr, q
"""


# ---------------------------------------------------------------- 单因子评估
@dataclass
class WindowStats:
    horizon: int
    ic_mean: float
    ic_std: float
    icir: float
    pos_rate: float
    n_days: int
    yearly: dict[str, float] = field(default_factory=dict)  # 年 → 年均 IC
    consistency: float | None = None  # 分年同号占比


def _agg_window(daily: list[dict], horizon: int, ic_all: float) -> WindowStats | None:
    key = f"ic{horizon}"
    # NaN 防御：截面零方差（同涨同跌日）时 corr = 0/0 = NaN，不得混入统计
    pts = [
        (r["date_ms"], r[key]) for r in daily
        if r.get(key) is not None and isinstance(r[key], (int, float))
        and not math.isnan(r[key])
    ]
    ics = [ic for _, ic in pts]
    if len(ics) < 30:
        return None
    mean = _mean(ics)
    std = _std(ics) or 0.0
    yearly: dict[str, float] = {}
    for ts, ic in pts:
        yr = datetime.fromtimestamp(ts / 1000).strftime("%Y")
        yearly.setdefault(yr, []).append(ic)
    yearly_mean = {y: _mean(v) for y, v in yearly.items()}
    # 分年同号占比：样本 ≥60 交易日的年份，IC 均值与全期同号
    signs = [
        (math.copysign(1, m) == math.copysign(1, ic_all))
        for y, m in yearly_mean.items()
        if m is not None and len(yearly[y]) >= 60
    ]
    consistency = (sum(signs) / len(signs)) if signs else None
    return WindowStats(
        horizon=horizon,
        ic_mean=round(mean, 4),
        ic_std=round(std, 4),
        icir=round(mean / std, 4) if std > 0 else 0.0,
        pos_rate=round(sum(1 for i in ics if i > 0) / len(ics), 4),
        n_days=len(ics),
        yearly={y: round(m, 4) for y, m in yearly_mean.items() if m is not None},
        consistency=round(consistency, 4) if consistency is not None else None,
    )


def _judge_quintiles(qrows: list[dict]) -> dict:
    """全期五分位 → 多空差/单调性/年化。q1=因子值最低组，q5=最高组。"""
    allq = {r["q"]: r["avg_fwd"] for r in qrows if r["yr"] == "ALL"}
    if len(allq) < 5:
        return {"available": False, "reason": "分位组不足 5（截面过小）"}
    ls = allq[5] - allq[1]
    diffs = [allq[i + 1] - allq[i] for i in range(1, 5)]
    direction = math.copysign(1, ls) if ls != 0 else 0.0
    same_pairs = sum(1 for d in diffs if d != 0 and math.copysign(1, d) == direction)
    return {
        "available": True,
        "q_avg_daily": {str(k): round(v, 6) for k, v in sorted(allq.items())},
        "q_ann": {str(k): round(v * DAYS_PER_YEAR * 100, 2) for k, v in sorted(allq.items())},
        "long_short_ann_pct": round(ls * DAYS_PER_YEAR * 100, 2),
        "monotonic_pairs": same_pairs,  # /4
        "yearly_ls_ann_pct": {
            r["yr"]: round((r["avg_fwd"] - 0.0) * DAYS_PER_YEAR * 100, 2)
            for r in qrows if r["yr"] != "ALL" and r["q"] == 5
        },
    }


def evaluate_factor(con, factor: FactorDef, market_daily: dict[int, int]) -> dict:
    """评估单因子：日 IC 全窗口 → 主窗口五分位 + 覆盖率 → 三层判定。"""
    rows = con.execute(_base_sql(factor)).fetchall()
    cols = [d[0] for d in con.description]
    daily = [dict(zip(cols, r)) for r in rows]

    wins: dict[int, WindowStats] = {}
    ic_series: dict[int, list[tuple[int, float]]] = {}
    for h in (*HORIZONS_EXEC, HORIZON_CLOSE):
        w = _agg_window(daily, h, ic_all=0.0)  # consistency 需 ic_all，二次填充
        if w is not None:
            w2 = _agg_window(daily, h, ic_all=w.ic_mean)
            wins[h] = w2
            # 与 _agg_window 同口径过滤 NaN（截面零方差日的 corr=NaN 不得进入序列）
            ic_series[h] = [
                (r["date_ms"], r[f"ic{h}"]) for r in daily
                if r.get(f"ic{h}") is not None and not math.isnan(r[f"ic{h}"])
            ]

    exec_wins = {h: w for h, w in wins.items() if h in HORIZONS_EXEC}
    # 主窗口：有效窗口（过有效性线）中 |ICIR| 最大者；无有效窗口取 |ICIR| 最大者
    effective = {
        h: w for h, w in exec_wins.items()
        if abs(w.ic_mean) >= IC_ABS_MIN and abs(w.icir) >= ICIR_ABS_MIN
    }
    pool = effective or exec_wins
    if not pool:
        return {
            "name": factor.name, "category": factor.category, "note": factor.note,
            "min_bars": factor.min_bars,
            "coverage": None, "windows": {}, "verdict": "FAIL",
            "reasons": ["有效交易日样本不足（<30 日截面）"],
            "daily_ic": {},
        }
    best_h = max(pool, key=lambda h: abs(pool[h].icir))

    # 覆盖率：有因子值截面数 / 全市场截面数（日级均值）
    cov_pairs = [r["n"] / market_daily[r["date_ms"]] for r in daily if r["date_ms"] in market_daily]
    coverage = round(_mean(cov_pairs), 4) if cov_pairs else None

    # 主窗口五分位
    qrows = con.execute(_quintile_sql(factor, best_h)).fetchall()
    qcols = [d[0] for d in con.description]
    quint = _judge_quintiles([dict(zip(qcols, r)) for r in qrows])

    w = wins[best_h]
    reasons: list[str] = []
    eff_ok = bool(effective)
    stable_ok = w.consistency is not None and w.consistency >= YEAR_CONSISTENCY_MIN
    cov_ok = coverage is not None and coverage >= COVERAGE_MIN
    ls_ann = quint.get("long_short_ann_pct") if quint.get("available") else None
    mono = quint.get("monotonic_pairs") if quint.get("available") else 0
    sign_match = (
        ls_ann is not None
        and math.copysign(1, ls_ann) == math.copysign(1, w.ic_mean)
    )
    layered_ok = (
        quint.get("available")
        and ls_ann is not None and abs(ls_ann) >= LS_ANN_MIN * 100
        and sign_match and mono >= SAME_DIR_PAIRS_MIN
    )
    if not eff_ok:
        reasons.append(
            f"有效性未过线：主窗口 {best_h} 日 IC={w.ic_mean} ICIR={w.icir}"
            f"（需 |IC|≥{IC_ABS_MIN} 且 |ICIR|≥{ICIR_ABS_MIN}）"
        )
    if not stable_ok:
        reasons.append(f"稳定性未过线：分年同号占比 {w.consistency}（需 ≥{YEAR_CONSISTENCY_MIN}）")
    if not layered_ok:
        reasons.append(f"分层未过线：多空年化 {ls_ann}%、单调对 {mono}/4（需 |多空|≥{LS_ANN_MIN * 100}% 同号且 ≥{SAME_DIR_PAIRS_MIN}/4）")
    if not cov_ok:
        reasons.append(f"覆盖率未过线：{coverage}（需 ≥{COVERAGE_MIN}）")

    if eff_ok and stable_ok and layered_ok and cov_ok:
        verdict = "PASS"
    elif eff_ok and stable_ok and cov_ok:
        verdict = "CONDITIONAL"
        reasons.append("分层维度弱 → 限制为辅助维度使用")
    else:
        verdict = "FAIL"

    # 滚动近 250 交易日 IC（衰减监控主指标，制度 §6.1/§7.2 的出库判定输入）：
    # 与全期 IC 对比，同号占比 <50% 或 |IC| 消失 → 触发观察/出库流程
    pts_best = ic_series.get(best_h) or []
    tail = pts_best[-ROLLING_WINDOW_DAYS:]
    roll_ic = round(_mean([ic for _, ic in tail]), 4) if tail else None
    roll_icir = None
    if tail:
        r_std = _std([ic for _, ic in tail])
        roll_icir = round(roll_ic / r_std, 4) if roll_ic is not None and r_std and r_std > 0 else None
    roll_sign_flip = (
        roll_ic is not None
        and w.ic_mean != 0
        and roll_ic != 0
        and abs(roll_ic) >= IC_ABS_MIN
        and math.copysign(1, roll_ic) != math.copysign(1, w.ic_mean)
    )

    return {
        "name": factor.name,
        "category": factor.category,
        "note": factor.note,
        "min_bars": factor.min_bars,
        "best_horizon": best_h,
        "coverage": coverage,
        "rolling": {
            "window_days": ROLLING_WINDOW_DAYS,
            "n_days": len(tail),
            "ic_mean": roll_ic,
            "icir": roll_icir,
            "sign_flip": roll_sign_flip,  # True = 滚动窗与全期系统性反向（结构性失效信号）
        },
        "windows": {
            str(h): {
                "ic_mean": v.ic_mean, "ic_std": v.ic_std, "icir": v.icir,
                "pos_rate": v.pos_rate, "n_days": v.n_days,
                "consistency": v.consistency, "yearly_ic": v.yearly,
            }
            for h, v in wins.items()
        },
        "quintile": quint,
        "verdict": verdict,
        "reasons": reasons,
        "daily_ic": {str(h): ic_series.get(h, []) for h in (best_h,)},
    }


# ---------------------------------------------------------------- 全量评估
def run_full_eval(db_path: str | Path, *, out_path: str | Path | None = None) -> dict:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        n_stocks = con.execute("SELECT count(DISTINCT thscode) FROM daily_k").fetchone()[0]
        rng = con.execute("SELECT min(date_ms), max(date_ms) FROM daily_k").fetchone()
        market_daily = dict(
            con.execute("SELECT date_ms, count(*) FROM daily_k_adj GROUP BY date_ms").fetchall()
        )
        results = []
        for f in FACTORS:
            log.info("evaluating factor %s ...", f.name)
            results.append(evaluate_factor(con, f, market_daily))

        # IC 相关去重（主窗口 daily IC 序列，公共日期交集）：
        # 按 |ICIR| 降序遍历——强因子先入列作锚，弱者标冗余（不依赖定义顺序）
        passed = sorted(
            [r for r in results if r["verdict"] in ("PASS", "CONDITIONAL")],
            key=lambda r: abs(r["windows"][str(r["best_horizon"])]["icir"]),
            reverse=True,
        )
        for r in results:
            r.setdefault("redundant_with", None)
        for i, a in enumerate(passed):
            for b in passed[i + 1:]:
                la = a["daily_ic"].get(str(a["best_horizon"])) or []
                lb = b["daily_ic"].get(str(b["best_horizon"])) or []
                da = {ts: ic for ts, ic in la}
                db = {ts: ic for ts, ic in lb}
                common = sorted(set(da) & set(db))
                if len(common) < 60:
                    continue
                rho = _pearson([da[t] for t in common], [db[t] for t in common])
                if rho is not None and abs(rho) >= IC_CORR_DEDUP:
                    b["redundant_with"] = a["name"]
                    b.setdefault("reasons", []).append(
                        f"与 {a['name']} 主窗口 IC 相关 {rho:.2f} ≥ {IC_CORR_DEDUP}（去重提示，人工取舍）"
                    )

        report = {
            "generated_at": beijing_now().isoformat(timespec="seconds"),
            "protocol": {
                "signal": "T 收盘", "entry": "T+1 收盘（保守执行口径）",
                "exit": [f"T+{h} 收盘" for h in HORIZONS_EXEC],
                "exclusions": ["T+1 一字板", "每日截面 <30 只", "次新（cnt < min_bars）"],
                "thresholds": {
                    "ic_abs_min": IC_ABS_MIN, "icir_abs_min": ICIR_ABS_MIN,
                    "year_consistency_min": YEAR_CONSISTENCY_MIN,
                    "long_short_ann_min_pct": LS_ANN_MIN * 100,
                    "monotonic_pairs_min": SAME_DIR_PAIRS_MIN,
                    "coverage_min": COVERAGE_MIN, "ic_corr_dedup": IC_CORR_DEDUP,
                },
            },
            "universe": {
                "db": str(db_path),
                "stocks": n_stocks,
                "range": [
                    datetime.fromtimestamp(rng[0] / 1000).strftime("%Y-%m-%d"),
                    datetime.fromtimestamp(rng[1] / 1000).strftime("%Y-%m-%d"),
                ],
                "market_daily_avg": round(_mean(list(market_daily.values())) or 0, 0),
            },
            "data_quality": DATA_QUALITY_NOTES,
            "factors": results,
            "summary": {
                "pass": sorted(r["name"] for r in results if r["verdict"] == "PASS"),
                "conditional": sorted(r["name"] for r in results if r["verdict"] == "CONDITIONAL"),
                "fail": sorted(r["name"] for r in results if r["verdict"] == "FAIL"),
            },
        }
        if out_path is not None:
            out = Path(out_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            tmp.replace(out)
            log.info("eval report written to %s", out)
        return report
    finally:
        con.close()
