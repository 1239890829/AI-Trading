"""One frozen, offline descriptive study; never writes marketdb or promotes rules.

The source is read-only. Temporary SQL spill files have a private lifecycle. All
three attempts are retained, including unknowns and immature forward outcomes.
The report is a reference close-to-close proxy, not actual fills or admission.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
import hashlib
from itertools import groupby
import json
import math
from pathlib import Path
import sys
import tempfile

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.bjtime import BJ_TZ  # noqa: E402
from app.picks._strategy_reference_rules import (  # noqa: E402
    BASE_DEFINITION_DIGEST as DEFINITION_DIGEST, REFERENCE_STOCK_PATTERN, BASE_RULES as RULES,
    iter_reference_rules, reference_rule_metadata,
)
from app.research import strategy_verify as sv  # noqa: E402

HORIZON = 5


def _day(ms):
    return datetime.fromtimestamp(ms / 1000, BJ_TZ).date().isoformat()


def _finite(value):
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _quality(path: Path) -> tuple[dict, dict]:
    try:
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes)
        issues = {}
        for issue in raw.get("issues") or []:
            if issue.get("check") == "daily_k_adj.single_day_jump":
                for code, stamp, _change in issue.get("sample") or []:
                    issues[(code, _day(stamp))] = "adjusted_price_discontinuity"
        return issues, {"state": raw.get("status", "unknown"), "saved_at": raw.get("synced_at"),
                        "sha256": hashlib.sha256(raw_bytes).hexdigest(), "known_issues": len(issues),
                        "scope": "saved source-quality report; samples may not enumerate every defect"}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}, {"state": "unknown", "saved_at": None, "sha256": None, "known_issues": None}


def _summaries(events, counts):
    con = duckdb.connect(":memory:", config={"threads": 1})
    try:
        con.execute("CREATE TABLE events(rule_code VARCHAR, date_ms BIGINT, fwd5 DOUBLE, mfwd5 DOUBLE)")
        if events:
            con.executemany("INSERT INTO events VALUES (?,?,?,?)", events)
        results = {}
        cfg = sv.VerifyConfig(table="events", cost_bps=sv.ADMISSION_COST_BPS)
        for key in RULES:
            measured = sv.baseline(con, where=f"rule_code='{key}'", cfg=cfg, horizons=[HORIZON])
            results[key] = {"version": RULES[key]["version"], "states": dict(counts[key]), "signal_count": measured["n"],
                            "metrics": sv.summarize_row(measured, HORIZON, cost_bps=cfg.cost_bps),
                            "effect_conclusion": "descriptive_reference_only", "production_promotion_eligible": False}
        return results
    finally:
        con.close()


def run_verification(source_db: Path, *, through: str, window_sessions: int = 60,
                     quality_report: Path | None = None) -> dict:
    if date.fromisoformat(through).isoformat() != through or not 1 <= window_sessions <= 252:
        raise ValueError("exact cutoff date and 1..252-session observation window required")
    before = (source_db.stat().st_size, source_db.stat().st_mtime_ns)
    issues, quality = _quality(quality_report or source_db.parent / "quality_report.json")
    counts = {key: Counter() for key in RULES}
    unknown_reasons = {key: Counter() for key in RULES}
    events, symbols = [], 0
    input_hash = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix="ashare-reference-study-") as scratch:
        con = duckdb.connect(str(source_db), read_only=True,
                             config={"threads": 1, "memory_limit": "256MB", "temp_directory": scratch})
        try:
            cut_ms = int(datetime.combine(date.fromisoformat(through), datetime.min.time(), BJ_TZ).timestamp() * 1000)
            # Include the whole Beijing source date; do not assume its epoch hour.
            upper = cut_ms + 86_400_000 - 1
            sessions_ms = [r[0] for r in con.execute(
                "SELECT DISTINCT date_ms FROM daily_k WHERE date_ms<=? AND regexp_full_match(thscode,?) ORDER BY date_ms",
                [upper, REFERENCE_STOCK_PATTERN]).fetchall()]
            sessions = [_day(ms) for ms in sessions_ms]
            if through not in sessions:
                raise ValueError("requested source session is unavailable; no fallback date used")
            start = sessions[max(0, len(sessions) - window_sessions)]
            bad_parameters = [value for (code, day) in issues for value in (code, day)]
            bad_sql = ("SELECT * FROM (VALUES " + ",".join("(?,?)" for _ in issues) + ") AS known_bad(thscode,trade_day)"
                       if issues else "SELECT NULL::VARCHAR AS thscode,NULL::VARCHAR AS trade_day WHERE FALSE")
            query = """
                WITH bad AS (""" + bad_sql + """), raw AS (
                    SELECT k.thscode,k.date_ms,a.close_adj,k.volume,
                           CASE WHEN b.thscode IS NULL AND a.close_adj>0 AND isfinite(a.close_adj)
                                THEN a.close_adj END AS outcome_close
                    FROM daily_k k LEFT JOIN daily_k_adj a USING(thscode,date_ms)
                    LEFT JOIN bad b ON k.thscode=b.thscode
                      AND CAST(to_timestamp(k.date_ms/1000) AT TIME ZONE 'Asia/Shanghai' AS DATE)::VARCHAR=b.trade_day
                    WHERE k.date_ms<=? AND regexp_full_match(k.thscode,?)
                ),
                sessions AS (
                    SELECT date_ms, lead(date_ms,5) OVER (ORDER BY date_ms) AS target_ms
                    FROM (SELECT DISTINCT date_ms FROM raw)
                ), px AS (
                    SELECT *,lead(outcome_close,5) OVER w AS next_close,
                           lead(date_ms,5) OVER w AS next_ms
                    FROM raw WINDOW w AS (PARTITION BY thscode ORDER BY date_ms)
                ), forward AS (
                    SELECT p.*, CASE WHEN p.next_ms=s.target_ms AND p.outcome_close>0 AND p.next_close>0
                         THEN (p.next_close/p.outcome_close-1)*100 END AS fwd5
                    FROM px p JOIN sessions s USING(date_ms)
                )
                SELECT thscode,date_ms,close_adj,volume,fwd5,avg(fwd5) OVER(PARTITION BY date_ms) AS mfwd5
                FROM forward ORDER BY thscode,date_ms
            """
            cursor = con.execute(query, [*bad_parameters, upper, REFERENCE_STOCK_PATTERN])

            def rows():
                while batch := cursor.fetchmany(65536):
                    yield from batch

            for code, group in groupby(rows(), key=lambda r: r[0]):
                saved = list(group)
                bars = [{"date": _day(r[1]), "close": r[2], "volume": r[3],
                         "quality_issue": issues.get((code, _day(r[1])))} for r in saved]
                outcomes = {_day(r[1]): (r[1], _finite(r[4]), _finite(r[5])) for r in saved}
                symbols += 1
                for day, verdicts in iter_reference_rules(bars, through=through, since=start, expected_sessions=sessions):
                    for key, verdict in verdicts.items():
                        if key not in RULES:
                            continue  # This immutable historical study contract retains its original three-rule family.
                        counts[key][verdict["state"]] += 1
                        unknown_reasons[key].update(verdict["reason_codes"])
                        if verdict["state"] == "matched":
                            stamp, forward, market = outcomes[day]
                            events.append((key, stamp, forward, market))
                    if day == through:
                        input_hash.update(f"{code}:{next(iter(verdicts.values()))['input_digest']}\n".encode())
        finally:
            con.close()
    after = (source_db.stat().st_size, source_db.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError("source database changed during study; report not issued")
    return {"study_version": 1, "definition_digest": DEFINITION_DIGEST,
            "frozen_rules": {key: value for key, value in reference_rule_metadata().items() if key in RULES}, "trial_family": list(RULES), "trials_total": len(RULES),
            "requested_through": through, "window_start": start, "window_sessions": min(window_sessions, len(sessions)),
            "source_symbols": symbols, "source_basis": "daily_k rows present; not a certified full historical universe",
            "stock_scope": REFERENCE_STOCK_PATTERN,
            "input_prefix_digest": input_hash.hexdigest(), "source_quality": quality,
            "source_signature": {"size": before[0], "mtime_ns": before[1]},
            "results": _summaries(events, counts), "unknown_reasons": {k: dict(v) for k, v in unknown_reasons.items()},
            "horizon": HORIZON, "cost_bps": sv.ADMISSION_COST_BPS,
            "return_identity": sv.RETURN_IDENTITY_REFERENCE_PROXY,
            "limitations": ["Read-only descriptive study, not untouched holdout or an admission receipt.",
                            "Close-to-close reference proxy; no actual fills, slippage or production shadow evidence.",
                            "The full three-rule trial family is retained; no parameter or winner search.",
                            "Missing exact T+5 market-session prices remain unknown, not later observations or zero.",
                            "Current-snapshot identities, finances and static theme memberships are not historical inputs."],
            "production_promotion_eligible": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--through", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--window-sessions", type=int, default=60)
    parser.add_argument("--quality-report", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; an old attempt must never be overwritten")
    report = run_verification(args.source_db, through=args.through,
                              window_sessions=args.window_sessions, quality_report=args.quality_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": args.output.name, "definition_digest": DEFINITION_DIGEST,
                      "trials": report["trials_total"], "production_promotion_eligible": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
