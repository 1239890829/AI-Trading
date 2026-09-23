"""Contracts for the application-directory workspace hygiene gate."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess

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


# ------------------------------------------- 运行数据双重事实源（2026-09-23 补）
#
# 背景：`backend/data/{lhb,minute_decisions,position_plans}` 的 13 个文件曾被
# `ca3ee07` / `3150d60` 两个功能提交顺带带入索引，而 .gitignore 从无对应规则
# ⇒ 每个交易日稳定新增 3 个未跟踪文件，且无人察觉。此组用例固化判据。


def _init_repo(repo, ignore):
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / ".gitignore").write_text(ignore, encoding="utf-8")


def _force_add(repo, rel):
    """强行纳入索引以复现"顺带提交"的成因；判定只需索引，无需提交。"""
    subprocess.run(["git", "-C", str(repo), "add", "-f", rel], check=True, capture_output=True)


def test_ignored_but_tracked_flags_runtime_drift(tmp_path):
    _init_repo(tmp_path, "backend/data/runtime/\n")
    target = tmp_path / "backend" / "data" / "runtime" / "2026-09-23.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    _force_add(tmp_path, "backend/data/runtime/2026-09-23.json")

    problems = mod.ignored_but_tracked(tmp_path)
    assert len(problems) == 1
    assert "backend/data/runtime/2026-09-23.json" in problems[0]
    # 须能定位规则出处，否则无法直接修
    assert ".gitignore:1:backend/data/runtime/" in problems[0]


def test_negated_rule_is_not_reported(tmp_path):
    """`!pattern` 是有意的取消忽略（如 .gitkeep 占位文件），不得误报。

    注意规则写法须与真实仓库一致：排除**目录内容**（`data/raw/*`）才允许反向
    规则再包含；写成排除目录本身（`data/*`）时 git 规定"父目录被排除则无法再
    包含"，`!data/*/.gitkeep` 会失效（实测：报出的是 `data/*`）。
    """
    _init_repo(tmp_path, "data/raw/*\n!data/*/.gitkeep\n")
    keep = tmp_path / "data" / "raw" / ".gitkeep"
    keep.parent.mkdir(parents=True)
    keep.write_text("", encoding="utf-8")
    _force_add(tmp_path, "data/raw/.gitkeep")

    assert mod.ignored_but_tracked(tmp_path) == []


def test_tracked_path_outside_data_roots_is_not_reported(tmp_path):
    """范围限定在数据根；源码树内的忽略规则由其它守卫负责。"""
    _init_repo(tmp_path, "*.log\n")
    (tmp_path / "build.log").write_text("x\n", encoding="utf-8")
    _force_add(tmp_path, "build.log")

    assert mod.ignored_but_tracked(tmp_path) == []


def test_check_is_fail_closed_without_git(tmp_path):
    """取不到 git 事实时必须报错，不得静默判为通过。"""
    problems = mod.check(tmp_path)
    assert any("fail-closed" in item for item in problems), problems


def test_current_repository_has_no_ignored_runtime_data():
    assert mod.ignored_but_tracked(ROOT) == []
