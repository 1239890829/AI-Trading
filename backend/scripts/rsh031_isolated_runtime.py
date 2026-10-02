"""Finite, research-only forward collection. No application startup or .env loading.

Run from the repository root with PYTHONPATH=backend. Runtime output must be
under ignored artifacts; the calendar is an independently verified input.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import signal
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ, beijing_now
from app.data_providers.eastmoney import EastmoneyProvider
from app.events.store import EventStore
from app.models.watchlist import Base
from app.research.leader_collector import load_evidence
from app.research.leader_followthrough import capture, summary
from app.services.snapshot_service import MarketSnapshotService
from app.core.scheduler import wait_or_stop


REPO = Path(__file__).resolve().parents[2]
def output_dir(value: Path) -> Path:
    path = value.resolve()
    root = (REPO / "artifacts").resolve()
    if path == root or root not in path.parents:
        raise ValueError("runtime output must be a child of repository artifacts")
    path.mkdir(parents=True, exist_ok=True)
    if (REPO / "artifacts").is_symlink() or any(p.is_symlink() for p in path.rglob("*")):
        raise ValueError("runtime tree must not contain symlinks")
    return path


def calendar(path: Path) -> list[date]:
    data = json.loads(path.read_text())
    if not str(data.get("source_url", "")).startswith("https://www.sse.com.cn/"):
        raise ValueError("calendar requires a verified exchange source")
    days = [date.fromisoformat(d) for d in data["days"]]
    if not days or days != sorted(set(days)):
        raise ValueError("calendar must be ordered and unique")
    return days


def eligible(now: datetime, days: list[date]) -> bool:
    if now.tzinfo is None:
        raise ValueError("runtime clock requires timezone")
    now = now.astimezone(BJ_TZ)
    clock = now.time().replace(tzinfo=None)
    return (now.date() in days and
            (time(9, 30) <= clock <= time(11, 30) or
             time(13) <= clock < time(15) or time(15, 5) <= clock <= time(15, 30)))


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    if path.is_symlink() or temporary.is_symlink():
        raise ValueError("runtime output cannot follow a symlink")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def idle_delay(now: datetime, days: list[date]) -> float:
    now = now.astimezone(BJ_TZ)
    openings = (datetime.combine(d, t, BJ_TZ) for d in days if d >= now.date()
                for t in (time(9, 30), time(13), time(15, 5)))
    return min([600.0] + [(d - now).total_seconds() for d in openings if d > now])


async def company_news(rows: list[dict], provider, store) -> dict:
    # Price-led bounded coverage, explicitly not all-market news discovery.
    selected = sorted((r for r in rows if r.get("change_pct") is not None
                       and str(r.get("symbol", "")).startswith(("00", "60"))
                       and not str(r.get("name", "")).upper().lstrip("*").startswith("ST")),
                      key=lambda r: (-r["change_pct"], r["symbol"]))[:20]
    fetched = created = 0
    errors = []
    undated = []
    limiter = asyncio.Semaphore(4)

    async def fetch(row):
        async with limiter:
            return await provider.get_news(row["symbol"], 5)

    responses = await asyncio.gather(*(fetch(row) for row in selected), return_exceptions=True)
    for row, items in zip(selected, responses):
        try:
            if isinstance(items, BaseException):
                raise items
            for item in items[:5]:
                title = str(item.get("title") or "").strip()
                if len(title) < 8:
                    continue
                fetched += 1
                name = str(row.get("name") or "").strip()
                direct = row["symbol"] in title or (len(name) >= 3 and name in title)
                # Search hits alone never establish an issuer relationship.
                try:
                    published = datetime.fromisoformat(str(item.get("date")))
                except (ValueError, TypeError):
                    published = None
                if published is None:
                    undated.append({"symbol": row["symbol"], "title": title,
                                    "source": item.get("source"), "url": item.get("url"),
                                    "source_date": item.get("date"), "reason": "publication_time_unknown"})
                    continue  # never use receipt time as a fresh publication
                if published and published.tzinfo:
                    published = published.astimezone(BJ_TZ).replace(tzinfo=None)
                _, is_new = store.register(
                    title, source=item.get("source") or "东财",
                    url=item.get("url"), summary=item.get("summary"),
                    published_at=published, source_published_at=published,
                    source_item_id=item.get("source_item_id"),
                    source_symbol=row["symbol"] if direct else None,
                    source_symbols=[row["symbol"]] if direct else [], theme_names=[],
                )
                created += int(is_new)
        except Exception as exc:
            errors.append({"symbol": row["symbol"], "error": type(exc).__name__})
    return {"symbols": len(selected), "fetched": fetched, "created": created,
            "errors": errors, "undated_news": undated, "scope": "price_top20_company_headlines",
            "public_theme_linkage": "unknown"}


async def tick(service, sf, provider, days, output, *, now=None, news_due=True):
    now = now or beijing_now()
    receipt = {"checked_at": now.isoformat(), "valid_observations": False}
    if not eligible(now, days):
        receipt.update(state="closed_or_outside_session", inserted=0)
        return receipt
    await service.refresh()
    rows, source_time = service.versioned_snapshot()
    if service.freshness(live=True).state != "ready" or source_time is None:
        receipt.update(state="source_unavailable", inserted=0)
        return receipt
    captured_at = beijing_now()
    refs = await asyncio.to_thread(load_evidence, captured_at, sf)
    result = await asyncio.to_thread(
        capture, rows, refs, as_of=captured_at, source_as_of=source_time,
        trading_days=days, source=service.last_snapshot_source, session_factory=sf,
    )
    receipt.update(result, valid_observations=result["state"] in ("ready", "closing_census"))
    if news_due:
        receipt["news"] = await company_news(rows, provider, EventStore(sf))
    write_json(output / f"review-{now.date()}.json", await asyncio.to_thread(summary, str(now.date()), sf))
    return receipt


async def run(output: Path, calendar_path: Path, until: datetime, once: bool):
    output = output_dir(output)
    if until.tzinfo is None or not beijing_now() < until <= beijing_now() + timedelta(days=31):
        raise ValueError("until must be aware, in the future and within 31 days")
    days = calendar(calendar_path)
    if days[-1] < until.astimezone(BJ_TZ).date():
        raise ValueError("calendar must cover the full runtime window")
    # Single writer; exiting releases the OS lock even after a crash.
    with (output / "runtime.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        engine = create_engine(f"sqlite:///{output / 'research.db'}")
        Base.metadata.create_all(engine)  # isolated only; no production DB helper
        sf = sessionmaker(engine, expire_on_commit=False)
        service = MarketSnapshotService(60, 300, output / "parquet")
        provider = EastmoneyProvider(timeout=8)
        next_news = 0.0
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        reason = "deadline"
        try:
            while beijing_now() < until and not stop.is_set() and not (output / "STOP").exists():
                if sum(p.stat().st_size for p in output.rglob("*") if p.is_file()) > 512 * 1024 * 1024:
                    reason = "disk_budget_exceeded"
                    break
                started = asyncio.get_running_loop().time()
                try:
                    receipt = await asyncio.wait_for(tick(
                        service, sf, provider, days, output, news_due=started >= next_news,
                    ), timeout=240)
                    if "news" in receipt:
                        next_news = started + 1800
                except Exception as exc:
                    receipt = {"state": "error", "error": type(exc).__name__,
                               "checked_at": beijing_now().isoformat(), "valid_observations": False}
                write_json(output / "health.json", {**receipt, "until": until.isoformat()})
                with (output / "receipts.jsonl").open("a") as target:
                    target.write(json.dumps(receipt, ensure_ascii=False) + "\n")
                print(json.dumps(receipt, ensure_ascii=False), flush=True)
                if once:
                    break
                # No market requests during closed sessions. Bounded disk heartbeat.
                elapsed = asyncio.get_running_loop().time() - started
                await wait_or_stop(stop, idle_delay(beijing_now(), days) if receipt["state"] == "closed_or_outside_session" else
                                   900 if receipt["state"] == "error" else max(1, 60 - elapsed))
            if not once:
                reason = "operator_stop" if stop.is_set() or (output / "STOP").exists() else reason
                write_json(output / "health.json", {"state": "stopped", "reason": reason,
                           "checked_at": beijing_now().isoformat(), "valid_observations": False})
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
            await provider.aclose()
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--until", type=datetime.fromisoformat, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.output, args.calendar, args.until, args.once))


if __name__ == "__main__":
    main()
