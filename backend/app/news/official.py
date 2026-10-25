"""Bounded, factual-only Federal Reserve monetary-policy RSS reader.

Official RSS/public-domain policy: https://www.federalreserve.gov/feeds/feeds.htm
and https://www.federalreserve.gov/disclaimer.htm. This is a feed-window reader,
not a complete historical archive or an A-share catalyst/score consumer.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree

from app.core.bjtime import BJ_TZ, beijing_now_naive

log = logging.getLogger(__name__)
SOURCE = "美联储官方RSS"
READ_ONLY_SOURCES = (SOURCE,)
FEED_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
POLL_SECONDS = 900.0
MAX_BYTES = 512 * 1024
MAX_ITEMS = 30
MAX_AGE_DAYS = 30
_HTTP = None


class FeedError(ValueError):
    pass


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def _plain(value: str) -> str:
    parser = _Text()
    parser.feed(re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", value, flags=re.I | re.S))
    return " ".join(html.unescape(" ".join(parser.parts)).split())


def _publication(value: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(BJ_TZ).replace(tzinfo=None)


def parse_feed(raw: bytes, *, now: datetime | None = None) -> dict:
    """Preserve source time/identity; reject unsafe or malformed input explicitly."""
    if len(raw) > MAX_BYTES or b"\x00" in raw or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I):
        raise FeedError("unsafe_or_oversized_xml")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise FeedError("invalid_xml") from exc
    channel = root.find("channel") if root.tag == "rss" else None
    if channel is None:
        raise FeedError("unexpected_feed_shape")
    now = now or beijing_now_naive()
    if now.tzinfo is not None:
        now = now.astimezone(BJ_TZ).replace(tzinfo=None)
    items = channel.findall("item")
    rows: list[dict] = []
    seen: set[tuple] = set()
    rejected = old = 0
    for item in items[:MAX_ITEMS]:
        title = _plain(item.findtext("title") or "")
        summary = _plain(item.findtext("description") or "")
        link = (item.findtext("link") or "").strip()
        try:
            parts = urlsplit(link)
        except ValueError:
            rejected += 1
            continue
        published = _publication(item.findtext("pubDate") or "")
        if (not title or len(title) > 500 or published is None or published > now
                or parts.scheme != "https" or parts.netloc.lower() != "www.federalreserve.gov"
                or not re.fullmatch(r"/newsevents/pressreleases/monetary\d{8}[a-z]\.htm", parts.path)):
            rejected += 1
            continue
        if published < now - timedelta(days=MAX_AGE_DAYS):
            old += 1
            continue
        url = "https://www.federalreserve.gov" + parts.path
        identity = (url, title, summary, published)
        if identity in seen:
            continue
        seen.add(identity)
        rows.append({"title": title, "summary": summary[:2000] or None,
                     "source_item_id": parts.path.rsplit("/", 1)[-1],
                     "url": url, "published_at": published})
    return {"items": rows, "rejected": rejected, "older_than_window": old,
            "truncated": len(items) > MAX_ITEMS, "source_count": len(items)}


def to_event(row: dict) -> dict:
    # No rule/model polarity, theme or company mapping is inferred for this source.
    from app.events.extract import title_fingerprint

    return {"fingerprint": title_fingerprint(SOURCE + ":" + row["source_item_id"] + ":" + row["title"]),
            "title": row["title"],
            "summary": row["summary"], "source": SOURCE, "source_tier": 5,
            "url": row["url"], "source_item_id": row["source_item_id"],
            "published_at": row["published_at"], "source_published_at": row["published_at"],
            "source_symbol": None, "source_symbols": [], "board_codes": [],
            "fact_kind": "fact", "certainty": "done", "category": "policy",
            "half_life_hours": 336, "directions": []}


def _http():
    global _HTTP
    if _HTTP is None:
        import httpx

        _HTTP = httpx.AsyncClient(
            trust_env=False, timeout=6.0, follow_redirects=False,
            headers={"User-Agent": "AShare-Research/1.0 (local RSS reader)",
                     "Accept": "application/rss+xml, application/xml, text/xml"},
        )
    return _HTTP


async def aclose() -> None:
    global _HTTP
    if _HTTP is not None:
        await _HTTP.aclose()
        _HTTP = None


async def fetch_feed(*, client=None, now: datetime | None = None) -> dict:
    # Fixed endpoint, no redirects, no linked-page requests; cap total time and bytes.
    async with asyncio.timeout(8.0):
        async with (client or _http()).stream("GET", FEED_URL) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in ("text/xml", "application/xml", "application/rss+xml"):
                raise FeedError("unexpected_content_type")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_BYTES:
                    raise FeedError("oversized_xml")
    return parse_feed(bytes(raw), now=now)


def source_snapshot(state) -> dict:
    """Observed health only; last_fetch_at is never a publication timestamp."""
    from app.core.config import settings

    status = dict(getattr(state, "official_news_status", {}) or {})
    if not settings.official_news_enabled:
        status["state"] = "disabled"
    return {"source": SOURCE, "feed_url": FEED_URL,
            "enabled": settings.official_news_enabled, "poll_seconds": POLL_SECONDS,
            "scope": "official_macro_read_only", "history_coverage": "feed_window_only",
            "state": status.get("state", "not_observed"), **status}


async def poll_if_due(app, *, client=None, now: datetime | None = None) -> int:
    """Called by the existing flash loop; no extra loop, model or notification."""
    from app.core.config import settings

    if not settings.official_news_enabled:
        return 0
    state = getattr(app, "state", app)
    if getattr(state, "official_news_running", False):
        return 0
    clock = time.monotonic()
    last = getattr(state, "official_news_attempt_clock", None)
    if last is not None and clock - last < POLL_SECONDS:
        return 0
    state.official_news_running = True
    state.official_news_attempt_clock = clock
    now = now or beijing_now_naive()
    if now.tzinfo is not None:
        now = now.astimezone(BJ_TZ).replace(tzinfo=None)
    previous = dict(getattr(state, "official_news_status", {}) or {})
    status = {**previous, "last_fetch_at": now.isoformat(), "state": "fetching",
              "last_error": None, "fetched": 0, "created": 0}
    state.official_news_status = status
    try:
        batch = await fetch_feed(client=client, now=now)
        store = getattr(state, "event_store", None)
        if store is None:
            raise FeedError("event_store_not_available")
        created = 0
        for row in sorted(batch["items"], key=lambda item: item["published_at"]):
            _, is_new = await asyncio.to_thread(store.add_event, to_event(row))
            created += int(is_new)
            status["created"] = created
        latest = max((row["published_at"] for row in batch["items"]), default=None)
        status.update({"state": "degraded" if batch["rejected"] or batch["truncated"] else "ready",
                       "last_success_at": now.isoformat(), "fetched": len(batch["items"]),
                       "latest_published_at": latest.isoformat() if latest else None,
                       "rejected": batch["rejected"], "truncated": batch["truncated"],
                       "older_than_window": batch["older_than_window"]})
        return created
    except Exception as exc:  # Independent source failure never blocks the existing feed.
        status["state"] = "degraded"
        status["last_error"] = type(exc).__name__
        log.warning("official RSS fetch/ingest failed: %s", type(exc).__name__)
        return status["created"]
    finally:
        state.official_news_running = False
