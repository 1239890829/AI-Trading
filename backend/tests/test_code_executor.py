"""C 类代码执行器单测：预检红线 / diff 提取 / 沙箱全链（真 git 仓库）/ 门禁拦截。

沙箱全链用真实 git 仓库（tmp init）+ monkeypatch LLM 与门禁命令；
pytest 全量门禁本身的正确性由 CI 主工作区保障，此处只验证编排逻辑。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.models.agent import AgentAudit
from app.services import code_executor as ce


# ---------------------------------------------------------------- fixtures


def _git(cwd: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


@pytest.fixture()
def mini_repo(tmp_path):
    """真实 git 仓库：backend/app/demo.py + backend/tests/test_demo.py 初始提交。"""
    root = tmp_path / "repo"
    (root / "backend" / "app").mkdir(parents=True)
    (root / "backend" / "tests").mkdir(parents=True)
    (root / "backend" / "app" / "demo.py").write_text(
        'def add(a, b):\n    """两数相加。"""\n    return a + b\n', encoding="utf-8")
    (root / "backend" / "tests" / "test_demo.py").write_text(
        "from app.demo import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "ai@test")
    _git(root, "config", "user.name", "AI")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    from app.models.watchlist import Base
    from app.services import agent_tasks

    (tmp_path / "audit.db").touch()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path}/audit.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(agent_tasks, "get_session_factory", lambda: factory)
    yield factory
    engine.dispose()


@pytest.fixture(autouse=True)
def enable_code_changes_for_enabled_path_tests(monkeypatch):
    """Enabled-path tests model an administrator's explicit opt-in."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "agent_code_change_enabled", True)


def _item(**kw) -> dict:
    base = {"class": "C", "finding": "add 函数缺注释示例", "action": "给 add 函数补一行用法注释",
            "files": ["backend/app/demo.py"], "expected_effect": "可读性", "status": "pending"}
    base.update(kw)
    return base


_DIFF = """diff --git a/backend/app/demo.py b/backend/app/demo.py
index 1111111..2222222 100644
--- a/backend/app/demo.py
+++ b/backend/app/demo.py
@@ -1,3 +1,4 @@
 def add(a, b):
     \"\"\"两数相加。\"\"\"
+    # 用法：add(1, 2) == 3
     return a + b
"""


def _stub_llm(monkeypatch, diff: str | None):
    monkeypatch.setattr(ce, "_llm_patch", lambda item, ctx: diff or "no diff here")


def _stub_gate(monkeypatch, fail: bool = False):
    """门禁 stub：全过或第一步失败。"""

    def fake(worktree, changed):
        if fail:
            return False, "门禁失败 rc=1（pytest）：1 failed"
        return True, "pytest 全量 + pyflakes 全绿"

    monkeypatch.setattr(ce, "_gate_commands", lambda w, c: [("__stub__", [])])
    monkeypatch.setattr(ce, "_run_gate", fake)


# ---------------------------------------------------------------- 预检


def test_validate_files_rejects_out_of_allowlist():
    # 校验顺序：.py 后缀先于白名单前缀
    assert ".py" in ce._validate_files(["apps/web/lib/api.ts"])
    assert "白名单" in ce._validate_files(["scripts/build_push_cards.py"])
    assert "白名单" in ce._validate_files(["docs/plan.md".replace(".md", ".py")])


def test_validate_files_rejects_denied_paths():
    assert "禁改" in ce._validate_files(["backend/app/core/config.py"])
    assert "禁改" in ce._validate_files(["backend/migrations/versions/x.py"])
    assert "禁改" in ce._validate_files(["backend/app/api/__init__.py"])
    assert "禁改" in ce._validate_files(["backend/tests/conftest.py"])


def test_validate_files_rejects_non_py_and_overflow():
    assert ".py" in ce._validate_files(["backend/app/demo.tsx"])
    assert "上限" in ce._validate_files([f"backend/app/f{i}.py" for i in range(4)])
    assert ce._validate_files([]) is not None
    assert ce._validate_files(["backend/app/demo.py"]) is None


def test_daily_limit_blocks_second_c(sf):
    with sf() as db:
        db.add(AgentAudit(actor="ai", action="code.apply", target="abc:backend/app/x.py"))
        db.commit()
    item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=Path("/tmp"))
    assert item["status"] == "deferred" and "上限" in item["result"]


def test_dirty_worktree_defers(mini_repo, sf, monkeypatch):
    (mini_repo / "scratch.txt").write_text("dirty", encoding="utf-8")  # 制造脏工作区
    _stub_llm(monkeypatch, _DIFF)
    _stub_gate(monkeypatch)
    item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)
    assert item["status"] == "deferred" and "干净" in item["result"]


def test_code_change_disabled(mini_repo, sf, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "agent_code_change_enabled", False)
    before_head = _git(mini_repo, "rev-parse", "HEAD")[1]
    before_status = _git(mini_repo, "status", "--porcelain")[1]
    monkeypatch.setattr(ce, "_llm_patch", lambda *a, **k: pytest.fail("关闭时不得请求 patch"))
    item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)
    assert item["status"] == "deferred"
    assert "关闭" in item["result"] and "不得修改、提交或合并" in item["result"]
    assert _git(mini_repo, "rev-parse", "HEAD")[1] == before_head
    assert _git(mini_repo, "status", "--porcelain")[1] == before_status


# ---------------------------------------------------------------- diff 提取


def test_extract_diff_variants():
    fenced = f"```diff\n{_DIFF}\n```"
    assert ce._extract_diff(fenced) == _DIFF.strip()
    assert ce._extract_diff(_DIFF) == _DIFF.strip()
    assert ce._extract_diff("我不会改，这里没有 diff") is None
    assert ce._extract_diff("") is None


# ---------------------------------------------------------------- 沙箱全链（真 git）


def test_full_cycle_applies_and_merges(mini_repo, sf, monkeypatch):
    """合法 diff + 门禁过 → commit 合并回主分支 + 审计 + 文件内容真实变更。"""
    _stub_llm(monkeypatch, _DIFF)
    _stub_gate(monkeypatch, fail=False)
    item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)

    assert item["status"] == "executed", item.get("result")
    assert item["commit"] and item["files_changed"] == ["backend/app/demo.py"]
    # 主分支内容已更新（AI 注释真实落地）
    assert "用法：add(1, 2) == 3" in (mini_repo / "backend/app/demo.py").read_text(encoding="utf-8")
    # 临时分支已删（沙箱目录路径含随机段，无法逐一断言，分支即证据）
    assert "evolution/" not in _git(mini_repo, "branch", "--list", "evolution/*")[1]
    # 审计留痕
    with sf() as db:
        rows = db.query(AgentAudit).filter(AgentAudit.action == "code.apply").all()
        assert len(rows) == 1 and rows[0].target.startswith(item["commit"])


def test_gate_failure_discards_sandbox(mini_repo, sf, monkeypatch):
    """门禁失败 → rejected、主分支零接触、沙箱分支清理。"""
    before = _git(mini_repo, "rev-parse", "HEAD")[1]
    _stub_llm(monkeypatch, _DIFF)
    _stub_gate(monkeypatch, fail=True)
    item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)

    assert item["status"] == "rejected" and "门禁" in item["result"]
    assert _git(mini_repo, "rev-parse", "HEAD")[1] == before  # 主分支未动
    assert "evolution/" not in _git(mini_repo, "branch", "--list", "evolution/*")[1]
    # 文件内容未被修改（沙箱已丢弃）
    assert "用法" not in (mini_repo / "backend/app/demo.py").read_text(encoding="utf-8")


def test_unapplicable_diff_rejected(mini_repo, sf, monkeypatch):
    """diff 上下文对不上（LLM 幻觉行号/内容）→ git apply --check 拦截。"""
    bad = _DIFF.replace('"""两数相加。"""', '"""两数相加（幻觉注释）。"""')
    _stub_llm(monkeypatch, bad)
    before = _git(mini_repo, "rev-parse", "HEAD")[1]
    item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)

    assert item["status"] == "rejected" and "无法干净应用" in item["result"]
    assert _git(mini_repo, "rev-parse", "HEAD")[1] == before


def test_a_experiment_path_unaffected(mini_repo, sf):
    """防回归：A 类实验挂账链路不受 C 类改动影响（import 层面）。"""
    from app.services import experiments  # noqa: F401
    assert hasattr(experiments, "attach_experiment")
