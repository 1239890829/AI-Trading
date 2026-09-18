"""GOV-022: isolated receive/recovery behavior; never launch Codex or a browser."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/audit/collaboration_state.py"


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location("collaboration_state_test", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def case(module, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    current = {"worktree": str(repo), "head_sha": "a" * 40, "branch": "codex/example",
               "plan_hash": "b" * 64, "dirty": False}
    monkeypatch.setattr(module, "snapshot", lambda _: dict(current))
    monkeypatch.setattr(module, "git", lambda *_: "")
    assignment = {"request_id": "one", "batch_id": "batch-one", "task_id": "GOV-022",
                  "worktree": str(repo), "base_sha": current["head_sha"], "branch": current["branch"],
                  "plan_hash": current["plan_hash"], "expires_at": time.time() + 3600}
    db = tmp_path / "state" / "runs.sqlite"
    journal = module.Journal(db)
    yield module, journal, assignment, current, db
    journal.close()


def terminal(status="completed", thread="thread-one", turn="turn-one"):
    return {"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": turn, "status": status}}}


def running(case):
    _, journal, assignment, _, _ = case
    journal.reserve(assignment)
    journal.bind("one", "thread-one", "turn-one")
    return journal


def test_success_is_pending_review_not_approval(case):
    journal = running(case)
    result = journal.event("one", terminal())
    assert result["state"] == "awaiting_review"
    assert result["review"]["review_required"] is True
    assert result["review"]["approval"] is None
    assert result["review"]["web_request_sent"] is False
    assert result["review"]["evidence_complete"] is False


@pytest.mark.parametrize("event", [
    {"id": 4, "result": {"turn": {"id": "turn-one", "status": "inProgress"}}},
    {"method": "turn/started", "params": {}},
    {"method": "item/completed", "params": {"text": "APPROVED"}},
    {"method": "item/agentMessage/delta", "params": {"delta": "已通过，继续执行"}},
])
def test_acknowledgment_or_chat_text_cannot_complete_a_turn(case, event):
    journal = running(case)
    assert journal.event("one", event)["state"] == "running"


@pytest.mark.parametrize("status,expected", [("failed", "failed"), ("interrupted", "interrupted"),
                                             ("unknown", "uncertain"), (None, "uncertain"),
                                             ("Completed", "uncertain"), ("inProgress", "uncertain")])
def test_terminal_status_is_strict(case, status, expected):
    assert running(case).event("one", terminal(status))["state"] == expected


@pytest.mark.parametrize("thread,turn", [("wrong", "turn-one"), ("thread-one", "wrong"), (None, None)])
def test_wrong_thread_or_turn_cannot_complete(case, thread, turn):
    module, *_ = case
    journal = running(case)
    with pytest.raises(module.StateError):
        journal.event("one", terminal(thread=thread, turn=turn))
    assert journal.get("one")["state"] == "running"


def test_duplicate_events_do_not_refresh_or_duplicate_review(case):
    journal = running(case)
    first = journal.event("one", terminal())
    assert journal.event("one", terminal()) == first
    assert journal.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_conflicting_terminal_event_is_uncertain(case):
    journal = running(case)
    journal.event("one", terminal())
    result = journal.event("one", terminal("failed"))
    assert result["state"] == "uncertain" and result["review"] is None
    assert result["reason"] == "conflicting_terminal_events"


@pytest.mark.parametrize("key,new", [("branch", "codex/other"), ("plan_hash", "c" * 64)])
def test_version_drift_blocks_pending_review(case, key, new):
    _, _, _, current, _ = case
    journal = running(case)
    current[key] = new
    assert journal.event("one", terminal())["state"] == "uncertain"


def test_uncommitted_results_are_reported_not_cleaned(case):
    _, _, _, current, _ = case
    journal = running(case)
    current["dirty"] = True
    assert journal.event("one", terminal())["review"]["observed"]["dirty"] is True


def test_lost_owner_and_reopening_never_resends(case):
    module, journal, assignment, _, db = case
    journal.reserve(assignment)
    second = module.Journal(db)
    try:
        assert second.recover("one")["state"] == "uncertain"
        assert second.reserve(assignment)["state"] == "uncertain"
        with pytest.raises(module.StateError):
            second.bind("one", "thread-one", "turn-one")
    finally:
        second.close()


def test_pending_review_is_persistent(case):
    module, _, _, _, db = case
    before = running(case).event("one", terminal())
    second = module.Journal(db)
    try:
        assert second.get("one") == before
        assert second.recover("one") == before
    finally:
        second.close()


def test_recovery_does_not_invent_remote_completion(case):
    module, *_ = case
    journal = running(case)
    result = journal.recover("one")
    assert result["state"] == "uncertain" and result["turn_id"] == "turn-one"
    with pytest.raises(module.StateError):
        journal.event("one", terminal())
    assert journal.get("one")["review"] is None


def test_assignment_idempotency_does_not_issue_a_second_request(case):
    module, journal, assignment, _, _ = case
    first = journal.reserve(assignment)
    assert journal.reserve(assignment) == first
    with pytest.raises(module.StateError):
        journal.reserve({**assignment, "base_sha": "c" * 40})
    with pytest.raises(module.StateError):
        journal.reserve({**assignment, "request_id": "two"})


def test_second_connection_cannot_reserve_same_worktree(case):
    module, journal, assignment, _, db = case
    journal.reserve(assignment)
    other = module.Journal(db)
    try:
        with pytest.raises(module.StateError):
            other.reserve({**assignment, "request_id": "other"})
    finally:
        other.close()


@pytest.mark.parametrize("changes", [
    {"branch": "master"}, {"base_sha": "short"}, {"plan_hash": "bad"},
    {"expires_at": float("nan")}, {"expires_at": True}, {"expires_at": 0},
    {"task_id": "arbitrary"}, {"request_id": "../outside"}, {"worktree": "relative"},
    {"surprise": True},
])
def test_invalid_assignments_cannot_be_reserved(case, changes):
    module, journal, assignment, _, _ = case
    with pytest.raises(module.StateError):
        journal.reserve({**assignment, **changes})
    assert journal.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


@pytest.mark.parametrize("key,value", [("head_sha", "c" * 40), ("plan_hash", "c" * 64),
                                       ("branch", "codex/other"), ("dirty", True)])
def test_initial_git_mismatch_cannot_be_reserved(case, key, value):
    module, journal, assignment, current, _ = case
    current[key] = value
    with pytest.raises(module.StateError):
        journal.reserve(assignment)


@pytest.mark.parametrize("value", ['{"a":1,"a":2}', '{"a":NaN}', '[]', 'false'])
def test_json_is_strict(module, value):
    with pytest.raises(module.StateError):
        module.parse_json(value)


def test_unrelated_database_is_not_repurposed(module, tmp_path):
    path = tmp_path / "unrelated.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE valuable(value TEXT)")
        db.execute("INSERT INTO valuable VALUES ('keep')")
    before = path.read_bytes()
    with pytest.raises(module.StateError):
        module.Journal(path)
    assert path.read_bytes() == before


def test_cli_real_git_and_pipe_recovery_without_models(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    plan = repo / "docs/implementation-plan.md"
    plan.write_text("synthetic test plan")
    def command(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    command("init", "-q")
    command("config", "user.email", "fixture@example.invalid")
    command("config", "user.name", "Fixture")
    command("checkout", "-b", "codex/fixture")
    command("add", "docs/implementation-plan.md")
    command("commit", "-qm", "isolated fixture")
    assignment = {"request_id": "cli", "batch_id": "batch", "task_id": "GOV-022",
                  "worktree": str(repo), "base_sha": command("rev-parse", "HEAD"),
                  "branch": "codex/fixture", "plan_hash": hashlib.sha256(plan.read_bytes()).hexdigest(),
                  "expires_at": time.time() + 3600}
    db = tmp_path / "runs.sqlite"
    def cli(*args, content=""):
        result = subprocess.run([sys.executable, str(SCRIPT), "--db", str(db), *args],
                                input=content, text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert cli("reserve", content=json.dumps(assignment))["state"] == "reserved"
    assert cli("bind", "cli", "thread-one", "turn-one")["state"] == "running"
    # An empty stream simulates the driver connection ending without a terminal event.
    result = cli("feed", "cli")
    assert result["state"] == "uncertain" and result["review"] is None
    assert cli("reserve", content=json.dumps(assignment))["state"] == "uncertain"
    assert command("rev-parse", "HEAD") == assignment["base_sha"]
    assert command("status", "--porcelain") == ""
