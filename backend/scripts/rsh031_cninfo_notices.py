"""Collect one public CNINFO disclosure day for bounded RSH-031 audit.

The source's announcementTime is a claimed disclosure time, not an independently
observed first_seen time. This collector does not fetch announcement attachments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from app.core.bjtime import BJ_TZ


URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PAGE_SIZE = 30
MAX_PAGES = 100


def collect_day(day: date, output_dir: Path, session: requests.Session | None = None) -> dict:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(output_dir)
    client = session or requests.Session()
    pages: list[dict] = []
    seen: set[str] = set()
    expected_total: int | None = None
    for page_num in range(1, MAX_PAGES + 1):
        response = client.post(
            URL,
            data={"pageNum": page_num, "pageSize": PAGE_SIZE, "column": "szse",
                  "tabName": "fulltext", "seDate": f"{day}~{day}",
                  "isHLtitle": "true"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("announcements")
        total = payload.get("totalAnnouncement")
        more = payload.get("hasMore")
        if not isinstance(rows, list) or not isinstance(total, int) or not isinstance(more, bool):
            raise ValueError(f"invalid CNINFO page schema: {page_num}")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise ValueError(f"CNINFO total changed during pagination: {page_num}")
        if more and not rows:
            raise ValueError(f"empty CNINFO page with hasMore=true: {page_num}")
        for row in rows:
            announcement_id = row.get("announcementId")
            if not announcement_id or announcement_id in seen:
                raise ValueError(f"missing or duplicate announcement ID: {announcement_id}")
            source_time = row.get("announcementTime")
            if (not isinstance(source_time, int) or
                    datetime.fromtimestamp(source_time / 1000, BJ_TZ).date() != day):
                raise ValueError(f"CNINFO returned another date or missing time: {announcement_id}")
            seen.add(announcement_id)
        pages.append({"page_num": page_num, "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                      "payload": payload})
        if not more:
            break
    else:
        raise ValueError(f"CNINFO exceeded {MAX_PAGES} pages")
    if len(seen) != expected_total:
        raise ValueError(f"CNINFO row count {len(seen)} != source total {expected_total}")
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = "".join(json.dumps(page, ensure_ascii=False, sort_keys=True) + "\n" for page in pages)
    (output_dir / "pages.jsonl").write_text(raw, encoding="utf-8")
    manifest = {
        "source": URL, "requested_date": day.isoformat(), "page_count": len(pages),
        "announcements": len(seen), "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "time_semantics": "source-claimed disclosure time, not verified first_seen",
        "completeness": "hasMore=false; unique IDs and row count equal source total",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("day", type=date.fromisoformat)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(collect_day(args.day, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
