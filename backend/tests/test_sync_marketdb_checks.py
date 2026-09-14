"""sync_marketdb 质量/新鲜度校验测试（移植自官方 SDK 的校验层；自建 fixture，不碰真实仓）。"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from scripts.sync_marketdb import (  # noqa: E402
    _calendar_days_ms,
    freshness_lag_days,
    run_quality_checks,
)
from app.core.bjtime import BJ_TZ  # S2-8 时区收敛

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


# --------------------------------------------------------------------------
# P0-7 修复回归（2026-09-11）：复权事件自然键 + 日历助手死代码
# --------------------------------------------------------------------------


def test_same_day_distinct_events_pass(con):
    """同一除权日的**不同**事件合法（同日派现 + 送股），不得判重复。

    实测形态：000812.SZ 1998-09-22 = 派现 0.2 + 送股 0.1；
    603883.SH 2024-06-27 = 两笔不同派送。旧判据只按 (thscode, ex_date_ms)
    分组，会把它们误报为 error → 每次同步恒判 fail（killed by false positive）。
    """
    _k(con)
    con.execute("INSERT INTO adjust_factor VALUES ('000812.SZ', 906393600000, 0.2, 0, 0, 0)")
    con.execute("INSERT INTO adjust_factor VALUES ('000812.SZ', 906393600000, 0, 0.1, 0, 0)")
    issues = run_quality_checks(con)
    assert [i for i in issues if i["check"] == "adjust_factor.pk_unique"] == []


def test_true_duplicate_factor_caught(con):
    """全字段完全相同的两条才是真重复（000601.SZ 1997-11-03 实测形态）。

    真重复会被 rebuild_adj 的窗口乘积**重复计数**→ 该股该日之前的复权序列
    被过复权，故必须继续报 error。
    """
    _k(con)
    con.execute("INSERT INTO adjust_factor VALUES ('000601.SZ', 878486400000, 0, 0.4, 0, 0)")
    con.execute("INSERT INTO adjust_factor VALUES ('000601.SZ', 878486400000, 0, 0.4, 0, 0)")
    issues = run_quality_checks(con)
    assert any(i["check"] == "adjust_factor.pk_unique" and i["severity"] == "error"
               for i in issues)


def test_calendar_days_ms_from_persisted(monkeypatch):
    """持久化日历 → UTC+8 零点毫秒，升序，末元素 = 日历最后一天（无补足）。

    🔴 2026-09-14 修正（[[KB-ENG-56]] 同族）：本用例原**不注入 `today`**，
    而实现是「日历落后于今天时用工作日补足」（`sync_marketdb.py:333-338`）
    ⇒ 只要真实运行日晚于 2026-09-11，`out[-1]` 就不是 9-11 而是被补到当天，
    断言**除了 2026-09-11 当天以外每天都红**。这类"真实运行日驱动"的断言，
    一周里只有特定日历日才是绿的 ⇒ 守卫等于没写。
    修法与相邻两条同风格：注入 `today=` 把基准日钉在夹具同一天
    （该注入参数本就是为此而设，见 `_calendar_days_ms` docstring「仅测试注入用」）。
    """
    from datetime import date, datetime

    from app.market import trade_calendar as tc

    asof = date(2026, 9, 11)  # 周五；与持久化日历末元素同日 ⇒ 不触发补足分支
    monkeypatch.setattr(tc, "_load_persisted", lambda: [date(2026, 9, 10), asof])
    out = _calendar_days_ms(today=asof)
    tz8 = BJ_TZ
    assert out == sorted(out)
    assert out[-1] == int(datetime(asof.year, asof.month, asof.day, tzinfo=tz8).timestamp() * 1000)


def test_calendar_days_ms_extends_stale_calendar_to_today(monkeypatch):
    """日历停在过去 → 用**工作日**补足到「≤ 今天的最后一个工作日」。

    口径（2026-09-12 修正）：补出的是**交易日**序列，不是自然日序列；
    今天本身非交易日时不追加，故末元素 = ≤ 今天的最后一个工作日。
    原断言写作 `out[-1] >= 今天`，在周末/长假**必然失败**——一句话写宽了。
    """
    from datetime import datetime, timedelta

    from app.market import trade_calendar as tc

    tz8 = BJ_TZ
    today = datetime.now(tz8).date()
    monkeypatch.setattr(tc, "_load_persisted", lambda: [today - timedelta(days=10)])
    out = _calendar_days_ms(today=today)

    def _ms(d):
        return int(datetime(d.year, d.month, d.day, tzinfo=tz8).timestamp() * 1000)

    last_weekday = today
    while last_weekday.weekday() >= 5:
        last_weekday -= timedelta(days=1)
    assert out == sorted(out)
    assert out[-1] == _ms(last_weekday)
    # 上界同样要钉住：日历不得被补到"今天之后"（那会让滞后少算 = 静默放行）
    assert out[-1] <= _ms(today)


def test_calendar_days_ms_weekend_last_day_is_friday(monkeypatch):
    """定点回归（2026-09-12）：今天 = 周六 → 末元素必须是周五。

    为什么必须定点：真实运行日驱动的断言，**只在周末/长假变红**，
    即"一周里 5 天是绿的"——这类守卫等于没写（绿 ≠ 有效）。
    """
    from datetime import date, datetime, timedelta

    from app.market import trade_calendar as tc

    sat = date(2026, 9, 12)
    assert sat.weekday() == 5, "夹具前提：2026-09-12 必须是周六"
    monkeypatch.setattr(tc, "_load_persisted", lambda: [sat - timedelta(days=10)])
    out = _calendar_days_ms(today=sat)
    tz8 = BJ_TZ
    fri = date(2026, 9, 11)
    assert out[-1] == int(datetime(fri.year, fri.month, fri.day, tzinfo=tz8).timestamp() * 1000)


def test_calendar_days_ms_sunday_last_day_is_friday(monkeypatch):
    """定点回归：今天 = 周日 → 末元素同样是周五（连续两个非交易日不重复追加）。"""
    from datetime import date, datetime, timedelta

    from app.market import trade_calendar as tc

    sun = date(2026, 9, 13)
    assert sun.weekday() == 6, "夹具前提：2026-09-13 必须是周日"
    monkeypatch.setattr(tc, "_load_persisted", lambda: [sun - timedelta(days=11)])
    out = _calendar_days_ms(today=sun)
    tz8 = BJ_TZ
    fri = date(2026, 9, 11)
    assert out[-1] == int(datetime(fri.year, fri.month, fri.day, tzinfo=tz8).timestamp() * 1000)


def test_calendar_days_ms_holiday_weekday_is_appended_on_purpose(monkeypatch):
    """**刻意**的保守方向：落在工作日的法定节假日会被当作交易日补上。

    这不是缺陷而是判据选择——工作日计数"最多算多"，过期日历按真实交易日计数
    则"会算少"，而**低估陈旧 = 静默放行**（同 `marketdb_freshness` 模块 docstring）。
    本用例把该取舍钉住：将来若想改成查真实节假日，必须同时改 freshness 口径，
    不能只改这里让两边分家。
    """
    from datetime import date, datetime, timedelta

    from app.market import trade_calendar as tc

    # 2026-10-01 是周四（国庆），非工作日历意义上的交易日，但实现按工作日补足 → 会追加
    holiday = date(2026, 10, 1)
    assert holiday.weekday() < 5, "夹具前提：2026-10-01 必须是周一~周五"
    monkeypatch.setattr(tc, "_load_persisted", lambda: [holiday - timedelta(days=3)])
    out = _calendar_days_ms(today=holiday)
    tz8 = BJ_TZ
    assert out[-1] == int(
        datetime(holiday.year, holiday.month, holiday.day, tzinfo=tz8).timestamp() * 1000
    )


def test_calendar_days_ms_empty_when_calendar_missing(monkeypatch, capsys):
    """日历不可用 → 返回 [] 且**显式**打警告（跳过检查，不静默）。"""
    from app.market import trade_calendar as tc

    monkeypatch.setattr(tc, "_load_persisted", lambda: None)
    assert _calendar_days_ms() == []
    assert "跳过 freshness 检查" in capsys.readouterr().err


def test_calendar_helper_never_calls_async_trading_days():
    """反漂移：不得再直接调 `trading_days()`——它是 `async def trading_days(provider, ...)`，
    缺 provider 且未 await 必然 TypeError，会让滞后门槛变成**死代码**（2026-09-11 修复前实况）。
    """
    src = (BACKEND_ROOT / "scripts" / "sync_marketdb.py").read_text(encoding="utf-8")
    assert "_load_persisted()" in src
    assert "days = trading_days()" not in src


def test_sync_factors_dedups_exact_duplicates(con, tmp_path, monkeypatch):
    """入库去重（行为级）：源 parquet 中**全字段完全相同**的行不得进入 adjust_factor，
    而同日不同事件（派现 / 送股）必须两条都保留。

    为什么必须去重：`rebuild_adj` 的累计系数用窗口乘积，同日重复行会被**重复计数**
    → 该股该日之前的复权序列过复权（实测 000601.SZ 1997-11-03 送股 0.4 重复，
    过复权 28.6%）。
    """
    import scripts.sync_marketdb as sm

    src = tmp_path / "factors.parquet"
    con.execute(
        "COPY (SELECT * FROM (VALUES "
        "('000601.SZ', 878486400000, 0.0, 0.4, 0.0, 0.0), "
        "('000601.SZ', 878486400000, 0.0, 0.4, 0.0, 0.0), "
        "('000812.SZ', 906393600000, 0.2, 0.0, 0.0, 0.0), "
        "('000812.SZ', 906393600000, 0.0, 0.1, 0.0, 0.0)"
        ") AS t(thscode, ex_date_ms, dividend_per_share, per_share_bonus, "
        "allotment_ratio, allotment_price)) "
        f"TO '{src}' (FORMAT PARQUET)"
    )
    monkeypatch.setattr(sm, "_get_download_url", lambda *a, **k: "http://test/parquet")
    monkeypatch.setattr(
        sm, "_download_parquet",
        lambda _client, _url, dest: dest.write_bytes(src.read_bytes()),
    )
    out = sm._sync_factors(con, object(), "http://test", "k")
    assert out["rows_in"] == 4, "源行数应如实报告（含重复），便于发现上游回归"
    assert out["events"] == 3, "真重复去重后 3 条；同日不同事件必须保留为 2 条"
