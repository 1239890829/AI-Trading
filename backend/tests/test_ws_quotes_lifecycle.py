"""行情 WebSocket 连接的**生命周期绑定**（R24，2026-09-14）。

旧实现把两条任务的生命周期绑在**一条**上：`await writer()`。于是 reader 先结束
（客户端正常断开、或意外异常）时无人察觉 ⇒ writer 继续 parked 在 `queue.get()`
上、`hub.unsubscribe` **永不执行**：

- 该订阅成为**孤儿**：hub 仍按 1Hz 往队列投递而无人 drain（配合当时无界的
  `asyncio.Queue()` 即内存无上限增长）；
- 若 hub 恰好不推送（休市 / 无匹配标的 / 空载荷），writer 连醒都不会醒，
  这对任务**永久挂着**，`reader_task` 的异常也从未被取用。

本文件用**最小 FastAPI app + 假 hub** 验证「任一侧结束都必须收尾并注销订阅」。
刻意不引真 `app.main`：那要进一次 lifespan（本机实测 35–46s），而这里要验的是
路由层的任务编排，与 Hub 的真实实现无关（那条链路由 `test_quote_hub*.py` 覆盖）。

⚠️ **两种驱动形态职责不同，不可互相替代**（2026-09-14 注入验证实测）：

- `TestClient` 形态（多数用例）：验端到端契约。但它**不是**「reader 单独结束被
  感知」的判据 —— `WebSocketTestSession.__enter__` 把 `close(1000)`、`cs.cancel()`、
  `fut.result()` 依次压入 ExitStack，会话退出时**外部取消**会顺带把同步清理跑掉，
  于是旧实现 `await writer_task` 在这类用例下**仍然全绿**（已实测）。
- 直接驱动协程形态（`test_reader_exit_alone_completes_cleanup_without_external_cancel`）：
  唯一能钉住「收尾必须**自行**发生」的形态。二者都要有。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from app.services.quote_hub import SUBSCRIBER_QUEUE_MAX
from app.websocket.routes import quotes_ws
from app.websocket.routes import router as ws_router


class FakeHub:
    """只实现路由用到的 5 个方法，并记录订阅/注销。

    完全**不推送**任何东西——这正是旧实现的致命场景：没有推送，writer 永远
    parked，孤儿订阅不可自愈。
    """

    def __init__(self) -> None:
        self.subscribed: list[asyncio.Queue] = []
        self.unsubscribed: list[asyncio.Queue] = []
        self.unsubscribed_evt = threading.Event()
        self._seq = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def get_quotes(self, symbols: list[str] | None = None) -> list:
        return []

    def subscribe(self, symbols: set[str] | None = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        self.subscribed.append(q)
        return q

    def update_symbols(self, queue: asyncio.Queue, symbols: set[str] | None) -> bool:
        return queue in self.subscribed

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.unsubscribed.append(queue)
        self.unsubscribed_evt.set()


def _app(hub: FakeHub) -> FastAPI:
    app = FastAPI()
    app.include_router(ws_router)
    app.state.hub = hub
    return app


class _FakeWS:
    """最小 WebSocket 替身：**直接驱动路由协程**，不经过 Starlette 会话。

    存在的理由见模块 docstring：`TestClient` 的会话退出会用 CancelScope 取消
    app task，那条路径会顺手把同步清理跑掉，使「reader 单独结束是否被感知」
    变得不可观测。这里把两件事彻底分开 —— 没有外部取消，路由要么**自行**收尾
    返回，要么永久挂住。
    """

    def __init__(self, hub: FakeHub, symbols: str = "600519") -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(hub=hub))
        self.query_params = {"symbols": symbols}
        self.sent: list[dict] = []

    async def accept(self) -> None:
        return None

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)

    async def receive_text(self) -> str:
        # 客户端断开：reader 立刻结束，writer 则永久 parked 在 queue.get() 上
        # （FakeHub 从不推送）—— 这正是旧实现失效的场景。
        raise WebSocketDisconnect(code=1000)


def test_client_disconnect_unsubscribes_even_when_hub_never_pushes():
    """客户端断开 ⇒ reader 结束 ⇒ 必须注销订阅（旧实现永不执行）。"""
    hub = FakeHub()
    with TestClient(_app(hub)) as client:
        with client.websocket_connect("/ws/quotes?symbols=600519") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            assert len(hub.subscribed) == 1
        # 退出 with = 客户端断开
        assert hub.unsubscribed_evt.wait(5.0), "reader 结束后未注销订阅（writer 成了孤儿）"

    assert hub.unsubscribed == [hub.subscribed[0]]
    # 只注销一次（两条任务都收尾不应重复注销）
    assert len(hub.unsubscribed) == 1


def test_reader_exit_alone_completes_cleanup_without_external_cancel():
    """**reader 单独结束**时路由必须自行收尾 —— 不依赖任何外部取消。

    这是旧实现 `await writer_task` 的**唯一精确判据**（详见模块 docstring 的
    两种驱动形态说明）：hub 从不推送 ⇒ writer 永久 parked 在 `queue.get()` 上
    ⇒ 旧实现下整条路由协程**永不返回**（生产侧 = 任务泄漏 + 订阅永不注销 +
    有界队列积压到满）。
    """

    async def _scenario() -> tuple[FakeHub, _FakeWS]:
        hub = FakeHub()
        ws = _FakeWS(hub)
        await asyncio.wait_for(quotes_ws(ws), timeout=2.0)
        return hub, ws

    hub, ws = asyncio.run(_scenario())
    assert [m["type"] for m in ws.sent][:1] == ["snapshot"], ws.sent
    assert hub.subscribed, "建连即注册"
    assert hub.unsubscribed == [hub.subscribed[0]]
    assert len(hub.unsubscribed) == 1


def test_unexpected_reader_error_is_logged_and_cleanup_still_runs(caplog):
    """reader 的**非预期**异常必须留痕，且收尾照常。

    旧实现无论 reader 抛什么，异常都只挂在 task 上无人取用、也不触发注销
    ——「静默失败」正是 2026-09-01 那次「推送死亡而 ping/pong 还活着」的形态。
    这里用 `symbols` 传非可迭代值制造 TypeError（真实协议里是客户端脏数据）。
    """
    hub = FakeHub()
    with caplog.at_level(logging.WARNING, logger="app.websocket.routes"):
        with TestClient(_app(hub)) as client:
            with client.websocket_connect("/ws/quotes?symbols=600519") as ws:
                assert ws.receive_json()["type"] == "snapshot"
                ws.send_text(json.dumps({"action": "subscribe", "symbols": 123}))
                assert hub.unsubscribed_evt.wait(5.0), "异常退出后未收尾"

    assert any("异常退出" in r.getMessage() for r in caplog.records), [r.getMessage() for r in caplog.records]


def test_ping_goes_through_outbound_queue_not_concurrent_send():
    """ping → pong 必须经**唯一出站点**（writer）返回。

    2026-09-01 P0：reader 直接 `await websocket.send_json(pong)` 与 writer 并发写
    同一 WebSocket，Starlette 抛 RuntimeError 被静默吞掉 ⇒ 推送永久死亡而
    ping/pong 还活着（前端误判连接健康）。这里断言 pong 确实回到了客户端。
    """
    hub = FakeHub()
    with TestClient(_app(hub)) as client:
        with client.websocket_connect("/ws/quotes?symbols=600519") as ws:
            ws.receive_json()  # snapshot
            ws.send_text(json.dumps({"action": "ping"}))
            got = ws.receive_json()
            assert got["type"] == "pong"

            # subscribe 回推的快照也经同一队列（顺序由队列串行化保证）
            ws.send_text(json.dumps({"action": "subscribe", "symbols": ["600519", "000001"]}))
            assert ws.receive_json()["type"] == "snapshot"


def test_invalid_json_is_ignored_without_killing_connection():
    """脏帧（非 JSON）只跳过，不得中断连接（既有契约，顺带钉住）。"""
    hub = FakeHub()
    with TestClient(_app(hub)) as client:
        with client.websocket_connect("/ws/quotes?symbols=600519") as ws:
            ws.receive_json()  # snapshot
            ws.send_text("not-json")
            ws.send_text(json.dumps({"action": "ping"}))
            assert ws.receive_json()["type"] == "pong"


@pytest.mark.parametrize("symbols", ["600519", ""])
def test_subscribe_registers_bounded_queue(symbols: str):
    """建连即注册，且注册的队列**有界**（R24 的防线在 Hub，这里钉接线）。"""
    hub = FakeHub()
    with TestClient(_app(hub)) as client:
        with client.websocket_connect(f"/ws/quotes?symbols={symbols}") as ws:
            ws.receive_json()
            assert len(hub.subscribed) == 1
            assert hub.subscribed[0].maxsize == SUBSCRIBER_QUEUE_MAX
