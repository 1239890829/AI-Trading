"""Only normal current candidates carry the read-only execution envelope."""
from copy import deepcopy

from app.picks.intraday_opportunity import top_watch_stocks


def test_top_preserves_independent_assessment_without_granting_reference_or_mutating_source():
    qualification = {"state": "ready", "selection_ref": "intraday:2026-10-23:600001",
        "source_version": "first-entry", "item": {"score": 72, "sub_scores": {"tech": 80}},
        "assessment": {"snapshot_id": "later", "version": "assess-v1"}}
    payload = {"trade_date": "2026-10-23", "themes": [{"theme": "合成题材", "participants": [
        {"symbol": "600001", "name": "合成观察", "change_pct": 7, "linkage": {"level": "高"},
         "execution_qualification": qualification}], "stocks": [
        {"symbol": "600099", "name": "仅参考", "boards": 2, "tradable": True,
         "execution_qualification": qualification}]}]}
    original = deepcopy(payload)
    output = top_watch_stocks(payload, limit=8)
    assert output["items"][0]["execution_qualification"] == qualification
    assert "execution_qualification" not in output["reference_items"][0]
    assert "score" not in output["items"][0]
    output["items"][0]["execution_qualification"]["item"]["score"] = 0
    assert payload == original
