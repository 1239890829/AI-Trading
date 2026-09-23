#!/usr/bin/env python3
"""Project-local workspace hygiene gate.

Reject application-specific state/plugin directories from the repository tree.
Heavy dependency/runtime trees are pruned because their internals are owned by
their package managers, not by this repository.

Also rejects runtime-data double bookkeeping: a path that `.gitignore` declares
ignorable while the git index still tracks it.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

#: 运行数据根。落在这里的内容按 .gitignore 判据属"运行时落盘/可重建"，
#: 不应同时被 git 跟踪——两者并存即双重事实源（见 ignored_but_tracked）。
DATA_ROOTS = ("backend/data/", "data/")

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


def git(root: Path, *args: str, stdin: str | None = None, allow_exit: tuple[int, ...] = (0,)) -> str:
    """按既定约定（`git ls-files -z`，同 public_repo_scan / deadcode_scan）取 git 事实。"""
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        input=stdin.encode() if stdin is not None else None,
        capture_output=True,
    )
    if proc.returncode not in allow_exit:
        detail = proc.stderr.decode(errors="replace").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"git {' '.join(args)}: {detail}")
    return proc.stdout.decode(errors="replace")


def ignored_but_tracked(root: Path) -> list[str]:
    """已跟踪、却同时被**忽略型** .gitignore 规则命中的数据根路径。

    这类路径是双重事实源：忽略规则声明"运行时落盘、可重建、不属于源码树"，
    而索引声明"受版本管理的资产"。两者并存时 git 每天都会产出新的未跟踪文件
    噪声，且历史文件会被功能提交顺带带入。

    实测来源（2026-09-23）：`backend/data/{lhb,minute_decisions,position_plans}`
    共 13 个文件曾被 `ca3ee07` / `3150d60` 顺带提交，而 .gitignore 从未有对应
    规则；补齐规则并 `git rm --cached` 后，由本项守卫防止复发。

    反向规则（`!pattern`，如 `!data/*/.gitkeep`）是**有意**的取消忽略，不报告。
    `--no-index` 是必需项：默认 check-ignore 会跳过已跟踪路径，只能看到未跟踪
    文件，恰好漏掉本项要查的场景（实测确认）。
    """
    tracked = git(root, "ls-files", "-z").split("\0")
    candidates = [p for p in tracked if p and p.startswith(DATA_ROOTS)]
    if not candidates:
        return []
    # exit 1 = 无任何命中，即健康态，不是错误。
    raw = git(
        root,
        "check-ignore", "--no-index", "-v", "-z", "--stdin",
        stdin="\0".join(candidates) + "\0",
        allow_exit=(0, 1),
    )
    # -v -z 输出为四段一组：source / lineno / pattern / pathname
    fields = raw.split("\0")
    out: list[str] = []
    for i in range(0, len(fields) - 3, 4):
        source, lineno, pattern, path = fields[i : i + 4]
        if pattern.startswith("!"):
            continue
        out.append(f"{path} (同时被 {source}:{lineno}:{pattern} 忽略)")
    return sorted(out)


def check(root: Path) -> list[str]:
    problems = [f"forbidden app directory: {p}" for p in find_forbidden(root)]
    problems += [f"ignore rule hides forbidden app state: {p}" for p in ignore_hides_forbidden(root)]
    try:
        problems += [f"runtime data tracked while .gitignore ignores it: {p}" for p in ignored_but_tracked(root)]
    except (OSError, RuntimeError) as exc:  # 取证失败不得静默放行
        problems.append(f"runtime-data tracking check unavailable (fail-closed): {exc}")
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
