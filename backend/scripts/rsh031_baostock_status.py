"""Collect historical SH/SZ ST flags for an explicit RSH-031 symbol list.

BaoStock is a research-only optional dependency. The output is source evidence,
not proof that every day's flag was visible before the opening auction.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date
from pathlib import Path


CODE = re.compile(r"^\d{6}\.(SH|SZ)$")


def collect_status(api, codes: list[str], output: Path, start: date, end: date,
                   limit: int | None = None, delay: float = 0.2) -> tuple[int, int]:
    if start > end or delay < 0 or limit is not None and limit < 1:
        raise ValueError("invalid collection range or limit")
    if codes != sorted(set(codes)) or any(not CODE.fullmatch(code) for code in codes):
        raise ValueError("codes must be unique, sorted SH/SZ ticker identifiers")
    finished: set[str] = set()
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("complete") is not True or row.get("thscode") in finished:
                raise ValueError("partial or duplicate checkpoint row")
            finished.add(row["thscode"])
        if not finished.issubset(codes):
            raise ValueError("checkpoint contains symbols outside input list")
    login = api.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code}")
    completed = 0
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a", encoding="utf-8") as stream:
            for code in codes:
                if code in finished:
                    continue
                source_code = code[-2:].lower() + "." + code[:6]
                result = api.query_history_k_data_plus(
                    source_code, "date,code,isST,tradestatus",
                    start_date=start.isoformat(), end_date=end.isoformat(),
                    frequency="d", adjustflag="3")
                if result.error_code != "0":
                    raise RuntimeError(f"BaoStock {code}: {result.error_code}")
                rows = []
                days = set()
                while result.next():
                    row = result.get_row_data()
                    if (len(row) != 4 or row[1] != source_code or
                            not start.isoformat() <= row[0] <= end.isoformat() or
                            row[0] in days or row[2] not in ("0", "1", "") or
                            row[3] not in ("0", "1", "")):
                        raise ValueError(f"invalid BaoStock row for {code}")
                    days.add(row[0])
                    rows.append(row)
                if result.error_code != "0":
                    raise RuntimeError(f"BaoStock rows {code}: {result.error_code}")
                stream.write(json.dumps({"thscode": code, "rows": rows,
                                         "complete": True}, sort_keys=True) + "\n")
                stream.flush()
                completed += 1
                if limit is not None and completed >= limit:
                    break
                time.sleep(delay)
    finally:
        api.logout()
    return completed, len(finished) + completed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codes_json", type=Path)
    parser.add_argument("output_jsonl", type=Path)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()
    import baostock  # Optional research-only package; not a backend runtime dependency.

    codes = json.loads(args.codes_json.read_text(encoding="utf-8"))
    completed, total = collect_status(baostock, codes, args.output_jsonl,
                                      args.start, args.end, args.limit, args.delay)
    print(json.dumps({"completed_this_run": completed, "checkpoint_symbols": total,
                      "requested_symbols": len(codes)}))


if __name__ == "__main__":
    main()
