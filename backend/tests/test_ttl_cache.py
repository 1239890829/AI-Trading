"""统一 TTL 缓存层（P0-5）单元测试：TTL/LRU/单飞/异常路径/注册表。"""

from __future__ import annotations

import asyncio
import gc

import pytest

import app.core.ttl_cache as tc
from app.core.ttl_cache import TTLCache, cache_on, live_caches


@pytest.fixture()
def clock(monkeypatch):
    """可控单调时钟：测试内 clock["now"] += N 手动推进。

    打补丁到 tc.monotonic（模块级名字）而非 tc.time.monotonic——后者是 stdlib
    time 模块本身，会连事件循环的 loop.time() 一起冻结，asyncio.sleep 永不触发。
    """
    state = {"now": 1000.0}
    monkeypatch.setattr(tc, "monotonic", lambda: state["now"])
    return state


def test_hit_and_miss(clock):
    c = TTLCache("t.basic", ttl=60)
    assert c.get("k") == (False, None)
    c.set("k", {"v": 1})
    assert c.get("k") == (True, {"v": 1})
    assert (c.hits, c.misses) == (1, 1)


def test_ttl_expiry(clock):
    c = TTLCache("t.expiry", ttl=60)
    c.set("k", 1)
    clock["now"] += 61
    assert c.get("k") == (False, None)
    c.set("k", 2)
    assert c.get("k") == (True, 2)


def test_lru_eviction_bounded(clock):
    c = TTLCache("t.lru", ttl=60, maxsize=2)
    c.set("a", 1)
    c.set("b", 2)
    assert c.get("a") == (True, 1)  # a 变为最近使用
    c.set("c", 3)                   # 逐出最旧的 b
    assert c.get("b") == (False, None)
    assert c.get("a") == (True, 1) and c.get("c") == (True, 3)
    assert c.evictions == 1 and len(c) == 2


def test_invalidate(clock):
    c = TTLCache("t.inv", ttl=60)
    c.set("a", 1)
    c.set("b", 2)
    c.invalidate("a")
    assert c.get("a")[0] is False and c.get("b")[0] is True
    c.invalidate()
    assert len(c) == 0


def test_stats_fields(clock):
    c = TTLCache("t.stats", ttl=60, maxsize=5)
    c.set("a", 1)
    c.get("a")
    c.get("zzz")
    s = c.stats()
    assert s["name"] == "t.stats"
    assert s["size"] == 1 and s["hits"] == 1 and s["misses"] == 1
    assert s["hit_rate"] == 0.5


def test_single_flight(clock):
    """同 key 并发未命中只回源一次（此前只有 screener 有此语义）。"""
    c = TTLCache("t.single", ttl=60)
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "v"

    async def main():
        results = await asyncio.gather(*[c.get_or_set("k", factory) for _ in range(10)])
        return results

    results = asyncio.run(main())
    assert calls == 1  # 十个并发只回源一次
    assert all(v == "v" for _, v in results)  # 回源的胜者 hit=False（非缓存命中），其余全部命中


def test_factory_exception_not_cached(clock):
    """异常不缓存：下一次调用重新回源（sentiment 503 这类错误路径不能被钉死）。"""
    c = TTLCache("t.err", ttl=60)
    state = {"calls": 0}

    def boom():
        state["calls"] += 1
        raise RuntimeError("x")

    async def main():
        with pytest.raises(RuntimeError):
            await c.get_or_set("k", boom)
        with pytest.raises(RuntimeError):
            await c.get_or_set("k", boom)

    asyncio.run(main())
    assert state["calls"] == 2
    assert len(c) == 0


def test_none_result_not_cached_by_default(clock):
    """回源得到 None 默认不缓存（trading-days 失败重试语义）；cache_none=True 可存。"""
    c = TTLCache("t.none", ttl=60)

    async def main():
        r1 = await c.get_or_set("k", lambda: None)
        r2 = await c.get_or_set("k", lambda: None, cache_none=True)
        return r1, r2

    (h1, v1), (h2, v2) = asyncio.run(main())
    assert (h1, v1) == (False, None)  # None 未缓存 → 两次都回源
    assert (h2, v2) == (False, None)
    assert c.get("k") == (True, None)  # 但 cache_none=True 的那次确实把 None 存进去了


def test_sync_and_async_factory_both_work(clock):
    c = TTLCache("t.factory", ttl=60)

    async def main():
        h1, v1 = await c.get_or_set("a", lambda: 1)
        h2, v2 = await c.get_or_set("b", lambda: asyncio.sleep(0, result=2))
        return (h1, v1), (h2, v2)

    (h1, v1), (h2, v2) = asyncio.run(main())
    assert (h1, v1) == (False, 1)
    assert (h2, v2) == (False, 2)


def test_cache_on_returns_same_instance(clock):
    holder = type("Holder", (), {})()
    c1 = cache_on(holder, "same.key", 60)
    c2 = cache_on(holder, "same.key", 999)
    assert c1 is c2 and c1.ttl == 60


def test_registry_lists_live_instances_and_gcs(clock):
    holder = type("Holder", (), {})()
    cache_on(holder, "reg.probe", 60)
    assert any(s["name"] == "reg.probe" for s in live_caches())
    del holder
    gc.collect()
    assert not any(s["name"] == "reg.probe" for s in live_caches())


def test_system_caches_endpoint():
    """观测端点：返回注册表快照，字段齐全（api-sweep 会自动巡检此端点）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import health as health_route

    holder = type("Holder", (), {})()
    c = cache_on(holder, "endpoint.probe", 60)
    c.set("k", 1)
    c.get("k")

    app = FastAPI()
    app.include_router(health_route.router, prefix="/api")
    with TestClient(app) as client:
        r = client.get("/api/system/caches")

    assert r.status_code == 200
    entry = next(s for s in r.json()["caches"] if s["name"] == "endpoint.probe")
    assert entry["size"] == 1 and entry["hits"] == 1 and entry["ttl"] == 60
