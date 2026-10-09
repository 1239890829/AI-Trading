"""Selection identity and display capacity keep all original source evidence."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import httpx
from fastapi import FastAPI

from app.core import runtime_params
from app.picks.intraday_opportunity import top_watch_stocks
from app.picks.opportunity_learning import build_intraday_records


def _payload():
    def stock(symbol, level, pct):
        return {"symbol": symbol, "name": symbol, "price": 10.0, "change_pct": pct,
                "linkage": {"level": level, "basis": f"{symbol}:{level}"},
                "tradability": {"level": "可参与", "basis": "原门通过"}}
    return {"trade_date": "2026-10-09", "linkage_stats": {"snapshot_state": "ready",
            "snapshot_as_of": "2026-10-09T10:05:00+08:00", "missing_quote": 0}, "themes": [
        {"theme": "甲", "stage": "发酵", "participants": [stock("600001", "中", 7), stock("600002", "高", 8)],
         "stocks": [{"symbol": "600099", "boards": 2, "tradable": True}]},
        {"theme": "乙", "stage": "启动", "participants": [stock("600001", "高", 7), stock("600003", "中", 5)],
         "stocks": [{"symbol": "600099", "boards": 2, "tradable": True}]},
    ]}


def test_unique_stocks_fill_capacity_and_preserve_each_theme_evidence():
    payload = _payload()
    original = deepcopy(payload)
    out = top_watch_stocks(payload, limit=3)
    assert [row["symbol"] for row in out["items"]] == ["600002", "600001", "600003"]
    assert out["total_candidates"] == 3
    item = out["items"][1]
    assert item["theme"] == "乙" and item["linkage"]["level"] == "高"
    assert {source["theme"]: source["linkage"]["level"] for source in item["theme_sources"]} == {"甲": "中", "乙": "高"}
    assert out["reference_total"] == 1
    assert {source["theme"] for source in out["reference_items"][0]["theme_sources"]} == {"甲", "乙"}
    assert payload == original


def test_archive_preserves_per_theme_denominator_and_shared_stock_rank():
    _, rows = build_intraday_records(_payload(), trade_date="2026-10-09", as_of=datetime(2026, 10, 9, 10, 5))
    assert len(rows) == 12  # Four original source candidates, each candidate/gate/rank remains.
    duplicate = [row for row in rows if row["symbol"] == "600001" and row["stage"] == "rank"]
    assert {row["source_theme"] for row in duplicate} == {"甲", "乙"}
    assert {row["rank"] for row in duplicate} == {2}
    assert {row["evidence"]["linkage_level"] for row in duplicate} == {"高", "中"}


def test_rejected_theme_does_not_borrow_other_theme_eligibility_or_rank():
    payload = _payload()
    payload["themes"][0]["participants"][0]["linkage"]["level"] = "低"
    _, rows = build_intraday_records(payload, trade_date="2026-10-09", as_of=datetime(2026, 10, 9, 10, 5))
    ranks = {row["source_theme"]: row for row in rows if row["symbol"] == "600001" and row["stage"] == "rank"}
    assert ranks["甲"]["decision"] == "rejected" and ranks["甲"]["rank"] is None
    assert ranks["乙"]["decision"] == "ranked" and ranks["乙"]["rank"] == 2


def test_default_api_and_normal_top_consume_same_runtime_capacity(monkeypatch):
    from app.api.routes import picks_intraday as route
    old = runtime_params.snapshot()
    runtime_params.set_overrides({"picks_intraday_top_limit": 2})
    async def build(*_args, **_kwargs):
        return {"data": _payload(), "meta": {}}
    async def trade_day(_hub):
        return "2026-10-09"
    monkeypatch.setattr(route, "_build_opportunities", build)
    monkeypatch.setattr(route, "default_trade_date", trade_day)
    app = FastAPI()
    app.state.hub = object()
    app.include_router(route.router)
    async def get(query=""):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/picks/intraday-top{query}")
            assert response.status_code == 200
            return response.json()["data"]
    try:
        # Missing HTTP query must use the same runtime setting as notification projection.
        normal = asyncio.run(get())
        assert normal["items"] == top_watch_stocks(_payload())["items"]
        assert normal["effective_limit"] == 2 and len(normal["items"]) == 2
        explicit = asyncio.run(get("?limit=1"))
        assert explicit["effective_limit"] == 1 and len(explicit["items"]) == 1
    finally:
        runtime_params.set_overrides(old)
