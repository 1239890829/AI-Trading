"""P1-22 实证核验：停牌核查/异动扣分参数（`app/picks/halt_risk.py`）是否需要重标。

⚠️ 本脚本 **import `app.picks.halt_risk` 的常量与纯函数**（`assess`/`ABNORMAL_3D_THRESHOLD`/
`PENALTY_*`/`RED_DEV_10D`/`YELLOW_DEV_10D_LO`），并对抽样行调用生产 `assess()` 复算
——「核验口径 = 线上口径」不可能漂移（[[KB-ENG-44]]）。

## 要回答的问题（KB-DEC-018：经验初值不得当结论）

模块末尾原本挂着 `TODO(回测校准)`：`PENALTY_Y1 / PENALTY_Y3 / PENALTY_BOARDS` 与
`RED_DEV_10D` 全是**经验初值**（占位数），文档第 6 节要求用近 6 个月数据回测
「排除红线票」对组合收益与最大回撤的影响后再定稿。本脚本回答四件事：

  ① **触发频率**：池内各规则实际多久触发一次？（0.1% 的规则调不调都无所谓）
  ② **分组前瞻超额**：命中各组（红线 / Y1 / Y2 各板数 / Y3）的标的，
     未来 h 日**相对同板块基准指数的超额**是否**单调更差**？
     —— 扣分是「风险定价」，只有单调更差才谈得上「扣多少分」；
  ③ **分年度稳定性**：上述差在年度上方向是否一致（廉价但有效的稳健性检验）；
  ④ **组合代理**：池内等权日频持有，剔除红线票 vs 不剔除，累计/年化/最大回撤差多少？

## 结论（2026-09-11 跑定；完整判读见 `halt_risk.py` 末尾「校准结论」段）

- **不要按均值显著性调参。** 红线组前瞻超额均值 ≈ 0（h=1 收盘口径 t=−0.20 不显著），
  全池等权剔除红线仅 +0.0016pp/日（t=+0.475），最强 5 只代理剔除后累计收益变好
  但**最大回撤由 −21.59% 恶化到 −28.59%**。按均值看应当删掉这条规则。
  红线真正的依据是**安全不对称性**（偏离 80%+ 距强制停牌 1~2 板；停牌期不可申报
  不可撤单、向上最多 1 个板而向下可连续跌停）——**尾险在均值里看不见**。
- **连板扣分方向在长样本站不住**：收盘口径 boards 1→≥6 超额单调递增（t 最高 +19.9），
  但**可成交口径（T+1 开盘）boards 1~5 全部转负** ⇒ 连板溢价是「收盘价幻觉」。
  分年度 boards≥4 命中组更差仅 0~2/10 年。PENALTY_BOARDS 数值属**经验刻度**。
- **Y3（dev10 ∈ [50,80)）是全表最稳健的信号**：ex5 口径命中组更差 **10/11 年**。

## 口径（三处易错点，均已收口）

- 样本池 = 当日 `boards ≥ 1`（收盘涨停，监管口径）**或** `dev_10d ≥ POOL_DEV10`。
  池子用**远低于待标定阈值**的界线定义（POOL_DEV10=15 ≪ YELLOW_DEV_10D_LO=50），
  否则「池子怎么定义」本身就把「阈值该定哪」的答案预设了。
- 收益一律用**相对基准指数的超额**（不是绝对涨跌）——否则测的是 beta 不是风险；
  前瞻 h 表示「T 日特征 → T+h 日超额」。
- 前复权收盘（marketdb `daily_k_adj`）+ **同日对齐**：偏离值的锚点日期
  （t−3 / t−10 / t−30 行）与指数按**同一交易日**取值，不用「指数 shift N 行」
  （停牌股的行与指数的行不同步，shift 会静默错位）。
- **可成交口径（回撤结论的关键）**：**当日收盘封板的票买不到**，故一律给双口径——
  ①T 日收盘 → T+h 收盘（不可成交，对涨停组**系统性高估**）；
  ②**T+1 开盘 → T+h 收盘**（可成交）。marketdb 仅 `daily_k_adj` 有复权收盘、无复权
  开盘 ⇒ 复权 T+1 开盘 = `open_price × (close_adj / close_price)`（同日折算）。
  **这一条翻转了连板组的结论**，只跑口径①会得出相反的答案。

## 已知边界（如实标注，勿当缺陷）

- **ST 无法历史还原**：marketdb 无名称历史，池内 ST 按主板 10% 判涨停（2026-07-06
  并轨后正确，此前应为 5%）⇒ 2026-07 之前的主板 ST 只有 10% 涨停才算「连板」，
  连板数会**低估**。
- **上市初期无涨跌幅限制**：用 `rn ≥ 31`（上市满 31 个交易日）规避，代价是
  新股上市首月不进样本。
- **停牌跨期**：偏离值按「行」计区间，停牌期间个股价格不变而指数在动，
  该行偏离值失真（与生产 `assess()` 同源同限，非本脚本独有）。

用法：cd backend && .venv/bin/python scripts/verify_halt_risk.py
     （可选 `--window 120` 改主窗口交易日数、`--guard 300` 改抽样复算条数）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import math
from datetime import datetime, timedelta, timezone

import duckdb
import pandas as pd

from app.picks import halt_risk as hr

DB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"

#: 基准指数：生产 `halt_risk.BENCHMARK_INDEX` 的板块 → akshare 新浪代码
BENCH_SINA: dict[str, str] = {
    "sh_main": "sh000001",
    "sz_main": "sz399107",
    "gem": "sz399102",
    "star": "sh000688",
    "bse": "bj899050",
}

#: 样本池：当日收盘涨停，或 10 日超额 ≥ 该值（远低于 YELLOW_DEV_10D_LO=50）
POOL_DEV10 = 15.0

HORIZONS = (1, 3, 5, 10)

#: 主窗口（交易日）——文档要求「近 6 个月」
WINDOW_DAYS = 120

_BJ = timezone(timedelta(hours=8))


def _ms_to_date(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=_BJ).date().isoformat()


def _date_to_ms(d) -> int:
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    if hasattr(d, "date") and not isinstance(d, datetime):
        d = d.date() if hasattr(d, "date") else d
    return int(datetime(d.year, d.month, d.day, tzinfo=_BJ).timestamp() * 1000)


# ------------------------------------------------------------------ 取数

def bench_series() -> tuple[pd.DataFrame, pd.DataFrame]:
    """5 个基准指数的日开/收盘（新浪）。

    :return: (long 表 (bench, date_ms, idx_open, idx_close)  ← 供「同日对齐」查表,
              idx_ret 表 (bench, date_ms, r3, r10, r30, f1, f3, f5, f10) ← 供 SQL 单次 join)
    """
    import akshare as ak

    rows, rets = [], []
    for bench, sym in BENCH_SINA.items():
        df = ak.stock_zh_index_daily(symbol=sym)
        idx = [_date_to_ms(d) for d in df["date"]]
        s = pd.Series([float(c) for c in df["close"]], index=idx).sort_index()
        o = pd.Series([float(c) for c in df["open"]], index=idx).sort_index()
        s = s[~s.index.duplicated()]
        o = o[~o.index.duplicated()]
        for ms, c in s.items():
            rows.append({"bench": bench, "date_ms": int(ms),
                         "idx_open": float(o.get(ms, float("nan"))), "idx_close": float(c)})
        rec = {"bench": bench, "date_ms": s.index.astype("int64")}
        for n in (3, 10, 30):
            rec[f"r{n}"] = (s / s.shift(n) - 1).values * 100
        for n in HORIZONS:
            rec[f"f{n}"] = (s.shift(-n) / s - 1).values * 100
        rets.append(pd.DataFrame(rec))
        print(f"  取数 {bench:8s}({sym}) n={len(s):5d}  "
              f"{df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
    return pd.DataFrame(rows), pd.concat(rets, ignore_index=True)


_SQL = """
CREATE OR REPLACE TEMP TABLE feat AS
WITH k AS (
  SELECT thscode, date_ms, close_adj,
         ROW_NUMBER()        OVER w AS rn,
         LAG(close_adj, 1)   OVER w AS c1,
         LAG(close_adj, 3)   OVER w AS c3,
         LAG(close_adj, 10)  OVER w AS c10,
         LAG(close_adj, 30)  OVER w AS c30,
         LEAD(date_ms, 1)    OVER w AS fd1,
         LEAD(date_ms, 3)    OVER w AS fd3,
         LEAD(date_ms, 5)    OVER w AS fd5,
         LEAD(date_ms, 10)   OVER w AS fd10,
         LEAD(close_adj, 1)  OVER w AS f1,
         LEAD(close_adj, 3)  OVER w AS f3,
         LEAD(close_adj, 5)  OVER w AS f5,
         LEAD(close_adj, 10) OVER w AS f10
  FROM daily_k_adj
  WHERE date_ms BETWEEN ? AND ?
  WINDOW w AS (PARTITION BY thscode ORDER BY date_ms)
),
b AS (
  SELECT *,
    CASE WHEN substr(thscode,1,3) IN ('300','301','688','689') THEN 20.0
         WHEN substr(thscode,1,1) IN ('8','4') OR substr(thscode,1,3) = '920' THEN 30.0
         ELSE 10.0 END AS lim,
    CASE WHEN substr(thscode,1,3) IN ('300','301') THEN 'gem'
         WHEN substr(thscode,1,3) IN ('688','689') THEN 'star'
         WHEN substr(thscode,1,1) IN ('8','4') OR substr(thscode,1,3) = '920' THEN 'bse'
         WHEN substr(thscode,1,1) = '6' THEN 'sh_main'
         ELSE 'sz_main' END AS board
  FROM k
),
s AS (
  SELECT *, CASE WHEN c1 > 0 AND (close_adj/c1 - 1)*100 >= lim - 0.6 THEN 1 ELSE 0 END AS is_lim
  FROM b
),
g AS (
  -- ⚠️ 必须排除当前行（... AND 1 PRECEDING）：否则「非涨停行」与它后面的涨停
  -- 连续段落进同一分组，row_number 从 2 起算 ⇒ 连板数恒 +1（实测 57/300 行）。
  SELECT *, SUM(1 - is_lim) OVER (PARTITION BY thscode ORDER BY date_ms
                                  ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS grp
  FROM s
),
bo AS (
  SELECT *, CASE WHEN is_lim = 1
                 THEN ROW_NUMBER() OVER (PARTITION BY thscode, grp ORDER BY date_ms)
                 ELSE 0 END AS boards
  FROM g
)
SELECT bo.thscode, bo.date_ms, bo.board, bo.boards, bo.rn, bo.close_adj,
       bo.fd1, bo.fd3, bo.fd5, bo.fd10, bo.f1, bo.f3, bo.f5, bo.f10,
       (bo.close_adj/bo.c3  - 1)*100 - i.r3  AS dev3,
       (bo.close_adj/bo.c10 - 1)*100 - i.r10 AS dev10,
       (bo.close_adj/bo.c30 - 1)*100 - i.r30 AS dev30,
       -- T+1 开盘（复权）：daily_k 只有原始价，用同日 close_adj/close_price 折算
       CASE WHEN nk.open_price IS NULL OR nk.close_price IS NULL OR nk.close_price = 0 THEN NULL
            ELSE nk.open_price * (na.close_adj / nk.close_price) END AS adj_open_next
FROM bo
JOIN idx_ret i ON i.bench = bo.board AND i.date_ms = bo.date_ms
LEFT JOIN daily_k     nk ON nk.thscode = bo.thscode AND nk.date_ms = bo.fd1
LEFT JOIN daily_k_adj na ON na.thscode = bo.thscode AND na.date_ms = bo.fd1
WHERE bo.rn >= 31 AND ? <= bo.date_ms
  AND ((bo.close_adj/bo.c10 - 1)*100 - i.r10 >= {pool} OR bo.boards >= 1)
"""

#: 分年处理：每块自带 70 个自然日预热（覆盖 30 行回看），块内窗口函数才正确。
CHUNK_YEARS = range(2017, 2027)
WARMUP_DAYS = 70


def load_features(idx_ret: pd.DataFrame, years=CHUNK_YEARS) -> pd.DataFrame:
    """分年计算个股特征 + 单次 join 指数收益 → 只取池内行到 pandas。

    为什么要分块：`daily_k_adj` 有 1000 万行，一次性做 14 个窗口函数 + 多路 join
    会把 duckdb 临时目录写爆（实测 46.5GiB 上限打满后 OOM）。按年切块后单块
    ~130 万行，峰值内存下降一个数量级，且预热窗口保证块边界回看正确。
    """
    con = duckdb.connect(str(DB), read_only=True)
    con.execute("SET preserve_insertion_order=false")
    con.register("idx_ret", idx_ret)
    out = []
    for y in years:
        lo = datetime(y, 1, 1, tzinfo=_BJ) - timedelta(days=WARMUP_DAYS)
        hi = datetime(y, 12, 31, tzinfo=_BJ)
        ms_lo, ms_hi = int(lo.timestamp() * 1000), int(hi.timestamp() * 1000)
        con.execute(_SQL.format(pool=POOL_DEV10), [ms_lo, ms_hi, ms_lo])
        part = con.execute("SELECT * FROM feat").fetchdf()
        out.append(part)
        print(f"  {y}: 池内行 {len(part):,}")
    con.close()
    df = pd.concat(out, ignore_index=True)
    # 前视：池行必须有完整的 h=10 前瞻，否则按列 NaN 处理（不丢弃）
    return df


# ------------------------------------------------------------------ 统计工具

def t_stat(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return None
    return m / math.sqrt(var / n)


def fmt(v, nd=3) -> str:
    return "—" if v is None else f"{v:+.{nd}f}"


def _clean(xs) -> list[float]:
    out = []
    for v in xs:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f) or math.isinf(f):
            continue
        out.append(f)
    return out


def group_line(name: str, xs) -> str:
    """超额列本身已是百分数（%），此处不再 ×100。"""
    vs = _clean(xs)
    if not vs:
        return f"  {name:22s} n=0"
    m = sum(vs) / len(vs)
    win = sum(1 for v in vs if v > 0) / len(vs)
    return (f"  {name:22s} n={len(vs):6d} 均值{m:+8.3f}% "
            f"中位{pd.Series(vs).median():+8.3f}% 胜率{win*100:5.1f}% "
            f"t={fmt(t_stat(vs))}")


def y1_flags(dev3, board) -> list[bool]:
    out = []
    for v, b in zip(dev3, board):
        out.append(bool(v is not None and not math.isnan(float(v))
                        and float(v) >= hr.ABNORMAL_3D_THRESHOLD[b]))
    return out


# ------------------------------------------------------------------ 主流程

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=WINDOW_DAYS, help="主窗口交易日数")
    ap.add_argument("--guard", type=int, default=300, help="生产 assess() 抽样复算条数")
    args = ap.parse_args()

    print("=" * 104)
    print("P1-22 实证核验：停牌核查/异动扣分参数（halt_risk）")
    print("=" * 104)
    print(f"常量基线：RED_DEV_10D={hr.RED_DEV_10D} Y3下界={hr.YELLOW_DEV_10D_LO} "
          f"PENALTY_Y1={hr.PENALTY_Y1} PENALTY_Y3={hr.PENALTY_Y3} "
          f"PENALTY_BOARDS={hr.PENALTY_BOARDS} MAX={hr.PENALTY_BOARDS_MAX} "
          f"P1={hr.POSITION_FACTOR_P1} P2={hr.POSITION_FACTOR_P2:.4f}")

    idx_long, idx_ret = bench_series()
    df = load_features(idx_ret)
    df["date"] = pd.to_datetime(df["date_ms"].map(_ms_to_date))

    # 指数同日对齐查表：(board, date) → 收盘 / 开盘
    cmap = pd.Series(
        idx_long["idx_close"].values,
        index=pd.MultiIndex.from_arrays([idx_long["bench"], idx_long["date_ms"]]),
    )
    omap = pd.Series(
        idx_long["idx_open"].values,
        index=pd.MultiIndex.from_arrays([idx_long["bench"], idx_long["date_ms"]]),
    )

    def _lookup(mp: pd.Series, col: str) -> pd.Series:
        """(board, 该列日期) → 指数值；缺日期的行返回 NaN。"""
        mask = df[col].notna()
        out = pd.Series(index=df.index, dtype="float64")
        sub = df.loc[mask, ["board", col]]
        keys = pd.Series(list(zip(sub["board"], sub[col].astype("int64"))), index=sub.index)
        out.loc[mask] = keys.map(mp)
        return out

    x0 = _lookup(cmap, "date_ms")
    for h in HORIZONS:
        xf = _lookup(cmap, f"fd{h}")
        stock_fwd = (df[f"f{h}"] / df["close_adj"] - 1) * 100
        df[f"ex{h}"] = stock_fwd - (xf / x0 - 1) * 100

    # 可成交口径：T+1 开盘买入（封板股当日收盘买不到，收盘口径会系统性高估）
    df["oc1"] = (df["f1"] / df["adj_open_next"] - 1) * 100 \
        - (_lookup(cmap, "fd1") / _lookup(omap, "fd1") - 1) * 100
    df["oc5"] = (df["f5"] / df["adj_open_next"] - 1) * 100 \
        - (_lookup(cmap, "fd5") / _lookup(omap, "fd1") - 1) * 100

    all_days = sorted(df["date"].unique())
    print(f"\n池内行数 n={len(df):,}  区间 {all_days[0].date()} ~ {all_days[-1].date()} "
          f"（共 {len(all_days)} 个交易日）")

    main_from = all_days[-args.window]
    w = df[df["date"] >= main_from].copy()
    n_win_days = w["date"].nunique()
    print(f"\n主窗口：{main_from.date()} ~ {all_days[-1].date()}（{n_win_days} 个交易日）"
          f" 行数 n={len(w):,}")

    # ---- ① 触发频率
    print("\n" + "-" * 104)
    print(f"① 触发频率（主窗口池内；池 = 当日涨停 或 dev_10d ≥ {POOL_DEV10:.0f}）")
    print("-" * 104)
    y1 = pd.Series(y1_flags(w["dev3"], w["board"]), index=w.index)
    conds = {
        "R1/R2 红线 dev10≥80": w["dev10"] >= hr.RED_DEV_10D,
        "Y3 dev10∈[50,80)": (w["dev10"] >= hr.YELLOW_DEV_10D_LO) & (w["dev10"] < hr.RED_DEV_10D),
        "Y1 dev3 越线": y1,
        "Y2 boards≥4": w["boards"] >= 4,
        "Y2 boards≥5": w["boards"] >= 5,
        "Y2 boards≥6": w["boards"] >= 6,
        "P1 boards≥3": w["boards"] >= 3,
    }
    for k, m in conds.items():
        m = pd.Series(m).fillna(False)
        daily_n = m.groupby(w["date"]).sum()
        print(f"  {k:22s} n={int(m.sum()):7,d}  占池 {m.mean()*100:6.2f}%  "
              f"有命中的交易日 {int((daily_n > 0).sum()):3d}/{n_win_days}  "
              f"（日均 {daily_n.mean():5.1f} 只）")
    print(f"  {'池基础（当日涨停）':22s} n={int((w['boards'] >= 1).sum()):7,d}  "
          f"占池 {(w['boards'] >= 1).mean()*100:6.2f}%")

    # ---- ② 分组前瞻超额
    print("\n" + "-" * 104)
    print("② 分组前瞻超额（相对同板块基准指数，%；各组独立切分，非互斥）")
    print("-" * 104)
    splits: list[tuple[str, pd.Series]] = [
        ("红线 dev10≥80", (w["dev10"] >= hr.RED_DEV_10D)),
        ("对照 dev10<80", (w["dev10"] < hr.RED_DEV_10D)),
        ("dev10∈[50,80)", ((w["dev10"] >= hr.YELLOW_DEV_10D_LO) & (w["dev10"] < hr.RED_DEV_10D))),
        ("dev10∈[30,50)", ((w["dev10"] >= 30) & (w["dev10"] < hr.YELLOW_DEV_10D_LO))),
        ("dev10<30", (w["dev10"] < 30)),
        ("Y1 命中", y1),
        ("boards 1", (w["boards"] == 1)),
        ("boards 2", (w["boards"] == 2)),
        ("boards 3", (w["boards"] == 3)),
        ("boards 4", (w["boards"] == 4)),
        ("boards 5", (w["boards"] == 5)),
        ("boards ≥6", (w["boards"] >= 6)),
    ]
    for h in HORIZONS:
        col = f"ex{h}"
        print(f"  —— h={h} 交易日（收盘买入口径：T 日收盘 → T+h 收盘） ——")
        for name, m in splits:
            m = pd.Series(m).fillna(False)
            print(group_line(name, w.loc[m, col].tolist()))

    print("\n" + "-" * 104)
    print("②b 可成交口径（T+1 开盘买入——**当日收盘封板的票买不到**，②的收盘口径对涨停组系统性高估）")
    print("-" * 104)
    for col, label in (("oc1", "T+1 开盘 → T+1 收盘"), ("oc5", "T+1 开盘 → T+5 收盘")):
        print(f"  —— {label} ——")
        for name, m in splits:
            m = pd.Series(m).fillna(False)
            print(group_line(name, w.loc[m, col].tolist()))

    # ---- ③ 分年度稳定性
    print("\n" + "-" * 104)
    print("③ 分年度稳定性（全样本；命中组 − 对照组 的超额差，pp；负 = 命中组更差 = 支持扣分）")
    print("-" * 104)
    full = df.copy()
    full["year"] = full["date"].dt.year
    yr_pairs = [
        ("boards≥4 vs 1-3板", (full["boards"] >= 4),
         ((full["boards"] >= 1) & (full["boards"] < 4))),
        ("dev10≥80 vs <80", (full["dev10"] >= hr.RED_DEV_10D),
         (full["dev10"] < hr.RED_DEV_10D)),
        ("dev10∈[50,80) vs <50",
         ((full["dev10"] >= hr.YELLOW_DEV_10D_LO) & (full["dev10"] < hr.RED_DEV_10D)),
         (full["dev10"] < hr.YELLOW_DEV_10D_LO)),
    ]
    for hcol in ("ex1", "ex5"):
        print(f"  —— 目标口径 {hcol}（{'收盘/不可成交' if hcol == 'ex1' else '收盘 h=5'}） ——")
        for label, mg, mb in yr_pairs:
            mg = pd.Series(list(mg)).fillna(False)
            mb = pd.Series(list(mb)).fillna(False)
            mg.index = full.index
            mb.index = full.index
            pos = tot = 0
            parts = []
            for y, g in full.groupby("year"):
                a = _clean(g.loc[mg.loc[g.index], hcol].tolist())
                b = _clean(g.loc[mb.loc[g.index], hcol].tolist())
                if len(a) >= 30 and len(b) >= 30:
                    d = sum(a) / len(a) - sum(b) / len(b)
                    tot += 1
                    pos += 1 if d < 0 else 0
                    parts.append(f"{y}:{d:+.2f}")
            print(f"  {label:24s} 命中组更差 {pos}/{tot} 年  " + " ".join(parts))

    # ---- ④ 组合代理
    print("\n" + "-" * 104)
    print("④ 组合代理：池内等权、每日再平衡、持 1 日（收益为相对基准超额）")
    print("-" * 104)
    pf = w[w["ex1"].notna()].copy()
    pf["keep"] = pf["dev10"] < hr.RED_DEV_10D
    g = pf.groupby("date")
    daily_all = g["ex1"].mean()
    daily_nored = pf.assign(v=pf["ex1"].where(pf["keep"])).groupby("date")["v"].mean()
    n_red = g["keep"].apply(lambda s: int((~s).sum()))
    daily = pd.DataFrame({"all": daily_all, "no_red": daily_nored}).dropna()
    for name, label in (("all", "不剔除红线"), ("no_red", "剔除红线  ")):
        r = daily[name].astype(float)
        cum = (1 + r / 100).cumprod()
        mdd = float((cum / cum.cummax() - 1).min()) * 100
        ann = float(cum.iloc[-1] ** (242 / len(r)) - 1) * 100 if len(r) > 1 else float("nan")
        print(f"  {label:10s} 累计{(cum.iloc[-1]-1)*100:+7.2f}%  年化{ann:+7.1f}%  "
              f"最大回撤{mdd:7.2f}%  日胜率{(r > 0).mean()*100:5.1f}%  日均{r.mean():+.3f}%")
    diff = _clean((daily["no_red"] - daily["all"]).tolist())
    print(f"  逐日差（剔除−全池）：均值 {sum(diff)/len(diff):+.4f}pp  "
          f"t={fmt(t_stat(diff))}  窗口内被剔除 {int(n_red.sum())} 个标的·日")
    by_board = pf[~pf["keep"]].groupby("board")["ex1"].agg(["count", "mean"])
    if len(by_board):
        print("  被剔除样本的板块分布：" + " | ".join(
            f"{b} n={int(r['count'])} 超额{r['mean']:+.3f}%" for b, r in by_board.iterrows()))

    # ④ 只回答「全池等权」；真实 picks 是每日 ≤5 只 → 再看「每日最强 5 只」口径
    print("\n  附：每日最强 5 只（按 dev10 降序，近似 picks 的 5 只容量）——剔除红线的影响在该口径被放大")
    same_days = sorted(pf["date"].unique())
    for hcol, clabel in (("ex1", "收盘口径(不可成交)"), ("oc1", "开盘口径(可成交)")):
        top = pf.dropna(subset=[hcol]).sort_values("dev10", ascending=False).groupby("date").head(5)
        rows = []
        for d, g in top.groupby("date"):
            keep = g[g["keep"]][hcol]
            rows.append({"date": d, "all": g[hcol].mean(),
                         "fill0": keep.mean() if len(keep) else 0.0, "refill": keep.mean()})
        tdf = pd.DataFrame(rows).set_index("date").reindex(same_days)
        print(f"  —— {clabel} ——")
        for nm, col in (("最强5·不剔除", "all"), ("最强5·剔红线(空仓补0)", "fill0"),
                        ("最强5·剔红线(次强补位)", "refill")):
            r = tdf[col].dropna().astype(float)
            if r.empty:
                continue
            cum = (1 + r / 100).cumprod()
            mdd = float((cum / cum.cummax() - 1).min()) * 100
            print(f"  {nm:22s} 累计{(cum.iloc[-1]-1)*100:+8.2f}%  最大回撤{mdd:7.2f}%  "
                  f"日均{r.mean():+.3f}%  日胜率{(r > 0).mean()*100:5.1f}%  样本日 {len(r)}")
    top = pf.sort_values("dev10", ascending=False).groupby("date").head(5)
    short_days = int((top[top["keep"]].groupby("date").size() < 5).sum())
    print(f"  红线票出现在最强 5 只中：{int((~top['keep']).sum())} 个标的·日"
          f"（占 {(~top['keep']).mean()*100:.2f}%）；剔除后不足 5 只的交易日 {short_days} 个")

    # ---- ⑤ 生产函数一致性守卫
    print("\n" + "-" * 104)
    print(f"⑤ 口径守卫：随机抽 {min(args.guard, len(w))} 行用生产 assess() 复算（board/boards/dev_10d）")
    print("-" * 104)
    guard = w.sample(min(args.guard, len(w)), random_state=17)
    con = duckdb.connect(str(DB), read_only=True)
    idx_lookup: dict[str, list[tuple[int, float]]] = {}
    for r in idx_long.itertuples():
        idx_lookup.setdefault(r.bench, []).append((int(r.date_ms), float(r.idx_close)))
    for k in idx_lookup:
        idx_lookup[k].sort()
    bad = 0
    for row in guard.itertuples():
        bars = con.execute(
            "SELECT date_ms, close_adj FROM daily_k_adj WHERE thscode=? AND date_ms<=? "
            "ORDER BY date_ms DESC LIMIT 40", [row.thscode, int(row.date_ms)]
        ).fetchall()
        bars = [{"close": float(c)} for _, c in reversed(bars)]
        ibars = [{"close": c} for ms, c in idx_lookup[row.board] if ms <= int(row.date_ms)]
        res = hr.assess(symbol=row.thscode.split(".")[0], bars=bars, index_bars=ibars)
        if (res["board"] != row.board or res["boards"] != int(row.boards)
                or abs(float(res["dev_10d"] or 0) - float(row.dev10)) > 0.05):
            bad += 1
            if bad <= 3:
                print(f"  ✗ {row.thscode} {_ms_to_date(row.date_ms)} 生产 "
                      f"board={res['board']}/boards={res['boards']}/dev10={res['dev_10d']} "
                      f"vs SQL {row.board}/{int(row.boards)}/{float(row.dev10):.2f}")
    con.close()
    print(f"  抽样 {len(guard)} 行，不一致 {bad} 行 → {'✅ 口径一致' if bad == 0 else '❌ 存在漂移'}")

    print("\n" + "-" * 104)
    print("说明：以上为**实测分布**，不构成买卖建议；样本边界见脚本 docstring（ST/新股/停牌）。")
    print("-" * 104)


if __name__ == "__main__":
    main()
