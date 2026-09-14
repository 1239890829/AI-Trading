#!/usr/bin/env python
"""窗口敏感性验证（账本 §6.0 `RSH-002`，2026-09-14）。

## 要回答的问题

现有因子**几乎全是 20 日单窗**（唯一例外是 `mom` 族：5/10/20/60/120）。自然的问题是：

> 这些因子的 PASS 结论，**是对窗口稳健的，还是恰好落在 20 日窗**？

这不是学术好奇：`mom` 族已给出反例——同一算子仅换窗口，verdict 就从
**FAIL(mom5) → CONDITIONAL(mom10) → PASS(mom20) → PASS(mom60) → FAIL(mom120)**。
若某个「20 日 PASS」因子实际只在 20 日成立，它就是**窗口过拟合**，
按制度 §4.3 不该占代表因子位。

## 口径：参数化 base 链 + 复用生产管线（**零 SQL 复制**）

早期版本把「窗口化表达式」写在 `scored` 层（如 `AVG(turnover) OVER (ROWS BETWEEN ...)`），
**这是错的**，且被自证正确拦下：生产列（`amt20_cur` 等）在 `lvl3` 计算，那时**尚未**
经 `scored` 的 `WHERE cnt >= min_bars` 过滤；而写在 `scored` 里的窗口函数只看到**过滤后**的
行集 ⇒ frame 覆盖的真实样本不同（每只股票最早 20 行被剔除，窗口整体后移）。
⇒ **同一算式在不同计划阶段不等价**。

现改为**参数化 base 链**：把生产 `_base_cte` 的 frame 与守卫按窗口落值，**列名保持不变**
（`amt20_cur` / `vola20_w` / `slope20` … 名字里的 `20` 是历史命名，语义由 `w` 决定），
于是全部 21 条生产 `FactorDef.expr` 可**逐字复用**，且过滤阶段与生产完全一致。
`_base_cte` 是**常量 SQL**（签名里的 `factor` 参数全未被使用，已核验），故参数化是单点且可证。

| 环节 | 来源 |
|---|---|
| base 链（`lvl1`~`lvl4`） | `evaluate._base_cte` 文本 + 定点参数化（下方 `_base_cte_param`） |
| IC 管线（scored / ranked / daily，含 R16 成熟分区） | `evaluate._base_sql` 尾部**逐字**复用（只换数据来源） |
| 聚合（ic_mean / icir / consistency） | `evaluate._agg_window` |
| 阈值 | `evaluate.IC_ABS_MIN` 等常量 |

**`min_bars` 口径**：生产规律是 `min_bars = 生产窗 + 1`（`liq20`→21、`atr14`→15），
作用是保证 frame 内满窗。扫描时**同步取 `w + 1`**，使 `w == 生产窗` 时与生产完全一致。
（另一种选择是固定 `min_bars` 以「不改筛选面」，但那会让长窗出现半截窗口；
此处取「随窗缩放」以维持满窗语义，并在自证中验证其与生产一致。）

## 自证（本脚本可信的前提）

对每个算子，在**其生产窗**下跑参数化版本，与真正的生产因子逐点比对：

- **日度 IC 序列**（`ic3/ic5/ic10/ic20`）——判据 `|Δ| ≤ 1e-9`。
  实测基线：同一 SQL 连跑两轮的**自身抖动 ~1e-16**（并行浮点累加顺序；
  与账本 `BUG-002` 同源）⇒ `1e-12` 严格判等会全数误报，`1e-9` 留 7 个数量级余量。
- **聚合统计**（`ic_mean` / `icir` / `consistency`，已 round 到 4 位）——要求**完全相等**。

不等 ⇒ **立即中止**，不产出任何结论（宁可无结论，不可给错结论）。

## 范围声明（诚实边界，勿过度解读）

- 只跑**有效性 + 稳定性 + 覆盖率**三层，**不跑五分位分层**。原因有二：①分层要额外
  `ntile(5)` 全表扫描，成本高；②`ntile` 无 tie-break 属已知缺陷（账本 `BUG-002`，
  并行下分位归属会抖）⇒ 用它当判据会引入噪声。
- 因此本脚本的判定是「**至少 CONDITIONAL**」的上界结论（三层全过 ⇒ 至少 CONDITIONAL；
  任一层不过 ⇒ 必然 FAIL），**不是完整 verdict**。完整 verdict 仍以 `run_full_eval` 为准。
- **跨窗样本池不同，且是必然的**：`min_bars = w + 1` 保证「满窗」，于是大窗会剔除
  「上市不足 w+1 日」的样本 ⇒ `w=60` 的池严格小于 `w=5`。这不是缺陷而是**窗口语义的
  固有代价**（若固定 `min_bars` 让长窗吃半截窗口，那就变成「在比不同的东西」）。
  读结论时**只可比各窗的判定与 ICIR 量级，不可把跨窗 IC 差直接归因于窗口本身**。
- 本脚本**不写 library.py、不改因子池、不产出报告文件**（除显式 `--out`）。

## 用法

    cd backend && .venv/bin/python scripts/verify_window_sensitivity.py            # 全量
    cd backend && .venv/bin/python scripts/verify_window_sensitivity.py --probe    # 只跑自证
    cd backend && .venv/bin/python scripts/verify_window_sensitivity.py --only liq,cord
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402

from app.factors.evaluate import (  # noqa: E402
    COVERAGE_MIN,
    ICIR_ABS_MIN,
    IC_ABS_MIN,
    MIN_CROSS_SECTION,
    YEAR_CONSISTENCY_MIN,
    _agg_window,
    _base_cte,
    _base_sql,
    _mature_key,
    _mean,
)
from app.factors.library import (  # noqa: E402
    FACTOR_BY_NAME,
    FACTORS,
    HORIZONS_EXEC,
)

MARKETDB = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"

#: 扫描窗口。`20` 是**基线锚点**（现有因子的生产窗），用来做自证与对照；
#: 5/10/30/60 是账本 `RSH-002` 登记要求的扫描窗。
WINDOWS: tuple[int, ...] = (5, 10, 20, 30, 60)

#: 生产 `atr14` 的窗口不在扫描网格内，自证时单独使用。
ATR_PROD_WINDOW = 14

#: 生产列名里的 `20`/`14` 是历史命名，语义由实际窗口决定 ⇒ 参数化后**不改列名**，
#: 这样 `FactorDef.expr` 才能逐字复用。改名会波及 `w20_n`/`r20c`/`atr14_n` 等，得不偿失。
_RAW = _base_cte(FACTORS[0])


@dataclass(frozen=True)
class OpSpec:
    """一个可窗口化的算子。

    `axis` 指明该算子由哪个 frame 承载：`"w"` → 生产 `r20c`（20 日轴）；
    `"aw"` → 生产 `r14c`（`atr` 的 14 日轴，扫描时与 `w` 同步）。
    `expr_tpl` 为 `None` 时**逐字沿用生产 `FactorDef.expr`**。
    """

    op: str
    prod: str
    prod_window: int
    axis: str = "w"
    expr_tpl: str | None = None  # 仅 `imax` 需要（生产 expr 里硬编码 `/20.0`）


#: 扫描清单。`prod` = 对应生产因子（自证基准），`prod_window` = 它的生产窗。
OPS: tuple[OpSpec, ...] = (
    # ── 流动性 / 波动 / 价量结构（首批 + 扩展批） ────────────────────────
    OpSpec("liq", "liq20", 20),
    OpSpec("vola", "vola20", 20),
    OpSpec("amihud", "amihud20", 20),
    OpSpec("range", "range20", 20),
    OpSpec("std", "std20", 20),
    OpSpec("atr", "atr14", ATR_PROD_WINDOW, axis="aw"),
    # ── 趋势回归族 ──────────────────────────────────────────────────────
    OpSpec("beta", "beta20", 20),
    OpSpec("rsqr", "rsqr20", 20),
    OpSpec("resi", "resi20", 20),
    OpSpec("bias", "bias20", 20),
    # ── 位置 / 突破族 ───────────────────────────────────────────────────
    OpSpec("rsv", "rsv20", 20),
    OpSpec("max", "max20", 20),
    OpSpec("imax", "imax20", 20, expr_tpl="(rn - imax_rn20) / {w}.0"),
    # ── 动量统计族 ──────────────────────────────────────────────────────
    OpSpec("cntp", "cntp20", 20),
    OpSpec("sump", "sump20", 20),
    # ── 价量关系族（全库最强） ──────────────────────────────────────────
    OpSpec("corr_pv", "corr_pv20", 20),
    OpSpec("cord", "cord20", 20),
    # ── 量能族 ──────────────────────────────────────────────────────────
    OpSpec("vma", "vma20", 20),
    OpSpec("vstd", "vstd20", 20),
    OpSpec("wvma", "wvma20", 20),
    OpSpec("vsumd", "vsumd20", 20),
)


# ---------------------------------------------------------------- 参数化 base 链
def _base_cte_param(w: int, aw: int) -> str:
    """把生产 `_base_cte` 的窗口落值：`r20c` 帧 → `w`，`r14c` 帧 → `aw`，守卫同步。

    **用占位符分两步替换**，不可直接 `>= 20` → `>= w` 后再替换 `>= 14`——
    当 `w == 14` 时第二步会命中第一步的产物（属 KB-ENG-86「替换面过宽」同族，已在探针中踩过）。
    """
    sql = _RAW
    sql = sql.replace(">= 20", ">= __GW__").replace(">= 14", ">= __GAW__")
    sql = sql.replace("ROWS BETWEEN 19 PRECEDING AND CURRENT ROW",
                      "ROWS BETWEEN __W1__ PRECEDING AND CURRENT ROW")
    sql = sql.replace("ROWS BETWEEN 13 PRECEDING AND CURRENT ROW",
                      "ROWS BETWEEN __AW1__ PRECEDING AND CURRENT ROW")
    for token, val in (("__GW__", w), ("__GAW__", aw), ("__W1__", w - 1), ("__AW1__", aw - 1)):
        sql = sql.replace(token, str(val))
    return sql


#: 参数化恒等性的**结构自证**：`(20, 14)` 必须逐字还原生产 base 链。
#: 这是下面所有结论的地基——不成立则整个脚本无意义。
assert _base_cte_param(20, ATR_PROD_WINDOW) == _RAW, "参数化未还原生产 base 链"


#: 物化时排除的列：本扫描的 21 个算子与 IC 管线**都不引用**它们
#: （`lvl1` 的 7 个 LAG 列、`open_price`、`rank20_*` —— `rank20` 不在扫描清单内）。
#: 排除只为**省盘**：不排除时单张表约 4 GB，2026-09-14 实测曾把临时盘撑到 34.9 GiB 而 OOM。
#: ⚠️ 排除面必须与「被引用的列」严格互斥；自证的**保真臂走的正是这条物化路径**，
#: 故一旦误排除，自证会立刻变红（不会静默给出错结论）。
_EXCLUDE: tuple[str, ...] = (
    "open_price", "c1", "c5", "c10", "c20", "c60", "c120", "pc1_raw",
    "rank20_lt", "rank20_le", "rank20_w",
)


def _materialize_sql(w: int, aw: int) -> str:
    """参数化 base 链 + 末级 SELECT（`_base_cte` 本身不含最终 SELECT 语句）。"""
    return (f"{_base_cte_param(w, aw)}\n"
            f"SELECT * EXCLUDE ({', '.join(_EXCLUDE)}) FROM lvl4")


def _build_base(con, w: int, aw: int) -> str:
    """物化参数化 base 链，返回表名。

    ⚠️ **调用方必须用 `_drop_base` 释放**。本脚本 2026-09-14 首版把物化表缓存在 dict 里
    从不释放，而循环是「算子优先」⇒ 最多 10 张宽表并存、实测累计 34.9 GiB 触发
    `OutOfMemoryException`。改「窗口优先 + 用后即弃」后峰值降到 1 张表。
    """
    table = f"base_w{w}_a{aw}"
    con.execute(f"CREATE OR REPLACE TEMP TABLE {table} AS {_materialize_sql(w, aw)}")
    return table


def _drop_base(con, table: str) -> None:
    """释放物化表（`DROP` 后 DuckDB 才回收其磁盘占用）。"""
    con.execute(f"DROP TABLE IF EXISTS {table}")


def _pipeline_sql(expr: str, min_bars: int, base_table: str) -> str:
    """生产 `_base_sql` 的 scored/ranked/daily **逐字复用**，只把数据来源换成物化表。

    只换 `FROM lvl4` 一处；CTE 前缀（`k`/`lvl1`~`lvl4`）整体丢弃。
    """
    from app.factors.library import FactorDef

    fake = FactorDef(name="_probe", category="_probe", min_bars=min_bars, expr=expr, note="")
    full = _base_sql(fake)
    prefix = _base_cte(fake) + ","
    if not full.startswith(prefix):
        raise AssertionError("生产 `_base_sql` 结构已变（CTE 前缀假设失效），本脚本需同步")
    body = full[len(prefix):]
    if body.count("FROM lvl4") != 1:
        raise AssertionError(f"`FROM lvl4` 出现 {body.count('FROM lvl4')} 次，预期 1 次")
    return "WITH " + body.replace("FROM lvl4", f"FROM {base_table}")


def _body_of(sql: str) -> str:
    """取 `_base_sql` 的**尾段**（丢掉常量 CTE 前缀），供结构自证逐字比对。"""
    prefix = _RAW + ","
    if not sql.startswith(prefix):
        raise AssertionError("生产 `_base_sql` 结构已变（CTE 前缀假设失效），本脚本需同步")
    return sql[len(prefix):]


def _frames(spec: OpSpec, w: int) -> tuple[int, int]:
    """(r20c 帧, r14c 帧)。

    非 `atr` 算子走 20 日轴 ⇒ `(w, 14)`；`atr` 走 14 日轴 ⇒ `(20, w)`——
    **`r20c` 固定留在生产值 20**：`atr` 的 expr 只引用 `atr14_w`/`atr14_raw`/`atr14_n`，
    r20 列对它无影响；固定住之后 `atr` 在生产窗下生成的 base 链与生产**逐字相同**
    （`_base_cte_param(20, 14) == _base_cte`），结构自证才能覆盖它。
    """
    return (20, w) if spec.axis == "aw" else (w, ATR_PROD_WINDOW)


def _op_expr(spec: OpSpec, w: int) -> str:
    """该算子在窗口 `w` 下的 expr：默认逐字沿用生产，仅 `imax` 需替换硬编码常数。"""
    if spec.expr_tpl is None:
        return FACTOR_BY_NAME[spec.prod].expr
    return spec.expr_tpl.format(w=w)


# ---------------------------------------------------------------- IC 管线
def _agg_from_daily(daily: list[dict], market_daily: dict[int, int]) -> dict:
    """把日度行聚合成与 `_ic_of` 同形的记录（供自证对照）。"""
    daily_ic = [r for r in daily if r["n"] >= MIN_CROSS_SECTION]
    wins: dict[str, dict] = {}
    for h in HORIZONS_EXEC:
        n_key = _mature_key(h)
        w0 = _agg_window(daily_ic, h, ic_all=0.0, n_key=n_key)
        if w0 is None:
            continue
        w1 = _agg_window(daily_ic, h, ic_all=w0.ic_mean, n_key=n_key)
        wins[str(h)] = {
            "ic_mean": w1.ic_mean, "ic_std": w1.ic_std, "icir": w1.icir,
            "consistency": w1.consistency, "n_days": w1.n_days, "pos_rate": w1.pos_rate,
        }
    cov_pairs = [r["n"] / market_daily[r["date_ms"]]
                 for r in daily if r["date_ms"] in market_daily]
    return {"windows": wins, "coverage": round(_mean(cov_pairs), 4) if cov_pairs else None,
            "daily": daily}


def _ic_of(con, spec: OpSpec, w: int, table: str, market_daily: dict[int, int]) -> dict:
    """跑一条 IC 管线并聚合——**复用生产 `_base_sql` 尾段 / `_agg_window`，不自写算式**。

    `table` 由调用方按 `_frames(spec, w)` 物化并负责释放（见 `_build_base`）。
    `min_bars = w + 1` 与生产规律一致（见模块 docstring）。
    """
    sql = _pipeline_sql(_op_expr(spec, w), w + 1, table)
    rows = con.execute(sql).fetchall()
    cols = [d[0] for d in con.description]
    return _agg_from_daily([dict(zip(cols, r)) for r in rows], market_daily)


def _judge(rec: dict) -> tuple[str, str]:
    """三层判定（**不含分层**）：有效 + 稳定 + 覆盖。

    与生产 `evaluate_factor` 的 `eff_ok`/`stable_ok`/`cov_ok` 同判据同阈值，
    但**刻意不含 `layered_ok`**（见模块 docstring「范围声明」）。
    返回 `(上层判定, 主窗)`；上层判定 ∈ {至少CONDITIONAL, FAIL}。
    """
    wins = {int(h): v for h, v in rec["windows"].items()}
    if not wins:
        return "FAIL", "-"
    effective = {
        h: v for h, v in wins.items()
        if abs(v["ic_mean"]) >= IC_ABS_MIN and abs(v["icir"]) >= ICIR_ABS_MIN
    }
    pool = effective or wins
    best_h = max(pool, key=lambda h: abs(pool[h]["icir"]))
    w = wins[best_h]
    eff_ok = bool(effective)
    stable_ok = w["consistency"] is not None and w["consistency"] >= YEAR_CONSISTENCY_MIN
    cov_ok = rec["coverage"] is not None and rec["coverage"] >= COVERAGE_MIN
    return ("至少CONDITIONAL" if (eff_ok and stable_ok and cov_ok) else "FAIL"), str(best_h)


# ---------------------------------------------------------------- 自证
IC_TOL = 1e-9  #: 日度 IC 逐点容差。实测自身抖动 ~1e-16（并行浮点累加），留 7 个数量级余量。

#: 保真臂抽样的算子（见 `_selfcheck` docstring）：覆盖裸均值 / 相关算子 / 14 日轴三类。
FIDELITY_PROBE: tuple[str, ...] = ("liq", "corr_pv", "atr")


def _diff_daily(a: list[dict], b: list[dict]) -> list[str]:
    keys = ["ic3", "ic5", "ic10", "ic20"]
    if len(a) != len(b):
        return [f"日度行数 {len(a)} vs {len(b)}"]
    bad: list[str] = []
    for ra, rb in zip(a, b):
        if ra["date_ms"] != rb["date_ms"]:
            bad.append(f"日期错位 {ra['date_ms']} vs {rb['date_ms']}")
            continue
        for k in keys:
            x, y = ra.get(k), rb.get(k)
            if x is None and y is None:
                continue
            if x is None or y is None:
                bad.append(f"d={ra['date_ms']} {k}: {x!r} vs {y!r}")
            elif abs(x - y) > IC_TOL:
                bad.append(f"d={ra['date_ms']} {k}: {x!r} vs {y!r} (Δ{y - x:+.3e})")
    return bad


def _diff_agg(a: dict, b: dict) -> list[str]:
    bad: list[str] = []
    if not a["windows"] or not b["windows"]:
        return [f"窗口为空 {list(a['windows'])} vs {list(b['windows'])}"]
    for h, va in a["windows"].items():
        vb = b["windows"].get(h)
        if vb is None:
            bad.append(f"h={h} 缺窗口")
            continue
        for k in ("ic_mean", "icir", "consistency", "n_days"):
            if va[k] != vb[k]:
                bad.append(f"h={h}.{k} {va[k]!r}≠{vb[k]!r}")
    if a["coverage"] != b["coverage"]:
        bad.append(f"coverage {a['coverage']!r}≠{b['coverage']!r}")
    return bad


def _selfcheck(con, market_daily: dict[int, int]) -> list[str]:
    """自证，两臂：

    **臂 1 · 结构（全 21 算子，瞬时）**——`w == 生产窗` 时，本脚本生成的完整 IC SQL
    必须与生产 `_base_sql` 的**尾段逐字相同**；base 链另由模块级
    `_base_cte_param(20, 14) == _base_cte` 的字符串等值保证。
    ⇒ 两臂之间唯一的差别只剩**数据来源**（CTE vs 物化表）。
    （`atr` 能进这一臂，正是 `_frames` 把 `r20c` 固定在 20 的原因。）

    **臂 2 · 保真（抽样实跑）**——结构既然已同，剩下的唯一风险就是「物化是否改变结果」。
    抽 3 个算子实跑生产路径 vs 物化路径比对，覆盖三类：`liq`（无守卫的裸均值）、
    `corr_pv`（相关算子，浮点累加顺序最敏感）、`atr`（14 日轴）。判据同 `IC_TOL`。

    不逐算子实跑全部 21 项的理由：结构臂已证明 SQL 逐字相同，而「物化是否保真」是
    数据来源的属性、与算子无关，抽样即可；逐算子实跑要多花约 12 分钟且不增加信息。
    """
    print("=" * 78)
    print(f"① 自证（臂 1 结构 · 全 {len(OPS)} 算子；臂 2 保真 · 抽样实跑，|Δ|≤{IC_TOL:g}）")
    print("=" * 78)

    # ── 臂 1：结构 ────────────────────────────────────────────────────
    struct_fails: list[str] = []
    for spec in OPS:
        w = spec.prod_window
        prod = FACTOR_BY_NAME[spec.prod]
        mine = _pipeline_sql(_op_expr(spec, w), w + 1, "lvl4")
        want = "WITH " + _body_of(_base_sql(prod))
        if mine != want:
            struct_fails.append(spec.op)
    ok = len(OPS) - len(struct_fails)
    print(f"  臂 1 {len(OPS)} 项：✅ {ok} 逐字相同"
          + ("" if not struct_fails else f"；❌ {struct_fails}"))

    # ── 臂 2：保真 ────────────────────────────────────────────────────
    # 三个抽样算子的 `_frames` 皆为 `(20, 14)`（`atr` 的 r20c 固定在 20）⇒ 共用一张表。
    fidelity_fails: list[str] = []
    table = _build_base(con, 20, ATR_PROD_WINDOW)
    try:
        for op in FIDELITY_PROBE:
            spec = next(s for s in OPS if s.op == op)
            w = spec.prod_window
            if _frames(spec, w) != (20, ATR_PROD_WINDOW):
                raise AssertionError(
                    f"{op} 的帧 {_frames(spec, w)} 与共用表 (20, 14) 不符，需改自证布局"
                )
            prod = FACTOR_BY_NAME[spec.prod]
            rows = con.execute(_base_sql(prod)).fetchall()
            cols = [d[0] for d in con.description]
            ref_daily = [dict(zip(cols, r)) for r in rows]
            ref = _agg_from_daily(ref_daily, market_daily)
            got = _ic_of(con, spec, w, table, market_daily)
            bad = _diff_daily(ref_daily, got["daily"]) + _diff_agg(ref, got)
            note = "" if not bad else "  " + "; ".join(bad[:3])
            print(f"  臂 2 {spec.op:8} @w{w:<3} (prod={spec.prod:9}) "
                  f"{'✅ 物化路径等价' if not bad else '❌'}{note}")
            if bad:
                fidelity_fails.append(spec.op)
    finally:
        _drop_base(con, table)

    print()
    fails = struct_fails + [f"{o}(保真)" for o in fidelity_fails]
    if fails:
        print(f"❌ 自证失败 {len(fails)} 项：{fails}")
        print("   ⇒ 参数化口径与生产不等价，**中止**（不产出任何结论）。")
    else:
        print(f"✅ 自证通过：结构 {len(OPS)}/{len(OPS)} 逐字相同；"
              f"保真实跑 {len(FIDELITY_PROBE)}/{len(FIDELITY_PROBE)} 等价。")
    return fails


# ---------------------------------------------------------------- 扫描
def _scan(con, market_daily: dict[int, int], only: set[str] | None,
          on_result=None) -> dict:
    """全量窗口扫描，返回 `{op: {...}}`。

    **循环顺序是「窗口优先」**（外层窗口、内层算子），每个窗口的物化表**用后立即 DROP**：
    同一窗口的全部算子共用一次 base 链物化，而任意时刻只存活 1 张表。
    早期版本「算子优先 + 表常驻缓存」会让 10 张宽表并存并撑爆临时盘（见 `_build_base`）。

    `on_result(out)` 在每个 (算子, 窗口) 完成后回调，供调用方**增量落盘**——
    长任务崩溃时不再丢掉全部已算结果（2026-09-14 实测：一次 OOM 让 27 次评估白跑）。
    """
    out: dict[str, dict] = {}
    specs = [s for s in OPS if only is None or s.op in only]
    for spec in specs:
        out[spec.op] = {"prod": spec.prod, "prod_window": spec.prod_window,
                        "axis": spec.axis, "windows": {}}
    w_axis = [s for s in specs if s.axis == "w"]
    aw_axis = [s for s in specs if s.axis == "aw"]
    total = len(specs) * len(WINDOWS)
    done = 0

    def _run(group: list[OpSpec], table: str, w: int) -> None:
        nonlocal done
        for spec in group:
            done += 1
            print(f"  [{done:>3}/{total}] {spec.op:8} w={w:<3}", flush=True)
            rec = _ic_of(con, spec, w, table, market_daily)
            v, best_h = _judge(rec)
            out[spec.op]["windows"][str(w)] = {
                "upper_verdict": v,
                "best_horizon": best_h,
                "coverage": rec["coverage"],
                "horizons": rec["windows"],
                "n_horizons_effective": sum(
                    1 for vv in rec["windows"].values()
                    if abs(vv["ic_mean"]) >= IC_ABS_MIN and abs(vv["icir"]) >= ICIR_ABS_MIN
                ),
            }
            if on_result is not None:
                on_result(out)

    for w in WINDOWS:
        if w_axis:
            table = _build_base(con, w, ATR_PROD_WINDOW)
            try:
                _run(w_axis, table, w)
            finally:
                _drop_base(con, table)
        if aw_axis:
            table = _build_base(con, 20, w)
            try:
                _run(aw_axis, table, w)
            finally:
                _drop_base(con, table)
    return out


def _sensitivity(d: dict) -> dict:
    """把一个算子的跨窗结果压成敏感性指标。"""
    ws = d["windows"]
    icirs = []
    for w in WINDOWS:
        p = ws[str(w)]
        bh = p["best_horizon"]
        if bh == "-":
            icirs.append(None)
            continue
        rep = p["horizons"].get(bh, {})
        icirs.append(rep.get("icir"))
    vals = [abs(v) for v in icirs if v is not None]
    n_ok = sum(1 for w in WINDOWS if ws[str(w)]["upper_verdict"] != "FAIL")
    prod = ws.get(str(d["prod_window"]))
    return {
        "n_windows_pass": n_ok,
        "n_windows": len(WINDOWS),
        "abs_icir_min": min(vals) if vals else None,
        "abs_icir_max": max(vals) if vals else None,
        "prod_window_verdict": prod["upper_verdict"] if prod else None,
    }


def _report(res: dict) -> None:
    """打印敏感性矩阵 + 稳健性汇总。

    「全窗稳健」=（本脚本口径）该算子在**全部扫描窗**下都给出 `至少CONDITIONAL`。
    """
    print()
    print("=" * 96)
    print("② 窗口敏感性矩阵（主窗 IC / ICIR；✓ = 至少CONDITIONAL，✗ = FAIL）")
    print("=" * 96)
    hdr = f"{'算子':9}{'生产窗':>6}{'生产':>6}" + "".join(f"{'w' + str(w):>17}" for w in WINDOWS)
    print(hdr)
    print("-" * len(hdr))
    robust, sensitive = [], []
    for op, d in res.items():
        ws = d["windows"]
        prod = ws.get(str(d["prod_window"]))
        prod_mark = "-" if prod is None else ("✓" if prod["upper_verdict"] != "FAIL" else "✗")
        row = f"{op:9}{d['prod_window']:>6}{prod_mark:>6}"
        all_ok = True
        for w in WINDOWS:
            p = ws[str(w)]
            bh = p["best_horizon"]
            rep = p["horizons"].get(bh, {}) if bh != "-" else {}
            ic, icir = rep.get("ic_mean"), rep.get("icir")
            mark = "✓" if p["upper_verdict"] != "FAIL" else "✗"
            if p["upper_verdict"] == "FAIL":
                all_ok = False
            cell = "n/a" if ic is None else f"{ic:+.4f}/{icir:+.3f}"
            row += f"{cell + ' ' + mark:>17}"
        print(row)
        (robust if all_ok else sensitive).append(op)

    print()
    print("=" * 96)
    print("③ 汇总")
    print("=" * 96)
    print(f"  ✅ 全窗稳健（{len(robust)}）：{', '.join(robust) if robust else '（无）'}")
    print(f"  ⚠️ 存在失败窗（{len(sensitive)}）：{', '.join(sensitive) if sensitive else '（无）'}")
    print()
    print(f"  {'算子':9}{'窗通过':>9}{'|ICIR|最小':>12}{'|ICIR|最大':>12}   生产窗判定")
    print("  " + "-" * 62)
    for op, d in sorted(res.items(), key=lambda kv: kv[1]["prod_window"]):
        s = _sensitivity(d)
        lo = "-" if s["abs_icir_min"] is None else f"{s['abs_icir_min']:.3f}"
        hi = "-" if s["abs_icir_max"] is None else f"{s['abs_icir_max']:.3f}"
        print(f"  {op:9}{str(s['n_windows_pass']) + '/' + str(s['n_windows']):>9}"
              f"{lo:>12}{hi:>12}   {s['prod_window_verdict'] or '(不在网格)'}")
    print()
    print("  ⚠️ 边界声明：判定只含「有效性 + 稳定性 + 覆盖率」三层，**不含五分位分层**")
    print("     ⇒ 「至少CONDITIONAL」是上界结论，完整 verdict 以 run_full_eval 为准。")
    print("  ⚠️ 「全窗稳健」= 5 个扫描窗全部至少 CONDITIONAL；扫描网格不含 120，")
    print("     长窗端衰减（如 mom120 FAIL）本网格看不到。")
    print("  ⚠️ 跨窗样本池不同（`min_bars = w+1` 保证满窗 ⇒ 大窗剔除历史不足的样本）")
    print("     ⇒ 只可比各窗的判定与 ICIR 量级，**不可把跨窗 IC 差直接归因于窗口本身**。")


def _dump(path: Path, res: dict) -> None:
    """增量落盘（`_scan` 每个 (算子, 窗口) 完成后调用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"windows": list(WINDOWS), "result": res},
                               ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只跑自证，不做扫描")
    ap.add_argument("--out", default=None, help="把结果 JSON 落盘到该路径（扫描中增量写入）")
    ap.add_argument("--only", default=None, help="逗号分隔的算子名子集（调试用）")
    args = ap.parse_args()

    only = set(args.only.split(",")) if args.only else None
    out_path = Path(args.out) if args.out else None

    con = duckdb.connect(str(MARKETDB), read_only=True)
    try:
        # 临时盘上限是**护栏**而非调优：物化表曾把临时盘撑到 34.9 GiB 触发 OOM，
        # 有了上限会**快速失败并报清原因**，而不是先把磁盘填满。
        # `preserve_insertion_order=false` 降低排序阶段的内存峰值（对数值影响在 1e-16 量级，
        # 低于自证判据 `IC_TOL`；且自证两臂跑在同一会话设置下，故已被自证覆盖）。
        con.execute("SET temp_directory='/tmp/duckdb_spill'")
        con.execute("SET max_temp_directory_size='20GB'")
        con.execute("SET preserve_insertion_order=false")
        market_daily = dict(
            con.execute("SELECT date_ms, count(*) FROM daily_k_adj GROUP BY date_ms").fetchall()
        )
        rng = con.execute("SELECT min(date_ms), max(date_ms) FROM daily_k_adj").fetchone()
        d0 = datetime.fromtimestamp(rng[0] / 1000).strftime("%Y-%m-%d")
        d1 = datetime.fromtimestamp(rng[1] / 1000).strftime("%Y-%m-%d")
        print(f"库：{MARKETDB}")
        print(f"区间：{d0} ~ {d1}（{len(market_daily)} 个交易日）")
        print(f"算子 {len(OPS)} 个 × 窗口 {WINDOWS} = {len(OPS) * len(WINDOWS)} 次评估\n")

        fails = _selfcheck(con, market_daily)
        if fails:
            return 2
        if args.probe:
            print("\n（--probe：跳过扫描）")
            return 0

        print("\n" + "=" * 96)
        print("② 扫描中 …（窗口优先；每窗物化一次、用后即弃）")
        print("=" * 96)
        res = _scan(con, market_daily, only,
                    on_result=(lambda r: _dump(out_path, r)) if out_path else None)
    finally:
        con.close()

    _report(res)

    if out_path:
        _dump(out_path, res)
        print(f"\n结果已落盘 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
