"""marketdb 数据质量门：近窗业务校验（合成 DuckDB fixture，零网络）。"""
from __future__ import annotations

import duckdb
import pytest

from app.market.marketdb_quality import run_recent_quality_checks

_SCHEMA = """
CREATE TABLE daily_k (
    thscode VARCHAR, date_ms BIGINT,
    open_price DOUBLE, high_price DOUBLE, low_price DOUBLE, close_price DOUBLE,
    volume DOUBLE, turnover DOUBLE
);
CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE);
"""

DAY = 86_400_000
T0 = 1_788_000_000_000  # 任意毫秒锚点


@pytest.fixture()
def con(tmp_path):
    c = duckdb.connect(str(tmp_path / "t.duckdb"))
    c.execute(_SCHEMA)
    yield c
    c.close()


def _k(con, sym, day_offset, close, open_=None, high=None, low=None, volume=100.0):
    o = open_ if open_ is not None else close
    h = high if high is not None else close
    l = low if low is not None else close
    con.execute(
        "INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [sym, T0 + day_offset * DAY, o, h, l, close, volume, volume * close],
    )


def test_clean_data_passes(con):
    for i in range(10):
        _k(con, "600519", i, 100.0 + i)
        con.execute("INSERT INTO daily_k_adj VALUES (?, ?, ?)", ["600519", T0 + i * DAY, 100.0 + i])
    assert run_recent_quality_checks(con) == []


def test_null_ohlcv_flagged(con):
    con.execute(
        "INSERT INTO daily_k VALUES ('600519', ?, 1, 1, 1, NULL, 1, 1)",
        [T0],
    )
    issues = run_recent_quality_checks(con)
    names = {i["check"] for i in issues}
    assert "daily_k.recent_null_ohlcv" in names


def test_adj_single_day_jump_flagged_but_raw_not(con):
    # 复权序列 +55% 单日跳变 = 数据错误（前复权序列不应有除权断崖）
    for i in range(6):
        con.execute("INSERT INTO daily_k_adj VALUES (?, ?, ?)",
                    ["600540", T0 + i * DAY, 10.0 * (1.55 ** i)])
        _k(con, "600540", i, 10.0 + i)  # 原始序列正常
    issues = run_recent_quality_checks(con)
    jump = next(i for i in issues if i["check"] == "daily_k_adj.single_day_jump")
    assert jump["severity"] == "warn"
    assert abs(jump["sample"][0][2] - 55.0) < 0.01


def test_zero_close_flagged(con):
    _k(con, "600540", 0, 0.0)
    issues = run_recent_quality_checks(con)
    assert any(i["check"] == "daily_k.recent_zero_close" for i in issues)
