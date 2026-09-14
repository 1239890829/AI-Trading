"""LightGBM 预测层实验（P0-2，§6.25）——运行环境：backend/.venv-research。

问题：在现有 37 个规则因子（Alpha158 族 + TA-Lib，``app/factors/library.py``）之上，
梯度提升树能否学到**规则因子之外**的预测信息？——用 walk-forward 分年样本外
RankIC 与五分位价差对照单因子基线（mom5/mom20/kmid2）回答。

口径（与 factors/evaluate.py 完全同源，防自建口径漂移）：
- 特征 = ``_base_cte`` 之上逐因子表达式（37 列），NULL 原样保留（LightGBM 原生
  处理缺失，不填补、不凑 0——三态纪律）；
- 标签 = fwd_exec_3（T+1 收盘进、T+3 收盘出，保守执行口径）；
- 过滤 = cnt ≥ 61 + 次日一字板剔除（nb1_high/nb1_low 守卫）；
- 判定门槛 = IC_ABS_MIN=0.02（evaluate.py 同值）+ 分年方向一致性 ≥75%
  + **必须打败同期最强单因子基线**（打不过 = 树模型没学到规则因子之外的东西，
  引入即纯复杂度）。

裁定纪律：本实验只产出**研究报告**——过门槛也只是「候选」，接不接信号体系
另行拍板；不过门槛则否证封存（P1-9 先例：把已验证的否证记成结论，防止重跑）。

用法：
    .venv-research/bin/python scripts/verify_ml_predict.py [--start 2016-01-01]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402
import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.factors.evaluate import _base_cte  # noqa: E402  口径单点（不复制 SQL）
from app.factors.library import FACTORS  # noqa: E402

MARKETDB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"
IC_ABS_MIN = 0.02                 # 与 evaluate.py 同值
MIN_YEAR_CONSISTENCY = 0.75       # 分年方向一致占比门槛
BASELINES = ("mom5", "mom20", "kmid2")
REPORT_DIR = Path(__file__).resolve().parents[2] / ".workbuddy" / "artifacts" / "ml-predict-20260913"


def build_features(start: str, *, use_cache: bool = True) -> pd.DataFrame:
    """base CTE（口径单点复用）× 37 因子列 → 特征/标签矩阵（float32 控内存）。"""
    cols = ",\n           ".join(f"({f.expr}) AS x_{f.name}" for f in FACTORS)
    sql = f"""{_base_cte(FACTORS[0])}
    SELECT thscode, date_ms,
           f3 / NULLIF(f1, 0) - 1 AS label_exec3,
           close_adj / NULLIF(c1, 0) - 1 AS ret1,
           turnover, close_price,
           {cols}
    FROM lvl4
    WHERE cnt >= 61
      AND nb1_high IS NOT NULL AND nb1_low IS NOT NULL AND nb1_high > nb1_low
      AND date_ms >= CAST(? AS BIGINT)
    """
    # ⚠️ 绝对锚定（2026-09-14，KB 读写分叉同族）：原先 `Path("data/cache")` 按**进程 CWD**
    # 解析——从仓库根跑就落 `data/cache`，从 backend/ 跑才落 `backend/data/cache`。
    # 实测缓存文件在 `backend/data/cache/ml_features_20160101.parquet`，
    # 故与同文件 `MARKETDB`（`parents[1]/"data"`）同锚点。
    cache = Path(__file__).resolve().parents[1] / "data" / "cache" / f"ml_features_{start.replace('-', '')}.parquet"
    if use_cache and cache.exists():
        print(f"  命中特征缓存 {cache}")
        return pd.read_parquet(cache)
    con = duckdb.connect(str(MARKETDB), read_only=True)
    try:
        start_ms = int(pd.Timestamp(start, tz="Asia/Shanghai").timestamp() * 1000)
        df = con.execute(sql, [start_ms]).fetchdf()
    finally:
        con.close()

    df["date"] = pd.to_datetime(df["date_ms"], unit="ms", utc=True).dt.tz_convert(
        "Asia/Shanghai").dt.date
    feat_cols = [f"x_{f.name}" for f in FACTORS]
    df[feat_cols] = df[feat_cols].astype("float32")
    df = df.dropna(subset=["label_exec3"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache)
    print(f"  特征已缓存 → {cache}")
    return df


def daily_rank_ic(df: pd.DataFrame, score_col: str, label_col: str = "label_exec3") -> pd.Series:
    """逐日 Spearman IC（截面 ≥30 只才计——MIN_CROSS_SECTION 协议）。"""
    def _ic(g: pd.DataFrame) -> float:
        if len(g) < 30:
            return np.nan
        return g[score_col].rank().corr(g[label_col].rank())

    return df.groupby("date", group_keys=False).apply(_ic).dropna()


def ic_stats(ic: pd.Series) -> dict:
    n = len(ic)
    mean = float(ic.mean())
    t = mean / (float(ic.std()) / np.sqrt(n)) if n > 1 and ic.std() > 0 else 0.0
    return {"ic_mean": round(mean, 5), "ic_t": round(t, 2), "days": int(n)}


def quintile_spread(df: pd.DataFrame, score_col: str, label_col: str = "label_exec3") -> float:
    """五分位 Q5−Q1（逐日 ntile 后等权均值差）。"""
    q = df.groupby("date", group_keys=False).apply(
        lambda g: pd.Series(np.select(
            [g[score_col].rank(pct=True) <= 0.2, g[score_col].rank(pct=True) > 0.8], [1, 5],
            default=3), index=g.index))
    dfq = df.assign(q=q)
    return float(dfq[dfq["q"] == 5]["label_exec3"].mean() - dfq[dfq["q"] == 1]["label_exec3"].mean())


def walk_forward(df: pd.DataFrame, oos_years: list[int]) -> tuple[list[dict], pd.DataFrame]:
    """扩展窗口 walk-forward：训练 < 目标年，预测目标年。返回 (逐年结果, 全 OOS 预测)。"""
    years = df["date"].map(lambda d: d.year)
    feat_cols = [f"x_{f.name}" for f in FACTORS]
    out: list[dict] = []
    oos_frames: list[pd.DataFrame] = []
    for y in oos_years:
        tr = df[years < y]
        te = df[years == y]
        if len(te) == 0 or len(tr) == 0:
            continue
        t0 = time.time()
        model = lgb.LGBMRegressor(
            n_estimators=200, learning_rate=0.05, num_leaves=48,
            subsample=0.8, colsample_bytree=0.8, min_child_samples=200,
            n_jobs=-1, verbose=-1,
        )
        model.fit(tr[feat_cols], tr["label_exec3"])
        pred = model.predict(te[feat_cols])
        te = te.assign(pred=pred)
        ic = daily_rank_ic(te, "pred")
        st = ic_stats(ic)
        gain = model.feature_importances_
        top = sorted(zip(feat_cols, gain), key=lambda x: -x[1])[:5]
        out.append({
            "year": y, "train_rows": len(tr), "test_rows": len(te),
            "ic_mean": st["ic_mean"], "ic_t": st["ic_t"], "ic_days": st["days"],
            "q5_q1": round(quintile_spread(te, "pred"), 5),
            "fit_seconds": round(time.time() - t0, 1),
            "top_features": [name.removeprefix("x_") for name, _ in top],
        })
        oos_frames.append(te[["thscode", "date", "label_exec3", "pred", "ret1",
                              "turnover", "close_price", "x_mom5", "x_mom20", "x_kmid2"]])
    return out, pd.concat(oos_frames) if oos_frames else pd.DataFrame()


def stratified_ic(oos: pd.DataFrame, pred_col: str = "pred") -> list[dict]:
    """分层 OOS IC（B6，§6.25 审计 B6）：回答「模型 edge 是否藏在某个层里」。

    三条分层轴（数据零外呼，全部取自特征帧自身）：
    - 市场日：当日全市场 ret1 中位 >0（强市）/<0（弱市）；
    - 个股体量：该股年内 turnover 中位数三分位（大/中/小——成交额代理市值）；
    - 价格档：该股年内 close_price 中位数三分位（高/中/低——整手/流动性代理）。
    """
    mkt = oos.groupby("date")["ret1"].median().rename("mkt_med")
    o = oos.join(mkt, on="date")
    o["day_regime"] = np.where(o["mkt_med"] > 0, "强市日", "弱市日")
    def _tercile(g: pd.DataFrame, col: str) -> pd.Series:
        q1, q2 = g[col].quantile([1 / 3, 2 / 3])
        return pd.cut(g[col], [-np.inf, q1, q2, np.inf],
                      labels=["低", "中", "高"])
    o["size_tercile"] = o.groupby(["date",])["turnover"].transform(
        lambda x: x)  # 占位：真实分位按股票聚合后再映射
    stock_med = o.groupby("thscode").agg(t_med=("turnover", "median"),
                                         p_med=("close_price", "median"))
    o = o.merge(stock_med, on="thscode", how="left")
    o["size_tercile"] = _tercile(o.drop_duplicates("thscode"), "t_med").reindex(
        o["thscode"].map(o.drop_duplicates("thscode").set_index("thscode").index)).values if False else         pd.cut(o["thscode"].map(stock_med["t_med"]), [-np.inf] + list(
            stock_med["t_med"].quantile([1 / 3, 2 / 3])) + [np.inf], labels=["小盘", "中盘", "大盘"])
    o["price_tercile"] = pd.cut(o["thscode"].map(stock_med["p_med"]), [-np.inf] + list(
        stock_med["p_med"].quantile([1 / 3, 2 / 3])) + [np.inf], labels=["低价", "中价", "高价"])

    def _ic(g: pd.DataFrame) -> float:
        if len(g) < 30:
            return np.nan
        return g[pred_col].rank().corr(g["label_exec3"].rank())

    out: list[dict] = []
    for axis, col in [("市场日", "day_regime"), ("体量", "size_tercile"), ("价格档", "price_tercile")]:
        for val, g in o.groupby(col, observed=True):
            ic_series = g.groupby("date", group_keys=False).apply(_ic).dropna()
            if len(ic_series) == 0:
                continue
            out.append({"axis": axis, "stratum": str(val), "ic_mean": round(float(ic_series.mean()), 4),
                        "days": int(len(ic_series))})
    # 基线：mom20 同层 IC（模型是否在某层打败它）
    base: list[dict] = []
    for axis, col in [("市场日", "day_regime"), ("体量", "size_tercile"), ("价格档", "price_tercile")]:
        for val, g in o.groupby(col, observed=True):
            ic_series = g.groupby("date", group_keys=False).apply(
                lambda gg: gg["x_mom20"].rank().corr(gg["label_exec3"].rank())
                if len(gg) >= 30 else np.nan).dropna()
            if len(ic_series):
                base.append({"axis": axis, "stratum": str(val),
                             "ic_mean": round(float(ic_series.mean()), 4), "days": int(len(ic_series))})
    return _merge_base(out, base)


def _merge_base(model_rows: list[dict], base_rows: list[dict]) -> list[dict]:
    bmap = {(r["axis"], r["stratum"]): r["ic_mean"] for r in base_rows}
    for r in model_rows:
        r["mom20_ic"] = bmap.get((r["axis"], r["stratum"]))
    return model_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--oos", default="2022,2023,2024,2025")
    args = ap.parse_args()

    print("加载特征矩阵（marketdb × 37 因子，口径 = factors/evaluate 同源）…")
    t0 = time.time()
    df = build_features(args.start)
    print(f"  {len(df):,} 行 × {len(FACTORS)} 特征 · {df['date'].min()} → {df['date'].max()} · "
          f"{time.time()-t0:.0f}s")

    oos_years = [int(y) for y in args.oos.split(",")]
    results, oos = walk_forward(df, oos_years)

    lines: list[str] = ["# LightGBM 预测层实验报告（fwd_exec_3 · walk-forward 分年样本外）", ""]
    ok_years = 0
    for r in results:
        pos = r["ic_mean"] > 0
        ok_years += int(pos)
        lines.append(
            f"- {r['year']}: IC={r['ic_mean']:+.4f}（t={r['ic_t']}，{r['ic_days']} 日）· "
            f"Q5−Q1={r['q5_q1']:+.4f} · 训练 {r['train_rows']:,} 行 / {r['fit_seconds']}s · "
            f"Top 特征 {','.join(r['top_features'])}")
    if not results:
        lines.append("- 无 OOS 年份产出（数据不足）")
        print("\n".join(lines))
        return

    # 汇总 IC（逐年 IC 等权）
    ic_all = [r["ic_mean"] for r in results]
    model_mean = float(np.mean(ic_all))
    consistency = ok_years / len(results)
    # 基线：同期单因子 IC（同数据同过滤）
    baselines: dict[str, float] = {}
    for b in BASELINES:
        col = f"x_{b}"
        ic_b = daily_rank_ic(oos, col)
        baselines[b] = float(ic_b.mean())
    best_base = max(baselines.values(), key=abs)

    verdict_pass = (abs(model_mean) >= IC_ABS_MIN) and (consistency >= MIN_YEAR_CONSISTENCY) \
        and (abs(model_mean) > abs(best_base))
    verdict = "✅ 候选通过（进观察项，接信号另行拍板）" if verdict_pass else \
        f"❌ 否决（未同时满足 |IC|≥{IC_ABS_MIN}、方向一致 ≥{MIN_YEAR_CONSISTENCY:.0%}、打败最强单因子 {best_base:+.4f}）"

    strata = stratified_ic(oos)
    lines += ["", "## 分层 OOS IC（B6：模型 edge 是否藏在某个层里——mom20 同层对照）", ""]
    for r in strata:
        lines.append(f"- {r['axis']}·{r['stratum']}: 模型 IC {r['ic_mean']:+.4f} vs mom20 {r['mom20_ic']:+.4f}"
                     f"（{r['days']} 日）")
    lines += [
        "",
        "## 汇总",
        f"- 模型平均 IC = **{model_mean:+.4f}**（{ok_years}/{len(results)} 年同号）",
        "- 同期单因子基线 IC：" + " · ".join(f"{k}={v:+.4f}" for k, v in baselines.items()),
        f"- 裁定：**{verdict}**",
        "",
        "## 纪律声明",
        "- 本报告为实验结论（P1-9 先例：否证即封存，防止重跑）；通过也只是候选，"
        "接入信号体系需另行拍板；模型输出永远作为 bias 输入并带 basis，不做独立决策。",
        "- 口径单点：特征/标签/过滤全部复用 app/factors/evaluate.py 的 base CTE。",
    ]
    text = "\n".join(lines)
    print(text)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "report.md").write_text(text, encoding="utf-8")
    print(f"\n报告已存 {REPORT_DIR / 'report.md'}")


if __name__ == "__main__":
    main()
