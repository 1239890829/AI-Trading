"""Freeze public event revisions and their actual opportunity rationale, read-only.

No model calls, no hindsight reconstruction of missing snapshot reasons, no gold
labels. Output is an offline review packet, never an operational recommendation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    return parsed


def freeze(db_path: Path, *, as_of: str, limit: int = 20) -> dict:
    cutoff = _time(as_of)
    if not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        # Hold one SQLite read snapshot throughout counts, links and versions.
        db.execute("BEGIN")
        revisions = db.execute(
            "SELECT COUNT(*) FROM (SELECT event_id FROM event_interpretation "
            "WHERE effective_at <= ? GROUP BY event_id HAVING COUNT(*)>1)",
            (cutoff.isoformat(sep=" "),),
        ).fetchone()[0]
        linked = db.execute(
            "SELECT COUNT(*) FROM opportunity_decision_snapshot WHERE as_of <= ? "
            "AND json_array_length(json_extract(evidence,'$.event_refs'))>0",
            (cutoff.isoformat(sep=" "),),
        ).fetchone()[0]
        rows = db.execute(
            "SELECT s.snapshot_id,s.as_of,s.evidence,j.value AS ref "
            "FROM opportunity_decision_snapshot s,json_each(s.evidence,'$.event_refs') j "
            "WHERE s.as_of <= ? ORDER BY s.as_of DESC,s.id DESC,j.key LIMIT ?",
            (cutoff.isoformat(sep=" "), limit),
        ).fetchall()
        packets, rejected = [], []
        for row in rows:
            ref, evidence = json.loads(row["ref"]), json.loads(row["evidence"])
            identity = {"snapshot_id": row["snapshot_id"], "event_ref": ref}
            event_id, version_id = ref.get("event_id"), ref.get("version_id")
            basis = (evidence.get("event_rationale") or {}).get("basis")
            if (type(event_id) is not int or type(version_id) is not int
                    or event_id <= 0 or version_id <= 0 or not isinstance(basis, str) or not basis.strip()):
                rejected.append({**identity, "reason": "missing_frozen_identity_or_rationale"})
                continue
            versions = db.execute(
                "SELECT i.*,o.title,o.summary,o.content_hash,o.available_at,o.source "
                "FROM event_interpretation i JOIN event_observation o ON o.id=i.observation_id "
                "AND o.event_id=i.event_id WHERE i.event_id=? AND i.effective_at<=? "
                "ORDER BY i.effective_at,i.id",
                (event_id, cutoff.isoformat(sep=" ")),
            ).fetchall()
            visible = [v for v in versions if _time(v["effective_at"]) <= _time(row["as_of"])]
            old = visible[-1] if visible else None
            new = versions[-1] if versions else None
            if (old is None or new is None or old["state"] != "active"
                    or old["id"] != version_id or new["id"] == old["id"]
                    or old["observation_id"] != ref.get("observation_id")
                    or new["observation_id"] == old["observation_id"]
                    or any(_time(v["available_at"]) > _time(v["effective_at"]) for v in (old, new))):
                rejected.append({**identity, "reason": "no_visible_bound_revision_pair"})
                continue
            def source(v):
                return {k: v[k] for k in ("id", "observation_id", "effective_at", "state",
                                          "available_at", "source", "title", "summary", "content_hash")}
            packets.append({**identity, "decision_as_of": row["as_of"],
                            "rationale": basis, "before": source(old), "after": source(new),
                            "human": {"reason_changed": None, "support": None, "notes": ""},
                            "recommendation": None, "adopted": False,
                            "fallback": "offline_evidence_only"})
    report = {"contract": "event-rationale-blind-v1", "as_of": as_of,
              "revised_events": revisions, "linked_snapshots": linked,
              "scanned_refs": len(rows), "scan_limit": limit,
              "scan_complete": len(rows) < limit, "packets": packets, "rejected": rejected,
              "ready_pairs": len(packets), "human_complete": 0,
              "state": "ready_for_independent_review" if packets else "missing_linked_revision_evidence"}
    report["packet_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--as-of", required=True, help="Decision-visible cutoff, Beijing time")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    report = freeze(args.db, as_of=args.as_of, limit=args.limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.out)
    print(json.dumps({k: report[k] for k in ("state", "revised_events", "linked_snapshots", "ready_pairs", "packet_sha256")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
