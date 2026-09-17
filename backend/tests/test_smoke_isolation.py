"""The offline smoke fixture must block real I/O, not bypass app assembly."""
import asyncio
import socket

import httpx
import pytest
import requests

from app.market import board_flow, fund_flow, tdx_kline
from tests.offline_smoke import isolate_sources


@pytest.mark.parametrize("kind", ["httpx", "httpx_async", "requests"])
def test_http_stops_before_socket(monkeypatch, kind):
    unexpected = isolate_sources(monkeypatch)
    url = "https://offline-smoke.invalid"
    if kind == "httpx":
        with httpx.Client() as client, pytest.raises(httpx.ConnectError):
            client.get(url)
    elif kind == "httpx_async":
        async def run():
            async with httpx.AsyncClient() as client:
                await client.get(url)
        with pytest.raises(httpx.ConnectError):
            asyncio.run(run())
    else:
        with requests.Session() as client, pytest.raises(requests.ConnectionError):
            client.get(url)
    assert not unexpected


@pytest.mark.parametrize("kind", ["dns", "connect", "connect_ex"])
def test_unmocked_network_is_recorded_before_io(monkeypatch, kind):
    unexpected = isolate_sources(monkeypatch)
    with socket.socket() as sock, pytest.raises(OSError, match="unmocked"):
        if kind == "dns":
            socket.getaddrinfo("offline-smoke.invalid", 443)
        else:
            getattr(sock, kind)(("192.0.2.1", 443))
    assert len(unexpected) == 1


def test_retry_wait_is_local_and_still_yields(monkeypatch):
    real_sleep = asyncio.sleep
    delays = []

    async def record_sleep(delay, result=None):
        delays.append(delay)
        return await real_sleep(0, result)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    isolate_sources(monkeypatch)
    assert asyncio.sleep is record_sleep

    async def run():
        seen = []
        asyncio.get_running_loop().call_soon(seen.append, "scheduled")
        assert await fund_flow.asyncio.sleep(999, "result") == "result"
        assert seen == ["scheduled"]
        await board_flow.asyncio.sleep(999)
    asyncio.run(run())
    assert delays == [0, 0]


def test_tdx_stops_at_external_fetch(monkeypatch):
    unexpected = isolate_sources(monkeypatch)
    with pytest.raises(OSError, match="TDX unavailable"):
        tdx_kline._fetch_daily_bars("600519", 30)
    assert not unexpected
