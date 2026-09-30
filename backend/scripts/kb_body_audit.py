"""Frozen index-only/body comparison: literal evidence coverage, never alpha/gold."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.picks.kb_routing import load_kb_index, retrieve_kb, snapshot_citations  # noqa: E402

# Selected before running the comparison; these are author-selected lookup probes,
# not independent human labels, ranking queries or stock-performance observations.
TASKS = (
    ("pre_open_event", "KB-STOCK-09", "量化窗口", "news-window"),
    ("pre_open_event", "KB-STOCK-01", "月频", "climate-negative-evidence"),
    ("intraday_pick", "KB-STOCK-11", "一字板", "auction-counterexample"),
    ("intraday_pick", "KB-STOCK-15", "证据不够", "overfit-correction"),
    ("post_close_review", "KB-STOCK-27", "实测否决", "title-already-sufficient"),
    ("post_close_review", "KB-STOCK-32", "盘中口径终判", "appendix-counterevidence"),
    ("intraday_pick", "KB-STOCK-01", "", "unauthorized-example"),
    ("post_close_review", "KB-STOCK-999", "", "missing-entry"),
)


def audit() -> dict:
    started = time.perf_counter()
    index = load_kb_index()
    results = []
    for scenario, kb_id, needle, task in TASKS:
        entry = index.entries.get(kb_id)
        baseline = entry.title if entry else ""
        result = retrieve_kb(scenario, kb_id, max_chars=12000, index=index)
        fragment = result["fragment"]
        ids, refs = snapshot_citations(scenario, [kb_id], index, fragments=[fragment] if fragment else [])
        results.append({
            "task": task, "scenario": scenario, "kb_id": kb_id, "needle": needle,
            "without_kb_contains": False,
            "index_contains": bool(needle and needle in baseline),
            "body_contains": bool(needle and fragment and needle in fragment["text"]),
            "retrieval": result["state"], "conflict": result["conflict"],
            "citation_ids": json.loads(ids), "citation_refs": json.loads(refs),
        })
    return {
        "protocol": "rsh027-literal-body-v1",
        "task_sha256": hashlib.sha256(json.dumps(TASKS, ensure_ascii=False).encode()).hexdigest(),
        "tasks": results,
        "counts": {
            "tasks": len(results),
            "without_kb_contains": 0,
            "index_contains": sum(r["index_contains"] for r in results),
            "body_contains": sum(r["body_contains"] for r in results),
            "retrieved": sum(r["retrieval"] == "retrieved" for r in results),
            "rejected": sum(r["retrieval"] == "rejected" for r in results),
            "no_increment": sum(r["index_contains"] and r["body_contains"] for r in results),
        },
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "semantic_support": "unvalidated", "stock_effect": "unvalidated",
        "note": "Same explicit IDs; literal coverage only. No model, external gold, relevance recall, win rate or causal usefulness claim.",
    }


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
