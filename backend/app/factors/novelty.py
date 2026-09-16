"""结构新颖性筛查（账本 §6.0 `RSH-003` 切片，2026-09-16）——候选因子**入池前的证据层**。

## 为什么需要它（不是"再补一个指标"）

`RSH-003` 原口径是「按 TA-Lib 目录逐个转正」。2026-09-14 的实测教训推翻了这条路径：
`mfi20` / `obv20` 入库（`FACTORS` 38→40）并双双 `PASS`，**但与既有 `sump20` 的 IC 相关
高达 0.96 / 0.95（彼此 0.97）** ⇒ 族内冗余、不重复计权。问题不在于"这两个指标不好"，
而在于**「看着像新指标」与「提供新信息」是两件事**，而冗余是**事后**才发现的，
代价是一次全量评估 + 一次口径讨论。

故本模块把"结构新颖性"落成**三个可复算的判据**，用于候选**入池前**筛查。
三个判据不是重复劳动——各自能看到另外两个看不到的东西：

| 判据 | 回答的问题 | 为什么另两个看不到 |
|---|---|---|
| `rank_corr` 逐日截面秩相关 | 排序是否同源 | 对量纲/单调变换不敏感 ⇒ 「换个单位」不会被它发现（也可能被它**过度**判重：完全单调的两个因子在"头部选票"上可能毫无关系） |
| `topk_overlap` 前 K 分位集合重合率 | **选出来的票**是不是同一批 | IC 相关 0.6 的两个因子在**头部**可能完全重合；而实盘只吃头部 ⇒ 该判据贴实际用途 |
| `conditional_ic` 近邻三分位组内 IC | 近邻已给出信息后，候选还能不能分辨好坏 | 前两个都是**对称**度量（A vs B 与 B vs A 相同），说不出"谁是增量"；条件 IC 是**非对称**的 |

## ⚠️ 本模块只产出证据，不做准入判定（红线，勿"顺手"加自动化）

`verdict` 能自动给的三个档里，只有 `duplicate` 是**数学结论**：

- `duplicate` —— 两因子的截面排序**完全相同**（|秩相关| ≥ `DUP_RANK_CORR`，即单调仿射）。
  这类候选**不可能**提供新信息，与阈值选取无关，属可证重复。
- `redundant_hint` —— 秩相关或头部重合 ≥ `IC_CORR_DEDUP`。**沿用既有常量**（不新造阈值），
  语义与 `evaluate.redundant_with` 一字一致：「去重提示，人工取舍」。
- `distinct` —— 两个判据都在线下。⚠️ **它不是"应当准入"**，只表示"没有明显的冗余证据"；
  准入仍需 IC / ICIR / 分层 / 覆盖率那一整套（`evaluate`）。
- `insufficient_sample` —— 有效截面日不足。**样本不足不得定论**（与 `MIN_LABELS_FOR_VERDICT` 同族纪律），
  此时三个数字照常给出但**不判档**。

**`conditional_ic` 刻意不参与 `verdict`**：「多小的增量才算无贡献」是**口径问题**、须人工拍板
（用户纪律：改进先提后做）。本模块只把无条件/条件 IC 并排摆出来，不做"无贡献"的自动断言。
同理，本模块**不写库、不改 `FACTORS`、不碰任何计权**。

## 口径（与 `evaluate` 同源，差异逐条写明）

- **样本面**：与 `evaluate._base_sql` 一致的守卫——`cnt >= min_bars`、`nb1_high/nb1_low` 存在且
  `nb1_high > nb1_low`（T+1 一字板买不进 ⇒ 剔除）。
- **秩口径**：用 SQL `percent_rank()`，**并列取组内首名次**（标准 SQL 语义），
  **不是** pandas `rank(method="average")`（`rank20` 因子用的是平均名次，那是因子自身口径，
  与本模块的统计口径是两件事）。对**仿射重复**的识别不受影响（并列结构被完全保留 ⇒ |ρ| 仍为 1）。
- **逐日 `corr` 的成对过滤**：不同因子的 NULL 分布不同，故每个配对各带
  `FILTER (WHERE ... IS NOT NULL)`。`percent_rank` 的分母是**当日全截面**而过滤后分子样本更少——
  这**不影响正确性**：`percent_rank = (rank−1)/(n−1)` 在同一天内对 `n` 是**仿射**的，
  而 Pearson 对仿射不变 ⇒ 结果等于「在成对非空子集上的 Spearman」。
- **前瞻收益**：`fwd_h = f{h}/f1 − 1`（T+1 收盘进、T+h 收盘出），与 `evaluate.HORIZONS_EXEC` 同式。
- **IC 计日**：单日成熟样本 < `MIN_CROSS_SECTION` 的日子不计（与 `evaluate` 同门槛）。
- **分位分组 tie-break**：`ntile(3) ... ORDER BY 近邻值, thscode`。
  ⚠️ `BUG-002` 的血训：分位切点落在并列块中间时，归属随并行执行顺序变化
  ⇒ 同一输入连跑连出不同结论。`(date_ms, thscode)` 是主键 ⇒ 补 tie-break 后是全序，结果确定。
- **最近邻的样本门槛**：只在**有效截面日 ≥ `MIN_DAYS_FOR_VERDICT`** 的配对里取最近邻。
  理由（2026-09-16 合成仓实测）：`rand_x` 与 `mom5` 有 49 个有效日，但**原始**最近邻
  `vma20` 只有 2 日 ⇒ 候选被判 `insufficient_sample` —— 「样本足够」被「样本不足的邻居」挡掉。
  被排除的配对不会静默消失，一律进 `thin_pairs`（含 `n_days` 与 |ρ|），供人工判断。
  若**没有任何**配对达到门槛 ⇒ `nearest=None` ⇒ `insufficient_sample`（不得用噪声定档）。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Sequence

from app.factors.evaluate import (
    HORIZONS_EXEC,
    IC_CORR_DEDUP,
    MIN_CROSS_SECTION,
    _base_cte,
)
from app.factors.library import FACTORS, FactorDef

log = logging.getLogger(__name__)

#: 可证重复线：|秩相关| ≥ 此值 ⇒ 两因子截面排序完全相同（单调仿射）。
#: 取 0.999 而非 1.0：浮点 `corr` 在百万级样本上会有 1e-12 级误差，钉死 1.0 会误判。
DUP_RANK_CORR = 0.999

#: 头部集口径：前 20% 分位、且不少于 10 只（小截面日不至于退化成"整日重合"）。
TOPK_FRAC = 0.20
TOPK_MIN = 10

#: 条件 IC 的分组数（三分位：低 / 中 / 高三组）。
COND_GROUPS = 3

#: 有效截面日下限：不足则 `insufficient_sample`（**不判档**）。
#: 初版值，与 `MIN_LABELS_FOR_VERDICT = 30` 同族纪律（样本不足不得转正）。
MIN_DAYS_FOR_VERDICT = 30

#: 默认回看交易日数（约 1 年，与 `evaluate.ROLLING_WINDOW_DAYS` 同量级）。
DEFAULT_LOOKBACK_DAYS = 250

#: **无扩展路径的冻结指纹**（2026-09-16）：`sha256(_panel_sql(specs, 1700000000000, 21))`，
#: 其中 `specs` = 两个合成 `FactorDef`（`alpha_probe` / `beta_probe`，`min_bars=21`；
#: spec 逐字写死在 `tests/test_smoothing.py::test_extension_is_load_bearing_and_keeps_baseline_sql`），
#: 该 spec 下 SQL 长 **9127** 字符。用途 = 加「面板扩展」钩子时，证明**不传扩展的 SQL 与钩子前
#: 逐字相同** ⇒ 既有 4 候选（`willr20`/`cmo20`/`kurt20`/`skew20`）的档位不可能因为这次改动而变。
#: ⚠️ **本条曾被记成一个不可复现的值**（`b4ac4881…`）：按文档写的 spec 实测是 9127 字符 /
#: `f40134c5…`，与原值差 **13 字符** —— 而两个名字在 SQL 里各出现 **3 次**（共 6 处）
#: ⇒ 13 这个差值**不可能**由任意"两个名字"的长度产生 ⇒ 原值来源不明（大概率来自某个已不存在的
#: 模板版本）。**教训：机械凭据必须能由它自己的文档复现**，否则它提供的是虚假的确定性。
#: 已按可复现 spec 重算，并用「**HEAD 版本 vs 工作区**」的逐字对照独立复核（2026-09-16 实测：
#: 两边同为 9127 字符、`f40134c5…`、`==` 为真）。
#: ⚠️ 若确实要改无扩展路径，**必须**同时说明理由并更新本值——它是"你没动生产路径"的唯一机械凭据。
_PANEL_SQL_BASELINE_SHA256 = "f40134c5ae282ae96577a7f5e039af5bde3b472810f80280a771048eba332370"


def _cutoff_ms(con: Any, lookback_days: int) -> int:
    """回看窗口起点（毫秒）：取倒数第 `lookback_days` 个交易日。

    用**交易日**而非日历日：日历回看会把长假算进窗口，导致有效截面日数随节假日漂移。
    """
    row = con.execute(
        "SELECT min(d) FROM (SELECT DISTINCT date_ms AS d FROM daily_k"
        " ORDER BY d DESC LIMIT ?)",
        [lookback_days],
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError("daily_k 为空：无交易日可取回看窗口")
    return int(row[0])


@dataclass(frozen=True)
class PanelExtension:
    """研究用**面板扩展**：在 base 链之后追加若干 CTE，并把 `scored` 的取数源指向末级。

    ## 为什么需要它
    候选表达式一律在 `lvl4` 上求值，而 `lvl4` 只有**等权**窗口聚合的列
    （`AVG/SUM/STDDEV/corr` 那一批）⇒ **指数平滑类**指标（PPO 的 EMA12/26、ADX 的 Wilder
    递归）在这里**一行都写不出来**。扩展钩子让这类候选把"自己需要的列"加在 base 链之后，
    而不必改动 `_base_cte`（那是**生产**评估链，动它就要 bump `ALGO_VERSION`）。

    ## 硬约束（由调用方保证，`test_smoothing.py` 有对应守卫）
    - `ctes` 里每一级都必须是 **`SELECT *, <新增列> FROM <上一级>`** 形态 ⇒ **列只增不改**。
      若某级改了既有列（如 `thscode` / `f1` / `f5` / `cnt` / `nb1_*`），
      `scored` 的守卫与前瞻收益会被**静默改写**，而所有判据看上去照常工作。
    - `ctes` 不含 `WITH`（`WITH` 由 `_base_cte` 提供），每项形如 `"名 AS (SELECT ...)"`。
    - `source` 必须是末级 CTE 名；未提供扩展时 `scored` 一律 `FROM lvl4`。

    ⚠️ **无扩展路径必须逐字不变**：`extension=None` 时 `_panel_sql` 的输出与加钩子前**完全相同**
    （由 `_PANEL_SQL_BASELINE_SHA256` 冻结）——否则既有 4 个候选的结论会跟着动。
    """

    ctes: tuple[str, ...]
    source: str
    #: 口径说明（透传进报告 `meta.extension`），让读者知道本轮筛的是**哪套口径**的候选。
    note: str = ""


def _panel_sql(
    specs: Sequence[FactorDef],
    cutoff_ms: int,
    min_bars: int,
    extension: PanelExtension | None = None,
) -> str:
    """构造「候选 + 既有」同一次扫描的基座（含每个 spec 的当日截面 `percent_rank`）。

    ⚠️ **为什么所有因子共用一次 base 链**：`_base_cte` 的四层窗口链（含 20/14 日聚合）
    是这条通路里最贵的部分，而它与**具体因子无关**（因子只出现在最外层表达式中）。
    一次扫描算完 40+ 个因子，远优于逐因子各跑一遍（那是线性放大）。

    ⚠️ **本模块初版注释曾估「约 30~60s」，实测偏高 5 倍以上**
    ⇒ 性能数字一律**当轮实测回填**（勿凭记忆或直觉；本仓纪律：数字要么实测、要么只写实测方法）。
    ⚠️ **耗时实测（2026-09-16，真实 marketdb 395MB / 1027 万行；4 候选 × 40 既有
    = 160 个 `corr` 列）**：
    · 默认调用（回看 **250** 交易日 ⇒ **249** 个截面日）实测 **295s**（CLI 外部计时 297s）；
    · `--lookback 8`（⇒ **7** 个截面日）实测 **47.7s**。
    ⇒ **成本随「窗口内截面日数」近似线性**（`corr` 那一段只算 `scored` 的行），
    窗口链（`lvl1`~`lvl3` 的 `LAG/LEAD/COUNT` 与聚合）是**固定底价**（< 47.7s）。
    **缩短回看窗确实能省时间（本项实测约 6×）。**
    ⚠️ **本条曾被写反，勿再"从代码推导"回错的版本**：原注释称「成本与 `--lookback` 基本无关、
    缩小回看窗省不了时间」，理由是「`date_ms >= cutoff` 只是事后过滤、下推不到窗口里」。
    该推理**只对窗口链成立、对 `corr` 段不成立** ⇒ 实测结论**方向相反**。
    且更早那句「实测 `--lookback 8` 同样跑不完」**是假证据**——那次运行的 `--lookback 8`
    从未被解析（CLI `parse_args(argv or [])` 缺陷，[[KB-ENG-108]]），实际跑的是 250。
    ⇒ **两条纪律**：①从代码读出的性能结论**必须实测确认**；②实测前先确认"跑的输入就是我以为的那些"。
    进一步提速方向：减少 `corr` 列数（分批候选）或给 base 链加物化。
    """
    exprs = ",\n           ".join(f"({s.expr}) AS {_col(s.name)}" for s in specs)
    ranks = ",\n           ".join(
        f"percent_rank() OVER (PARTITION BY date_ms ORDER BY {_col(s.name)}) AS {_rank(s.name)}"
        for s in specs
    )
    #: ⚠️ `extra`/`src` 的默认值**必须**让无扩展路径与加钩子前逐字相同（冻结哈希见模块常量）。
    extra = "" if extension is None else ",\n" + ",\n".join(extension.ctes)
    src = "lvl4" if extension is None else extension.source
    return f"""{_base_cte(specs[0])}{extra},
scored AS (
    SELECT thscode, date_ms, f5 / NULLIF(f1, 0) - 1 AS fwd,
           {exprs}
    FROM {src}
    WHERE cnt >= {min_bars}
      AND date_ms >= {cutoff_ms}
      AND nb1_high IS NOT NULL AND nb1_low IS NOT NULL AND nb1_high > nb1_low
),
rk AS (
    SELECT *, {ranks}
    FROM scored
)"""


def _col(name: str) -> str:
    return f'"{name}"'


def _rank(name: str) -> str:
    return f'rk_{name}'


def _rank_corr_pass(
    con: Any,
    candidates: Sequence[FactorDef],
    incumbents: Sequence[FactorDef],
    cutoff_ms: int,
    extension: PanelExtension | None = None,
) -> dict[int, dict[str, float | None]]:
    """第一遍：逐日截面秩相关（候选 × 全体既有）。

    返回 `{date_ms: {"rk_<cand>|rk_<inc>": rho}}`，**只算得有出的配对**才有键。

    ⚠️ **样本面由「候选」的 `min_bars` 决定，不是全池最大值**（2026-09-16 实测抓出）：
    若取 `max(min_bars)`，池里只要有一个 `mom120`（121 根）就会把整个宇宙压到
    「上市满 121 日」——合成仓 70 日实测**全空**、真库上则静默砍掉大半个截面，
    且症状是"所有候选都 insufficient_sample"，与「样本真的不足」**同形**。
    正确口径：候选的 `min_bars` 管宇宙（它是被筛查的对象），既有因子的长窗口缺口
    由**成对 FILTER** 处理（其 expr 自身在窗口不足时返回 NULL）。
    """
    min_bars = max(c.min_bars for c in candidates)
    pairs = [(c.name, i.name) for c in candidates for i in incumbents]
    corr_cols = ",\n           ".join(
        f"corr({_rank(c)}, {_rank(i)}) FILTER "
        f"(WHERE {_rank(c)} IS NOT NULL AND {_rank(i)} IS NOT NULL) AS \"{c}|{i}\""
        for c, i in pairs
    )
    sql = f"""{_panel_sql((*candidates, *incumbents), cutoff_ms, min_bars, extension)}
SELECT date_ms, count(*) AS n, {corr_cols}
FROM rk
GROUP BY date_ms
ORDER BY date_ms"""
    cursor = con.execute(sql)
    names = [d[0] for d in cursor.description]
    out: dict[int, dict[str, float | None]] = {}
    for row in cursor.fetchall():
        rec = dict(zip(names, row))
        out[int(rec.pop("date_ms"))] = rec
    return out


def _detail_sql(
    cand: FactorDef,
    near: FactorDef,
    cutoff_ms: int,
    horizon: int,
    extension: PanelExtension | None = None,
) -> str:
    """第二遍（仅最近邻）：头部重合 + 无条件/条件 IC。

    IC 只在**成熟样本**上算（`fwd IS NOT NULL`）：`corr` 的 FILTER 已保证配对非空，
    该写法与 `evaluate` 的「按窗口分别排名」等价（见模块 docstring 的仿射论证）。

    样本面同样只由**候选**的 `min_bars` 决定（理由见 `_rank_corr_pass`）。
    """
    min_bars = cand.min_bars
    cc, nn = _col(cand.name), _col(near.name)
    extra = "" if extension is None else ",\n" + ",\n".join(extension.ctes)
    src = "lvl4" if extension is None else extension.source
    return f"""{_base_cte(cand)}{extra},
scored AS (
    SELECT thscode, date_ms, f{horizon} / NULLIF(f1, 0) - 1 AS fwd,
           ({cand.expr}) AS {cc}, ({near.expr}) AS {nn}
    FROM {src}
    WHERE cnt >= {min_bars}
      AND date_ms >= {cutoff_ms}
      AND nb1_high IS NOT NULL AND nb1_low IS NOT NULL AND nb1_high > nb1_low
),
cnt AS (
    SELECT date_ms, count(*) AS n FROM scored
    WHERE {cc} IS NOT NULL AND {nn} IS NOT NULL GROUP BY date_ms
),
rk AS (
    SELECT s.*,
           row_number() OVER (PARTITION BY date_ms ORDER BY {cc} DESC NULLS LAST, thscode) AS rc,
           row_number() OVER (PARTITION BY date_ms ORDER BY {nn} DESC NULLS LAST, thscode) AS ri,
           percent_rank() OVER (PARTITION BY date_ms ORDER BY {cc}) AS pc,
           percent_rank() OVER (PARTITION BY date_ms ORDER BY {nn}) AS pn,
           percent_rank() OVER (PARTITION BY date_ms ORDER BY fwd) AS pf,
           ntile({COND_GROUPS}) OVER (PARTITION BY date_ms ORDER BY {nn}, thscode) AS g
    FROM scored s
    WHERE {cc} IS NOT NULL AND {nn} IS NOT NULL
),
rk2 AS (
    SELECT *, percent_rank() OVER (PARTITION BY date_ms, g ORDER BY {cc}) AS pgc,
              percent_rank() OVER (PARTITION BY date_ms, g ORDER BY fwd) AS pgf
    FROM rk
)
SELECT cnt.date_ms, cnt.n,
       greatest({TOPK_MIN}, cast(ceil({TOPK_FRAC} * cnt.n) AS INTEGER)) AS k,
       sum(CASE WHEN rc <= greatest({TOPK_MIN}, cast(ceil({TOPK_FRAC} * cnt.n) AS INTEGER))
                 AND ri <= greatest({TOPK_MIN}, cast(ceil({TOPK_FRAC} * cnt.n) AS INTEGER))
                THEN 1 ELSE 0 END) AS hit,
       corr(pc, pf) FILTER (WHERE pf IS NOT NULL) AS ic,
       count(pf) AS n_mature,
       corr(pgc, pgf) FILTER (WHERE pgf IS NOT NULL AND g = 1) AS ic_g1,
       corr(pgc, pgf) FILTER (WHERE pgf IS NOT NULL AND g = 2) AS ic_g2,
       corr(pgc, pgf) FILTER (WHERE pgf IS NOT NULL AND g = {COND_GROUPS}) AS ic_g3
FROM rk2 JOIN cnt USING (date_ms)
GROUP BY cnt.date_ms, cnt.n
ORDER BY cnt.date_ms"""


def _mean(xs: Sequence[float]) -> float | None:
    vals = [x for x in xs if x is not None and not math.isnan(x)]
    return sum(vals) / len(vals) if vals else None


def _median(xs: Sequence[float]) -> float | None:
    vals = sorted(x for x in xs if x is not None and not math.isnan(x))
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _max_abs(xs: Sequence[float | None]) -> float | None:
    vals = [abs(x) for x in xs if x is not None and not math.isnan(x)]
    return max(vals) if vals else None


def _verdict(best_abs_corr: float | None, best_overlap: float | None, n_days: int) -> str:
    """判档（顺序不可换：先看可证重复，再看提示线，最后看样本是否够定论）。"""
    if n_days < MIN_DAYS_FOR_VERDICT:
        return "insufficient_sample"
    if best_abs_corr is not None and best_abs_corr >= DUP_RANK_CORR:
        return "duplicate"
    if (best_abs_corr is not None and best_abs_corr >= IC_CORR_DEDUP) or (
        best_overlap is not None and best_overlap >= IC_CORR_DEDUP
    ):
        return "redundant_hint"
    return "distinct"


def screen_candidates(
    con: Any,
    candidates: Sequence[FactorDef],
    *,
    incumbents: Sequence[FactorDef] | None = None,
    horizon: int = 5,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_cross_section: int = MIN_CROSS_SECTION,
    extension: PanelExtension | None = None,
) -> dict:
    """对候选因子做结构新颖性筛查（只读；不写库、不改 `FACTORS`）。

    `incumbents` 默认 = 当前因子池（`FACTORS`）。候选**不应**已在池内（否则是自我比较）。
    返回结构见模块 docstring；`verdict` 三态 + `insufficient_sample`。
    """
    if horizon not in HORIZONS_EXEC:
        raise ValueError(f"horizon 必须是 {HORIZONS_EXEC} 之一（与 evaluate 同口径），收到 {horizon}")
    pool = tuple(incumbents if incumbents is not None else FACTORS)
    cand_names = [c.name for c in candidates]
    dup = sorted(set(cand_names) & {i.name for i in pool})
    if dup:
        raise ValueError(f"候选已在既有池内（请换名或移出池）：{dup}")
    if not candidates or not pool:
        raise ValueError("候选与既有池都不得为空")
    if len({c.name for c in candidates}) != len(candidates):
        raise ValueError("候选名重复：秩相关列以名称为键，重名会静默覆盖")

    cutoff_ms = _cutoff_ms(con, lookback_days)
    corr_by_day = _rank_corr_pass(con, candidates, pool, cutoff_ms, extension)

    days = sorted(corr_by_day)
    #: 逐日「有效截面」= 该日至少有一个配对算得出秩相关（用于样本量透明度）
    out_cands: list[dict] = []
    for cand in candidates:
        pairs: list[dict] = []
        for inc in pool:
            key = f"{cand.name}|{inc.name}"
            rhos = [corr_by_day[d].get(key) for d in days]
            rhos = [r for r in rhos if r is not None and not math.isnan(r)]
            if not rhos:
                continue
            pairs.append({
                "incumbent": inc.name,
                "category": inc.category,
                "rank_corr_mean": _mean(rhos),
                "rank_corr_median": _median(rhos),
                "n_days": len(rhos),
                #: 是否够格当「最近邻」——只有寥寥几个有效截面日的配对，其相关系数是噪声，
                #  按 |ρ| 排名等于**让噪声决定结论**。
                #: ⚠️ 2026-09-16 实测（合成仓）：`rand_x` 与 `mom5` 有 **49** 个有效日，
                #:  但原始最近邻 `vma20` 只有 **2** 日 ⇒ 候选被判 `insufficient_sample`，
                #:  即「样本足够」被「样本不足的邻居」挡掉，档位与事实相反。
                "verdict_eligible": len(rhos) >= MIN_DAYS_FOR_VERDICT,
            })
        pairs.sort(key=lambda p: -abs(p["rank_corr_mean"]))
        eligible = [p for p in pairs if p["verdict_eligible"]]
        nearest = eligible[0] if eligible else None
        #: 排序在最近邻之前、却因样本不足**未被采信**的配对——**必须显式列出**：
        #: 否则读者看到「最近邻是 A」却算不出「为什么不是更像的 B」，会以为判据出错。
        thin_pairs = [
            p for p in pairs
            if not p["verdict_eligible"]
            and (nearest is None or abs(p["rank_corr_mean"]) > abs(nearest["rank_corr_mean"]))
        ]
        #: 未能比较的既有因子（无任何有效配对日）——**必须显式列出**：
        #  静默丢弃会让读者以为「40 项都比过了」，而真相是长窗口因子在短样本面上无从比较。
        compared = {p["incumbent"] for p in pairs}
        uncompared = [i.name for i in pool if i.name not in compared]
        record: dict[str, Any] = {
            "name": cand.name,
            "category": cand.category,
            "min_bars": cand.min_bars,
            "expr": cand.expr,
            "n_days": nearest["n_days"] if nearest else 0,
            "nearest": None,
            "topk_overlap_mean": None,
            "topk_overlap_median": None,
            "ic_unconditional": None,
            "ic_conditional": [],
            "pairs": pairs,
            "thin_pairs": thin_pairs,
            "uncompared": uncompared,
        }
        if nearest is not None:
            near_def = next(i for i in pool if i.name == nearest["incumbent"])
            detail = _detail_rows(
                con, cand, near_def, cutoff_ms, horizon, min_cross_section, extension
            )
            # g1/g2/g3 = 近邻值的**低/中/高**三分位（`ntile` 升序），组内重算候选的 IC。
            cond_groups = [
                {"group": f"g{g}", "ic": _mean(detail["ic_g"][f"g{g}"])}
                for g in range(1, COND_GROUPS + 1)
            ]
            record.update({
                "nearest": nearest,
                "topk_overlap_mean": _mean(detail["overlap"]),
                "topk_overlap_median": _median(detail["overlap"]),
                "ic_unconditional": _mean(detail["ic"]),
                "ic_conditional": cond_groups,
                # ⚠️ 取的是**组间均值的绝对值最大值**（不是逐日列表）——
                #    2026-09-16 实测：误把列表传进来会 `TypeError: must be real number, not list`，
                #    而该错误被 e2e 用例兜住（纯函数用例抓不到，属接线类缺陷）。
                "ic_conditional_max_abs": _max_abs([g["ic"] for g in cond_groups]),
                "n_days_ic": detail["n_days_ic"],
            })
        record["verdict"] = _verdict(
            abs(nearest["rank_corr_mean"]) if nearest else None,
            record["topk_overlap_mean"],
            record["n_days"],
        )
        out_cands.append(record)

    return {
        "meta": {
            "horizon": horizon,
            "lookback_days": lookback_days,
            "cutoff_ms": cutoff_ms,
            "n_incumbents": len(pool),
            "n_candidate_days": len(days),
            "min_cross_section": min_cross_section,
            "duplicate_rank_corr": DUP_RANK_CORR,
            "redundant_hint_rank_corr": IC_CORR_DEDUP,
            "topk": {"frac": TOPK_FRAC, "min": TOPK_MIN},
            "min_days_for_verdict": MIN_DAYS_FOR_VERDICT,
            #: 面板扩展的**口径留痕**（无扩展时为 None）：候选是在"哪一套附加口径"下筛的，
            #: 必须写进报告——否则同一个候选名在不同口径下的结论会长得一模一样。
            "extension": (
                None if extension is None
                else {
                    "source": extension.source,
                    "n_ctes": len(extension.ctes),
                    "note": extension.note,
                }
            ),
            "note": (
                "verdict 只有 duplicate 是数学结论（单调仿射 ⇒ 排序信息完全相同）；"
                "redundant_hint 沿用 IC_CORR_DEDUP=0.70，语义为「去重提示，人工取舍」；"
                "distinct **不等于应当准入**（准入仍需 IC/ICIR/分层/覆盖率）；"
                "条件 IC 只作证据、不参与判档（阈值属口径问题，须人工拍板）；"
                f"最近邻只在有效截面日 ≥ {MIN_DAYS_FOR_VERDICT} 的配对中取"
                "（样本不足的配对数不出可信的 |ρ|，按它排名等于让噪声决定档位），"
                "被排除的配对见 `thin_pairs`。"
            ),
        },
        "candidates": out_cands,
    }


def _detail_rows(
    con: Any,
    cand: FactorDef,
    near: FactorDef,
    cutoff_ms: int,
    horizon: int,
    min_cross_section: int,
    extension: PanelExtension | None = None,
) -> dict:
    """跑第二遍并把逐日行收成按指标分组的列（IC 日按 `min_cross_section` 过滤）。"""
    sql = _detail_sql(cand, near, cutoff_ms, horizon, extension)
    rows = con.execute(sql).fetchall()
    overlap: list[float] = []
    ic: list[float | None] = []
    ic_g: dict[str, list[float | None]] = {f"g{g}": [] for g in range(1, COND_GROUPS + 1)}
    n_ic_days = 0
    for _day, n, k, hit, ic_v, n_mature, *gs in rows:
        if n:
            overlap.append(hit / k)
        if n_mature is not None and n_mature >= min_cross_section:
            n_ic_days += 1
            ic.append(ic_v)
            for idx, g in enumerate(range(1, COND_GROUPS + 1)):
                ic_g[f"g{g}"].append(gs[idx])
    return {"overlap": overlap, "ic": ic, "ic_g": ic_g, "n_days_ic": n_ic_days}
