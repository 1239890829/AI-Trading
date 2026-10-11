"""LAB records retain all supplied public context independently of model output.

This does not fetch sources or prove their completeness. It does not repair
model quotes, classify news, or write to production consumers.
"""
from __future__ import annotations

import json

try:
    from scripts.news_classification_contract import (
        CONTEXT_SCHEMA_VERSION, build_messages, validate_response,
    )
except ModuleNotFoundError:  # direct invocation from backend/scripts
    from news_classification_contract import (
        CONTEXT_SCHEMA_VERSION, build_messages, validate_response,
    )


def build_research_records(raw: str | list | None, samples: list[dict], *,
                           protocol: str = CONTEXT_SCHEMA_VERSION) -> list[dict]:
    """Retain each projected input even when a model item is missing or rejected.

    contract_accepted means structure and literal binding only. All semantic
    interpretations remain unverified. Source context is not model-selected.
    """
    public = json.loads(build_messages(samples, protocol=protocol)[1]["content"])
    report = validate_response(raw, samples, protocol=protocol)
    # Batch mapping errors make the whole batch unusable. Individual contract
    # errors still retain accepted peers and every original source record.
    accepted = {} if report["batch_errors"] else {row["id"]: row for row in report["valid"]}
    errors = {row["id"]: row["reasons"] for row in report["invalid"]}
    return [{
        "record_version": "news-context-record-v1",
        "id": sample["id"],
        "source_context": sample,
        "unverified_model_output": accepted.get(sample["id"]),
        "contract_state": "contract_accepted" if sample["id"] in accepted else "unknown",
        "contract_errors": errors.get(sample["id"], []),
        "batch_errors": list(report["batch_errors"]),
        "semantic_verified": False,
        "review_required": True,
    } for sample in public]
