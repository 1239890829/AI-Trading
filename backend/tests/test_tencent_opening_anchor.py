"""Opening-auction rows keep their source label and separate observation clock."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import get_hub
from app.api.routes.market_quotes import router
from app.core.bjtime import BJ_TZ
from app.data_providers import tencent
from app.data_providers.eastmoney import ProviderError
from app.market import minute_backfill
from app.schemas.envelope import MinuteLinePayload


def _payload(symbol="sh600293", *, clock="20261026092519", row="0930 4.50 128804 57961800.00"):
    # Public, same-package fields captured on 2026-10-26; no account identity.
    fields = [""] * 37
    fields[2], fields[3], fields[30], fields[36] = symbol[2:], "4.50", clock, "128804"
    return {"data": {symbol: {"data": {"date": "20261026", "data": [row]}, "qt": {symbol: fields}}}}


def _read(payload, monkeypatch, *, now="2026-10-26T09:25:22+08:00", symbol="600293", status=200):
    monkeypatch.setattr(tencent, "beijing_now", lambda: datetime.fromisoformat(now).astimezone(BJ_TZ))

    async def run():
        provider = tencent.TencentProvider()
        await provider._client.aclose()
        calls = []

        def transport(request):
            calls.append(request.url)
            assert request.url.path == "/appstock/app/minute/query"
            assert request.url.params["code"] == tencent.to_tencent_symbol(symbol)
            return httpx.Response(status, json=payload)

        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        try:
            points = await provider.get_minute_line(symbol)
            assert len(calls) == 1  # Never obtain proof from a second quote request.
            return points
        finally:
            await provider.aclose()

    return asyncio.run(run())


@pytest.mark.parametrize(("row", "source_clock", "now", "cum_volume", "cum_amount"), [
    ("0930 4.50 0 0.00", "20261026092100", "2026-10-26T09:21:30+08:00", 0, 0),
    ("0930 4.50 128804 57961800.00", "20261026092519", "2026-10-26T09:25:22+08:00", 12880400, 57961800),
    # A delayed packet keeps its identity after 09:30, rather than becoming a live minute.
    ("0930 4.50 128804 57961800.00", "20261026092519", "2026-10-26T09:30:02+08:00", 12880400, 57961800),
])
def test_same_package_opening_anchor_preserves_price_quantity_and_label(monkeypatch, row, source_clock, now,
                                                                       cum_volume, cum_amount):
    payload = _payload(clock=source_clock, row=row)
    payload["data"]["sh600293"]["qt"]["sh600293"][36] = str(cum_volume // 100)
    original = deepcopy(payload)
    points = _read(payload, monkeypatch, now=now)
    assert len(points) == 1
    point = points[0]
    assert point["time_label"] == "opening_anchor"
    assert point["observation_ts"] == datetime.strptime(source_clock, "%Y%m%d%H%M%S").replace(
        tzinfo=BJ_TZ).astimezone(timezone.utc).isoformat()
    assert point["received_at"] == datetime.fromisoformat(now).astimezone(timezone.utc).isoformat()
    assert point["ts"] == "2026-10-26T01:30:00+00:00"
    assert point["price"] == 4.5
    assert point["cum_volume"] == cum_volume
    assert point["cum_amount"] == cum_amount
    assert point["volume"] == (cum_volume or None)
    assert payload == original
    assert "quality" not in point  # Metadata does not grant market-quality eligibility.


def test_index_auction_anchor_does_not_assume_quote_and_minute_volume_equal(monkeypatch):
    payload = _payload("sh000001", clock="20261026092506", row="0930 3671.00 0 0.00")
    fields = payload["data"]["sh000001"]["qt"]["sh000001"]
    fields[3], fields[36] = "3671.00", "4371035"
    point = _read(payload, monkeypatch, symbol="sh000001")[0]
    assert point["time_label"] == "opening_anchor"
    assert point["observation_ts"] == "2026-10-26T01:25:06+00:00"
    assert point["cum_volume"] == 0 and point["cum_amount"] == 0 and point["avg"] is None


@pytest.mark.parametrize("case", [
    "missing_qt", "short_qt", "wrong_key", "wrong_identity", "wrong_price", "wrong_volume",
    "missing_volume", "bad_clock", "bad_clock_date", "numeric_clock", "future_clock", "before_auction",
    "continuous_clock", "closing_clock", "cross_day_clock", "cross_day_receive", "missing_trade_date",
    "wrong_label", "multiple_points", "bad_amount", "bad_price", "bad_volume",
])
def test_unproven_rows_do_not_gain_opening_anchor_exception(monkeypatch, case):
    payload = _payload()
    node = payload["data"]["sh600293"]
    fields = node["qt"]["sh600293"]
    now = "2026-10-26T09:25:22+08:00"
    if case == "missing_qt":
        node.pop("qt")
    elif case == "short_qt":
        node["qt"]["sh600293"] = fields[:30]
    elif case == "wrong_key":
        node["qt"] = {"sh600519": fields}
    elif case == "missing_trade_date":
        node["data"].pop("date")
    elif case == "cross_day_receive":
        now = "2026-10-27T09:25:22+08:00"
    elif case in {"wrong_label", "multiple_points", "bad_amount", "bad_price", "bad_volume"}:
        node["data"]["data"] = {
            "wrong_label": ["0931 4.50 128804 57961800.00"],
            "multiple_points": ["0930 4.50 128804 57961800.00", "0931 4.50 128804 57961800.00"],
            "bad_amount": ["0930 4.50 128804 nan"],
            "bad_price": ["0930 nan 128804 57961800.00"],
            "bad_volume": ["0930 4.50 nan 57961800.00"],
        }[case]
    else:
        index, value = {
            "wrong_identity": (2, "600519"), "wrong_price": (3, "4.51"), "wrong_volume": (36, "128805"),
            "missing_volume": (36, ""), "bad_clock": (30, "invalid"), "bad_clock_date": (30, "20261326092519"),
            "numeric_clock": (30, 20261026092519), "future_clock": (30, "20261026092523"),
            "before_auction": (30, "20261026091459"), "continuous_clock": (30, "20261026093000"),
            "closing_clock": (30, "20261026145700"), "cross_day_clock": (30, "20261023092519"),
        }[case]
        fields[index] = value
    points = _read(payload, monkeypatch, now=now)
    assert all(point.get("time_label") != "opening_anchor" for point in points)
    assert all("received_at" not in point for point in points)


def test_source_http_failure_is_not_converted_to_auction_waiting(monkeypatch):
    with pytest.raises(ProviderError, match="HTTP 503"):
        _read(_payload(), monkeypatch, status=503)


def test_minute_endpoint_preserves_opening_anchor_metadata(monkeypatch):
    payload = _payload()
    now = datetime.fromisoformat("2026-10-26T09:25:22+08:00")
    monkeypatch.setattr(tencent, "beijing_now", lambda: now)
    monkeypatch.setattr(minute_backfill, "load_vr_baseline", lambda _symbol: None)

    async def read():
        provider = tencent.TencentProvider()
        await provider._client.aclose()
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
        hub = SimpleNamespace(provider=provider, last_success_refresh=None, is_stale=lambda: False)
        api = FastAPI()
        api.include_router(router, prefix="/api")
        api.dependency_overrides[get_hub] = lambda: hub
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://isolated") as client:
                response = await client.get("/api/minute-line/600293")
            assert response.status_code == 200
            point = response.json()["data"]["points"][0]
            assert point["time_label"] == "opening_anchor"
            assert point["observation_ts"] == "2026-10-26T01:25:19+00:00"
            assert point["received_at"] == "2026-10-26T01:25:22+00:00"
            assert MinuteLinePayload.model_validate(response.json()["data"]).points[0].time_label == "opening_anchor"
        finally:
            await provider.aclose()

    asyncio.run(read())
