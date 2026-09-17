#!/usr/bin/env python3
"""Compare complete OpenAPI documents using a fresh, isolated baseline checkout.

Run with the backend interpreter, e.g. from the repository root:
backend/.venv/bin/python scripts/audit/openapi-contract-diff.py --base-ref origin/master
Imports the app without entering its lifespan. Does not start schedulers or HTTP
requests. Unlike a route-count check, compares schema bodies and all operation
fields too. Never reuses, checks out or removes somebody else's worktree.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

DUMP = "from app.main import app; import json; print(json.dumps(app.openapi(), sort_keys=True))"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def dump(backend: Path) -> dict:
    result = subprocess.run([sys.executable, "-c", DUMP], cwd=backend,
                            env={**os.environ, "PYTHONPATH": str(backend)},
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def differences(base, current, path: str = "$") -> list[str]:
    if type(base) is not type(current):
        return [path + ": type changed"]
    if isinstance(base, dict):
        changes = [path + "/" + key + ": added or removed" for key in sorted(base.keys() ^ current.keys())]
        for key in sorted(base.keys() & current.keys()):
            changes.extend(differences(base[key], current[key], path + "/" + key))
        return changes
    if base != current:
        return [path + ": value changed"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--cur", type=Path, default=Path(__file__).resolve().parents[2] / "backend")
    args = parser.parse_args()
    cur = args.cur.resolve()
    root = Path(git(cur, "rev-parse", "--show-toplevel"))
    sha = git(root, "rev-parse", "--verify", f"{args.base_ref}^{{commit}}")
    temporary = Path(tempfile.mkdtemp(prefix="ashare-openapi-"))
    baseline = temporary / "baseline"
    git(root, "worktree", "add", "--detach", str(baseline), sha)
    try:
        if git(baseline, "rev-parse", "HEAD") != sha:
            raise ValueError("Baseline identity mismatch")
        old, new = dump(baseline / cur.relative_to(root)), dump(cur)
        changed = differences(old, new)
        print(json.dumps({"base": sha, "paths": [len(old["paths"]), len(new["paths"])],
                          "differences": changed}, ensure_ascii=False, indent=2))
        return 1 if changed else 0
    finally:
        # No --force: unexpected baseline changes remain available for inspection.
        git(root, "worktree", "remove", str(baseline))
        temporary.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())
