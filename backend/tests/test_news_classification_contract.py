"""Research contract boundaries; these synthetic cases are not human gold."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from scripts import news_classification_contract as contract


def _sample(identifier="news-1", title="公司拟收购存储设备企业", summary="", prior=None):
    row = {
        "id": identifier, "title": title, "summary": summary, "source": "公开公告",
        "url": "https://example.com/news", "published_at": "2026-10-09T15:30:00+08:00",
        "available_at": "2026-10-09T15:35:00+08:00",
    }
    if prior is not None:
        row["prior_version"] = prior
    row["content_hash"] = contract.sample_content_hash(row)
    return row


def _result(sample, **updates):
    row = {
        "id": sample["id"], "category": "material_event", "direction": 0, "themes": [],
        "chain": "", "reason": "原文报告拟议收购，是否完成仍未知。",
        "evidence": [{"field": "title", "quote": sample["title"]}],
        "uncertainties": ["交易完成情况未知"], "event_status": "proposed",
        "novelty": "unavailable",
    }
    row.update(updates)
    return row


@pytest.mark.parametrize("title,updates", [
    ("机器人概念走强，多股涨停", {
        "category": "market_action", "event_status": "factual", "uncertainties": [],
        "reason": "只有行情表现，未给出新增事件驱动。",
    }),
    ("公司拟收购存储设备企业", {}),
    ("公司澄清未参与苹果玻璃基板项目", {
        "event_status": "denied", "direction": -1, "themes": ["玻璃基板"],
        "reason": "原文否认参与该项目，不能据此确认相关订单。",
        "chain": "假设：项目预期被否认，可能削弱相关经营预期。",
    }),
    ("上游原料涨价，下游采购成本上升", {
        "event_status": "factual", "reason": "供应者与采购者的影响方向不同。",
        "uncertainties": ["未指定受影响对象，不能给出统一正向结论。"],
    }),
    ("关于举办投资者交流会的通知", {
        "category": "routine", "event_status": "proposed",
        "reason": "例行会议安排，没有具体经营变化。", "uncertainties": [],
    }),
    ("某企业传闻受到关注", {
        "category": "insufficient", "event_status": "rumor",
        "reason": "文本缺少事件内容和确认依据。", "uncertainties": ["传闻内容未知。"],
    }),
])
def test_contract_accepts_explicit_status_scope_and_missingness(title, updates):
    sample = _sample(title=title)
    report = contract.validate_response([_result(sample, **updates)], [sample])
    assert report["ok"] and report["valid_count"] == 1


def test_messages_bind_frozen_input_and_exclude_undeclared_private_fields():
    sample = _sample()
    sample["account_balance"] = "private-value"
    sample["reference_rule"] = {"category": "do not leak labels"}
    messages = contract.build_messages([sample])
    sent = json.loads(messages[1]["content"])[0]
    assert "account_balance" not in sent and "reference_rule" not in sent
    assert sent["content_hash"] == sample["content_hash"]
    sample["title"] += "已完成"
    with pytest.raises(ValueError, match="content_hash mismatch"):
        contract.build_messages([sample])


def test_prior_version_is_required_for_novelty_and_grounded_prior_quote():
    sample = _sample()
    result = _result(sample, novelty="new")
    report = contract.validate_response([result], [sample])
    assert report["invalid_count"] == 1 and not report["valid"]
    prior = {"title": sample["title"], "summary": ""}
    sample = _sample(prior=prior)
    result = _result(sample, novelty="repeat", evidence=[
        {"field": "prior_version.title", "quote": prior["title"]},
    ])
    assert contract.validate_response([result], [sample])["ok"]
    original_hash = sample["content_hash"]
    sample["prior_version"]["title"] = "另一项此前公告"
    assert contract.sample_content_hash(sample) != original_hash


@pytest.mark.parametrize("updates,reason", [
    ({"direction": True}, "direction must be integer"),
    ({"direction": 2}, "direction must be integer"),
    ({"category": "policy"}, "invalid category"),
    ({"event_status": "completed"}, "invalid event_status"),
    ({"novelty": "probably_new"}, "invalid novelty"),
    ({"themes": ["a"] * 9}, "invalid themes"),
    ({"reason": ""}, "invalid reason"),
    ({"chain": "公司将受益"}, "identify a hypothesis"),
    ({"reason": "这是必涨机会"}, "unsupported investment claim"),
    ({"category": "market_action", "direction": 1}, "non-material direction"),
    ({"category": "insufficient", "themes": ["存储"], "uncertainties": []}, "retain uncertainty"),
    ({"evidence": [{"field": "title", "quote": "公司收购已完成"}]}, "not found"),
    ({"evidence": [{"field": "summary", "quote": "公司拟收购"}]}, "not found"),
    ({"evidence": [{"field": "title", "quote": "公司拟收购", "url": "extra"}]}, "invalid evidence"),
])
def test_bad_model_outputs_are_unknown_failures_not_insufficient(updates, reason):
    sample = _sample()
    report = contract.validate_response([_result(sample, **updates)], [sample])
    assert report["valid"] == [] and report["invalid_count"] == 1
    assert any(reason in error for error in report["invalid"][0]["reasons"])
    assert "category" not in report["invalid"][0]


def test_missing_duplicates_and_external_ids_keep_all_input_denominators():
    samples = [_sample(f"news-{i}") for i in range(3)]
    report = contract.validate_response([
        _result(samples[0]), _result(samples[1]), _result(samples[1]),
        _result(samples[2], id="external"),
    ], samples)
    assert report["expected_count"] == 3
    assert report["valid_count"] == 1 and report["invalid_count"] == 2
    assert {row["id"] for row in report["invalid"]} == {"news-1", "news-2"}
    assert report["unexpected"][0]["id"] == "external"
    assert not report["ok"]


def test_response_order_cannot_override_identity_or_evidence_source():
    first, second = _sample("first"), _sample("second", title="公司发生生产事故")
    reversed_results = [_result(second), _result(first)]
    report = contract.validate_response(json.dumps(reversed_results, ensure_ascii=False), [first, second])
    assert [row["id"] for row in report["valid"]] == ["first", "second"]
    reversed_results[0]["id"], reversed_results[1]["id"] = "first", "second"
    assert contract.validate_response(reversed_results, [first, second])["invalid_count"] == 2


@pytest.mark.parametrize("raw", [
    "not JSON", "{}", "null", "```json\n[]\n```", [None],
    '[{"id":"first","id":"second"}]',
])
def test_batch_parse_failure_is_preserved_in_full_denominator(raw):
    samples = [_sample("first"), _sample("second")]
    report = contract.validate_response(raw, samples)
    assert report["expected_count"] == report["invalid_count"] == 2
    assert report["valid_count"] == 0 and report["batch_errors"]


def test_input_ids_and_prior_records_are_validated_before_external_call():
    sample = _sample()
    with pytest.raises(ValueError, match="duplicate id"):
        contract.build_messages([sample, deepcopy(sample)])
    sample = _sample(prior={})
    with pytest.raises(ValueError, match="empty prior_version"):
        contract.build_messages([sample])
