"""Subscription generations bind complete frames to their original symbol scope."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient
import pytest

from app.core.auth import ws_encode_token
from app.core.config import settings
from app.schemas.market import Quote
from app.services.quote_hub import QuoteHub, SUBSCRIBER_QUEUE_MAX
from app.websocket.routes import quotes_ws, router


MAX_ID = 2**31 - 1


@pytest.fixture(autouse=True)
def local_posture(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "api_token", "")


def _hub():
    hub = QuoteHub(None, 1, get_watchlist=lambda: [])
    stamp = datetime.now(timezone.utc)
    hub.quotes = {symbol: Quote(symbol=symbol, market="SH", price=10, prev_close=10,
                               source="offline", data_timestamp=stamp)
                  for symbol in ["600519", "600000"]}
    return hub


def _app(hub):
    api = FastAPI()
    api.state.hub = hub
    api.include_router(router)
    return api


def test_hub_queued_quotes_and_stale_keep_each_enqueued_scope_through_aba():
    hub = _hub()
    queue = hub.subscribe({"600519"}, subscription_id=7)
    hub._broadcast("quotes")
    hub.update_symbols(queue, {"600519", "600000"}, subscription_id=8)
    hub._broadcast("stale")
    hub.update_symbols(queue, {"600519"}, subscription_id=9)
    hub._broadcast("quotes")

    frames = [queue.get_nowait() for _ in range(3)]
    assert [frame["subscription_id"] for frame in frames] == [7, 8, 9]
    assert [frame["type"] for frame in frames] == ["quotes", "stale", "quotes"]
    assert [{row["symbol"] for row in frame["data"]} for frame in frames] == [
        {"600519"}, {"600519", "600000"}, {"600519"},
    ]
    assert [frame["seq"] for frame in frames] == [1, 2, 3]
    assert len(hub._subscribers) == 1 and hub._subscribers[0].queue is queue
    assert queue.maxsize == SUBSCRIBER_QUEUE_MAX
    hub.unsubscribe(queue)
    assert not hub._subscribers


@pytest.mark.parametrize("bad", [True, False, -1, MAX_ID + 1, 1.0, "1", None])
def test_hub_rejects_invalid_scope_before_registering_or_mutating(bad):
    hub = _hub()
    with pytest.raises(ValueError, match="subscription_id"):
        hub.subscribe({"600519"}, subscription_id=bad)
    assert not hub._subscribers
    queue = hub.subscribe({"600519"}, subscription_id=4)
    with pytest.raises(ValueError, match="subscription_id"):
        hub.update_symbols(queue, {"600000"}, subscription_id=bad)
    assert hub._subscribers[0].symbols == {"600519"}
    assert hub._subscribers[0].subscription_id == 4 and hub._subscribers[0].queue is queue


def test_actual_route_keeps_legacy_zero_and_labels_an_explicit_empty_snapshot():
    hub = _hub()
    with TestClient(_app(hub)) as client:
        with client.websocket_connect("/ws/quotes?symbols=600519") as socket:
            first = socket.receive_json()
            assert first["subscription_id"] == 0 and first["data"][0]["symbol"] == "600519"
            queue = hub._subscribers[0].queue
            socket.send_json({"action": "subscribe", "symbols": ["600001"]})
            empty = socket.receive_json()
            assert empty["type"] == "snapshot" and empty["subscription_id"] == 0 and empty["data"] == []
            assert hub._subscribers[0].queue is queue
    assert not hub._subscribers


class _GatedSocket:
    def __init__(self, hub):
        self.app = SimpleNamespace(state=SimpleNamespace(hub=hub))
        self.headers = {}
        self.query_params = {"symbols": "600519", "subscription_id": "0"}
        self.inbox = asyncio.Queue()
        self.initial = asyncio.Event()
        self.sending_old = asyncio.Event()
        self.release_old = asyncio.Event()
        self.sent = []

    async def accept(self, subprotocol=None):
        self.accepted_subprotocol = subprotocol

    async def send_json(self, message):
        if message["type"] in {"quotes", "stale"} and not self.sending_old.is_set():
            self.sending_old.set()
            await self.release_old.wait()
        self.sent.append(deepcopy(message))
        self.initial.set()

    async def receive_text(self):
        message = await self.inbox.get()
        if isinstance(message, Exception):
            raise message
        return json.dumps(message)

    async def close(self, code=1000, reason=None):
        self.closed_with = code


async def _until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def test_actual_writer_cannot_relabel_old_queued_frames_after_scope_aba():
    async def run():
        hub = _hub()
        socket = _GatedSocket(hub)
        serving = asyncio.create_task(quotes_ws(socket))
        try:
            await asyncio.wait_for(socket.initial.wait(), 2)
            queue = hub._subscribers[0].queue
            hub._broadcast("quotes")
            await asyncio.wait_for(socket.sending_old.wait(), 2)
            hub._broadcast("quotes")  # This old-scope frame stays queued until after both updates.
            socket.inbox.put_nowait({"action": "subscribe", "symbols": ["600519", "600000"], "subscription_id": 1})
            await _until(lambda: hub._subscribers[0].symbols == {"600519", "600000"})
            socket.inbox.put_nowait({"action": "subscribe", "symbols": ["600519"], "subscription_id": 2})
            await _until(lambda: hub._subscribers[0].symbols == {"600519"})
            assert hub._subscribers[0].queue is queue
            socket.release_old.set()
            await _until(lambda: len(socket.sent) == 5)
            assert [message["subscription_id"] for message in socket.sent] == [0, 0, 0, 1, 2]
            assert [message["seq"] for message in socket.sent] == [1, 2, 3, 4, 5]
            assert [{row["symbol"] for row in message["data"]} for message in socket.sent] == [
                {"600519"}, {"600519"}, {"600519"}, {"600519", "600000"}, {"600519"},
            ]
        finally:
            socket.release_old.set()
            socket.inbox.put_nowait(WebSocketDisconnect(1000))
            await asyncio.wait_for(serving, 2)
            await asyncio.sleep(0)
        assert not hub._subscribers
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())


@pytest.mark.parametrize("bad", ["", "-1", "2147483648", "true", "1.0", "1e2", "１２", "1" * 40])
def test_invalid_initial_scope_is_rejected_before_accept_or_subscription(bad):
    hub = _hub()
    with TestClient(_app(hub)) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(f"/ws/quotes?symbols=600519&subscription_id={bad}"):
                pytest.fail("invalid subscription scope was accepted")
        assert rejected.value.code == 1008
    assert not hub._subscribers


@pytest.mark.parametrize("bad", [True, False, -1, MAX_ID + 1, 1.0, "1", None, 10**40])
def test_invalid_update_closes_without_replacing_existing_scope(bad):
    hub = _hub()
    with TestClient(_app(hub)) as client:
        with client.websocket_connect("/ws/quotes?symbols=600519&subscription_id=4") as socket:
            socket.receive_json()
            subscriber = hub._subscribers[0]
            socket.send_json({"action": "subscribe", "symbols": ["600000"], "subscription_id": bad})
            with pytest.raises(WebSocketDisconnect) as rejected:
                socket.receive_json()
            assert rejected.value.code == 1008
            assert subscriber.symbols == {"600519"} and subscriber.subscription_id == 4
    assert not hub._subscribers


@pytest.mark.parametrize("token", [None, "wrong"])
def test_scope_never_bypasses_existing_ws_credential_check(monkeypatch, token):
    monkeypatch.setattr(settings, "auth_mode", "shared")
    monkeypatch.setattr(settings, "api_token", "isolated-scope-auth")
    hub = _hub()
    protocols = [] if token is None else [f"ashare-token.{ws_encode_token(token)}"]
    with TestClient(_app(hub)) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(f"/ws/quotes?symbols=600519&subscription_id={MAX_ID}", subprotocols=protocols):
                pytest.fail("subscription scope bypassed authentication")
        assert rejected.value.code == 1008
    assert not hub._subscribers


def test_maximum_scope_preserves_accepted_credential_echo(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "shared")
    monkeypatch.setattr(settings, "api_token", "isolated-scope-auth")
    protocol = f"ashare-token.{ws_encode_token('isolated-scope-auth')}"
    hub = _hub()
    with TestClient(_app(hub)) as client:
        with client.websocket_connect(f"/ws/quotes?symbols=600519&subscription_id={MAX_ID}", subprotocols=[protocol]) as socket:
            assert socket.accepted_subprotocol == protocol
            assert socket.receive_json()["subscription_id"] == MAX_ID
    assert not hub._subscribers
