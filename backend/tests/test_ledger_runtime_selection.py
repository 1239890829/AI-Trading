from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ledger-runtime-selection.py"
REPO = Path(__file__).resolve().parents[2]
BJ = ZoneInfo("Asia/Shanghai")


def load():
    spec = importlib.util.spec_from_file_location("ledger_runtime_selection", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def calendar(tmp_path: Path, *days: str) -> Path:
    path = tmp_path / "trade_calendar.json"
    path.write_text(json.dumps({"days": list(days)}))
    return path


def test_a_share_session_condition_is_time_bounded(tmp_path):
    mod = load()
    cal = calendar(tmp_path, "2026-09-21")

    before = mod.evaluate_condition(
        mod.A_SHARE_OBSERVABLE_SESSION,
        now=datetime(2026, 9, 21, 8, 29, tzinfo=BJ),
        calendar_path=cal,
    )
    active = mod.evaluate_condition(
        mod.A_SHARE_OBSERVABLE_SESSION,
        now=datetime(2026, 9, 21, 9, 0, tzinfo=BJ),
        calendar_path=cal,
    )
    closed = mod.evaluate_condition(
        mod.A_SHARE_OBSERVABLE_SESSION,
        now=datetime(2026, 9, 21, 9, 16, tzinfo=BJ),
        calendar_path=cal,
    )

    assert before["state"] == "pending"
    assert active["state"] == "active"
    assert closed["state"] == "expired"


def test_a_share_session_fails_closed_when_calendar_is_stale(tmp_path):
    mod = load()
    cal = calendar(tmp_path, "2026-09-18")
    result = mod.evaluate_condition(
        mod.A_SHARE_OBSERVABLE_SESSION,
        now=datetime(2026, 9, 21, 9, 0, tzinfo=BJ),
        calendar_path=cal,
    )
    assert result == {"state": "unknown", "reason": "calendar_not_covered"}


def test_real_ledger_runtime_condition_reclaims_g0_during_session(tmp_path):
    mod = load()
    cal = calendar(tmp_path, "2026-09-21")
    payload = mod.runtime_selection(
        now=datetime(2026, 9, 21, 9, 0, tzinfo=BJ),
        calendar_path=cal,
    )
    assert payload["static_selection"]["gate"] == "G4"
    assert payload["static_selection"]["task"] == "IMP-052"
    assert payload["effective_selection"]["gate"] == "G0"
    assert payload["effective_selection"]["task"] == "BUG-020"


def test_real_ledger_returns_to_static_after_session_close(tmp_path):
    mod = load()
    cal = calendar(tmp_path, "2026-09-21")
    payload = mod.runtime_selection(
        now=datetime(2026, 9, 21, 20, 0, tzinfo=BJ),
        calendar_path=cal,
    )
    assert payload["effective_selection"]["gate"] == "G4"
    assert payload["effective_selection"]["task"] == "IMP-052"


def test_runtime_selector_is_propagated_to_execution_surfaces():
    required = [
        REPO / "AGENTS.md",
        REPO / "docs/collaboration-workflow.md",
        REPO / "docs/plan-registry.md",
        REPO / "docs/stages/w08-governance.md",
        REPO / "skills/ashare-ledger-continue/SKILL.md",
        REPO / "skills/ashare-task-handoff/SKILL.md",
    ]
    for path in required:
        assert "ledger-runtime-selection.py" in path.read_text(), path
