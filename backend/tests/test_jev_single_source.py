"""Jev integration has one backend adapter; no parallel TypeSafe clients."""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
CLIENT = APP / "core" / "jev_client.py"
CONFIG = APP / "core" / "config.py"
KEY_NAMES = {"TYPESAFE_API_KEY", "JEV_API_KEY"}


def _py_files():
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def test_typesafe_endpoint_literal_has_single_backend_home():
    hits = []
    for path in _py_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "api.typesafe.ai" in node.value:
                    hits.append(path)
    assert set(hits) <= {CLIENT, CONFIG}, hits


def test_jev_key_environment_reads_only_live_in_adapter():
    hits = []
    for path in _py_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            arg = node.args[0]
            if not isinstance(arg, ast.Constant) or arg.value not in KEY_NAMES:
                continue
            # Any executable string lookup of a key name outside the adapter is a second credential path.
            hits.append(path)
    assert set(hits) <= {CLIENT}, hits


def test_business_modules_use_adapter_not_direct_typesafe_url():
    offenders = []
    for path in _py_files():
        if path in {CLIENT, CONFIG}:
            continue
        text = path.read_text()
        if "systemone" in text.lower() or "api.typesafe.ai" in text.lower():
            offenders.append(path)
    assert offenders == []


def test_jev_blueprint_links_all_active_ledger_owners():
    repo = APP.parents[1]
    blueprint = (repo / "docs" / "jev-integration.md").read_text()
    for task_id in ("GOV-024", "IMP-045", "IMP-046", "RSH-030"):
        assert task_id in blueprint


def test_default_jev_usage_receipt_path_is_gitignored():
    import subprocess

    repo = APP.parents[1]
    probe = subprocess.run(
        ["git", "check-ignore", "data/jev/usage.jsonl"],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "data/jev/usage.jsonl"
