"""战法 / 策略实证核验器（可复用）。

**定位**：与 `app/factors/evaluate.py` 的区别 —— 后者做**截面因子 IC** 评估（每日排序质量），
本条做**事件研究式**信号核验（信号日 → 前瞻收益）。两者口径不同，不可互相替代。

**五道检验 + 两道边界**（缺任何一道都可能得出"看着对"的错误结论）：
  ① `funnel`      累计漏斗    —— 每多筛一步是变好还是变差（实战中常见"越筛越差"）
  ② `single`      单条件独立  —— **哪一步真携带信息**（只看组合效果会漏掉零信息量/负贡献的步骤）
  ③ `sensitivity` 参数敏感性  —— 原文阈值是不是拍脑袋（方向对但阈值反了比完全无效更常见）
  ④ `regime`      环境分层    —— 市场级效应 vs 选股 alpha
  ⑤ `yearly`      分年度稳定性 —— 稳定效应还是某几年特例（2/11 与 11/11 是天壤之别）
  + `limit_up_share` 可成交性（涨停收盘买不进，纸面最优档常恰在涨停附近）
  + `cost_bps`      成本（往返基点，默认 0；结论必须声明是否扣成本）

**关键口径（默认如此，覆盖需显式）**：
- 买入 = 信号日**收盘**；卖出 = T+N 收盘（close-to-close）。
- 量比分母 = 前 N 日均量，**不含当日**（用"昨日量"分母会把极缩量日误判成放量）。
- **每个统计量都同时给出「原始均值」与「市场中性均值」**：后者 = 个股收益 − **同日全市场均值**。
  这是区分 beta 与 alpha 的唯一正确基准 —— 只用混合池均值会被 market timing 骗
  （例："顺大盘"类条件天然落在好日子，混合池对照会把它算成 alpha）。
- 信号日 `chg >= limit_up_pct` 记为**疑似不可成交**（涨跌停价未做精确判定，是代理口径）。

**用法**：
    con = connect(read_only=True)
    build(con, BuildConfig(float_shares_sql=snapshot_float_shares_sql(SNAPSHOT_DIR)))
    print(render(funnel(con, {"S1": "chg BETWEEN 3 AND 5", "S2": "vr >= 1"})))
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import duckdb

#: 默认 marketdb 路径（与 app/picks/rps.py 等一致：parents[2] = backend/）
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "marketdb" / "market.duckdb"

DEFAULT_HORIZONS: tuple[int, ...] = (1, 3, 5, 10)

#: 疑似涨停的涨幅代理阈值（%）。10% 涨停板/ST 5%/创业板科创板 20% 未区分，
#: 仅用于"这一档能不能买进"的量级判断，不作精确判定。
LIMIT_UP_PCT = 9.5

#: 处置结论（KB-DEC-019 三级态）。`verify_registry` 从此处导入，保持单向依赖：
#: 核验器是纯计算层，登记层依赖它，反之不成立。
VERDICT_PASS = "pass"        # 过闸
VERDICT_OBSERVE = "observe"  # 方向成立但有硬伤
VERDICT_REJECT = "reject"    # 否决


# ---------------------------------------------------------------- 配置 / 连接

@dataclass(frozen=True)
class BuildConfig:
    """特征表构建参数。任何一项改动都会改变结论口径，须与结论一起记录。"""

    source_table: str = "daily_k"
    table: str = "sig"
    vol_lookback: int = 5
    ma_windows: tuple[int, int, int] = (5, 10, 20)
    horizons: tuple[int, ...] = DEFAULT_HORIZONS
    warmup: int = 20
    #: 提供则生成 `turn`（换手率%）并按名称剔除 ST/退市；不提供则 `turn` 为 NULL 且不做名称过滤
    float_shares_sql: str | None = None
    #: 追加到 FROM 的裸 SQL（如 `JOIN mytab t ON t.thscode = f.thscode`）
    extra_joins: str = ""
    #: 追加到 SELECT 列表的裸 SQL（如 `, t.mcap`），须与 extra_joins 配套
    extra_cols: str = ""
    #: 额外过滤（如剔除停牌）；会拼进 WHERE
    extra_filter: str = "TRUE"


@dataclass(frozen=True)
class VerifyConfig:
    """查询期口径（前瞻窗口属**建表期**属性，见 BuildConfig.horizons，此处自动探测）。"""

    table: str = "sigv"
    cost_bps: float = 0.0          # 往返成本（基点），从每个前瞻收益里扣
    limit_up_pct: float = LIMIT_UP_PCT


def horizons_of(con: duckdb.DuckDBPyConnection, table: str = "sigv") -> tuple[int, ...]:
    """从表结构探测已构建的前瞻窗口（`fwd{h}` 列），保证查询期不会引用不存在的列。"""
    cols = [d[0] for d in con.execute(f"DESCRIBE {table}").fetchall()]
    hz = [int(c[3:]) for c in cols if c.startswith("fwd") and c[3:].isdigit()]
    return tuple(sorted(hz)) or DEFAULT_HORIZONS


def connect(db_path: Path | str = DEFAULT_DB_PATH, *, read_only: bool = True):
    return duckdb.connect(str(db_path), read_only=read_only)


def snapshot_float_shares_sql(snapshot_dir: Path | str) -> str:
    """从**最新全市场快照**反推流通股本（股）的 SQL 片段。

    流通股本 = 流通市值(nmc，万元) × 1e4 ÷ 价格。同一天多份快照取 `amount` 最大的一行
    （最接近收盘）。⚠️ 这是"用当期股本算历史"（前视偏差），对期间解禁/增发的个股有误差
    —— **换手率相关结论只能当近似**，须与结论一起声明。
    """
    pat = str(Path(snapshot_dir) / "*.parquet")
    return f"""
    (SELECT symbol || '.' || market AS thscode, any_value(name) AS name,
            max(nmc * 10000.0 / nullif(price, 0)) AS float_shares
     FROM (SELECT *, row_number() OVER (PARTITION BY symbol ORDER BY amount DESC) AS rk
           FROM read_parquet('{pat}') WHERE price > 0 AND nmc > 0)
     WHERE rk = 1 GROUP BY 1)
    """


# ---------------------------------------------------------------- 构建

def build(con: duckdb.DuckDBPyConnection, cfg: BuildConfig = BuildConfig()) -> int:
    """物化特征表 + 同日市场均值视图，返回样本行数。

    一次性算完所有窗口函数（千万行级别必须走这条路，不能把数据拉进内存）。
    产出：
      - `cfg.table`     逐行特征（chg / vr / turn / step_up / ma* / dev* / fwd{h} / at_limit ...）
      - `mkt_fwd`       按日期的全市场 fwd{h} 均值
      - `sigv`          视图 = 特征 LEFT JOIN 市场均值，附带 `mfwd{h}` 与市场中性列在查询时相减
    """
    src, tbl = cfg.source_table, cfg.table
    hz = cfg.horizons
    m5, m10, m20 = cfg.ma_windows

    leads = ",\n                ".join(f"lead(close_price, {h}) OVER w AS c{h}" for h in hz)
    fwd_cols = ", ".join(f"(f.c{h} / f.close_price - 1) * 100 AS fwd{h}" for h in hz)

    if cfg.float_shares_sql:
        fs_join = f"JOIN {cfg.float_shares_sql} fs ON fs.thscode = f.thscode"
        turn_expr = "f.volume / nullif(fs.float_shares, 0) * 100"
        name_filter = "AND fs.name IS NOT NULL AND fs.name NOT LIKE '%ST%' AND fs.name NOT LIKE '%退%'"
    else:
        fs_join, turn_expr, name_filter = "", "NULL::DOUBLE", ""

    con.execute(f"DROP TABLE IF EXISTS {tbl}")
    con.execute(f"""
        CREATE TEMP TABLE {tbl} AS
        WITH feat AS (
            SELECT thscode, date_ms, close_price, volume,
                lag(close_price) OVER w AS prev_close,
                avg(volume) OVER (PARTITION BY thscode ORDER BY date_ms
                                  ROWS BETWEEN {cfg.vol_lookback} PRECEDING AND 1 PRECEDING)
                    AS vol_ma_prev,
                avg(close_price) OVER (PARTITION BY thscode ORDER BY date_ms
                                       ROWS BETWEEN {m5 - 1} PRECEDING AND CURRENT ROW) AS ma{m5},
                avg(close_price) OVER (PARTITION BY thscode ORDER BY date_ms
                                       ROWS BETWEEN {m10 - 1} PRECEDING AND CURRENT ROW) AS ma{m10},
                avg(close_price) OVER (PARTITION BY thscode ORDER BY date_ms
                                       ROWS BETWEEN {m20 - 1} PRECEDING AND CURRENT ROW) AS ma{m20},
                lag(volume, 1) OVER w AS v1,
                lag(volume, 2) OVER w AS v2,
                row_number() OVER (PARTITION BY thscode ORDER BY date_ms) AS rn,
                {leads}
            FROM {src} WHERE close_price > 0 AND volume > 0
            WINDOW w AS (PARTITION BY thscode ORDER BY date_ms)
        ),
        -- 大盘环境：当日**全市场个股涨幅中位数**（等权宽度口径）。
        -- 用全量（不受 warmup/ST 过滤影响）计算，保证"同一交易日的环境"对所有人一致。
        mktc AS (
            SELECT date_ms, quantile_cont((close_price / prev_close - 1) * 100, 0.5) AS mchg
            FROM (SELECT thscode, date_ms, close_price,
                         lag(close_price) OVER (PARTITION BY thscode ORDER BY date_ms) AS prev_close
                  FROM {src} WHERE close_price > 0)
            WHERE prev_close > 0 GROUP BY date_ms
        )
        SELECT f.thscode, f.date_ms, f.rn, f.volume,
            f.close_price AS close,
            (f.close_price / f.prev_close - 1) * 100 AS chg,
            f.volume / nullif(f.vol_ma_prev, 0) AS vr,
            {turn_expr} AS turn,
            CASE WHEN f.volume > f.v1 AND f.v1 > f.v2 THEN 1 ELSE 0 END AS vol_step_up,
            f.ma{m5} AS ma_short, f.ma{m10} AS ma_mid, f.ma{m20} AS ma_long,
            (f.close_price / f.ma{m5} - 1) * 100 AS dev_short,
            CASE WHEN (f.close_price / f.prev_close - 1) * 100 >= {cfg_hint_limit_up()} THEN 1 ELSE 0 END
                AS at_limit,
            mc.mchg AS mchg,
            {fwd_cols}
            {cfg.extra_cols}
        FROM feat f
        {fs_join}
        LEFT JOIN mktc mc ON mc.date_ms = f.date_ms
        {cfg.extra_joins}
        WHERE f.rn > {cfg.warmup} AND f.prev_close > 0 AND f.vol_ma_prev > 0
          AND {cfg.extra_filter} {name_filter}
    """)

    hz_avg = ", ".join(f"avg(fwd{h}) AS mfwd{h}" for h in hz)
    con.execute("DROP TABLE IF EXISTS mkt_fwd")
    con.execute(f"CREATE TEMP TABLE mkt_fwd AS SELECT date_ms, {hz_avg} FROM {tbl} GROUP BY date_ms")
    hz_sel = ", ".join(f"m.mfwd{h}" for h in hz)
    con.execute("DROP VIEW IF EXISTS sigv")
    con.execute(f"CREATE TEMP VIEW sigv AS SELECT s.*, {hz_sel} FROM {tbl} s LEFT JOIN mkt_fwd m USING (date_ms)")
    return con.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]


def cfg_hint_limit_up() -> float:
    """建表时用于 `at_limit` 的阈值（与 VerifyConfig.limit_up_pct 默认值保持同源）。"""
    return LIMIT_UP_PCT


def date_lo(con: duckdb.DuckDBPyConnection, n_days: int, *, table: str = "sig") -> int:
    """最近 n_days 个交易日的起始 date_ms（n_days 很大时即全样本）。"""
    dates = [r[0] for r in con.execute(f"SELECT DISTINCT date_ms FROM {table} ORDER BY date_ms").fetchall()]
    return dates[max(0, len(dates) - n_days)] if dates else 0


# ---------------------------------------------------------------- 聚合

def _sq(text: str) -> str:
    """SQL 字符串字面量转义（单引号加倍）——分组标签来自调用方，必须转义。"""
    return str(text).replace("'", "''")


def _agg(hz: Sequence[int], cost_bps: float) -> str:
    """每个窗口输出：n（成熟） / p（未成熟） / 均值 / 中位 / 胜率 / 标准差 / 市场中性均值。

    **成熟度口径（R15，2026-09-14 修）**：前瞻收益 `fwd{h}` 在「T+h 尚未到来」时是 NULL。
    旧实现把胜率写作 `avg(CASE WHEN net > 0 THEN 1.0 ELSE 0 END)`，而
    `NULL > 0` 求值为 **NULL 并落入 ELSE 0** ⇒ 未成熟样本被**当成亏损**计入分子；
    同时分母是 `count(*)`（全部样本）而非 `count(fwd{h})`（成熟样本）。
    两个偏差叠加的后果是「两笔成熟盈利 + 一笔未成熟」报成 **66.67%** 而不是 100%，
    且 **horizon 越长胜率被系统性压得越低**（长窗口 pending 更多）——
    这不是噪声而是方向固定的偏差，会让长窗口的核验结论被一致地看衰。

    现改为「只对成熟样本求均值」：`fwd{h} IS NULL` 的行整体排除在 `avg` 之外。
    全无成熟样本时 `avg` 返回 **NULL（unknown）** 而不是 0 —— 三态纪律：
    「没到期」不等于「零胜率」。

    `p{h}` 单列未成熟计数，让「分母被谁稀释」在结果里可见（`n{h} + p{h}` = 该组总样本）。
    """
    parts = []
    for h in hz:
        net = f"(fwd{h} - {cost_bps / 100.0})"
        parts.append(
            f"count(fwd{h}) AS n{h}, count(*) - count(fwd{h}) AS p{h}, "
            f"avg({net}) AS m{h}, quantile_cont({net}, 0.5) AS med{h}, "
            f"avg(CASE WHEN fwd{h} IS NOT NULL THEN CASE WHEN {net} > 0 THEN 1.0 ELSE 0.0 END END) "
            f"AS w{h}, "
            f"stddev_samp({net}) AS s{h}, avg({net} - mfwd{h}) AS x{h}"
        )
    return ", ".join(parts)


def _agg_cols(hz: Sequence[int]) -> list[str]:
    return [
        c for h in hz
        for c in (f"n{h}", f"p{h}", f"m{h}", f"med{h}", f"w{h}", f"s{h}", f"x{h}")
    ]


def stats(con, *, where: str = "TRUE", group: str = "'__all__'", cfg: VerifyConfig = VerifyConfig(),
          horizons: Sequence[int] | None = None, labels: Sequence[str] | None = None) -> list[dict]:
    """通用分组统计。`group` 是产出标签的 SQL 表达式，`where` 是过滤条件。

    :param labels: 给定**期望出现的全部分组标签**时，用 LEFT JOIN 补齐 —— 样本量为 0 的档
        会显式输出 `n=0` 而不是从结果里消失。这是刻意的：分组静默缺失会被读成"这一档不存在"，
        而三态纪律要求「没有」必须能被看见（漏斗/单条件两处都传 labels）。
    """
    hz = tuple(horizons) if horizons else horizons_of(con, cfg.table)
    agg = _agg(hz, cfg.cost_bps)
    if not labels:
        sql = (f"SELECT {group} AS grp, count(*) AS n, {agg} "
               f"FROM {cfg.table} WHERE {where} GROUP BY 1 ORDER BY 1")
    else:
        vals = ", ".join("'" + str(l).replace("'", "''") + "'" for l in labels)
        cols = ", ".join(f"b.{c}" for c in _agg_cols(hz))
        sql = (f"WITH base AS (SELECT {group} AS grp, count(*) AS n, {agg} FROM {cfg.table} "
               f"WHERE {where} GROUP BY 1), g AS (SELECT unnest([{vals}]) AS grp) "
               f"SELECT g.grp AS grp, coalesce(b.n, 0) AS n, {cols} "
               f"FROM g LEFT JOIN base b USING (grp) ORDER BY 1")
    names = [d[0] for d in con.execute(sql).description]
    return [dict(zip(names, r)) for r in con.execute(sql).fetchall()]


def baseline(con, *, where: str = "TRUE", cfg: VerifyConfig = VerifyConfig(),
             horizons: Sequence[int] | None = None) -> dict:
    """整体基准。**不使用 GROUP BY**，因此过滤后为空时返回 `n=0` 而不是空列表
    （空切片是合法输入，不该抛 IndexError）。"""
    hz = tuple(horizons) if horizons else horizons_of(con, cfg.table)
    sql = f"SELECT count(*) AS n, {_agg(hz, cfg.cost_bps)} FROM {cfg.table} WHERE {where}"
    names = [d[0] for d in con.execute(sql).description]
    row = dict(zip(names, con.execute(sql).fetchone()))
    row["grp"] = "__base__"
    return row


def funnel(con, conds: dict[str, str], *, where: str = "TRUE", cfg: VerifyConfig = VerifyConfig(),
           horizons: Sequence[int] | None = None) -> list[dict]:
    """① 累计漏斗：输出「通过前 k 步」的样本量与收益，看逐层筛掉多少、剩下什么。"""
    cum, exprs = [], []
    for c in conds.values():
        cum.append(c)
        exprs.append(" AND ".join(f"({p})" for p in cum))
    whens = " ".join(
        f"WHEN NOT ({e}) THEN '{i + 1}_ 卡在 {k}'" for i, (k, e) in enumerate(zip(conds, exprs))
    )
    labels = [f"{i + 1}_ 卡在 {k}" for i, k in enumerate(conds)] + [f"{len(conds) + 1}_ 全部通过"]
    group = f"CASE {whens} ELSE '{labels[-1]}' END"
    return stats(con, where=where, group=group, cfg=cfg, horizons=horizons, labels=labels)


def single(con, conds: dict[str, str], *, where: str = "TRUE", cfg: VerifyConfig = VerifyConfig(),
           horizons: Sequence[int] | None = None) -> list[dict]:
    """② 单条件独立：每条条件**各自独立过滤**后的表现（判断哪一步本身有信息量）。

    R15（2026-09-14）修正：旧实现把全部条件塞进**同一个 `CASE ... WHEN`**，语义是
    「首命中分配」⇒ 各条件样本**互斥**：同时满足 A 与 B 的样本只会落进先写的那一组。
    后果有两层——
    ① 「独立贡献」实际是「扣除前序条件后的增量」，条件间的信息重叠被记成了依赖；
    ② **调换书写顺序就会改变结论**（谁写在前面谁吃掉重叠样本），结论不可复现。

    现改为逐条件**独立 WHERE** 再合并：重叠样本可**同时**进入多组，顺序无关。

    刻意**不**保留「各行 n 之和 = 总样本量」这一性质：那是互斥划分的产物，
    不是独立性的证据。改用 `zz 都不满足` 一行显式给出「一条都不命中」的样本量，
    「未覆盖的那部分」因此仍然可见。
    """
    hz = tuple(horizons) if horizons else horizons_of(con, cfg.table)
    rows: list[dict] = []
    for k, c in conds.items():
        label = f"{k} 单独"
        rows.extend(stats(con, where=f"({where}) AND ({c})", group=f"'{_sq(label)}'",
                          cfg=cfg, horizons=hz, labels=[label]))
    rest = "TRUE" if not conds else " AND ".join(f"NOT ({c})" for c in conds.values())
    rows.extend(stats(con, where=f"({where}) AND ({rest})", group="'zz 都不满足'",
                      cfg=cfg, horizons=hz, labels=["zz 都不满足"]))
    return rows


def sensitivity(con, expr: str, others: str | None = None, *, cfg: VerifyConfig = VerifyConfig(),
                horizons: Sequence[int] | None = None) -> list[dict]:
    """③ 参数敏感性：`expr` 为分档 CASE；`others` 为固定住的其他条件（基准 = 同条件的整体）。"""
    where = f"({others})" if others else "TRUE"
    return stats(con, where=where, group=expr, cfg=cfg, horizons=horizons)


def yearly(con, cond: str = "TRUE", *, cfg: VerifyConfig = VerifyConfig(),
           horizons: Sequence[int] | None = None) -> list[dict]:
    """⑤ 分年度稳定性。注意：必须再看「年度均值 > 0 的年数」，单看总均值会被个别年份带偏。"""
    return stats(con, where=f"({cond})", group="year(to_timestamp(date_ms / 1000))",
                 cfg=cfg, horizons=horizons)


def split_sample(con, cond: str, split_ms: int, *, cfg: VerifyConfig = VerifyConfig(),
                 horizons: Sequence[int] | None = None) -> dict:
    """按时间切分（样本外复验用）：返回 {train, test} 两段统计。"""
    return {
        "train": baseline(con, where=f"({cond}) AND date_ms < {split_ms}", cfg=cfg, horizons=horizons),
        "test": baseline(con, where=f"({cond}) AND date_ms >= {split_ms}", cfg=cfg, horizons=horizons),
    }


def limit_up_share(con, cond: str = "TRUE", *, cfg: VerifyConfig = VerifyConfig()) -> dict:
    """可成交性：信号日疑似涨停（买不进）的比例。"""
    sql = (f"SELECT count(*) AS n, avg(at_limit) AS share, avg(chg) AS chg_mean "
           f"FROM {cfg.table} WHERE ({cond})")
    n, share, chg = con.execute(sql).fetchone()
    return {"n": n, "limit_up_share": share, "chg_mean": chg}


def year_counts(rows: list[dict], horizon: int = 5, *, cost_bps: float = 0.0) -> tuple[int, int]:
    """分年度结果里「年度均值 > 0 的年数 / 总年数」。"""
    key = f"m{horizon}"
    vals = [r[key] for r in rows if r.get(key) is not None]
    return sum(1 for v in vals if v > 0), len(vals)


def summarize_row(r: dict, horizon: int = 5, *, cost_bps: float = 0.0) -> dict:
    """从一行统计结果里抽出**可落盘**的关键量（S2-11 核验登记）。

    核验器本身只负责算与渲染；要落盘就得有稳定字段名，否则每个消费脚本各写一遍
    `m5/med5/w5/x5` 的映射——那正是 P1-41 之前"每个脚本一份手写 SQL"的翻版。

    六个量刻意**全给**（含中位与胜率）：只看均值会被右偏分布骗（候选 B 样本外
    +1.33% 但中位 −0.08%、跑赢 49.3%，均值单独看像有效）。`excess` = 市场中性均值，
    是区分 beta 与 alpha 的唯一正确基准。

    返回键：`n` / `pending` / `mean` / `median` / `win_rate` / `std` / `excess` / `horizon` / `cost_bps`。
    `pending` = 该组中**前瞻收益尚未到期**的样本数（R15，2026-09-14）——
    `n + pending` = 该组总样本；`win_rate` 的分母就是 `n`（成熟样本），不是总数。
    """
    def _f(key: str) -> float | None:
        v = r.get(key)
        return None if v is None else round(float(v), 4)

    return {
        "n": int(r.get(f"n{horizon}") or r.get("n") or 0),
        "pending": int(r.get(f"p{horizon}") or 0),
        "mean": _f(f"m{horizon}"),
        "median": _f(f"med{horizon}"),
        "win_rate": _f(f"w{horizon}"),
        "std": _f(f"s{horizon}"),
        "excess": _f(f"x{horizon}"),
        "horizon": horizon,
        "cost_bps": cost_bps,
    }


def gate_verdict(
    metrics: dict,
    *,
    yearly_pos: int | None = None,
    yearly_tot: int | None = None,
    limit_up_share: float | None = None,
    excess_median: float | None = None,
    excess_win_rate: float | None = None,
    min_n: int = 200,
    yearly_floor: float = 0.6,
    limit_up_ceiling: float = 0.3,
) -> dict:
    """**KB-DEC-019 准入闸门的可机判部分**（S2-11）。

    此前准入五条只写在文档里，判定时靠人读数字下结论——既不可回查，也无法复核。
    这里把其中**四条能量化的**变成可执行判据；剩下两条（样本外盲测 / 与既有信号
    不重复计分）依赖人工，故本函数只给建议，**终审仍是人**。

    判定顺序（**先硬后软**，硬伤直接否决）：
      1. 样本不足 `n < min_n`      → observe（**不是 reject**：样本少 ≠ 无效）
      2. 市场中性超额 ≤ 0          → reject（连 beta 都跑不赢，无从谈 alpha）
      3. 中位 ≤ 0 或跑赢比例 < 50% → observe（收益右偏，均值被少数极端样本抬起）
      4. 年度为正比例 < 60%        → observe（效应不稳定，可能是某几年特例）
      5. 疑似涨停占比 > 30%        → observe（纸面最优档买不进，可成交性存疑）

    ⚠️ **第 3 条必须用中性口径**（`excess_median` / `excess_win_rate`），
    不能拿 `metrics` 里的 `median` / `win_rate` 顶替——那是**原始**收益的中位与跑赢比例。
    教训（2026-09-11 实测）：候选B 测试段原始中位 +3.33%、跑赢 68.1%，看着完全达标；
    换成中性口径却是 **中位 −0.08%、跑赢 49.3%**，结论从 pass 翻转为 observe。
    市场上涨时人人跑赢，**原始胜率天然 >50%，用它当判据形同虚设**——
    与「PROMO_FLOOR=30% 落在 241 日分布之外、近似恒真」是同一类错误。

    中性口径拿不到时，本函数**跳过**第 3 条并记入 `unchecked`，**绝不**用原始口径
    冒充中性给出 pass——「没验」必须能被看见，不能伪装成「验过」。

    :returns: `{"verdict", "failed", "unchecked", "note"}`，`failed` 是**全部**命中项
        （不止第一条），因为「哪一项不达标」比「达不达标」更有诊断价值。
    """
    n = int(metrics.get("n") or 0)
    excess = metrics.get("excess")
    failed: list[str] = []
    unchecked: list[str] = []

    if n < min_n:
        failed.append(f"样本不足（n={n} < {min_n}）")
    if excess is None:
        failed.append("缺少市场中性超额")
    elif excess <= 0:
        failed.append(f"市场中性超额 {excess:+.2f}% ≤ 0")

    if excess_median is not None:
        if excess_median <= 0:
            failed.append(f"中性中位 {excess_median:+.2f}% ≤ 0（收益右偏）")
    else:
        unchecked.append("中性中位未验（缺 excess_median）")
    if excess_win_rate is not None:
        if excess_win_rate < 0.5:
            failed.append(f"中性跑赢比例 {excess_win_rate * 100:.1f}% < 50%")
    else:
        unchecked.append("中性跑赢比例未验（缺 excess_win_rate）")
    if yearly_tot:
        ratio = (yearly_pos or 0) / yearly_tot
        if ratio < yearly_floor:
            failed.append(f"年度为正 {yearly_pos}/{yearly_tot} < {yearly_floor:.0%}（不稳定）")
    if limit_up_share is not None and limit_up_share > limit_up_ceiling:
        failed.append(f"疑似涨停占比 {limit_up_share * 100:.1f}% > {limit_up_ceiling:.0%}（难成交）")

    # 中性超额 ≤ 0 是唯一直接否决项（连同日市场均值都跑不赢，无从谈 alpha）；
    # 其余命中项一律 observe——"有硬伤"≠"无效"，降级而非否决。
    if any("市场中性超额" in f and "≤ 0" in f for f in failed):
        verdict = VERDICT_REJECT
    elif failed:
        verdict = VERDICT_OBSERVE
    else:
        verdict = VERDICT_PASS
    note = "；".join(failed) if failed else "已验条款全部通过"
    if unchecked:
        # 「没验」优先于「通过」——不能让跳过的条款被读成已通过
        note = f"{note}（未验：{'；'.join(unchecked)}）" if note else f"未验：{'；'.join(unchecked)}"
    elif not failed:
        note = "四条可机判条款全部通过（样本外与重复计分仍需人工终审）"
    return {"verdict": verdict, "failed": failed, "unchecked": unchecked, "note": note}


# ---------------------------------------------------------------- 输出

def _fmt_row(label: str, r: dict, base: dict | None, hz: Sequence[int]) -> str:
    out = [f"{label:<28}{r.get('n', 0):>10,}"]
    for h in hz:
        m, w, x = r.get(f"m{h}"), r.get(f"w{h}"), r.get(f"x{h}")
        if m is None or not r.get(f"n{h}"):
            out.append(f"{'—':>9}{'—':>9}{'—':>7}")
        else:
            xs = "—" if x is None else f"{x:+.2f}"
            out.append(f"{m:+8.2f}%{xs:>9}{w * 100:6.1f}%")
    return " ".join(out)


def render(rows: list[dict], horizons: Sequence[int] = DEFAULT_HORIZONS, base: dict | None = None) -> str:
    """把 `stats()` 系列结果渲染成对齐文本表。列语义：T+N 均值 / 市场中性超额 / 胜率。

    ⚠️ `horizons` 必须与建表时的窗口一致（用 `horizons_of(con)` 取），否则会渲染出空列。
    """
    head = f"{'组':<28}{'样本量':>10}"
    for h in horizons:
        head += f"{'T+' + str(h):>9}{'中性超额':>9}{'胜率':>7}"
    lines = [head, "-" * len(head)]
    if base is not None:
        lines.append(_fmt_row("基准", base, None, horizons))
    for r in rows:
        lines.append(_fmt_row(str(r.get("grp", "")), r, base, horizons))
    return "\n".join(lines)


def welch_t(a_mean: float, a_std: float, a_n: int, b_mean: float, b_std: float, b_n: int) -> float:
    """近似 t（Welch）。只作"是否值得再看"的提示，不替代严格检验。"""
    if not a_n or not b_n or a_std is None or b_std is None:
        return 0.0
    se = math.sqrt(a_std ** 2 / a_n + b_std ** 2 / b_n)
    return (a_mean - b_mean) / se if se > 0 else 0.0
