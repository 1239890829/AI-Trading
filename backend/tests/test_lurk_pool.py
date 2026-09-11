"""P1-5 lurk_pool 单测：潜伏+试盘+回踩确认判据（mock K 线）+ 陈旧披露（as_of/stale_days）。"""
from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pytest

from app.market.marketdb_freshness import MAX_STALE_TRADE_DAYS
from app.picks.lurk_pool import _limit_pct, _scan, scan_lurk_pool
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
