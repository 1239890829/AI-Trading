#!/usr/bin/env python3
"""Recover one flash-news gap beyond the normal 20-page catch-up limit.

Run from backend with the application and schedulers stopped. Dry-run is the
default. Apply makes a SQLite backup in ignored artifacts/recovery first.
The source must still expose the exact old code; otherwise the gap stays open.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.events.store import EventStore  # noqa: E402
from app.news.flash import FlashRecovery, aclose, poll_once  # noqa: E402
from app.news.flash_state import FlashCheckpointStore  # noqa: E402
from app.services.theme_catalog_service import ThemeCatalogService  # noqa: E402

ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts" / "recovery"


def _backup(path: Path) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out = ARTIFACTS / f"flash-{stamp}.sqlite3"
    try:
        with closing(sqlite3.connect(str(path))) as source, closing(sqlite3.connect(str(out))) as target:
            source.backup(target)
    except Exception:
        out.unlink(missing_ok=True)
        raise
    return out


async def _apply(session_factory, request: FlashRecovery) -> int:
    state = SimpleNamespace(event_store=EventStore(session_factory), theme_catalog=None)
    if settings.ths_api_key:
        state.theme_catalog = ThemeCatalogService(session_factory)
    try:
        return await poll_once(state, recovery=request)
    finally:
        if state.theme_catalog is not None:
            await state.theme_catalog.aclose()
        await aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="existing SQLite database")
    parser.add_argument("--channel", type=int, required=True)
    parser.add_argument("--expected-last-code", required=True)
    parser.add_argument("--max-pages", type=int, required=True, help="21..100 pages")
    parser.add_argument("--apply", action="store_true", help="back up DB and ingest")
    args = parser.parse_args()
    request = FlashRecovery(args.channel, args.expected_last_code, args.max_pages)
    if args.channel < 1 or not args.expected_last_code.strip() or not 21 <= args.max_pages <= 100:
        parser.error("channel and old code are required; max-pages must be 21..100")
    db_path = args.db.resolve()
    if not db_path.is_file():
        parser.error("database does not exist")
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        checkpoint = FlashCheckpointStore(session_factory)
        row = checkpoint.load([args.channel]).get(args.channel)
        if (row is None or row.last_code != args.expected_last_code
                or row.gap_at is None or row.gap_reason == "prebaseline_unverifiable"):
            print("BLOCKED: old code or gap state differs from requested recovery")
            return 2
        print(f"channel={args.channel} gap={row.gap_reason} max_pages={args.max_pages}")
        if not args.apply:
            print("DRY RUN: no source request or database write; rerun with --apply after stopping the app")
            return 0
        backup = _backup(db_path)
        print(f"backup={backup}")
        created = asyncio.run(_apply(session_factory, request))
        after = checkpoint.load([args.channel])[args.channel]
        if after.gap_at is None and after.last_code != args.expected_last_code:
            print(f"RECOVERED: created={created} new_code={after.last_code}")
            return 0
        print(f"OPEN: created={created} gap={after.gap_reason}; old frontier retained")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
