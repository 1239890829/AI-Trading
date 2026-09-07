"""sync_marketdb 质量/新鲜度校验测试（移植自官方 SDK 的校验层；自建 fixture，不碰真实仓）。"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from scripts.sync_marketdb import (  # noqa: E402
    freshness_lag_days,
    run_quality_checks,
)

SCHEMA = """
CREATE TABLE daily_k (
    thscode VARCHAR, date_ms BIGINT,
    open_price DOUBLE, high_price DOUBLE, low_price DOUBLE, close_price DOUBLE,
    volume DOUBLE, turnover DOUBLE
);
CREATE TABLE adjust_factor (
    thscode VARCHAR, ex_date_ms BIGINT,
    dividend_per_share DOUBLE, per_share_bonus DOUBLE,
    allotment_ratio DOUBLE, allotment_price DOUBLE
);
CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE);
"""


@pytest.fixture()
def con(tmp_path):
    c = duckdb.connect(str(tmp_path / "t.duckdb"))
    c.execute(SCHEMA)
    yield c
    c.close()


def _k(con, code="600519.SH", ms=1788700800000, o=10.0, h=11.0, low=9.9, close=10.5, vol=100.0):
    con.execute(
        "INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [code, ms, o, h, low, close, vol, vol * 10.0],
    )


def test_clean_db_passes(con):
    _k(con)
    con.execute("INSERT INTO adjust_factor VALUES ('600519.SH', 1788700800000, 0.1, 0, 0, 0)")
    issues = run_quality_checks(con)
    assert [i for i in issues if i["severity"] == "error"] == []


def test_empty_db_is_error(con):
    issues = run_quality_checks(con)
    errs = [i for i in issues if i["severity"] == "error"]
    assert any(i["check"] == "daily_k.rowcount_positive" for i in errs)


def test_high_lt_low_caught(con):
    _k(con, h=9.0, low=9.9)  # high < low
    issues = run_quality_checks(con)
    assert any(i["check"] == "daily_k.high_ge_low" and i["severity"] == "error" for i in issues)


def test_duplicate_pk_caught(con):
    _k(con)
    _k(con)  # 同 (thscode, date_ms) 两条
    issues = run_quality_checks(con)
    assert any(i["check"] == "daily_k.pk_unique" and i["severity"] == "error" for i in issues)


def test_negative_ohlc_caught(con):
    _k(con, close=-1.0)
    issues = run_quality_checks(con)
    assert any(i["check"] == "daily_k.ohlc_non_negative" and i["severity"] == "error" for i in issues)


def test_check_failure_is_explicit_error(con):
    con.execute("DROP TABLE adjust_factor")  # 检查自身失败也要显式暴露
    issues = run_quality_checks(con)
    assert any(i["check"] == "adjust_factor.pk_unique" and i["severity"] == "error"
               and "check failed" in i["detail"] for i in issues)


def test_freshness_empty_table(con):
    days = [1000, 2000, 3000]
    assert freshness_lag_days(con, days) == 3


def test_freshness_lag_counts_trading_days_between(con):
    _k(con, ms=1000)
    # 本地到 1000，日历到 3000：滞后 2000、3000 两天
    assert freshness_lag_days(con, [1000, 2000, 3000]) == 2


def test_freshness_up_to_date(con):
    _k(con, ms=3000)
    assert freshness_lag_days(con, [1000, 2000, 3000]) == 0
