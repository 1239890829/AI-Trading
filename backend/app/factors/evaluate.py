"""因子评估引擎（docs/summary/factor-system.md §4-5）。

口径（防泄露，代码级）：
- signal at T 收盘；entry = T+1 收盘（f1），exit = T+h 收盘（f3/f5/f10/f20）——
  保守执行口径，剔除隔夜跳空虚增；fwd_close_1（T 收盘进）仅作损耗对比，不参与判定；
- T+1 一字板（nb1_high = nb1_low）样本剔除——涨停一字买不进；
- 每日截面 <30 只的 IC 不计入聚合（小截面失真）；
- **收益量纲（R17，2026-09-14 修）**：`fwd = f{h}/f1 - 1` 是 T+1 收盘 → T+h 收盘的
  **期间收益**，持有 **h−1 个交易日**——**不是日收益**。年化只能按 `243/(h−1)` 线性缩放，
  且该缩放值在**重叠持有**下**不可实现** ⇒ 一律标 `*_ann_pct` + 显式 `simple_linear`
  口径标签，可实现的组合年化走净值曲线（本模块不产出，字段三态为 `None`）；
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

import asyncio
import contextlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.factors.library import (
    FACTORS,
    HORIZON_CLOSE,
    HORIZONS_EXEC,
    FactorDef,
)
from app.core.bjtime import BJ_TZ, beijing_now  # S2-8 时区收敛

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- 准入阈值（文档 §4 对齐）
IC_ABS_MIN = 0.02          # |RankIC 均值| 下限
ICIR_ABS_MIN = 0.30        # |ICIR|（IC 均值/IC 标准差，非年化）下限
YEAR_CONSISTENCY_MIN = 0.60  # 分年 IC 与全期同号年份占比下限
# Q5-Q1 多空差下限（与 IC 同号）。**单位口径（R17）**：分母是「期间收益 × 243/(h−1)」
# ——简单线性年化的**近似值**，重叠持有下不是可实现年化收益，仅作跨窗口量纲可比。
LS_ANN_MIN = 0.03
SAME_DIR_PAIRS_MIN = 3     # 五分位相邻 4 对中至少 3 对与多空方向一致
COVERAGE_MIN = 0.90        # 截面覆盖率下限
IC_CORR_DEDUP = 0.70       # 因子间 IC 相关去重阈值
MIN_CROSS_SECTION = 30     # 每日截面最少股票数
DAYS_PER_YEAR = 243        # A 股年均交易日（年化用）
ROLLING_WINDOW_DAYS = 250  # 滚动衰减监控窗口（约 1 年，制度 §6.1/§7.2）

#: 算法口径版本（R15-R17 验收：算法口径变化必须能定位到受影响产物）。
#: 2026-09-14 变更：① 年化由「期间收益误当日收益 ×243」改为「期间收益 ×243/(h−1)」（近似）；
#: ② 年度多空由「只取 Q5」改为「同年 Q5 − Q1」；③ **IC 按窗口分别排名与过滤**
#: （成熟样本内排名 + `n{h} >= MIN_CROSS_SECTION`）。旧报告结论保留为 `verdict_prev` 并标待复核。
#: 2026-09-15 变更（**`BUG-002` 修复**）：`_quintile_sql` 的 `ntile(5)` 补唯一 tie-break
#: （`ORDER BY f, thscode`）。原式排序键在截面内大量并列 ⇒ 分位归属随并行执行顺序变化，
#: 同一输入连跑可出不同 verdict（`rank20` 实测 5 次中 3 次过分层判据、2 次不过）。
#: ⚠️ **这是口径变更**：并列样本的归属由「随机」变为「确定」，分位成员随之改变——
#: 实测 tie-break 后的 `(yr,q,avg_fwd,n)` 指纹与修复前**任何一次**观测都不相同
#: （`rank20` 多空年化由 −8.72~−8.90% 变为 −8.39%）。故必须 bump，并使旧结论走 `review_required`。
ALGO_VERSION = "2026-09-15.ntile-tiebreak-v4"

#: **新增因子不 bump 本版本号**（RSH-001 定例，2026-09-14）：
#: 本常量描述的是**判定口径**——signal/entry/exit、剔除规则、IC 排名与最小截面守卫、准入阈值。
#: 纯新增因子（如 `rank20`）不改其中任何一项，也不改任何既有因子的输出：既有因子的 IC 序列与
#: verdict **逐点不变**（base 链只增列、不改列），由 `tests/test_factors.py::
#: test_adding_factor_does_not_change_existing_numeric_conclusions` 钉死（含注入自证）。
#: 因子池变化本身由报告 `factors`/`summary` 列表可见，并按制度
#: `docs/strategy/factor-lifecycle-governance.md` §8 记入版本日志——`ALGO_VERSION` 不是它的载体。
#: 反之，若仅因新增因子就 bump，`review_required` 会列出**全部**历史结论并标「基于旧口径」，
#: 而它们在数值上并未失效 ⇒ 属误报，会训练消费方忽略该清单（正是 GOV-001 要防的）。
#: **判据**：改动能改变既有因子**数值结论**（IC/verdict）时才是口径变更，必须 bump 并使旧结论
#: 走 `review_required`；只增不改则否。唯一例外是 `redundant_with` 会按新池重算——它是
#: 「去重提示，人工取舍」（非结论），不构成 bump 理由。

#: 数据质量硬结论（2026-09-07 marketdb 实测，docs/summary/factor-system.md §1.4）
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
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cnt,
           -- RSH-003（2026-09-14）MFI 的**复权**典型价 TP_adj = ((H+L+C)/3) × (close_adj/close_price)。
           -- 为什么必须复权（而非照抄 TA-Lib 的原价 TP）：除权日原始价腰斩，未复权 TP 会把
           -- 「10 送 10」误读成**巨额资金流出**（单日 −50% 的负向冲击）并污染随后 20 日的 MFI。
           -- 乘上复权因子后 TP_adj 在除权处**连续**（原始价 ÷2 与复权因子 ×2 相消）。
           (high_price + low_price + close_price) / 3.0
               * (close_adj / NULLIF(close_price, 0)) AS tp_adj
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
           END AS tr_f,
           -- RSH-003：MFI 需要 TP_adj 的**前值**判方向（>前值=流入日 / <前值=流出日）。
           -- 放在 lvl2 而非 lvl1：TP_adj 在 lvl1 刚生成，**同层 SELECT 不能引用自己的别名**。
           LAG(tp_adj) OVER (PARTITION BY thscode ORDER BY date_ms) AS tp_adj_1
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
           -- RSH-001（2026-09-14）：qlib RANK20 —— 现价在 20 日**滚动窗口内**的百分位排名。
           -- 平均名次法的分子两件：窗口内 `< 现价` 与 `<= 现价` 的样本数（在 lvl4 汇合）。
           -- 注：`list()` **包含 NULL**，故此处只取计数，分母在 lvl4 用 COUNT(close_adj)。
           len(list_filter(list(close_adj) OVER r20c, v -> v <  close_adj)) AS rank20_lt,
           len(list_filter(list(close_adj) OVER r20c, v -> v <= close_adj)) AS rank20_le,
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
           COUNT(tr_f) OVER r14c AS atr14_n,
           -- RSH-003（2026-09-14）：MFI20 —— 正/负资金流（TP_adj 方向 × **成交额**）。
           -- 用 `turnover`（额）而非 `volume`（股数），以便与 `fund_flow` 的资金流口径互验。
           -- 这与 TA-Lib 原式（用股数）是**有意的口径差异**，已写入 library.py 的 note。
           SUM(CASE WHEN tp_adj > tp_adj_1 THEN tp_adj * turnover ELSE 0.0 END)
               OVER r20c AS mfi_pos20,
           SUM(CASE WHEN tp_adj < tp_adj_1 THEN tp_adj * turnover ELSE 0.0 END)
               OVER r20c AS mfi_neg20,
           COUNT(CASE WHEN tp_adj IS NOT NULL AND tp_adj_1 IS NOT NULL
                           AND turnover IS NOT NULL THEN 1 END) OVER r20c AS mfi20_n,
           -- RSH-003：OBV20 —— 按**价格方向**加权的成交量净额，及其分母与有效样本数。
           -- 与 `vsumd20` 对偶但判据不同：后者的方向取自 volume vs 前一日量，此处取自 close_adj vs c1。
           -- 分子分母的样本面**必须严格一致**（都要求 close_adj / c1 / volume 三者非 NULL）：
           -- 否则「方向不可判」的行会只进分母不进分子，把占比系统性地压低。
           SUM(CASE WHEN close_adj > c1 THEN volume
                    WHEN close_adj < c1 THEN -volume
                    ELSE 0.0 END) OVER r20c AS obv20_num,
           SUM(CASE WHEN close_adj IS NOT NULL AND c1 IS NOT NULL AND volume IS NOT NULL
                    THEN volume ELSE 0.0 END) OVER r20c AS obv20_den,
           COUNT(CASE WHEN close_adj IS NOT NULL AND c1 IS NOT NULL AND volume IS NOT NULL
                      THEN 1 END) OVER r20c AS obv20_n
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
           CASE WHEN atr14_n >= 14 THEN atr14_raw END AS atr14_w,
           -- RSH-001：qlib RANK20 平均名次百分位 = (n_lt + n_le + 1) / (2·n_valid)。
           -- 该闭式与 pandas `rank(pct=True, method="average")`（qlib 主路径）逐点等价，
           -- 已由 tests/test_factors.py 以朴素参考实现钉死（含并列与全并列）。
           -- **两处守卫都不是可选的**（均属三态纪律，缺失即塌缩）：
           --   ① 分母必须用 `w20_n` = COUNT(close_adj)（**忽略 NULL**）——
           --      若用 `len(list(close_adj) OVER r20c)`（**含 NULL**）会把缺失日计入样本数；
           --   ② `close_adj IS NULL` 时窗口比较全为 NULL ⇒ 分子恒为 0，若不显式置 NULL
           --      会输出 0.0（"最弱"）而非「未判定」——把缺数误读成极端值。
           -- 窗口有效样本 <20 → NULL（与 vola20/amihud20/std20 同口径，不凑 0）。
           CASE WHEN w20_n >= 20 AND close_adj IS NOT NULL
                THEN (rank20_lt + rank20_le + 1) / (2.0 * w20_n) END AS rank20_w,
           -- RSH-003（2026-09-14）TA-Lib MFI20 = 100 − 100/(1 + 正资金流/负资金流)。
           -- 三态守卫两条（均不可省）：
           --   ① 窗口有效样本 <20 → NULL（与 vola20/amihud20/rank20 同口径，不凑 0）；
           --   ② `正 + 负 = 0`（20 日 TP_adj 全程持平）⇒ **无方向信息** ⇒ NULL。
           --      此处若输出 50.0 就是凭空的「中性值」，属凑数（三态纪律禁止）。
           -- `负 = 0` 且 `正 > 0` **不是**缺失：数学上 MFI 的极限就是 100（全程净流入），
           -- 显式写出以免 `NULLIF` 把这个**真实的极端读数**塌成 NULL。
           CASE WHEN mfi20_n >= 20 AND (mfi_pos20 + mfi_neg20) > 0
                THEN CASE WHEN mfi_neg20 = 0 THEN 100.0
                          ELSE 100.0 - 100.0 / (1.0 + mfi_pos20 / mfi_neg20) END
           END AS mfi20_w,
           -- RSH-003 OBV20 归一化：净额 / 总量 ∈ [-1, 1]。
           -- 取**占比**而非累积值：累积 OBV 是路径依赖量（依赖起点选取），跨股票不可比。
           -- 该窗口定义与 `vsumd20` 同形，但方向判据是**价格**（close_adj vs c1）。
           CASE WHEN obv20_n >= 20 AND obv20_den > 0
                THEN obv20_num / obv20_den END AS obv20_w
    FROM lvl3
)"""


def _base_sql(factor: FactorDef) -> str:
    """单因子日 IC 全流程：base 链 → scored(因子+fwd+一字过滤) → ranked → daily。

    **成熟度口径（R16，2026-09-14 修）**：`percent_rank() OVER (PARTITION BY date_ms
    ORDER BY fwd_h)` 会给 **NULL 也分配名次**（窗口排序把 NULL 排在分区端点），
    于是「T+h 尚未到来」的样本会带着一个**假名次**混进 `corr(rf, rr{h})` 的配对里；
    而 `n` 用的是**全部截面**，最小截面守卫因此被虚增样本绕过
    （审查复现：截面 32 只、20 日窗口仅 24 只有标签，SQL 仍报 n=32 且 ic20 为有限数）。

    两处修法都必要（`kb/09` KB-ENG-72：**修判据 ≠ 修守卫，两件事都要做**）：
    ① **按窗口分别排名**——`PARTITION BY date_ms, (fwd_exec_h IS NOT NULL)`
       把每个交易日切成「该窗口已成熟 / 未成熟」两个分区，成熟分区内的 `percent_rank`
       就是在**成熟样本内部**的名次（未成熟分区取出的值一律被 CASE 置 NULL）；
    ② **按窗口分别过滤**——`daily` 输出各窗口自己的成熟计数 `n{h}`（close 口径 `nc{h}`），
       守卫改用 `n{h} >= MIN_CROSS_SECTION`（见 `_mature_key` / `_agg_window`）。

    `n`（全部截面）保留原义，只用于覆盖率——覆盖率统计的是「有因子值的截面」，
    与某窗口是否成熟无关，滤掉会虚增覆盖率。
    """
    horizons_sql = ", ".join(f"corr(rf{h}, rr{h}) AS ic{h}" for h in HORIZONS_EXEC)
    rank_sql = ", ".join(
        f"percent_rank() OVER (PARTITION BY date_ms, (fwd_exec_{h} IS NOT NULL) "
        f"ORDER BY f) AS rf{h}, "
        f"CASE WHEN fwd_exec_{h} IS NOT NULL THEN percent_rank() OVER "
        f"(PARTITION BY date_ms, (fwd_exec_{h} IS NOT NULL) "
        f"ORDER BY fwd_exec_{h}) END AS rr{h}"
        for h in HORIZONS_EXEC
    )
    n_sql = ", ".join(f"count(fwd_exec_{h}) AS n{h}" for h in HORIZONS_EXEC)
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
           {rank_sql},
           percent_rank() OVER (PARTITION BY date_ms, (fwd_close_{HORIZON_CLOSE} IS NOT NULL)
                                ORDER BY f) AS rfc{HORIZON_CLOSE},
           CASE WHEN fwd_close_{HORIZON_CLOSE} IS NOT NULL THEN percent_rank() OVER
                (PARTITION BY date_ms, (fwd_close_{HORIZON_CLOSE} IS NOT NULL)
                 ORDER BY fwd_close_{HORIZON_CLOSE}) END AS rrc{HORIZON_CLOSE}
    FROM scored
    WHERE f IS NOT NULL
),
daily AS (
    SELECT date_ms, COUNT(*) AS n, {n_sql},
           count(fwd_close_{HORIZON_CLOSE}) AS nc{HORIZON_CLOSE},
           {horizons_sql},
           corr(rfc{HORIZON_CLOSE}, rrc{HORIZON_CLOSE}) AS icc{HORIZON_CLOSE}
    FROM ranked
    GROUP BY date_ms
)
SELECT * FROM daily ORDER BY date_ms
"""


#: 各窗口的**成熟样本数**列名（R16）：执行口径 `n{h}`、close 口径 `nc{h}`。
#: 两个口径必须分开命名——`HORIZON_CLOSE` 若与某个执行窗口同值时用同一列名会串。
def _mature_key(horizon: int) -> str:
    return f"nc{horizon}" if horizon == HORIZON_CLOSE else f"n{horizon}"


def _quintile_sql(factor: FactorDef, horizon: int) -> str:
    """主窗口五分位（复用完整 base 链——gap/range20 的表达式依赖 lvl3/lvl4 列）：
    全期（yr='ALL'）+ 分年。avg 为逐样本等权（日间不等权，近似）。

    **排序键必须唯一（`BUG-002` 修复，2026-09-15）**：`ntile(5) ... ORDER BY f`
    的排序键 `f` 在截面内**大量并列**（整数索引类因子如 `imax20` 取值仅 0~19、
    计数类如 `cntp20` 亦然）⇒ 分位切点落在并列块中间时，**谁进哪一组取决于执行顺序**
    ⇒ 并行（默认 `threads` = 核数）下同一输入连跑连出不同结论，`monotonic_pairs`
    在整数阈值 `SAME_DIR_PAIRS_MIN` 上抖动 ⇒ verdict 不可位级复现。

    故补 `thscode` 作 tie-break：`(date_ms, thscode)` 是主键 ⇒ `ORDER BY f, thscode`
    是**全序**，分位归属完全确定。实测（`market.duckdb` 1027 万行，2026-09-15）：
    `rank20` 原式 3 次跑出 3 个不同 `(yr,q,avg_fwd,n)` 指纹、分层判据 5 次里通过 3 次
    （`mono_pairs` 出现 2 与 3 两种取值，跨 `>=3` 阈值）⇒ verdict 在 PASS/CONDITIONAL
    间翻转；加 tie-break 后 3 次指纹**逐字相同**。

    ⚠️ **并列样本仍是"被硬切"到相邻组**（`ntile` 语义即等分，不保证并列同组）；
    tie-break 只保证**切分位置确定且可复现**，不改变"并列可被拆开"这一性质。
    若将来需要"并列整组同归属"，那是**另一套口径**（组大小将不再相等），须另行拍板。
    """
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
           ntile(5) OVER (PARTITION BY date_ms ORDER BY f, thscode) AS q
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


def _agg_window(
    daily: list[dict], horizon: int, ic_all: float, *,
    n_key: str | None = None, min_cross_section: int = MIN_CROSS_SECTION,
) -> WindowStats | None:
    """聚合单窗口的日 IC 序列。

    **R16（2026-09-14）**：`n_key` 给定时，逐日按**该窗口自己的成熟截面数**
    再执行一次 `MIN_CROSS_SECTION` —— 旧实现只在调用方按共享的 `n`（全部截面）筛一次，
    而 `n{h} <= n` 恒成立 ⇒ 三十只里只有二十只有 20 日标签的日子照样计入 IC，
    守卫形同虚设。判据本身（30）不变，变的是它的**输入**。
    """
    key = f"ic{horizon}"
    # NaN 防御：截面零方差（同涨同跌日）时 corr = 0/0 = NaN，不得混入统计
    pts = [
        (r["date_ms"], r[key]) for r in daily
        if r.get(key) is not None and isinstance(r[key], (int, float))
        and not math.isnan(r[key])
        and (n_key is None or int(r.get(n_key) or 0) >= min_cross_section)
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


def _judge_quintiles(qrows: list[dict], horizon: int) -> dict:
    """全期五分位 → 多空差/单调性/年化。q1=因子值最低组，q5=最高组。

    **量纲契约（R17，2026-09-14 修）**：`_quintile_sql` 的 `fwd = f{h}/f1 - 1` 是
    **T+1 收盘 → T+h 收盘的期间收益**，持有期 = `horizon - 1` 个交易日。
    旧实现把期间收益命名为 `q_avg_daily` 并直接 `×243`——19 日的 2% 被报成 486%。
    本函数把三种量**分开命名、分开返回**，不再混用：

    - `q_avg_period` / `long_short_period` —— **期间收益**（一手量，缩放前）；
    - `q_avg_daily_approx` —— 期间收益 ÷ (h−1)，**日均近似**；
    - `q_avg_ann_simple_pct` / `long_short_ann_pct` —— 期间收益 × 243/(h−1)，
      **简单线性年化，仅为近似**：样本内每日都在建仓，同一时刻有 h−1 个重叠批次，
      该缩放值**不是可实现的年化收益**，只用于跨窗口量纲可比；
    - `implementable_annual_return_pct` —— **恒为 None**（三态显式「未判定」）：
      可实现的组合年化必须来自逐日建仓/持仓的净值曲线，本模块不产出，
      **不得**用上面的缩放值冒充（审查 R17 验收第 4 条）。

    `yearly_ls_*` 为**同年 Q5 − Q1**（旧实现只取 Q5、不减 Q1——那仍是含市场 beta
    的绝对收益，不是多空收益）。
    """
    hold_days = horizon - 1
    if hold_days < 1:
        raise ValueError(f"horizon 必须 ≥2（持有期 = horizon−1 个交易日），实收 {horizon}")
    allq = {r["q"]: r["avg_fwd"] for r in qrows if r["yr"] == "ALL"}
    if len(allq) < 5:
        return {"available": False, "reason": "分位组不足 5（截面过小）"}

    def _ann_pct(period: float) -> float:
        """期间收益 → 简单线性年化百分数（近似，**非**可实现年化收益）。"""
        return round(period * DAYS_PER_YEAR / hold_days * 100, 2)

    ls = allq[5] - allq[1]
    diffs = [allq[i + 1] - allq[i] for i in range(1, 5)]
    direction = math.copysign(1, ls) if ls != 0 else 0.0
    same_pairs = sum(1 for d in diffs if d != 0 and math.copysign(1, d) == direction)

    # 分年多空：同年 Q1 与 Q5 必须都在，缺一组则该年不产出（三态，不凑 0）
    by_year: dict[str, dict[int, float]] = {}
    for r in qrows:
        if r["yr"] == "ALL":
            continue
        by_year.setdefault(r["yr"], {})[r["q"]] = r["avg_fwd"]
    yearly_period = {
        y: round(qs[5] - qs[1], 6)
        for y, qs in sorted(by_year.items()) if 1 in qs and 5 in qs
    }

    return {
        "available": True,
        "horizon": horizon,
        "hold_days": hold_days,
        "annualization": "simple_linear",  # 期间收益 × 243/(h−1)，近似
        "q_avg_period": {str(k): round(v, 6) for k, v in sorted(allq.items())},
        "q_avg_daily_approx": {
            str(k): round(v / hold_days, 8) for k, v in sorted(allq.items())
        },
        "q_avg_ann_simple_pct": {str(k): _ann_pct(v) for k, v in sorted(allq.items())},
        "long_short_period": round(ls, 6),
        "long_short_ann_pct": _ann_pct(ls),
        "monotonic_pairs": same_pairs,  # /4
        "yearly_ls_period": yearly_period,
        "yearly_ls_ann_pct": {y: _ann_pct(v) for y, v in yearly_period.items()},
        # 三态：本模块不产出可执行组合曲线 ⇒ 可实现的年化收益「未判定」，
        # 不得用上面的缩放值顶替（审查 R17 验收第 4 条）
        "implementable_annual_return_pct": None,
        "note": (
            f"期间收益 = T+1→T+{horizon} 收盘（持有 {hold_days} 个交易日）；"
            f"年化 = 期间收益 × {DAYS_PER_YEAR}/{hold_days}（简单线性，近似）；"
            "样本内每日重叠建仓 ⇒ 该缩放值非可实现年化收益"
        ),
    }


def evaluate_factor(con, factor: FactorDef, market_daily: dict[int, int]) -> dict:
    """评估单因子：日 IC 全窗口 → 主窗口五分位 + 覆盖率 → 三层判定。"""
    rows = con.execute(_base_sql(factor)).fetchall()
    cols = [d[0] for d in con.description]
    daily = [dict(zip(cols, r)) for r in rows]

    # 协议排除（MIN_CROSS_SECTION）：每日截面 <30 只的 IC 不计入聚合（小截面失真）。
    # 覆盖率统计**不含**此过滤——小截面日是真实的覆盖信号，滤掉会虚增覆盖率。
    # R16：这里只是「整体截面」的粗筛（n_h ≤ n，故不会放过 n_h 不足的日子）；
    # **真正生效的判据**在 `_agg_window` 里按各窗口成熟计数 `n{h}` 再筛一次。
    daily_ic = [r for r in daily if r["n"] >= MIN_CROSS_SECTION]

    # 成熟度留痕（R16）：每个窗口的成熟样本均值与成熟率 —— 「n 是多少只」必须能被看见，
    # 否则"截面 32 只"与"该窗口只有 24 只有标签"这两种完全不同的输入会长得一模一样。
    maturity: dict[str, dict] = {}
    for h in (*HORIZONS_EXEC, HORIZON_CLOSE):
        k = _mature_key(h)
        usable = [r for r in daily if r.get(k) is not None]
        m_sum = sum(int(r[k]) for r in usable)
        n_sum = sum(int(r["n"]) for r in usable)
        maturity[str(h)] = {
            "days": len(usable),
            "days_ge_min": sum(1 for r in usable if int(r[k]) >= MIN_CROSS_SECTION),
            "mean_mature": round(m_sum / len(usable), 1) if usable else None,
            "mature_rate": round(m_sum / n_sum, 4) if n_sum else None,
        }

    wins: dict[int, WindowStats] = {}
    ic_series: dict[int, list[tuple[int, float]]] = {}
    for h in (*HORIZONS_EXEC, HORIZON_CLOSE):
        n_key = _mature_key(h)
        w = _agg_window(daily_ic, h, ic_all=0.0, n_key=n_key)  # consistency 需 ic_all，二次填充
        if w is not None:
            w2 = _agg_window(daily_ic, h, ic_all=w.ic_mean, n_key=n_key)
            wins[h] = w2
            # 与 _agg_window 同口径过滤：NaN（截面零方差日）+ 该窗口成熟截面不足
            ic_series[h] = [
                (r["date_ms"], r[f"ic{h}"]) for r in daily_ic
                if int(r.get(n_key) or 0) >= MIN_CROSS_SECTION
                and r.get(f"ic{h}") is not None and not math.isnan(r[f"ic{h}"])
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
            "maturity": maturity,
        }
    best_h = max(pool, key=lambda h: abs(pool[h].icir))

    # 覆盖率：有因子值截面数 / 全市场截面数（日级均值）
    cov_pairs = [r["n"] / market_daily[r["date_ms"]] for r in daily if r["date_ms"] in market_daily]
    coverage = round(_mean(cov_pairs), 4) if cov_pairs else None

    # 主窗口五分位（必须传 best_h：年化缩放系数 = 243/(h−1)，R17）
    qrows = con.execute(_quintile_sql(factor, best_h)).fetchall()
    qcols = [d[0] for d in con.description]
    quint = _judge_quintiles([dict(zip(qcols, r)) for r in qrows], best_h)

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
    # 分层准入：|多空年化| 门槛。**单位是百分数**，且该值是 243/(h−1) 线性缩放的近似
    # （重叠持有下非可实现收益）——阈值本身不变，但缩放系数修正后判定会自然变严（R17）。
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
        reasons.append(
            f"分层未过线：多空年化 {ls_ann}%"
            f"（= 期间收益 ×{DAYS_PER_YEAR}/{best_h - 1} 的简单线性近似）、"
            f"单调对 {mono}/4（需 |多空|≥{LS_ANN_MIN * 100}% 同号且 ≥{SAME_DIR_PAIRS_MIN}/4）"
        )
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
        "maturity": maturity,  # R16：各窗口成熟样本均值 / 成熟率 / 过线天数
        "verdict": verdict,
        "reasons": reasons,
        "daily_ic": {str(h): ic_series.get(h, []) for h in (best_h,)},
    }


# ---------------------------------------------------------------- 全量评估
def _prev_verdicts(out_path: str | Path | None) -> dict[str, str]:
    """读取**将被覆盖**的旧报告的逐因子结论。

    R15-R17 验收要求「旧结论保留并标待复核，修复不自动把通过改失败」——
    口径修复会改变量纲与阈值判定，因此重算前先把旧结论取出来做差，
    让 PASS→FAIL 这类翻转**显式留痕**而不是被静默覆盖。
    旧报告缺失 / 损坏 = 无旧结论（返回 {}，不阻塞评估）。
    """
    if out_path is None:
        return {}
    try:
        p = Path(out_path)
        if not p.exists():
            return {}
        old = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  旧报告损坏等同无旧结论
        return {}
    entries = old.get("factors") if isinstance(old, dict) else None
    if not isinstance(entries, list):
        return {}
    return {
        e["name"]: e.get("verdict") for e in entries
        if isinstance(e, dict) and e.get("name")
    }


def _annotate_verdict_changes(results: list[dict], prev: dict[str, str]) -> list[dict]:
    """给每个因子挂上旧结论（`verdict_prev`）与翻转标记（`verdict_changed`/`recheck`）。

    返回「待复核」清单（**纯函数**，便于单测——口径修复不静默翻转历史结论）。
    """
    recheck: list[dict] = []
    for r in results:
        v0 = prev.get(r["name"])
        r["verdict_prev"] = v0
        changed = v0 is not None and v0 != r["verdict"]
        r["verdict_changed"] = changed
        if changed:
            r["recheck"] = "pending"
            recheck.append({
                "name": r["name"], "verdict_prev": v0, "verdict_now": r["verdict"],
            })
    return recheck


# ---------------------------------------------------------------- 版本化留存与复核清单
#: 版本化留存子目录名（相对评估报告所在目录）。制度文档
#: `docs/strategy/factor-lifecycle-governance.md` §7.2 承诺「报告按 generated_at 版本化留存」，
#: 而落盘路径长期是**单文件原地覆盖**——历史结论只剩上一次的 `verdict_prev`，更早的
#: 不可枚举（[[KB-ENG-72]]：**文档承诺 ≠ 实际覆盖**）。
HISTORY_DIRNAME = "history"


def _history_dir(out_path: str | Path) -> Path:
    """归档目录**跟随报告落点**（不锚定模块级常量）⇒ 测试传 tmp 出参时同样成立。"""
    return Path(out_path).parent / HISTORY_DIRNAME


def _archive_previous(out_path: str | Path | None) -> Path | None:
    """把**将被覆盖**的旧报告归档到 `history/`，返回归档路径（无可归档返回 None）。

    归档名含**旧报告自己声明的口径版本**与生成时间 ⇒ 可同时回答
    「哪些结论是旧口径算的」，即口径变更后复核清单的输入。
    **幂等**：同名已存在则不重写（重复归档同一份旧报告不产生新文件）。
    旧报告缺失 / 不可解析 ⇒ None，**不阻塞评估**（与 `_prev_verdicts` 同姿势）。
    """
    if out_path is None:
        return None
    src = Path(out_path)
    try:
        if not src.exists():
            return None
        old = json.loads(src.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  旧报告损坏 = 无可归档（不阻塞）
        return None
    if not isinstance(old, dict):
        return None
    declared = old.get("algo_version")
    ver = declared if isinstance(declared, str) and declared else "unknown"
    # 时间戳去冒号以跨文件系统安全（`2026-09-07T17:00:46` → `2026-09-07T170046`）
    gen = str(old.get("generated_at") or "undated").replace(":", "")
    dst = _history_dir(src) / f"eval_report.{ver}.{gen}.json"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            dst.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001  归档失败不得阻塞本轮评估
        log.warning("factor report archive failed: %s", dst)
        return None
    return dst


def _prev_algo_version(out_path: str | Path | None) -> str | None:
    """旧报告**自己声明**的口径版本。

    **三态**：声明了 → 该字符串；旧报告存在但无该字段（口径版本机制之前的产出）→ None
    ⇒ 调用方须按「未判定」处理，**不得塌缩成「确定过期」**（§1「三态 > 二态」纪律）。
    旧报告缺失 / 损坏同样 None。
    """
    if out_path is None:
        return None
    try:
        p = Path(out_path)
        if not p.exists():
            return None
        old = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(old, dict):
        return None
    declared = old.get("algo_version")
    return declared if isinstance(declared, str) and declared else None


def _build_review_required(
    results: list[dict], prev: dict[str, str], prev_algo: str | None, cur_algo: str,
) -> list[dict]:
    """列出**全部基于旧口径的历史结论**（含未翻转的）——GOV-001 核心（**纯函数**）。

    为什么不能只看翻转（`recheck`）：**未翻转 ≠ 不受影响**。旧结论是用旧口径算出来的
    数，口径一改它就该被人工复核——哪怕三态恰好停在原位。只看翻转会让「结论没变」
    被读成「结论仍然成立」，而报告 F5 要防的正是「旧报告继续被当调参依据」。

    三种情形（**三态纪律：`未判定` 不等于 `未变更`**）：

    - 无历史结论（首次跑，`prev` 为空）⇒ `[]`；
    - 旧报告口径 == 当前口径 ⇒ `[]`（翻转项另由 `recheck` 承载，两清单职责不重叠）；
    - 旧报告口径 != 当前口径 ⇒ 列出全部旧结论，`reason` 标「基于旧口径 X」；
    - 旧报告**未声明**口径（机制之前的产出）⇒ 同样列出全部旧结论，`reason` 标
      「未判定是否同源」——**旧报告不可知不等于可以继续当调参依据**（实测活案例：
      2026-09-14 磁盘报告产出于 09-07，无该字段）。
    """
    if not prev:
        return []
    if prev_algo == cur_algo:
        return []
    if prev_algo is None:
        reason = f"旧报告未声明口径版本 ⇒ 是否与当前口径 {cur_algo} 同源**未判定**"
    else:
        reason = f"结论基于旧口径 {prev_algo}（当前 {cur_algo}）"
    out: list[dict] = []
    for r in results:
        v0 = prev.get(r["name"])
        if v0 is None:
            continue  # 旧口径下本无该因子结论（新增因子）⇒ 无历史结论可复核
        out.append({
            "name": r["name"],
            "verdict_prev": v0,
            "verdict_now": r["verdict"],
            "changed": v0 != r["verdict"],
            "reason": reason,
        })
    return out


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

        # 口径版本追溯（R15-R17 验收）：与旧报告逐因子做差，旧结论保留为 verdict_prev，
        # 翻转项标 recheck=pending——**修复不静默把通过改成失败**，翻转必须显式留痕待复核。
        # GOV-001 增量：重算前先把旧报告**归档**（制度 §7.2 承诺的版本化留存，此前只在
        # 文档里、落盘仍是原地覆盖），并读出旧报告自己声明的口径版本 —— 口径变更时据此
        # 列出**全部**旧口径结论（含未翻转），而不只是翻转项（未翻转 ≠ 不受影响）。
        prev_verdicts = _prev_verdicts(out_path)
        prev_algo = _prev_algo_version(out_path)
        archived = _archive_previous(out_path)
        recheck = _annotate_verdict_changes(results, prev_verdicts)
        review_required = _build_review_required(results, prev_verdicts, prev_algo, ALGO_VERSION)
        algo_changed = (None if prev_algo is None else prev_algo != ALGO_VERSION)

        report = {
            "generated_at": beijing_now().isoformat(timespec="seconds"),
            "algo_version": ALGO_VERSION,
            # 口径变更的可判定性（GOV-001）：上一份报告声明的口径 + 是否已变 + 被归档的旧报告名。
            # `prev_algo_version is None` = 上一份报告无该字段（未判定），不得读成「口径未变」。
            "prev_algo_version": prev_algo,
            "algo_changed": algo_changed,
            "archived_prev_report": (archived.name if archived is not None else None),
            "protocol": {
                "algo_version": ALGO_VERSION,  # R15-R17：口径变更必须能定位到受影响产物
                "signal": "T 收盘", "entry": "T+1 收盘（保守执行口径）",
                "exit": [f"T+{h} 收盘" for h in HORIZONS_EXEC],
                "exclusions": [
                    "T+1 一字板",
                    f"每日截面 <{MIN_CROSS_SECTION} 只",
                    "次新（cnt < min_bars）",
                ],
                # IC 截面口径（R16）：按窗口**分别**排名与过滤
                "ic_maturity": {
                    "ranking": "每个窗口在**自身已成熟样本内**排名（同日切成成熟/未成熟两分区）",
                    "min_cross_section": MIN_CROSS_SECTION,
                    "guard_input": "各窗口自己的成熟截面数 n{h}（close 口径 nc{h}），非全截面 n",
                    "note": ("旧实现按全截面排名且以 n 守卫 ⇒ 未成熟样本带假名次进入 corr，"
                             "截面 32 只而 20 日仅 24 只有标签的日子照样计入 IC"),
                },
                "recheck_policy": {
                    "prev_verdict": "verdict_prev（来自被覆盖的旧报告）",
                    "on_change": "verdict_changed=True 且 recheck='pending'（不静默改写旧结论）",
                    # GOV-001：翻转清单 ≠ 复核清单——口径变更时**未翻转的旧口径结论同样要复核**
                    "versioned_review": (
                        "review_required 列出**全部**需复核的历史结论（含三态未变的）："
                        "旧口径与当前不同、或旧报告未声明口径（未判定）时均非空；"
                        "仅当无历史结论或口径确认为同一版本时才为空"
                    ),
                },
                # 收益量纲口径（R17）：期间收益 → 简单线性年化，近似且不可实现
                "annualization": {
                    "period_return": "T+1 收盘 → T+h 收盘，持有 h−1 个交易日",
                    "method": "simple_linear",
                    "factor": "243/(h−1)",
                    "approximation": True,
                    "implementable": False,
                    "note": ("样本内每日重叠建仓，同一时刻存在 h−1 个未平仓批次 ⇒ "
                             "缩放值不是可实现的年化收益；可实现口径须走逐日净值曲线"),
                },
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
            # 口径变更引起的结论翻转（旧结论见各因子 verdict_prev）——待人工复核
            "recheck": recheck,
            # 口径变更时**全部**基于旧口径的历史结论（含未翻转）——GOV-001「版本化复核清单」
            "review_required": review_required,
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


# ---------------------------------------------------------------- 月度复核调度（S2-11）
#: 与 `rps.py` / `chip.py` / `strategy_verify.py` 同口径的行情库路径
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "marketdb" / "market.duckdb"
#: 评估产物落盘位置（`factors/report.py::REPORT_PATH` 读的就是它）
DEFAULT_OUT_PATH = Path(__file__).resolve().parents[2] / "data" / "factors" / "eval_report.json"
#: 调度状态（记录"上次尝试"的日期，避免失败时每个检查周期都重跑全量评估）
EVAL_STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "factors" / "eval_state.json"


def _load_eval_state() -> dict:
    try:
        if EVAL_STATE_PATH.exists():
            data = json.loads(EVAL_STATE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001  状态文件损坏 = 无状态（下次重跑，不阻塞）
        pass
    return {}


def _save_eval_state(state: dict) -> None:
    try:
        EVAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = EVAL_STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(EVAL_STATE_PATH)
    except Exception:  # noqa: BLE001
        log.warning("factor eval state write failed")


def should_run_eval(now, *, run_day: int = 1, max_age_days: int = 40) -> bool:
    """**纯函数**（便于单测）：此刻是否该跑全量评估。

    三个条件同时满足才跑，缺一不可：
    1. 报告缺失或已超 `max_age_days`（月度复核口径，宽限 40 天）；
    2. 当天日期 >= `run_day`（月初跑；设 1 = 每月 1 号之后）；
    3. 今天还没尝试过（防止评估失败时每个检查周期都重跑——37 因子全历史
       扫一遍是分钟级开销，不能拿它当心跳）。
    """
    rep = None
    try:
        if DEFAULT_OUT_PATH.exists():
            rep = json.loads(DEFAULT_OUT_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rep = None

    if isinstance(rep, dict) and rep.get("generated_at"):
        try:
            gen = datetime.fromisoformat(str(rep["generated_at"]).replace("Z", ""))
            # ⚠️ `now` 是 aware（BJ_TZ），产物里的 `generated_at` 是**北京 naive**。
            # 两者直接相减会抛 TypeError（这是刻意保留的防线：naive/aware 语义不可互换），
            # 而下面若用 `except: pass` 吞掉，就会**永远按超期处理** ⇒ 每次启动都跑一遍
            # 37 因子全历史扫描（分钟级）。2026-09-11 实测踩到：报告仅 4 天新鲜却照跑。
            # 修法是**统一语义**后比较，不是吞异常。
            if gen.tzinfo is None:
                gen = gen.replace(tzinfo=BJ_TZ)
            if (now - gen).days <= max_age_days:
                return False
        except (ValueError, TypeError):
            # 只吞"解析/类型"两类已知问题，且**必须留痕**——静默 pass 是死守卫
            log.warning("eval_report.generated_at 无法解析（%r）⇒ 按超期处理",
                        rep.get("generated_at"))

    if now.day < run_day:
        return False

    if _load_eval_state().get("last_attempt_ymd") == now.date().isoformat():
        return False
    return True


async def factor_eval_scheduler(
    *,
    stop: asyncio.Event,
    run_day: int = 1,
    run_hour: int = 17,
    run_minute: int = 30,
    check_interval_seconds: float = 3600.0,
    db_path: Path | None = None,
) -> None:
    """月度因子复核调度（lifespan 任务）。

    **为什么必须 `to_thread`**：`run_full_eval` 走 duckdb 全历史扫描，是分钟级
    同步 CPU/IO 开销；直接在事件循环里跑会卡死整个行情推送（QuoteHub 1s 节奏）。
    """
    while not stop.is_set():
        try:
            now = beijing_now()
            if now.hour * 100 + now.minute >= run_hour * 100 + run_minute and should_run_eval(
                now, run_day=run_day
            ):
                _save_eval_state({**_load_eval_state(), "last_attempt_ymd": now.date().isoformat()})
                log.info("factor eval start (monthly review)")
                t0 = time.monotonic()
                rep = await asyncio.to_thread(
                    run_full_eval, str(db_path or DEFAULT_DB_PATH), out_path=str(DEFAULT_OUT_PATH)
                )
                summary = rep.get("summary") or {}
                log.info(
                    "factor eval done in %.1fs: pass=%d conditional=%d fail=%d",
                    time.monotonic() - t0,
                    len(summary.get("pass") or []),
                    len(summary.get("conditional") or []),
                    len(summary.get("fail") or []),
                )
        except Exception:  # noqa: BLE001
            log.exception("factor eval scheduler failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=check_interval_seconds)
