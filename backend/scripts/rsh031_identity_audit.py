"""Audit BaoStock ST/listing evidence against the frozen RSH-031 sample and pools."""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
from pathlib import Path

import duckdb


def _sql_path(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def audit(codes_path: Path, status_path: Path, basic_path: Path,
          pools_path: Path, samples_path: Path, output_dir: Path) -> dict:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(output_dir)
    codes = json.loads(codes_path.read_text(encoding="utf-8"))
    if codes != sorted(set(codes)):
        raise ValueError("symbol input is not unique and sorted")
    basic = json.loads(basic_path.read_text(encoding="utf-8"))
    if basic["fields"] != ["code", "code_name", "ipoDate", "outDate", "type", "status"]:
        raise ValueError("BaoStock basic schema changed")
    listing = {}
    for row in basic["rows"]:
        if row[4] == "1" and row[0].startswith(("sh.", "sz.")):
            symbol = row[0][3:] + (".SH" if row[0].startswith("sh.") else ".SZ")
            listing[symbol] = row[2]
    if any(not listing.get(code) for code in codes):
        raise ValueError("missing IPO date in BaoStock basic data")

    output_dir.mkdir(parents=True, exist_ok=True)
    status_csv = output_dir / "status.csv"
    completed = set()
    source_rows = 0
    with status_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["trade_date", "thscode", "is_st", "trade_status"])
        for line in status_path.open(encoding="utf-8"):
            item = json.loads(line)
            code = item["thscode"]
            if item.get("complete") is not True or code in completed or code not in codes:
                raise ValueError("incomplete or duplicate BaoStock checkpoint")
            completed.add(code)
            source_code = code[-2:].lower() + "." + code[:6]
            dates = set()
            for row in item["rows"]:
                if (len(row) != 4 or row[1] != source_code or row[0] in dates or
                        row[2] not in ("0", "1", "") or row[3] not in ("0", "1", "")):
                    raise ValueError(f"invalid BaoStock row for {code}")
                dates.add(row[0])
                writer.writerow([row[0], code, row[2], row[3]])
                source_rows += 1
    if completed != set(codes):
        raise ValueError(f"BaoStock checkpoint covers {len(completed)}/{len(codes)} codes")

    events = []
    days = set()
    for line in pools_path.open(encoding="utf-8"):
        page = json.loads(line)
        days.add(page["trade_date"])
        if page["pool"] == "limit-up-pool":
            events.extend((page["trade_date"], item["thscode"], bool(item["is_st"]))
                          for item in page["items"])
    calendar = sorted(days)
    if not calendar:
        raise ValueError("empty source pool calendar")
    ipo_first_five_pool = sum(
        1 for day, code, _ in events
        if listing[code] >= calendar[0]
        and 1 <= bisect.bisect_right(calendar, day) - bisect.bisect_left(calendar, listing[code]) <= 5
    )
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"""
            CREATE TEMP TABLE status AS SELECT trade_date::DATE AS trade_date, thscode,
                   NULLIF(is_st, '')::INTEGER AS is_st,
                   NULLIF(trade_status, '')::INTEGER AS trade_status
            FROM read_csv({_sql_path(status_csv)}, auto_detect=false, header=true,
                 columns={{'trade_date':'VARCHAR','thscode':'VARCHAR',
                           'is_st':'VARCHAR','trade_status':'VARCHAR'}})
        """)
        con.execute("CREATE TEMP TABLE events(trade_date DATE, thscode VARCHAR, source_st BOOLEAN)")
        con.executemany("INSERT INTO events VALUES (?, ?, ?)", events)
        sample = con.execute(f"""
            SELECT count(*) AS stock_days,
                   count(*) FILTER (WHERE s.thscode IS NULL) AS missing_status,
                   count(*) FILTER (WHERE s.thscode IS NOT NULL AND s.is_st IS NULL) AS unknown_st,
                   count(*) FILTER (WHERE s.is_st = 1) AS st_days,
                   count(*) FILTER (WHERE s.trade_status = 0) AS suspended_days
            FROM read_parquet({_sql_path(samples_path)}) x
            LEFT JOIN status s USING (trade_date, thscode)
        """).fetchone()
        pool = con.execute("""
            SELECT count(*) AS source_up_rows,
                   count(*) FILTER (WHERE s.thscode IS NULL) AS missing_status,
                   count(*) FILTER (WHERE s.thscode IS NOT NULL AND s.is_st IS NULL) AS unknown_st,
                   count(*) FILTER (WHERE s.is_st IS NOT NULL AND
                                         s.is_st != e.source_st::INTEGER) AS conflicting_st
            FROM events e LEFT JOIN status s USING (trade_date, thscode)
        """).fetchone()
    finally:
        con.close()
    result = {
        "source": "BaoStock 0.9.4 daily isST/tradestatus + stock_basic ipoDate",
        "requested_symbols": len(codes), "completed_symbols": len(completed),
        "source_status_rows": source_rows,
        "sample": dict(zip(("stock_days", "missing_status", "unknown_st",
                            "st_days", "suspended_days"), sample)),
        "limit_up_pool": dict(zip(("source_up_rows", "missing_status", "unknown_st",
                                   "conflicting_st"), pool)),
        "window_ipo_first_five_in_limit_up_pool": ipo_first_five_pool,
        "point_in_time_publication_certified": False,
        "source_status_sha256": hashlib.sha256(status_path.read_bytes()).hexdigest(),
        "source_basic_sha256": hashlib.sha256(basic_path.read_bytes()).hexdigest(),
        "source_pool_sha256": hashlib.sha256(pools_path.read_bytes()).hexdigest(),
    }
    (output_dir / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("codes_json", "status_jsonl", "basic_json", "pools_jsonl",
                 "samples_parquet", "output_dir"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.codes_json, args.status_jsonl, args.basic_json,
                           args.pools_jsonl, args.samples_parquet, args.output_dir),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
