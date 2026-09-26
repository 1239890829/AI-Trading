"""Collect bounded historical limit-up and limit-break pools for RSH-031.

This is an explicitly invoked, read-only research export. It does not update
marketdb, strategy state, or production services. Responses live in ignored
artifacts; source query time is not mistaken for historical availability.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from app.core.config import Settings
from app.data_providers.ths import ThsFuyaoProvider, date_ms


PROTOCOL = "rsh031-recent-pools-v1"
START = date(2025, 10, 9)
EXPLORATION_END = date(2026, 3, 31)
VALIDATION_END = date(2026, 5, 31)
END = date(2026, 8, 31)
PAGE_SIZE = 200
POOLS = ("limit-up-pool", "limit-break-pool")


def trade_dates(db_path: Path) -> list[date]:
    con = duckdb.connect(":memory:")
    try:
        escaped = str(db_path).replace("'", "''")
        con.execute(f"ATTACH '{escaped}' AS source (READ_ONLY)")
        rows = con.execute("""
            SELECT DISTINCT CAST(to_timestamp(date_ms / 1000.0)
                AT TIME ZONE 'Asia/Shanghai' AS DATE) AS d
            FROM source.daily_k WHERE date_ms >= ? AND date_ms < ?
            ORDER BY d
        """, [date_ms(START), date_ms(date(2026, 9, 1))]).fetchall()
        return [row[0] for row in rows]
    finally:
        con.close()


def validate_page(data: dict, page: int) -> tuple[list[dict], dict]:
    items = data.get("item")
    pagination = data.get("pagination")
    if not isinstance(items, list) or not isinstance(pagination, dict):
        raise ValueError("missing items or pagination")
    if pagination.get("page") != page or pagination.get("size") != PAGE_SIZE:
        raise ValueError("pagination page/size mismatch")
    total, pages = pagination.get("total"), pagination.get("pages")
    if not isinstance(total, int) or not isinstance(pages, int) or total < 0 or pages < 1:
        raise ValueError("invalid pagination totals")
    if any(not isinstance(item, dict) or not item.get("thscode") for item in items):
        raise ValueError("missing symbol identity")
    return items, pagination


async def fetch_pool(provider: ThsFuyaoProvider, day: date, pool: str) -> dict:
    path = "/api/a-share/special-data/" + pool
    all_items: list[dict] = []
    pages = None
    total = None
    query_timestamps: list[int | None] = []
    page = 1
    while pages is None or page <= pages:
        data = await provider._get(path, {"date_ms": date_ms(day), "page": page, "size": PAGE_SIZE})
        items, pagination = validate_page(data, page)
        if pages is None:
            pages, total = pagination["pages"], pagination["total"]
        elif pages != pagination["pages"] or total != pagination["total"]:
            raise ValueError("pagination changed during collection")
        all_items.extend(items)
        query_timestamps.append(data.get("timestamp"))
        page += 1
    codes = [str(item["thscode"]) for item in all_items]
    if len(all_items) != total or len(codes) != len(set(codes)):
        raise ValueError("pool count or symbol uniqueness mismatch")
    return {"trade_date": day.isoformat(), "pool": pool, "items": all_items,
            "total": total, "pages": pages, "query_timestamps": query_timestamps}


async def collect(db_path: Path, env_path: Path, output_dir: Path) -> dict:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(output_dir)
    dates = trade_dates(db_path)
    if not dates or dates[0] != START or dates[-1] != END:
        raise ValueError("local trade-date inventory does not cover frozen window")
    settings = Settings(_env_file=str(env_path))
    if not settings.ths_api_key:
        raise ValueError("existing THS credential unavailable")
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = ThsFuyaoProvider(settings.ths_api_key, timeout=20)
    path = output_dir / "pools.jsonl"
    failures: list[dict] = []
    counts = {pool: 0 for pool in POOLS}
    try:
        with path.open("w", encoding="utf-8") as out:
            for index, day in enumerate(dates, 1):
                day_rows = []
                for pool in POOLS:
                    try:
                        row = await fetch_pool(provider, day, pool)
                        day_rows.append(row)
                    except Exception as exc:
                        failures.append({"date": day.isoformat(), "pool": pool,
                                         "error_type": type(exc).__name__,
                                         "error": str(exc)[:160]})
                    await asyncio.sleep(0.25)
                if len(day_rows) == 2:
                    up = {it["thscode"] for it in day_rows[0]["items"]}
                    broken = {it["thscode"] for it in day_rows[1]["items"]}
                    if up & broken:
                        failures.append({"date": day.isoformat(),
                                         "error_type": "PoolOverlap",
                                         "error": f"{len(up & broken)} symbols in both pools"})
                for row in day_rows:
                    counts[row["pool"]] += row["total"]
                    out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                if index % 25 == 0:
                    print(f"collected {index}/{len(dates)} dates; failures={len(failures)}", flush=True)
    finally:
        await provider.aclose()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "protocol": PROTOCOL, "source": "existing_ths_fuyao_special_data_api",
        "window": [START.isoformat(), END.isoformat()],
        "exploration_end": EXPLORATION_END.isoformat(),
        "validation_end": VALIDATION_END.isoformat(),
        "holdout_start": "2026-06-01", "trade_days": len(dates),
        "rows": counts, "failures": failures, "complete": not failures,
        "pools_sha256": digest,
        "source_date_identity": "request date_ms only; response has no echoed trade date",
        "source_timestamp_meaning": "query response time, not historical available_at",
        "point_in_time_text_certified": False,
        "retrieved_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(collect(args.db, args.env, args.output))
    print(json.dumps({key: result[key] for key in ("window", "trade_days", "rows",
                                                "complete", "failures", "pools_sha256")},
                     ensure_ascii=False, indent=2))
    if not result["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
