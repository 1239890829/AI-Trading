"""进程内性能观测（策略进化 P1 方向 4 性能基线）。

三路信号，全部内存环形缓冲、零持久化、读时聚合：

- **API 分位延迟**：中间件逐请求记 (时刻, 路由模板, 耗时ms)，/api/system/metrics
  聚合 p50/p95/p99。路由名取 ``scope["route"].path``（FastAPI 路由匹配后写入
  scope，中间件 await call_next 之后可读）；拿不到退化为数字折叠的原始路径
  （/positions/123 → /positions/{id}），避免高基数路径撑爆聚合。
- **DuckDB 慢查询**：只记超过阈值的查询（默认 300ms）——快查询记了也是噪音。
  调用方用 ``duck_timing(label)`` 上下文管理器或 ``record_duck``。
- **同步时长趋势**：sync_marketdb 成功后把耗时 append 进
  data/marketdb/sync_history.json（保留 30 条），本模块只负责读。

为什么不走 Prometheus/OTel：单进程部署、观测点是「有没有变慢」而非
「分布式追踪」；deque + Lock 的复杂度与收益匹配。
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

_API_CAP = 20000
_DUCK_CAP = 500
DUCK_SLOW_MS = 300.0

_api: deque[tuple[float, str, float]] = deque(maxlen=_API_CAP)
_duck: deque[tuple[float, str, float]] = deque(maxlen=_DUCK_CAP)
_lock = Lock()

_NUM_SEG = re.compile(r"/\d+")


def collapse_path(path: str) -> str:
    """原始路径 → 聚合模板（数字段折叠），限制路由基数。"""
    return _NUM_SEG.sub("/{id}", path or "/unknown")


def record_api(route: str, ms: float, *, now: float | None = None) -> None:
    with _lock:
        _api.append((now if now is not None else time.monotonic(), route, float(ms)))


def record_duck(label: str, ms: float, *, slow_ms: float = DUCK_SLOW_MS) -> bool:
    """记录 DuckDB 查询耗时；仅慢查询入缓冲。返回是否被记录。"""
    if ms < slow_ms:
        return False
    with _lock:
        _duck.append((time.monotonic(), label, float(ms)))
    return True


@contextmanager
def duck_timing(label: str, *, slow_ms: float = DUCK_SLOW_MS):
    """DuckDB 慢查询计时上下文：``with duck_timing("chip.grid"): con.execute(...)``。"""
    t0 = time.monotonic()
    try:
        yield
    finally:
        record_duck(label, (time.monotonic() - t0) * 1000, slow_ms=slow_ms)


def _quantile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1))))
    return round(sorted_vals[idx], 1)


def api_metrics(window_s: float = 3600.0, *, now: float | None = None) -> list[dict]:
    """按路由聚合窗口内延迟分位，p95 降序。"""
    t_now = now if now is not None else time.monotonic()
    cutoff = t_now - window_s
    buckets: dict[str, list[float]] = {}
    with _lock:
        rows = list(_api)
    for ts, route, ms in rows:
        if ts < cutoff:
            continue
        buckets.setdefault(route, []).append(ms)
    out = []
    for route, vals in buckets.items():
        vals.sort()
        out.append({
            "route": route,
            "count": len(vals),
            "p50_ms": _quantile(vals, 0.50),
            "p95_ms": _quantile(vals, 0.95),
            "p99_ms": _quantile(vals, 0.99),
            "max_ms": round(vals[-1], 1),
        })
    out.sort(key=lambda r: (r["p95_ms"] or 0.0), reverse=True)
    return out


def duck_slow_queries(limit: int = 10) -> list[dict]:
    """最近的慢查询（新的在前），供 /api/system/metrics 直接透出。"""
    with _lock:
        rows = list(_duck)
    rows.sort(key=lambda r: r[0], reverse=True)
    return [{"label": label, "ms": round(ms, 1)} for _, label, ms in rows[:limit]]


def read_sync_history(db_path: Path, keep: int = 30) -> list[dict]:
    """读同步时长趋势（由近及远）。文件缺失/损坏 → []（三态，不臆造）。"""
    hist_file = db_path.parent / "sync_history.json"
    try:
        rows = json.loads(hist_file.read_text(encoding="utf-8"))
        return list(rows)[-keep:][::-1] if isinstance(rows, list) else []
    except Exception:  # noqa: BLE001
        return []
