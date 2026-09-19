"""Build and score Jev human-gold datasets without treating rule labels as truth."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "ashare.db"
CATEGORIES = ("policy", "statement", "data", "rumor", "corporate", "other")
CERTAINTIES = ("done", "proposed", "rumor")


def _stable_key(seed: str, event_id: int) -> str:
    return hashlib.sha256(f"{seed}:{event_id}".encode()).hexdigest()


def _load_events(db_path: Path) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            SELECT id,published_at,title,summary,source,source_tier,source_symbol,
                   category,certainty,fact_kind
            FROM event_card
            ORDER BY id
            """
        ).fetchall()
        directions: dict[int, list[dict]] = defaultdict(list)
        for event_id, target_type, target, direction in con.execute(
            "SELECT event_id,target_type,target,direction FROM event_direction ORDER BY id"
        ):
            directions[int(event_id)].append({
                "target_type": target_type,
                "target": target,
                "direction": int(direction or 0),
            })
    finally:
        con.close()

    out = []
    for row in rows:
        (
            event_id, published_at, title, summary, source, source_tier,
            source_symbol, category, certainty, fact_kind,
        ) = row
        dirs = directions.get(int(event_id), [])
        out.append({
            "event_id": int(event_id),
            "published_at": published_at,
            "title": title,
            "summary": summary,
            "source": source,
            "source_tier": int(source_tier or 0),
            "source_symbol": source_symbol,
            "reference_rule": {
                "category": category,
                "certainty": certainty,
                "fact_kind": fact_kind,
                "actionable": any(int(d["direction"]) != 0 for d in dirs),
                "directions": dirs,
            },
            "human": {
                "category": None,
                "certainty": None,
                "actionable": None,
                "notes": "",
            },
        })
    return out


def stratified_sample(rows: list[dict], target: int, seed: str) -> list[dict]:
    if not 1 <= target <= 500:
        raise ValueError("target must be between 1 and 500")
    groups: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    for row in rows:
        groups.setdefault(str(row["reference_rule"]["category"]), []).append(row)
    for values in groups.values():
        values.sort(key=lambda r: _stable_key(seed, r["event_id"]))

    active = [c for c in CATEGORIES if groups.get(c)]
    if not active:
        return []
    selected: list[dict] = []
    per = max(1, target // len(active))
    for category in active:
        selected.extend(groups[category][:per])

    selected_ids = {r["event_id"] for r in selected}
    remainder = [r for r in rows if r["event_id"] not in selected_ids]
    remainder.sort(key=lambda r: _stable_key(seed + ":fill", r["event_id"]))
    selected.extend(remainder[: max(0, target - len(selected))])
    selected = selected[:target]
    selected.sort(key=lambda r: (str(r["reference_rule"]["category"]), _stable_key(seed, r["event_id"])))
    return selected


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict]:
    out = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{lineno}: row must be object")
        out.append(row)
    return out


def validate_rows(rows: list[dict], *, require_human: bool = False) -> dict:
    errors: list[str] = []
    ids: set[int] = set()
    human_complete = 0
    for idx, row in enumerate(rows, 1):
        event_id = row.get("event_id")
        if not isinstance(event_id, int) or event_id in ids:
            errors.append(f"row {idx}: invalid/duplicate event_id")
        if isinstance(event_id, int):
            ids.add(event_id)
        human = row.get("human")
        if not isinstance(human, dict):
            errors.append(f"row {idx}: missing human object")
            continue
        category = human.get("category")
        certainty = human.get("certainty")
        actionable = human.get("actionable")
        complete = (
            category in CATEGORIES
            and certainty in CERTAINTIES
            and isinstance(actionable, bool)
        )
        if complete:
            human_complete += 1
        elif require_human:
            errors.append(f"row {idx}: human labels incomplete/invalid")
    return {
        "rows": len(rows),
        "human_complete": human_complete,
        "errors": errors,
        "ok": not errors,
    }


def _metric(rows: list[tuple[object, object]]) -> dict:
    usable = [(truth, pred) for truth, pred in rows if truth is not None and pred is not None]
    correct = sum(truth == pred for truth, pred in usable)
    confusion: dict[str, Counter] = defaultdict(Counter)
    for truth, pred in usable:
        confusion[str(truth)][str(pred)] += 1
    return {
        "n": len(usable),
        "correct": correct,
        "accuracy": round(correct / len(usable), 6) if usable else None,
        "confusion": {k: dict(sorted(v.items())) for k, v in sorted(confusion.items())},
    }


def score(labeled: list[dict], predictions: list[dict]) -> dict:
    by_id = {int(r["event_id"]): r for r in predictions if isinstance(r.get("event_id"), int)}
    pairs = {"category": [], "certainty": [], "actionable": []}
    missing_predictions = 0
    for row in labeled:
        event_id = row.get("event_id")
        human = row.get("human") if isinstance(row.get("human"), dict) else {}
        pred = by_id.get(event_id)
        if pred is None:
            missing_predictions += 1
            pred = {}
        for field in pairs:
            pairs[field].append((human.get(field), pred.get(field)))
    return {
        "labeled_rows": len(labeled),
        "prediction_rows": len(predictions),
        "missing_predictions": missing_predictions,
        "metrics": {field: _metric(values) for field, values in pairs.items()},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    exp = sub.add_parser("export-events")
    exp.add_argument("--db", type=Path, default=DEFAULT_DB)
    exp.add_argument("--out", type=Path, required=True)
    exp.add_argument("--count", type=int, default=240)
    exp.add_argument("--seed", default="jev-rsh030-v1")

    val = sub.add_parser("validate")
    val.add_argument("path", type=Path)
    val.add_argument("--require-human", action="store_true")

    sc = sub.add_parser("score")
    sc.add_argument("labeled", type=Path)
    sc.add_argument("predictions", type=Path)

    args = ap.parse_args(argv)
    if args.cmd == "export-events":
        rows = stratified_sample(_load_events(args.db), args.count, args.seed)
        write_jsonl(args.out, rows)
        summary = validate_rows(rows)
        summary["by_reference_category"] = dict(Counter(r["reference_rule"]["category"] for r in rows))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "validate":
        summary = validate_rows(read_jsonl(args.path), require_human=args.require_human)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["ok"] else 2
    if args.cmd == "score":
        out = score(read_jsonl(args.labeled), read_jsonl(args.predictions))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
