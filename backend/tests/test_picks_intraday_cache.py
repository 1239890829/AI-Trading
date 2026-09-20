"""猎场机会缓存的并发一致性与请求隔离。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone


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


def test_opportunity_cache_key_tracks_snapshot_version(monkeypatch):
    from app.api.routes import picks_intraday as route
    svc=SimpleNamespace(last_success=datetime(2026,9,21,1,30,tzinfo=timezone.utc), snapshot=[])
    state=SimpleNamespace(snapshot_service=svc)
    request=SimpleNamespace(app=SimpleNamespace(state=state))
    calls=0
    async def fake_uncached(_request, trade_date, top_themes, stocks_per_theme):
        nonlocal calls
        calls += 1
        return {"data":{"trade_date":str(trade_date),"themes":[]},"meta":{}}
    monkeypatch.setattr(route,"_build_opportunities_uncached",fake_uncached)
    monkeypatch.setattr(route,"_attach_risk_to_themes",lambda *_:None)
    asyncio.run(route._build_opportunities(request,"2026-09-21",5,8))
    svc.last_success += timedelta(seconds=60)
    asyncio.run(route._build_opportunities(request,"2026-09-21",5,8))
    assert calls == 2


def test_opportunity_cache_key_tracks_snapshot_freshness_state(monkeypatch):
    from app.api.routes import picks_intraday as route
    class Svc:
        snapshot=[]
        last_success=datetime(2026,9,21,1,30,tzinfo=timezone.utc)
        state="ready"
        def freshness(self):
            return SimpleNamespace(state=self.state, as_of=self.last_success)
    svc=Svc(); state=SimpleNamespace(snapshot_service=svc)
    request=SimpleNamespace(app=SimpleNamespace(state=state))
    calls=0
    async def fake_uncached(_request, trade_date, top_themes, stocks_per_theme):
        nonlocal calls; calls += 1
        return {"data":{"trade_date":str(trade_date),"themes":[]},"meta":{}}
    monkeypatch.setattr(route,"_build_opportunities_uncached",fake_uncached)
    monkeypatch.setattr(route,"_attach_risk_to_themes",lambda *_:None)
    asyncio.run(route._build_opportunities(request,"2026-09-21",5,8))
    svc.state="stale"  # as_of 不变，仅 freshness 状态变；也必须失效缓存
    asyncio.run(route._build_opportunities(request,"2026-09-21",5,8))
    assert calls == 2


def test_ever_sealed_identity_comes_from_full_board_not_display_quota():
    from app.api.routes.picks_intraday import _ever_sealed_symbols
    board={"themes":[
        {"ladder":[{"symbol":"600001"},{"symbol":"600002"}]},
        {"ladder":[{"symbol":"600003"}]},
    ]}
    assert _ever_sealed_symbols(board) == {"600001","600002","600003"}
