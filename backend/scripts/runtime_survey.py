"""Controlled, offline runtime survey; never starts production services.

Run from backend: PYTHONPATH= .venv/bin/python scripts/runtime_survey.py --output ...
Synthetic inputs are explicitly labelled. Results are descriptive measurements,
not production SLO or strategy evidence. Every request, including failures, is kept.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import platform
import resource
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.api.routes.market_board import router  # noqa: E402
from app.core.ttl_cache import cache_on  # noqa: E402
from app.schemas.market import Quote, utcnow  # noqa: E402
from app.services.quote_hub import QuoteHub, SUBSCRIBER_QUEUE_MAX  # noqa: E402

FIXTURE = [{"code": f"B{i:04}", "change_pct": (i % 21 - 10) / 10,
            "source": "synthetic-runtime-survey", "source_time": "frozen-fixture",
            "amount": i * 1000000} for i in range(300)]


def sample_quantile(values, q):
    if not values:
        return None
    return round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 3)


def distribution(values):
    values = sorted(values)
    return {"samples": len(values), "p50_ms": sample_quantile(values, .50),
            "p95_ms": sample_quantile(values, .95), "p99_ms": sample_quantile(values, .99),
            "max_ms": round(values[-1], 3) if values else None}


class Provider:
    name = "synthetic-runtime-survey"
    realtime = False

    def __init__(self):
        self.calls = 0
        self.fail = False

    async def get_board_rankings(self, kind):
        self.calls += 1
        await asyncio.sleep(.03)  # Frozen upstream service time; no external IO.
        if self.fail:
            raise OSError("injected source failure")
        return copy.deepcopy(FIXTURE)


async def api_survey(*, research=False):
    provider = Provider()
    app = FastAPI()
    app.state.hub = QuoteHub(provider, 1)
    app.include_router(router, prefix="/api/market")
    cache = cache_on(app.state, "market.boards", 60, maxsize=4)
    records, lags = [], []
    stop = asyncio.Event()

    async def lag_probe():
        while not stop.is_set():
            started = time.perf_counter()
            await asyncio.sleep(.005)
            lags.append(max(0, (time.perf_counter() - started - .005) * 1000))

    async def request(client, phase):
        started = time.perf_counter()
        research_active = worker is not None and not worker.done()
        status, rows, digest, error = None, None, None, None
        try:
            response = await client.get("/api/market/boards?type=concept")
            status = response.status_code
            if status == 200:
                rows = response.json()["data"]["boards"]
                digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
                rows = len(rows)
        except Exception as exc:
            error = type(exc).__name__
        records.append({"phase": phase, "ms": (time.perf_counter() - started) * 1000,
                        "status": status, "rows": rows, "digest": digest, "error": error,
                        "research_active": research_active})

    probe = asyncio.create_task(lag_probe())
    worker = asyncio.create_task(asyncio.to_thread(research_work)) if research else None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://survey") as client:
        for _ in range(20):
            cache.invalidate()
            await asyncio.gather(*(request(client, "cold_burst") for _ in range(20)))
            await asyncio.gather(*(request(client, "warm_burst") for _ in range(20)))
        provider.fail = True
        cache.invalidate()
        await request(client, "source_failure")
        provider.fail = False
        await request(client, "failure_recovery")
    research_result = None
    if worker:
        research_result = await worker  # Drain actual research; never pretend cancel killed a thread.
    stop.set()
    await probe
    phases = {}
    for phase in sorted({r["phase"] for r in records}):
        rows = [r for r in records if r["phase"] == phase]
        phases[phase] = {**distribution([r["ms"] for r in rows]),
                         "attempts": len(rows), "errors": sum(r["status"] != 200 for r in rows)}
    return {"phases": phases, "research_duration": research_result,
            "attempts_during_research": sum(r["research_active"] for r in records), "all_attempts": len(records),
            "errors": sum(r["status"] != 200 for r in records), "upstream_calls": provider.calls,
            "cache": cache.stats(), "event_loop_lag": distribution(lags),
            "coverage_counts": sorted({r["rows"] for r in records if r["status"] == 200}),
            "response_digests": sorted({r["digest"] for r in records if r["digest"]}),
            "records": records}


def research_work():
    """Actual factor SQL on a frozen synthetic panel, same to_thread as scheduler."""
    import duckdb
    from app.factors.evaluate import evaluate_factor
    from app.factors.library import FACTORS

    with duckdb.connect(":memory:", config={"threads": 2}) as con:
        con.execute("""CREATE TABLE daily_k AS SELECT
          printf('%06d.SH', s.i) AS thscode,
          (1577836800000 + d.i * 86400000)::BIGINT AS date_ms,
          (10 + s.i * .03 + sin(d.i * .1 + s.i) * .5 + d.i * .001)::DOUBLE AS close_price,
          close_price * .999 AS open_price, close_price * 1.02 AS high_price,
          close_price * .98 AS low_price, (1e7 + s.i * 1000 + d.i * 500) AS turnover,
          (1e6 + s.i * 100 + d.i * 50) AS volume
          FROM range(80) s(i) CROSS JOIN range(180) d(i)""")
        con.execute("CREATE TABLE daily_k_adj AS SELECT thscode,date_ms,close_price AS close_adj FROM daily_k")
        market_daily = dict(con.execute("SELECT date_ms,count(*) FROM daily_k GROUP BY date_ms").fetchall())
        timings = []
        for _ in range(5):
            started = time.perf_counter()
            evaluate_factor(con, FACTORS[0], market_daily)
            timings.append((time.perf_counter() - started) * 1000)
        return distribution(timings)


async def queue_survey():
    """Actual hub fan-out; slow consumer deliberately does not drain intermediate frames."""
    hub = QuoteHub(Provider(), 1)
    fast, slow = hub.subscribe({"600519"}), hub.subscribe({"600519"})
    durations, ages = [], []
    import tracemalloc
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for i in range(1000):
        hub.quotes["600519"] = Quote(symbol="600519", price=10, prev_close=10,
                                    source="synthetic-runtime-survey", data_timestamp=utcnow())
        started = time.perf_counter()
        hub._broadcast("quotes")
        durations.append((time.perf_counter() - started) * 1000)
        msg = fast.get_nowait()
        stamp = msg["data"][0]["data_timestamp"]
        from datetime import datetime
        ages.append((utcnow() - datetime.fromisoformat(stamp)).total_seconds() * 1000)
        if i % 20 == 0:
            await asyncio.sleep(0)
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    slow_last = None
    while not slow.empty():
        slow_last = slow.get_nowait()
    hub.unsubscribe(fast)
    hub.unsubscribe(slow)
    return {"attempts": 1000, "errors": 0, "fanout": distribution(durations),
            "fast_source_age": distribution(ages), "slow_dropped_frames": hub.dropped_frames,
            "slow_latest_seq": slow_last["seq"], "producer_latest_seq": hub._seq,
            "queue_cap": SUBSCRIBER_QUEUE_MAX, "retained_bytes_delta": retained - before,
            "peak_traced_bytes": peak, "active_after_disconnect": hub.subscriber_stats()}


def db_survey():
    """SQLite read/load/lock wait in a disposable file; not production DB latency."""
    with tempfile.TemporaryDirectory(prefix="ashare-runtime-survey-") as tmp:
        path = str(Path(tmp) / "survey.sqlite")
        with sqlite3.connect(path) as con:
            con.execute("CREATE TABLE rows(id INTEGER PRIMARY KEY, value TEXT)")
            con.executemany("INSERT INTO rows VALUES (?,?)", [(i, "x" * 100) for i in range(10000)])
            con.commit()
            timings = []
            for _ in range(100):
                started = time.perf_counter()
                assert len(con.execute("SELECT * FROM rows ORDER BY id LIMIT 100").fetchall()) == 100
                timings.append((time.perf_counter() - started) * 1000)
        # Real competing writer; lock is held until a timer releases its connection.
        import threading
        waits = []
        for _ in range(10):
            blocker = sqlite3.connect(path, check_same_thread=False)
            blocker.execute("BEGIN IMMEDIATE")
            timer = threading.Timer(.05, blocker.rollback)
            timer.start()
            started = time.perf_counter()
            with sqlite3.connect(path, timeout=1) as con:
                con.execute("UPDATE rows SET value=value WHERE id=0")
                con.commit()
            waits.append((time.perf_counter() - started) * 1000)
            timer.join()
            blocker.close()
        return {"read": distribution(timings), "writer_lock_wait": distribution(waits),
                "read_attempts": 100, "write_attempts": 10, "errors": 0,
                "scope": "SQLite mechanics; 50ms injected lock, 10000 synthetic rows"}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    def denied(*args, **kwargs):
        raise RuntimeError("runtime survey is offline: external socket operation forbidden")

    socket.getaddrinfo = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    start = time.perf_counter()
    idle = await api_survey()
    contention = await api_survey(research=True)
    queues = await queue_survey()
    db = await asyncio.to_thread(db_survey)
    # ru_maxrss is peak RSS since process start, not per-phase current memory.
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {"schema": "runtime-survey-v1", "scope": "offline controlled synthetic inputs",
              "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "tracked_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD", "--", "backend/app"])).hexdigest(),
              "python": platform.python_version(), "platform": platform.system(),
              "fixture_sha256": hashlib.sha256(json.dumps(FIXTURE, sort_keys=True).encode()).hexdigest(),
              "frozen": {"boards": 300, "source_delay_ms": 30, "rounds": 20, "concurrency": 20,
                         "ttl_seconds": 60, "factor": "mom5", "factor_panel": [80, 180], "factor_threads": 2},
              "idle": idle, "research_contention": contention, "hub_queues": queues, "sqlite": db,
              "peak_rss_bytes": rss if platform.system() == "Darwin" else rss * 1024,
              "wall_seconds": time.perf_counter() - start,
              "unknown": ["production startup/opening load", "live upstream latency", "browser/Canvas (separate probe)",
                          "full historical research contention", "production SLO impact"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"idle", "research_contention"}}, indent=2))
    for label in ("idle", "research_contention"):
        print(label, json.dumps({k: v for k, v in report[label].items() if k != "records"}))


if __name__ == "__main__":
    asyncio.run(main())
