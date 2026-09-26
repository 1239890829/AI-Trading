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
