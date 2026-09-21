"""RSH-026: point-in-time funnel evidence, replay, and outcome labels."""
from __future__ import annotations

import json

import pytest
from datetime import date, datetime, timezone

from sqlalchemy import create_engine, delete, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.opportunity_learning import (
    OpportunityDecisionRun, OpportunityDecisionSnapshot,
    OpportunityOutcomeLabel, OpportunityOutcomeRevision,
)
from app.models.watchlist import Base
from app.schemas.market import Kline
from app.picks.opportunity_learning import (
    COST_MODEL_VERSION,
    CROSS_DAY_PATH_VERSION,
    FEATURE_VERSION,
    FILL_SEAL_GAP_PCT,
    OUTCOME_HORIZON,
    OUTCOME_REVISION_VERSION,
    PATH_VERSION,
    PRICE_BASIS_VERSION,
    STRATEGY_VERSION,
    archive_intraday_pipeline,
    archive_records,
    build_intraday_run_meta,
    assess_fill_state,
    backfill_missing_outcome_identities,
    backfill_outcome_revisions,
    build_intraday_records,
    build_notification_records,
    cross_day_path_metrics,
    d0_path_metrics,
    label_cross_day_paths,
    label_d0_paths,
    label_trade_date,
    due_outcome_symbols,
    ensure_outcome_horizons,
    label_due_outcomes,
    learning_summary,
    opportunity_scorecard,
    outcome_target_dates,
    pending_cross_day_path_targets,
    pending_d0_path_symbols,
    pending_outcome_targets,
    pending_symbols,
    replay_run,
    round_trip_net_pct,
)


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'opportunity-learning.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _basis(trade_date: str, *symbols: str, factor: float = 1.0):
    return {
        (trade_date, symbol): (factor, "qfq:test|raw:test")
        for symbol in symbols
    }


def _payload():
    return {
        "trade_date": "2026-09-16",
        "hot_available": True,
        "linkage_stats": {
            "snapshot_state": "ready",
            "snapshot_as_of": "2026-09-16T02:05:00+00:00",
            "missing_quote": 0,
        },
        "themes": [{
            "theme": "算力", "stage": "发酵", "strength_tier": "强势",
            "participants": [
                {
                    "symbol": "600001", "name": "甲", "price": 10.0,
                    "change_pct": 6.8, "amount": 2e8, "container": "算力概念",
                    "basis": "尚未涨停，已进临板区", "board": "沪市主板",
                    "tradability": {"level": "可参与", "basis": "报价可成交"},
                    "linkage": {"level": "高", "basis": "题材成建制且进入临板区"},
                },
                {
                    "symbol": "600002", "name": "乙", "price": 8.0,
                    "change_pct": 1.2, "amount": 1e8, "container": "算力概念",
                    "basis": "尚未涨停但联动弱", "board": "沪市主板",
                    "tradability": {"level": "可参与", "basis": "报价可成交"},
                    "linkage": {"level": "低", "basis": "跟进迹象弱"},
                },
            ],
            "stocks": [],
        }],
    }


def test_intraday_snapshot_covers_candidate_gate_and_rank_with_unknown_safe():
    payload = _payload()
    payload["themes"][0]["participants"][1]["tradability"] = {
        "level": "unknown", "basis": "盘口缺失"
    }
    run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )

    assert run_id
    assert len(rows) == 6
    by = {(r["symbol"], r["stage"]): r for r in rows}
    assert by[("600001", "candidate")]["decision"] == "included"
    assert by[("600001", "hard_gate")]["decision"] == "passed"
    assert by[("600001", "rank")]["decision"] == "ranked"
    assert by[("600001", "rank")]["rank"] == 1
    # unknown 不能塌缩成 passed/rejected，也不能靠排序补进名单。
    assert by[("600002", "hard_gate")]["decision"] == "unknown"
    assert by[("600002", "rank")]["decision"] == "unknown"


def test_intraday_data_state_degrades_when_snapshot_freshness_is_not_ready():
    payload = _payload()
    payload["linkage_stats"] = {
        "snapshot_state": "stale",
        "snapshot_as_of": "2026-09-16T02:05:00+00:00",
        "missing_quote": 0,
    }
    run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    assert run_id
    assert rows
    assert {row["data_state"] for row in rows} == {"degraded"}


def test_intraday_data_state_degrades_when_snapshot_version_is_missing():
    payload = _payload()
    payload["linkage_stats"] = {
        "snapshot_state": "ready", "snapshot_as_of": None, "missing_quote": 0,
    }
    _run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    assert rows
    assert {row["data_state"] for row in rows} == {"degraded"}


def test_filtered_symbol_is_archived_before_it_disappears_from_participants():
    payload = _payload()
    payload["themes"][0]["participants"] = [payload["themes"][0]["participants"][0]]
    payload["themes"][0]["_candidate_audit"] = [
        {
            "symbol": "300003", "name": "创业丙", "candidate_decision": "rejected",
            "hard_gate_decision": "rejected", "reason": "账户无创业板交易权限",
            "price": 12.0, "change_pct": 5.0, "amount": 2e8, "board": "创业板",
            "facts": {"quote_present": True, "board_tradable": False},
        },
        {
            "symbol": "600001", "name": "甲", "candidate_decision": "included",
            "hard_gate_decision": "passed", "reason": "候选与可参与硬门均通过",
            "price": 10.0, "change_pct": 6.8, "amount": 2e8, "board": "沪市主板",
            "facts": {
                "quote_present": True, "board_tradable": True, "change_pct": 6.8,
                "linkage_min_pct": 1.0, "amount": 2e8, "amount_min": 3e7,
                "seal_line": 9.7,
            },
        },
    ]
    _run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    filtered = [r for r in rows if r["symbol"] == "300003"]
    assert [(r["stage"], r["decision"]) for r in filtered] == [
        ("candidate", "rejected"), ("hard_gate", "rejected")
    ]
    assert all(r["symbol"] != "300003" or r["stage"] != "rank" for r in rows)


def test_zero_record_intraday_run_is_durable_idempotent_and_replayable(tmp_path):
    sf = _factory(tmp_path)
    payload = {
        "trade_date": "2026-09-16",
        "hot_available": True,
        "linkage_stats": {
            "snapshot_state": "ready",
            "snapshot_as_of": "2026-09-16T02:05:00+00:00",
            "missing_quote": 0,
            "themes_mined": 0,
            "candidates": 0,
        },
        "themes": [],
        "summary": {"limit_up_total": 81, "top_theme": None},
        "caveats": ["本轮无满足集中度/容器条件的候选"],
    }
    as_of = datetime(2026, 9, 16, 10, 5)
    first = archive_intraday_pipeline(
        payload, trade_date="2026-09-16", as_of=as_of, session_factory=sf
    )
    second = archive_intraday_pipeline(
        payload, trade_date="2026-09-16", as_of=as_of, session_factory=sf
    )
    assert first["records"] == 0 and first["run_inserted"] == 1
    assert second["records"] == 0 and second["run_inserted"] == 0

    with sf() as db:
        run = db.get(OpportunityDecisionRun, first["run_id"])
        assert run is not None
        assert run.records_total == 0
        assert run.theme_count == 0
        assert run.participant_count == 0
        assert run.candidate_audit_count == 0
        assert run.data_state == "ready"
        assert len(db.execute(select(OpportunityDecisionSnapshot)).scalars().all()) == 0

    replay = replay_run(first["run_id"], sf)
    assert replay["records"] == 0 and replay["mismatches"] == 0
    assert replay["run_evidence"]["records_total"] == 0
    assert replay["run_evidence"]["theme_count"] == 0
    assert replay["run_evidence"]["summary"]["limit_up_total"] == 81

    summary = learning_summary("2026-09-16", sf)
    assert summary["run_ledger"] == {
        "runs": 1, "zero_record_runs": 1, "data_states": {"ready": 1},
        "latest_as_of": "2026-09-16T10:05:00",
        "latest_run_id": first["run_id"],
    }
    assert summary["evidence_quality"]["decision_runs"] == 1
    assert summary["evidence_quality"]["zero_record_runs"] == 1
    assert summary["evidence_quality"]["run_states"] == {"ready": 1}
    assert summary["evidence_quality"]["complete"] is True


def test_same_run_id_with_different_run_digest_is_rejected(tmp_path):
    sf = _factory(tmp_path)
    payload = _payload()
    as_of = datetime(2026, 9, 16, 10, 5)
    first = archive_intraday_pipeline(
        payload, trade_date="2026-09-16", as_of=as_of, session_factory=sf
    )
    mutated = json.loads(json.dumps(payload))
    mutated["summary"] = {"limit_up_total": 999}
    with pytest.raises(RuntimeError, match="run digest mismatch"):
        archive_intraday_pipeline(
            mutated, trade_date="2026-09-16", as_of=as_of, session_factory=sf
        )
    with sf() as db:
        run = db.get(OpportunityDecisionRun, first["run_id"])
        assert run is not None
        assert json.loads(run.summary).get("limit_up_total") != 999
        assert len(db.execute(select(OpportunityDecisionSnapshot)).scalars().all()) == 6


def test_run_and_symbol_rows_share_one_atomic_transaction(tmp_path):
    sf = _factory(tmp_path)
    payload = _payload()
    as_of = datetime(2026, 9, 16, 10, 5)
    run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=as_of
    )
    first = dict(rows[0])
    collision = dict(first)
    collision["decision"] = "rejected" if first["decision"] != "rejected" else "unknown"
    collision["evidence"] = {**(first.get("evidence") or {}), "collision": True}
    bad_rows = [first, collision]
    meta = build_intraday_run_meta(
        payload, run_id=run_id, trade_date="2026-09-16", as_of=as_of, records=bad_rows
    )
    with pytest.raises(IntegrityError):
        archive_records(run_id, bad_rows, sf, run_meta=meta)

    with sf() as db:
        assert db.get(OpportunityDecisionRun, run_id) is None
        assert db.execute(select(OpportunityDecisionSnapshot)).scalars().all() == []
        assert db.execute(select(OpportunityOutcomeLabel)).scalars().all() == []


def test_archive_is_append_only_idempotent_and_offline_replay_matches(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    first = archive_records(run_id, rows, sf)
    second = archive_records(run_id, rows, sf)

    assert first["inserted"] == 6 and first["outcomes_inserted"] == 6
    assert first["outcomes_repaired"] == 0
    assert second["inserted"] == 0 and second["outcomes_inserted"] == 0
    assert second["outcomes_repaired"] == 0
    replay = replay_run(run_id, sf)
    assert replay["records"] == 6 and replay["mismatches"] == 0
    assert all(item["matches"] for item in replay["items"])
    with sf() as db:
        evidence_before = db.execute(
            select(OpportunityDecisionSnapshot.snapshot_id, OpportunityDecisionSnapshot.evidence)
            .order_by(OpportunityDecisionSnapshot.id)
        ).all()
    label_trade_date("2026-09-16", {"600001": 10.5}, sf, price_basis_by_key=_basis("2026-09-16", "600001"))
    with sf() as db:
        evidence_after = db.execute(
            select(OpportunityDecisionSnapshot.snapshot_id, OpportunityDecisionSnapshot.evidence)
            .order_by(OpportunityDecisionSnapshot.id)
        ).all()
    assert evidence_after == evidence_before, "结果标签必须写独立表，不能改写当时证据"


def test_rerun_repairs_missing_legacy_outcome_without_duplicating_snapshot(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    with sf() as db:
        rejected_snapshot = db.execute(
            select(OpportunityDecisionSnapshot)
            .where(OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.symbol == "600002")
        ).scalar_one()
        db.execute(
            delete(OpportunityOutcomeLabel).where(
                OpportunityOutcomeLabel.snapshot_id == rejected_snapshot.snapshot_id
            )
        )
        db.commit()
        before_snapshots = len(db.execute(select(OpportunityDecisionSnapshot)).scalars().all())

    repaired = archive_records(run_id, rows, sf)
    assert repaired["inserted"] == 0 and repaired["outcomes_inserted"] == 1
    assert repaired["outcomes_repaired"] == 0
    with sf() as db:
        after_snapshots = len(db.execute(select(OpportunityDecisionSnapshot)).scalars().all())
        outcome = db.execute(
            select(OpportunityOutcomeLabel).where(
                OpportunityOutcomeLabel.snapshot_id == rejected_snapshot.snapshot_id
            )
        ).scalar_one()
    assert after_snapshots == before_snapshots == 6
    assert outcome.state == "deferred" and outcome.fill_state == "not_actionable"


def test_rerun_repairs_legacy_pending_fill_claim_without_touching_snapshot(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    with sf() as db:
        selected = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.symbol == "600001")
        ).one()
        outcome, snapshot = selected
        outcome_id, snapshot_db_id = outcome.id, snapshot.id
        evidence_before = snapshot.evidence
        outcome.fill_state = "ok"  # simulate legacy pre-assessment ORM default
        db.commit()

    repaired = archive_records(run_id, rows, sf)
    assert repaired["inserted"] == 0 and repaired["outcomes_inserted"] == 0
    assert repaired["outcomes_repaired"] == 1
    with sf() as db:
        outcome = db.get(OpportunityOutcomeLabel, outcome_id)
        snapshot = db.get(OpportunityDecisionSnapshot, snapshot_db_id)
        assert outcome.fill_state == "pending" and outcome.state == "pending"
        assert snapshot.evidence == evidence_before


def test_assessed_pending_fill_ok_is_not_reclassified_as_legacy_default(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    missing = label_trade_date("2026-09-16", {}, sf, price_basis_by_key=_basis("2026-09-16", "600001"))
    assert missing["pending"] == 1
    with sf() as db:
        outcome = db.execute(
            select(OpportunityOutcomeLabel)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.decision == "ranked")
        ).scalar_one()
        assert outcome.fill_state == "ok"
        assert outcome.reason != "等待收盘价"

    replayed = archive_records(run_id, rows, sf)
    assert replayed["outcomes_repaired"] == 0
    recovered = backfill_missing_outcome_identities(sf)
    assert recovered["repaired_pending_fill_state"] == 0
    with sf() as db:
        outcome = db.execute(
            select(OpportunityOutcomeLabel)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.decision == "ranked")
        ).scalar_one()
        assert outcome.fill_state == "ok"


def test_outcome_label_is_retryable_and_coverage_is_mechanical(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)

    assert pending_symbols("2026-09-16", sf) == {"600001"}
    assert pending_symbols("2026-09-16", sf, include_deferred=True) == {"600001", "600002"}
    missing = label_trade_date("2026-09-16", {}, sf, price_basis_by_key=_basis("2026-09-16", "600001"))
    assert missing == {"trade_date": "2026-09-16", "labeled": 0, "unknown": 0, "pending": 1}
    done = label_trade_date("2026-09-16", {"600001": 10.5}, sf, price_basis_by_key=_basis("2026-09-16", "600001"))
    assert done["labeled"] == 1 and done["pending"] == 0
    # 幂等：已标结果不重算，不允许未来一次价格覆盖原始 D0 标签。
    assert label_trade_date("2026-09-16", {"600001": 99.0}, sf, price_basis_by_key=_basis("2026-09-16", "600001"))["labeled"] == 0
    with sf() as db:
        label = db.execute(
            select(OpportunityOutcomeLabel)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.symbol == "600001")
        ).scalar_one()
    assert label.state == "labeled" and label.label == "positive"
    assert label.return_pct == 5.0 and label.outcome_price == 10.5
    summary = learning_summary("2026-09-16", sf)
    assert summary["label_coverage"] == 1.0
    assert summary["label_coverage_scope"] == "selected_outcome_rows_legacy"
    assert summary["funnel_denominator"] == {
        "snapshot_rows": 6, "symbols": 2, "outcome_rows": 6, "outcome_symbols": 2,
        "labeled_symbols": 1, "outcome_attachment_coverage": 1.0, "label_coverage": 0.5,
        "missing_outcome_symbols": [], "unlabeled_symbols": ["600002"],
        "run_symbol_opportunities": 2, "outcome_opportunities": 2,
        "labeled_opportunities": 1,
        "current_basis_labeled_opportunities": 1,
        "current_basis_version": PRICE_BASIS_VERSION,
        "current_basis_opportunity_coverage": 0.5,
        "opportunity_label_coverage": 0.5,
    }
    assert summary["stages"] == {"candidate": 2, "hard_gate": 2, "rank": 2, "notification": 0}


def test_legacy_full_funnel_identity_backfill_is_batched_idempotent_and_repairs_pending_fill(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)

    with sf() as db:
        snapshots = db.execute(select(OpportunityDecisionSnapshot)).scalars().all()
        selected = next(s for s in snapshots if s.stage == "rank" and s.decision == "ranked")
        selected_outcome = db.execute(
            select(OpportunityOutcomeLabel).where(
                OpportunityOutcomeLabel.snapshot_id == selected.snapshot_id,
                OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
            )
        ).scalar_one()
        selected_outcome.fill_state = "ok"
        db.execute(
            delete(OpportunityOutcomeLabel).where(
                OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
                OpportunityOutcomeLabel.snapshot_id != selected.snapshot_id,
            )
        )
        db.commit()

    got = backfill_missing_outcome_identities(
        sf, trade_dates=["2026-09-16"], batch_size=1
    )
    assert got == {
        "inserted": 5,
        "selected_pending": 0,
        "deferred": 5,
        "repaired_pending_fill_state": 1,
        "trade_dates": ["2026-09-16"],
    }

    with sf() as db:
        stored = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            )
            .where(OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON)
        ).all()
    assert len(stored) == 6
    selected_rows = [
        (outcome, snapshot) for outcome, snapshot in stored
        if snapshot.stage == "rank" and snapshot.decision == "ranked"
    ]
    assert len(selected_rows) == 1
    assert selected_rows[0][0].fill_state == "pending"
    deferred = [
        (outcome, snapshot) for outcome, snapshot in stored
        if not (snapshot.stage == "rank" and snapshot.decision == "ranked")
    ]
    assert len(deferred) == 5
    assert all(
        outcome.state == "deferred" and outcome.fill_state == "not_actionable"
        for outcome, _snapshot in deferred
    )
    assert all(
        outcome.path_state == "deferred" and outcome.path_version == PATH_VERSION
        for outcome, _snapshot in deferred
    )

    again = backfill_missing_outcome_identities(
        sf, trade_dates=["2026-09-16"], batch_size=2
    )
    assert again["inserted"] == 0
    assert again["repaired_pending_fill_state"] == 0


def test_legacy_pending_fill_repair_does_not_overwrite_assessed_pending_row(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    with sf() as db:
        selected = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            )
            .where(
                OpportunityDecisionSnapshot.stage == "rank",
                OpportunityDecisionSnapshot.decision == "ranked",
                OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
            )
        ).one()
        outcome, _snapshot = selected
        outcome.fill_state = "ok"
        outcome.reason = (
            "决策时点涨幅 2.10%，距 10% 涨停有余量；"
            "等待 d0_close@2026-09-16 有限且为正的收盘价"
        )
        db.commit()

    got = backfill_missing_outcome_identities(
        sf, trade_dates=["2026-09-16"]
    )
    assert got["repaired_pending_fill_state"] == 0
    with sf() as db:
        kept = db.execute(
            select(OpportunityOutcomeLabel)
            .join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            )
            .where(
                OpportunityDecisionSnapshot.stage == "rank",
                OpportunityDecisionSnapshot.decision == "ranked",
                OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
            )
        ).scalar_one()
    assert kept.fill_state == "ok"
    assert "决策时点涨幅" in kept.reason


def test_legacy_identity_backfill_keeps_full_funnel_market_result_nonactionable(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    with sf() as db:
        db.execute(delete(OpportunityOutcomeLabel))
        db.commit()

    inserted = backfill_missing_outcome_identities(
        sf, trade_dates=["2026-09-16"]
    )
    assert inserted["inserted"] == 6
    assert inserted["selected_pending"] == 1
    assert inserted["deferred"] == 5

    result = label_trade_date(
        "2026-09-16",
        {"600001": 10.5, "600002": 8.4},
        sf,
        price_basis_by_key=_basis("2026-09-16", "600001", "600002"),
        include_deferred=True,
        batch_size=1,
    )
    assert result["labeled"] == 6

    with sf() as db:
        outcome, _snapshot = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            )
            .where(
                OpportunityDecisionSnapshot.stage == "rank",
                OpportunityDecisionSnapshot.decision == "rejected",
                OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
            )
        ).one()
    assert outcome.state == "labeled"
    assert outcome.fill_state == "not_actionable"
    assert outcome.return_pct is not None
    assert outcome.net_return_pct is None


def test_deferred_denominator_backfill_records_market_result_without_fill_claim(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    # realtime lane only labels selected 600001; explicit offline lane later supplies both closes.
    label_trade_date("2026-09-16", {"600001": 10.5}, sf, price_basis_by_key=_basis("2026-09-16", "600001"))
    result = label_trade_date(
        "2026-09-16", {"600001": 10.5, "600002": 8.4}, sf,
        price_basis_by_key=_basis("2026-09-16", "600001", "600002"), include_deferred=True
    )
    assert result["labeled"] == 5
    with sf() as db:
        rejected = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.symbol == "600002")
        ).one()
    outcome, snapshot = rejected
    assert snapshot.decision == "rejected"
    assert outcome.state == "labeled" and outcome.return_pct == 5.0
    assert outcome.fill_state == "not_actionable"
    assert outcome.net_return_pct is None and outcome.cost_pct is None
    summary = learning_summary("2026-09-16", sf)
    assert summary["funnel_denominator"]["label_coverage"] == 1.0


def test_multihorizon_targets_use_trading_sessions_not_calendar_days():
    days = [
        date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22),
        date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25),
    ]
    assert outcome_target_dates("2026-09-18", days) == {
        "d0_close": "2026-09-18", "d1_close": "2026-09-21",
        "d3_close": "2026-09-23", "d5_close": "2026-09-25",
    }
    # Calendar does not contain the anchor/future: fail closed rather than weekday guessing.
    assert outcome_target_dates("2026-09-19", days) == {"d0_close": "2026-09-19"}
    assert outcome_target_dates("2026-09-25", days) == {"d0_close": "2026-09-25"}


def test_future_horizons_are_idempotent_and_do_not_expand_realtime_deferred_requests(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-18", as_of=datetime(2026, 9, 18, 10, 5)
    )
    archive_records(run_id, rows, sf)
    days = [
        date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22),
        date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25),
    ]
    first = ensure_outcome_horizons("2026-09-18", days, sf)
    second = ensure_outcome_horizons("2026-09-18", days, sf)
    assert first["inserted"] == 18 and second["inserted"] == 0
    assert first["missing_horizons"] == []
    assert due_outcome_symbols("2026-09-21", sf) == {"600001"}
    assert due_outcome_symbols("2026-09-21", sf, include_deferred=True) == {"600001", "600002"}
    # D0 compatibility helper must ignore future pending rows.
    assert pending_symbols("2026-09-18", sf) == {"600001"}


def test_future_horizons_selected_only_scope_does_not_materialize_deferred_rows(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-18", as_of=datetime(2026, 9, 18, 10, 5)
    )
    archive_records(run_id, rows, sf)
    days = [
        date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22),
        date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25),
    ]
    got = ensure_outcome_horizons(
        "2026-09-18", days, sf, include_deferred=False
    )
    assert got["scope"] == "selected_only"
    assert got["source_snapshots"] == 6 and got["snapshots"] == 1
    assert got["inserted"] == 3
    assert due_outcome_symbols("2026-09-21", sf) == {"600001"}
    assert due_outcome_symbols("2026-09-21", sf, include_deferred=True) == {"600001"}


def test_future_horizon_selected_scope_loads_only_selected_snapshots(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-18", as_of=datetime(2026, 9, 18, 10, 5)
    )
    archive_records(run_id, rows, sf)
    days = [
        date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22),
        date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25),
    ]
    loaded = []

    def _loaded(_target, _context):
        loaded.append(1)

    event.listen(OpportunityDecisionSnapshot, "load", _loaded)
    try:
        got = ensure_outcome_horizons(
            "2026-09-18", days, sf, include_deferred=False
        )
    finally:
        event.remove(OpportunityDecisionSnapshot, "load", _loaded)

    assert got["source_snapshots"] == 6
    assert got["snapshots"] == 1
    assert len(loaded) == 1


def test_pending_future_targets_are_selected_only_bounded_and_recover_overdue(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-18", as_of=datetime(2026, 9, 18, 10, 5)
    )
    archive_records(run_id, rows, sf)
    days = [
        date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22),
        date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25),
    ]
    ensure_outcome_horizons("2026-09-18", days, sf)

    # Monday D1 was missed; Tuesday run must still discover it for selected-only recovery.
    targets = pending_outcome_targets("2026-09-22", sf, lookback_days=30)
    assert targets["2026-09-21"] == {"600001"}
    assert "600002" not in set().union(*targets.values()), "deferred denominator must not expand realtime fetches"

    repaired = label_due_outcomes("2026-09-21", {"600001": 10.5}, sf, price_basis_by_key=_basis("2026-09-18", "600001"))
    assert repaired["labeled"] == 1
    assert "2026-09-21" not in pending_outcome_targets("2026-09-22", sf, lookback_days=30)

    # A bounded live recovery surface must not grow without limit.
    assert pending_outcome_targets("2026-10-31", sf, lookback_days=30) == {}


def test_d1_label_is_reference_proxy_not_shadow_fill_and_deferred_stays_nonactionable(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-18", as_of=datetime(2026, 9, 18, 10, 5)
    )
    archive_records(run_id, rows, sf)
    days = [
        date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22),
        date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25),
    ]
    ensure_outcome_horizons("2026-09-18", days, sf)
    realtime = label_due_outcomes("2026-09-21", {"600001": 10.5, "600002": 8.4}, sf, price_basis_by_key=_basis("2026-09-18", "600001", "600002"))
    assert realtime["labeled"] == 1
    offline = label_due_outcomes(
        "2026-09-21", {"600001": 10.5, "600002": 8.4}, sf,
        price_basis_by_key=_basis("2026-09-18", "600001", "600002"), include_deferred=True
    )
    assert offline["labeled"] == 5
    with sf() as db:
        selected = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityOutcomeLabel.horizon == "d1_close",
                   OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.symbol == "600001")
        ).one()
        rejected = db.execute(
            select(OpportunityOutcomeLabel, OpportunityDecisionSnapshot)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityOutcomeLabel.horizon == "d1_close",
                   OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.symbol == "600002")
        ).one()
    assert selected[0].state == "labeled" and selected[0].net_return_pct is not None
    assert "reference" in selected[0].reason and "非 shadow fill" in selected[0].reason
    assert rejected[0].state == "labeled" and rejected[0].fill_state == "not_actionable"
    assert rejected[0].net_return_pct is None
    card = opportunity_scorecard("2026-09-18", horizon="d1_close", session_factory=sf)
    assert card["metric_identity"]["realizable_return"] is False
    assert "cost_adjusted_reference_proxy_pct" in card["expectancy"]


def _path_snapshot(*, as_of: datetime | None = None, entry_price: float | None = 10.0):
    return OpportunityDecisionSnapshot(
        snapshot_id="path-s1", run_id="path-r1", trade_date="2026-09-16",
        as_of=as_of or datetime(2026, 9, 16, 10, 5, 30),
        scenario="intraday_opportunity", stage="rank", symbol="600001", name="甲",
        source_theme="算力", decision="ranked", rank=1,
        strategy_version=STRATEGY_VERSION, feature_version=FEATURE_VERSION,
        data_state="ready", entry_price=entry_price, evidence="{}",
    )


def _tencent_bar(hh: int, mm: int, *, high: float, low: float, source: str = "tencent"):
    # Kline.ts is UTC aware.  10:xx Beijing = 02:xx UTC.
    return Kline(
        symbol="600001", timeframe="1m",
        ts=datetime(2026, 9, 16, hh - 8, mm, tzinfo=timezone.utc),
        open=10.0, close=10.0, high=high, low=low, volume=1000, source=source,
    )


def test_corporate_action_qfq_basis_prevents_mechanical_dividend_loss(tmp_path):
    sf = _factory(tmp_path)
    snapshot = OpportunityDecisionSnapshot(
        snapshot_id="ca-s1", run_id="ca-r1", trade_date="2026-09-16",
        as_of=datetime(2026, 9, 16, 10, 5), scenario="intraday_opportunity",
        stage="rank", symbol="603444", name="吉比特", source_theme="游戏",
        decision="ranked", rank=1, strategy_version=STRATEGY_VERSION,
        feature_version=FEATURE_VERSION, data_state="ready", entry_price=373.49,
        evidence="{}",
    )
    outcome = OpportunityOutcomeLabel(
        snapshot_id="ca-s1", horizon="d1_close", target_date="2026-09-17",
        state="pending", label="unknown", reference_price=373.49,
        fill_state="pending", price_basis_version=PRICE_BASIS_VERSION,
    )
    with sf() as db:
        db.add(snapshot)
        db.add(outcome)
        db.commit()

    factor = 363.49 / 373.49  # real 2026-09-16 raw/qfq anchor before 09-17 ex-dividend
    result = label_due_outcomes(
        "2026-09-17", {"603444": 352.06}, sf,
        price_basis_by_key={
            ("2026-09-16", "603444"): (factor, "qfq:marketdb|raw:marketdb")
        },
        horizons=("d1_close",),
    )
    assert result["labeled"] == 1
    with sf() as db:
        saved = db.execute(select(OpportunityOutcomeLabel)).scalar_one()
    assert saved.reference_price == 373.49
    assert saved.basis_reference_price == pytest.approx(363.49)
    assert saved.reference_adjustment_factor == pytest.approx(factor)
    assert saved.return_pct == pytest.approx(-3.14)
    assert saved.return_pct != pytest.approx(round((352.06 / 373.49 - 1) * 100, 2))
    assert saved.price_basis_version == PRICE_BASIS_VERSION


def test_suspended_target_date_stays_pending_and_never_borrows_neighbor_close(tmp_path):
    sf = _factory(tmp_path)
    snapshot = OpportunityDecisionSnapshot(
        snapshot_id="susp-s1", run_id="susp-r1", trade_date="2026-09-18",
        as_of=datetime(2026, 9, 18, 10, 5), scenario="intraday_opportunity",
        stage="rank", symbol="600001", name="甲", source_theme="算力",
        decision="ranked", rank=1, strategy_version=STRATEGY_VERSION,
        feature_version=FEATURE_VERSION, data_state="ready", entry_price=10.0,
        evidence="{}",
    )
    outcome = OpportunityOutcomeLabel(
        snapshot_id="susp-s1", horizon="d1_close", target_date="2026-09-21",
        state="pending", label="unknown", reference_price=10.0,
        fill_state="pending", price_basis_version=PRICE_BASIS_VERSION,
    )
    with sf() as db:
        db.add_all([snapshot, outcome])
        db.commit()

    # The symbol may resume on 09-22, but this API accepts only the exact target
    # day's close map.  An absent 09-21 bar must remain pending, never borrow 09-22.
    got = label_due_outcomes(
        "2026-09-21", {}, sf,
        price_basis_by_key=_basis("2026-09-18", "600001"),
        horizons=("d1_close",),
    )
    assert got == {
        "target_date": "2026-09-21", "horizons": ["d1_close"],
        "labeled": 0, "unknown": 0, "pending": 1,
    }
    with sf() as db:
        saved = db.execute(select(OpportunityOutcomeLabel)).scalar_one()
    assert saved.state == "pending"
    assert saved.outcome_price is None and saved.return_pct is None
    assert "d1_close@2026-09-21" in saved.reason


def test_d0_path_excludes_predecision_and_decision_minute_and_computes_excursions():
    snap = _path_snapshot()
    bars = [
        _tencent_bar(10, 4, high=99.0, low=1.0),   # before decision: must never leak
        _tencent_bar(10, 5, high=50.0, low=2.0),   # decision minute: conservatively excluded
        _tencent_bar(10, 6, high=11.0, low=9.5),
        _tencent_bar(10, 7, high=10.8, low=9.8),
        _tencent_bar(15, 0, high=10.2, low=10.0),
    ]
    got = d0_path_metrics(
        snap, bars, limit_member=True, first_seal_time="10:20:00"
    )
    assert got["path_state"] == "labeled"
    assert got["path_high_price"] == 11.0 and got["path_low_price"] == 9.5
    assert got["mfe_pct"] == 10.0 and got["mae_pct"] == -5.0
    assert got["path_bar_count"] == 3
    assert got["limit_state"] == "hit" and got["first_limit_time"] == "10:20:00"
    assert got["time_to_limit_minutes"] == 14.5


def test_d0_path_never_turns_preexisting_limit_into_negative_time_to_limit():
    got = d0_path_metrics(
        _path_snapshot(), [
            _tencent_bar(10, 6, high=10.2, low=9.9),
            _tencent_bar(15, 0, high=10.1, low=10.0),
        ],
        limit_member=True, first_seal_time="09:55:00",
    )
    assert got["limit_state"] == "preexisting"
    assert got["first_limit_time"] == "09:55:00"
    assert got["time_to_limit_minutes"] is None


def test_time_to_limit_excludes_lunch_break_from_actionable_lead_time():
    got = d0_path_metrics(
        _path_snapshot(as_of=datetime(2026, 9, 16, 11, 29)),
        [_tencent_bar(15, 0, high=10.3, low=9.9)],
        limit_member=True,
        first_seal_time="13:01:00",
    )
    assert got["path_state"] == "labeled"
    assert got["limit_state"] == "hit"
    assert got["time_to_limit_minutes"] == 2.0


def test_d0_path_waits_for_close_complete_minute_series():
    snap = _path_snapshot()
    partial = d0_path_metrics(
        snap,
        [
            _tencent_bar(10, 6, high=11.0, low=9.5),
            _tencent_bar(14, 59, high=10.8, low=9.8),
        ],
        limit_member=False,
    )
    assert partial["path_state"] == "pending"
    assert partial["path_bar_count"] == 2
    assert partial["mfe_pct"] is None and partial["mae_pct"] is None
    assert "尚未覆盖收盘" in partial["path_reason"]

    complete = d0_path_metrics(
        snap,
        [
            _tencent_bar(10, 6, high=11.0, low=9.5),
            _tencent_bar(15, 0, high=10.2, low=10.0),
        ],
        limit_member=False,
    )
    assert complete["path_state"] == "labeled"
    assert complete["mfe_pct"] == 10.0 and complete["mae_pct"] == -5.0


def test_d0_path_rejects_non_tencent_minute_timestamps_and_missing_reference():
    wrong_source = d0_path_metrics(
        _path_snapshot(), [_tencent_bar(10, 6, high=11.0, low=9.0, source="eastmoney")],
        limit_member=False,
    )
    assert wrong_source["path_state"] == "pending"
    assert wrong_source["path_bar_count"] == 0
    assert wrong_source["limit_state"] == "not_hit"

    no_ref = d0_path_metrics(
        _path_snapshot(entry_price=None), [_tencent_bar(10, 6, high=11.0, low=9.0)],
        limit_member=None,
    )
    assert no_ref["path_state"] == "unknown"
    assert no_ref["mfe_pct"] is None and no_ref["mae_pct"] is None


def test_learning_summary_future_path_denominator_survives_missing_outcome_identity(tmp_path):
    sf = _factory(tmp_path)
    snap = _path_snapshot()
    with sf() as db:
        db.add(snap)
        db.commit()
    summary = learning_summary("2026-09-16", sf)
    d1 = summary["horizon_coverage"]["d1_close"]["path"]
    assert d1["selected_opportunities"] == 1
    assert d1["outcome_attached"] == 0
    assert d1["labeled_selected_opportunities"] == 0
    assert d1["coverage"] == 0.0


def test_cross_day_path_version_fits_persisted_schema_width():
    assert len(CROSS_DAY_PATH_VERSION) <= 32
    source = "d0:tencent_1m+zt_zb_pools|qfq_daily:eastmoney"
    assert len(source) <= 64


def _future_path_outcome(horizon: str, target_date: str):
    return OpportunityOutcomeLabel(
        snapshot_id="path-s1", horizon=horizon, target_date=target_date,
        state="pending", label="unknown", reference_price=10.0,
        fill_state="pending", price_basis_version=PRICE_BASIS_VERSION,
        path_state="not_started", path_version="",
    )


def _d0_path_outcome(*, high: float = 11.0, low: float = 9.5, state: str = "labeled"):
    return OpportunityOutcomeLabel(
        snapshot_id="path-s1", horizon=OUTCOME_HORIZON, target_date="2026-09-16",
        state="pending", label="unknown", reference_price=10.0, fill_state="pending",
        price_basis_version=PRICE_BASIS_VERSION, path_state=state, path_version=PATH_VERSION,
        path_high_price=high if state == "labeled" else None,
        path_low_price=low if state == "labeled" else None,
        mfe_pct=10.0 if state == "labeled" else None,
        mae_pct=-5.0 if state == "labeled" else None,
        path_bar_count=30 if state == "labeled" else 0, path_source="tencent_1m",
    )


def test_cross_day_path_is_corporate_action_safe_and_keeps_d0_postdecision_only():
    snap = _path_snapshot(entry_price=373.49)
    snap.symbol = "603444"
    d0 = _d0_path_outcome(high=380.0, low=370.0)
    d0.snapshot_id = "path-s1"
    future = _future_path_outcome("d1_close", "2026-09-17")
    factor = 363.49 / 373.49
    got = cross_day_path_metrics(
        snap, d0, future,
        {date(2026, 9, 17): (360.0, 350.0, "marketdb.daily_k_adj")},
        [date(2026, 9, 16), date(2026, 9, 17)],
        {("2026-09-16", "603444"): (factor, "qfq:marketdb.daily_k_adj|raw:marketdb.daily_k")},
    )
    expected_high = max(380.0, 360.0 / factor)
    expected_low = min(370.0, 350.0 / factor)
    assert got["path_state"] == "labeled"
    assert got["path_version"] == CROSS_DAY_PATH_VERSION
    assert got["path_high_price"] == pytest.approx(expected_high)
    assert got["path_low_price"] == pytest.approx(expected_low)
    assert got["mfe_pct"] == pytest.approx(round(max(0.0, (expected_high / 373.49 - 1) * 100), 2))
    assert got["mae_pct"] == pytest.approx(round(min(0.0, (expected_low / 373.49 - 1) * 100), 2))
    assert got["path_bar_count"] == 31
    assert "qfq_daily:marketdb.daily_k_adj" in got["path_source"]


def test_cross_day_path_ignores_bars_after_exact_target_horizon():
    snap = _path_snapshot()
    future = _future_path_outcome("d1_close", "2026-09-17")
    got = cross_day_path_metrics(
        snap, _d0_path_outcome(high=10.4, low=9.8), future,
        {
            date(2026, 9, 17): (10.6, 9.7, "test"),
            # Later extreme must not leak into the D1 horizon.
            date(2026, 9, 18): (99.0, 1.0, "test"),
        },
        [date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18)],
        {("2026-09-16", "600001"): (1.0, "qfq:test|raw:test")},
    )
    assert got["path_state"] == "labeled"
    assert got["path_high_price"] == 10.6
    assert got["path_low_price"] == 9.7
    assert got["mfe_pct"] == 6.0
    assert got["mae_pct"] == -3.0


def test_cross_day_path_missing_intermediate_market_session_fails_closed():
    snap = _path_snapshot()
    future = _future_path_outcome("d3_close", "2026-09-21")
    got = cross_day_path_metrics(
        snap, _d0_path_outcome(), future,
        {
            date(2026, 9, 17): (10.5, 9.8, "test"),
            # 2026-09-18 deliberately absent: cannot guess suspension vs data gap.
            date(2026, 9, 21): (11.0, 10.0, "test"),
        },
        [date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 21)],
        {("2026-09-16", "600001"): (1.0, "qfq:test|raw:test")},
    )
    assert got["path_state"] == "pending"
    assert got["mfe_pct"] is None and got["mae_pct"] is None
    assert "2026-09-18" in got["path_reason"]


def test_cross_day_path_rejects_cross_provider_qfq_and_bad_horizon_identity():
    snap = _path_snapshot()
    cross_source = cross_day_path_metrics(
        snap, _d0_path_outcome(), _future_path_outcome("d1_close", "2026-09-17"),
        {date(2026, 9, 17): (10.5, 9.8, "other")},
        [date(2026, 9, 16), date(2026, 9, 17)],
        {("2026-09-16", "600001"): (1.0, "qfq:test|raw:test")},
    )
    assert cross_source["path_state"] == "pending" and "source=other" in cross_source["path_reason"]

    bad_target = cross_day_path_metrics(
        snap, _d0_path_outcome(), _future_path_outcome("d3_close", "2026-09-17"),
        {date(2026, 9, 17): (10.5, 9.8, "test")},
        [date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 21)],
        {("2026-09-16", "600001"): (1.0, "qfq:test|raw:test")},
    )
    assert bad_target["path_state"] == "unknown"
    assert "目标交易日身份不一致" in bad_target["path_reason"]


def test_cross_day_path_requires_current_d0_path_and_terminal_unknown_propagates():
    snap = _path_snapshot()
    future = _future_path_outcome("d1_close", "2026-09-17")
    pending = cross_day_path_metrics(
        snap, None, future,
        {date(2026, 9, 17): (10.5, 9.8, "test")},
        [date(2026, 9, 16), date(2026, 9, 17)],
        {("2026-09-16", "600001"): (1.0, "qfq:test|raw:test")},
    )
    assert pending["path_state"] == "pending"
    terminal = cross_day_path_metrics(
        snap, _d0_path_outcome(state="unknown"), future,
        {date(2026, 9, 17): (10.5, 9.8, "test")},
        [date(2026, 9, 16), date(2026, 9, 17)],
        {("2026-09-16", "600001"): (1.0, "qfq:test|raw:test")},
    )
    assert terminal["path_state"] == "unknown"
    assert "终态 unknown" in terminal["path_reason"]


def test_cross_day_path_retry_surface_survives_close_label_completion(tmp_path):
    sf = _factory(tmp_path)
    snap = _path_snapshot()
    d0 = _d0_path_outcome()
    d1 = _future_path_outcome("d1_close", "2026-09-17")
    d1.state = "labeled"
    d1.label = "positive"
    d1.outcome_price = 10.4
    d1.return_pct = 4.0
    d1.path_state = "pending"
    d1.path_version = CROSS_DAY_PATH_VERSION
    with sf() as db:
        db.add_all([snap, d0, d1])
        db.commit()
    assert pending_outcome_targets("2026-09-17", sf) == {}
    assert pending_cross_day_path_targets("2026-09-17", sf) == {
        "2026-09-17": {"600001"}
    }


def test_cross_day_path_labeling_is_retryable_and_scorecard_independent_of_close_label(tmp_path):
    sf = _factory(tmp_path)
    snap = _path_snapshot()
    d0 = _d0_path_outcome()
    d1 = _future_path_outcome("d1_close", "2026-09-17")
    with sf() as db:
        db.add_all([snap, d0, d1])
        db.commit()

    assert pending_cross_day_path_targets("2026-09-17", sf) == {"2026-09-17": {"600001"}}
    first = label_cross_day_paths(
        "2026-09-17",
        {"600001": {date(2026, 9, 17): (10.8, 9.6, "test")}},
        [date(2026, 9, 16), date(2026, 9, 17)],
        {("2026-09-16", "600001"): (1.0, "qfq:test|raw:test")},
        sf, horizons=("d1_close",),
    )
    assert first["labeled"] == 1 and first["pending"] == 0
    assert pending_cross_day_path_targets("2026-09-17", sf) == {}

    # Close outcome deliberately remains pending: path maturity has its own denominator.
    card = opportunity_scorecard("2026-09-16", horizon="d1_close", session_factory=sf)
    path = card["path_metrics"]
    assert card["labeled"] == 0
    assert path["available"] is True
    assert path["version"] == CROSS_DAY_PATH_VERSION
    assert path["denominator"] == 1 and path["outcome_attached"] == 1
    assert path["evaluable"] == 1 and path["coverage"] == 1.0
    assert path["avg_mfe_pct"] == 10.0 and path["avg_mae_pct"] == -5.0
    assert path["limit_metrics_available"] is False


def test_current_version_terminal_unknown_does_not_retry_minute_fetch_forever(tmp_path):
    sf = _factory(tmp_path)
    item = {"symbol": "600009", "name": "缺价", "confidence": {"tier": "executable"}}
    run_id, rows = build_notification_records(
        [item], trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 30),
        hits=[{"item": item, "price": None, "chg": 2.0}], skips=[], dispatch_by_symbol={},
    )
    archive_records(run_id, rows, sf)
    assert pending_d0_path_symbols("2026-09-16", sf) == {"600009"}
    result = label_d0_paths(
        "2026-09-16", {"600009": [_tencent_bar(10, 31, high=10.2, low=9.8)]},
        limit_pool_known=False, session_factory=sf,
    )
    assert result["unknown"] == 1
    assert pending_d0_path_symbols("2026-09-16", sf) == set()


def test_d0_path_realtime_labels_selected_only_and_scorecard_exposes_identity(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5, 30)
    )
    archive_records(run_id, rows, sf)
    assert pending_d0_path_symbols("2026-09-16", sf) == {"600001"}
    assert pending_d0_path_symbols("2026-09-16", sf, include_deferred=True) == {"600001", "600002"}

    bars = {
        "600001": [
            _tencent_bar(10, 5, high=99.0, low=1.0),
            _tencent_bar(10, 6, high=10.8, low=9.7),
            _tencent_bar(15, 0, high=10.2, low=10.0),
        ],
    }
    result = label_d0_paths(
        "2026-09-16", bars,
        first_seal_by_symbol={"600001": "10:20:00"}, limit_pool_known=True,
        session_factory=sf,
    )
    assert result["labeled"] == 1 and result["skipped_deferred"] == 5
    label_trade_date("2026-09-16", {"600001": 10.5}, sf, price_basis_by_key=_basis("2026-09-16", "600001"))

    with sf() as db:
        selected = db.execute(
            select(OpportunityOutcomeLabel)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.symbol == "600001",
                   OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON)
        ).scalar_one()
        rejected = db.execute(
            select(OpportunityOutcomeLabel)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.symbol == "600002",
                   OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON)
        ).scalar_one()
    assert selected.path_state == "labeled" and selected.mfe_pct == 8.0 and selected.mae_pct == -3.0
    assert selected.limit_state == "hit" and selected.time_to_limit_minutes == 14.5
    assert rejected.path_state == "deferred" and rejected.mfe_pct is None

    summary = learning_summary("2026-09-16", sf)
    assert summary["d0_path"]["selected_path_coverage"] == 1.0
    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    path = card["path_metrics"]
    assert path["evaluable"] == 1 and path["avg_mfe_pct"] == 8.0 and path["avg_mae_pct"] == -3.0
    assert path["limit_hits_after_decision"] == 1 and path["post_decision_timed_hits"] == 1
    assert path["ever_limit_hits"] == 1 and path["limit_membership_evaluable"] == 1
    assert path["limit_states"] == {"hit": 1}
    assert path["avg_time_to_limit_minutes"] == 14.5
    assert path["available"] is True and "not shadow-fill" in path["identity"]
    assert path["denominator"] == 1 and path["coverage"] == 1.0


def test_path_metrics_exclude_degraded_selected_decision_but_keep_audit_denominator(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5, 30)
    )
    archive_records(run_id, rows, sf)
    with sf() as db:
        snapshot = db.execute(
            select(OpportunityDecisionSnapshot).where(
                OpportunityDecisionSnapshot.symbol == "600001",
                OpportunityDecisionSnapshot.stage == "rank",
                OpportunityDecisionSnapshot.decision == "ranked",
            )
        ).scalars().first()
        assert snapshot is not None
        snapshot.data_state = "degraded"
        db.commit()

    label_d0_paths(
        "2026-09-16",
        {"600001": [
            _tencent_bar(10, 6, high=10.8, low=9.7),
            _tencent_bar(15, 0, high=10.2, low=10.0),
        ]},
        first_seal_by_symbol={"600001": "10:20:00"},
        limit_pool_known=True,
        session_factory=sf,
    )

    path = opportunity_scorecard("2026-09-16", session_factory=sf)["path_metrics"]
    assert path["audit_denominator"] == 1
    assert path["data_state_required"] == "ready"
    assert path["data_state_excluded"] == 1
    assert path["denominator"] == 0
    assert path["evaluable"] == 0
    assert path["coverage"] is None
    assert path["avg_mfe_pct"] is None and path["avg_mae_pct"] is None


def test_path_scorecard_is_independent_of_close_label_availability(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5, 30)
    )
    archive_records(run_id, rows, sf)
    label_d0_paths(
        "2026-09-16",
        {"600001": [
            _tencent_bar(10, 6, high=10.8, low=9.7),
            _tencent_bar(15, 0, high=10.2, low=10.0),
        ]},
        first_seal_by_symbol={"600001": "10:20:00"},
        limit_pool_known=True,
        session_factory=sf,
    )

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["labeled"] == 0, "close outcome intentionally remains pending"
    path = card["path_metrics"]
    assert path["denominator"] == 1
    assert path["evaluable"] == 1 and path["coverage"] == 1.0
    assert path["avg_mfe_pct"] == 8.0 and path["avg_mae_pct"] == -3.0

    with sf() as db:
        stored = db.execute(
            select(OpportunityOutcomeLabel).where(
                OpportunityOutcomeLabel.path_state == "labeled"
            )
        ).scalar_one()
        stored.path_version = "legacy-path-v0"
        db.commit()

    old = opportunity_scorecard("2026-09-16", session_factory=sf)["path_metrics"]
    assert old["denominator"] == 1 and old["evaluable"] == 0 and old["coverage"] == 0.0
    summary = learning_summary("2026-09-16", sf)["d0_path"]
    assert summary["labeled_selected_opportunities"] == 0
    assert summary["versions"]["legacy-path-v0"] == 1


def test_path_coverage_keeps_selected_snapshot_when_outcome_row_is_missing(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5, 30)
    )
    archive_records(run_id, rows, sf)

    with sf() as db:
        selected = db.execute(
            select(OpportunityDecisionSnapshot).where(
                OpportunityDecisionSnapshot.symbol == "600001",
                OpportunityDecisionSnapshot.stage == "rank",
                OpportunityDecisionSnapshot.decision == "ranked",
            )
        ).scalars().first()
        assert selected is not None
        db.execute(
            delete(OpportunityOutcomeLabel).where(
                OpportunityOutcomeLabel.snapshot_id == selected.snapshot_id,
                OpportunityOutcomeLabel.horizon == OUTCOME_HORIZON,
            )
        )
        db.commit()

    path = opportunity_scorecard("2026-09-16", session_factory=sf)["path_metrics"]
    assert path["denominator"] == 1
    assert path["outcome_attached"] == 0
    assert path["evaluable"] == 0 and path["coverage"] == 0.0

    summary = learning_summary("2026-09-16", sf)["d0_path"]
    assert summary["selected_opportunities"] == 1
    assert summary["labeled_selected_opportunities"] == 0
    assert summary["selected_path_coverage"] == 0.0


def test_missing_entry_price_becomes_unknown_not_zero_return(tmp_path):
    sf = _factory(tmp_path)
    payload = _payload()
    payload["themes"][0]["participants"][0]["price"] = None
    run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    result = label_trade_date("2026-09-16", {"600001": 10.5}, sf, price_basis_by_key=_basis("2026-09-16", "600001"))
    assert result["unknown"] == 1
    with sf() as db:
        label = db.execute(
            select(OpportunityOutcomeLabel)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.symbol == "600001")
        ).scalar_one()
    assert label.state == "unknown" and label.return_pct is None
    assert "价格缺失" in label.reason


def test_notification_replay_keeps_rejections_and_dispatch_result(tmp_path):
    sf = _factory(tmp_path)
    items = [
        {"symbol": "600001", "name": "甲", "confidence": {"tier": "strong"}},
        {"symbol": "600002", "name": "乙", "confidence": {"tier": "observe"}},
    ]
    hits = [{"item": items[0], "price": 10.0, "chg": 2.0}]
    skips = [{"symbol": "600002", "reason": "置信档 observe 不足"}]
    run_id, rows = build_notification_records(
        items, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 30),
        hits=hits, skips=skips, dispatch_by_symbol={"600001": "notified"},
    )
    archive_records(run_id, rows, sf)

    assert {r["decision"] for r in rows} == {"notified", "rejected"}
    replay = replay_run(run_id, sf)
    assert replay["mismatches"] == 0
    rejected = next(i for i in replay["items"] if i["symbol"] == "600002")
    assert "observe" in rejected["evidence"]["gate_reason"]


def test_archived_evidence_is_valid_json_and_contains_versions(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    with sf() as db:
        stored = db.execute(select(OpportunityDecisionSnapshot)).scalars().all()
    assert all(json.loads(row.evidence) for row in stored)
    assert all(row.strategy_version and row.feature_version and row.as_of for row in stored)


# ── RSH-026 第二批（2026-09-16）：成本后净收益 + 可成交性 + 样本门禁 ──────────────


def test_net_return_is_sourced_from_calc_fee_not_a_second_rate_table():
    """净收益必须**真的消费** `paper.engine.calc_fee`，而不是自带一份费率常量。

    判据不是「值对得上」（抄一遍公式也能对得上），而是**改费率 ⇒ 净收益跟着变**
    —— 这才是「同源」的可证伪形式（同族：`paper/reconcile.py` 的费率漂移自检）。
    """
    from app.paper.engine import FEE, calc_fee

    net, cost, qty = round_trip_net_pct(10.0, 10.5)
    assert qty == 10_000, "10 万元名义本金 ÷ 10 元 = 1 万整股，整手口径"
    buy_fee = calc_fee("buy", 10.0, qty)
    sell_fee = calc_fee("sell", 10.5, qty)
    paid = 10.0 * qty + buy_fee
    assert cost == round((buy_fee + sell_fee) / paid * 100, 4)
    assert net == round((10.5 * qty - sell_fee - paid) / paid * 100, 2)
    # 同口径毛收益：净必须**严格小于**毛，差额即成本（本例约 0.11 个百分点）
    gross = round((10.5 * qty - 10.0 * qty) / paid * 100, 2)
    assert net < gross
    assert 0 < gross - net - cost < 0.02, f"差额 {gross - net} 应约等于成本率 {cost}"

    original = FEE["commission_rate"]
    try:
        FEE["commission_rate"] = original * 8
        higher_cost_net, higher_cost, _q = round_trip_net_pct(10.0, 10.5)
    finally:
        FEE["commission_rate"] = original
    assert higher_cost > cost and higher_cost_net < net, (
        "费率提高后成本率必须上升、净收益必须下降；"
        "若不变，说明本模块自带了一份费率常量（同源契约被破坏）"
    )


def test_flat_close_turns_positive_gross_into_negative_net():
    """毛收益 0% 时净收益必须为负——这是「扣成本」最直观的可证伪点。"""
    net, _cost, _q = round_trip_net_pct(10.0, 10.0)
    assert net < 0, "平盘卖出必亏双边成本；若净收益为 0 或正，说明成本没真的扣"


def test_fill_state_boundaries_follow_board_limit_and_tolerance():
    """可成交性判据：涨停幅度取 `price_rules.limit_pct`，容差取 `FILL_SEAL_GAP_PCT`。"""
    from app.market.price_rules import limit_pct

    limit = limit_pct("600001")
    assert limit == 10.0
    # 阈值恰为 limit − 容差（10 − 0.15 = 9.85）：两侧各钉一点
    assert assess_fill_state("600001", limit - FILL_SEAL_GAP_PCT)[0] == "sealed"
    assert assess_fill_state("600001", limit - FILL_SEAL_GAP_PCT - 0.01)[0] == "ok"
    # 板块差异必须生效：同样 12% 在主板可买、在创业板距 20% 仍有余量、在 19.9% 封板
    assert assess_fill_state("300750", 19.9)[0] == "sealed"
    assert assess_fill_state("300750", 12.0)[0] == "ok"
    assert assess_fill_state("832000", 29.9)[0] == "sealed"
    assert assess_fill_state("600001", None)[0] == "no_quote"


def test_label_trade_date_actually_wires_net_return_into_the_row(tmp_path):
    """**接线守卫**：`label_trade_date` 必须真的调用净收益口径并把结果落库。

    本用例是注入自证抓出的补丁：上面那几条只测**纯函数**（`round_trip_net_pct` /
    `assess_fill_state`），而「纯函数对」**不等于**「调用它」—— 把 `label_trade_date`
    里的净收益换成 `net = ret, cost = 0.0` 时，纯函数用例全绿、只有本用例变红。
    判据刻意取**关系式**（`net < gross`、`cost > 0`、与独立复算相等）而非硬编码数值。
    """
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    label_trade_date("2026-09-16", {"600001": 10.5}, sf, price_basis_by_key=_basis("2026-09-16", "600001"))

    expected_net, expected_cost, _q = round_trip_net_pct(10.0, 10.5)
    with sf() as db:
        row = db.execute(
            select(
                OpportunityOutcomeLabel.return_pct,
                OpportunityOutcomeLabel.net_return_pct,
                OpportunityOutcomeLabel.cost_pct,
            ).join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            ).where(
                OpportunityDecisionSnapshot.stage == "rank",
                OpportunityDecisionSnapshot.symbol == "600001",
            )
        ).one()
    gross, net, cost = float(row[0]), float(row[1]), float(row[2])
    assert net == expected_net and cost == expected_cost, "落库值必须等于口径函数的独立复算"
    assert net < gross, f"净 {net} 必须严格小于毛 {gross}；相等即成本没真的扣"
    assert cost > 0, "成本率必须为正"
    assert round(gross - net, 3) >= cost * 0.9, "毛净差额应与成本量级一致"


def test_sealed_board_keeps_net_return_none_and_never_zero(tmp_path):
    """封板买不到 ⇒ 净收益必须是 `None`，**严禁记 0**。

    记 0 会把「买不到」混进「零收益」样本，使净期望被系统性高估
    （与既有 `test_missing_entry_price_becomes_unknown_not_zero_return` 同族）。
    """
    sf = _factory(tmp_path)
    payload = _payload()
    participants = payload["themes"][0]["participants"]
    participants[0]["change_pct"] = 9.95      # 贴 10% 涨停 ⇒ sealed
    participants[1]["change_pct"] = 1.2       # 距涨停有余量 ⇒ ok
    # ⚠️ 必须让两只都进 `ranked`：结果标签只为 ranked/notified/suppressed 建档，
    # 联动为「低」者判 rejected ⇒ 根本没有 outcome 行（这是既有漏斗语义，非缺陷）。
    participants[1]["linkage"] = {"level": "高", "basis": "题材成建制且进入临板区"}
    run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    label_trade_date("2026-09-16", {"600001": 10.1, "600002": 8.2}, sf, price_basis_by_key=_basis("2026-09-16", "600001", "600002"))

    with sf() as db:
        rows = db.execute(
            select(
                OpportunityDecisionSnapshot.symbol,
                OpportunityOutcomeLabel.fill_state,
                OpportunityOutcomeLabel.net_return_pct,
                OpportunityOutcomeLabel.cost_pct,
                OpportunityOutcomeLabel.return_pct,
                OpportunityOutcomeLabel.reason,
            ).join(
                OpportunityDecisionSnapshot,
                OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id,
            ).where(OpportunityDecisionSnapshot.stage == "rank")
        ).all()
    by_symbol = {symbol: (fill, net, cost, ret, reason) for symbol, fill, net, cost, ret, reason in rows}
    sealed, ok = by_symbol["600001"], by_symbol["600002"]
    # 元组序：(fill_state, net_return_pct, cost_pct, return_pct, reason)
    assert sealed[:3] == ("sealed", None, None)
    assert ok[0] == "ok" and ok[1] is not None
    # 封板的**毛收益照常记录**（信号方向仍可评估），只是不进净期望、不给成本
    assert sealed[3] is not None and sealed[2] is None
    assert "封板买不到" in sealed[4]
    summary = learning_summary("2026-09-16", sf)
    assert summary["fill_states"] == {"not_actionable": 4, "ok": 1, "sealed": 1}
    assert summary["cost_model"] == COST_MODEL_VERSION


def test_scorecard_distinguishes_empty_version_from_incomplete_denominator(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)

    card = opportunity_scorecard(
        "2026-09-16", session_factory=sf,
        strategy_version="missing-strategy", feature_version="missing-feature",
    )
    assert card["funnel_denominator"]["state"] == "empty"
    assert card["funnel_denominator"]["complete"] is False
    assert card["funnel_denominator"]["run_symbol_opportunities"] == 0
    assert card["verdict"] == "no_matching_denominator"


def test_scorecard_blocks_verdict_until_full_funnel_denominator_is_labeled(tmp_path):
    """selected 样本全标不等于全漏斗完整；漏掉 rejected 时必须先报 incomplete_denominator。"""
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    label_trade_date("2026-09-16", {"600001": 10.5, "600002": 8.2}, sf, price_basis_by_key=_basis("2026-09-16", "600001", "600002"))

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["verdict"] == "incomplete_denominator"
    assert card["funnel_denominator"]["outcome_attachment_coverage"] == 1.0
    assert card["funnel_denominator"]["label_coverage"] == 0.5
    assert card["funnel_denominator"]["unlabeled_symbols"] == ["600002"]


def test_scorecard_does_not_let_later_selected_run_cover_earlier_rejected_run(tmp_path):
    sf = _factory(tmp_path)
    when = datetime(2026, 9, 16, 10, 5)
    # Later run has a selected/labeled observation for the same symbol.
    _seed_scorecard_label(
        sf, snapshot_id="later-selected", run_id="run-b", symbol="600001",
        as_of=when, stage="rank", rank=1,
    )
    # Earlier run rejected the same symbol and still lacks an offline market label.
    with sf() as db:
        db.add(OpportunityDecisionSnapshot(
            snapshot_id="earlier-rejected", run_id="run-a", trade_date="2026-09-16",
            as_of=datetime(2026, 9, 16, 9, 45), scenario="intraday_opportunity",
            stage="rank", symbol="600001", name="甲", source_theme="T", decision="rejected",
            rank=None, strategy_version=STRATEGY_VERSION, feature_version=FEATURE_VERSION,
            data_state="ready", entry_price=9.8, evidence=json.dumps({"change_pct": 1.0}),
        ))
        db.add(OpportunityOutcomeLabel(
            snapshot_id="earlier-rejected", horizon=OUTCOME_HORIZON, target_date="2026-09-16",
            state="deferred", label="unknown", reference_price=9.8, fill_state="not_actionable",
            reason="denominator only",
        ))
        db.commit()

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["funnel_denominator"]["symbols"] == 1
    assert card["funnel_denominator"]["label_coverage"] == 1.0
    assert card["funnel_denominator"]["run_symbol_opportunities"] == 2
    assert card["funnel_denominator"]["labeled_opportunities"] == 1
    assert card["funnel_denominator"]["opportunity_label_coverage"] == 0.5
    assert card["funnel_denominator"]["complete"] is False
    assert card["verdict"] == "incomplete_denominator"


def test_scorecard_refuses_verdict_until_min_labels_reached(tmp_path):
    """全漏斗结果齐后，样本仍不足时才进入 insufficient_sample。"""
    from app.picks.opportunity_learning import MIN_LABELS_FOR_VERDICT

    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    label_trade_date(
        "2026-09-16", {"600001": 10.5, "600002": 8.2}, sf,
        price_basis_by_key=_basis("2026-09-16", "600001", "600002"),
        include_deferred=True
    )

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["funnel_denominator"]["complete"] is True
    assert card["min_labels_for_verdict"] == MIN_LABELS_FOR_VERDICT
    assert card["fillable"] < MIN_LABELS_FOR_VERDICT
    assert card["verdict"] == "insufficient_sample"
    assert "不构成买卖建议" in card["note"]
    assert "deferred/not_actionable" in card["note"]


def test_scorecard_expectancy_fields_never_mix_denominators(tmp_path):
    """毛/净期望**分母不同**，故必须另给同分母毛期望——否则差额会被读成负成本。

    本用例刻意构造「封板样本毛收益低于可成交样本」：若有人把全样本 `gross_pct`
    与 `net_pct` 相减当成本，会得到**负成本**（净 > 毛）的荒谬结论。
    """
    sf = _factory(tmp_path)
    payload = _payload()
    participants = payload["themes"][0]["participants"]
    participants[0]["change_pct"] = 9.95   # sealed，D0 收盘 +1%（买不到，毛收益仍记账）
    participants[1]["change_pct"] = 1.2    # ok，D0 收盘 +5%
    # 两只都必须进 `ranked` 才有 outcome 行（rejected 不建档，见既有漏斗语义）
    participants[1]["linkage"] = {"level": "高", "basis": "题材成建制且进入临板区"}
    run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    label_trade_date("2026-09-16", {"600001": 10.1, "600002": 8.4}, sf, price_basis_by_key=_basis("2026-09-16", "600001", "600002"))

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    exp = card["expectancy"]
    assert exp["gross_labels"] == 2 and exp["fillable_labels"] == 1
    # 同分母差额 = 成本（正数、量级合理）
    assert exp["gross_on_fillable_pct"] is not None and exp["net_pct"] is not None
    gap = round(exp["gross_on_fillable_pct"] - exp["net_pct"], 4)
    assert 0 < gap < 0.5, f"同分母差额 {gap} 应为正的成本量级"
    assert exp["gross_on_fillable_pct"] != exp["gross_pct"], "两个毛期望的分母不同，不可同值"
    # 反例自证：全样本毛期望**低于**净期望 ⇒ 两者相减会得负成本。
    # 若本条变红，说明有人把 gross_pct 改成了同分母口径 ⇒ 应删除本条并同步
    # 更新 opportunity_scorecard 文档里的「分母纪律」段落（不是放宽断言）。
    assert exp["net_pct"] > exp["gross_pct"], (
        "本用例的构造前提是「封板样本拉低全样本毛期望」；该前提消失时应更新用例而非删除判据"
    )


def _seed_scorecard_label(
    sf, *, snapshot_id: str, run_id: str, symbol: str, as_of: datetime,
    stage: str = "rank", rank: int | None = 1, horizon: str = OUTCOME_HORIZON,
    strategy_version: str = STRATEGY_VERSION, feature_version: str = FEATURE_VERSION,
    gross: float = 1.0, proxy: float | None = 0.9, source_theme: str = "T",
    reason: str | None = None, price_basis_version: str = PRICE_BASIS_VERSION,
    data_state: str = "ready",
) -> None:
    decision = "ranked" if stage == "rank" else "notified"
    with sf() as db:
        db.add(OpportunityDecisionSnapshot(
            snapshot_id=snapshot_id, run_id=run_id, trade_date="2026-09-16", as_of=as_of,
            scenario="intraday_opportunity", stage=stage, symbol=symbol, name=symbol,
            source_theme=source_theme, decision=decision, rank=rank if stage == "rank" else None,
            strategy_version=strategy_version, feature_version=feature_version,
            data_state=data_state, entry_price=10.0, evidence=json.dumps({"change_pct": 1.0}),
        ))
        db.add(OpportunityOutcomeLabel(
            snapshot_id=snapshot_id, horizon=horizon, target_date="2026-09-16",
            state="labeled", label="positive", reference_price=10.0, outcome_price=10.1,
            return_pct=gross, fill_state="ok", cost_pct=0.1 if proxy is not None else None,
            net_return_pct=proxy, price_basis_version=price_basis_version,
            reason=reason or f"D0 cost proxy ({COST_MODEL_VERSION})",
        ))
        db.commit()

def test_scorecard_blocks_effect_when_zero_record_run_is_degraded(tmp_path):
    from app.picks.opportunity_learning import MIN_LABELS_FOR_VERDICT

    sf = _factory(tmp_path)
    when = datetime(2026, 9, 16, 10, 5)
    for i in range(MIN_LABELS_FOR_VERDICT):
        _seed_scorecard_label(
            sf, snapshot_id=f"run-quality-ready-{i}", run_id=f"run-quality-{i}",
            symbol=f"{601000 + i:06d}", as_of=when, proxy=1.0, data_state="ready",
        )
    with sf() as db:
        db.add(OpportunityDecisionRun(
            run_id="zero-degraded-run", trade_date="2026-09-16", as_of=when,
            scenario="intraday_opportunity", strategy_version=STRATEGY_VERSION,
            feature_version=FEATURE_VERSION, data_state="degraded",
            snapshot_state="stale", snapshot_as_of="2026-09-16T02:05:00+00:00",
            records_total=0, evidence_digest="d" * 64,
        ))
        db.commit()

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["funnel_denominator"]["complete"] is True
    assert card["sample"]["count"] == MIN_LABELS_FOR_VERDICT
    assert card["evidence_quality"]["decision_runs"] == 1
    assert card["evidence_quality"]["zero_record_runs"] == 1
    assert card["evidence_quality"]["run_states"] == {"degraded": 1}
    assert card["evidence_quality"]["unready_runs"] == 1
    assert card["evidence_quality"]["complete"] is False
    assert card["verdict"] == "degraded_input"

    summary = learning_summary("2026-09-16", sf)
    assert summary["evidence_quality"]["run_states"] == {"degraded": 1}
    assert summary["evidence_quality"]["unready_runs"] == 1
    assert summary["evidence_quality"]["complete"] is False


def test_scorecard_blocks_effect_verdict_when_funnel_contains_degraded_input(tmp_path):
    """标签全齐、ready 样本已达下限，也不能跨过 degraded 输入质量门。"""
    from app.picks.opportunity_learning import MIN_LABELS_FOR_VERDICT

    sf = _factory(tmp_path)
    when = datetime(2026, 9, 16, 10, 5)
    for i in range(MIN_LABELS_FOR_VERDICT):
        _seed_scorecard_label(
            sf, snapshot_id=f"ready-{i}", run_id=f"run-ready-{i}",
            symbol=f"{600100 + i:06d}", as_of=when, proxy=1.0, data_state="ready",
        )
    _seed_scorecard_label(
        sf, snapshot_id="degraded-1", run_id="run-degraded", symbol="600999",
        as_of=when, proxy=99.0, data_state="degraded",
    )

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["funnel_denominator"]["complete"] is True
    assert card["funnel_denominator"]["labeled_opportunities"] == MIN_LABELS_FOR_VERDICT + 1
    assert card["evidence_quality"]["states"] == {"degraded": 1, "ready": MIN_LABELS_FOR_VERDICT}
    assert card["evidence_quality"]["ready_run_symbol_opportunities"] == MIN_LABELS_FOR_VERDICT
    assert card["evidence_quality"]["unready_run_symbol_opportunities"] == 1
    assert card["evidence_quality"]["complete"] is False
    assert card["audit"]["data_state_excluded_labeled_rows"] == 1
    assert card["sample"]["count"] == MIN_LABELS_FOR_VERDICT
    assert card["fillable"] == MIN_LABELS_FOR_VERDICT
    assert card["verdict"] == "degraded_input"

    summary = learning_summary("2026-09-16", sf)
    assert summary["evidence_quality"]["states"] == {
        "degraded": 1, "ready": MIN_LABELS_FOR_VERDICT
    }
    assert summary["evidence_quality"]["ready_run_symbol_opportunities"] == MIN_LABELS_FOR_VERDICT
    assert summary["evidence_quality"]["unready_run_symbol_opportunities"] == 1
    assert summary["evidence_quality"]["complete"] is False


def test_evidence_quality_requires_all_stages_of_run_symbol_to_be_ready(tmp_path):
    sf = _factory(tmp_path)
    when = datetime(2026, 9, 16, 10, 5)
    _seed_scorecard_label(
        sf, snapshot_id="mixed-rank", run_id="run-mixed", symbol="600001",
        as_of=when, stage="rank", rank=1, proxy=1.0, data_state="ready",
    )
    _seed_scorecard_label(
        sf, snapshot_id="mixed-candidate", run_id="run-mixed", symbol="600001",
        as_of=when, stage="candidate", rank=None, proxy=1.0, data_state="degraded",
    )

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["funnel_denominator"]["run_symbol_opportunities"] == 1
    assert card["funnel_denominator"]["complete"] is True
    assert card["evidence_quality"]["ready_run_symbol_opportunities"] == 0
    assert card["evidence_quality"]["unready_run_symbol_opportunities"] == 1
    assert card["evidence_quality"]["complete"] is False
    assert card["verdict"] == "degraded_input"


def test_precision_at_k_keeps_degraded_rank_visible_but_not_evaluable(tmp_path):
    sf = _factory(tmp_path)
    when = datetime(2026, 9, 16, 10, 5)
    _seed_scorecard_label(
        sf, snapshot_id="pq-ready", run_id="run-q", symbol="600001",
        as_of=when, rank=1, proxy=1.0, data_state="ready",
    )
    _seed_scorecard_label(
        sf, snapshot_id="pq-degraded", run_id="run-q", symbol="600002",
        as_of=when, rank=2, proxy=50.0, data_state="degraded",
    )

    card = opportunity_scorecard(
        "2026-09-16", top_k=2, run_id="run-q", session_factory=sf
    )
    p = card["precision_at_k"]
    assert p["symbols"] == ["600001", "600002"]
    assert p["selected"] == 2 and p["evaluable"] == 1
    assert p["coverage"] == 0.5 and p["observed"] == 1.0
    assert p["data_state_counts"] == {"degraded": 1, "ready": 1}


def test_scorecard_36_audit_rows_are_one_symbol_day_sample(tmp_path):
    sf = _factory(tmp_path)
    stages = ("candidate", "hard_gate", "rank", "notification")
    for run_no in range(9):
        for stage_no, stage in enumerate(stages):
            _seed_scorecard_label(
                sf, snapshot_id=f"s-{run_no}-{stage}", run_id=f"run-{run_no}",
                symbol="600001", as_of=datetime(2026, 9, 16, 9, 30 + run_no),
                stage=stage, rank=1, source_theme=f"T{stage_no}",
            )

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["audit"]["labeled_rows"] == 36
    assert card["sample"]["unit"] == "symbol_trade_date"
    assert card["sample"]["count"] == 1
    assert card["denominators"] == {
        "audit_rows": 36, "runs": 9, "run_symbol_opportunities": 9,
        "symbols": 1, "symbol_trade_date_samples": 1,
    }
    assert card["fillable"] == 1
    assert card["verdict"] == "insufficient_sample"


def test_scorecard_top_k_is_single_run_and_unique_symbol(tmp_path):
    sf = _factory(tmp_path)
    when = datetime(2026, 9, 16, 10, 5)
    _seed_scorecard_label(
        sf, snapshot_id="a1", run_id="run-a", symbol="600001", as_of=when,
        rank=1, source_theme="T1", proxy=1.0,
    )
    _seed_scorecard_label(
        sf, snapshot_id="a2", run_id="run-a", symbol="600001", as_of=when,
        rank=2, source_theme="T2", proxy=-1.0,
    )
    _seed_scorecard_label(
        sf, snapshot_id="a3", run_id="run-a", symbol="600002", as_of=when,
        rank=3, source_theme="T1", proxy=1.0,
    )
    _seed_scorecard_label(
        sf, snapshot_id="b1", run_id="run-b", symbol="600003",
        as_of=datetime(2026, 9, 16, 10, 6), rank=1, source_theme="T3", proxy=-1.0,
    )

    card = opportunity_scorecard("2026-09-16", top_k=2, run_id="run-a", session_factory=sf)
    p = card["precision_at_k"]
    assert p["run_id"] == "run-a" and p["k_requested"] == 2
    assert p["symbols"] == ["600001", "600002"]
    assert p["selected"] == 2 and p["evaluable"] == 2
    assert p["observed"] == 1.0


def test_scorecard_filters_horizon_and_versions_explicitly(tmp_path):
    sf = _factory(tmp_path)
    when = datetime(2026, 9, 16, 10, 5)
    _seed_scorecard_label(
        sf, snapshot_id="current-d0", run_id="run-current", symbol="600001",
        as_of=when, horizon=OUTCOME_HORIZON,
    )
    _seed_scorecard_label(
        sf, snapshot_id="current-d1", run_id="run-current-d1", symbol="600002",
        as_of=when, horizon="d1_close",
    )
    _seed_scorecard_label(
        sf, snapshot_id="legacy-d0", run_id="run-legacy", symbol="600003",
        as_of=when, strategy_version="legacy-strategy", feature_version="legacy-features",
    )

    current = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert current["filters"] == {
        "horizon": OUTCOME_HORIZON,
        "strategy_version": STRATEGY_VERSION,
        "feature_version": FEATURE_VERSION,
    }
    assert current["audit"]["labeled_rows"] == 1
    d1 = opportunity_scorecard("2026-09-16", horizon="d1_close", session_factory=sf)
    assert d1["audit"]["labeled_rows"] == 1
    assert d1["path_metrics"]["available"] is True
    assert d1["path_metrics"]["evaluable"] == 0
    assert d1["path_metrics"]["coverage"] == 0.0
    assert "cumulative reference path" in d1["path_metrics"]["identity"]


def test_nan_close_never_becomes_a_labeled_sample(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)

    result = label_trade_date("2026-09-16", {"600001": float("nan")}, sf, price_basis_by_key=_basis("2026-09-16", "600001"))
    assert result["labeled"] == 0
    with sf() as db:
        outcome = db.execute(
            select(OpportunityOutcomeLabel)
            .join(OpportunityDecisionSnapshot,
                  OpportunityDecisionSnapshot.snapshot_id == OpportunityOutcomeLabel.snapshot_id)
            .where(OpportunityDecisionSnapshot.stage == "rank",
                   OpportunityDecisionSnapshot.symbol == "600001")
        ).scalar_one()
    assert outcome.state == "pending"
    assert outcome.return_pct is None and outcome.net_return_pct is None

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["sample"]["count"] == 0
    assert card["fillable"] == 0


def test_d0_scorecard_names_cost_value_as_nonrealizable_proxy(tmp_path):
    sf = _factory(tmp_path)
    _seed_scorecard_label(
        sf, snapshot_id="d0", run_id="run-d0", symbol="600001",
        as_of=datetime(2026, 9, 16, 10, 5),
    )
    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    identity = card["metric_identity"]
    assert identity["horizon"] == OUTCOME_HORIZON
    assert identity["realizable_return"] is False
    assert "T+1" in identity["note"]
    assert "cost_adjusted_d0_proxy_pct" in card["expectancy"]


def test_scorecard_excludes_legacy_unversioned_price_basis_from_current_metrics(tmp_path):
    sf = _factory(tmp_path)
    _seed_scorecard_label(
        sf, snapshot_id="legacy-basis", run_id="run-legacy", symbol="600001",
        as_of=datetime(2026, 9, 16, 10, 5), price_basis_version="",
    )
    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["audit"]["price_basis_versions"] == {"legacy_unversioned": 1}
    assert card["audit"]["price_basis_excluded_labeled_rows"] == 1
    assert card["sample"]["count"] == 0
    assert card["funnel_denominator"]["state"] == "incomplete"
    assert card["funnel_denominator"]["label_coverage"] == 0.0
    assert card["verdict"] == "incomplete_denominator"


def test_current_revision_overlays_metrics_without_mutating_legacy_base(tmp_path):
    sf = _factory(tmp_path)
    _seed_scorecard_label(
        sf, snapshot_id="legacy-revisable", run_id="run-legacy-revisable",
        symbol="600001", as_of=datetime(2026, 9, 16, 10, 5),
        gross=-7.0, proxy=-7.1, price_basis_version="",
        reason="legacy unversioned result",
    )

    with sf() as db:
        base = db.execute(select(OpportunityOutcomeLabel)).scalar_one()
        base.path_state = "labeled"
        base.path_version = PATH_VERSION
        base.mfe_pct = 5.0
        base.mae_pct = -2.0
        base.limit_state = "not_hit"
        db.commit()

    first = backfill_outcome_revisions(
        "2026-09-16", {"600001": 10.5},
        _basis("2026-09-16", "600001", factor=0.9), sf,
    )
    assert first["inserted"] == 1 and first["labeled"] == 1 and first["pending"] == 0
    second = backfill_outcome_revisions(
        "2026-09-16", {"600001": 99.0},
        _basis("2026-09-16", "600001", factor=0.9), sf,
    )
    assert second["inserted"] == 0

    with sf() as db:
        base = db.execute(select(OpportunityOutcomeLabel)).scalar_one()
        revision = db.execute(select(OpportunityOutcomeRevision)).scalar_one()
    assert base.price_basis_version == ""
    assert base.return_pct == -7.0 and base.net_return_pct == -7.1
    assert base.reason == "legacy unversioned result"
    assert revision.base_outcome_id == base.id
    assert revision.revision_version == OUTCOME_REVISION_VERSION
    assert revision.price_basis_version == PRICE_BASIS_VERSION
    assert revision.basis_reference_price == pytest.approx(9.0)
    assert revision.return_pct == pytest.approx(16.67)

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["sample"]["count"] == 1
    assert card["audit"]["price_basis_versions"] == {PRICE_BASIS_VERSION: 1}
    assert card["audit"]["base_price_basis_versions"] == {"legacy_unversioned": 1}
    assert card["audit"]["outcome_revisions_applied"] == 1
    assert card["audit"]["price_basis_excluded_labeled_rows"] == 0
    assert card["expectancy"]["gross_pct"] == pytest.approx(16.67)
    assert card["path_metrics"]["evaluable"] == 1
    assert card["path_metrics"]["avg_mfe_pct"] == 5.0
    assert card["path_metrics"]["avg_mae_pct"] == -2.0

    summary = learning_summary("2026-09-16", sf)
    assert summary["price_basis"]["revisions_applied"] == 1
    assert summary["price_basis"]["versions"] == {PRICE_BASIS_VERSION: 1}
    assert summary["price_basis"]["base_versions"] == {"legacy_unversioned": 1}
    assert summary["funnel_denominator"]["current_basis_labeled_opportunities"] == 1


def test_current_revision_uses_structured_cost_version_not_reason_text(tmp_path):
    sf = _factory(tmp_path)
    _seed_scorecard_label(
        sf, snapshot_id="legacy-structured-cost", run_id="run-structured-cost",
        symbol="600001", as_of=datetime(2026, 9, 16, 10, 5),
        price_basis_version="", reason="legacy base",
    )
    with sf() as db:
        base = db.execute(select(OpportunityOutcomeLabel)).scalar_one()
        db.add(OpportunityOutcomeRevision(
            base_outcome_id=base.id, snapshot_id=base.snapshot_id,
            horizon=base.horizon, target_date=base.target_date,
            revision_version=OUTCOME_REVISION_VERSION,
            state="labeled", label="positive", reference_price=10.0,
            outcome_price=10.5, return_pct=5.0, fill_state="ok",
            cost_pct=0.1, net_return_pct=4.9,
            reason="human-readable explanation without embedded token",
            source="qfq_close", basis_reference_price=10.0,
            reference_adjustment_factor=1.0,
            price_basis_version=PRICE_BASIS_VERSION,
            price_basis_source="qfq:test|raw:test",
            cost_model_version=COST_MODEL_VERSION,
        ))
        db.commit()

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["sample"]["count"] == 1
    assert card["fillable"] == 1
    assert card["expectancy"]["cost_adjusted_d0_proxy_pct"] == 4.9
    assert card["audit"]["cost_version_excluded_samples"] == 0


def test_noncurrent_revision_never_overlays_current_scorecard(tmp_path):
    sf = _factory(tmp_path)
    _seed_scorecard_label(
        sf, snapshot_id="legacy-stale-revision", run_id="run-stale-revision",
        symbol="600001", as_of=datetime(2026, 9, 16, 10, 5),
        gross=-7.0, proxy=-7.1, price_basis_version="",
        reason="legacy unversioned result",
    )
    with sf() as db:
        base = db.execute(select(OpportunityOutcomeLabel)).scalar_one()
        db.add(OpportunityOutcomeRevision(
            base_outcome_id=base.id, snapshot_id=base.snapshot_id,
            horizon=base.horizon, target_date=base.target_date,
            revision_version="close-v0.old-basis.old-cost",
            state="labeled", label="positive", reference_price=10.0,
            outcome_price=11.0, return_pct=10.0, fill_state="ok",
            net_return_pct=9.9, price_basis_version=PRICE_BASIS_VERSION,
            price_basis_source="test", cost_model_version=COST_MODEL_VERSION,
            reason="stale revision",
        ))
        db.commit()

    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["sample"]["count"] == 0
    assert card["audit"]["outcome_revisions_applied"] == 0
    assert card["audit"]["price_basis_versions"] == {"legacy_unversioned": 1}
    assert card["verdict"] == "incomplete_denominator"


def test_revision_backfill_default_scope_never_relabels_cross_day_horizon(tmp_path):
    sf = _factory(tmp_path)
    _seed_scorecard_label(
        sf, snapshot_id="legacy-d1-revision", run_id="run-legacy-d1-revision",
        symbol="600001", as_of=datetime(2026, 9, 16, 10, 5),
        horizon="d1_close", price_basis_version="",
    )
    got = backfill_outcome_revisions(
        "2026-09-16", {"600001": 99.0},
        _basis("2026-09-16", "600001"), sf,
    )
    assert got["inserted"] == 0
    with sf() as db:
        assert db.execute(select(OpportunityOutcomeRevision)).scalars().all() == []


def test_revision_backfill_missing_basis_is_retryable_without_pending_row(tmp_path):
    sf = _factory(tmp_path)
    _seed_scorecard_label(
        sf, snapshot_id="legacy-no-basis", run_id="run-legacy-no-basis",
        symbol="600001", as_of=datetime(2026, 9, 16, 10, 5),
        price_basis_version="",
    )
    got = backfill_outcome_revisions(
        "2026-09-16", {"600001": 10.5}, {}, sf,
    )
    assert got["inserted"] == 0 and got["pending"] == 1
    with sf() as db:
        assert db.execute(select(OpportunityOutcomeRevision)).scalars().all() == []

    retry = backfill_outcome_revisions(
        "2026-09-16", {"600001": 10.5},
        _basis("2026-09-16", "600001"), sf,
    )
    assert retry["inserted"] == 1 and retry["labeled"] == 1


def test_scorecard_excludes_unproven_cost_model_rows_from_proxy(tmp_path):
    sf = _factory(tmp_path)
    _seed_scorecard_label(
        sf, snapshot_id="old-cost", run_id="run-old", symbol="600001",
        as_of=datetime(2026, 9, 16, 10, 5), reason="legacy cost proxy without version",
    )
    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["audit"]["cost_version_excluded_samples"] == 1
    assert card["sample"]["count"] == 1
    assert card["fillable"] == 0
    assert card["expectancy"]["cost_adjusted_d0_proxy_pct"] is None


def test_scorecard_stage_diagnostics_dedupe_within_stage_not_globally(tmp_path):
    sf = _factory(tmp_path)
    when = datetime(2026, 9, 16, 10, 5)
    for n, stage in enumerate(("rank", "notification")):
        for dup in range(2):
            _seed_scorecard_label(
                sf, snapshot_id=f"{stage}-{dup}", run_id=f"run-{stage}-{dup}",
                symbol="600001", as_of=when, stage=stage, proxy=1.0 + n,
            )
    card = opportunity_scorecard("2026-09-16", session_factory=sf)
    assert card["sample"]["count"] == 1
    assert card["by_stage"]["rank"]["labels"] == 1
    assert card["by_stage"]["notification"]["labels"] == 1
    assert card["audit"]["repeated_labeled_rows"] == 3


def test_replay_v2_distinguishes_ever_sealed_from_current_sealed():
    from app.picks.opportunity_learning import replay_decision

    opened = {
        "quote_present": True, "board_tradable": True,
        "change_pct": 6.8, "linkage_min_pct": 1.0,
        "amount": 2e8, "amount_min": 3e7, "seal_line": 9.7,
        "ever_sealed": True, "current_sealed": False,
        "snapshot_state": "ready", "version": "2026-09-21T10:05:00+08:00",
    }
    assert replay_decision("candidate", {"facts": opened}) == "included"
    assert replay_decision(
        "hard_gate", {"facts": opened, "tradability_level": "可参与"}
    ) == "passed"

    resealed = {**opened, "current_sealed": True}
    assert replay_decision("candidate", {"facts": resealed}) == "rejected"
    unknown = {**opened, "current_sealed": None, "snapshot_state": "stale"}
    assert replay_decision("candidate", {"facts": unknown}) == "unknown"


def test_replay_keeps_legacy_v1_sealed_pool_semantics():
    from app.picks.opportunity_learning import replay_decision

    legacy = {
        "quote_present": True, "board_tradable": True, "change_pct": 6.8,
        "sealed_pool": True,
    }
    assert replay_decision("candidate", {"facts": legacy}) == "rejected"
    assert replay_decision(
        "hard_gate", {"facts": legacy, "tradability_level": "不可参与"}
    ) == "rejected"


def test_intraday_archive_carries_seal_state_version_facts():
    payload = _payload()
    payload["themes"][0]["participants"] = [payload["themes"][0]["participants"][0]]
    facts = {
        "quote_present": True, "board_tradable": True,
        "change_pct": 6.8, "linkage_min_pct": 1.0,
        "amount": 2e8, "amount_min": 3e7, "seal_line": 9.7,
        "ever_sealed": True, "current_sealed": False,
        "snapshot_state": "ready", "version": "2026-09-21T10:05:00+08:00",
    }
    payload["themes"][0]["participants"][0]["seal_state"] = {
        "ever_sealed": True, "current_sealed": False,
        "snapshot_state": "ready", "version": facts["version"],
    }
    payload["themes"][0]["_candidate_audit"] = [{
        "symbol": "600001", "name": "甲", "candidate_decision": "included",
        "hard_gate_decision": "passed", "reason": "曾封板后当前开板重评",
        "price": 10.0, "change_pct": 6.8, "amount": 2e8,
        "board": "沪市主板", "facts": facts,
    }]
    _run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-21", as_of=datetime(2026, 9, 21, 10, 5)
    )
    candidate = next(r for r in rows if r["symbol"] == "600001" and r["stage"] == "candidate")
    hard_gate = next(r for r in rows if r["symbol"] == "600001" and r["stage"] == "hard_gate")
    assert candidate["evidence"]["facts"] == facts
    assert hard_gate["evidence"]["facts"] == facts
    rank = next(r for r in rows if r["symbol"] == "600001" and r["stage"] == "rank")
    assert rank["evidence"]["seal_state"]["version"] == facts["version"]


def test_notification_contract_separates_reference_action_and_dispatch_version():
    item = {
        "symbol": "600001", "name": "甲", "price": 10.0,
        "confidence": {"tier": "executable"}, "vetoes": [],
        "buy_range": {"low": 10.2, "high": 10.8},
    }
    hit = {"item": item, "price": 10.5, "chg": 5.0}
    snap = {
        "state": "ready", "price": 10.5, "change_pct": 5.0,
        "prev_close": 10.0, "source": "sina_market",
        "received_at": "2026-09-21T02:30:00+00:00",
    }
    kw = dict(
        items=[item], trade_date="2026-09-21",
        as_of=datetime(2026, 9, 21, 10, 30), hits=[hit], skips=[],
        pick_generated_at="2026-09-21T09:26:00+08:00",
        execution_by_symbol={"600001": snap},
    )
    _, eligible = build_notification_records(dispatch_by_symbol={}, **kw)
    _, notified = build_notification_records(dispatch_by_symbol={"600001": "notified"}, **kw)
    c1 = eligible[0]["evidence"]["execution_contract"]
    c2 = notified[0]["evidence"]["execution_contract"]
    assert c1["reference_entry"]["price"] == 10.0
    assert c1["executable_snapshot"]["price"] == 10.5
    assert c1["reference_entry"]["semantics"] == "reference_only_not_fill"
    assert c1["executable_snapshot"]["semantics"] == "action_time_quote_not_fill"
    assert c1["decision_id"] == c2["decision_id"]
    assert c1["decision_version"] == c2["decision_version"]

    refreshed_only = {
        **snap,
        "received_at": "2026-09-21T02:31:00+00:00",
        "as_of": "2026-09-21T10:31:00+08:00",
        "ticktime": "10:31:00",
    }
    _, refreshed = build_notification_records(
        dispatch_by_symbol={}, execution_by_symbol={"600001": refreshed_only},
        **{k: v for k, v in kw.items() if k != "execution_by_symbol"},
    )
    assert refreshed[0]["evidence"]["execution_contract"]["decision_version"] == c1["decision_version"], (
        "只有采样时间刷新、物质事实未变时不得制造新 decision_version"
    )

    newer = {**snap, "price": 10.6, "received_at": "2026-09-21T02:31:00+00:00"}
    _, changed = build_notification_records(
        dispatch_by_symbol={}, execution_by_symbol={"600001": newer},
        **{k: v for k, v in kw.items() if k != "execution_by_symbol"},
    )
    c3 = changed[0]["evidence"]["execution_contract"]
    assert c3["decision_id"] == c1["decision_id"]
    assert c3["decision_version"] != c1["decision_version"]


def test_eligible_notification_decision_creates_outcome_label(tmp_path):
    """IMP-006：dispatch 与交易 decision 分离后，eligible 仍必须进入结果标签链。"""
    sf = _factory(tmp_path)
    item = {
        "symbol": "600001", "name": "甲", "price": 10.0,
        "confidence": {"tier": "executable"}, "vetoes": [],
        "buy_range": {"low": 9.8, "high": 10.8},
    }
    hit = {"item": item, "price": 10.5, "chg": 5.0}
    run_id, rows = build_notification_records(
        [item], trade_date="2026-09-21", as_of=datetime(2026, 9, 21, 10, 30),
        hits=[hit], skips=[], dispatch_by_symbol={},
        pick_generated_at="2026-09-21T09:26:00+08:00",
        execution_by_symbol={"600001": {"state": "ready", "price": 10.5}},
    )
    assert rows[0]["decision"] == "eligible"
    archive_records(run_id, rows, sf)
    with sf() as db:
        outcomes = db.execute(select(OpportunityOutcomeLabel)).scalars().all()
    assert len(outcomes) == 1
    assert outcomes[0].reference_price == 10.5


def test_notification_archive_keeps_late_older_snapshot_but_latest_read_stays_newer(tmp_path):
    """晚到旧拍必须保留作回放；当前视图只在读侧按 as_of 选择较新版本。"""
    from app.picks.opportunity_learning import latest_notification_execution

    sf = _factory(tmp_path)
    item = {"symbol": "600001", "name": "甲", "price": 10.0}
    hit = {"item": item, "price": 10.5, "chg": 5.0}

    def rows(at: datetime, price: float):
        run_id, records = build_notification_records(
            [item], trade_date="2026-09-21", as_of=at,
            hits=[{**hit, "price": price}], skips=[], dispatch_by_symbol={},
            pick_generated_at="2026-09-21T09:26:00+08:00",
            execution_by_symbol={"600001": {"state": "ready", "price": price}},
        )
        return run_id, records

    new_run, new_rows = rows(datetime(2026, 9, 21, 10, 31), 10.6)
    old_run, old_rows = rows(datetime(2026, 9, 21, 10, 30), 10.5)
    assert archive_records(new_run, new_rows, sf)["inserted"] == 1
    assert archive_records(old_run, old_rows, sf)["inserted"] == 1

    with sf() as db:
        stored = db.execute(
            select(OpportunityDecisionSnapshot)
            .where(OpportunityDecisionSnapshot.trade_date == "2026-09-21")
        ).scalars().all()
    assert len(stored) == 2, "append-only 历史不能因晚到而删除旧拍"
    latest = latest_notification_execution("2026-09-21", sf)["600001"]
    assert latest["executable_snapshot"]["price"] == 10.6
    assert latest["archived_as_of"].startswith("2026-09-21T10:31:00")
