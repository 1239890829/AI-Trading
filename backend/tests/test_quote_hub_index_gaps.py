"""指数缓存漏返回：独立回归，不把成功请求当作每项都已刷新。"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.api.routes.market_themes import market_overview
from app.schemas.market import Quality, Quote, utcnow
from app.services import quote_hub as qh


def quote(symbol: str, price: float) -> Quote:
    return Quote(symbol=symbol, market="SH", price=price, prev_close=price,
                 change_pct=0, data_timestamp=utcnow(), source="isolated-test")


class Provider:
    name = "isolated-test"
    realtime = True

    def __init__(self):
        self.indices = [quote("000001", 3000), quote("000688", 1000)]

    async def get_indices(self):
        return [q.model_copy(deep=True) for q in self.indices]


@pytest.fixture
def hub(monkeypatch):
    async def no_calendar(*args, **kwargs):
        return []

    monkeypatch.setattr(qh.tc, "trading_days", no_calendar)
    monkeypatch.setattr(qh.tc, "in_trading_window", lambda: True)
    return qh.QuoteHub(Provider(), poll_interval=1)


def refresh(hub):
    asyncio.run(hub.refresh())


def test_missing_cached_index_preserves_value_and_source_time_but_is_stale(hub):
    refresh(hub)
    old = hub.indices["000001"].model_dump()
    hub.provider.indices = [quote("000688", 1001)]
    refresh(hub)
    missing = hub.indices["000001"]
    assert missing.quality == Quality.stale
    assert missing.quality_reasons == ["index_batch_missing"]
    assert missing.freshness().state == "stale"
    assert missing.price == old["price"]
    assert missing.data_timestamp == old["data_timestamp"]
    assert missing.received_at == old["received_at"]
    assert hub.indices["000688"].quality == Quality.high
    assert hub.indices["000688"].price == 1001
    assert hub.last_missing_indices == ["000001", "000300", "000852", "399001", "399006"]
    assert hub.freshness().state == "ready"  # 逐项披露，不误伤成功的数据


def test_empty_index_reply_marks_all_cached_indices_stale(hub):
    refresh(hub)
    hub.provider.indices = []
    refresh(hub)
    assert hub.last_missing_indices == ["000001", "000300", "000688", "000852", "399001", "399006"]
    assert all(q.quality == Quality.stale for q in hub.get_indices())


def test_unseen_indices_are_not_fabricated(hub):
    hub.provider.indices = [quote("000688", 1000)]
    refresh(hub)
    assert [q.symbol for q in hub.get_indices()] == ["000688"]
    assert hub.get_quotes(["sh000001"]) == []


def test_index_gap_recovers_when_provider_returns(hub):
    refresh(hub)
    hub.provider.indices = [quote("000688", 1001)]
    refresh(hub)
    assert hub.indices["000001"].quality == Quality.stale
    hub.provider.indices = [quote("000001", 3001), quote("000688", 1001)]
    refresh(hub)
    assert hub.last_missing_indices == ["000300", "000852", "399001", "399006"]
    assert hub.indices["000001"].quality == Quality.high
    assert hub.indices["000001"].price == 3001


def test_late_index_observation_keeps_newer_value_and_marks_degraded(hub):
    refresh(hub)
    current = hub.indices["000001"].model_copy(deep=True)
    late = quote("000001", 2999)
    late.data_timestamp = current.data_timestamp - timedelta(seconds=5)
    hub.provider.indices = [late, quote("000688", 1001)]

    refresh(hub)

    kept = hub.indices["000001"]
    assert kept.price == current.price
    assert kept.data_timestamp == current.data_timestamp
    assert kept.quality == Quality.stale
    assert kept.quality_reasons == ["source_time_regress_ignored"]
    assert kept.freshness(fresh_within=600).state == "stale"
    assert "000001" in hub.last_missing_indices


def test_index_gap_logs_only_when_missing_set_changes(hub, caplog):
    refresh(hub)
    hub.provider.indices = []
    refresh(hub)
    first = len(caplog.records)
    assert first > 0
    refresh(hub)
    assert len(caplog.records) == first


def test_confirmed_closed_reason_overrides_index_gap(hub, monkeypatch):
    async def calendar(*args, **kwargs):
        return [qh.beijing_now().date()]

    refresh(hub)
    monkeypatch.setattr(qh.tc, "trading_days", calendar)
    monkeypatch.setattr(qh.tc, "market_open_state", lambda *args: qh.tc.MARKET_CLOSED)
    hub.provider.indices = []
    refresh(hub)
    assert hub.indices["000001"].quality_reasons == ["market_closed"]
    assert hub.last_missing_indices == ["000001", "000300", "000688", "000852", "399001", "399006"]


def test_market_overview_preserves_per_index_stale_status(hub):
    refresh(hub)
    hub.provider.indices = [quote("000688", 1001)]
    refresh(hub)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    result = asyncio.run(market_overview(request, hub))
    indices = {q["symbol"]: q for q in result["data"]["indices"]}
    assert indices["000001"]["quality"] == "stale"
    assert indices["000688"]["quality"] == "high"


def test_subscriber_receives_stale_index_without_aliasing_bare_stock(hub):
    refresh(hub)
    queue = hub.subscribe({"sh000001", "000001"})
    hub.quotes["000001"] = quote("000001", 10)

    async def stocks(symbols):
        return [quote("000001", 11)]

    hub.provider.get_quotes = stocks
    hub.provider.indices = [quote("000688", 1001)]
    refresh(hub)
    payload = {q["symbol"]: q for q in queue.get_nowait()["data"]}
    assert payload["sh000001"]["quality"] == "stale"
    assert payload["sh000001"]["price"] == 3000
    assert payload["000001"]["quality"] == "high"
    assert payload["000001"]["price"] == 11
