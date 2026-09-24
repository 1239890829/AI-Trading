#!/usr/bin/env python3
"""Recover one flash-news gap beyond the normal 20-page catch-up limit.

Dry-run is the default and makes no source request. Explicit --probe reads the
source and saves a local coverage receipt without changing the database. Stop
the application and schedulers before --apply, which backs up SQLite first.
The source must still expose the exact old code; otherwise the gap stays open.
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
from app.core.bjtime import beijing_now_naive  # noqa: E402
from app.events.store import EventStore  # noqa: E402
from app.news.flash import FlashRecovery, aclose, fetch_fast_news, poll_once  # noqa: E402
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


async def _probe(request: FlashRecovery):
    try:
        return await fetch_fast_news(
            column=request.channel, pages=request.max_pages,
            stop_at_code=request.expected_last_code, min_pages=settings.flash_news_pages,
        )
    finally:
        await aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="existing SQLite database")
    parser.add_argument("--channel", type=int, required=True)
    parser.add_argument("--expected-last-code", required=True)
    parser.add_argument("--max-pages", type=int, required=True, help="21..100 pages")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--probe", action="store_true", help="read source and save local coverage receipt")
    action.add_argument("--apply", action="store_true", help="back up DB and ingest")
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
        if args.probe:
            probe_error = None
            try:
                fetched = asyncio.run(_probe(request))
            except Exception as exc:  # noqa: BLE001 —— 抓取异常也留最小回执，不动水位
                fetched = None
                probe_error = type(exc).__name__
            coverage = (getattr(fetched, "coverage", {}) or {}).get(args.channel) or {}
            after = checkpoint.load([args.channel]).get(args.channel)
            frontier_unchanged = bool(after is not None and
                                      after.last_code == row.last_code and
                                      after.gap_at == row.gap_at and
                                      after.gap_reason == row.gap_reason)
            receipt = {
                "checked_at": beijing_now_naive().isoformat(sep=" "),
                "channel": args.channel, "expected_last_code": args.expected_last_code,
                "gap_at": row.gap_at.isoformat(sep=" "), "gap_reason": row.gap_reason,
                "pages_requested": args.max_pages,
                "pages_fetched": coverage.get("pages_fetched"),
                "terminal": (coverage.get("terminal") or "unknown") if fetched is not None
                else "source_failure",
                "failure_type": probe_error,
                "source_host": coverage.get("source_host"),
                "newest_code": coverage.get("newest_code"),
                "old_code_found": bool(coverage.get("overlap")),
                "complete": bool(coverage.get("complete")),
                "item_count": len(fetched) if fetched is not None else 0,
                "frontier_unchanged": frontier_unchanged,
            }
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            receipt_path = ARTIFACTS / f"flash-probe-{stamp}.json"
            with receipt_path.open("x", encoding="utf-8") as out:
                json.dump(receipt, out, ensure_ascii=False, indent=2, sort_keys=True)
                out.write("\n")
            print(f"PROBE: receipt={receipt_path} terminal={receipt['terminal']} "
                  f"old_code_found={receipt['old_code_found']} "
                  f"frontier_unchanged={frontier_unchanged}; probe made no database write")
            return 0 if frontier_unchanged and fetched is not None else 2
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
