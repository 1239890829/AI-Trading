from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.quote_hub import offer

log = logging.getLogger(__name__)
router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_task_failure(exc: BaseException) -> None:
    """分流两条任务的收尾异常：正常断开/发送竞态不吵，其余必须留痕。

    旧实现用 `except (WebSocketDisconnect, RuntimeError): pass` 把**所有**异常
    一并吞掉，且 reader 的异常对象只挂在 task 上、无人取用 —— "静默失败"正是
    2026-09-01 那次「推送死亡而 ping/pong 还活着、前端误判连接健康」的形态。
    现在只有这两类预期内异常安静，其它一律 WARNING（带栈）。
    """
    if isinstance(exc, (asyncio.CancelledError, WebSocketDisconnect, RuntimeError)):
        return
    log.warning("行情 WebSocket 任务异常退出：%r", exc, exc_info=exc)


def _report_task_failures(fut: "asyncio.Future") -> None:
    """在 done-callback 里分流两条任务的收尾异常。

    为什么走回调而不是 `await asyncio.gather(...)`：收尾路径**绝不能含 await**。
    本函数可能正被**外部取消**（生产侧客户端断开、以及 Starlette TestClient 在
    会话退出时都用 CancelScope 取消 app task）；而任务被取消后，`finally` 里的
    任何 `await` 都会在首个挂起点立刻重抛 CancelledError —— 后面的清理代码
    （含 `hub.unsubscribe`）会被**整段跳过**。实测：写成 await 版本时 5/6 个
    生命周期用例在会话退出处抛 `CancelledError`，且订阅实际未被注销。
    """
    if fut.cancelled():
        return
    for exc in fut.result():
        if isinstance(exc, BaseException):
            _log_task_failure(exc)


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
                offer(slot[0], {"type": "pong", "ts": _now_iso()})
            elif msg.get("action") == "subscribe":
                new_syms = msg.get("symbols") or []
                symbols = {s for s in new_syms if s} or None
                # ⚠️ 2026-09-01 P0：原地更新订阅集（hub.update_symbols）——
                # 旧实现 unsubscribe + subscribe 换新队列，而 writer 正 parked
                # 在旧队列的 get() 上：换队列后 writer 永远等在孤儿队列，推送
                # 静默死亡（前端 32s 心跳自愈重连才见一次快照 = 用户体感
                # "约 30 秒才更新一次"的根因）。同一队列终身复用，竞态不存在。
                hub.update_symbols(slot[0], symbols)
                offer(slot[0], _snapshot())

    async def writer() -> None:
        # 唯一出站发送点：hub 推送 / pong / subscribe 快照全部经队列串行化，
        # 从根上消灭并发 send。
        while True:
            msg = await slot[0].get()
            await websocket.send_json(msg)

    reader_task = asyncio.create_task(reader())
    writer_task = asyncio.create_task(writer())
    try:
        # ⚠️ 2026-09-14 R24：两条任务的生命周期必须**互相绑定**（任一侧结束即整体收尾）。
        # 旧实现只 `await writer()`：reader 先死（客户端断开或意外异常）时无人察觉 ⇒
        # writer 继续 parked 在 `queue.get()` 上、`hub.unsubscribe` **永不执行**，
        # 该订阅成为孤儿 —— hub 仍按 1Hz 往里投递而无人 drain，配合无界队列即
        # 内存无限增长；若 hub 恰好不推送（休市 / 无匹配标的），连 writer 都不会醒，
        # 这对任务就永久挂着。且 reader 的异常从未被取用。
        await asyncio.wait({reader_task, writer_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (reader_task, writer_task):
            t.cancel()
        # ⚠️ 收尾路径**不得 await**（理由见 `_report_task_failures` 的 docstring）：
        # 被外部取消时 await 会立刻重抛，`hub.unsubscribe` 就会漏执行——那正是
        # 孤儿订阅的成因。异常交给 done-callback 取用（既避免
        # "Task exception was never retrieved"，也把非预期异常留痕）。
        asyncio.gather(reader_task, writer_task, return_exceptions=True).add_done_callback(
            _report_task_failures
        )
        hub.unsubscribe(slot[0])
