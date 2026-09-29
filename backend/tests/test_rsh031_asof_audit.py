"""Prevent outcomes, missing data and later quality checks from rewriting ranks."""
import json

import duckdb
import pandas as pd
import pytest

from scripts.rsh031_asof_audit import (
    evaluate, load_inputs, load_pools, outcome_labels, run, select_candidates,
)


def feature(symbol, **changes):
    row = {"trade_date": "2026-09-01", "thscode": symbol,
           "prev_close": 10.0, "close_21": 9.0,
           "history_points": 21, "history_span": 20,
           "pre_0955_bars": 5, "pre_0955_close": 10.5, "is_st": 0, "suspended": 0}
    return row | changes


def test_future_result_and_full_day_quality_cannot_change_candidates_or_ranks():
    frame = pd.DataFrame([feature("000001.SZ"), feature("000002.SZ", pre_0955_close=11.0)])
    before, coverage = select_candidates(frame)
    frame["pool_kind"] = ["limit-up-pool", "limit-break-pool"]
    frame["pool_board_count"] = [1, 0]
    frame["first_hit_5m_end"] = ["2026-09-01 09:35", "2026-09-01 14:55"]
    frame["bars_5m"] = [1, 48]
    frame["close_price"] = [100.0, 0.0]
    after, changed_coverage = select_candidates(frame)
    pd.testing.assert_frame_equal(before, after)
    assert changed_coverage == coverage
    assert set(after.thscode) == {"000001.SZ", "000002.SZ"}


def test_input_gaps_stay_in_coverage_but_never_receive_a_score():
    frame = pd.DataFrame([
        feature("000001.SZ"), feature("000002.SZ", pre_0955_close=float("nan")),
        feature("000003.SZ", history_span=21), feature("000004.SZ", is_st=None),
        feature("000005.SZ", pre_0955_close=float("inf")), feature("920001.BJ"),
    ])
    candidates, coverage = select_candidates(frame)
    assert list(candidates.thscode) == ["000001.SZ"]
    assert coverage["prior_day_universe"] == 6
    assert coverage["excluded_unique"] == 5
    assert coverage["exclusion_reasons_overlapping"]["unknown_daily_identity"] == 1
    assert coverage["exclusion_reasons_overlapping"]["source_st_or_suspended"] == 0
    with pytest.raises(ValueError, match="duplicate"):
        select_candidates(pd.concat([frame, frame.iloc[:1]]))


def test_unknown_outcome_does_not_improve_precision_or_shrink_denominator():
    candidates, _ = select_candidates(pd.DataFrame([feature("000001.SZ"), feature("000002.SZ")]))
    labels = pd.DataFrame([{"trade_date": "2026-09-01", "thscode": "000001.SZ", "label": 1.0}])
    report = evaluate(candidates, labels, ["2026-09-01", "2026-09-02"], k=2)
    for metric in report["metrics"].values():
        assert metric["slots"] == 2
        assert metric["unknown_slots"] == 1
        assert metric["precision_bounds"] == [0.5, 1.0]
        assert metric["recall"] is None
        assert metric["weighted_average_precision"] is None
    assert report["days"][1]["eligible"] == 0


def test_tied_ranks_and_all_negative_days_are_deterministic():
    candidates, _ = select_candidates(pd.DataFrame([feature("000002.SZ"), feature("000001.SZ")]))
    labels = candidates[["trade_date", "thscode"]].copy()
    labels["label"] = [1.0, 0.0]
    report = evaluate(candidates, labels, ["2026-09-01"], k=1)
    assert all(value["hits"] == 1 for value in report["metrics"].values())
    labels["label"] = 0
    report = evaluate(candidates, labels, ["2026-09-01"], k=1)
    assert all(value["weighted_average_precision"] is None for value in report["metrics"].values())


def test_source_time_conflict_is_unknown_but_early_touch_is_negative():
    minutes = pd.DataFrame([
        {"trade_date": "2026-09-01", "thscode": symbol,
         "first_hit_5m_end": hit, "pool_first_seal": seal}
        for symbol, hit, seal in [("000000.SZ", None, None),
                                  ("000001.SZ", "2026-09-01 10:00", "09:40"),
                                  ("000002.SZ", "2026-09-01 09:55", "09:50"),
                                  ("000003.SZ", "2026-09-01 10:00", "09:59")]
    ])
    events = minutes[["trade_date", "thscode"]].copy()
    events["pool_first_board"] = True
    events.loc[0, "pool_first_board"] = False
    labels = outcome_labels(minutes, events)
    assert labels.label.iloc[0] == 0.0
    assert pd.isna(labels.label.iloc[1])
    assert labels.label.iloc[2:].tolist() == [0.0, 1.0]


def test_pool_completeness_is_required_even_for_empty_labels(tmp_path):
    p = tmp_path / "pools.jsonl"
    rows = [{"trade_date": "2026-09-01", "pool": pool, "total": 0, "items": []}
            for pool in ["limit-up-pool", "limit-break-pool"]]
    p.write_text(json.dumps(rows[0]) + "\n")
    with pytest.raises(ValueError, match="both complete"):
        load_pools(p)
    p.write_text("\n".join(map(json.dumps, rows)))
    days, events = load_pools(p)
    assert days == ["2026-09-01"] and events.empty


def test_prior_day_universe_does_not_require_outcome_day_daily_bar(tmp_path):
    db = tmp_path / "market.duckdb"
    days = pd.bdate_range("2026-07-30", periods=22)
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE daily_k(thscode VARCHAR, date_ms BIGINT, close_price DOUBLE)")
        rows = [(symbol, int(day.tz_localize("Asia/Shanghai").timestamp() * 1000), 10.0)
                for day in days for symbol in (["000001.SZ", "000002.SZ"] if day != days[-1]
                                               else ["000001.SZ"])]
        con.executemany("INSERT INTO daily_k VALUES (?,?,?)", rows)
    day = days[-1].date().isoformat()
    minutes = tmp_path / "minutes.parquet"
    pd.DataFrame([{"trade_date": day, "thscode": "000001.SZ", "pre_0955_bars": 5,
                   "pre_0955_close": 10.2, "first_hit_5m_end": None,
                   "pool_first_seal": None}]).to_parquet(minutes)
    status = tmp_path / "status.csv"
    pd.DataFrame([{"trade_date": day, "thscode": symbol, "is_st": 0, "trade_status": 1}
                  for symbol in ["000001.SZ", "000002.SZ"]]).to_csv(status, index=False)
    facts, _ = load_inputs(db, [minutes], status, "baostock", [day])
    assert set(facts.thscode) == {"000001.SZ", "000002.SZ"}
    selected, coverage = select_candidates(facts)
    assert selected.thscode.tolist() == ["000001.SZ"]
    assert coverage["exclusion_reasons_overlapping"]["missing_asof_bars"] == 1
    pools = tmp_path / "pools.jsonl"
    pools.write_text("\n".join(json.dumps({"trade_date": day, "pool": pool,
                                          "total": 0, "items": []})
                               for pool in ["limit-up-pool", "limit-break-pool"]))
    protocol = tmp_path / "protocol.md"
    protocol.write_text("fixed audit, not unseen holdout")
    output = tmp_path / "audit"
    report = run(db, [minutes], status, "baostock", pools, protocol, output)
    frozen = pd.read_parquet(output / "input-facts.parquet")
    replay, _ = select_candidates(frozen)
    pd.testing.assert_frame_equal(replay, pd.read_parquet(output / "candidates.parquet"))
    assert len(frozen) == 2 and report["coverage"]["eligible"] == 1
    with pytest.raises(FileExistsError):
        run(db, [minutes], status, "baostock", pools, protocol, output)


def test_invalid_labels_or_dates_cannot_silently_change_metrics():
    candidates, _ = select_candidates(pd.DataFrame([feature("000001.SZ")]))
    labels = pd.DataFrame([{"trade_date": "2026-09-01", "thscode": "000001.SZ", "label": 2}])
    with pytest.raises(ValueError, match="outcome labels"):
        evaluate(candidates, labels, ["2026-09-01"])
    with pytest.raises(ValueError, match="dates"):
        evaluate(candidates, labels, ["2026-09-02"])


def test_changed_source_snapshot_fails_before_publishing_output(tmp_path, monkeypatch):
    import scripts.rsh031_asof_audit as audit

    pools = tmp_path / "pools.jsonl"
    pools.write_text("\n".join(json.dumps({"trade_date": "2026-09-01", "pool": pool,
                                          "total": 0, "items": []})
                               for pool in ["limit-up-pool", "limit-break-pool"]))
    protocol = tmp_path / "protocol.md"
    protocol.write_text("version one")
    status = tmp_path / "status.csv"
    status.write_text("unused test input")

    def changing_source(*_args):
        protocol.write_text("version two")
        return pd.DataFrame([feature("000001.SZ")]), pd.DataFrame([
            {"trade_date": "2026-09-01", "thscode": "000001.SZ",
             "first_hit_5m_end": None, "pool_first_seal": None},
        ])

    monkeypatch.setattr(audit, "load_inputs", changing_source)
    with pytest.raises(ValueError, match="snapshot changed"):
        run(tmp_path / "unused.db", [], status, "baostock", pools, protocol, tmp_path / "audit")
    assert not (tmp_path / "audit").exists()
