"""Preserve complete public inputs, including failures, without repairing predictions."""
from __future__ import annotations

from copy import deepcopy

from scripts.news_classification_contract import sample_content_hash
from scripts.news_context_record import build_research_records


def _sample(identifier="n1"):
    sample = {
        "id": identifier, "title": "公司发布事件与风险公告", "source": "公开媒体转载",
        "summary": "公司称已妥善安排，董事会运作正常。" + "判断资料。" * 160 +
                   "标的尚未盈利，相关业务尚未量产，实际经营影响仍待核。",
        "url": "https://example.com/public-news",
        "published_at": "2026-10-11T10:00:00+08:00",
        "available_at": "2026-10-11T22:00:00+08:00",
        "private_account": "must-not-send", "author_label": "must-not-send",
    }
    sample["content_hash"] = sample_content_hash(sample)
    return sample


def _output(sample):
    return {
        "id": sample["id"], "category": "material_event", "direction": 0, "themes": [],
        "chain": "", "reason": "事实：公司报告该事件。解释：实际影响待核。",
        "evidence": [{"field": "title", "quote": sample["title"]}],
        # Deliberately incomplete model selection must not remove the source.
        "key_conditions": [{"field": "summary", "quote": "董事会运作正常"}],
        "impact_object": "公司", "uncertainties": ["经营影响待核"],
        "event_status": "factual", "novelty": "unavailable",
    }


def test_source_context_preserves_early_and_late_limitations_without_private_fields():
    sample = _sample()
    original = deepcopy(sample)
    record = build_research_records([_output(sample)], [sample])[0]
    source = record["source_context"]
    assert source["summary"] == sample["summary"]
    assert source["content_hash"] == sample_content_hash(source)
    assert source["summary"].endswith("实际经营影响仍待核。")
    assert source["available_at"] == sample["available_at"]
    assert "private_account" not in source and "author_label" not in source
    assert record["contract_state"] == "contract_accepted"
    assert record["semantic_verified"] is False and record["review_required"] is True
    assert sample == original


def test_misquoted_output_is_unknown_without_fuzzy_repair_or_loss_of_source():
    sample = _sample()
    output = _output(sample)
    output["evidence"] = [{"field": "summary", "quote": "公司已经独立证实经营正常"}]
    original = deepcopy(output)
    record = build_research_records([output], [sample])[0]
    assert record["contract_state"] == "unknown" and record["unverified_model_output"] is None
    assert record["contract_errors"] and record["source_context"]["summary"] == sample["summary"]
    assert output == original


def test_missing_duplicate_and_transport_unknown_retains_every_source_in_order():
    samples = [_sample("a"), _sample("b")]
    for raw in (None, [], [_output(samples[0]), _output(samples[0])]):
        records = build_research_records(raw, samples)
        assert [r["id"] for r in records] == ["a", "b"]
        assert all(r["unverified_model_output"] is None and r["contract_state"] == "unknown" for r in records)
        assert all(r["source_context"]["summary"] == s["summary"] for r, s in zip(records, samples))


def test_prior_context_and_input_identity_are_preserved_as_supplied():
    sample = _sample()
    sample["prior_version"] = {"title": "旧消息", "summary": "旧消息已有条件"}
    sample["content_hash"] = sample_content_hash(sample)
    record = build_research_records([], [sample])[0]
    assert record["source_context"]["prior_version"] == sample["prior_version"]
    assert sample_content_hash(record["source_context"]) == sample["content_hash"]


def test_external_id_injection_marks_batch_unknown_but_retains_input():
    sample = _sample()
    extra = dict(_output(sample), id="external")
    record = build_research_records([_output(sample), extra], [sample])[0]
    assert record["unverified_model_output"] is None and record["contract_state"] == "unknown"
    assert record["batch_errors"] and record["source_context"]["id"] == sample["id"]
