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

from app.research.strategy_trials import EVIDENCE_VERSION as TRIAL_EVIDENCE_VERSION

#: 默认 marketdb 路径（与 app/picks/rps.py 等一致：parents[2] = backend/）
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "marketdb" / "market.duckdb"

DEFAULT_HORIZONS: tuple[int, ...] = (1, 3, 5, 10)

#: 疑似涨停的涨幅代理阈值（%）。10% 涨停板/ST 5%/创业板科创板 20% 未区分，
#: 仅用于"这一档能不能买进"的量级判断，不作精确判定。
LIMIT_UP_PCT = 9.5

#: 处置结论（KB-DEC-019 三级态）。`verify_registry` 从此处导入，保持单向依赖：
#: 核验器是纯计算层，登记层依赖它，反之不成立。
VERDICT_PASS = "pass"        # 研究机器条款通过；仍不等于自动生产晋级
VERDICT_OBSERVE = "observe"  # 方向成立但有硬伤 / 协议证据不完整
VERDICT_REJECT = "reject"    # 统计反证；协议完整性另决定其能否作为当前生命周期证据

#: IMP-020 v1：KB-DEC-019 要求 0.2~0.35% 往返成本仍正；研究准入按上沿 35bps 压力。
#: 这不是撮合成本真值，只是 reference close-to-close 研究代理的保守压力参数。
ADMISSION_COST_BPS = 35.0
VALIDATION_PROTOCOL_VERSION = 2
GATE_VERSION = 4
RETURN_IDENTITY_REFERENCE_PROXY = "reference_close_to_close_proxy"


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
    #: 提供则生成 `turn`（换手率%）代理；不提供则 `turn` 为 NULL。
    #: 该 join 只补特征，**不得**用当前快照名称/当前 universe 删除历史行；
    #: 是否 ST/退市必须来自 point-in-time 身份源，否则属于未来信息倒灌。
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
    """从**单日当前快照**反推流通股本（股）的近似 SQL 片段。

    流通股本 = 流通市值(nmc，万元) × 1e4 ÷ 价格。该 helper 只提供研究近似：
    它不是 point-in-time 历史股本，不能证明历史换手率或历史 ST/退市身份。

    同日多份快照按 ``amount → received_at → price → nmc`` 确定性选一行；这只修复
    BUG-011 的并列不确定性，不把“当前快照”升级成历史真值。
    """
    pat = str(Path(snapshot_dir) / "*.parquet")
    return f"""
    (SELECT symbol || '.' || market AS thscode, name,
            nmc * 10000.0 / nullif(price, 0) AS float_shares
     FROM (
       SELECT symbol, market, name, price, nmc, amount, received_at,
              row_number() OVER (
                PARTITION BY symbol, market
                ORDER BY amount DESC NULLS LAST, received_at DESC NULLS LAST,
                         price DESC NULLS LAST, nmc DESC NULLS LAST
              ) AS rk
       FROM read_parquet('{pat}')
       WHERE price > 0 AND nmc > 0
     )
     WHERE rk = 1)
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
        # 当前快照只允许补近似股本特征，绝不能作为历史 universe/ST 身份过滤器。
        # INNER JOIN + 当前名称过滤会把“今天仍存在/今天不是 ST”倒灌到 10 年历史，
        # 形成幸存偏差；缺股本的历史行保留，turn 显式 NULL。
        fs_join = f"LEFT JOIN {cfg.float_shares_sql} fs ON fs.thscode = f.thscode"
        turn_expr = "f.volume / nullif(fs.float_shares, 0) * 100"
    else:
        fs_join, turn_expr = "", "NULL::DOUBLE"

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
          AND {cfg.extra_filter}
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


def split_windows(
    con, split_ms: int, *, cfg: VerifyConfig = VerifyConfig(),
    horizons: Sequence[int] | None = None, purge_sessions: int | None = None,
    embargo_sessions: int | None = None,
) -> dict:
    """Build one trading-session-aware purged holdout boundary.

    Signal labels use future closes, so a naïve ``date < split`` train set leaks test-period
    prices through the last ``max(horizon)`` training labels. Sessions immediately before
    the split are purged and, by default, the same number after it are embargoed.
    """
    hz = tuple(horizons) if horizons else horizons_of(con, cfg.table)
    if not hz or any(isinstance(h, bool) or not isinstance(h, int) or h <= 0 for h in hz):
        raise ValueError("horizons 必须为非空正整数序列")
    max_h = max(hz)
    split_value = _sample_count(split_ms)
    if split_value is None or split_value == 0:
        raise ValueError("split_ms 必须为正整数时间戳")

    def _count(value: int | None, default: int, label: str) -> int:
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{label} 必须是非负整数")
        return value
    purge = _count(purge_sessions, max_h, "purge_sessions")
    embargo = _count(embargo_sessions, max_h, "embargo_sessions")
    dates = [int(r[0]) for r in con.execute(
        f"SELECT DISTINCT date_ms FROM {cfg.table} ORDER BY date_ms"
    ).fetchall()]
    if not dates:
        return {
            "train_where": "FALSE", "purge_where": "FALSE", "embargo_where": "FALSE",
            "test_where": "FALSE", "split_ms": int(split_ms), "split_date_ms": None,
            "train_last_ms": None, "test_first_ms": None, "purge_sessions": purge,
            "embargo_sessions": embargo, "max_horizon": max_h, "trade_days": 0,
            "train_days": 0, "purged_days": 0, "embargo_days": 0, "test_days": 0,
        }
    split_idx = next((i for i, value in enumerate(dates) if value >= split_value), len(dates))
    if split_idx == 0 or split_idx >= len(dates):
        raise ValueError("split_ms 必须落在样本内部并同时保留 train/test")
    purge_start = max(0, split_idx - purge)
    test_start = min(len(dates), split_idx + embargo)
    if purge_start == 0 or test_start >= len(dates):
        raise ValueError("purge/embargo 使 train 或 test 为空；缩短隔离窗口或调整 split")
    train_cut = dates[purge_start] if purge_start < len(dates) else None
    test_cut = dates[test_start] if test_start < len(dates) else None
    train_where = "FALSE" if purge_start == 0 else f"date_ms < {train_cut}"
    split_date = dates[split_idx] if split_idx < len(dates) else None
    purge_where = (
        "FALSE" if split_date is None or purge_start == split_idx
        else f"date_ms >= {dates[purge_start]} AND date_ms < {split_date}"
    )
    embargo_where = (
        "FALSE" if split_date is None or test_start == split_idx
        else f"date_ms >= {split_date} AND date_ms < {dates[test_start]}"
        if test_start < len(dates) else f"date_ms >= {split_date}"
    )
    test_where = "FALSE" if test_start >= len(dates) else f"date_ms >= {test_cut}"
    return {
        "train_where": train_where, "purge_where": purge_where,
        "embargo_where": embargo_where, "test_where": test_where,
        "split_ms": split_value, "split_date_ms": split_date,
        "train_last_ms": dates[purge_start - 1] if purge_start > 0 else None,
        "test_first_ms": test_cut, "purge_sessions": purge, "embargo_sessions": embargo,
        "max_horizon": max_h, "trade_days": len(dates), "train_days": purge_start,
        "purged_days": split_idx - purge_start, "embargo_days": test_start - split_idx,
        "test_days": len(dates) - test_start,
    }


def split_sample(
    con, cond: str, split_ms: int, *, cfg: VerifyConfig = VerifyConfig(),
    horizons: Sequence[int] | None = None, purge_sessions: int | None = None,
    embargo_sessions: int | None = None,
) -> dict:
    """Purged/embargoed chronological holdout; returns metrics plus boundary evidence."""
    hz = tuple(horizons) if horizons else horizons_of(con, cfg.table)
    window = split_windows(
        con, split_ms, cfg=cfg, horizons=hz, purge_sessions=purge_sessions,
        embargo_sessions=embargo_sessions,
    )
    return {
        "train": baseline(con, where=f"({cond}) AND ({window['train_where']})", cfg=cfg, horizons=hz),
        "purged": baseline(con, where=f"({cond}) AND ({window['purge_where']})", cfg=cfg, horizons=hz),
        "embargoed": baseline(con, where=f"({cond}) AND ({window['embargo_where']})", cfg=cfg, horizons=hz),
        "test": baseline(con, where=f"({cond}) AND ({window['test_where']})", cfg=cfg, horizons=hz),
        "split": window,
    }


def validation_protocol(
    *, horizon: int, cost_bps: float, split: dict, selection_scope: str,
    universe_point_in_time: bool, feature_point_in_time: bool, trials: int,
    multiple_testing_accounted: bool, signal_overlap_checked: bool,
    multiple_testing_evidence: dict | None = None,
    signal_overlap_evidence: dict | None = None,
    return_identity: str = RETURN_IDENTITY_REFERENCE_PROXY,
) -> dict:
    """Freeze the evidence identity required for one IMP-020 research admission check.

    ``reference_close_to_close_proxy`` can support a research conclusion only.  A
    production-promotion candidate additionally needs ``shadow_fill_net`` evidence;
    both remain review-required and never auto-promote.
    """
    h = _sample_count(horizon)
    trial_count = _sample_count(trials)
    cost = _finite_number(cost_bps)
    if h is None or h == 0 or trial_count is None or trial_count == 0:
        raise ValueError("horizon/trials 必须为正整数")
    if cost is None or cost < 0:
        raise ValueError("cost_bps 必须为有限非负数")
    for label, value in (
        ("universe_point_in_time", universe_point_in_time),
        ("feature_point_in_time", feature_point_in_time),
        ("multiple_testing_accounted", multiple_testing_accounted),
        ("signal_overlap_checked", signal_overlap_checked),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"{label} 必须是 bool")
    return {
        "protocol_version": VALIDATION_PROTOCOL_VERSION,
        "split_kind": "purged_holdout",
        "horizon": h,
        "purge_sessions": split.get("purge_sessions"),
        "embargo_sessions": split.get("embargo_sessions"),
        "split_date_ms": split.get("split_date_ms"),
        "selection_scope": str(selection_scope),
        "universe_point_in_time": universe_point_in_time,
        "feature_point_in_time": feature_point_in_time,
        "trials": trial_count,
        "multiple_testing_accounted": multiple_testing_accounted,
        "multiple_testing_evidence": multiple_testing_evidence,
        "signal_overlap_checked": signal_overlap_checked,
        "signal_overlap_evidence": signal_overlap_evidence,
        "cost_bps": cost,
        "return_identity": str(return_identity),
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


def _finite_number(value: object) -> float | None:
    """Parse a measurement without accepting booleans or NaN/Infinity."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _sample_count(value: object) -> int | None:
    number = _finite_number(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def summarize_row(r: dict, horizon: int = 5, *, cost_bps: float = 0.0) -> dict:
    """JSON-safe mature measurements, retaining but identifying legacy totals.

    Zero mature samples are never replaced with the group's total. This changes
    new summaries only, not historical stored reports. Invalid input is explicit.
    """
    h = _sample_count(horizon)
    cost = _finite_number(cost_bps)
    if h is None or h == 0 or cost is None or cost < 0:
        raise ValueError("horizon需为正整数，cost_bps需为有限非负数")
    errors: list[str] = []
    mature_key, pending_key = f"n{h}", f"p{h}"
    basis = "mature" if mature_key in r else "legacy_total"
    total = _sample_count(r.get("n"))
    n = _sample_count(r.get(mature_key)) if mature_key in r else total
    if n is None:
        errors.append("成熟样本计数缺失或不是有限非负整数")
    if "n" in r and total is None:
        errors.append("总样本计数无效")
    if pending_key in r:
        pending = _sample_count(r[pending_key])
        if pending is None:
            errors.append("未成熟样本计数无效")
    elif basis == "mature" and total is not None and n is not None and n <= total:
        pending = total - n
    else:
        pending = None
    if basis == "mature" and total is not None and n is not None:
        if n > total or (pending is not None and n + pending != total):
            errors.append("成熟与未成熟样本之和不等于总样本")

    def metric(key: str, *, proportion: bool = False, nonnegative: bool = False):
        raw = r.get(key)
        value = _finite_number(raw)
        if raw is not None and (value is None or (proportion and not 0 <= value <= 1)
                                or (nonnegative and value < 0)):
            errors.append(f"{key} 非有限或超出合法范围")
            return None
        return None if value is None else round(value, 4)

    values = {
        "mean": metric(f"m{h}"), "median": metric(f"med{h}"),
        "win_rate": metric(f"w{h}", proportion=True),
        "std": metric(f"s{h}", nonnegative=True), "excess": metric(f"x{h}"),
    }
    return {
        "n": n if n is not None else 0, "pending": pending,
        **values, "horizon": h, "cost_bps": cost,
        "summary_version": 2, "sample_basis": basis, "validation_errors": errors,
    }


def _protocol_issues(metrics: dict, protocol: dict | None) -> list[str]:
    """Return admission-protocol defects; any item makes the evidence research-only."""
    if not isinstance(protocol, dict):
        return ["验证协议未登记（缺 purged holdout / 成本 / 试验分母 / PIT 身份）"]
    issues: list[str] = []
    if protocol.get("protocol_version") != VALIDATION_PROTOCOL_VERSION:
        issues.append("验证协议版本不是当前版本")
    if protocol.get("split_kind") != "purged_holdout":
        issues.append("样本外切分未声明 purged_holdout")
    h = _sample_count(protocol.get("horizon"))
    metric_h = _sample_count(metrics.get("horizon"))
    if h is None or h == 0:
        issues.append("验证 horizon 缺失或无效")
    elif metric_h is not None and metric_h != h:
        issues.append(f"验证 horizon {h} 与指标 horizon {metric_h} 不一致")
    purge = _sample_count(protocol.get("purge_sessions"))
    embargo = _sample_count(protocol.get("embargo_sessions"))
    if h is not None and h > 0:
        if purge is None or purge < h:
            issues.append(f"purge 不足（{purge!r} < horizon {h}）")
        if embargo is None or embargo < h:
            issues.append(f"embargo 不足（{embargo!r} < horizon {h}）")
    if _sample_count(protocol.get("split_date_ms")) in (None, 0):
        issues.append("样本外切分时点缺失")
    if protocol.get("selection_scope") not in {"train_only", "external_preregistered"}:
        issues.append("规则/参数选择使用了测试段或未声明预注册")
    if protocol.get("universe_point_in_time") is not True:
        issues.append("验证 universe 不是 point-in-time（存在当前身份/幸存信息倒灌）")
    if protocol.get("feature_point_in_time") is not True:
        issues.append("特征不是 point-in-time（存在当前信息倒灌历史）")
    trials = _sample_count(protocol.get("trials"))
    if trials is None or trials == 0:
        issues.append("试验全集/尝试次数未登记")
    elif trials > 1 and protocol.get("multiple_testing_accounted") is not True:
        issues.append(f"多重比较未控制（本轮登记 trials={trials}）")
    mt = protocol.get("multiple_testing_evidence")
    if protocol.get("multiple_testing_accounted") is True:
        if (not isinstance(mt, dict)
                or mt.get("evidence_version") != TRIAL_EVIDENCE_VERSION
                or mt.get("accounted") is not True
                or not isinstance(mt.get("trials"), list)):
            issues.append("多重比较仅自报为已控制，缺当前版本结构化 trial-family 证据")
        elif _sample_count(mt.get("trials_total")) != trials or len(mt["trials"]) != trials:
            issues.append("trial-family 分母与 protocol.trials 不一致")
    overlap = protocol.get("signal_overlap_evidence")
    if protocol.get("signal_overlap_checked") is not True:
        issues.append("与既有信号的重复计分/重叠未检查")
    elif (not isinstance(overlap, dict)
          or overlap.get("evidence_version") != TRIAL_EVIDENCE_VERSION
          or overlap.get("checked") is not True
          or not isinstance(overlap.get("comparisons"), list)
          or not isinstance(overlap.get("exact_duplicate"), bool)):
        issues.append("信号重叠仅自报为已检查，缺当前版本结构化 overlap 证据")
    cost = _finite_number(protocol.get("cost_bps"))
    metric_cost = _finite_number(metrics.get("cost_bps"))
    if cost is None or cost < ADMISSION_COST_BPS:
        issues.append(f"成本压力不足（需 ≥ {ADMISSION_COST_BPS:.0f}bps）")
    elif metric_cost is None or abs(metric_cost - cost) > 1e-9:
        issues.append("指标成本口径与验证协议不一致")
    if protocol.get("return_identity") != RETURN_IDENTITY_REFERENCE_PROXY:
        issues.append("收益身份未声明为 reference close-to-close proxy")
    return issues


def gate_verdict(
    metrics: dict, *, yearly_pos: int | None = None, yearly_tot: int | None = None,
    limit_up_share: float | None = None, excess_median: float | None = None,
    excess_win_rate: float | None = None, protocol: dict | None = None, min_n: int = 200,
    yearly_floor: float = 0.6, limit_up_ceiling: float = 0.3,
) -> dict:
    """Machine-check one research result; never autonomously promote production logic."""
    minimum = _sample_count(min_n)
    year_floor = _finite_number(yearly_floor)
    limit_ceiling = _finite_number(limit_up_ceiling)
    if (minimum is None or minimum == 0 or year_floor is None or not 0 <= year_floor <= 1
            or limit_ceiling is None or not 0 <= limit_ceiling <= 1):
        raise ValueError("准入配置必须为有效正样本数及[0,1]内有限阈值")

    failed: list[str] = []
    machine_unchecked: list[str] = []
    protocol_issues = _protocol_issues(metrics, protocol)
    if isinstance(protocol, dict):
        mt = protocol.get("multiple_testing_evidence")
        trials = _sample_count(protocol.get("trials"))
        if (trials is not None and trials > 1 and isinstance(mt, dict)
                and mt.get("accounted") is True
                and mt.get("selected_survives_alpha") is not True):
            failed.append("被选规则未通过多重检验校正后的显著性门")
        overlap = protocol.get("signal_overlap_evidence")
        if isinstance(overlap, dict) and overlap.get("exact_duplicate") is True:
            failed.append("与既有信号事件集合完全重复（不可重复计分）")

    if metrics.get("sample_basis") not in (None, "mature"):
        machine_unchecked.append("成熟度未验：旧总样本数不代表成熟样本")
    if metrics.get("validation_errors"):
        machine_unchecked.append(f"样本摘要校验未通过：{metrics['validation_errors']}")

    n = _sample_count(metrics.get("n"))
    if n is None:
        machine_unchecked.append("样本数未验（缺失或不是有限非负整数）")
    elif n < minimum:
        failed.append(f"样本不足（n={n} < {minimum}）")

    excess = _finite_number(metrics.get("excess"))
    if excess is None:
        machine_unchecked.append("市场中性超额未验（缺失或非有限）")
    elif excess <= 0:
        failed.append(f"市场中性超额 {excess:+.2f}% ≤ 0")

    median = _finite_number(excess_median)
    if median is None:
        machine_unchecked.append("中性中位未验（缺 excess_median 或非有限）")
    elif median <= 0:
        failed.append(f"中性中位 {median:+.2f}% ≤ 0（收益右偏）")

    win = _finite_number(excess_win_rate)
    if win is None or not 0 <= win <= 1:
        machine_unchecked.append("中性跑赢比例未验（缺 excess_win_rate、非有限或超范围）")
    elif win < 0.5:
        failed.append(f"中性跑赢比例 {win * 100:.1f}% < 50%")

    positive_years, total_years = _sample_count(yearly_pos), _sample_count(yearly_tot)
    if positive_years is None or total_years is None or total_years == 0 or positive_years > total_years:
        machine_unchecked.append("年度稳定性未验（需合法已观测年数及正收益年数）")
    elif positive_years / total_years < year_floor:
        failed.append(f"年度为正 {positive_years}/{total_years} < {year_floor:.0%}（不稳定）")

    limit_share = _finite_number(limit_up_share)
    if limit_share is None or not 0 <= limit_share <= 1:
        machine_unchecked.append("涨停可成交代理未验（缺失、非有限或超范围）")
    elif limit_share > limit_ceiling:
        failed.append(f"疑似涨停占比 {limit_share * 100:.1f}% > {limit_ceiling:.0%}（难成交）")

    if excess is not None and excess <= 0:
        machine_verdict = VERDICT_REJECT
    elif failed or machine_unchecked:
        machine_verdict = VERDICT_OBSERVE
    else:
        machine_verdict = VERDICT_PASS

    protocol_complete = not protocol_issues
    # 协议缺陷不能制造 PASS；统计上明确为负的结果仍保留 machine_reject 作为反证，
    # 但只有 protocol_complete 的结果才具备 research admission 身份。
    verdict = machine_verdict if protocol_complete or machine_verdict == VERDICT_REJECT else VERDICT_OBSERVE
    unchecked = [*machine_unchecked, *(f"协议：{item}" for item in protocol_issues)]

    note = "；".join(failed)
    if unchecked:
        note = (note + "；" if note else "") + "未验：" + "；".join(unchecked)
    elif not failed:
        note = "研究准入机器条款通过（仍需独立审阅；不得冒充真实成交净收益）"

    return_identity = (
        protocol.get("return_identity") if isinstance(protocol, dict) else RETURN_IDENTITY_REFERENCE_PROXY
    )
    machine_complete = not machine_unchecked
    research_review_eligible = (
        verdict == VERDICT_PASS and protocol_complete and machine_complete and not failed
    )
    # 本模块的统计身份恒为 reference proxy；actual shadow fill 由 IMP-053/执行证据链拥有。
    production_evidence_complete = False

    return {
        "verdict": verdict,
        "machine_verdict": machine_verdict,
        "failed": failed,
        "machine_unchecked": machine_unchecked,
        "protocol_issues": protocol_issues,
        "unchecked": unchecked,
        "note": note,
        "gate_version": GATE_VERSION,
        "scope": "research_admission_machine_checks",
        "validation_protocol": protocol,
        "protocol_complete": protocol_complete,
        "machine_checks_complete": machine_complete,
        "review_required": True,
        "return_identity": return_identity,
        "research_admission_eligible_for_review": research_review_eligible,
        "production_effect_evidence_complete": production_evidence_complete,
        # 即便 shadow-fill 证据完整，也只是“可进入人工晋级审查”，绝不自动上线。
        "production_promotion_eligible": False,
        "promotion_blocker": "始终需要独立审阅/人工晋级；reference proxy 不能冒充实际成交净收益",
    }


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
