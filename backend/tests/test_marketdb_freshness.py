"""marketdb 新鲜度判定单测（P0-7）：内容日期口径、交易日滞后、工作日兜底、三态。

不依赖真实 marketdb：合成小库 + 受控日历（monkeypatch `_load_persisted`）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import duckdb

from app.market import trade_calendar as tc
from app.market.marketdb_freshness import (
    MAX_STALE_TRADE_DAYS,
    freshness,
    latest_content_date,
    trading_day_lag,
)

_MS_DAY = 86_400_000
_BJ = timezone(timedelta(hours=8))


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=_BJ).timestamp() * 1000)


def _mk_db(tmp_path, tables: dict[str, list[int]]):
    """{表名: [date_ms...]} → 小库；空列表 = 建表不插数。"""
    db = tmp_path / "market.duckdb"
    con = duckdb.connect(str(db))
    for name, stamps in tables.items():
        con.execute(f"CREATE TABLE {name} (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
        if stamps:
            con.executemany(f"INSERT INTO {name} VALUES ('600519.SH', ?, 10.0)",
                            [(s,) for s in stamps])
    con.close()
    return db


# ---------------------------------------------------------------- 内容日期


def test_missing_db_is_not_stale_but_unavailable(tmp_path):
    fr = freshness(tmp_path / "nope.duckdb")
    assert fr["available"] is False and fr["stale"] is False
    assert "marketdb 不存在" in fr["reason"] and "sync_marketdb.py" in fr["reason"]


def test_latest_content_date_prefers_adj_then_falls_back(tmp_path):
    """优先复权表；复权表缺失/为空 → 回落 daily_k（不因一张表空就判"读不出"）。"""
    old = date(2026, 9, 3)
    db = _mk_db(tmp_path, {"daily_k_adj": [_ms(old - timedelta(days=1)), _ms(old)],
                           "daily_k": [_ms(old + timedelta(days=5))]})
    assert latest_content_date(db) == old

    # 复权表存在但为空 → 回落 daily_k
    d2 = tmp_path / "b"
    d2.mkdir()
    con = duckdb.connect(str(d2 / "market.duckdb"))
    con.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
    con.execute("CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
    con.execute("INSERT INTO daily_k VALUES ('600519.SH', ?, 10.0)", [_ms(old)])
    con.close()
    assert latest_content_date(d2 / "market.duckdb") == old


def test_empty_or_corrupt_db_unreadable(tmp_path):
    db = _mk_db(tmp_path, {"daily_k_adj": [], "daily_k": []})
    assert latest_content_date(db) is None
    fr = freshness(db)
    assert fr["available"] is False and "不可读" in fr["reason"]


# ---------------------------------------------------------------- 交易日滞后


def test_trading_day_lag_uses_calendar_when_it_covers_asof(monkeypatch):
    """日历覆盖 asof → 真实交易日计数（跳过周末与节假日）。"""
    cal = [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7),
           date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)]
    monkeypatch.setattr(tc, "_load_persisted", lambda: cal)
    assert trading_day_lag(date(2026, 9, 3), date(2026, 9, 11)) == 6
    # asof 早于/等于 latest → 0（历史截面不算陈旧）
    assert trading_day_lag(date(2026, 9, 3), date(2026, 9, 3)) == 0
    assert trading_day_lag(date(2026, 9, 10), date(2026, 9, 3)) == 0


def test_trading_day_lag_falls_back_to_weekdays_when_calendar_incomplete(monkeypatch):
    """日历**过期**（不覆盖 asof）→ 退工作日计数：偏保守，绝不把滞后算少。

    若按过期日历截断计数会低估陈旧（假 OK）——这是本兜底存在的理由。
    """
    stale_cal = [date(2026, 9, 3), date(2026, 9, 4)]  # 只到 09-04
    monkeypatch.setattr(tc, "_load_persisted", lambda: stale_cal)
    # 09-03 → 09-11：工作日 09-04、09-07…09-11 共 6 个
    assert trading_day_lag(date(2026, 9, 3), date(2026, 9, 11)) == 6
    monkeypatch.setattr(tc, "_load_persisted", lambda: None)
    assert trading_day_lag(date(2026, 9, 3), date(2026, 9, 11)) == 6


# ---------------------------------------------------------------- freshness 契约


def test_freshness_stale_message_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "_load_persisted", lambda: [])
    old = date(2020, 1, 2)
    db = _mk_db(tmp_path, {"daily_k_adj": [_ms(old)]})
    fr = freshness(db, asof=date(2026, 9, 11))
    assert fr["available"] is True and fr["stale"] is True
    assert fr["latest"] == "2020-01-02" and fr["lag"] > MAX_STALE_TRADE_DAYS
    assert fr["threshold"] == MAX_STALE_TRADE_DAYS
    assert "数据陈旧" in fr["reason"] and "sync_marketdb.py" in fr["reason"]
    assert fr["asof"] == "2026-09-11"


def test_freshness_threshold_override(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "_load_persisted", lambda: [])
    old = date(2020, 1, 2)
    db = _mk_db(tmp_path, {"daily_k_adj": [_ms(old)]})
    assert freshness(db, max_stale_days=10_000)["stale"] is False
    assert freshness(db, max_stale_days=0)["stale"] is True


def test_freshness_historical_asof_never_stale(tmp_path, monkeypatch):
    """回测/复盘显式基准日：库内更新的数据不算陈旧（要的就是那一刻的截面）。"""
    monkeypatch.setattr(tc, "_load_persisted", lambda: [])
    db = _mk_db(tmp_path, {"daily_k_adj": [_ms(date(2026, 9, 3))]})
    fr = freshness(db, asof=date(2026, 3, 2))
    assert fr["stale"] is False and fr["lag"] == 0
