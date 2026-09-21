"""首次返回完整性：期望身份独立于生产目录，HTTP/WS 不臆造缺席报价。"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_hub
from app.api.routes.health import liveness_router
from app.api.routes.market_themes import market_overview
from app.data_providers.eastmoney import INDEX_SECIDS as EASTMONEY_INDICES
from app.data_providers.mock import INDEX_BASES
from app.data_providers.tencent import INDEX_SECIDS as TENCENT_INDICES
from app.data_providers.ths import ThsFuyaoProvider
from app.schemas.market import Quality, Quote, utcnow
from app.services import quote_hub as qh
from app.websocket.routes import router as ws_router


EXPECTED = [
    ("000001", "SH"), ("399001", "SZ"), ("399006", "SZ"),
    ("000688", "SH"), ("000300", "SH"), ("000852", "SH"),
]


def rows():
    return [Quote(symbol=symbol, market=market, price=3000 + i, prev_close=3000 + i,
                  data_timestamp=utcnow(), source="isolated-test")
            for i, (symbol, market) in enumerate(EXPECTED)]


@pytest.fixture
def hub(monkeypatch):
    async def no_calendar(*args, **kwargs):
        return []

    monkeypatch.setattr(qh.tc, "trading_days", no_calendar)
    monkeypatch.setattr(qh.tc, "in_trading_window", lambda: True)

    class Provider:
        name = "isolated-test"
        realtime = True

        def __init__(self):
            self.rows = rows()

        async def get_indices(self):
            return [q.model_copy(deep=True) for q in self.rows]

    return qh.QuoteHub(Provider(), poll_interval=1)


def refresh(hub):
    asyncio.run(hub.refresh())


def overview(hub):
    return asyncio.run(market_overview(SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace())), hub))


def test_provider_request_catalogs_keep_the_existing_six_market_identities():
    assert [(code[4:], code[2:4].upper()) for code in TENCENT_INDICES] == EXPECTED
    assert [(code[2:], "SH" if code[0] == "1" else "SZ")
            for code, _ in EASTMONEY_INDICES] == EXPECTED
    assert [(code[:6], code[-2:]) for code in ThsFuyaoProvider.INDEX_NAMES] == EXPECTED
    assert [(code, market) for code, market, _, _ in INDEX_BASES] == EXPECTED


def test_unattempted_batch_is_unknown_not_complete_or_empty(hub):
    assert overview(hub)["meta"]["index_batch"] == {
        "expected_count": 6, "coverage": None, "missing_symbols": [],
    }


@pytest.mark.parametrize("count", [0, 1, 5, 6])
def test_first_reply_coverage_uses_requested_indices_without_fabricating(hub, count):
    hub.provider.rows = rows()[:count]
    refresh(hub)
    result = overview(hub)
    missing = sorted(symbol for symbol, _ in EXPECTED[count:])
    assert result["meta"]["index_batch"] == {
        "expected_count": 6, "coverage": count / 6, "missing_symbols": missing,
    }
    assert [q["symbol"] for q in result["data"]["indices"]] == [s for s, _ in EXPECTED[:count]]
    assert hub.last_missing_indices == missing
    assert all(q.quality == Quality.high for q in hub.get_indices())


@pytest.mark.parametrize("bad", ["wrong_market", "unknown_symbol", "unknown_without_market", "duplicate"])
def test_wrong_or_ambiguous_identity_cannot_fill_the_missing_index(hub, bad):
    valid = rows()
    if bad == "duplicate":
        hub.provider.rows = valid + [valid[0].model_copy(update={"price": 10})]
    else:
        replacement = {"market": "SZ"} if bad == "wrong_market" else {"symbol": "999999"}
        if bad == "unknown_without_market":
            replacement["market"] = None
        hub.provider.rows = [valid[0].model_copy(update=replacement), *valid[1:]]
    refresh(hub)
    assert hub.last_missing_indices == ["000001"]
    assert "000001" not in hub.indices
    assert "999999" not in hub.indices
    assert overview(hub)["meta"]["index_batch"]["coverage"] == 5 / 6
    expected_reason = {
        "duplicate": "source_identity_duplicate_ignored",
        "wrong_market": "source_identity_market_mismatch_ignored",
        "unknown_symbol": "source_identity_unexpected_ignored",
        "unknown_without_market": "source_identity_unexpected_ignored",
    }[bad]
    assert hub.source_rejections()["indices"] == {
        "count": 1, "reasons": {expected_reason: 1},
    }


def test_first_gap_recovers_and_cached_gap_keeps_original_timestamp(hub):
    hub.provider.rows = rows()[1:]
    refresh(hub)
    assert hub.last_missing_indices == ["000001"]
    hub.provider.rows = rows()
    refresh(hub)
    before = hub.indices["000001"].model_dump()
    assert overview(hub)["meta"]["index_batch"]["coverage"] == 1
    hub.provider.rows = rows()[1:]
    refresh(hub)
    after = hub.indices["000001"]
    assert after.price == before["price"]
    assert after.data_timestamp == before["data_timestamp"]
    assert after.received_at == before["received_at"]
    assert after.quality_reasons == ["index_batch_missing"]
    assert after.freshness().state == "stale"
    assert hub.freshness().state == "ready"  # 时间新鲜度与完整性是不同维度


def test_http_health_is_degraded_for_incomplete_batch_and_recovers(hub):
    app = FastAPI()
    app.include_router(liveness_router, prefix="/api")
    app.dependency_overrides[get_hub] = lambda: hub
    with TestClient(app) as client:
        hub.provider.rows = rows()[1:]
        refresh(hub)
        body = client.get("/api/health").json()
        assert body["status"] == "degraded"
        assert body["is_stale"] is False
        assert body["index_batch"] == overview(hub)["meta"]["index_batch"]
        hub.provider.rows = rows()
        refresh(hub)
        assert client.get("/api/health").json()["status"] == "ok"


def test_http_health_includes_market_snapshot_and_degrades_on_durable_failure(hub):
    app = FastAPI()
    app.include_router(liveness_router, prefix="/api")
    app.dependency_overrides[get_hub] = lambda: hub
    snapshot = {
        "freshness": {"state": "ready"},
        "consecutive_save_failures": 1,
        "last_save_error": "disk-full-test",
    }
    app.state.snapshot_service = SimpleNamespace(breadth_payload=lambda: snapshot)
    with TestClient(app) as client:
        refresh(hub)
        body = client.get("/api/health").json()
        assert body["status"] == "degraded"
        assert body["market_snapshot"]["last_save_error"] == "disk-full-test"
        snapshot["consecutive_save_failures"] = 0
        snapshot["last_save_error"] = None
        assert client.get("/api/health").json()["status"] == "ok"


def test_rejected_index_is_visible_in_rest_health_and_ws_snapshot(hub):
    refresh(hub)
    bad = rows()
    bad[0].data_timestamp = utcnow() + timedelta(hours=1)
    hub.provider.rows = bad
    refresh(hub)

    expected = {
        "quotes": {"count": 0, "reasons": {}},
        "indices": {"count": 1, "reasons": {"source_invalid_ignored": 1}},
    }
    assert hub.source_rejections() == expected
    assert overview(hub)["meta"]["source_rejections"] == expected

    app = FastAPI()
    app.state.hub = hub
    app.include_router(liveness_router, prefix="/api")
    app.include_router(ws_router)
    app.dependency_overrides[get_hub] = lambda: hub
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "degraded"
        assert health["source_rejections"] == expected
        with client.websocket_connect("/ws/quotes?symbols=sh000001") as ws:
            frame = ws.receive_json()
            assert frame["meta"]["source_rejections"] == expected


@pytest.mark.parametrize("prefix", ["sh", "SH"])
@pytest.mark.parametrize("count", [0, 1])
def test_subscription_reports_cold_gap_even_without_any_quote(hub, count, prefix):
    hub.provider.rows = rows()[1:1 + count]
    queue = hub.subscribe({prefix + "000001"})
    refresh(hub)
    assert not queue.empty()
    frame = queue.get_nowait()
    assert frame["data"] == []
    assert frame["meta"]["index_batch"] == overview(hub)["meta"]["index_batch"]


def test_ws_initial_and_resubscribe_snapshots_use_same_batch_evidence(hub):
    hub.provider.rows = rows()[1:]
    refresh(hub)
    app = FastAPI()
    app.state.hub = hub
    app.include_router(ws_router)
    with TestClient(app) as client, client.websocket_connect("/ws/quotes?symbols=sh000001") as ws:
        first = ws.receive_json()
        assert first["data"] == []
        assert first["meta"]["index_batch"] == overview(hub)["meta"]["index_batch"]
        ws.send_json({"action": "subscribe", "symbols": ["sz399001"]})
        second = ws.receive_json()
        assert second["data"][0]["symbol"] == "sz399001"
        assert second["meta"]["index_batch"] == first["meta"]["index_batch"]


def test_failed_request_does_not_erase_last_completed_batch_evidence(hub):
    hub.provider.rows = rows()[1:]
    refresh(hub)
    before = overview(hub)["meta"]["index_batch"]

    async def fail():
        raise RuntimeError("isolated outage")

    hub.provider.get_indices = fail
    refresh(hub)
    assert hub.consecutive_failures == 1
    assert overview(hub)["meta"]["index_batch"] == before


@pytest.mark.parametrize("symbol,market", EXPECTED)
def test_query_market_cannot_alias_another_markets_index(hub, symbol, market):
    refresh(hub)
    wrong_market = "sz" if market == "SH" else "sh"
    assert hub.get_quotes([wrong_market + symbol]) == []
    assert hub.get_quotes(["bj" + symbol]) == []
    actual = hub.get_quotes([market.lower() + symbol])
    assert len(actual) == 1
    assert actual[0].market == market


def test_unknown_cached_market_is_not_guessed_from_the_query(hub):
    hub.indices["000001"] = rows()[0].model_copy(update={"market": None})
    assert hub.get_quotes(["sh000001", "sz000001", "bj000001"]) == []
