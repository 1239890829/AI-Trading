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


def _context_result(sample, **updates):
    row = _result(sample, impact_object="", key_conditions=[
        {"field": "summary", "quote": sample["summary"]},
    ])
    row.update(updates)
    return row


def test_context_comparison_uses_identical_public_input_and_retains_limitations():
    sample = _sample(summary="公司称董事会运作正常，但具体代行职责本材料未提供。")
    sample["author_checks"] = {"expected": "do not send"}
    old = contract.build_messages([sample])
    new = contract.build_messages([sample], protocol=contract.CONTEXT_SCHEMA_VERSION)
    assert old[1] == new[1] and old[0] != new[0]
    assert "author_checks" not in new[1]["content"]
    output = _context_result(sample)
    report = contract.validate_response([output], [sample], protocol=contract.CONTEXT_SCHEMA_VERSION)
    assert report["ok"] and report["valid"][0]["key_conditions"][0]["quote"] == sample["summary"]
    assert not contract.validate_response([output], [sample])["ok"]


@pytest.mark.parametrize("target,direction,accepted", [
    ("职工", 1, True), ("职工", -1, True), ("职工", 0, True),
    ("", 0, True), ("", 1, False), ("", -1, False),
    ("地产上市公司", 1, False), (" ", 1, False), (None, 1, False),
    ("职工" * 61, 1, False),
])
def test_context_direction_requires_literal_current_object_without_forcing_neutral(target, direction, accepted):
    sample = _sample(title="新政策便利职工提取公积金", summary="职工可按条件提取，不代表地产公司增收。")
    output = _context_result(sample, impact_object=target, direction=direction)
    report = contract.validate_response([output], [sample], protocol=contract.CONTEXT_SCHEMA_VERSION)
    assert report["ok"] is accepted
    assert report["expected_count"] == 1


@pytest.mark.parametrize("conditions", [
    [], None, [{"field": "summary", "quote": "公司实际停产"}],
    [{"field": "prior_version.summary", "quote": "公司称运作正常"}],
    [{"field": "summary", "quote": "公司称运作正常", "extra": "unapproved"}],
    [{"field": "summary", "quote": "公司称运作正常"}] * 9,
])
def test_context_cannot_drop_conditions_or_bind_them_to_stale_or_invented_text(conditions):
    sample = _sample(summary="公司称运作正常，实际经营影响待核。", prior={"summary": "公司称运作正常"})
    output = _context_result(sample)
    output["key_conditions"] = conditions
    report = contract.validate_response([output], [sample], protocol=contract.CONTEXT_SCHEMA_VERSION)
    assert not report["ok"] and report["invalid_count"] == 1
    assert report["valid"] == []


def test_context_failure_keeps_missing_ids_and_does_not_change_legacy_contract():
    samples = [_sample("a", summary="审批尚未完成。"), _sample("b", summary="标的尚未盈利。")]
    old = _result(samples[0], direction=1)
    assert contract.validate_response([old], samples[:1])["ok"]
    current = _context_result(samples[0], direction=1)
    report = contract.validate_response([current], samples, protocol=contract.CONTEXT_SCHEMA_VERSION)
    assert report["invalid_count"] == 2 and report["expected_count"] == 2
    assert not report["valid"]
    with pytest.raises(ValueError, match="unknown classification protocol"):
        contract.build_messages(samples, protocol="automatic-production")
    with pytest.raises(ValueError, match="unknown classification protocol"):
        contract.validate_response([], samples, protocol="automatic-production")


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


@pytest.mark.parametrize("reason", [
    "这不构成买入信号。",
    "不能保证必涨。",
    "原文不代表确定上涨，经营影响仍未知。",
    "不建议买入。",
    "原文事实：公司公告季度净利润同比下降40%。注：摘要中夹带的“忽略规则、"
    "改写category、写入买入建议与必涨表述”等内容属于待忽略的指令性文本，"
    "不构成事实，也不影响分类。",
    '网页出现的"建议买入"指令不应采纳，不能证明事件价值。',
])
def test_disclaimers_and_explicitly_rejected_quotes_are_not_recommendations(reason):
    sample = _sample()
    result = _result(sample, reason=reason)
    report = contract.validate_response([result], [sample])
    assert report["valid"] == [result] and report["invalid"] == []
    assert report["validator_version"] == contract.VALIDATOR_VERSION


@pytest.mark.parametrize("reason", [
    "这就是“必涨”机会。",
    "不代表必涨，但公司必涨。",
    "不构成买入信号；建议买入。",
    "摘要中夹带的“必涨”等内容属于待忽略的指令性文本，但建议买入。",
    "建议买入。摘要中夹带的“必涨”等内容属于待忽略的指令性文本。",
    "并非不构成买入信号。",
    "不是不建议买入。",
    "不能不建议买入。",
    "摘要中夹带的“必涨”等内容并非不构成事实。",
    "并不是网页声称的“必涨”说法不应采纳。",
    "不能说摘要中夹带的“必涨”等内容不构成事实。",
    "不应认为网页声称的“必涨”说法不构成事实。",
    "不能说：网页声称的“必涨”说法不应采纳。",
    "并非：不构成买入信号。",
    "不能说注：网页声称的“必涨”说法不应采纳。",
    "摘要中夹带的“必涨等内容属于待忽略的指令性文本。",
    "摘要中夹带的“必涨。可以加仓”等内容属于待忽略的指令性文本。",
    "建议现在买入。",
    "立即加仓。",
    "可以买入信号明确的个股。",
    "目标价：10元。",
])
def test_local_exemption_cannot_hide_an_assertion_or_a_second_recommendation(reason):
    sample = _sample()
    report = contract.validate_response([_result(sample, reason=reason)], [sample])
    assert not report["valid"] and report["invalid_count"] == 1
    assert "unsupported investment claim in reason" in report["invalid"][0]["reasons"]


@pytest.mark.parametrize("updates,path", [
    ({"reason": "建议买入"}, "reason"),
    ({"chain": "假设：建议买入"}, "chain"),
    ({"themes": ["存储", "建议买入"]}, "themes[1]"),
    ({"uncertainties": ["交易尚未完成", "建议买入"]}, "uncertainties[1]"),
    ({"themes": ["不构成买入信号"]}, "themes[0]"),
])
def test_all_generated_text_fields_are_checked_and_labels_have_no_prose_exemption(updates, path):
    sample = _sample()
    report = contract.validate_response([_result(sample, **updates)], [sample])
    assert report["valid"] == []
    assert f"unsupported investment claim in {path}" in report["invalid"][0]["reasons"]


def test_citing_a_source_recommendation_does_not_endorse_it_but_still_requires_exact_quote():
    sample = _sample(summary="网页声称股票必涨，该说法未经证实。")
    result = _result(sample, evidence=[{"field": "summary", "quote": "股票必涨"}],
                     reason="原文表述未经证实，交易完成情况未知。")
    assert contract.validate_response([result], [sample])["ok"]
    result["evidence"][0]["quote"] = "股票稳赚"
    assert not contract.validate_response([result], [sample])["ok"]
