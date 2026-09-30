"""Point-in-time revision packets never infer a missing historical reason."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "rationale_audit", Path(__file__).parents[1] / "scripts" / "jev_rationale_audit.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def _db(tmp_path):
    path = tmp_path / "evidence.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE event_interpretation(id INTEGER, event_id INTEGER, observation_id INTEGER,
                effective_at TEXT, state TEXT);
            CREATE TABLE event_observation(id INTEGER, event_id INTEGER, title TEXT, summary TEXT,
                content_hash TEXT, available_at TEXT, source TEXT);
            CREATE TABLE opportunity_decision_snapshot(id INTEGER, snapshot_id TEXT, as_of TEXT, evidence TEXT);
            INSERT INTO event_interpretation VALUES(1,7,11,'2026-09-29 09:00:00','active');
            INSERT INTO event_interpretation VALUES(2,7,12,'2026-09-29 11:00:00','withdrawn');
            INSERT INTO event_observation VALUES(11,7,'签约','合同生效','h1','2026-09-29 09:00:00','公开公告');
            INSERT INTO event_observation VALUES(12,7,'撤回','合同已取消','h2','2026-09-29 11:00:00','公开公告');
        """)
        db.execute("INSERT INTO opportunity_decision_snapshot VALUES(1,'snap','2026-09-29 10:00:00',?)", (
            json.dumps({"event_refs": [{"event_id": 7, "version_id": 1, "observation_id": 11}],
                        "event_rationale": {"basis": "签约带来订单需求"}}),))
    return path


def test_freezes_real_reason_and_never_writes_source_or_gold(tmp_path):
    path = _db(tmp_path)
    before = path.read_bytes()
    out = audit.freeze(path, as_of="2026-09-29T12:00:00+08:00")
    assert out["ready_pairs"] == 1
    packet = out["packets"][0]
    assert packet["rationale"] == "签约带来订单需求"
    assert packet["before"]["id"] == 1 and packet["after"]["id"] == 2
    assert packet["human"]["reason_changed"] is None and packet["recommendation"] is None
    assert packet["adopted"] is False and before == path.read_bytes()
    early = audit.freeze(path, as_of="2026-09-29T02:30:00+00:00")
    assert early["ready_pairs"] == 0  # new version is not visible yet


@pytest.mark.parametrize("damage", ["missing_reason", "wrong_version", "wrong_observation", "future_source"])
def test_rejects_missing_or_mismatched_historical_binding(tmp_path, damage):
    path = _db(tmp_path)
    with sqlite3.connect(path) as db:
        evidence = json.loads(db.execute("SELECT evidence FROM opportunity_decision_snapshot").fetchone()[0])
        if damage == "missing_reason":
            evidence.pop("event_rationale")
        elif damage == "wrong_version":
            evidence["event_refs"][0]["version_id"] = 2
        elif damage == "wrong_observation":
            evidence["event_refs"][0]["observation_id"] = 12
        else:
            db.execute("UPDATE event_observation SET available_at='2026-09-29 13:00:00' WHERE id=12")
        db.execute("UPDATE opportunity_decision_snapshot SET evidence=?", (json.dumps(evidence),))
    out = audit.freeze(path, as_of="2026-09-29T12:00:00+08:00")
    assert out["ready_pairs"] == 0 and len(out["rejected"]) == 1
