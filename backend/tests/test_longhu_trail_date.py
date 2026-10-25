"""A historical trail must not borrow a current source pool or cache window."""
from datetime import date
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.deps import get_hub
from app.api.routes import market_longhu as route
from app.market import trade_calendar
from app.schemas.market import LongHuRecord


@pytest.fixture
def context(monkeypatch):
    seen = []
    state = {"anchor": date(2026, 9, 4), "wrong": set(), "failed": set(), "empty": set()}
    async def records(day):
        seen.append(day)
        if day in state["failed"]:
            raise RuntimeError("isolated source unavailable")
        if day in state["empty"]:
            return []
        return [LongHuRecord(symbol="600001", name="隔离样本", trade_date=state["anchor"] if day in state["wrong"] else day,
                             concept_tags=f"题材-{day.isoformat()}", range_days=1, net_buy=100, source="fixture")]
    hub = SimpleNamespace(provider=SimpleNamespace(get_longhu_records=records))
    async def anchor(_hub):
        return state["anchor"]
    async def calendar(_provider):
        return [date(2026, 8, 31), *[date(2026, 9, n) for n in range(1, 5)]]
    monkeypatch.setattr(route, "default_trade_date", anchor)
    monkeypatch.setattr(route, "beijing_today", lambda: date(2026, 9, 4))
    monkeypatch.setattr(trade_calendar, "trading_days", calendar)
    monkeypatch.setattr(route, "meta_payload", lambda _hub: {})
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.dependency_overrides[get_hub] = lambda: hub
    return SimpleNamespace(client=TestClient(app), seen=seen, state=state)


def test_current_historical_and_rolling_default_windows_have_separate_cache_identities(context):
    url = "/api/market/longhu/theme-trail?days=2"
    current = context.client.get(url).json()["data"]
    historical = context.client.get(url + "&date=2026-09-01").json()["data"]
    assert current["days"] == ["2026-09-03", "2026-09-04"] and current["requested_date"] is None
    assert historical["days"] == ["2026-08-31", "2026-09-01"] and historical["anchor_date"] == "2026-09-01"
    assert historical["requested_date"] == "2026-09-01" and historical["coverage_state"] == "complete"
    assert historical["sources"] == ["fixture"]
    assert len(context.seen) == 4
    assert context.client.get(url).json()["data"] == current
    assert context.client.get(url + "&date=2026-09-01").json()["data"] == historical
    assert len(context.seen) == 4
    context.state["anchor"] = date(2026, 9, 3)
    rolled = context.client.get(url).json()["data"]
    assert rolled["days"] == ["2026-09-02", "2026-09-03"] and len(context.seen) == 6
    assert "非真实拆分" in rolled["note"] and "range_days=1" in rolled["note"]


@pytest.mark.parametrize("day", ["20260901", "2026-02-30", "2026-13-01", "2026-9-01", "2026-09-05", "bogus-data"])
def test_invalid_or_future_cutoff_rejects_before_any_source_read(context, day):
    assert context.client.get("/api/market/longhu/theme-trail", params={"date": day}).status_code == 422
    assert context.seen == []


def test_source_ignoring_requested_history_is_unknown_and_never_uses_latest_rows(context):
    context.state["wrong"] = {date(2026, 8, 31), date(2026, 9, 1)}
    response = context.client.get("/api/market/longhu/theme-trail?days=2&date=2026-09-01")
    assert response.status_code == 502
    assert context.seen == [date(2026, 8, 31), date(2026, 9, 1)]
    assert "日期不匹配" in response.json()["detail"]


def test_one_mismatched_day_stays_out_of_partial_historical_aggregate(context):
    context.state["wrong"] = {date(2026, 9, 1)}
    data = context.client.get("/api/market/longhu/theme-trail?days=2&date=2026-09-01").json()["data"]
    assert data["days"] == ["2026-08-31"] and data["coverage_state"] == "partial"
    assert data["expected_days"] == ["2026-08-31", "2026-09-01"]
    assert all(point["date"] == "2026-08-31" for row in data["trail"] for point in row["daily"])
    assert "2026-09-01" in data["degraded"][0]


def test_empty_source_does_not_claim_verified_no_history(context):
    context.state["empty"] = {date(2026, 8, 31), date(2026, 9, 1)}
    data = context.client.get("/api/market/longhu/theme-trail?days=2&date=2026-09-01").json()["data"]
    assert data["coverage_state"] == "unknown" and data["trail"] == []
    assert data["empty_days"] == data["days"]


def test_calendar_without_selected_history_fails_instead_of_falling_forward_to_latest(context):
    response = context.client.get("/api/market/longhu/theme-trail?date=2020-01-01")
    assert response.status_code == 503 and context.seen == []


def test_default_and_explicit_same_anchor_preserve_requested_scope(context):
    current = context.client.get("/api/market/longhu/theme-trail?days=1").json()["data"]
    explicit = context.client.get("/api/market/longhu/theme-trail?days=1&date=2026-09-04").json()["data"]
    assert current["requested_date"] is None and explicit["requested_date"] == "2026-09-04"
    assert current["trail"] == explicit["trail"]
