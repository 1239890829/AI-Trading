"""GOV-022: durable receive/recovery state, NOT a model launcher or approval gate.

Consumes exact App Server thread/turn events and exposes pending review as JSON.
No credentials, chat text, browser automation, model calls, background polling or
Git writes. A trusted caller must supply assignments/bindings; the local journal
is not proof of reviewer identity. A future dispatcher must integrate it explicitly.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = 20260918
MAX_BYTES = 1_048_576
TERMINAL = {"completed": "awaiting_review", "failed": "failed", "interrupted": "interrupted"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}")


class StateError(ValueError):
    """Refuse ambiguous identity or unsafe replay without hiding existing state."""


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def parse_json(text: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise StateError("duplicate JSON key")
            result[key] = value
        return result
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise StateError("message too large")
    value = json.loads(text, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(StateError("non-finite JSON")))
    if not isinstance(value, dict):
        raise StateError("expected JSON object")
    return value


def git(worktree: Path, *args: str) -> str:
    proc = subprocess.run(["git", "--no-optional-locks", "-C", str(worktree), *args],
                          text=True, capture_output=True, timeout=10)
    if proc.returncode:
        raise StateError("cannot verify Git worktree")
    return proc.stdout.strip()


def snapshot(worktree: str) -> dict:
    path = Path(worktree)
    if not path.is_absolute() or not path.is_dir():
        raise StateError("worktree must be an existing absolute directory")
    path = path.resolve()
    if Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path:
        raise StateError("worktree must be repository root")
    before = git(path, "rev-parse", "HEAD")
    branch = git(path, "branch", "--show-current")
    plan = path / "docs/implementation-plan.md"
    if not plan.is_file() or not plan.resolve().is_relative_to(path):
        raise StateError("plan missing or outside worktree")
    result = {"worktree": str(path), "head_sha": before, "branch": branch,
              "plan_hash": hashlib.sha256(plan.read_bytes()).hexdigest(),
              "dirty": bool(git(path, "status", "--porcelain=v1"))}
    if before != git(path, "rev-parse", "HEAD"):
        raise StateError("HEAD changed while reading")
    return result


def validate_assignment(value: dict) -> dict:
    required = {"request_id", "batch_id", "task_id", "worktree", "branch", "base_sha", "plan_hash", "expires_at"}
    if set(value) != required:
        raise StateError("assignment fields do not match contract")
    for key in ("request_id", "batch_id", "task_id"):
        if not isinstance(value[key], str) or not IDENTIFIER.fullmatch(value[key]):
            raise StateError("invalid assignment identity")
    if not re.fullmatch(r"(?:BUG|IMP|GOV|OPS|RSH)-[0-9]{3}", value["task_id"]):
        raise StateError("invalid task ID")
    if not isinstance(value["branch"], str) or not value["branch"].startswith("codex/"):
        raise StateError("only a named feature branch is supported")
    for key, size in (("base_sha", 40), ("plan_hash", 64)):
        if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{" + str(size) + r"}", value[key]):
            raise StateError("invalid fixed version")
    deadline = value["expires_at"]
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise StateError("expiry must be finite Unix seconds")
    if not isinstance(value["worktree"], str) or not Path(value["worktree"]).is_absolute():
        raise StateError("invalid worktree")
    result = dict(value)
    result["worktree"] = str(Path(value["worktree"]).resolve())
    return result


class Journal:
    def __init__(self, path: Path):
        if path.is_symlink():
            raise StateError("journal cannot be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=3, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if version not in (0, SCHEMA) or (version == 0 and tables):
            self.db.close()
            raise StateError("refusing to use an unrelated database")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                        "worktree TEXT NOT NULL, assignment TEXT NOT NULL, state TEXT NOT NULL, "
                        "thread_id TEXT, turn_id TEXT, reason TEXT, review TEXT, updated_at REAL NOT NULL)")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS active_worktree ON runs(worktree) WHERE "
                        "state IN ('reserved','running','uncertain','awaiting_review')")
        self.db.execute(f"PRAGMA user_version={SCHEMA}")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def get(self, request_id: str) -> dict:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise StateError("unknown request")
        result = dict(row)
        result["assignment"] = json.loads(result["assignment"])
        result["review"] = json.loads(result["review"]) if result["review"] else None
        return result

    def reserve(self, assignment: dict, *, now: float | None = None) -> dict:
        item = validate_assignment(assignment)
        payload = encoded(item)
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        clock = time.time() if now is None else now
        with self.transaction():
            existing = self.db.execute("SELECT fingerprint FROM runs WHERE id=?", (item["request_id"],)).fetchone()
            if existing:
                if existing[0] != fingerprint:
                    raise StateError("request ID reused with different assignment")
                return self.get(item["request_id"])
            if item["expires_at"] <= clock:
                raise StateError("assignment expired")
            current = snapshot(item["worktree"])
            if (current["head_sha"] != item["base_sha"] or current["branch"] != item["branch"]
                    or current["plan_hash"] != item["plan_hash"] or current["dirty"]):
                raise StateError("worktree/version mismatch or uncommitted work")
            try:
                self.db.execute("INSERT INTO runs(id,fingerprint,worktree,assignment,state,updated_at) "
                                "VALUES(?,?,?,?,'reserved',?)",
                                (item["request_id"], fingerprint, item["worktree"], payload, clock))
            except sqlite3.IntegrityError as error:
                raise StateError("worktree already has an unresolved request") from error
            return self.get(item["request_id"])

    def bind(self, request_id: str, thread_id: str, turn_id: str) -> dict:
        if not all(isinstance(v, str) and IDENTIFIER.fullmatch(v) for v in (thread_id, turn_id)):
            raise StateError("invalid actual thread/turn IDs")
        with self.transaction():
            row = self.get(request_id)
            if row["state"] == "running" and (row["thread_id"], row["turn_id"]) == (thread_id, turn_id):
                return row
            if row["state"] != "reserved":
                raise StateError("cannot bind or replay this request")
            self.db.execute("UPDATE runs SET state='running',thread_id=?,turn_id=?,updated_at=? WHERE id=?",
                            (thread_id, turn_id, time.time(), request_id))
            return self.get(request_id)

    def recover(self, request_id: str) -> dict:
        """Record lost ownership; never resend, kill or claim the remote turn ended."""
        with self.transaction():
            row = self.get(request_id)
            if row["state"] in {"reserved", "running"}:
                self.db.execute("UPDATE runs SET state='uncertain',reason='owner_lost_check_actual_turn', "
                                "updated_at=? WHERE id=?", (time.time(), request_id))
            return self.get(request_id)

    def event(self, request_id: str, message: dict) -> dict:
        """Only a bound, exact terminal event may create a pending review notice."""
        with self.transaction():
            row = self.get(request_id)
            if message.get("method") != "turn/completed":
                return row
            params = message.get("params")
            turn = params.get("turn") if isinstance(params, dict) else None
            if not isinstance(turn, dict) or not row["turn_id"]:
                raise StateError("terminal message lacks verified binding")
            if (params.get("threadId"), turn.get("id")) != (row["thread_id"], row["turn_id"]):
                raise StateError("terminal event belongs to another thread/turn")
            target = TERMINAL.get(turn.get("status"), "uncertain")
            if row["state"] == target:
                return row
            if row["state"] in set(TERMINAL.values()):
                target = "uncertain"
                reason = "conflicting_terminal_events"
            elif row["state"] != "running":
                raise StateError("uncertain state requires explicit external reconciliation")
            else:
                reason = None if target != "uncertain" else "unrecognized_terminal_status"
            review = None
            if target == "awaiting_review":
                current = snapshot(row["worktree"])
                expected = row["assignment"]
                if current["branch"] != expected["branch"] or current["plan_hash"] != expected["plan_hash"]:
                    target, reason = "uncertain", "version_changed_before_review"
                else:
                    git(Path(row["worktree"]), "merge-base", "--is-ancestor", expected["base_sha"], current["head_sha"])
                    review = {"request_id": request_id, "task_id": expected["task_id"],
                              "base_sha": expected["base_sha"], "observed": current,
                              "thread_id": row["thread_id"], "turn_id": row["turn_id"],
                              "review_required": True, "approval": None,
                              "evidence_complete": False, "web_request_sent": False}
            self.db.execute("UPDATE runs SET state=?,reason=?,review=?,updated_at=? WHERE id=?",
                            (target, reason, encoded(review) if review else None, time.time(), request_id))
            return self.get(request_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="dedicated ignored runtime journal")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("reserve", help="assignment JSON on stdin; DOES NOT launch Codex")
    for name in ("bind", "feed", "status", "recover"):
        sub = commands.add_parser(name)
        sub.add_argument("request_id")
        if name == "bind":
            sub.add_argument("thread_id")
            sub.add_argument("turn_id")
    args = parser.parse_args()
    journal = Journal(args.db)
    try:
        if args.command == "reserve":
            result = journal.reserve(parse_json(sys.stdin.read(MAX_BYTES + 1)))
        elif args.command == "bind":
            result = journal.bind(args.request_id, args.thread_id, args.turn_id)
        elif args.command in {"status", "recover"}:
            result = getattr(journal, "get" if args.command == "status" else "recover")(args.request_id)
        else:
            try:
                while line := sys.stdin.readline(MAX_BYTES + 1):
                    journal.event(args.request_id, parse_json(line))
            except (ValueError, OSError, subprocess.SubprocessError):
                journal.recover(args.request_id)
                raise
            # EOF while running is not successful completion.
            result = journal.recover(args.request_id)
        print(encoded(result))
        return 0
    finally:
        journal.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(encoded({"state": "error", "reason": str(error)}), file=sys.stderr)
        raise SystemExit(2)
