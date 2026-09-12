"""筹码分布引擎测试：合成 OHLCV 验证形态语义，不依赖真实 marketdb。"""

from __future__ import annotations

from datetime import date, datetime

import duckdb
import pytest

from app.market import trade_calendar as tc
from app.market.chip import ChipService, simulate_chip_distribution
from app.core.bjtime import BJ_TZ, beijing_today  # S2-8 时区收敛

_MS_DAY = 86_400_000



def _row(day: int, low: float, high: float, close: float, vol: float, turnover: float) -> dict:
    return {
        "open": low + (high - low) * 0.3, "high": high, "low": low, "close": close,
        "volume": vol, "turnover": turnover,
    }


def _flat_then_rise(n_flat: int = 60, flat_px: float = 10.0, rise_to: float = 20.0) -> list[dict]:
    """60 日 10 元横盘（高换手，筹码密集）→ 40 日爬升到 20 元（低换手）。"""
    rows = [
        _row(d, flat_px * 0.98, flat_px * 1.02, flat_px, vol=1_000_000, turnover=12_000_000)
        for d in range(n_flat)
    ]
    px = flat_px
    for d in range(40):
        px = flat_px + (rise_to - flat_px) * (d + 1) / 40
        rows.append(_row(60 + d, px * 0.99, px * 1.01, px, vol=200_000, turnover=3_000_000))
    return rows


class TestSimulateChipDistribution:
    def test_flat_then_rise_profit_ratio_high_and_peak_at_cost_zone(self):
        out = simulate_chip_distribution(_flat_then_rise())
        assert out is not None
        # 现价 20 元：横盘期筹码几乎全部获利（爬升期低换手，旧筹码基本保留）
        assert out["profit_ratio"] > 0.85
        # 主峰在横盘成本区（10 元附近 ±15%），支撑≈主峰
        assert 8.5 <= out["main_peak"]["price"] <= 11.5
        assert out["support"] is not None and out["support"] <= out["main_peak"]["price"] + 1.0
        # 集中度：横盘期筹码密集，(p95-p5)/(p95+p5) 应显著小
        assert out["concentration"] is not None and out["concentration"] < 0.6

    def test_high_level_distribution_signal(self):
        """高位放量滞涨（派发场景）：新筹码铺在高价区 → 获利盘显著下降。"""
        rows = _flat_then_rise()
        # 最后 10 日在 20 元横盘且巨量换手（高位堆筹码）
        for d in range(10):
            rows.append(_row(100 + d, 19.8, 20.2, 20.0, vol=5_000_000, turnover=100_000_000))
        out = simulate_chip_distribution(rows)
        assert out is not None
        base = simulate_chip_distribution(_flat_then_rise())
        # 对比未派发基线：获利盘下降（高位新筹码 = 部分套牢/接盘筹码）
        assert out["profit_ratio"] < base["profit_ratio"]
        # 高位派发后上方出现压力峰（现价上方有可观筹码）
        assert out["resistance"] is not None and out["resistance"] > out["as_of_close"] - 0.5

    def test_one_word_board_no_crash(self):
        """一字板（high==low）全铺该价位，不除零不炸。"""
        rows = [_row(d, 10.0, 10.0, 10.0, vol=1_000_000, turnover=10_000_000) for d in range(30)]
        out = simulate_chip_distribution(rows)
        assert out is not None
        assert out["main_peak"]["price"] == pytest.approx(10.0, abs=0.3)
        assert out["profit_ratio"] in (0.0, 1.0)  # 现价=唯一价位，取整边界二选一

    def test_missing_turnover_rows_skipped_but_old_chips_untouched(self):
        rows = _flat_then_rise()
        n_before = len(rows)
        for d in range(5):  # 停牌样：turnover=None 的行
            rows.append(_row(200 + d, 20.5, 20.5, 20.5, vol=0, turnover=0))
            rows[-1]["turnover"] = None
            rows[-1]["volume"] = 0
        out = simulate_chip_distribution(rows)
        assert out is not None
        assert out["bars"] >= n_before - 10  # 有效行统计不因停牌崩

    def test_insufficient_rows_returns_none(self):
        assert simulate_chip_distribution([]) is None
        assert simulate_chip_distribution([_row(0, 10, 10.1, 10.05, 1, 10)] * 3) is None

    def test_grid_sum_normalized(self):
        out = simulate_chip_distribution(_flat_then_rise())
        assert out is not None
        assert sum(out["dist"]) == pytest.approx(1.0, abs=1e-4)
        assert len(out["dist"]) == 100


class TestChipService:
    def test_missing_db_explicit_degrade(self, tmp_path):
        svc = ChipService(db_path=tmp_path / "nope.duckdb")
        out = svc.distribution("000910")
        assert out["available"] is False
        assert "marketdb 不存在" in out["reason"]

    def test_bare_symbol_suffix_tolerated(self, tmp_path):
        svc = ChipService(db_path=tmp_path / "nope.duckdb")
        assert svc.distribution("000910.SZ")["available"] is False  # 同样显式降级不炸

    def test_stale_db_explicit_degrade_with_stale_days(self, tmp_path):
        """回归（P0-7）：仓存在但内容陈旧 → available=False 且带 stale_days。

        真实事故：marketdb 停在 2026-09-03（下游同步从未启动），chip 只判
        「仓在不在」→ 拿 6 个交易日前的日K 算出筹码形态当今日形态用。
        """
        _mk_daily_k(tmp_path, tail=date(2025, 3, 3), n=30)
        svc = ChipService(db_path=tmp_path / "market.duckdb")
        out = svc.distribution("600519")
        assert out["available"] is False
        assert "数据陈旧" in out["reason"] and "sync_marketdb.py" in out["reason"]
        assert out["stale_days"] > 3 and out["latest"] == "2025-03-03"

    def test_fresh_db_serves_distribution(self, tmp_path):
        """反证：锚到最近交易日 → 同一库正常出形态（降级只因陈旧，不是因为功能坏了）。"""
        days = tc._load_persisted() or []
        anchor = days[-1] if days else beijing_today()
        _mk_daily_k(tmp_path, tail=anchor, n=30)
        svc = ChipService(db_path=tmp_path / "market.duckdb")
        out = svc.distribution("600519")
        assert out["available"] is True and out["bars"] >= 5
        assert out.get("stale_days") is None


def _mk_daily_k(tmp_path, *, tail: date, n: int) -> None:
    """造 daily_k 小库：n 根日K，最后一根为 tail（上海零点毫秒，与仓内口径一致）。"""
    db = tmp_path / "market.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""CREATE TABLE daily_k (
        thscode VARCHAR, date_ms BIGINT, open_price DOUBLE, high_price DOUBLE,
        low_price DOUBLE, close_price DOUBLE, volume DOUBLE, turnover DOUBLE)""")
    ms = int(datetime(tail.year, tail.month, tail.day, tzinfo=BJ_TZ).timestamp() * 1000)
    con.executemany(
        "INSERT INTO daily_k VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [("600519.SH", ms - (n - 1 - i) * _MS_DAY, 10.0, 10.2, 9.9, 10.1, 1_000_000, 10_100_000)
         for i in range(n)],
    )
    con.close()
