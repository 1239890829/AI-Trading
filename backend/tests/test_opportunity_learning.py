"""RSH-026: point-in-time funnel evidence, replay, and outcome labels."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.opportunity_learning import OpportunityDecisionSnapshot, OpportunityOutcomeLabel
from app.models.watchlist import Base
from app.picks.opportunity_learning import (
    archive_records,
    build_intraday_records,
    build_notification_records,
    label_trade_date,
    learning_summary,
    pending_symbols,
    replay_run,
)


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'opportunity-learning.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _payload():
    return {
        "trade_date": "2026-09-16",
        "hot_available": True,
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


def test_archive_is_append_only_idempotent_and_offline_replay_matches(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    first = archive_records(run_id, rows, sf)
    second = archive_records(run_id, rows, sf)

    assert first["inserted"] == 6
    assert second["inserted"] == 0
    replay = replay_run(run_id, sf)
    assert replay["records"] == 6 and replay["mismatches"] == 0
    assert all(item["matches"] for item in replay["items"])
    with sf() as db:
        evidence_before = db.execute(
            select(OpportunityDecisionSnapshot.snapshot_id, OpportunityDecisionSnapshot.evidence)
            .order_by(OpportunityDecisionSnapshot.id)
        ).all()
    label_trade_date("2026-09-16", {"600001": 10.5}, sf)
    with sf() as db:
        evidence_after = db.execute(
            select(OpportunityDecisionSnapshot.snapshot_id, OpportunityDecisionSnapshot.evidence)
            .order_by(OpportunityDecisionSnapshot.id)
        ).all()
    assert evidence_after == evidence_before, "结果标签必须写独立表，不能改写当时证据"


def test_outcome_label_is_retryable_and_coverage_is_mechanical(tmp_path):
    sf = _factory(tmp_path)
    run_id, rows = build_intraday_records(
        _payload(), trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)

    assert pending_symbols("2026-09-16", sf) == {"600001"}
    missing = label_trade_date("2026-09-16", {}, sf)
    assert missing == {"trade_date": "2026-09-16", "labeled": 0, "unknown": 0, "pending": 1}
    done = label_trade_date("2026-09-16", {"600001": 10.5}, sf)
    assert done["labeled"] == 1 and done["pending"] == 0
    # 幂等：已标结果不重算，不允许未来一次价格覆盖原始 D0 标签。
    assert label_trade_date("2026-09-16", {"600001": 99.0}, sf)["labeled"] == 0
    with sf() as db:
        label = db.execute(select(OpportunityOutcomeLabel)).scalar_one()
    assert label.state == "labeled" and label.label == "positive"
    assert label.return_pct == 5.0 and label.outcome_price == 10.5
    summary = learning_summary("2026-09-16", sf)
    assert summary["label_coverage"] == 1.0
    assert summary["stages"] == {"candidate": 2, "hard_gate": 2, "rank": 2, "notification": 0}


def test_missing_entry_price_becomes_unknown_not_zero_return(tmp_path):
    sf = _factory(tmp_path)
    payload = _payload()
    payload["themes"][0]["participants"][0]["price"] = None
    run_id, rows = build_intraday_records(
        payload, trade_date="2026-09-16", as_of=datetime(2026, 9, 16, 10, 5)
    )
    archive_records(run_id, rows, sf)
    result = label_trade_date("2026-09-16", {"600001": 10.5}, sf)
    assert result["unknown"] == 1
    with sf() as db:
        label = db.execute(select(OpportunityOutcomeLabel)).scalar_one()
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
