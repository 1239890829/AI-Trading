"""新闻/公告 60s 进程内缓存测试（技术债 #4）：切股回看不闪加载，源只打一次。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_hub
from app.api.routes import market as market_route
from app.core.errors import register_error_handlers


class _FakeHub:
    """最小 hub：provider 可数调用次数（TTL 缓存现挂在 app.state 上，P0-5 统一缓存层）。"""

    name = "fake"
    last_success_refresh = None

    def __init__(self, rows: list[dict]):
        self.provider = self
        self.calls = 0
        self._rows = rows

    def is_stale(self):
        return False

    async def get_announcements(self, symbol: str, limit: int):
        self.calls += 1
        return self._rows


@pytest.fixture()
def client():
    hub = _FakeHub([{"title": "关于某某的公告", "date": "2026-08-28"}])
    from fastapi import FastAPI

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(market_route.router, prefix="/api")
    # 依赖覆盖的 key 必须是**同一个函数对象**：`get_hub` 的唯一来源是 `app.api.deps`
    # （门面只装配 router，不再持有各分片的 import 引用 —— IMP-005 批 3）
    app.dependency_overrides[get_hub] = lambda: hub
    with TestClient(app) as c:
        # TestClient 生命周期内携带 hub 引用，便于断言调用次数
        c.hub = hub  # type: ignore[attr-defined]
        yield c


def test_announcements_cached_second_call(client):
    r1 = client.get("/api/announcements/600519")
    assert r1.status_code == 200
    assert "cached" not in r1.json()["meta"]
    r2 = client.get("/api/announcements/600519")
    assert r2.json()["meta"]["cached"] is True
    assert client.hub.calls == 1  # 源只打一次


def test_different_symbol_not_shared(client):
    client.get("/api/announcements/600519")
    r2 = client.get("/api/announcements/000001")
    assert "cached" not in r2.json()["meta"]
    assert client.hub.calls == 2  # 不同 key 各打一次源


def test_ttl_expiry_refetches(client, monkeypatch):
    client.get("/api/announcements/600519")
    # 时间前进 61s → 缓存过期（补丁打在 ttl_cache 模块级 monotonic 上；
    # 不能打 tc.time.monotonic——那会连事件循环时钟一起冻结）
    import app.core.ttl_cache as ttl_cache

    real = ttl_cache.monotonic
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: real() + 61)
    r = client.get("/api/announcements/600519")
    assert "cached" not in r.json()["meta"]
    assert client.hub.calls == 2
