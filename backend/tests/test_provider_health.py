"""P0-A provider 可观测：/api/system/providers + 断源演练。

演练方法（验收标准）：在 provider 边界注入故障模拟「腾讯被封」。腾讯基址硬编码
且 trust_env=False 直连，域名级封锁要动 /etc/hosts（沙箱红线不碰系统文件）；
边界注入走的是同一条真实链路——真实 CompositeProvider + 真实路由 + 真实熔断
逻辑，语义等价。按序验证：
① 故障期请求自动降级、返回数据正确（来源=备源）；
② /api/system/providers 能看到该源 breaker=open 与连续失败数；
③ 故障解除后状态自愈（冷却过期 → 重试成功 → 计数清零回 closed）。
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import health as health_route
from app.api.routes import market as market_route
from app.data_providers import composite as composite_mod
from app.data_providers.composite import CompositeProvider
from app.schemas.market import OrderBook, OrderBookLevel


def _mk_ob(symbol: str, source: str) -> OrderBook:
    return OrderBook(
        symbol=symbol,
        source=source,
        bids=[OrderBookLevel(price=10.00, volume=100), OrderBookLevel(price=9.99, volume=200)],
        asks=[OrderBookLevel(price=10.01, volume=150), OrderBookLevel(price=10.02, volume=250)],
    )


class FlakyTencent:
    """可开关的腾讯替身：blocked=True 时所有请求抛连接错误（模拟被封）。"""

    name = "tencent"
    realtime = True
    realtime_rank = 0

    def __init__(self) -> None:
        self.blocked = False
        self.calls = 0

    async def get_order_book(self, symbol: str) -> OrderBook:
        self.calls += 1
        if self.blocked:
            raise ConnectionError("blocked: connection refused")
        return _mk_ob(symbol, "tencent")


class HealthySina:
    name = "sina"
    realtime = True
    realtime_rank = 1

    async def get_order_book(self, symbol: str) -> OrderBook:
        return _mk_ob(symbol, "sina")


class _Hub:
    def __init__(self, provider):
        self.provider = provider

    def is_stale(self) -> bool:
        return False

    last_success_refresh = None


def _make_app(comp: CompositeProvider) -> FastAPI:
    app = FastAPI()
    app.include_router(health_route.router, prefix="/api")
    app.include_router(market_route.router, prefix="/api")
    app.dependency_overrides[health_route.get_hub] = lambda: _Hub(comp)
    app.dependency_overrides[market_route.get_hub] = lambda: _Hub(comp)
    return app


# ---------------------------------------------------------------- 单元：provider_health 结构


def test_provider_health_reports_chain_and_methods():
    t = FlakyTencent()
    s = HealthySina()
    comp = CompositeProvider([t, s])
    h = comp.provider_health()
    assert h["chain"] == "chain(tencent→sina)"
    assert [p["name"] for p in h["providers"]] == ["tencent", "sina"]
    tentry = h["providers"][0]
    assert tentry["realtime_rank"] == 0 and tentry["realtime"] is True
    assert "get_order_book" in tentry["methods"]
    # 健康链：无失败记录、无 last_good、无切换
    assert h["breakers"] == {} and h["last_good"] == {} and h["switch_log"] == []


def test_provider_health_watch_state_below_threshold():
    """失败 1 次（未达阈值）：state=watch、无冷却，源仍会被继续尝试。"""
    t = FlakyTencent()
    comp = CompositeProvider([t, HealthySina()])
    t.blocked = True
    ob = asyncio.run(comp.get_order_book("600519"))
    assert ob.source == "sina"  # 备源即刻接住，调用方无感
    h = comp.provider_health()
    b = h["breakers"]["get_order_book@tencent"]
    assert b["failures"] == 1 and b["state"] == "watch" and b["cooldown_left"] == 0
    # 失败期间已由备源接住：last_good 可观测；switch_log 只记"后续切换"，
    # 首次成功只落 last_good（见 composite._call 的 `if method in self._last_good` 分支）
    assert h["last_good"]["get_order_book"] == "sina"
    assert h["switch_log"] == []


# ---------------------------------------------------------------- 演练：HTTP 全链路


def test_outage_drill_failover_visible_selfheal(monkeypatch):
    """断源演练三段式：降级正确 → 熔断可见 → 自愈回 closed。"""
    monkeypatch.setattr(composite_mod, "COOLDOWN_SECONDS", 0.2)  # 演练提速，逻辑不变
    tencent = FlakyTencent()
    comp = CompositeProvider([tencent, HealthySina()])

    with TestClient(_make_app(comp)) as client:
        # 段 0：健康基线——主源服务，无熔断
        r = client.get("/api/order-book/600519")
        assert r.status_code == 200
        assert r.json()["data"]["source"] == "tencent"
        assert client.get("/api/system/providers").json()["breakers"] == {}

        # 段 1：人为封禁腾讯 → 连续 3 次失败（阈值）→ 请求仍 200 且数据来自备源
        tencent.blocked = True
        for _ in range(3):
            r = client.get("/api/order-book/600519")
            assert r.status_code == 200
            body = r.json()["data"]
            assert body["source"] == "sina"
            assert body["bids"][0]["price"] == 10.00  # 数据本身正确，不是空壳
        # 段 2：观测端点看到 tencent 熔断 OPEN + 连续失败数 + 备源接管
        h = client.get("/api/system/providers").json()
        b = h["breakers"]["get_order_book@tencent"]
        # cooldown_left > 0 不再断言：0.2s 演练冷却与重负载下的时钟竞速（2026-09-07
        # 全量复现）会在断言前耗尽冷却——state=="open" 已充分表达「冷却生效中」。
        assert b["state"] == "open" and b["failures"] == 3
        assert h["last_good"]["get_order_book"] == "sina"
        assert "get_order_book: tencent -> sina" in h["switch_log"]

        # 段 3：解除封禁——冷却期内仍跳过坏源（sina 服务），过期后自动重试成功
        tencent.blocked = False
        r = client.get("/api/order-book/600519")
        assert r.json()["data"]["source"] == "sina"  # 冷却中：坏源不被调用
        import time

        time.sleep(0.3)  # > COOLDOWN_SECONDS
        r = client.get("/api/order-book/600519")
        assert r.json()["data"]["source"] == "tencent"  # 自愈：主源回归
        h = client.get("/api/system/providers").json()
        assert "get_order_book@tencent" not in h["breakers"]  # 计数清零（无记录）
        assert h["last_good"]["get_order_book"] == "tencent"
        assert h["switch_log"][-1] == "get_order_book: sina -> tencent"


def test_system_providers_single_provider_contract():
    """单源部署（无 provider_health）：路由降级为最小契约，不 500。"""
    class Solo:
        name = "mock"
        realtime = False

        async def get_order_book(self, symbol: str):
            return None

    app = FastAPI()
    app.include_router(health_route.router, prefix="/api")
    app.dependency_overrides[health_route.get_hub] = lambda: _Hub(Solo())
    with TestClient(app) as client:
        r = client.get("/api/system/providers")
    assert r.status_code == 200
    body = r.json()
    assert body["chain"] == "mock" and body["breakers"] == {}
    assert body["providers"][0]["name"] == "mock"
    assert "caches" in body  # 一站式：缓存命中统计一并返回
