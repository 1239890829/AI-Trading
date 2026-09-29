import json

import pandas as pd
import pytest

from scripts.rsh031_practical_audit import board_paths, read_asof, screen_period, sha256


def test_screen_keeps_unknowns_and_failed_candidates():
    rows = []
    for day in ("2026-09-01", "2026-09-02"):
        for index in range(100):
            rows.append({"trade_date": day, "thscode": f"{index:06d}.SZ",
                         "early_ret": index / 100, "label": 1.0 if index in (0, 99)
                         else float("nan") if index == 98 else 0.0})
    result = screen_period(pd.DataFrame(rows), ["2026-09-01", "2026-09-02"], 100)
    assert result["screen"]["count"] == 4
    assert result["screen"]["hits"] == 2
    assert result["screen"]["unknown"] == 2
    assert result["screen"]["false_positives"] == 0
    assert result["other"]["hits"] == 2
    assert result["screen"]["hit_rate_bounds"] == [0.5, 1.0]


def test_screen_with_no_positive_outcomes_reports_undefined_recall():
    rows = [{"trade_date": "2026-09-01", "thscode": f"{index:06d}.SZ",
             "early_ret": index / 100, "label": 0.0} for index in range(100)]
    result = screen_period(pd.DataFrame(rows), ["2026-09-01"], 100)
    assert result["screen"]["known_positive_recall"] is None
    assert result["observed_rate_ratio"] is None
    assert result["day_cluster_bootstrap_ratio_95pct"] is None


def test_board_paths_keep_failure_conflict_and_last_day_censor(tmp_path):
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps(["2026-09-01", "2026-09-02"]))
    pools = tmp_path / "pools.jsonl"
    records = [
        {"trade_date": "2026-09-01", "pool": "limit-up-pool", "total": 4,
         "items": [{"thscode": symbol, "continue_day_cnt": 1, "limit_up_time": "09:40"}
                   for symbol in "ABCD"]},
        {"trade_date": "2026-09-01", "pool": "limit-break-pool", "total": 0, "items": []},
        {"trade_date": "2026-09-02", "pool": "limit-up-pool", "total": 2,
         "items": [{"thscode": "A", "continue_day_cnt": 2},
                   {"thscode": "B", "continue_day_cnt": 3}]},
        {"trade_date": "2026-09-02", "pool": "limit-break-pool", "total": 1,
         "items": [{"thscode": "C"}]},
    ]
    pools.write_text("\n".join(json.dumps(row) for row in records))
    result = board_paths([pools], calendar)
    row = result["summary"]["1"]["all"]
    assert row["total"] == 4
    assert [row[key] for key in ("continued", "board_count_conflict",
                                  "next_day_break_pool", "next_day_neither_pool")] == [1, 1, 1, 1]
    assert result["last_date_censored"] == 2


def test_board_paths_reject_missing_or_overlapping_pools(tmp_path):
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps(["2026-09-01", "2026-09-02"]))
    pools = tmp_path / "pools.jsonl"
    up = {"trade_date": "2026-09-01", "pool": "limit-up-pool", "total": 1,
          "items": [{"thscode": "A", "continue_day_cnt": 1}]}
    pools.write_text("\n".join(json.dumps(row) for row in
                               [up, {**up, "trade_date": "2026-09-02"}]))
    with pytest.raises(ValueError, match="both pools required"):
        board_paths([pools], calendar)
    rows = [up, {**up, "pool": "limit-break-pool"},
            {**up, "trade_date": "2026-09-02"},
            {**up, "trade_date": "2026-09-02", "pool": "limit-break-pool", "items": []}]
    rows[-1]["total"] = 0
    pools.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="overlap"):
        board_paths([pools], calendar)


def test_asof_artifact_rejects_modified_input(tmp_path):
    candidates = tmp_path / "candidates.parquet"
    labels = tmp_path / "source-labels.parquet"
    pd.DataFrame([{"trade_date": "2026-09-01", "thscode": "000001.SZ",
                   "early_ret": 0.01, "ret20": 0.02}]).to_parquet(candidates)
    pd.DataFrame([{"trade_date": "2026-09-01", "thscode": "000001.SZ",
                   "label": 1.0}]).to_parquet(labels)
    (tmp_path / "report.json").write_text(json.dumps({
        "candidates_sha256": sha256(candidates), "labels_sha256": sha256(labels)}))
    assert len(read_asof(tmp_path)) == 1
    pd.DataFrame([{"trade_date": "2026-09-01", "thscode": "000001.SZ",
                   "label": 0.0}]).to_parquet(labels)
    with pytest.raises(ValueError, match="hash changed"):
        read_asof(tmp_path)
