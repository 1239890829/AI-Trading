#!/usr/bin/env python3
"""Runtime-aware ledger selection without turning wall-clock state into CI truth.

Static task state stays in docs/stages. Supported runtime conditions may
temporarily promote a waiting task to actionable for the current selection.
Unknown/unsupported conditions fail closed and remain waiting.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DOC_HEALTH = ROOT / "scripts" / "doc-health.py"
CALENDAR = ROOT / "backend" / "data" / "trade_calendar.json"
BEIJING = ZoneInfo("Asia/Shanghai")
A_SHARE_OBSERVABLE_SESSION = "A_SHARE_OBSERVABLE_SESSION"


def _load_doc_health():
    spec = importlib.util.spec_from_file_location("ledger_doc_health", DOC_HEALTH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _calendar_days(path: Path = CALENDAR) -> set[str]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    days = payload.get("days")
    if not isinstance(days, list):
        return set()
    return {str(day) for day in days if isinstance(day, str)}


def evaluate_condition(
    condition: str,
    *,
    now: datetime | None = None,
    calendar_path: Path = CALENDAR,
) -> dict[str, str]:
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    if condition != A_SHARE_OBSERVABLE_SESSION:
        return {"state": "unsupported", "reason": "unsupported_condition"}

    today = current.date().isoformat()
    days = _calendar_days(calendar_path)
    if current.weekday() >= 5:
        return {"state": "inactive", "reason": "weekend"}
    if not days or max(days) < today:
        return {"state": "unknown", "reason": "calendar_not_covered"}
    if today not in days:
        return {"state": "inactive", "reason": "calendar_closed"}

    hhmm = current.hour * 100 + current.minute
    if hhmm < 830:
        return {"state": "pending", "reason": "before_observation_start_window"}
    if hhmm <= 915:
        return {"state": "active", "reason": "observable_a_share_session_start"}
    return {"state": "expired", "reason": "full_session_start_window_missed"}


def runtime_selection(
    *,
    now: datetime | None = None,
    calendar_path: Path = CALENDAR,
) -> dict:
    doc = _load_doc_health()
    entries, parse_errors = doc.phase_tasks()
    tasks = {tid: (rel, fields) for tid, rel, fields in entries}
    effective = deepcopy(tasks)
    evaluations: list[dict[str, str]] = []

    for tid, (_, fields) in tasks.items():
        condition = fields.get("运行条件", "").strip()
        if fields.get("状态") != "待条件" or not condition:
            continue
        result = evaluate_condition(
            condition,
            now=now,
            calendar_path=calendar_path,
        )
        evaluations.append({"task": tid, "condition": condition, **result})
        if result["state"] == "active":
            effective[tid][1]["状态"] = "待执行"

    static_gate, static_task, static_candidates = doc.derive_current_stage_selection(tasks)
    gate, task, candidates = doc.derive_current_stage_selection(effective)
    current = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    return {
        "now_beijing": current.isoformat(),
        "parse_errors": parse_errors,
        "static_selection": {
            "gate": static_gate,
            "task": static_task,
            "candidates": static_candidates,
        },
        "effective_selection": {"gate": gate, "task": task, "candidates": candidates},
        "conditions": evaluations,
    }


def _parse_now(raw: str | None) -> datetime | None:
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING)
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", help="ISO timestamp; naive values use Asia/Shanghai")
    parser.add_argument(
        "--calendar-path",
        type=Path,
        default=CALENDAR,
        help="Persisted A-share trade calendar; defaults to backend/data/trade_calendar.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = runtime_selection(
        now=_parse_now(args.now),
        calendar_path=args.calendar_path,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    effective = result["effective_selection"]
    static = result["static_selection"]
    print(f"runtime-ledger-selection @ {result['now_beijing']}")
    print(f"static={static['gate']}/{static['task']}")
    print(f"effective={effective['gate']}/{effective['task']}")
    for row in result["conditions"]:
        print(
            f"{row['task']}: {row['condition']} -> "
            f"{row['state']} ({row['reason']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
