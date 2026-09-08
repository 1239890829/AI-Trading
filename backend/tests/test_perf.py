"""性能基线（core/perf）纯函数单测——零 IO、零网络。"""
from __future__ import annotations

from app.core.perf import (
    api_metrics,
    collapse_path,
    duck_slow_queries,
    duck_timing,
    read_sync_history,
    record_api,
    record_duck,
)


def test_collapse_path_folds_numeric_segments():
    assert collapse_path("/positions/123") == "/positions/{id}"
    # 折叠只吃「斜杠开头的数字段」：日期串的月日部分保留——基数仍可控，语义可读
    assert collapse_path("/review/reports/2026-09-08") == "/review/reports/{id}-09-08"
    assert collapse_path("/api/health") == "/api/health"


def test_api_metrics_quantiles_and_window():
    # 直接注入固定时刻，避免真实时钟参与断言
    now = 10_000.0
    route = "GET /unit"
    for i, ms in enumerate([10.0, 20.0, 30.0, 40.0, 100.0]):
        record_api(route, ms, now=now - (i % 2) * 10)  # 全部落在窗口内
    rows = api_metrics(window_s=3600.0, now=now)
    hit = next(r for r in rows if r["route"] == route)
    assert hit["count"] == 5
    assert hit["p50_ms"] == 30.0
    assert hit["max_ms"] == 100.0
    assert hit["p95_ms"] >= hit["p50_ms"]
    # 窗口外不计入：半数样本在 now-10（窗口 5s 外），只剩窗口内的 3 条
    rows2 = api_metrics(window_s=5.0, now=now)
    hit2 = next((r for r in rows2 if r["route"] == route), None)
    assert hit2 is not None and hit2["count"] == 3


def test_record_duck_only_keeps_slow():
    assert record_duck("fast-query", 12.0) is False
    assert record_duck("slow-query", 500.0) is True
    labels = [r["label"] for r in duck_slow_queries(limit=50)]
    assert "slow-query" in labels and "fast-query" not in labels


def test_duck_timing_records_on_exception_too():
    try:
        with duck_timing("unit-timing", slow_ms=0.0):
            raise ValueError("x")
    except ValueError:
        pass
    assert any(r["label"] == "unit-timing" for r in duck_slow_queries(limit=50))


def test_read_sync_history_missing_file(tmp_path):
    assert read_sync_history(tmp_path / "market.duckdb") == []
