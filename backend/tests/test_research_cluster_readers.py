"""Research overlays share domain reads and never reconstruct a historic day."""
from copy import deepcopy
from datetime import date, datetime
from types import SimpleNamespace
import asyncio

import pytest

from app.core import bjtime


@pytest.fixture
def inputs(monkeypatch, tmp_path):
    from app.api.routes import market_pools, market_themes
    from app.services import official_match, research_cluster_reader, research_cluster_storage

    monkeypatch.setattr(bjtime, "beijing_today", lambda: date(2026, 10, 28))
    now = datetime.fromisoformat("2026-10-28T14:42:00+08:00")
    monkeypatch.setattr(research_cluster_reader, "beijing_now", lambda: now)
    monkeypatch.setattr(research_cluster_storage, "beijing_now", lambda: now)
    monkeypatch.setattr(research_cluster_storage, "ROOT", tmp_path / "clusters")
    async def default_day(_hub):
        return date(2026, 10, 28)
    monkeypatch.setattr(market_themes, "default_trade_date", default_day)
    records = [
        {"symbol": "002708", "name": "光洋股份", "trade_date": "2026-10-28", "source": "ths",
         "reason": "人形机器人+汽车零部件+“羊”字辈", "change_pct": 9.98, "quality": "medium"},
        {"symbol": "002172", "name": "澳洋健康", "trade_date": "2026-10-28", "source": "ths",
         "reason": "医疗服务+“羊”字辈", "change_pct": 9.9, "quality": "medium"},
    ]
    calls = []

    async def pool(**kwargs):
        calls.append("pool")
        return {"data": {"pool": deepcopy(records)}, "meta": {}}

    async def directory(_):
        calls.append("catalog")
        return {"state": "available", "identities": {}, "ambiguous": [],
                "catalog_version": "a" * 64, "observed_at": now.isoformat()}

    monkeypatch.setattr(market_pools, "limit_up", pool)
    monkeypatch.setattr(official_match, "resolve_official_catalog", directory)
    return records, calls


def test_actual_current_route_retains_secondary_labels_and_saves_version(client, inputs):
    records, calls = inputs
    before = deepcopy(records)
    response = client.get("/api/themes/research-clusters")
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["scope"] == "research_theme_clusters"
    assert payload["reference_mode"] == "current"
    assert payload["trade_date"] == "2026-10-28"
    sheep = next(row for row in payload["clusters"] if row["name"] == "“羊”字辈")
    assert sheep["state"] == "candidate"
    assert {row["symbol"] for row in sheep["stocks"]} == {"002708", "002172"}
    assert all(row["change_pct"] is None for row in payload["clusters"])
    assert records == before and calls == ["pool", "catalog"]
    stored = client.get("/api/themes/research-clusters", params={
        "date": "2026-10-28", "version": payload["version"],
    }).json()["data"]
    assert stored["version"] == payload["version"] and stored["reference_mode"] == "saved"
    assert calls == ["pool", "catalog"]


@pytest.mark.parametrize("params", [
    {"date": "2026-10-27"}, {"date": "2026-10-28", "historical": "true"},
    {"date": "2026-10-28", "version": "b" * 64},
])
def test_missing_saved_date_or_version_never_fetches_current_inputs(client, inputs, params):
    _, calls = inputs
    response = client.get("/api/themes/research-clusters", params=params)
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["state"] == "not_recorded" and payload["clusters"] == []
    assert calls == []


@pytest.mark.parametrize("params", [
    {"date": "bad"}, {"date": "2026-10-29"}, {"version": "../old.json"},
])
def test_invalid_or_future_scope_rejected_before_any_source(client, inputs, params):
    _, calls = inputs
    assert client.get("/api/themes/research-clusters", params=params).status_code == 422
    assert calls == []


def test_resolved_previous_trading_day_reads_saved_only_before_open_or_on_holiday(client, inputs, monkeypatch):
    from app.api.routes import market_themes

    async def previous(_hub):
        return date(2026, 10, 27)
    monkeypatch.setattr(market_themes, "default_trade_date", previous)
    _, calls = inputs
    response = client.get("/api/themes/research-clusters")
    assert response.status_code == 200
    assert response.json()["data"]["trade_date"] == "2026-10-27"
    assert response.json()["data"]["state"] == "not_recorded"
    assert calls == []


def test_concurrent_readers_and_one_departed_view_share_one_source_capture(inputs, monkeypatch):
    from app.api.routes import market_pools, market_themes

    records, calls = inputs

    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()

        async def delayed(**kwargs):
            calls.append("pool")
            started.set()
            await finish.wait()
            return {"data": {"pool": deepcopy(records)}, "meta": {}}

        monkeypatch.setattr(market_pools, "limit_up", delayed)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
        hub = object()
        async def read():
            return await market_themes.research_clusters(
                request, date_str="2026-10-28", version=None, historical=False, hub=hub,
            )
        first = asyncio.create_task(read())
        await started.wait()
        second = asyncio.create_task(read())
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        finish.set()
        result = await second
        assert result["data"]["version"]

    asyncio.run(scenario())
    assert calls.count("pool") == 1 and calls.count("catalog") == 1


@pytest.mark.parametrize("fail_save", [False, True])
def test_new_review_freezes_actual_version_or_reports_save_failure_without_losing_original(inputs, monkeypatch, tmp_path, fail_save):
    from app.market import auction_premium
    from app.review import collector, daily_market, provenance
    from app.services import official_match, research_cluster_reader, theme_service

    records, calls = inputs
    now = datetime.fromisoformat("2026-10-28T14:42:00+08:00")
    monkeypatch.setattr(bjtime, "beijing_now", lambda: now)
    monkeypatch.setattr(daily_market, "DEFAULT_DB_PATH", tmp_path / "missing.duckdb")
    board = {"themes": [{"theme": "原始主题", "strength_score": 31}], "source_observations": records}
    before = deepcopy(board)
    async def build(*_, **kwargs):
        return board
    async def attach(*_, **kwargs):
        return {"state": "unavailable"}
    async def no_auction(*_):
        raise ValueError("fixture auction unavailable")
    monkeypatch.setattr(theme_service, "build_theme_board", build)
    monkeypatch.setattr(official_match, "attach_official_async", attach)
    monkeypatch.setattr(auction_premium, "collect_premium", no_auction)
    monkeypatch.setattr(provenance, "collect_sector_boards", lambda day: (
        {"industry": [], "concept": []}, provenance.evidence(day, state="verified", source="fixture", source_day=day)))
    if fail_save:
        async def fail(*_, **kwargs):
            raise OSError("fixture disk full")
        monkeypatch.setattr(research_cluster_reader, "freeze_research_clusters", fail)
    hub = SimpleNamespace(provider=object(), get_indices=lambda: [])
    snap = SimpleNamespace(breadth_payload=lambda: {}, versioned_snapshot=lambda: ([], None))
    result = asyncio.run(collector.collect_market(hub, snap, date(2026, 10, 28)))
    assert board == before and result.reported_values["themes"] == before["themes"]
    assert result.provenance["themes"].state == "unverified"
    if fail_save:
        assert result.research_clusters is None
        assert any(g.field == "research_clusters" for g in result.gaps)
    else:
        assert result.research_clusters["trade_date"] == "2026-10-28"
        assert any(c["name"] == "“羊”字辈" for c in result.research_clusters["clusters"])
    assert calls == []
