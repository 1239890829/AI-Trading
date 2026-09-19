"""RSH-030 human-gold dataset tooling."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from scripts import jev_goldset as gs


def _db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE event_card(
          id INTEGER PRIMARY KEY,
          published_at TEXT,
          title TEXT,
          summary TEXT,
          source TEXT,
          source_tier INTEGER,
          source_symbol TEXT,
          category TEXT,
          certainty TEXT,
          fact_kind TEXT
        );
        CREATE TABLE event_direction(
          id INTEGER PRIMARY KEY,
          event_id INTEGER,
          target_type TEXT,
          target TEXT,
          direction INTEGER
        );
        """
    )
    rows = []
    eid = 1
    for category in gs.CATEGORIES:
        for j in range(5):
            rows.append((
                eid,
                f"2026-09-{10+j:02d} 10:00:00",
                f"{category} title {j}",
                f"{category} summary {j}",
                "synthetic",
                3,
                None,
                category,
                gs.CERTAINTIES[j % len(gs.CERTAINTIES)],
                "fact",
            ))
            eid += 1
    con.executemany(
        "INSERT INTO event_card VALUES(?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.executemany(
        "INSERT INTO event_direction VALUES(?,?,?,?,?)",
        [
            (1, 1, "theme", "A", 1),
            (2, 2, "theme", "B", 0),
        ],
    )
    con.commit()
    con.close()


def test_export_keeps_rule_reference_separate_from_human_labels(tmp_path):
    db = tmp_path / "x.db"
    _db(db)
    rows = gs._load_events(db)
    picked = gs.stratified_sample(rows, 18, "seed")
    assert len(picked) == 18
    assert set(r["reference_rule"]["category"] for r in picked) == set(gs.CATEGORIES)
    assert all(r["human"] == {
        "category": None,
        "certainty": None,
        "actionable": None,
        "notes": "",
    } for r in picked)
    row1 = next(r for r in rows if r["event_id"] == 1)
    row2 = next(r for r in rows if r["event_id"] == 2)
    assert row1["reference_rule"]["actionable"] is True
    assert row2["reference_rule"]["actionable"] is False


def test_stratified_sample_is_stable_for_seed(tmp_path):
    db = tmp_path / "x.db"
    _db(db)
    rows = gs._load_events(db)
    a = [r["event_id"] for r in gs.stratified_sample(rows, 20, "same")]
    b = [r["event_id"] for r in gs.stratified_sample(rows, 20, "same")]
    c = [r["event_id"] for r in gs.stratified_sample(rows, 20, "different")]
    assert a == b
    assert a != c


def test_validate_requires_human_only_when_requested(tmp_path):
    row = {
        "event_id": 1,
        "human": {"category": None, "certainty": None, "actionable": None, "notes": ""},
    }
    assert gs.validate_rows([row])["ok"] is True
    strict = gs.validate_rows([row], require_human=True)
    assert strict["ok"] is False
    assert strict["human_complete"] == 0


def test_score_uses_human_truth_not_reference_rule():
    labeled = [
        {
            "event_id": 1,
            "reference_rule": {"category": "other", "certainty": "done", "actionable": False},
            "human": {"category": "policy", "certainty": "proposed", "actionable": True},
        },
        {
            "event_id": 2,
            "reference_rule": {"category": "policy", "certainty": "done", "actionable": True},
            "human": {"category": "data", "certainty": "done", "actionable": False},
        },
    ]
    predictions = [
        {"event_id": 1, "category": "policy", "certainty": "proposed", "actionable": True},
        {"event_id": 2, "category": "policy", "certainty": "done", "actionable": False},
    ]
    out = gs.score(labeled, predictions)
    assert out["metrics"]["category"]["accuracy"] == 0.5
    assert out["metrics"]["certainty"]["accuracy"] == 1.0
    assert out["metrics"]["actionable"]["accuracy"] == 1.0


def test_cli_export_and_validate(tmp_path, capsys):
    db = tmp_path / "x.db"
    out = tmp_path / "gold.jsonl"
    _db(db)
    assert gs.main(["export-events", "--db", str(db), "--out", str(out), "--count", "12"]) == 0
    first = json.loads(out.read_text().splitlines()[0])
    assert first["human"]["category"] is None
    capsys.readouterr()
    assert gs.main(["validate", str(out)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["rows"] == 12 and result["human_complete"] == 0
