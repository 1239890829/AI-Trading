"""临板展示距离补价：候选范围、同源身份、时间质量及超时真实drain。零网络/DB。"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core import ttl_cache
from app.core.bjtime import to_beijing
from app.market.price_rules import limit_up_distance
from app.picks import pre_limit_radar as radar
from app.schemas.market import Quote, Quality
from app.services import quote_enrich


def _candidate(symbol="600002"):
    return {"symbol": symbol, "market": "SH", "name": "临板股", "price": 10.72,
            "pct": 7.2, "runway_pct": 2.5, **limit_up_distance({"price": 10.72})}


def _quote(symbol="600002", **changes):
    return Quote(symbol=symbol, market="SH", name="临板股", source="tencent",
                 price=10.8, limit_up_price=11.0,
                 data_timestamp=datetime.now(timezone.utc), **changes)


def _state(cached=()):
    return SimpleNamespace(hub=SimpleNamespace(get_quotes=lambda _symbols: list(cached)))


def test_candidate_distance_uses_same_quote_price_and_keeps_original_entry_and_runway(monkeypatch):
    calls = []
    quote = _quote()
    async def fetch(_hub, symbols, **kwargs):
        calls.append((symbols, kwargs))
        return {"600002": quote}
    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", fetch)
    candidate = _candidate()
    asyncio.run(radar._enrich_candidate_distances(_state(), [candidate]))
    assert candidate["limit_up_gap_pct"] == 1.85  # 用10.8而非原筛选快照10.72（后者会得2.61）。
    assert candidate["limit_up_gap_price"] == 10.8 and candidate["limit_up_price"] == 11.0
    assert candidate["limit_up_gap_source"] == "tencent"
    assert candidate["limit_up_gap_as_of"] == to_beijing(quote.data_timestamp).isoformat()
    assert candidate["price"] == 10.72 and candidate["pct"] == 7.2 and candidate["runway_pct"] == 2.5
    assert calls == [(["600002"], {"prefer_cache": False, "batch_size": 50})]


@pytest.mark.parametrize("reopen", [False, True], ids=["pre_limit", "board_reopen"])
@pytest.mark.parametrize("quote_available", [True, False], ids=["quote_10_80", "unknown_quote"])
def test_reminder_distinguishes_selection_snapshot_from_actual_distance_quote(monkeypatch, reopen, quote_available):
    from app.picks import source_events, watch_ledger, watcher

    quote = _quote()
    alerts, sightings = [], []
    monkeypatch.setattr(radar, "_REOPEN", set())
    monkeypatch.setattr(watch_ledger, "get_day", lambda _day: [
        {"symbol": "600002", "reason": {"gate": "sealed_no_entry"}},
    ] if reopen else [])
    def record(**kwargs):
        sightings.append(kwargs)
        return None if reopen else {"symbol": kwargs["symbol"]}
    async def dispatch(_app, alert):
        alerts.append(alert)
        return True
    def source_event(_kind, _key, **kwargs):
        alerts.append(kwargs["brief_alert"])
        return 31, True, True
    async def fetch(_hub, _symbols, **_kwargs):
        return {"600002": quote} if quote_available else {}
    monkeypatch.setattr(watch_ledger, "record_sighting", record)
    monkeypatch.setattr(watcher, "dispatch_alert", dispatch)
    monkeypatch.setattr(source_events, "record_source_event", source_event)
    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", fetch)
    state = _state()
    state.snapshot_service = SimpleNamespace(
        snapshot=[{"symbol": "600002", "market": "SH", "name": "临板股",
                   "price": 10.72, "change_pct": 7.2, "turnover_rate": 8.0}],
        last_success=datetime.now(timezone.utc),
    )
    assert asyncio.run(radar.pre_limit_sweep(state)) == (0 if reopen else 1)
    assert len(alerts) == 1
    alert, snapshot_as_of = alerts[0], state.snapshot_service.last_success.isoformat()
    assert alert["kind"] == ("board_reopen" if reopen else "pre_limit")
    assert "入选快照涨幅 7.2%" in alert["text"] and "入选快照价 10.72" in alert["text"]
    assert f"快照版本时点 {snapshot_as_of}" in alert["text"]
    assert "现价 10.72" not in alert["text"]
    assert alert["meta"]["trigger_value"] == alert["meta"]["snapshot_price"] == 10.72
    assert alert["meta"]["snapshot_pct"] == 7.2 and alert["meta"]["snapshot_as_of"] == snapshot_as_of
    if quote_available:
        quote_as_of = to_beijing(quote.data_timestamp).isoformat()
        assert "距实际涨停价还需上涨 1.85%" in alert["text"]
        assert "距离基准现价 10.80，实际涨停价 11.00" in alert["text"]
        assert f"来源 tencent，源时间 {quote_as_of}" in alert["text"]
        assert alert["meta"]["limit_up_gap_price"] == 10.8 and alert["meta"]["limit_up_price"] == 11.0
        assert alert["meta"]["limit_up_gap_source"] == "tencent"
        assert alert["meta"]["limit_up_gap_as_of"] == quote_as_of
        assert len(alert["text"]) < 500  # source_event事实保留text[:500]，这些报价身份必须在内。
    else:
        assert "距实际涨停价待核对" in alert["text"]
        assert "距离基准现价" not in alert["text"] and "源时间" not in alert["text"]
        assert alert["meta"]["limit_up_gap_state"] == "unknown"
        assert alert["meta"]["limit_up_gap_price"] is None and alert["meta"]["limit_up_gap_as_of"] is None
    if not reopen:
        assert sightings[0]["entry_price"] == 10.72
        assert sightings[0]["reason"]["pct"] == 7.2 and sightings[0]["reason"]["runway_pct"] == 2.5


@pytest.mark.parametrize("failure", [
    "symbol", "market", "previous_day", "future", "stale", "missing_time",
    "quality", "missing_limit", "invalid_price", "above_limit", "mock_source",
])
def test_untrusted_quote_stays_unknown_and_is_negatively_cached(monkeypatch, failure):
    quote = _quote()
    if failure == "symbol": quote.symbol = "600003"
    elif failure == "market": quote.market = "SZ"
    elif failure == "previous_day": quote.data_timestamp -= timedelta(days=1)
    elif failure == "future": quote.data_timestamp += timedelta(seconds=30)
    elif failure == "stale": quote.data_timestamp -= timedelta(seconds=121)
    elif failure == "missing_time": quote.data_timestamp = None
    elif failure == "quality": quote.quality = Quality.medium
    elif failure == "missing_limit": quote.limit_up_price = None
    elif failure == "invalid_price": quote.price = float("nan")
    elif failure == "above_limit": quote.price = 11.01
    elif failure == "mock_source": quote.source = "mock"
    calls = []
    async def fetch(_hub, symbols, **_kwargs):
        calls.append(symbols)
        return {"600002": quote}
    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", fetch)
    async def scenario():
        state, candidate = _state(), _candidate()
        await radar._enrich_candidate_distances(state, [candidate])
        await radar._enrich_candidate_distances(state, [candidate])
        assert candidate["limit_up_gap_state"] == "unknown" and candidate["limit_up_gap_pct"] is None
        assert calls == [["600002"]]
    asyncio.run(scenario())


def test_valid_subscribed_quote_is_reused_without_network(monkeypatch):
    quote = _quote()
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("已有可信完整缓存不应回源")
    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", forbidden)
    candidate = _candidate()
    asyncio.run(radar._enrich_candidate_distances(_state([quote]), [candidate]))
    assert candidate["limit_up_gap_pct"] == 1.85


def test_failed_fetch_retries_after_bounded_negative_cache_expiry(monkeypatch):
    clock = [ttl_cache.monotonic()]
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])
    calls = []
    async def fetch(_hub, symbols, **_kwargs):
        calls.append(symbols)
        return {} if len(calls) == 1 else {"600002": _quote()}
    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", fetch)
    async def scenario():
        state, candidate = _state(), _candidate()
        await radar._enrich_candidate_distances(state, [candidate])
        await radar._enrich_candidate_distances(state, [candidate])
        assert len(calls) == 1 and candidate["limit_up_gap_state"] == "unknown"
        clock[0] += 31
        await radar._enrich_candidate_distances(state, [candidate])
        assert len(calls) == 2 and candidate["limit_up_gap_pct"] == 1.85
    asyncio.run(scenario())


def test_one_batch_at_most_50_candidate_misses_per_30_seconds(monkeypatch):
    calls = []
    async def fetch(_hub, symbols, **_kwargs):
        calls.append(symbols)
        return {symbol: _quote(symbol) for symbol in symbols}
    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", fetch)
    async def scenario():
        state = _state()
        candidates = [_candidate(f"600{i:03}") for i in range(61)]
        await radar._enrich_candidate_distances(state, candidates)
        await radar._enrich_candidate_distances(state, candidates)
        assert calls == [[c["symbol"] for c in candidates[:50]]]
        assert all(c["limit_up_gap_state"] == "ready" for c in candidates[:50])
        assert all(c["limit_up_gap_state"] == "unknown" for c in candidates[50:])
    asyncio.run(scenario())


def test_timeout_keeps_single_real_thread_until_drain_and_shutdown_waits(monkeypatch):
    release = threading.Event()
    jobs = []
    clock = [ttl_cache.monotonic()]
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])
    monkeypatch.setattr(radar, "DISTANCE_FETCH_TIMEOUT", 0.01)
    def worker():
        jobs.append("started")
        release.wait(timeout=2)
        jobs.append("drained")
        return {"600002": _quote()}
    async def fetch(_hub, _symbols, **_kwargs):
        return await asyncio.to_thread(worker)
    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", fetch)
    async def scenario():
        state, candidate = _state(), _candidate()
        shutdown = None
        try:
            await radar._enrich_candidate_distances(state, [candidate])
            task = state._pre_limit_distance_inflight
            assert task is not None and not task.done() and jobs == ["started"]
            clock[0] += 31  # TTL过期也不能把仍在运行的线程当成已取消。
            await radar._enrich_candidate_distances(state, [candidate])
            assert state._pre_limit_distance_inflight is task and jobs == ["started"]
            stop = asyncio.Event()
            stop.set()
            shutdown = asyncio.create_task(radar.pre_limit_loop(SimpleNamespace(state=state), stop))
            await asyncio.sleep(0)
            assert not shutdown.done()
            release.set()
            await shutdown
            assert jobs == ["started", "drained"] and state._pre_limit_distance_inflight is None
            await radar._enrich_candidate_distances(state, [candidate])
            assert candidate["limit_up_gap_pct"] == 1.85 and jobs == ["started", "drained"]
        finally:
            release.set()
            if shutdown is not None: await shutdown
    asyncio.run(scenario())
