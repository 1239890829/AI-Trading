"""marketdb：ths 官方全市场 dump → 本地 DuckDB 日 K 仓（RPS / 横截面地基）。

数据源：`/api/dump/market-dumps`（3 端点，Parquet 直下，见
skills/hithink-finance/docs/api/endpoints-market-dumps.md）：
- daily-k           全市场 10 年日K（未复权，~945 万行，实测 2-3 分钟）——首次全量
- daily-k-10d       全市场最近 10 交易日——日常增量
- adjustment-factors 全市场复权事件（分红/送股/配股，~5.2 万行）——因子推算

本地仓：backend/data/marketdb/market.duckdb（backend/data/ 不入 git）
- daily_k      (thscode, date_ms, OHLCV, turnover) 原始未复权
- adjust_factor (thscode, ex_date_ms, 分红/送转/配股)
- daily_k_adj  (thscode, date_ms, close_adj) 复权收盘（物化，每次 sync 后重建）

复权口径（重要）：close_adj = close × ∏(ratio of **未来**事件)——这是**前复权**
（qfq）序列，历史价随新除权事件变化，但对 **N 日涨幅与后复权完全等价**
（只差每股常数因子，比值时约掉）。RPS / 横截面收益只消费涨幅，不受影响。
ratio_e = (C_prev - 分红 + 配股价×配股比) / (C_prev × (1 + 送转比 + 配股比))
为「除权日价格断崖系数」（10送10 → 0.5）。

用法（cwd 必须是 backend/，Settings 的 env_file 是相对路径）：
    .venv/bin/python scripts/sync_marketdb.py --full      # 首次全量
    .venv/bin/python scripts/sync_marketdb.py             # 日常增量（近 10 交易日）
    .venv/bin/python scripts/sync_marketdb.py --factors   # 复权因子（全量，量小）

避错要点（来自官方文档）：
- 预签名 URL 有效期 ~5 分钟，拿到立刻下载，绝不缓存 URL
- 增量数据落后 >7 个交易日时 daily-k-10d 盖不住缺口，改用 --full
- 增量入库按 (thscode, date_ms) 去重：本脚本按「重叠交易日整段删除重插」

质量/新鲜度校验（2026-09-07 移植自上游官方 `python/marketdb` SDK，MIT）：
- 8 条 SQL 质量检查（行数/主键唯一/high≥low/OHLC 非负/复权事件主键），error 级失败 → 同步报错退出
- freshness：增量同步前按交易日历算滞后天数，>7（与 daily-k-10d 覆盖窗口一致）拒绝增量，提示 --full
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402
import httpx  # noqa: E402

from app.core.config import Settings  # noqa: E402

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "marketdb" / "market.duckdb"

_DAILY_COLS = ("thscode", "date_ms", "open_price", "high_price", "low_price",
               "close_price", "volume", "turnover")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_k (
    thscode VARCHAR, date_ms BIGINT,
    open_price DOUBLE, high_price DOUBLE, low_price DOUBLE, close_price DOUBLE,
    volume DOUBLE, turnover DOUBLE
);
CREATE TABLE IF NOT EXISTS adjust_factor (
    thscode VARCHAR, ex_date_ms BIGINT,
    dividend_per_share DOUBLE, per_share_bonus DOUBLE,
    allotment_ratio DOUBLE, allotment_price DOUBLE
);
CREATE TABLE IF NOT EXISTS daily_k_adj (
    thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE
);
"""


def _get_download_url(client: httpx.Client, base: str, api_key: str, dump_type: str) -> str:
    """签出预签名 URL（有效期 ~5 分钟，调用方必须立刻下载）。"""
    resp = client.get(
        f"{base}/api/dump/market-dumps/{dump_type}/download-url",
        headers={"X-api-key": api_key},
        timeout=30.0,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"market-dumps {dump_type} 签名失败：code={payload.get('code')} "
                           f"message={payload.get('message')}")
    url = (payload.get("data") or {}).get("presigned_url")
    if not url:
        raise RuntimeError(f"market-dumps {dump_type} 响应缺少 presigned_url：{payload.get('data')}")
    return url


def _download_parquet(client: httpx.Client, url: str, dest: Path) -> None:
    """流式下载 Parquet 到 dest。"""
    with client.stream("GET", url, timeout=300.0) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                fh.write(chunk)


def _sync_daily(con: duckdb.DuckDBPyConnection, client: httpx.Client,
                base: str, api_key: str, full: bool) -> dict:
    """全量（重建表）或增量（重叠交易日整段删除重插）。"""
    dump_type = "daily-k" if full else "daily-k-10d"
    url = _get_download_url(client, base, api_key, dump_type)
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        t0 = time.monotonic()
        _download_parquet(client, url, tmp_path)
        dl_s = time.monotonic() - t0

        rows_in = con.execute(
            "SELECT COUNT(*) FROM read_parquet(?)", [str(tmp_path)]
        ).fetchone()[0]
        if full:
            con.execute("DROP TABLE IF EXISTS daily_k")
            con.execute("CREATE TABLE daily_k (" + ", ".join(
                f"{c} {'VARCHAR' if c == 'thscode' else 'BIGINT' if c == 'date_ms' else 'DOUBLE'}"
                for c in _DAILY_COLS) + ")")
            con.execute(
                f"INSERT INTO daily_k SELECT {', '.join(_DAILY_COLS)} "
                "FROM read_parquet(?) ORDER BY thscode, date_ms", [str(tmp_path)]
            )
            replaced = 0
        else:
            # 去重口径：重叠交易日整段删除重插（同日数据以增量文件为准）。
            # 官方建议按 (thscode, date_ms) UPSERT；按 date_ms 整段删等价且免主键索引。
            dates = con.execute(
                "SELECT DISTINCT date_ms FROM read_parquet(?)", [str(tmp_path)]
            ).fetchall()
            date_list = [d[0] for d in dates]
            replaced = con.execute(
                "SELECT COUNT(*) FROM daily_k WHERE date_ms IN (SELECT unnest(?))",
                [date_list],
            ).fetchone()[0]
            con.execute("DELETE FROM daily_k WHERE date_ms IN (SELECT unnest(?))", [date_list])
            con.execute(
                f"INSERT INTO daily_k SELECT {', '.join(_DAILY_COLS)} "
                "FROM read_parquet(?) ORDER BY thscode, date_ms", [str(tmp_path)]
            )
    finally:
        tmp_path.unlink(missing_ok=True)

    total, sym_n, dmin, dmax = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT thscode), MIN(date_ms), MAX(date_ms) FROM daily_k"
    ).fetchone()
    return {
        "dump_type": dump_type, "rows_in": rows_in, "replaced": replaced,
        "total": total, "symbols": sym_n, "dmin": dmin, "dmax": dmax, "download_s": round(dl_s, 1),
    }


def _sync_factors(con: duckdb.DuckDBPyConnection, client: httpx.Client,
                  base: str, api_key: str) -> dict:
    """复权因子全量替换（事件表 ~5.2 万行，量小）。"""
    url = _get_download_url(client, base, api_key, "adjustment-factors")
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        _download_parquet(client, url, tmp_path)
        rows_in = con.execute(
            "SELECT COUNT(*) FROM read_parquet(?)", [str(tmp_path)]
        ).fetchone()[0]
        con.execute("DROP TABLE IF EXISTS adjust_factor")
        con.execute("""
            CREATE TABLE adjust_factor (
                thscode VARCHAR, ex_date_ms BIGINT,
                dividend_per_share DOUBLE, per_share_bonus DOUBLE,
                allotment_ratio DOUBLE, allotment_price DOUBLE
            )
        """)
        con.execute("""
            INSERT INTO adjust_factor
            SELECT DISTINCT thscode, ex_date_ms, dividend_per_share, per_share_bonus,
                   allotment_ratio, allotment_price
            FROM read_parquet(?) ORDER BY thscode, ex_date_ms
        """, [str(tmp_path)])
    finally:
        tmp_path.unlink(missing_ok=True)
    total = con.execute("SELECT COUNT(*), COUNT(DISTINCT thscode) FROM adjust_factor").fetchone()
    return {"rows_in": rows_in, "events": total[0], "symbols": total[1]}


def rebuild_adj(con: duckdb.DuckDBPyConnection) -> dict:
    """重建 daily_k_adj 物化表（前复权收盘，口径见模块 docstring）。

    两次 ASOF JOIN（DuckDB 原生，均命中「最近一条」语义）：
    1. 事件 × 前一交易日收盘 → 每事件断崖系数 ratio
    2. K 线 × 「日期之后首个事件」的累计系数 → close_adj
    """
    t0 = time.monotonic()
    con.execute("""
        DELETE FROM daily_k_adj;
        INSERT INTO daily_k_adj
        WITH picked AS (
            SELECT f.thscode, f.ex_date_ms,
                   COALESCE(f.dividend_per_share, 0) AS d,
                   COALESCE(f.per_share_bonus, 0) AS b,
                   COALESCE(f.allotment_ratio, 0) AS r,
                   COALESCE(f.allotment_price, 0) AS p,
                   k.close_price AS c_prev
            FROM adjust_factor f
            ASOF JOIN daily_k k
              ON f.thscode = k.thscode AND k.date_ms < f.ex_date_ms
        ),
        ratio AS (
            SELECT thscode, ex_date_ms,
                   CASE WHEN c_prev > 0
                        THEN (c_prev - d + r * p) / (c_prev * (1 + b + r))
                        ELSE 1.0 END AS ratio
            FROM picked
        ),
        cum AS (
            -- cum(e) = ∏(e 及其未来所有事件的 ratio)：从 e 起到序列末尾的窗口乘积
            SELECT thscode, ex_date_ms,
                   exp(sum(ln(ratio)) OVER (
                       PARTITION BY thscode ORDER BY ex_date_ms ASC
                       ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING
                   )) AS cum_ratio
            FROM ratio
            WHERE ratio > 0
        )
        SELECT k.thscode, k.date_ms,
               k.close_price * COALESCE(c.cum_ratio, 1.0) AS close_adj
        FROM daily_k k
        ASOF LEFT JOIN cum c
          ON k.thscode = c.thscode AND k.date_ms < c.ex_date_ms
    """)
    total = con.execute("SELECT COUNT(*) FROM daily_k_adj").fetchone()[0]
    return {"rows": total, "rebuild_s": round(time.monotonic() - t0, 1)}


# ---- 质量/新鲜度校验（移植自上游官方 marketdb SDK，MIT；适配本仓 date_ms schema）----

#: (check 名, severity, SQL)。SQL 返回行 = 违规；error 级违规 → 同步失败退出。
_QUALITY_CHECKS: list[tuple[str, str, str]] = [
    ("daily_k.rowcount_positive", "error",
     "SELECT 'daily_k empty' FROM (SELECT 1) WHERE NOT EXISTS (SELECT 1 FROM daily_k LIMIT 1)"),
    ("daily_k.thscode_not_null", "error",
     "SELECT 'rows missing thscode', COUNT(*) FROM daily_k WHERE thscode IS NULL HAVING COUNT(*) > 0"),
    ("daily_k.date_not_null", "error",
     "SELECT 'rows missing date_ms', COUNT(*) FROM daily_k WHERE date_ms IS NULL HAVING COUNT(*) > 0"),
    ("daily_k.pk_unique", "error",
     "SELECT thscode, date_ms, COUNT(*) FROM daily_k "
     "GROUP BY thscode, date_ms HAVING COUNT(*) > 1 LIMIT 10"),
    ("daily_k.high_ge_low", "error",
     "SELECT thscode, date_ms, high_price, low_price FROM daily_k "
     "WHERE high_price < low_price LIMIT 10"),
    ("daily_k.ohlc_non_negative", "error",
     "SELECT thscode, date_ms FROM daily_k WHERE open_price < 0 OR high_price < 0 "
     "OR low_price < 0 OR close_price < 0 LIMIT 10"),
    ("daily_k.volume_non_negative", "warn",
     "SELECT thscode, date_ms FROM daily_k WHERE volume < 0 OR turnover < 0 LIMIT 10"),
    # 自然键 = 全部 6 列。同一除权日**可以有多个合法事件**（同日「派现」+「送股」，
    # 或两次独立权益分派），只按 (thscode, ex_date_ms) 分组会把它们误判成重复。
    # 2026-09-11 实测（P0-7）：全库 3 组同日多事件中 2 组是合法多事件
    # （000812.SZ 1998-09-22 = 派现 0.2 + 送股 0.1；603883.SH 2024-06-27 = 两笔不同派送），
    # 只有 000601.SZ 1997-11-03 是真重复。旧判据每次同步都误报 error → 同步恒判 fail。
    ("adjust_factor.pk_unique", "error",
     "SELECT thscode, ex_date_ms, COUNT(*) FROM adjust_factor "
     "GROUP BY thscode, ex_date_ms, dividend_per_share, per_share_bonus, "
     "allotment_ratio, allotment_price HAVING COUNT(*) > 1 LIMIT 10"),
]


def run_quality_checks(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """跑全部质量检查。返回违规列表（空 = 通过）；不抛异常（三态：结果显式呈现）。"""
    issues: list[dict] = []
    for name, severity, sql in _QUALITY_CHECKS:
        try:
            rows = con.execute(sql).fetchall()
        except Exception as exc:  # noqa: BLE001 —— 检查自身失败也是显式问题
            issues.append({"check": name, "severity": "error",
                           "detail": f"check failed: {exc}", "sample": []})
            continue
        if rows:
            issues.append({"check": name, "severity": severity,
                           "detail": str(rows[0][0]) if isinstance(rows[0][0], str) else "violations found",
                           "sample": [list(r) for r in rows[:10]]})
    return issues


def freshness_lag_days(con: duckdb.DuckDBPyConnection, trade_days_ms: list[int]) -> int:
    """本地最新交易日落后目标日历多少个交易日（目标=日历最后一天）。

    :param trade_days_ms: 升序交易日 date_ms 列表（调用方由 trade_calendar 转换）
    :return: 滞后交易日数；空表返回 len(trade_days_ms)（全缺）
    """
    row = con.execute("SELECT MAX(date_ms) FROM daily_k").fetchone()
    local_max = row[0] if row and row[0] else None
    if local_max is None:
        return len(trade_days_ms)
    target = trade_days_ms[-1] if trade_days_ms else local_max
    return sum(1 for d in trade_days_ms if local_max < d <= target)


def _calendar_days_ms(limit: int = 400) -> list[int]:
    """交易日历 → 升序 date_ms 列表（UTC+8 零点，与 dump 口径一致）。失败返回 []（跳过检查）。

    🔴 2026-09-11 修复（原为**死代码**）：此处原本调 `trading_days()`，但该函数签名是
    `async def trading_days(provider, lookback_days=120)`——**缺 provider 且未 await**，
    必然抛 TypeError → 被 except 吞掉 → 每次同步都打印「交易日历不可用」并**静默跳过
    滞后门槛**，即「滞后 >7 交易日则拒绝增量、强制 --full」这道保护**从未真正生效过**。

    CLI 既无事件循环也无 provider，故改用**持久化日历**（`trade_calendar._load_persisted()`，
    与 `app/market/marketdb_freshness.trading_day_lag` 同源，口径单点收口）；日历落后于
    今天时用**工作日**补足到今天——节假日场景偏保守，宁可多报不可静默，同 freshness 模块判据。
    """
    from datetime import datetime, timedelta, timezone

    from app.market import trade_calendar as tc

    try:
        days = list(tc._load_persisted() or [])
        if not days:
            print("warn: 持久化交易日历不可用/过短，跳过 freshness 检查", file=sys.stderr)
            return []
        tz8 = timezone(timedelta(hours=8))
        today = datetime.now(tz8).date()
        cur = days[-1]
        while cur < today:  # 日历过期 → 工作日补足（宁可多报滞后）
            cur += timedelta(days=1)
            if cur.weekday() < 5:
                days.append(cur)
        out = [int(datetime(d.year, d.month, d.day, tzinfo=tz8).timestamp() * 1000) for d in days]
        return sorted(out)[-limit:]
    except Exception as exc:  # noqa: BLE001 —— 日历不可用不阻塞同步，仅跳过新鲜度检查
        print(f"warn: 交易日历不可用，跳过 freshness 检查：{exc}", file=sys.stderr)
        return []


def main() -> int:
    t0 = time.monotonic()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--full", action="store_true", help="全量拉 10 年日K（首次/缺口>7 交易日）")
    ap.add_argument("--factors", action="store_true", help="同步复权因子（全量，量小）")
    ap.add_argument("--no-adj", action="store_true", help="跳过重建 daily_k_adj")
    args = ap.parse_args()

    settings = Settings()
    if not settings.ths_api_key:
        print("缺少 ASHARE_THS_API_KEY（.env），退出", file=sys.stderr)
        return 2
    if not args.full and not args.factors:
        # 默认行为：日常增量（日K 近 10 交易日）；因子只在显式 --factors 时同步
        args.full = False
        do_daily = True
    else:
        do_daily = args.full
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(DB_PATH))
    con.execute(_SCHEMA)
    report: dict = {}

    # 增量前置新鲜度门槛（官方语义：滞后超覆盖窗口拒绝增量，强制重拉全量）
    if do_daily and not args.full:
        days_ms = _calendar_days_ms()
        if days_ms:
            lag = freshness_lag_days(con, days_ms)
            report["freshness_lag_days"] = lag
            if lag > 7:
                print(f"拒绝增量：本地数据滞后 {lag} 个交易日（>7，daily-k-10d 盖不住缺口）。"
                      f"请改用 --full 重拉全量。", file=sys.stderr)
                con.close()
                return 3

    with httpx.Client(trust_env=False, follow_redirects=True) as client:
        try:
            if do_daily:
                report["daily"] = _sync_daily(con, client, settings.ths_base_url,
                                              settings.ths_api_key, full=args.full)
            if args.factors:
                report["factors"] = _sync_factors(con, client, settings.ths_base_url,
                                                  settings.ths_api_key)
        except Exception as exc:  # noqa: BLE001
            print(f"同步失败：{exc}", file=sys.stderr)
            con.close()
            return 1
        if not args.no_adj:
            report["adj"] = rebuild_adj(con)

    # 收尾质量检查：error 级违规 → 同步判定失败（数据不干净 ≠ 同步成功）
    issues = run_quality_checks(con)
    # 近窗业务校验（P1 方向4 数据质量门）：近窗空值率 / 复权单日涨跌超限 / 零收盘
    from app.market.marketdb_quality import append_sync_history, run_recent_quality_checks, write_quality_report

    issues += run_recent_quality_checks(con)
    con.close()

    # 报告与时长趋势落盘（进程外可见性：/api/system/marketdb-quality、/api/system/metrics）
    report["total_s"] = round(time.monotonic() - t0, 1)
    body = write_quality_report(DB_PATH, report, issues)
    append_sync_history(DB_PATH, {
        "ts": body["synced_at"], "full": bool(args.full), "factors": bool(args.factors),
        "total_s": report["total_s"], "status": body["status"],
    })

    if issues:
        errs = [i for i in issues if i["severity"] == "error"]
        for i in issues:
            print(f"[quality:{i['severity']}] {i['check']} — {i['detail']} sample={i['sample'][:3]}",
                  file=sys.stderr)
        if errs:
            print(f"质量检查 {len(errs)} 项 error 级违规，同步判定为失败。", file=sys.stderr)
            return 4

    for k, v in report.items():
        print(f"[{k}] {v}")
    print(f"db={DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
