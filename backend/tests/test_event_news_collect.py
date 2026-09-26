"""Search hits are candidates, not authoritative company links."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import events as event_routes
from app.events.store import EventStore
from app.models.watchlist import Base


@pytest.mark.parametrize("watchlist_name,quote_name,cache_name,expected_name_link", [
    ("三峡新材", None, None, "600293"),
    ("三 峡 新 材", None, None, "600293"),
    (None, "三峡新材", None, "600293"),
    (None, None, "三峡新材", "600293"),
    (None, None, None, None),
])
def test_collector_keeps_unrelated_search_hit_without_stock_link(
    monkeypatch, tmp_path, watchlist_name, quote_name, cache_name, expected_name_link,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'news-collect.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = EventStore(sf)

    class Repo:
        def list_items(self):
            return [SimpleNamespace(symbol="600293", name=watchlist_name)]

    class Provider:
        async def get_quotes(self, symbols):
            assert cache_name is None, "cached name should avoid a provider request"
            assert symbols == ["600293"]
            return [SimpleNamespace(symbol="600293", name=quote_name)] if quote_name else []

        async def get_news(self, symbol, limit):
            assert (symbol, limit) == ("600293", 5)
            return [
                {"title": "歌尔股份否认有玻璃基板业务收入", "source": "eastmoney"},
                {"title": "三峡新材发布玻璃基板业务公告", "source": "eastmoney"},
                {"title": "600293发布新的生产计划", "source": "eastmoney"},
                {"title": "玻璃基板概念多股活跃", "summary": "三峡新材今日涨停", "source": "eastmoney"},
            ]

    monkeypatch.setattr(event_routes, "get_session_factory", lambda: sf)
    cached = [SimpleNamespace(symbol="600293", name=cache_name)] if cache_name else []
    state = SimpleNamespace(theme_catalog=None,
                            hub=SimpleNamespace(provider=Provider(), get_quotes=lambda _: cached),
                            watchlist_repo=Repo(), event_store=store)

    result = asyncio.run(event_routes.collect_news_events(state))

    assert result["fetched"] == 4
    assert result["unverified_stock_links"] == (2 if expected_name_link else 3)
    events = {row.title: row for row in store.list_events(active_only=False, limit=10)}
    assert len(events) == 4
    wrong_event = events["歌尔股份否认有玻璃基板业务收入"]
    assert wrong_event.source_symbol is None
    assert not [d for d in wrong_event.directions if d.target_type == "symbol"]
    assert events["三峡新材发布玻璃基板业务公告"].source_symbol == expected_name_link
    assert events["600293发布新的生产计划"].source_symbol == "600293"
    assert events["玻璃基板概念多股活跃"].source_symbol is None


def test_collector_retains_source_identity_and_summary_revisions(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'news-revision.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = EventStore(sf)

    class Provider:
        title = "三峡新材发布生产计划"
        summary = "原计划年内投产"

        async def get_news(self, symbol, limit):
            return [{"title": self.title, "summary": self.summary,
                     "source_item_id": "article-42", "source": "东财",
                     "date": "2026-09-26 09:00", "url": "https://example.test/article-42"}]

    provider = Provider()
    state = SimpleNamespace(
        theme_catalog=None, hub=SimpleNamespace(provider=provider),
        watchlist_repo=SimpleNamespace(list_items=lambda: [
            SimpleNamespace(symbol="600293", name="三峡新材")]),
        event_store=store,
    )
    monkeypatch.setattr(event_routes, "get_session_factory", lambda: sf)

    first = asyncio.run(event_routes.collect_news_events(state))
    repeated = asyncio.run(event_routes.collect_news_events(state))
    assert first["created"] == 1
    assert repeated["created"] == 0
    assert len(store.observations_of(store.list_events(active_only=False)[0].id)) == 1

    provider.summary = "更正：项目仍在审批，投产时间未定"
    revised = asyncio.run(event_routes.collect_news_events(state))
    assert revised["created"] == 0
    event = store.list_events(active_only=False)[0]
    observations = store.observations_of(event.id)
    assert [o.summary for o in observations] == ["原计划年内投产", provider.summary]
    assert [o.change_kind for o in observations] == ["initial", "revision"]
    assert {o.source_item_id for o in observations} == {"article-42"}
    assert all(o.source_published_at is not None for o in observations)
    assert event.revision_pending_at is not None

    provider.title = "三峡新材修订生产计划"
    asyncio.run(event_routes.collect_news_events(state))
    events = store.list_events(active_only=False)
    assert len(events) == 1
    assert len(store.observations_of(event.id)) == 3


def test_same_article_returned_for_two_symbols_is_one_observation(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'two-symbols.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = EventStore(sf)

    class Provider:
        async def get_news(self, symbol, limit):
            return [{"title": "三峡新材与歌尔股份发布联合项目公告",
                     "summary": "两家公司共同披露项目进展", "source_item_id": "article-88",
                     "source": "东财", "date": "2026-09-26 09:00"}]

    items = [SimpleNamespace(symbol="600293", name="三峡新材"),
             SimpleNamespace(symbol="002241", name="歌尔股份")]
    state = SimpleNamespace(
        theme_catalog=None, hub=SimpleNamespace(provider=Provider()),
        watchlist_repo=SimpleNamespace(list_items=lambda: items),
        event_store=store,
    )
    monkeypatch.setattr(event_routes, "get_session_factory", lambda: sf)
    result = asyncio.run(event_routes.collect_news_events(state))
    events = store.list_events(active_only=False)
    assert result["created"] == 1
    assert len(events) == 1
    assert len(store.observations_of(events[0].id)) == 1
    assert events[0].revision_pending_at is None
    assert {(d.target, d.direction) for d in events[0].directions if d.target_type == "symbol"} == {
        ("600293", 0), ("002241", 0),
    }
    items.reverse()
    asyncio.run(event_routes.collect_news_events(state))
    assert len(store.observations_of(events[0].id)) == 1
