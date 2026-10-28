"""Source identities and causal claims remain separate from grouping."""
from copy import deepcopy

import pytest

from app.services.research_clusters import build_research_clusters, snapshot_version, validate_snapshot


DAY = "2026-10-28"
CLOCK = "2026-10-28T14:42:00+08:00"


def record(symbol_value, reason_value, **patch):
    return {"symbol": symbol_value, "name": "来源公司", "reason": reason_value, "source": "ths",
            "trade_date": DAY, "quality": "high", "change_pct": 3.5, **patch}


def directory(*names, observed_at=CLOCK):
    return {"state": "available", "ambiguous": [], "identities": {
        name: {"code": f"{885000 + n:06d}.TI", "name": name, "kind": "industry" if name == "汽车零部件" else "concept",
               "source": "ths_official", "basis": "exact_name", "observed_at": observed_at,
               "catalog_version": "a" * 64}
        for n, name in enumerate(names)}}


def build(records, resolution=None, **patch):
    return build_research_clusters(records, trade_date=DAY, observed_at=CLOCK,
                                   resolution=resolution or directory("人形机器人", "汽车零部件", "机器人概念"), **patch)


def test_partial_auto_requires_literal_humanoid_tag_and_never_absorbs_industry():
    rows = [record("600001", "人形机器人+汽车零部件+“羊”字辈"),
            record("600002", "汽车零部件+补贴"), record("600003", "机器人概念+其他订单")]
    before = deepcopy(rows)
    result = build(rows)
    assert rows == before
    structural = next(row for row in result["clusters"] if row["type"] == "industry_chain")
    assert structural["state"] == "supported" and structural["support_scope"] == "industry_structure"
    assert structural["driver_state"] == "candidate"
    assert structural["unique_stock_count"] == 1 and structural["change_pct"] is None
    branches = {row["raw_name"]: row for row in structural["branches"]}
    assert branches["人形机器人"]["relation"] == "whole"
    assert branches["汽车零部件"]["relation"] == "partial"
    assert branches["汽车零部件"]["symbols"] == ["600001"]
    assert branches["汽车零部件"]["official_identity"]["kind"] == "industry"
    assert structural["stocks"][0]["raw_tags"] == ["“羊”字辈", "人形机器人", "汽车零部件"]
    assert all("600002" not in branch["symbols"] for branch in structural["branches"])
    assert "机器人概念" not in branches  # no A-B-C transitive merge
    assert {r["name"] for r in result["clusters"]} >= {"“羊”字辈", "补贴", "其他订单"}
    assert "汽车零部件" not in {r["name"] for r in result["clusters"]}  # original directory stays separate
    validate_snapshot(result)


def test_secondary_sheep_tag_covers_all_observed_members_without_homophone_guesses():
    rows = [record("600001", "人形机器人+“羊”字辈", name="普通名称"),
            record("600002", "医疗服务+“羊”字辈", change_pct=-2),
            record("600003", "其他事件", name="阳光羊洋"),
            record("600004", "羊字辈", name="独立原始写法")]
    result = build(rows)
    sheep = next(row for row in result["clusters"] if row["name"] == "“羊”字辈")
    assert sheep["state"] == "candidate" and sheep["type"] == "market_narrative"
    assert [row["symbol"] for row in sheep["stocks"]] == ["600001", "600002"]
    assert sheep["stocks"][1]["change_pct"] == -2
    assert sheep["unique_stock_count"] == 2 and sheep["change_pct"] is None
    assert sheep["branches"][0]["official_identity"] is None
    assert "full_theme_universe_not_observed" in sheep["missing"]
    assert any(row["name"] == "羊字辈" and row["unique_stock_count"] == 1 for row in result["clusters"])
    assert result["coverage"] == {"source_scope": "limit_up_sample", "source_count": 4,
                                  "tagged_count": 4, "unique_stock_count": 4, "complete_universe": False}


def test_same_price_or_overlap_never_promotes_unrelated_source_labels():
    rows = [record("600001", "芯片+新能源"), record("600002", "芯片+新能源")]
    result = build(rows)
    assert {row["name"] for row in result["clusters"]} == {"芯片", "新能源"}
    assert all(row["state"] == "candidate" for row in result["clusters"])
    assert all(row["driver_state"] == "candidate" for row in result["clusters"])


def test_catalog_unknown_cannot_assert_tag_is_unofficial_or_guess_formal_code():
    result = build([record("600001", "人形机器人+汽车零部件")], resolution={"state": "unavailable"})
    assert result["state"] == "partial"
    assert "formal_catalog_unavailable" in result["missing"]
    assert all(branch["official_identity"] is None for row in result["clusters"] for branch in row["branches"])
    candidates = [row for row in result["clusters"] if row["type"] == "market_narrative"]
    assert len(candidates) == 2
    assert all("formal_catalog_status_unknown" in row["missing"] for row in candidates)
    assert all("no_exact_formal_catalog_identity" not in row["missing"] for row in candidates)


def test_formal_standalone_is_not_duplicated_and_ambiguous_name_has_no_identity():
    result = build([record("600001", "机器人概念")])
    assert result["clusters"] == []
    resolution = directory("机器人概念")
    resolution["ambiguous"] = ["机器人概念"]
    result = build([record("600001", "机器人概念")], resolution=resolution)
    assert result["clusters"][0]["state"] == "candidate"
    assert result["clusters"][0]["branches"][0]["official_identity"] is None
    branch = result["clusters"][0]["branches"][0]
    assert branch["catalog_mapping_state"] == "ambiguous"
    assert "formal_catalog_name_ambiguous" in branch["missing"]
    assert "no_exact_formal_catalog_identity" not in result["clusters"][0]["missing"]


def test_order_and_receive_clocks_do_not_change_content_version_but_real_facts_do():
    rows = [record("600001", "人形机器人+汽车零部件"), record("600002", "“羊”字辈")]
    original = build(rows)
    later = build_research_clusters(list(reversed(rows)), trade_date=DAY,
                                    observed_at="2026-10-28T14:43:00+08:00",
                                    resolution=directory("人形机器人", "汽车零部件", "机器人概念",
                                                         observed_at="2026-10-28T14:43:00+08:00"))
    assert original["version"] == later["version"]
    changed = deepcopy(rows)
    changed[1]["change_pct"] = -1
    assert build(changed)["version"] != original["version"]
    changed_directory = directory("人形机器人", "汽车零部件", "机器人概念")
    changed_directory["identities"]["汽车零部件"]["code"] = "881126.TI"
    assert build(rows, resolution=changed_directory)["version"] != original["version"]
    assert original["source_hash_basis"] == "normalized_reason_observations_v1"


def test_original_receive_clock_is_preserved_but_never_becomes_quote_clock_or_new_version():
    first = build([record("600001", "新标签", received_at="2026-10-28T14:40:00+08:00")])
    later = build([record("600001", "新标签", received_at="2026-10-28T14:41:00+08:00")])
    assert first["version"] == later["version"]
    assert first["clusters"][0]["stocks"][0]["received_at"] == "2026-10-28T14:40:00+08:00"
    assert "source_quote_clock_unavailable" in first["missing"]
    same_rows = [record("600001", "新标签", received_at="2026-10-28T14:41:00+08:00"),
                 record("600001", "新标签", received_at="2026-10-28T14:40:00+08:00")]
    duplicate = build(same_rows)
    assert duplicate["coverage"]["unique_stock_count"] == 1
    assert duplicate["clusters"][0]["stocks"][0]["received_at"] == "2026-10-28T14:40:00+08:00"
    assert "conflicting_duplicate_symbol_excluded" not in duplicate["missing"]


@pytest.mark.parametrize("patch,reason", [
    ({"source": "eastmoney"}, "non_ths_reason_observation_excluded"),
    ({"trade_date": "2026-10-27"}, "source_trade_date_mismatch"),
    ({"trade_date": None}, "source_trade_date_unknown"),
    ({"quality": None}, "source_quality_unknown"),
    ({"received_at": "2026-10-28T15:00:00+08:00"}, "source_receive_clock_invalid"),
    ({"received_at": "2026-10-28T14:00:00"}, "source_receive_clock_invalid"),
    ({"quality": "stale"}, "unusable_source_quality"),
    ({"quality": "invalid"}, "unusable_source_quality"),
    ({"symbol": "../x"}, "invalid_source_symbol"),
])
def test_unusable_observation_never_makes_a_current_cluster(patch, reason):
    result = build([record("600001", "“羊”字辈", **patch)])
    assert result["state"] == "unavailable" and result["clusters"] == []
    assert reason in result["missing"]
    assert result["coverage"]["source_count"] == 1
    assert result["coverage"]["unique_stock_count"] == 0


def test_conflicting_duplicate_not_merged_or_counted_twice_and_real_zero_is_retained():
    result = build([record("600001", "“羊”字辈"), record("600001", "人形机器人"),
                    record("600002", "新标签", change_pct=0), record("600002", "新标签", change_pct=0)])
    assert "conflicting_duplicate_symbol_excluded" in result["missing"]
    assert result["coverage"]["unique_stock_count"] == 1
    assert result["clusters"][0]["stocks"][0]["change_pct"] == 0
    assert result["clusters"][0]["unique_stock_count"] == 1


def test_missing_change_does_not_become_zero_and_snapshot_semantics_are_checked():
    result = build([record("600001", "新标签", change_pct=float("nan"))])
    assert result["clusters"][0]["stocks"][0]["change_pct"] is None
    changed = deepcopy(result)
    changed["clusters"][0]["change_pct"] = 9.99
    with pytest.raises(ValueError):
        validate_snapshot(changed)


@pytest.mark.parametrize("observed,day", [("2026-10-28T14:42:00", DAY), (CLOCK, "2026-10-29")])
def test_no_naive_clock_or_future_day(observed, day):
    with pytest.raises(ValueError):
        build_research_clusters([], trade_date=day, observed_at=observed, resolution=directory())


def test_valid_empty_pool_is_not_source_failure():
    result = build([])
    assert result["clusters"] == [] and result["state"] == "ready"
    assert result["coverage"]["source_count"] == 0


def test_present_stock_with_no_source_reason_is_partial_unknown_not_proof_of_no_theme():
    result = build([record("600001", None)])
    assert result["state"] == "partial" and result["clusters"] == []
    assert "source_reason_missing" in result["missing"]
    assert result["coverage"]["source_count"] == 1
    assert result["coverage"]["tagged_count"] == 0


def test_empty_or_invalid_available_catalog_is_unknown_not_proof_of_unofficial():
    for resolution in ({"state": "available", "identities": {}},
                       {"state": "available", "identities": {"新标签": {"name": "新标签", "code": "BK1234", "source": "eastmoney"}}}):
        result = build([record("600001", "新标签")], resolution=resolution)
        assert "formal_catalog_unavailable" in result["missing"]
        assert result["clusters"][0]["missing"][0] == "formal_catalog_status_unknown"


@pytest.mark.parametrize("patch", [
    {"basis": "member_overlap"}, {"basis": None}, {"catalog_version": "a" * 63},
    {"observed_at": "2026-10-28T14:00:00"}, {"observed_at": "2026-10-28T15:00:00+08:00"},
    {"name": "同名替代"}, {"source": "eastmoney"}, {"kind": "region"},
])
def test_invalid_catalog_provenance_is_unknown_but_valid_unrelated_identity_survives(patch):
    resolution = directory("正确正式概念", "待核标签")
    resolution["identities"]["待核标签"].update(patch)
    result = build([record("600001", "正确正式概念+待核标签")], resolution=resolution)
    assert "formal_catalog_partially_invalid" in result["missing"]
    assert [row["name"] for row in result["clusters"]] == ["待核标签"]
    candidate = result["clusters"][0]
    assert candidate["branches"][0]["official_identity"] is None
    assert candidate["missing"][0] == "formal_catalog_status_unknown"
    validate_snapshot(result)


@pytest.mark.parametrize("mutation", ["outside", "duplicate", "missing_ref", "extra_ref", "missing_branch_stock", "fake_identity"])
def test_rehashed_snapshot_cannot_claim_unbound_or_duplicate_branch_members(mutation):
    result = build([record("600001", "人形机器人+汽车零部件+“羊”字辈"),
                    record("600002", "人形机器人+汽车零部件")])
    structural = next(row for row in result["clusters"] if row["type"] == "industry_chain")
    branch = next(row for row in structural["branches"] if row["raw_name"] == "汽车零部件")
    if mutation == "outside":
        branch["symbols"].append("600999")
    elif mutation == "duplicate":
        branch["symbols"].append("600001")
    elif mutation == "missing_ref":
        branch["evidence_ids"] = branch["evidence_ids"][:-1]
    elif mutation == "extra_ref":
        branch["evidence_ids"] = sorted([*branch["evidence_ids"], "fake-ref"])
    elif mutation == "missing_branch_stock":
        branch["symbols"] = ["600001"]
    elif mutation == "fake_identity":
        branch["official_identity"]["basis"] = "member_overlap"
    result["version"] = snapshot_version(result)
    with pytest.raises(ValueError):
        validate_snapshot(result)


@pytest.mark.parametrize("mutation", ["publication_missing", "publication_replaced", "one_branch", "empty_refs"])
def test_supported_structure_cannot_be_claimed_by_rehashing_unreviewed_evidence(mutation):
    result = build([record("600001", "人形机器人+汽车零部件")])
    structural = next(row for row in result["clusters"] if row["type"] == "industry_chain")
    if mutation == "publication_missing":
        structural["evidence"] = [row for row in structural["evidence"] if row["evidence_type"] != "published_industry_structure"]
    elif mutation == "publication_replaced":
        next(row for row in structural["evidence"] if row["evidence_type"] == "published_industry_structure")["url"] = "https://example.com/unsupported"
    elif mutation == "one_branch":
        structural["branches"] = structural["branches"][:1]
    elif mutation == "empty_refs":
        structural["branches"][0]["evidence_ids"] = []
    result["version"] = snapshot_version(result)
    with pytest.raises(ValueError):
        validate_snapshot(result)
