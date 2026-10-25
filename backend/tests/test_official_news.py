"""Official macro facts remain dated, bounded, idempotent and read-only."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ, beijing_now_naive
from app.events.store import EventStore
from app.models.event import EventObservation
from app.models.watchlist import Base
from app.news import official

NOW = datetime(2026, 10, 25, 12, 0)
LINK = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261013a.htm"
DATE = "Tue, 13 Oct 2026 18:00:00 GMT"


def item(*, title="Federal Reserve issues FOMC statement", date=DATE, url=LINK, summary="Original policy text"):
    return ("<item><title>" + escape(title) + "</title><link>" + escape(url)
            + "</link><pubDate>" + escape(date) + "</pubDate><description>"
            + escape(summary) + "</description></item>")


def feed(*items):
    return ("<?xml version='1.0'?><rss><channel>" + "".join(items) + "</channel></rss>").encode()


def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'official.db'}")
    Base.metadata.create_all(engine)
    return EventStore(sessionmaker(engine, expire_on_commit=False))


def client_for(raw, *, status=200, content_type="text/xml"):
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(status, content=raw, headers={"Content-Type": content_type})

    return httpx.AsyncClient(transport=httpx.MockTransport(respond)), calls


def run(coro):
    return asyncio.run(coro)


def test_source_time_identity_and_neutrality_are_preserved():
    row = official.parse_feed(feed(item()), now=NOW)["items"][0]
    assert row["published_at"] == datetime(2026, 10, 14, 2)
    assert row["source_item_id"] == "monetary20261013a.htm"
    event = official.to_event(row)
    assert event["published_at"] == event["source_published_at"] != NOW
    assert event["source"] == official.SOURCE
    assert event["directions"] == [] and event["source_symbols"] == []
    assert event["source_symbol"] is None and event["source_tier"] == 5


@pytest.mark.parametrize("raw", [
    b'<!DOCTYPE rss [<!ENTITY x "boom">]><rss><channel/></rss>',
    b'<rss><channel>&external;</channel></rss>',
    b'<html><body>unavailable</body></html>',
    b'<rss>\x00</rss>',
    b'X' * (official.MAX_BYTES + 1),
])
def test_unsafe_invalid_or_oversized_input_is_not_valid_empty(raw):
    with pytest.raises(official.FeedError):
        official.parse_feed(raw, now=NOW)


def test_bad_missing_naive_future_and_external_rows_are_explicitly_rejected():
    raw = feed(item(), item(title=""), item(date="bad"),
               item(date="Sun, 25 Oct 2026 12:00:00"),
               item(date="Mon, 26 Oct 2026 18:00:00 GMT"),
               item(url="https://example.com/news"), item(url="https://[broken"))
    batch = official.parse_feed(raw, now=NOW)
    assert len(batch["items"]) == 1
    assert batch["rejected"] == 6


def test_feed_window_duplicate_and_old_entries_do_not_fabricate_history():
    batch = official.parse_feed(feed(item(), item(), item(date="Mon, 1 Jun 2026 18:00:00 GMT")), now=NOW)
    assert len(batch["items"]) == 1 and batch["older_than_window"] == 1
    assert batch["rejected"] == 0
    assert official.parse_feed(feed(), now=NOW)["items"] == []


def test_explicit_item_limit_and_html_are_safe_text():
    batch = official.parse_feed(feed(*[item(summary="<b>Policy</b> &amp; facts") for _ in range(35)]), now=NOW)
    assert batch["truncated"] is True
    assert batch["items"][0]["summary"] == "Policy & facts"


def test_poll_persists_original_times_and_content_revision_without_directions(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.official_news_enabled", True)
    clock = [100.0]
    monkeypatch.setattr(official.time, "monotonic", lambda: clock[0])
    source_store = store(tmp_path)
    state = SimpleNamespace(event_store=source_store)

    async def scenario():
        client, calls = client_for(feed(item()))
        async with client:
            assert await official.poll_if_due(state, client=client, now=NOW) == 1
            assert await official.poll_if_due(state, client=client, now=NOW) == 0
            assert len(calls) == 1
            clock[0] += 901
            assert await official.poll_if_due(state, client=client, now=NOW) == 0
        clock[0] += 901
        revised, _ = client_for(feed(item(summary="Revised original wording")))
        async with revised:
            assert await official.poll_if_due(state, client=revised, now=NOW) == 0

    run(scenario())
    with source_store._sf() as db:
        observations = db.execute(select(EventObservation).order_by(EventObservation.id)).scalars().all()
    assert len(observations) == 2
    assert [r.change_kind for r in observations] == ["initial", "revision"]
    assert all(r.source_published_at == datetime(2026, 10, 14, 2) for r in observations)
    assert all(r.received_at > r.source_published_at for r in observations)
    assert observations[0].content_hash != observations[1].content_hash
    event = source_store.get_event(observations[0].event_id)
    assert event.directions == []
    snap = official.source_snapshot(state)
    assert snap["latest_published_at"] == "2026-10-14T02:00:00"
    assert snap["last_fetch_at"] == "2026-10-25T12:00:00"
    assert snap["history_coverage"] == "feed_window_only"


@pytest.mark.parametrize("status,ctype,raw", [(403,"text/xml",feed()), (200,"text/html",b'blocked'), (200,"text/xml",b'<broken')])
def test_source_failure_preserves_prior_success_and_has_bounded_retry(tmp_path, monkeypatch, status, ctype, raw):
    monkeypatch.setattr("app.core.config.settings.official_news_enabled", True)
    state = SimpleNamespace(event_store=store(tmp_path), official_news_status={
        "last_success_at":"2026-10-24T12:00:00", "latest_published_at":"2026-10-13T10:00:00"})

    async def scenario():
        client, calls = client_for(raw, status=status, content_type=ctype)
        async with client:
            assert await official.poll_if_due(state, client=client, now=NOW) == 0
            assert await official.poll_if_due(state, client=client, now=NOW) == 0
            assert len(calls) == 1
    run(scenario())
    snap = official.source_snapshot(state)
    assert snap["state"] == "degraded" and snap["last_error"]
    assert snap["last_success_at"] == "2026-10-24T12:00:00"
    assert snap["latest_published_at"] == "2026-10-13T10:00:00"


def test_disabled_and_not_observed_health_do_not_claim_connectivity(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.official_news_enabled", False)
    state = SimpleNamespace()
    assert run(official.poll_if_due(state, now=NOW)) == 0
    assert official.source_snapshot(state)["state"] == "disabled"
    monkeypatch.setattr("app.core.config.settings.official_news_enabled", True)
    assert official.source_snapshot(state)["state"] == "not_observed"


def test_official_macro_never_enters_automatic_model_candidates_but_other_news_still_can(tmp_path):
    from app.events.llm_aux import _pending_candidates

    source_store = store(tmp_path)
    now = beijing_now_naive() - timedelta(minutes=1)
    row = {"title":"Federal Reserve policy release", "summary":"Original official statement",
           "source_item_id":"monetary20261025a.htm", "url":LINK, "published_at":now}
    source_store.add_event(official.to_event(row))
    other = official.to_event({**row,"title":"New industry policy announcement"})
    other["source"] = "东财快讯"
    source_store.add_event(other)
    candidates = _pending_candidates(source_store._sf, theme_names=["半导体"], max_batch=10, age_max_h=48)
    assert [c.source for c in candidates] == ["东财快讯"]


def test_aware_now_uses_beijing_date():
    batch = official.parse_feed(feed(item()), now=NOW.replace(tzinfo=BJ_TZ))
    assert len(batch["items"]) == 1


def test_distinct_policy_releases_with_identical_headlines_are_not_merged(tmp_path):
    source_store = store(tmp_path)
    first = official.parse_feed(feed(item()), now=NOW)["items"][0]
    second = {**first,"source_item_id":"monetary20261007a.htm",
              "url":"https://www.federalreserve.gov/newsevents/pressreleases/monetary20261007a.htm"}
    a, anew = source_store.add_event(official.to_event(first))
    b, bnew = source_store.add_event(official.to_event(second))
    assert anew and bnew and a.id != b.id
    revised = {**first,"title":"Corrected FOMC statement heading"}
    c, cnew = source_store.add_event(official.to_event(revised))
    assert not cnew and c.id == a.id


def test_frozen_official_feed_sample_keeps_actual_source_fields():
    raw = (Path(__file__).parent / "fixtures/fed_monetary_rss.xml").read_bytes()
    batch = official.parse_feed(raw, now=NOW)
    assert batch["rejected"] == 0 and len(batch["items"]) == 1
    row = batch["items"][0]
    assert row["source_item_id"] == "monetary20261013a.htm"
    assert row["published_at"] == datetime(2026, 10, 14, 2)
    assert "discount rate meetings" in row["title"]
