"""marketdb（DuckDB 日K 仓）新鲜度判定——RPS / 筹码 / 数据健康哨兵共用。

## 为什么需要它（P0-7，2026-09-10 实测）

`backend/data/marketdb/market.duckdb` 停在 **2026-09-03**，距发现日 6 个交易日，
而 `settings.marketdb_sync_enabled` 默认 False ⇒ 盘后同步调度器**从未启动**。
真正的危险不是停更本身，而是**下游无陈旧检测**：`picks/rps.py` 的降级只覆盖
「仓未建 / 查询失败」两态，**没有「数据陈旧」态**，于是它照旧用 6 天前的横截面
算分位，`tech_score` 的 rps 维（权重 0.10）与 `market/chip.py` 筹码分布都
**当今日数据静默使用且无任何标注**——与「拿昨天分位描述今天」同类，
属"陈旧比缺失更危险"那一类（静默、看着正常、数字还挺像）。

## 判据（与 evolution 哨兵 sentiment_metrics 同口径）

- 用**交易日滞后数**而非自然日差：周末/长假用自然日会虚报（同 `_METRIC_HISTORY_MAX_LAG`）；
  日历本身过期时退回工作日计数（偏保守，宁可多报不可静默）；
- 用**内容日期**（库内 `MAX(date_ms)`）而非**文件 mtime**：一次失败的 `--full`
  重跑会刷新 mtime 而数据仍停在旧日期——mtime 只证明「文件被写过」，
  不证明「数据到了新交易日」。哨兵原按 mtime 判定，这条是本次一并收紧的洞；
- 稳态滞后 = 1（盘后同步前，库尾仍是上一交易日属正常），阈值 3 留调度周期余量；
- 历史截面（显式 `asof` 早于库内最新）恒判新鲜——回测/复盘要的就是那一刻的截面。

## 三态纪律

仓不存在 / 内容日期不可读 / 陈旧 → `available` 与 `stale` 显式区分，`reason`
一律中文且带**可执行处置**；消费方据此降级，绝不把陈旧截面当今日数据用。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# app/market/marketdb_freshness.py → parents[2] = backend/
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "marketdb" / "market.duckdb"

#: 允许的滞后交易日数。稳态 1（盘后同步前的正常态），给到 3 是给周末/长假与
#: 调度周期留余量——口径与 evolution 的 _METRIC_HISTORY_MAX_LAG 一致。
MAX_STALE_TRADE_DAYS = 3

from app.core.bjtime import BJ_TZ, beijing_now  # S2-8 时区收敛


def _to_date(ms: int | float | None) -> date | None:
    """上海零点毫秒 → date；非正/越界/不可解析 → None（不猜）。"""
    if ms is None:
        return None
    try:
        ts = float(ms)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts / 1000.0, tz=BJ_TZ).date()
    except (OverflowError, OSError, ValueError):
        return None


def latest_content_date(db_path: str | Path | None = None) -> date | None:
    """仓内日K 的**内容日期** = `MAX(date_ms)`（优先复权表 daily_k_adj）。

    表缺失/为空 → 回落下一张表；两表都读不出 → None（消费方显式降级）。
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    try:
        import duckdb

        with duckdb.connect(str(path), read_only=True) as con:
            for table in ("daily_k_adj", "daily_k"):
                try:
                    row = con.execute(f"SELECT MAX(date_ms) FROM {table}").fetchone()
                except Exception:  # noqa: BLE001  表缺失/为空属正常回落路径
                    continue
                d = _to_date(row[0] if row else None)
                if d is not None:
                    return d
    except Exception as exc:  # noqa: BLE001
        log.warning("marketdb 内容日期读取失败（%s）：%s", path, exc)
        return None
    return None


def trading_day_lag(latest: date, asof: date) -> int:
    """计数「> latest 且 <= asof」的交易日数。asof <= latest 恒 0。

    日历（持久化文件）**覆盖到 asof** 时按真实交易日计数（含长假）；否则退化为
    **工作日计数**（跳过周末）——因为一份过期日历会把滞后**算少**（低估陈旧），
    而工作日计数最多算多（节假日场景偏保守）。不因日历缺失/过期而放行：
    宁可多报，不可静默。
    """
    if asof <= latest:
        return 0
    from app.market import trade_calendar as tc

    days = tc._load_persisted()
    if days and days[-1] >= asof:
        return sum(1 for d in days if latest < d <= asof)
    n, cur = 0, latest
    while cur < asof:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def freshness(
    db_path: str | Path | None = None,
    *,
    asof: date | None = None,
    max_stale_days: int = MAX_STALE_TRADE_DAYS,
) -> dict:
    """仓新鲜度判定。

    :param asof: 判定基准日；None = 今天（上海）。历史截面传当时的交易日，
                 此时库内更新的数据不算陈旧（回测/复盘要的就是那一刻的截面）。
    :returns: {db, available, latest, asof, lag, threshold, stale, reason}
              `reason` 仅在不可用/陈旧时非空，中文且带可执行处置。
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    base = asof or beijing_now().date()
    out: dict = {
        "db": str(path), "available": False, "latest": None, "asof": base.isoformat(),
        "lag": None, "threshold": max_stale_days, "stale": False, "reason": "",
    }
    if not path.exists():
        out["reason"] = "marketdb 不存在（先跑 scripts/sync_marketdb.py --full）"
        return out
    latest = latest_content_date(path)
    if latest is None:
        out["reason"] = f"marketdb 内容日期不可读（{path.name} 空表或损坏）"
        return out
    out["available"] = True
    out["latest"] = latest.isoformat()
    lag = trading_day_lag(latest, base)
    out["lag"] = lag
    out["stale"] = lag > max_stale_days
    if out["stale"]:
        out["reason"] = (
            f"marketdb 数据陈旧：库内最新 {latest.isoformat()}，"
            f"滞后 {lag} 个交易日（阈值 {max_stale_days}）——先跑 scripts/sync_marketdb.py"
        )
    return out
