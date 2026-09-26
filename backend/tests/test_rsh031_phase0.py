"""Phase 0 must preserve its full denominator and untouched future boundary."""
from datetime import date, datetime, timedelta

import duckdb
import pytest

from app.core.bjtime import BJ_TZ
from scripts.rsh031_phase0 import build


def _ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=BJ_TZ).timestamp() * 1000)


def test_phase0_proxy_controls_and_future_boundary(tmp_path):
    database = tmp_path / "market.duckdb"
    con = duckdb.connect(str(database))
    con.execute("""CREATE TABLE daily_k (
        thscode VARCHAR, date_ms BIGINT, open_price DOUBLE, high_price DOUBLE,
        low_price DOUBLE, close_price DOUBLE, volume DOUBLE, turnover DOUBLE)
    """)
    con.execute("CREATE TABLE adjust_factor (thscode VARCHAR, ex_date_ms BIGINT)")
    days = [date(2020, 8, 1) + timedelta(days=i) for i in range(31)]
    rows = []
    for code in ("300001.SZ", "300002.SZ"):
        for i, day in enumerate(days):
            close = 10.0
            high = 10.0
            if code == "300001.SZ" and i == 23:
                close = high = 12.0  # 2020-08-24: ChiNext 20cm proxy
            if code == "300002.SZ" and i == 24:
                close = high = 12.0  # ex-date move must stay unclassifiable
            rows.append((code, _ms(day), 10.0, high, 10.0, close, 1000.0, 20_000_000.0))
    rows.append(("300001.SZ", _ms(date(2025, 1, 2)), 10.0, 20.0, 10.0,
                 20.0, 1000.0, 20_000_000.0))
    con.executemany("INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute("INSERT INTO adjust_factor VALUES (?, ?)",
                ["300002.SZ", _ms(date(2020, 8, 25))])
    con.close()

    output = tmp_path / "assets"
    manifest = build(database, output)
    assert manifest["exploration_bars"] == 62
    assert manifest["source_rows_all_periods"] == 63
    assert manifest["validation_or_holdout_outcomes_queried"] is False
    check = duckdb.connect()
    observed = check.execute("SELECT proxy_class, nominal_limit FROM read_parquet(?) "
                             "WHERE thscode = '300001.SZ' AND trade_date = DATE '2020-08-24'",
                             [str(output / "exploration_observations.parquet")]).fetchone()
    assert observed[0] == "close_at_nominal_limit_proxy"
    assert float(observed[1]) == pytest.approx(0.2)
    ex_date = check.execute("SELECT proxy_class, corporate_action_on_date "
                            "FROM read_parquet(?) WHERE thscode = '300002.SZ' "
                            "AND trade_date = DATE '2020-08-25'",
                            [str(output / "exploration_observations.parquet")]).fetchone()
    assert ex_date == ("unclassifiable", True)
    matched = check.execute("SELECT control_code FROM read_parquet(?) WHERE event_code = '300001.SZ'",
                            [str(output / "matched_controls.parquet")]).fetchall()
    assert ("300002.SZ",) in matched
    assert check.execute("SELECT max(trade_date) FROM read_parquet(?)",
                         [str(output / "exploration_observations.parquet")]).fetchone()[0] < date(2024, 1, 1)
    check.close()
    with pytest.raises(FileExistsError):
        build(database, output)


def test_missing_daily_schema_fails_closed(tmp_path):
    database = tmp_path / "bad.duckdb"
    con = duckdb.connect(str(database))
    con.execute("CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT)")
    con.close()
    with pytest.raises(ValueError, match="missing columns"):
        build(database, tmp_path / "out")
