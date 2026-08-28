from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)
router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.websocket("/ws/quotes")
async def quotes_ws(websocket: WebSocket):
    """行情推送。

    - 连接参数 ?symbols=600519,000001 指定订阅，缺省为全部自选
    - 服务端消息：{"type":"snapshot"|"quotes"|"stale","seq":n,"ts":iso,"data":[Quote...]}
    - 客户端消息：{"action":"ping"} → {"type":"pong"}；
      {"action":"subscribe","symbols":[...]} 更新本连接的订阅集（会回推新快照）
    """
    await websocket.accept()
    hub = websocket.app.state.hub

    raw = websocket.query_params.get("symbols", "")
    symbols: set[str] | None = {s.strip() for s in raw.split(",") if s.strip()} or None

    slot: list[asyncio.Queue] = [hub.subscribe(symbols)]

    def _snapshot() -> dict:
        return {
            "type": "snapshot",
            "seq": hub.next_seq(),
            "ts": _now_iso(),
            "data": [q.model_dump(mode="json") for q in hub.get_quotes(sorted(symbols) if symbols else None)],
        }

    try:
        await websocket.send_json(_snapshot())
    except Exception:
        hub.unsubscribe(slot[0])
        return

    async def reader() -> None:
        nonlocal symbols
        while True:
            raw_msg = await websocket.receive_text()
            try:
                msg = json.loads(raw_msg)
            except ValueError:
                continue
            if msg.get("action") == "ping":
                await websocket.send_json({"type": "pong", "ts": _now_iso()})
            elif msg.get("action") == "subscribe":
                new_syms = msg.get("symbols") or []
                symbols = {s for s in new_syms if s} or None
                hub.unsubscribe(slot[0])
                slot[0] = hub.subscribe(symbols)
                slot[0].put_nowait(_snapshot())

    async def writer() -> None:
        while True:
            msg = await slot[0].get()
            await websocket.send_json(msg)

    reader_task = asyncio.create_task(reader())
    try:
        await writer()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader_task.cancel()
        hub.unsubscribe(slot[0])
