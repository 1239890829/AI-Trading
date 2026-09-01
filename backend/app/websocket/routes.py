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
                # ⚠️ 2026-09-01 P0 修复：pong 改走出站队列——原实现 reader 直接
                # await websocket.send_json(pong)，与 writer task 的推送**并发写同一
                # WebSocket**，Starlette 不允许并发 send → writer 抛 RuntimeError 被
                # except 静默吞掉 → 推送永久死亡，而 ping/pong 还活着，前端误以为
                # 连接健康（实测盘中自选列表/详情/图全部冻结在初始值）。
                slot[0].put_nowait({"type": "pong", "ts": _now_iso()})
            elif msg.get("action") == "subscribe":
                new_syms = msg.get("symbols") or []
                symbols = {s for s in new_syms if s} or None
                # ⚠️ 2026-09-01 P0：原地更新订阅集（hub.update_symbols）——
                # 旧实现 unsubscribe + subscribe 换新队列，而 writer 正 parked
                # 在旧队列的 get() 上：换队列后 writer 永远等在孤儿队列，推送
                # 静默死亡（前端 32s 心跳自愈重连才见一次快照 = 用户体感
                # "约 30 秒才更新一次"的根因）。同一队列终身复用，竞态不存在。
                hub.update_symbols(slot[0], symbols)
                slot[0].put_nowait(_snapshot())

    async def writer() -> None:
        # 唯一出站发送点：hub 推送 / pong / subscribe 快照全部经队列串行化，
        # 从根上消灭并发 send。
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
