"""P1-5 lurk_pool 单测：潜伏+试盘+回踩确认判据（mock K 线）+ 陈旧披露（as_of/stale_days）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import pytest

from app.market.marketdb_freshness import MAX_STALE_TRADE_DAYS
from app.picks.lurk_pool import LOOKBACK_DAYS, _limit_pct, _scan, scan_lurk_pool
from app.core.bjtime import BJ_TZ  # S2-8 时区收敛


_BASE_MS = 1700000000000


def _bar(i, o, h, l, c, v, t0=_BASE_MS):
    # bars 元组: (code, date_ms, open, high, low, close, volume)
    return ("600000.SH", t0 + i * 86400000, o, h, l, c, v)


def _flat_bar(i, base=10.0, v=1_000_000, t0=_BASE_MS):
    """窄幅横盘（低量平台）。"""
    return _bar(i, base - 0.05, base + 0.06, base - 0.08, base + 0.03, v, t0)


def test_limit_pct_by_board():
    assert _limit_pct("600000.SH") == 0.10
    assert _limit_pct("300001.SZ") == 0.20
    assert _limit_pct("688001.SH") == 0.20
    assert _limit_pct("830001.BJ") == 0.30


def test_scan_hit_lurk_then_probe_pullback():
    bars = []
    # 45 根低量横盘（潜伏）
    for i in range(45):
        bars.append(_flat_bar(i, base=10.0, v=500_000))
    # 试盘日：放量 +6% 长上影（open 10.0 → close 10.6, high 11.1 上影）
    bars.append(_bar(45, 10.0, 11.1, 10.05, 10.60, 2_000_000))
    # 回踩：次日缩量回调但不破试盘低点/平台（low floor≈10.03）
    bars.append(_bar(46, 10.35, 10.38, 10.06, 10.10, 600_000))
    hit = _scan(bars)
    assert hit is not None and hit["confirm_ms"] == bars[46][1]


def test_scan_no_hit_when_active_uptrend():
    # 持续放量上涨的活跃票（无潜伏横盘）→ 不命中
    bars = [_bar(i, 10.0 + i * 0.2, 10.0 + i * 0.2 + 0.5, 10.0 + i * 0.2 - 0.2,
                 10.0 + i * 0.2 + 0.3, 3_000_000) for i in range(50)]
    assert _scan(bars) is None


# ---------------------------------------------------------------------------
# 陈旧披露（2026-09-11）：as_of / stale_days / stale / stale_note
# ---------------------------------------------------------------------------


@pytest.fixture()
def lurk_db(tmp_path):
    """最小 daily_k 仓（含一只「潜伏+试盘+回踩」命中票）→ (db_path, 数据日)。

    K 线日期锚定在**今天往前 60 天**：`stale_days` 走的是真实交易日历，
    数据日必须是近期，`asof` 才落在日历覆盖区间内（否则退化为工作日计数，
    测不到生产路径）。
    """
    
    today0 = datetime.now(BJ_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    t0 = int(today0.timestamp() * 1000) - 60 * 86400000
    bars = [_flat_bar(i, base=10.0, v=500_000, t0=t0) for i in range(45)]
    bars.append(_bar(45, 10.0, 11.1, 10.05, 10.60, 2_000_000, t0))
    bars.append(_bar(46, 10.35, 10.38, 10.06, 10.10, 600_000, t0))
    path = tmp_path / "m.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE, "
        "high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume DOUBLE, "
        "turnover DOUBLE)"
    )
    con.executemany(
        "INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(c, t, o, h, low, cl, v, v * 10.0) for c, t, o, h, low, cl, v in bars],
    )
    con.close()
    data_date = datetime.fromtimestamp(bars[-1][1] / 1000, tz=BJ_TZ).date()
    return path, data_date


def test_scan_lurk_pool_fresh_disclosure(lurk_db):
    """新鲜时 as_of = 库内最新 K 线日，stale_days=0，无 note。"""
    path, data_date = lurk_db
    res = scan_lurk_pool(path, asof=data_date)
    assert res["as_of"] == data_date.isoformat()
    assert res["trade_date"] == data_date.isoformat()
    assert res["stale_days"] == 0
    assert res["stale"] is False
    assert res["stale_note"] == ""
    assert [it["symbol"] for it in res["items"]] == ["600000"]


def test_scan_lurk_pool_stale_discloses_but_keeps_items(lurk_db):
    """停更时**不丢弃结果**（可用但可见）：池照出，只把口径与处置摊开。"""
    path, data_date = lurk_db
    res = scan_lurk_pool(path, asof=data_date + timedelta(days=30))
    assert res["as_of"] == data_date.isoformat()  # as_of 仍是数据日，不伪装成今天
    assert res["stale_days"] > MAX_STALE_TRADE_DAYS
    assert res["stale"] is True
    assert data_date.isoformat() in res["stale_note"]
    assert "sync_marketdb.py" in res["stale_note"]
    assert res["items"], "停更不是丢弃理由——只标注，不隐藏"


def test_scan_lurk_pool_missing_db_degrades_explicitly(tmp_path):
    """仓未建（CI/新机）≠ 停更：显式不可用态，绝不 500、绝不冒充「今日无候选」。

    CI 实测（2026-09-12）：无 marketdb 文件时 duckdb.read_only 直接 IOException → 500。
    """
    missing = tmp_path / "market.duckdb"
    res = scan_lurk_pool(missing)
    assert res["items"] == []
    assert res["trade_date"] is None and res["as_of"] is None
    assert res["stale"] is True
    assert "marketdb 仓不存在" in res["stale_note"]
    assert "sync_marketdb.py" in res["stale_note"]

# ---------------------------------------------------------------------------
# BUG-028：未来数据污染 / point-in-time 截断
# ---------------------------------------------------------------------------


def _valid_hit_bars(*, n: int = 42, probe_i: int = 40, t0: int = _BASE_MS):
    """构造满足 40 根量能历史 + 试盘 + 次日确认的最小序列。"""
    assert n >= probe_i + 2
    bars = [_flat_bar(i, base=10.0, v=100.0, t0=t0) for i in range(n)]
    bars[probe_i] = _bar(probe_i, 10.0, 11.1, 10.05, 10.60, 200.0, t0)
    bars[probe_i + 1] = _bar(probe_i + 1, 10.35, 10.38, 10.06, 10.10, 50.0, t0)
    return bars


def test_scan_short_history_cannot_borrow_future_via_negative_index():
    bars = [_flat_bar(i, base=10.0, v=100.0) for i in range(27)]
    bars[25] = _bar(25, 10.0, 11.1, 10.05, 10.60, 200.0)
    bars[26] = _bar(26, 10.35, 10.38, 10.06, 10.10, 50.0)
    assert _scan(bars) is None


def test_scan_future_tail_cannot_backfill_early_probe():
    prefix = [_flat_bar(i, base=10.0, v=100.0) for i in range(45)]
    prefix[25] = _bar(25, 10.0, 11.1, 10.05, 10.60, 200.0)
    prefix[26] = _bar(26, 10.60, 10.70, 10.50, 10.65, 100.0)
    prefix[27] = _bar(27, 10.35, 10.38, 10.06, 10.10, 50.0)
    tail_high = [_flat_bar(i, base=10.0, v=100.0) for i in range(45, 60)]
    tail_low = [_flat_bar(i, base=10.0, v=1.0) for i in range(45, 60)]
    assert _scan(prefix + tail_high) is None
    assert _scan(prefix + tail_low) is None


def test_scan_returns_latest_confirmation_not_first():
    bars = _valid_hit_bars(n=77, probe_i=40)
    bars[75] = _bar(75, 10.0, 11.1, 10.05, 10.60, 200.0)
    bars[76] = _bar(76, 10.35, 10.38, 10.06, 10.10, 50.0)
    hit = _scan(bars)
    assert hit is not None
    assert hit["confirm_ms"] == bars[76][1]
    assert hit["probe_ms"] == bars[75][1]


def test_scan_duplicate_dates_fail_closed():
    bars = _valid_hit_bars()
    bars[10] = (bars[10][0], bars[9][1], *bars[10][2:])
    assert _scan(bars) is None


def test_scan_nan_fail_closed():
    bars = _valid_hit_bars()
    bars[20] = (
        bars[20][0], bars[20][1], bars[20][2], bars[20][3],
        bars[20][4], bars[20][5], float("nan"),
    )
    assert _scan(bars) is None


def _make_daily_db(path, bars, *, with_adj: bool = False):
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE, "
        "high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume DOUBLE, "
        "turnover DOUBLE)"
    )
    con.executemany(
        "INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(c, t, o, h, low, cl, v, v * 10.0) for c, t, o, h, low, cl, v in bars],
    )
    if with_adj:
        con.execute(
            "CREATE TABLE daily_k_adj "
            "(thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)"
        )
        con.executemany(
            "INSERT INTO daily_k_adj VALUES (?, ?, ?)",
            [(b[0], b[1], 9999.0 - i) for i, b in enumerate(bars)],
        )
    con.close()


def test_scan_lurk_pool_asof_is_real_sql_upper_bound(tmp_path):
    t0 = int(datetime(2026, 6, 1, tzinfo=BJ_TZ).timestamp() * 1000)
    bars = _valid_hit_bars(n=55, probe_i=40, t0=t0)
    path = tmp_path / "asof.duckdb"
    _make_daily_db(path, bars)
    asof = datetime.fromtimestamp(bars[41][1] / 1000, tz=BJ_TZ).date()

    res = scan_lurk_pool(path, asof=asof)
    assert res["trade_date"] == asof.isoformat()
    assert res["as_of"] == asof.isoformat()
    assert res["requested_asof"] == asof.isoformat()
    assert [it["symbol"] for it in res["items"]] == ["600000"]
    assert res["items"][0]["confirm_ms"] == bars[41][1]


def test_scan_lurk_pool_historical_asof_ignores_future_row_mutation(tmp_path):
    t0 = int(datetime(2026, 6, 1, tzinfo=BJ_TZ).timestamp() * 1000)
    bars = _valid_hit_bars(n=55, probe_i=40, t0=t0)
    path = tmp_path / "future-mutation.duckdb"
    _make_daily_db(path, bars)
    asof = datetime.fromtimestamp(bars[41][1] / 1000, tz=BJ_TZ).date()

    before = scan_lurk_pool(path, asof=asof)
    con = duckdb.connect(str(path))
    con.execute(
        "UPDATE daily_k SET open_price = 99, high_price = 120, low_price = 1, "
        "close_price = 100, volume = 1 WHERE date_ms > ?",
        [bars[41][1]],
    )
    con.close()
    after = scan_lurk_pool(path, asof=asof)

    assert after == before
    assert [it["symbol"] for it in after["items"]] == ["600000"]


def test_scan_lurk_pool_existing_empty_table_degrades_explicitly(tmp_path):
    path = tmp_path / "empty.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE, "
        "high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume DOUBLE, "
        "turnover DOUBLE)"
    )
    con.close()
    res = scan_lurk_pool(path, asof=date(2026, 9, 18))
    assert res["items"] == []
    assert res["trade_date"] is None and res["as_of"] is None
    assert res["stale"] is True
    assert "无可用 K 线" in res["stale_note"]


def test_scan_lurk_pool_uses_raw_daily_k_not_adjusted_shape(tmp_path):
    bars = _valid_hit_bars()
    path = tmp_path / "raw-vs-adj.duckdb"
    _make_daily_db(path, bars, with_adj=True)
    asof = datetime.fromtimestamp(bars[-1][1] / 1000, tz=BJ_TZ).date()
    res = scan_lurk_pool(path, asof=asof)
    assert [it["symbol"] for it in res["items"]] == ["600000"]


def test_recent_confirmation_uses_trading_sessions_not_calendar_days(tmp_path):
    start = date(2026, 6, 1)
    days = []
    cur = start
    while len(days) < 47:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    times = [
        int(datetime(d.year, d.month, d.day, tzinfo=BJ_TZ).timestamp() * 1000)
        for d in days
    ]
    bars = [
        ("600000.SH", ms, 9.95, 10.06, 9.92, 10.03, 100.0)
        for ms in times
    ]
    bars[40] = ("600000.SH", times[40], 10.0, 11.1, 10.05, 10.60, 200.0)
    bars[41] = ("600000.SH", times[41], 10.35, 10.38, 10.06, 10.10, 50.0)
    path = tmp_path / "trading-days.duckdb"
    _make_daily_db(path, bars)

    res = scan_lurk_pool(path, asof=days[-1])
    assert (days[-1] - days[41]).days > LOOKBACK_DAYS
    assert [it["symbol"] for it in res["items"]] == ["600000"]


def test_lurk_limit_pct_respects_chinext_2020_rule_timepoint():
    assert _limit_pct("300001.SZ", asof=date(2020, 8, 21)) == 0.10
    assert _limit_pct("300001.SZ", asof=date(2020, 8, 24)) == 0.20
