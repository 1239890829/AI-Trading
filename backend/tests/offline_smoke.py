"""External I/O boundaries for the real-app smoke suite, never production settings."""
from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import httpx
import requests

from app.market import board_flow, fund_flow, tdx_kline


def isolate_sources(monkeypatch, *, block_raw_sockets: bool = True):
    """Keep route/service/retry code real; fail at external transport boundaries.

    Return unexpected socket attempts so callers can fail even when application
    fallback code catches the OSError. TestClient's in-process transport is intact.
    """
    unexpected = []

    def blocked_socket(*args, **kwargs):
        unexpected.append("unmocked socket operation")
        raise OSError("offline smoke: unmocked socket operation")

    def http_failure(self, request):
        raise httpx.ConnectError("offline smoke: source unavailable", request=request)

    async def async_http_failure(self, request):
        return http_failure(self, request)

    def requests_failure(self, request, **kwargs):
        raise requests.ConnectionError("offline smoke: source unavailable", request=request)

    def tdx_failure(*args, **kwargs):
        raise OSError("offline smoke: TDX unavailable")

    async def yield_without_delay(delay, result=None):
        return await asyncio.sleep(0, result)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", http_failure)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_http_failure)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", requests_failure)
    monkeypatch.setattr(tdx_kline, "_fetch_daily_bars", tdx_failure)
    for module in (fund_flow, board_flow):
        # Replace only this module's reference, not the shared asyncio.sleep.
        monkeypatch.setattr(module, "asyncio", SimpleNamespace(
            **{**vars(asyncio), "sleep": yield_without_delay},
        ))
    if block_raw_sockets:
        monkeypatch.setattr(socket, "getaddrinfo", blocked_socket)
        monkeypatch.setattr(socket.socket, "connect", blocked_socket)
        monkeypatch.setattr(socket.socket, "connect_ex", blocked_socket)
    return unexpected
