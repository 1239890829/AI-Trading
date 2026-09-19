"""RSH-030 human-gold dataset tooling."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

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



def _fake_evaluate(state, questions, *, purpose, model):
    answers = {}
    for qid in questions:
        idx = int(qid.rsplit("_", 1)[1])
        if qid.startswith("category_"):
            answers[qid] = {
                "type": "choice",
                "choice": "policy",
                "confidence": 0.9,
                "probabilities": {
                    "policy": 0.9,
                    "statement": 0.02,
                    "data": 0.02,
                    "rumor": 0.02,
                    "corporate": 0.02,
                    "other": 0.02,
                },
            }
        elif qid.startswith("certainty_"):
            answers[qid] = {
                "type": "choice",
                "choice": "done",
                "confidence": 0.8,
                "probabilities": {"done": 0.8, "proposed": 0.1, "rumor": 0.1},
            }
        else:
            answers[qid] = {
                "type": "noul",
                "noul": 0.8 if idx % 2 == 0 else 0.2,
            }
    return {
        "ok": True,
        "model": model,
        "answers": answers,
        "usage": {"input_tokens": 321, "output_tokens": 44},
        "latency_ms": 10,
    }


def test_predict_jev_batches_without_mutating_human_labels(tmp_path):
    db = tmp_path / "x.db"
    _db(db)
    rows = gs._load_events(db)[:2]
    before = json.loads(json.dumps(rows))
    predictions, meta = gs.predict_jev(
        rows,
        model="jev-test",
        batch_size=2,
        evaluate_fn=_fake_evaluate,
    )
    assert rows == before
    assert [p["event_id"] for p in predictions] == [1, 2]
    assert predictions[0]["category"] == "policy"
    assert predictions[0]["actionable"] is True
    assert predictions[1]["actionable"] is False
    assert predictions[0]["actionable_noul"] == 0.8
    assert meta["calls"] == 1
    assert meta["input_tokens"] == 321
    assert meta["models_returned"] == {"jev-test": 1}


def test_predict_jev_rejects_invalid_choice_probabilities(tmp_path):
    db = tmp_path / "x.db"
    _db(db)
    rows = gs._load_events(db)[:1]

    def bad(state, questions, *, purpose, model):
        out = _fake_evaluate(state, questions, purpose=purpose, model=model)
        out["answers"]["category_0"]["probabilities"]["other"] = 0.5
        return out

    with pytest.raises(ValueError, match="rounding tolerance"):
        gs.predict_jev(rows, evaluate_fn=bad)


def test_prioritize_review_puts_disagreement_and_uncertainty_first():
    queue = [
        {
            "event_id": 1,
            "title": "A",
            "summary": "",
            "source": "x",
            "reference_rule": {
                "category": "data",
                "certainty": "done",
                "actionable": False,
            },
        },
        {
            "event_id": 2,
            "title": "B",
            "summary": "",
            "source": "x",
            "reference_rule": {
                "category": "policy",
                "certainty": "done",
                "actionable": True,
            },
        },
    ]
    predictions = [
        {
            "event_id": 1,
            "category": "rumor",
            "category_confidence": 0.55,
            "certainty": "proposed",
            "certainty_confidence": 0.6,
            "actionable": True,
            "actionable_noul": 0.52,
        },
        {
            "event_id": 2,
            "category": "policy",
            "category_confidence": 0.98,
            "certainty": "done",
            "certainty_confidence": 0.97,
            "actionable": True,
            "actionable_noul": 0.98,
        },
    ]
    ranked = gs.prioritize_review(queue, predictions)
    assert [row["event_id"] for row in ranked] == [1, 2]
    assert set(ranked[0]["reasons"]) >= {
        "category_disagreement",
        "certainty_disagreement",
        "actionable_disagreement",
        "category_low_confidence",
        "certainty_low_confidence",
        "actionable_uncertain",
    }
    assert ranked[0]["priority_score"] > ranked[1]["priority_score"]


def test_predict_cli_does_not_leave_partial_outputs_on_failure(tmp_path, monkeypatch):
    queue = tmp_path / "queue.jsonl"
    out = tmp_path / "predictions.jsonl"
    meta = tmp_path / "meta.json"
    gs.write_jsonl(queue, [{
        "event_id": 1,
        "reference_rule": {"category": "data", "certainty": "done", "actionable": False},
        "human": {"category": None, "certainty": None, "actionable": None, "notes": ""},
    }])

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic prelabel failure")

    monkeypatch.setattr(gs, "predict_jev", fail)
    with pytest.raises(RuntimeError, match="synthetic prelabel failure"):
        gs.main([
            "predict-jev", str(queue),
            "--out", str(out),
            "--meta", str(meta),
        ])
    assert not out.exists()
    assert not meta.exists()



def test_parse_choice_allows_bounded_two_decimal_rounding_drift():
    answer = {
        "choice": "policy",
        "confidence": 0.9,
        # Sum = 0.99. This is compatible with independent two-decimal rounding
        # across six labels and must not make a real Jev batch unusable.
        "probabilities": {
            "policy": 0.80,
            "statement": 0.05,
            "data": 0.04,
            "rumor": 0.03,
            "corporate": 0.04,
            "other": 0.03,
        },
    }
    choice, confidence, probs = gs._parse_choice(answer, gs.CATEGORY_CRITERIA)
    assert choice == "policy"
    assert confidence == 0.9
    assert sum(probs.values()) == pytest.approx(0.99)



def test_compare_reference_is_explicitly_not_accuracy():
    queue = [
        {
            "event_id": 1,
            "reference_rule": {"category": "data", "certainty": "done", "actionable": False},
        },
        {
            "event_id": 2,
            "reference_rule": {"category": "policy", "certainty": "proposed", "actionable": True},
        },
    ]
    predictions = [
        {
            "event_id": 1,
            "category": "policy",
            "category_confidence": 0.60,
            "certainty": "done",
            "certainty_confidence": 0.90,
            "actionable": False,
            "actionable_noul": 0.20,
        },
        {
            "event_id": 2,
            "category": "policy",
            "category_confidence": 0.95,
            "certainty": "done",
            "certainty_confidence": 0.55,
            "actionable": True,
            "actionable_noul": 0.80,
        },
    ]
    out = gs.compare_reference(queue, predictions)
    assert "not human ground truth" in out["warning"]
    assert out["agreement_not_accuracy"]["category"]["agreement_count"] == 1
    assert out["agreement_not_accuracy"]["certainty"]["agreement_count"] == 1
    assert out["agreement_not_accuracy"]["actionable"]["agreement_count"] == 2
    assert out["category_confidence"]["lt_0_75"] == 1
    assert out["certainty_confidence"]["lt_0_60"] == 1
    assert out["actionable_noul"]["le_0_20"] == 1
    assert out["actionable_noul"]["ge_0_80"] == 1



def test_validate_prediction_meta_binds_exact_queue(tmp_path):
    queue = tmp_path / "queue.jsonl"
    preds = tmp_path / "preds.jsonl"
    meta = tmp_path / "meta.json"
    gs.write_jsonl(queue, [{
        "event_id": 1,
        "reference_rule": {"category": "data", "certainty": "done", "actionable": False},
        "human": {"category": None, "certainty": None, "actionable": None, "notes": ""},
    }])
    gs.write_jsonl(preds, [{"event_id": 1, "category": "data"}])
    gs.atomic_write_json(meta, {
        "queue_sha256": gs.file_sha256(queue),
        "prediction_rows": 1,
        "model_requested": "jev-test",
        "models_returned": {"jev-test": 1},
    })
    out = gs.validate_prediction_meta(queue, preds, meta)
    assert out["queue_sha256"] == gs.file_sha256(queue)
    assert out["prediction_sha256"] == gs.file_sha256(preds)

    queue.write_text(queue.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="queue hash mismatch"):
        gs.validate_prediction_meta(queue, preds, meta)


def test_score_cli_requires_complete_human_by_default(tmp_path, capsys):
    labeled = tmp_path / "labeled.jsonl"
    preds = tmp_path / "preds.jsonl"
    gs.write_jsonl(labeled, [{
        "event_id": 1,
        "human": {"category": None, "certainty": None, "actionable": None, "notes": ""},
    }])
    gs.write_jsonl(preds, [{
        "event_id": 1,
        "category": "policy",
        "certainty": "done",
        "actionable": True,
    }])

    with pytest.raises(ValueError, match="human labels incomplete"):
        gs.main(["score", str(labeled), str(preds)])

    assert gs.main(["score", str(labeled), str(preds), "--allow-partial"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["human_complete"] == 0
    assert out["human_total"] == 1
    assert out["partial"] is True
    assert out["metrics"]["category"]["n"] == 0


def test_score_rejects_duplicate_prediction_ids():
    labeled = [{
        "event_id": 1,
        "human": {"category": "data", "certainty": "done", "actionable": False},
    }]
    preds = [
        {"event_id": 1, "category": "data", "certainty": "done", "actionable": False},
        {"event_id": 1, "category": "policy", "certainty": "done", "actionable": True},
    ]
    with pytest.raises(ValueError, match="unique integers"):
        gs.score(labeled, preds)
