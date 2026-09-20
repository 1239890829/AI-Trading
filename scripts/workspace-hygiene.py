#!/usr/bin/env python3
"""Project-local workspace hygiene gate.

Reject application-specific state/plugin directories from the repository tree.
Heavy dependency/runtime trees are pruned because their internals are owned by
their package managers, not by this repository.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_APP_DIRS = frozenset({
    ".workbuddy", ".workbuddy-ai",
    ".claude", ".claude-plugin",
    ".cursor", ".cursor-plugin",
    ".codex", ".opencode", ".gemini",
    ".vscode", ".idea", ".windsurf", ".cline", ".aider", ".agents",
})
PRUNE_DIRS = frozenset({
    ".git", "node_modules", ".venv", ".venv-research",
    ".next", ".turbo", "__pycache__", "dist", "build",
})


def find_forbidden(root: Path) -> list[str]:
    root = root.resolve()
    found: list[str] = []
    for parent, dirs, _files in os.walk(root, followlinks=False):
        parent_path = Path(parent)
        keep: list[str] = []
        for name in dirs:
            path = parent_path / name
            rel = path.relative_to(root).as_posix()
            if name in FORBIDDEN_APP_DIRS:
                found.append(rel)
                continue
            if name in PRUNE_DIRS:
                continue
            keep.append(name)
        dirs[:] = keep
    return sorted(found)


def ignore_hides_forbidden(root: Path) -> list[str]:
    root = root.resolve()
    out: list[str] = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in PRUNE_DIRS]
        if ".gitignore" not in files:
            continue
        ignore = Path(parent) / ".gitignore"
        rel = ignore.relative_to(root).as_posix()
        for lineno, raw in enumerate(ignore.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if any(name in line for name in FORBIDDEN_APP_DIRS):
                out.append(f"{rel}:{lineno}:{line}")
    return sorted(out)


def check(root: Path) -> list[str]:
    problems = [f"forbidden app directory: {p}" for p in find_forbidden(root)]
    problems += [f"ignore rule hides forbidden app state: {p}" for p in ignore_hides_forbidden(root)]
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    problems = check(args.root)
    if problems:
        for item in problems:
            print(f"[FAIL] {item}")
        return 1
    print("[OK] workspace hygiene: no application-specific project directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
