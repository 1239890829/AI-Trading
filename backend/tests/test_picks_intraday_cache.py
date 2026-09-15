"""猎场机会缓存的并发一致性与请求隔离。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_opportunity_endpoints_share_one_build_and_isolate_overlays(monkeypatch):
    """并发刷新只能装配一次，且请求级字段不得污染缓存或另一份响应。"""
    from app.api.routes import picks_intraday as route

    state = SimpleNamespace()
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    calls = 0
    release = asyncio.Event()

    async def fake_uncached(_request, trade_date, top_themes, stocks_per_theme):
        nonlocal calls
        calls += 1
        await release.wait()
        return {
            "data": {
                "trade_date": str(trade_date),
                "themes": [{
                    "theme": "算力",
                    "participants": [{"symbol": f"60000{i}"} for i in range(8)],
                    "stocks": [],
                }],
            },
            "meta": {"top_themes": top_themes, "stocks_per_theme": stocks_per_theme},
        }

    overlay_calls = 0

    def fake_overlay(data, _snapshot):
        nonlocal overlay_calls
        overlay_calls += 1
        data["request_overlay"] = overlay_calls

    monkeypatch.setattr(route, "_build_opportunities_uncached", fake_uncached)
    monkeypatch.setattr(route, "_attach_risk_to_themes", fake_overlay)
    monkeypatch.setattr(route, "_snapshot_by", lambda _request: {})

    async def run():
        one = asyncio.create_task(route._build_opportunities(request, "2026-09-15", 5, 8))
        two = asyncio.create_task(route._build_opportunities(request, "2026-09-15", 5, 8))
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(one, two)
        third = await route._build_opportunities(request, "2026-09-15", 5, 8)
        return first, second, third

    first, second, third = asyncio.run(run())

    assert calls == 1, "同键并发未命中必须单飞，禁止 1/8 只结果互相覆盖缓存"
    assert [len(x["data"]["themes"][0]["participants"]) for x in (first, second, third)] == [8, 8, 8]
    assert {first["data"]["request_overlay"], second["data"]["request_overlay"], third["data"]["request_overlay"]} == {1, 2, 3}
    first["data"]["themes"][0]["participants"].clear()
    assert len(second["data"]["themes"][0]["participants"]) == 8
    assert len(third["data"]["themes"][0]["participants"]) == 8
