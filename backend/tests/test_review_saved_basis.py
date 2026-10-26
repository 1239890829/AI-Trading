"""A saved explanation must not disappear or become an invented buy rationale."""
from app.review.collector import _saved_pick_basis


def test_original_explicit_basis_takes_precedence():
    assert _saved_pick_basis({"source_basis": "原事件依据", "bases": {"tech": "原技术"}}) == "原事件依据"


def test_saved_scoring_reasons_and_observation_constraints_are_retained():
    item = {"observation_only": True, "follow_reasons": ["无连板高度", "龙头判据未全满足"],
            "bases": {"fundamental": "原营收同比增长", "capital": "原资金资料缺失"}}
    result = _saved_pick_basis(item)
    assert "原观察约束：无连板高度；龙头判据未全满足" in result
    assert "原评分依据（非买入理由）：原营收同比增长；原资金资料缺失" in result
    assert item["observation_only"] is True


def test_malformed_basis_is_not_stringified_or_generated():
    assert _saved_pick_basis({"source_basis": {"fake": "wrong"}, "bases": {"one": {}, "two": None}}) is None
    assert _saved_pick_basis({}) is None
