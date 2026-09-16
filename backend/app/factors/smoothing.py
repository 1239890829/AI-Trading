"""指数平滑（EMA / Wilder 递归）的**窗口 SQL 原语**（`RSH-003` 切片 2，研究用，只读）。

## 为什么必须新造原语

`evaluate._base_cte` 的 `lvl3` 只有**等权**窗口聚合（`AVG/SUM/STDDEV/corr/regr_*`）。
靠它们能写出 CMO / RSV / RANK 那类"窗口内差值比"，但**指数平滑类一行都写不出来**：
PPO 要 `EMA12 / EMA26`、ADX 要 `Wilder` 平滑 `TR / +DM / −DM` 再平滑 `DX`
——都是"无限记忆"的几何加权，不是任何固定窗的等权聚合。

## ⚠️ 陷阱：权重是 **(当前行, 帧内行)** 的成对量，用单行量表达会**静默退化成等权 SMA**

EMA 递归 `EMA_i = c·EMA_{i-1} + a·x_i`（`c = 1 − a`）的闭式是

    EMA_i = Σ_j x_j·c^(i−j) / Σ_j c^(i−j)

权重 `c^(i−j)` 里的 **`i` 是当前行**。一个很自然但**错误**的写法，是让指数取自"该行自身的属性"，
例如「该行距**帧首行**的距离」`off_j = rn_j − min(rn) OVER frame`：

- 对**满帧**，`min(rn)` 恰是帧首行 ⇒ `off_j ≡ N−1`（**常数**）⇒ 权重全部相等
  ⇒ 窗口里的"加权平均"**退化成等权 SMA**；
- 症状极其隐蔽：数值**完全合理**（就是一个移动平均）、不报错、不告警。
  **2026-09-16 实测**（真实库单只 2435 根 K 线、`a=2/13`）：该错误形态与独立朴素递归参照的
  最大偏差 **8.15**（价格量级 ~10，相对误差 ~50%），而它与简单移动平均**逐位相同**
  （实测该写法的分母 = `400 × (1−a)^399` ⇒ 整帧都用了"当前行"那一个权重）；
- ⇒ **只有拿独立参照（朴素递归）逐点对照才会发现**。任何"看数值是否合理"的检查都抓不到它。

**正确写法**：把 `c^i` 从分子分母**约掉**，使两式都只含**行自身的量**，
于是可预计算成列、窗口内只做求和：

    EMA_i = Σ_frame z / Σ_frame v,   z_j = x_j·(1/c)^(rrel_j),   v_j = (1/c)^(rrel_j)

（`c^i` 是**同一分区内所有行共有**的因子 ⇒ 必然约掉；与"当前行"无关。）

**精确性实测（2026-09-16，真实库 2435 根 K 线，独立朴素递归参照）**：跳过 warmup 段后，
`a = 2/13` 最大偏差 **7.1e-15**、`a = 1/14`（Wilder）**1.2e-12** ⇒ 机器精度级。

## 口径（逐条写明；**不声称"照抄 TA-Lib"**）

- **截断窗** `SMOOTHING_WINDOW_BARS = 400` 根：无限记忆被截到 400 根，截断误差 ~`(1−a)^N`。
  本模块实际会用到的最慢衰减：`a = 2/27`（PPO 慢线）→ `(25/27)^400 ≈ 4e-14`；
  `a = 1/14`（ADX）→ `(13/14)^400 ≈ 1e-13`；`a = 2/13` → `(11/13)^400 ≈ 1e-29` ⇒ 三者均可忽略。
- **`(1/c)^rrel` 的幅值有界且必须断言**：`rrel ∈ [1, 分区行数]` ⇒ 最坏对数幅值
  = `max_bars · ln(1/c)`。本库实测 `max_bars = 2435` ⇒ `a=2/13` 为 **406.8**、
  `a=1/14` 为 **180.5**，均在 double 内（上限约 709）。⚠️ 超限的表现是**静默变 `inf`**
  ⇒ 一律先过 `assert_exponent_safe()`（本库真实值随数据增长而变，**必须当轮实测**）。
  ⚠️ **为什么基准取"分区最早一行"而不是"分区最新一行"**：取最新一行会让值域变成 `(0, 1]`，
  早期行（离最新行几千根）会**下溢到 0** ⇒ 整段平滑值静默变 `NULL`（"看起来是 warmup"）。
  取最早一行则值恒 `≥ 1`，唯一风险是溢出，而溢出可断言 ⇒ **取最早一行**。
- **warmup 置 NULL**：`rrel < N`（帧未满）**不输出值**。理由：截断窗内的"归一化加权均值"
  **不是** EMA（它没有帧外的记忆），输出它等于给出"从上市起算的另一种平滑"而不自知
  （与 base 链「窗口有效样本 <20 → NULL，不凑 0」同族：**三态纪律**）。
- **缺失日不参与，且分子分母样本面严格一致**：`x` 为 NULL 的行，其 `v` **同样置 NULL**
  （否则该行只进分母不进分子 ⇒ 结果被系统性压低，base 链 OBV 的注释记过这条）。
  窗口内有缺失日 ⇒ 等价于在有效样本上**重新归一**权重。
  ⚠️ 另要求**当前行的 `x` 非 NULL** 才输出：否则等于在"缺最新一根"的前提下外推。
- **不写库、不改 `FACTORS`、不 bump `ALGO_VERSION`**：本模块只造 SQL 片段，供研究脚本经
  `novelty.PanelExtension` 注入；**生产评估链 `_base_cte` 一字未改**（由 `_panel_sql`
  的冻结哈希与 `evaluate` 的既有守卫共同保证）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

#: 截断窗（根 K 线）：无限记忆被截到此长度，误差 ~`(1−a)^N`（见模块 docstring 的逐项估算）。
SMOOTHING_WINDOW_BARS = 400

#: `(1/c)^rrel` 的最坏对数幅值上限。double 的溢出阈值 `ln(DBL_MAX) ≈ 709.78`，
#: 取 600 留余量 —— 超限时**报错**，不静默产出 `inf`。
MAX_SAFE_LOG_EXPONENT = 600.0

#: 支持的平滑类型。**不设默认兜底**：写错必须报错，不能静默按某一种算。
_KINDS = ("ema", "wilder")


@dataclass(frozen=True)
class SmoothSpec:
    """一条待平滑的序列。

    `alias` = 输出列名（同时派生 `z_<alias>` / `v_<alias>` 两个内部列）；
    `src` = 输入表达式（列名或算式，在 `source` 那一级可用的任意列）；
    `period` = 周期 `n`；`kind` = `"ema"`（`a = 2/(n+1)`）或 `"wilder"`（`a = 1/n`）。
    """

    alias: str
    src: str
    period: int
    kind: str = "ema"


def alpha_for(kind: str, period: int) -> float:
    """平滑系数 `a`。`ema` 取 `2/(n+1)`（TA-Lib/ChartSchools 惯例）、`wilder` 取 `1/n`。"""
    if kind not in _KINDS:
        raise ValueError(f"未知平滑类型 {kind!r}；可选 {_KINDS}")
    if period < 1:
        raise ValueError(f"period 必须 ≥ 1，收到 {period}")
    return 2.0 / (period + 1.0) if kind == "ema" else 1.0 / period


def log_exponent_headroom(alpha: float, max_bars: int) -> float:
    """最坏对数幅值 = `max_bars · ln(1/(1−α))`；超过 `MAX_SAFE_LOG_EXPONENT` 即可能溢出。"""
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha 必须落在 (0, 1)，收到 {alpha}")
    return max_bars * math.log(1.0 / (1.0 - alpha))


def assert_exponent_safe(
    alpha: float, max_bars: int, *, alias: str = "", limit: float = MAX_SAFE_LOG_EXPONENT
) -> float:
    """溢出护栏（**fail loud**）：超限即抛错，附上可执行的修法。

    ⚠️ 必须**用当轮实测的分区最大行数**调用（本库实测 2435 根 ⇒ `a=2/13` 为 406.8）。
    凭记忆填 `max_bars` 等于没护栏 —— 数据一长就把 `inf` 静默写进结论。
    """
    head = log_exponent_headroom(alpha, max_bars)
    if head > limit:
        raise ValueError(
            f"指数平滑会溢出：{alias or 'α=%g' % alpha} 在 max_bars={max_bars} 下最坏对数幅值 "
            f"{head:.1f} > {limit:.0f}（double 溢出约 709.8）⇒ 结果会是 inf 而非报错。"
            f"修法：① 调小 window_bars；② 用更长的 period（α 更小）；③ 给基准加分段。"
        )
    return head


def smooth_ctes(
    tag: str,
    specs: tuple[SmoothSpec, ...] | list[SmoothSpec],
    *,
    source: str = "lvl4",
    window_bars: int = SMOOTHING_WINDOW_BARS,
) -> tuple[str, ...]:
    """生成指数平滑的 CTE 链（3 级：基准序号 → 权重 → 平滑值），供 `PanelExtension.ctes` 使用。

    产出（`tag="sm"`、`specs=[SmoothSpec("ema12","close_adj",12)]`）：

    - `sm_prep`：`SELECT *, rn − min(rn) OVER (PARTITION BY thscode) + 1 AS sm_rrel FROM <source>`
    - `sm_w`：逐行 `z_<alias>` / `v_<alias>`（`v` 与 `src` **同源置空**，见模块 docstring）
    - `sm_s`：`CASE WHEN sm_rrel >= N AND z_<alias> IS NOT NULL THEN Σz/Σv END AS <alias>`

    返回的元组按依赖顺序排列（后者可引用前者）；末级名 = `f"{tag}_s"`。
    多级链式使用时（如 ADX 要先平滑 `TR/DM` 再平滑 `DX`）**给每次调用不同的 `tag`**，
    并把前一次的末级名作为后一次的 `source`。
    """
    specs = tuple(specs)
    if not specs:
        raise ValueError("specs 不得为空")
    names = [s.alias for s in specs]
    if len(set(names)) != len(names):
        raise ValueError(f"alias 重复：{names}（列名会静默互相覆盖）")
    if tag in names or any(n.startswith(f"{tag}_") for n in names):
        raise ValueError(f"tag {tag!r} 与 alias 冲突（{names}）")
    if window_bars < 2:
        raise ValueError(f"window_bars 必须 ≥ 2，收到 {window_bars}")

    frame = (
        f"PARTITION BY thscode ORDER BY date_ms "
        f"ROWS BETWEEN {window_bars - 1} PRECEDING AND CURRENT ROW"
    )
    z_cols, v_cols, out_cols = [], [], []
    for s in specs:
        a = alpha_for(s.kind, s.period)
        base = 1.0 / (1.0 - a)
        z_cols.append(
            f"CASE WHEN ({s.src}) IS NOT NULL THEN ({s.src}) * pow({base!r}, {tag}_rrel) END"
            f" AS z_{s.alias}"
        )
        v_cols.append(
            f"CASE WHEN ({s.src}) IS NOT NULL THEN pow({base!r}, {tag}_rrel) END AS v_{s.alias}"
        )
        out_cols.append(
            f"CASE WHEN {tag}_rrel >= {window_bars} AND z_{s.alias} IS NOT NULL "
            f"THEN sum(z_{s.alias}) OVER ({frame}) / NULLIF(sum(v_{s.alias}) OVER ({frame}), 0) "
            f"END AS {s.alias}"
        )
    return (
        f"{tag}_prep AS (\n"
        f"    SELECT *, rn - min(rn) OVER (PARTITION BY thscode) + 1 AS {tag}_rrel\n"
        f"    FROM {source}\n"
        f")",
        f"{tag}_w AS (\n"
        f"    SELECT *,\n           " + ",\n           ".join((*z_cols, *v_cols)) + "\n"
        f"    FROM {tag}_prep\n"
        f")",
        f"{tag}_s AS (\n"
        f"    SELECT *,\n           " + ",\n           ".join(out_cols) + "\n"
        f"    FROM {tag}_w\n"
        f")",
    )


def warmup_bars(window_bars: int = SMOOTHING_WINDOW_BARS) -> int:
    """平滑可用所需的最少历史行数（= `window_bars`）：不足则列值为 `NULL`。

    研究脚本应把它写进 `FactorDef.note`，并在需要时用手工 `CASE` 在**候选表达式**里
    额外置空 —— ⚠️ **不要**把它塞进 `FactorDef.min_bars`：那个字段会经
    `novelty._rank_corr_pass` 的 `max(...)` **抬高整个面板的宇宙门槛**，
    从而改变**全部候选与既有池**的比较面（不是只影响这一个候选）。
    """
    return window_bars
