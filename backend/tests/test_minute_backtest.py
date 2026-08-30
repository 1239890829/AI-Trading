"""回测运行器测试：JSONP 剥离 / schema 转换 / 聚合统计（合成 Parquet，无网络）。

沙箱注意：pytest 的 tmp_path fixture 会被 sitecustomize shim 以 PermissionError
拦截（EEXIST 误判），所以这里用 backend/data/tmp-backtest/ 项目内目录。
"""

import sys
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, ".")

import polars as pl

from app.market.minute_backfill import strip_jsonp, to_sina_symbol
from app.market.minute_backtest import run_backtest

T0 = datetime(2026, 8, 28, 1, 30, tzinfo=timezone.utc)  # 北京 09:30
TMP = Path(__file__).resolve().parent.parent / "data" / "tmp-backtest"


def _day_points(day: str, prices: list[float], vols: list[int] | None = None):
    """一日 5 分钟点（6 bar = 30 分钟可结算窗口）。"""
    base = datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc) - timedelta(hours=8)
    pts = []
    cum_amt = 0.0
    cum_vol = 0
    for i, p in enumerate(prices):
        v = (vols[i] if vols else 1000)
        ts = base + timedelta(minutes=5 * i)
        cum_amt += p * v
        cum_vol += v
        pts.append({
            "ts": ts.isoformat(), "price": p, "volume": float(v),
            "cum_amount": round(cum_amt, 2), "cum_volume": cum_vol,
            "avg": round(cum_amt / cum_vol, 3), "source": "sina_m5",
        })
    return pts


def test_strip_jsonp_and_symbol():
    raw = '/*<script>location.href="//sina.com";</script>*/\nvar _data=([{"day":"2026-08-28 15:00:00"}]);\n'
    assert strip_jsonp(raw) == [{"day": "2026-08-28 15:00:00"}]
    assert to_sina_symbol("600519") == "sh600519"
    assert to_sina_symbol("000001") == "sz000001"


def test_run_backtest_aggregates_synthetic_parquet():
    """2 只票 × 2 天合成数据：第一天横盘（无信号），第二天构造低吸共振日
    （下杀→缩量新低底背离→确认），断言聚合结构完整且信号被捕获。"""
    if TMP.exists():
        shutil.rmtree(TMP)
    flat = [10.0] * 48
    # day2：10 根平 → 4 根下杀 → 1 根缩量新低（底背离 vol 300 ≤ 均量×0.4）
    # → 14 根 9.76 确认（谷底 3 bar 确认在缩量段内触发）→ 17 根 10.02 拉升
    d2 = [10.0] * 10 + [9.9, 9.85, 9.8, 9.78] + [9.75] + [9.76] * 14 + [10.02] * 17
    v2 = [1000] * 14 + [300] + [300] * 14 + [1000] * 17
    data = {
        "600519": {"20260827": flat, "20260828": d2},
        "000001": {"20260827": flat, "20260828": [10.0] * 48},
    }
    for sym, days in data.items():
        pts = []
        for d, prices in days.items():
            pts += _day_points(d, prices, v2 if d == "20260828" and sym == "600519" else None)
        TMP.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(pts).write_parquet(TMP / f"{sym}.parquet")

    try:
        report = run_backtest(["600519", "000001"], parquet_dir=TMP, in_ratio=0.5)
        assert report["data_days"] == 2
        assert report["split"]["in_days"] == 1 and report["split"]["out_days"] == 1
        for key in ("sample_in", "sample_out", "overall"):
            agg = report[key]
            assert {"signals", "correct", "wrong", "invalid", "expired", "hit_rate"} <= set(agg)
        assert report["overall"]["signals"] >= 1, report["overall"]
        assert report["overall"]["by_bias"].get("低吸偏向", 0) >= 1
        assert report["note"].startswith("阈值未经校准")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


def test_run_backtest_no_data_symbol_skipped():
    if TMP.exists():
        shutil.rmtree(TMP)
    try:
        report = run_backtest(["999999"], parquet_dir=TMP)
        assert report["overall"]["signals"] == 0
        assert report["data_days"] == 0
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
