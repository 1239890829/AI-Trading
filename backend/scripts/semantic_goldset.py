"""Build and score provider-independent human-gold datasets without treating rule labels as truth."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
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


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_jsonl(path: Path, rows: list[dict]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        write_jsonl(tmp, rows)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


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


BLIND_FIELDS = (
    "event_id", "published_at", "title", "summary", "source", "source_symbol",
)


def blind_rows(queue: list[dict]) -> list[dict]:
    """Give an annotator source text without rule or model answers."""
    validation = validate_rows(queue)
    if not validation["ok"]:
        raise ValueError(f"queue validation failed: {validation['errors'][:5]}")
    rows = [
        {
            **{field: row.get(field) for field in BLIND_FIELDS},
            "human": {"category": None, "certainty": None,
                      "actionable": None, "notes": ""},
        }
        for row in queue
    ]
    # The original queue is grouped by reference category. Shuffle that order,
    # too, so the annotation order cannot disclose the rule answer.
    rows.sort(key=lambda row: _stable_key("rsh030-blind-v1", row["event_id"]))
    return rows


def import_human(queue: list[dict], annotated: list[dict], *,
                 allow_partial: bool = False) -> list[dict]:
    """Attach independent labels only after exact source-content matching."""
    expected = {row["event_id"]: row for row in blind_rows(queue)}
    received: dict[int, dict] = {}
    for row in annotated:
        event_id = row.get("event_id")
        if not isinstance(event_id, int) or isinstance(event_id, bool) or event_id in received:
            raise ValueError("annotation has invalid/duplicate event_id")
        if event_id not in expected or set(row) != set(expected[event_id]):
            raise ValueError(f"annotation row shape/identity mismatch: {event_id}")
        if any(row[field] != expected[event_id][field] for field in BLIND_FIELDS):
            raise ValueError(f"annotation source content changed: {event_id}")
        human = row["human"]
        if not isinstance(human, dict) or set(human) != set(expected[event_id]["human"]):
            raise ValueError(f"annotation human fields invalid: {event_id}")
        values = (human["category"], human["certainty"], human["actionable"])
        complete = (human["category"] in CATEGORIES
                    and human["certainty"] in CERTAINTIES
                    and isinstance(human["actionable"], bool))
        empty = values == (None, None, None)
        if not complete and not (allow_partial and empty):
            raise ValueError(f"annotation labels incomplete/invalid: {event_id}")
        if not isinstance(human["notes"], str):
            raise ValueError(f"annotation notes invalid: {event_id}")
        received[event_id] = human
    if set(received) != set(expected):
        raise ValueError("annotation event_id coverage differs from frozen queue")
    return [{**row, "human": received[row["event_id"]]} for row in queue]


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


def _unit(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("probability/confidence must be numeric")
    out = float(value)
    if not math.isfinite(out) or not 0.0 <= out <= 1.0:
        raise ValueError("probability/confidence must be in [0,1]")
    return out











def validate_prediction_meta(
    queue_path: Path,
    predictions_path: Path,
    meta_path: Path,
) -> dict:
    """Bind predictions to the exact queue snapshot that produced them."""
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise ValueError("prediction meta must be object")
    queue_sha = file_sha256(queue_path)
    expected_queue_sha = str(meta.get("queue_sha256") or "")
    if expected_queue_sha != queue_sha:
        raise ValueError(
            f"queue hash mismatch: meta={expected_queue_sha or '<missing>'} actual={queue_sha}"
        )
    predictions = read_jsonl(predictions_path)
    expected_rows = int(meta.get("prediction_rows") or -1)
    if expected_rows != len(predictions):
        raise ValueError(
            f"prediction row count mismatch: meta={expected_rows} actual={len(predictions)}"
        )
    return {
        "queue_sha256": queue_sha,
        "prediction_sha256": file_sha256(predictions_path),
        "prediction_rows": len(predictions),
        "model_requested": meta.get("model_requested"),
        "models_returned": meta.get("models_returned"),
    }

def prioritize_review(queue: list[dict], predictions: list[dict]) -> list[dict]:
    """Rank human review by model/rule disagreement and prediction uncertainty."""
    by_id: dict[int, dict] = {}
    for pred in predictions:
        event_id = pred.get("event_id")
        if not isinstance(event_id, int) or event_id in by_id:
            raise ValueError("prediction event_id values must be unique integers")
        by_id[event_id] = pred

    ranked: list[dict] = []
    for row in queue:
        event_id = row.get("event_id")
        pred = by_id.get(event_id)
        if pred is None:
            continue
        ref = row.get("reference_rule") if isinstance(row.get("reference_rule"), dict) else {}
        reasons: list[str] = []
        priority = 0.0

        if pred.get("category") != ref.get("category"):
            reasons.append("category_disagreement")
            priority += 3.0
        if pred.get("certainty") != ref.get("certainty"):
            reasons.append("certainty_disagreement")
            priority += 2.0
        if pred.get("actionable") != ref.get("actionable"):
            reasons.append("actionable_disagreement")
            priority += 2.0

        cat_conf = _unit(pred.get("category_confidence"))
        cert_conf = _unit(pred.get("certainty_confidence"))
        noul = _unit(pred.get("actionable_noul"))
        priority += 1.0 - cat_conf
        priority += 0.75 * (1.0 - cert_conf)
        priority += 1.0 - abs(noul - 0.5) * 2.0

        if cat_conf < 0.75:
            reasons.append("category_low_confidence")
        if cert_conf < 0.75:
            reasons.append("certainty_low_confidence")
        if 0.25 <= noul <= 0.75:
            reasons.append("actionable_uncertain")

        ranked.append({
            "event_id": event_id,
            "priority_score": round(priority, 6),
            "reasons": reasons,
            "title": row.get("title"),
            "summary": row.get("summary"),
            "source": row.get("source"),
            "reference_rule": ref,
            "prediction": pred,
        })

    ranked.sort(key=lambda r: (-float(r["priority_score"]), int(r["event_id"])))
    return ranked


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


def compare_reference(queue: list[dict], predictions: list[dict]) -> dict:
    """Compare candidate predictions with existing rules for triage only. This is NOT accuracy."""
    by_id = {int(r["event_id"]): r for r in predictions if isinstance(r.get("event_id"), int)}
    if len(by_id) != len(predictions):
        raise ValueError("prediction event_id values must be unique integers")

    fields = ("category", "certainty", "actionable")
    comparisons: dict[str, dict] = {}
    for field in fields:
        pairs: list[tuple[object, object]] = []
        for row in queue:
            ref = row.get("reference_rule") if isinstance(row.get("reference_rule"), dict) else {}
            pred = by_id.get(row.get("event_id"))
            if pred is None:
                continue
            pairs.append((ref.get(field), pred.get(field)))
        cross: dict[str, Counter] = defaultdict(Counter)
        agree = 0
        for reference, prediction in pairs:
            agree += int(reference == prediction)
            cross[str(reference)][str(prediction)] += 1
        comparisons[field] = {
            "n": len(pairs),
            "agreement_count": agree,
            "agreement_rate": round(agree / len(pairs), 6) if pairs else None,
            "disagreement_count": len(pairs) - agree,
            "cross": {k: dict(sorted(v.items())) for k, v in sorted(cross.items())},
        }

    ordered_predictions = [by_id[row["event_id"]] for row in queue if row.get("event_id") in by_id]
    cat_conf = [_unit(row.get("category_confidence")) for row in ordered_predictions]
    cert_conf = [_unit(row.get("certainty_confidence")) for row in ordered_predictions]
    noul = [_unit(row.get("actionable_noul")) for row in ordered_predictions]

    def _median(values: list[float]) -> float | None:
        if not values:
            return None
        s = sorted(values)
        mid = len(s) // 2
        value = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
        return round(value, 6)

    def _mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 6) if values else None

    return {
        "warning": "Rule/candidate agreement is diagnostic only; existing rule labels are not human ground truth.",
        "queue_rows": len(queue),
        "prediction_rows": len(predictions),
        "missing_predictions": len(queue) - len(ordered_predictions),
        "agreement_not_accuracy": comparisons,
        "category_confidence": {
            "mean": _mean(cat_conf),
            "median": _median(cat_conf),
            "lt_0_75": sum(value < 0.75 for value in cat_conf),
            "lt_0_60": sum(value < 0.60 for value in cat_conf),
        },
        "certainty_confidence": {
            "mean": _mean(cert_conf),
            "median": _median(cert_conf),
            "lt_0_75": sum(value < 0.75 for value in cert_conf),
            "lt_0_60": sum(value < 0.60 for value in cert_conf),
        },
        "actionable_noul": {
            "mean": _mean(noul),
            "median": _median(noul),
            "le_0_05": sum(value <= 0.05 for value in noul),
            "le_0_10": sum(value <= 0.10 for value in noul),
            "le_0_20": sum(value <= 0.20 for value in noul),
            "uncertain_0_25_0_75": sum(0.25 <= value <= 0.75 for value in noul),
            "ge_0_80": sum(value >= 0.80 for value in noul),
            "ge_0_90": sum(value >= 0.90 for value in noul),
            "ge_0_95": sum(value >= 0.95 for value in noul),
        },
    }


def score(labeled: list[dict], predictions: list[dict], *,
          allow_partial: bool = False) -> dict:
    validation = validate_rows(labeled, require_human=not allow_partial)
    if not validation["ok"]:
        raise ValueError(f"human labels incomplete/invalid: {validation['errors'][:5]}")
    if allow_partial:
        labeled = [row for row in labeled if (
            row["human"].get("category") in CATEGORIES
            and row["human"].get("certainty") in CERTAINTIES
            and isinstance(row["human"].get("actionable"), bool)
        )]
    by_id = {int(r["event_id"]): r for r in predictions if isinstance(r.get("event_id"), int)}
    if len(by_id) != len(predictions):
        raise ValueError("prediction event_id values must be unique integers")
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
    exp.add_argument("--seed", default="semantic-rsh030-v1")

    val = sub.add_parser("validate")
    val.add_argument("path", type=Path)
    val.add_argument("--require-human", action="store_true")

    blind = sub.add_parser("export-blind")
    blind.add_argument("queue", type=Path)
    blind.add_argument("--out", type=Path, required=True)
    blind.add_argument("--meta-out", type=Path, required=True)

    imp = sub.add_parser("import-human")
    imp.add_argument("queue", type=Path)
    imp.add_argument("annotated", type=Path)
    imp.add_argument("--blind-meta", type=Path, required=True)
    imp.add_argument("--out", type=Path, required=True)
    imp.add_argument("--allow-partial", action="store_true")

    pri = sub.add_parser("prioritize")
    pri.add_argument("queue", type=Path)
    pri.add_argument("predictions", type=Path)
    pri.add_argument("--prediction-meta", type=Path, required=True)
    pri.add_argument("--out", type=Path, required=True)
    pri.add_argument("--meta-out", type=Path, required=True)

    cmp = sub.add_parser("compare-reference")
    cmp.add_argument("queue", type=Path)
    cmp.add_argument("predictions", type=Path)
    cmp.add_argument("--prediction-meta", type=Path, required=True)
    cmp.add_argument("--out", type=Path, required=True)

    sc = sub.add_parser("score")
    sc.add_argument("labeled", type=Path)
    sc.add_argument("predictions", type=Path)
    sc.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow scoring only the subset with complete human labels.",
    )

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
    if args.cmd == "export-blind":
        if len({args.queue.resolve(), args.out.resolve(), args.meta_out.resolve()}) != 3:
            raise ValueError("queue, blind output and metadata must be distinct files")
        queue = read_jsonl(args.queue)
        rows = blind_rows(queue)
        atomic_write_jsonl(args.out, rows)
        meta = {
            "queue_sha256": file_sha256(args.queue),
            "blind_sha256": file_sha256(args.out),
            "rows": len(rows),
            "instructions": "Annotate blind rows before viewing rule/candidate predictions.",
        }
        atomic_write_json(args.meta_out, meta)
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "import-human":
        paths = {args.queue.resolve(), args.annotated.resolve(),
                 args.blind_meta.resolve(), args.out.resolve()}
        if len(paths) != 4:
            raise ValueError("queue, annotations, metadata and output must be distinct files")
        meta = json.loads(args.blind_meta.read_text(encoding="utf-8"))
        if meta.get("queue_sha256") != file_sha256(args.queue):
            raise ValueError("frozen queue SHA-256 mismatch")
        queue = read_jsonl(args.queue)
        expected_blind = blind_rows(queue)
        expected_bytes = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in expected_blind
        ).encode("utf-8")
        if meta.get("blind_sha256") != hashlib.sha256(expected_bytes).hexdigest():
            raise ValueError("blind export SHA-256 mismatch")
        labeled = import_human(
            queue, read_jsonl(args.annotated), allow_partial=args.allow_partial
        )
        atomic_write_jsonl(args.out, labeled)
        result = validate_rows(labeled, require_human=not args.allow_partial)
        result["partial"] = result["human_complete"] < result["rows"]
        result["queue_sha256"] = meta["queue_sha256"]
        result["labeled_sha256"] = file_sha256(args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "prioritize":
        provenance = validate_prediction_meta(
            args.queue, args.predictions, args.prediction_meta
        )
        queue = read_jsonl(args.queue)
        predictions = read_jsonl(args.predictions)
        ranked = prioritize_review(queue, predictions)
        if len(ranked) != len(queue):
            raise ValueError(
                f"prediction coverage incomplete: ranked={len(ranked)} queue={len(queue)}"
            )
        atomic_write_jsonl(args.out, ranked)
        reasons = Counter(reason for row in ranked for reason in row["reasons"])
        summary = {
            "rows": len(ranked),
            "provenance": provenance,
            "review_priority_sha256": file_sha256(args.out),
            "reason_counts": dict(sorted(reasons.items())),
            "top_event_ids": [row["event_id"] for row in ranked[:20]],
        }
        atomic_write_json(args.meta_out, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "compare-reference":
        provenance = validate_prediction_meta(
            args.queue, args.predictions, args.prediction_meta
        )
        out = compare_reference(read_jsonl(args.queue), read_jsonl(args.predictions))
        out["provenance"] = provenance
        atomic_write_json(args.out, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "score":
        labeled = read_jsonl(args.labeled)
        validation = validate_rows(
            labeled,
            require_human=not args.allow_partial,
        )
        if not validation["ok"]:
            raise ValueError(
                "human labels incomplete/invalid; run validate --require-human "
                "or pass --allow-partial for progress-only scoring"
            )
        out = score(labeled, read_jsonl(args.predictions),
                    allow_partial=args.allow_partial)
        out["human_complete"] = validation["human_complete"]
        out["human_total"] = validation["rows"]
        out["partial"] = validation["human_complete"] < validation["rows"]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    raise SystemExit(main())
