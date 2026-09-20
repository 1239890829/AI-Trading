"""Contracts for the application-directory workspace hygiene gate."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("workspace_hygiene", ROOT / "scripts/workspace-hygiene.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_detects_forbidden_application_directories(tmp_path):
    (tmp_path / "skills" / ".codex").mkdir(parents=True)
    (tmp_path / ".workbuddy-ai").mkdir()
    assert mod.find_forbidden(tmp_path) == [".workbuddy-ai", "skills/.codex"]


def test_prunes_dependency_owned_trees(tmp_path):
    (tmp_path / "backend" / ".venv" / ".codex").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "node_modules" / "pkg" / ".claude").mkdir(parents=True)
    assert mod.find_forbidden(tmp_path) == []


def test_gitignores_must_not_hide_forbidden_state(tmp_path):
    (tmp_path / ".gitignore").write_text("artifacts/\n.workbuddy/\n", encoding="utf-8")
    nested = tmp_path / "skills" / "vendor"
    nested.mkdir(parents=True)
    (nested / ".gitignore").write_text(".vscode/\n", encoding="utf-8")
    assert mod.ignore_hides_forbidden(tmp_path) == [
        ".gitignore:2:.workbuddy/",
        "skills/vendor/.gitignore:1:.vscode/",
    ]


def test_current_repository_has_no_forbidden_app_directories():
    assert mod.check(ROOT) == []
