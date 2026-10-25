"""List I/O stays small; optional derived summaries never replace full evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from app.services import _tradingagents_summary as summary
from app.services import tradingagents_research as research
from scripts.tradingagents_saved_summary import main


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "ROOT", tmp_path / "research")
    monkeypatch.setattr(research, "get_session_factory", lambda: pytest.fail("Summary derivation must not open a DB"))
    monkeypatch.setattr(research, "_read_task_state", lambda *a, **kw: {"task": None})
    symbol, run_id = "600001", "a" * 32
    folder = research.ROOT / symbol / run_id
    folder.mkdir(parents=True)
    packet = {"case_id": "isolated-original"}
    (folder / "packet.json").write_text(json.dumps(packet))
    result = {"version": 1, "state": "completed", "symbol": symbol, "run_id": run_id,
              "generated_at": "2000-01-02T16:00:00+08:00", "as_of": "2000-01-02T15:00:00+08:00",
              "upstream_sha": research.UPSTREAM_SHA, "input_sha256": research._digest(packet),
              "method_sha256": "b" * 64, "verification": research.VERIFICATION, "production_eligible": False,
              "reports": {key: "original saved report " + key for key in research.REPORTS},
              "review_state": "unreviewed", "review_summary": {"finite_failures": 0, "peer_review_required": 0,
                  "source_verified": False, "independent_human_gold": False}}
    (folder / "result.json").write_text(json.dumps(result))
    (folder.parent / "latest.json").write_text(json.dumps({"run_id": run_id,
        "result_sha256": hashlib.sha256((folder / "result.json").read_bytes()).hexdigest()}))
    (folder / "model-outputs").mkdir()
    rows = []
    for number, role in enumerate(research.ROLES, 1):
        field = research.ROLE_FIELDS.get(role, ("content",))[0]
        output = {field: "original role answer", "tool_calls": []}
        if role == "Portfolio Manager":
            output["rating"] = "hold"
        digest = research._digest(output)
        peer = {"role": role, "call_id": number, "output": output, "output_sha256": digest,
                "accepted": True, "state": "accepted", "case_id": packet["case_id"], "arm": "ta-luna"}
        rows.append(json.dumps({"peer": peer}))
        (folder / "model-outputs" / f"{number}.json").write_text(json.dumps({"call_id": number, "role": role,
            "output_sha256": digest, "structured_output": output}))
    (folder / "role-outputs.jsonl").write_text("\n".join(rows))
    return folder


def originals(folder):
    return {str(path.relative_to(folder.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [folder.parent / "latest.json", *folder.rglob("*")] if path.is_file()}


def test_explicit_derivation_preserves_all_originals_and_cli_defaults_to_dry_run(saved, capsys):
    before = originals(saved)
    assert main(["--symbol", "600001"]) == 0
    assert '"state": "dry_run"' in capsys.readouterr().out
    assert not (saved.parent / "derived").exists()
    assert main(["--symbol", "600001", "--write"]) == 0
    output = capsys.readouterr().out
    assert '"state": "written"' in output and "original role answer" not in output and str(saved) not in output
    assert originals(saved) == before
    payload = summary.read("600001")
    assert payload["state"] == "available"
    assert (payload["summary"]["report_count"], payload["summary"]["roles_count"], payload["summary"]["final_rating"]) == (7, 12, "hold")


def test_list_cold_and_warm_reads_open_zero_deep_bodies_while_detail_still_reads_all(saved, monkeypatch):
    summary.materialize("600001", write=True)
    original_open = Path.open
    opened = []
    def tracked(path, *args, **kwargs):
        if path.is_relative_to(saved.parent):
            opened.append(path)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", tracked)
    # Explicit detail keeps the independent complete verification path.
    detail = research.read_view("600001", None, saved_only=True)
    assert len(detail["latest"]["reports"]) == 7 and len(detail["latest"]["roles"]) == 12
    deep_count = sum(path.is_relative_to(saved) for path in opened)
    assert deep_count >= 15
    opened.clear()
    def forbid_deep(path, *args, **kwargs):
        if path.is_relative_to(saved):
            pytest.fail(f"Selected list opened a deep original body: {path.name}")
        return tracked(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", forbid_deep)
    monkeypatch.setattr(research, "_latest", lambda *a: pytest.fail("List cannot verify complete report bodies"))
    monkeypatch.setattr(research, "_role_projection", lambda *a: pytest.fail("List cannot parse original roles"))
    first = research.read_saved_summary("600001")
    second = research.read_saved_summary("600001")
    assert first == second and first["summary_state"] == "available"
    assert first["report_count"] == 7 and first["roles_count"] == 12
    assert set(path.name for path in opened) <= {"latest.json", "latest-summary.json"}
    assert sum(path.is_relative_to(saved) for path in opened) == 0


def test_legacy_record_without_sidecar_is_unknown_not_first_research(saved):
    value = research.read_saved_summary("600001")
    assert value["has_saved"] is True and value["run_id"] == saved.name
    assert value["state"] == "unknown" and value["summary_state"] == "unavailable"
    assert value["report_count"] is None and value["roles_count"] is None
    assert not (saved.parent / "derived").exists()


@pytest.mark.parametrize("fault", ["digest", "symbol", "run_id", "method", "missing", "pointer", "capture", "result"])
def test_mismatched_or_changed_evidence_is_not_reused_as_current_summary(saved, fault):
    summary.materialize("600001", write=True)
    sidecar = saved.parent / "derived/latest-summary.json"
    if fault in {"digest", "symbol", "run_id", "method"}:
        value = json.loads(sidecar.read_text())
        if fault == "digest":
            value["sha256"] = "0" * 64
        else:
            if fault in {"symbol", "run_id"}:
                value["body"][fault] = "600002" if fault == "symbol" else "c" * 32
            else:
                value["body"]["summary"]["method_sha256"] = "invalid"
            value["sha256"] = summary._digest(value["body"])
        sidecar.write_text(json.dumps(value))
    elif fault == "missing":
        sidecar.unlink()
    elif fault == "pointer":
        (saved.parent / "latest.json").write_text(json.dumps({"run_id": "d" * 32, "result_sha256": "e" * 64}))
    elif fault == "capture":
        (saved / "model-outputs/1.json").write_text("{}")
    else:
        (saved / "result.json").write_text("{}")
    value = research.read_saved_summary("600001")
    assert value["state"] == "unknown" and value["summary_state"] == "unavailable"
    assert value["has_saved"] and value["roles_count"] is None and value["final_rating"] is None


def test_live_task_and_last_known_saved_state_survive_summary_damage(saved, monkeypatch):
    summary.materialize("600001", write=True)
    (saved / "model-outputs/1.json").write_text("{}")
    for status in ("queued", "running", "failed", "canceled"):
        monkeypatch.setattr(research, "_read_task_state", lambda *a, **kw: {"task": {"status": status}})
        value = research.read_saved_summary("600001")
        assert value["task_state"] == status and value["state"] == status
        assert value["has_saved"] and value["summary_state"] == "unavailable"


def test_returned_short_summary_cannot_mutate_cached_manifest(saved):
    summary.materialize("600001", write=True)
    first = summary.read("600001")
    expected = deepcopy(first)
    first["summary"]["final_rating"] = "invented"
    assert summary.read("600001") == expected


def test_cli_rejects_unbounded_or_outside_symbol_scope_before_writing(saved):
    for argv in ([], ["--symbol", "../600001"], ["--symbol", str(saved)]):
        with pytest.raises(SystemExit) as error:
            main(argv)
        assert error.value.code == 2
    assert not (saved.parent / "derived").exists()


def test_sidecar_symlink_cannot_be_read_or_written(saved, tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    (saved.parent / "derived").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        summary.materialize("600001", write=True)
    assert summary.read("600001")["state"] == "unavailable"
    assert list(target.iterdir()) == []


def test_publisher_store_mismatch_is_rejected_before_any_original_read(saved, tmp_path, monkeypatch):
    monkeypatch.setattr(summary, "_pointer", lambda *a: pytest.fail("Mismatched publisher must not open another store"))
    with pytest.raises(ValueError, match="publisher research store mismatch"):
        summary.materialize("600001", write=True, expected_root=tmp_path / "other")
