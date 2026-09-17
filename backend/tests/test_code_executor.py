"""C 类代码执行器单测：预检红线 / diff 级授权 / 应用后 git 复核 / 攻击性用例 / 门禁。

沙箱全链用真实 git 仓库（tmp init）+ monkeypatch LLM 与门禁命令；
pytest 全量门禁本身的正确性由 CI 主工作区保障，此处只验证编排逻辑。

⚠️ 本文件**只能由人类开发者修改**（`backend/tests/**` 已于 2026-09-15 移出 AI 白名单）：
门禁在**宿主解释器**里跑 pytest，允许 AI 写测试 = 允许 AI 借测试在宿主上执行任意代码。
见 `app/services/code_executor.py` 模块 docstring 第 1 条与 `test_ai_cannot_touch_tests_*`。

对齐审计要求（O1 加固）：执行器**只提议、不落地**——产出 = patch + 隔离分支 commit +
审计，落地走 `codex/*` → PR → 完整 CI → 网页版审查。
"""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

from app.models.agent import AgentAudit
from app.services import code_executor as ce


# ---------------------------------------------------------------- 工具


def _git(cwd: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def _item(**kw) -> dict:
    base = {"class": "C", "finding": "add 函数缺注释示例", "action": "给 add 函数补一行用法注释",
            "files": ["backend/app/demo.py"], "expected_effect": "可读性", "status": "pending"}
    base.update(kw)
    return base


_DEMO_BODY = [
    "@@ -1,3 +1,4 @@",
    " def add(a, b):",
    '     """两数相加。"""',
    "+    # 用法：add(1, 2) == 3",
    "     return a + b",
]


def _mk_diff(path: str, body: list[str] | None = None) -> str:
    """构造一个「修改既有文件」的 unified diff（路径原样嵌入，便于构造攻击形态）。"""
    lines = [
        f"diff --git a/{path} b/{path}",
        "index 1111111..2222222 100644",
        f"--- a/{path}",
        f"+++ b/{path}",
        *(body if body is not None else _DEMO_BODY),
    ]
    return "\n".join(lines) + "\n"


_DIFF = _mk_diff("backend/app/demo.py")


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


# ---------------------------------------------------------------- fixtures


@pytest.fixture()
def mini_repo(tmp_path):
    """真实 git 仓库：app/demo.py + app/core/config.py（禁改面）+ tests/test_demo.py。"""
    root = tmp_path / "repo"
    (root / "backend" / "app" / "core").mkdir(parents=True)
    (root / "backend" / "tests").mkdir(parents=True)
    (root / "backend" / "app" / "demo.py").write_text(
        'def add(a, b):\n    """两数相加。"""\n    return a + b\n', encoding="utf-8")
    (root / "backend" / "app" / "core" / "config.py").write_text(
        'SECRET_KEY = "must-not-be-touched"\n', encoding="utf-8")
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


@pytest.fixture(autouse=True)
def isolate_patch_archive(tmp_path, monkeypatch):
    """patch 归档目录默认指向真实仓库 `artifacts/`——测试必须重定向，避免污染工作区。"""
    monkeypatch.setattr(ce, "PATCH_ARCHIVE_DIR", tmp_path / "patch-archive")


# ---------------------------------------------------------------- 预检：白名单 / 禁改 / 形态


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


def test_validate_files_rejects_path_shape_attacks():
    """形态层：空路径 / 绝对路径 / ../ 穿越 / ~ / 盘符 / 目录。"""
    absolute = ce._validate_files(["/etc/evil.py"])
    assert absolute and "绝对路径" in absolute
    traversal = ce._validate_files(["backend/app/../../../etc/evil.py"])
    assert traversal and "穿越" in traversal
    assert "反斜杠" in ce._validate_files([r"backend\app\demo.py"])
    assert "~" in ce._validate_files(["~/evil.py"])
    assert "盘符" in ce._validate_files(["C:/evil.py"])
    assert "目录" in ce._validate_files(["backend/app/"])
    assert "空" in ce._validate_files([""]) or "空" in ce._validate_files(["   "])


def test_validate_files_rejects_workflows_and_credentials():
    """受保护面：工作流 / 依赖与构建配置 / 凭据（纵深防御，白名单外的部分另由前缀拦截）。"""
    assert "禁改" in ce._validate_files(["backend/app/core/db.py"])
    assert "禁改" in ce._validate_files(["backend/app/core/ttl_cache.py"])
    assert ce._validate_files([".github/workflows/ci.yml"]) is not None
    assert ce._validate_files(["backend/.env"]) is not None
    assert ce._validate_files(["backend/requirements.txt"]) is not None


def test_is_protected_matches_protected_surface_only():
    """反套套逻辑：受保护面判据必须**同时**证明「会拒绝」与「不会误拒」。

    ⚠️ 用例里的路径必须是**真实存在**的仓内路径：`doc-health` 的 F 项会扫代码里
    形似文档的路径引用，写臆造路径（如 `docs/` 下的假名）当场判红，
    而「登记豁免」正是该项目明令不要走的路（见 [[KB-ENG-97]] 末段）。
    """
    for norm in (".github/workflows/ci.yml", "backend/app/weird.yml", "docs/INDEX.md",
                 "skills/x/SKILL.md", "backend/migrations/v1.py", "backend/tests/t.py",
                 "AGENTS.md", "backend/app/core/config.toml", "backend/app/x.pem"):
        assert ce._is_protected(norm), norm
    for norm in ("backend/app/demo.py", "backend/app/api/routes/market.py",
                 "backend/app/services/code_executor.py"):
        assert not ce._is_protected(norm), norm


def test_normalize_repo_path_keeps_legacy_short_name_semantics():
    """历史议程写的是 backend 内短路径——归一化必须保持该语义（否则存量议程全线失效）。"""
    assert ce._normalize_repo_path("app/demo.py") == "backend/app/demo.py"
    assert ce._normalize_repo_path("backend/app/demo.py") == "backend/app/demo.py"
    assert ce._normalize_repo_path("./app/demo.py") == "backend/app/demo.py"
    assert ce._normalize_repo_path(" app/demo.py ") == "backend/app/demo.py"
    assert ce._normalize_repo_path(r"app\demo.py") == "backend/app/demo.py"


def test_ai_cannot_touch_tests_precheck_blocks_before_any_llm_call(mini_repo, sf, monkeypatch, tmp_path):
    """攻击：让 AI 写一个「在宿主上执行命令」的测试文件。

    `backend/tests/**` 已移出白名单 ⇒ 预检在最前面就拒，**连 patch 生成机会都没有**
    （门禁在宿主解释器里跑 pytest，AI 生成的测试 = 宿主上的任意代码执行）。
    """
    sentinel = tmp_path / "pwned-by-ai-test"
    monkeypatch.setattr(ce, "_llm_patch", lambda *a, **k: pytest.fail("禁止为 tests/ 生成 patch"))
    monkeypatch.setattr(ce, "_run_gate", lambda *a, **k: pytest.fail("禁止在宿主上跑 AI 生成的测试"))

    item = _item(files=["backend/tests/test_demo.py"])
    out = ce.execute_c_item(item, sf, "2026-09-08", repo_root=mini_repo)

    assert out["status"] == "rejected"
    assert "白名单" in out["result"] and "宿主" in out["result"]
    assert not sentinel.exists()
    assert _git(mini_repo, "status", "--porcelain")[1] == ""


# ---------------------------------------------------------------- 每日上限 / 工作区


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


# ---------------------------------------------------------------- diff 提取 / 解析


def test_extract_diff_variants():
    fenced = f"```diff\n{_DIFF}\n```"
    assert ce._extract_diff(fenced) == _DIFF.strip()
    assert ce._extract_diff(_DIFF) == _DIFF.strip()
    assert ce._extract_diff("我不会改，这里没有 diff") is None
    assert ce._extract_diff("") is None


def test_parse_patch_ignores_header_like_lines_inside_hunk():
    """hunk 内的删除行若原文是 `-- x`，会被渲染成 `--- x`；不区分就会误判成文件头。

    若把 hunk 内的 `--- ...` 当文件头，则该段 old/new 路径不相等 ⇒ 被判成 **rename**。
    """
    tricky = _mk_diff("backend/app/demo.py", [
        "@@ -1,3 +1,4 @@",
        " def add(a, b):",
        "--- TODO: legacy note",
        "+    # 用法：add(1, 2) == 3",
        "     return a + b",
    ])
    entries = ce._parse_patch(tricky)
    assert len(entries) == 1, entries
    assert entries[0].status == "modify"
    assert entries[0].old_path == entries[0].new_path == "backend/app/demo.py"


def test_parse_patch_detects_add_delete_rename():
    add = ce._parse_patch(
        "diff --git a/backend/app/new.py b/backend/app/new.py\n"
        "new file mode 100644\n--- /dev/null\n+++ b/backend/app/new.py\n@@ -0,0 +1 @@\n+x\n")
    assert add and add[0].status == "add" and add[0].old_path == ""

    delete = ce._parse_patch(
        "diff --git a/backend/app/demo.py b/backend/app/demo.py\n"
        "deleted file mode 100644\n--- a/backend/app/demo.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n")
    assert delete and delete[0].status == "delete" and delete[0].new_path == ""

    rename = ce._parse_patch(
        "diff --git a/backend/app/a.py b/backend/app/b.py\n"
        "rename from backend/app/a.py\nrename to backend/app/b.py\n"
        "--- a/backend/app/a.py\n+++ b/backend/app/b.py\n@@ -1 +1 @@\n-x\n+y\n")
    assert rename and rename[0].status == "rename"
    assert rename[0].old_path != rename[0].new_path


# ---------------------------------------------------------------- diff 级授权（应用前）


def test_validate_patch_rejects_undeclared_file(tmp_path):
    """攻击①：patch 修改了议程**未声明**的文件（声明的是意图，patch 才是事实）。"""
    reason = ce._validate_patch(_mk_diff("backend/app/other.py"), ["backend/app/demo.py"], tmp_path)
    assert reason and "未声明" in reason


def test_validate_patch_rejects_declared_plus_hidden_file(tmp_path):
    """攻击②：一个声明文件 + 一个隐藏文件——隐藏文件那一段必须被拦。"""
    diff = _mk_diff("backend/app/demo.py") + _mk_diff("backend/app/hidden.py")
    reason = ce._validate_patch(diff, ["backend/app/demo.py"], tmp_path)
    assert reason and "未声明" in reason and "hidden.py" in reason


def test_validate_patch_rejects_missing_declared_file(tmp_path):
    """反向：声明了 A、patch 只改了 B（B 也未声明）⇒ 同样拦。"""
    reason = ce._validate_patch(_mk_diff("backend/app/other.py"), ["backend/app/demo.py"], tmp_path)
    assert reason is not None


def test_validate_patch_rejects_traversal_and_absolute(tmp_path):
    """攻击③④：`../` 路径穿越 / 绝对路径。"""
    traversal = ce._validate_patch(
        _mk_diff("backend/app/../../evil.py"), ["backend/app/../../evil.py"], tmp_path)
    assert traversal and "穿越" in traversal

    absolute = ce._validate_patch(
        _mk_diff("/etc/evil.py"), ["backend/app/demo.py"], tmp_path)
    assert absolute and "绝对路径" in absolute


def test_validate_patch_rejects_symlink_escaping_repo(mini_repo, sf, monkeypatch, tmp_path):
    """攻击⑤a：仓库内的符号链接指向仓库**外** ⇒ canonical 解析后逃逸。

    注意 worktree 只是 **git 层**隔离、不是 OS 沙箱，所以这是靠路径判据而非沙箱拦下的。
    """
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = 'outside the repo'\n", encoding="utf-8")
    os.symlink(str(outside), str(mini_repo / "backend" / "app" / "link.py"))
    _git(mini_repo, "add", "-A")
    _git(mini_repo, "commit", "-q", "-m", "add symlink")
    before = _git(mini_repo, "rev-parse", "HEAD")[1]

    _stub_llm(monkeypatch, _mk_diff("backend/app/link.py"))
    _stub_gate(monkeypatch)
    out = ce.execute_c_item(_item(files=["backend/app/link.py"]), sf, "2026-09-08", repo_root=mini_repo)

    assert out["status"] == "rejected", out.get("result")
    assert "逃出仓库根" in out["result"], out.get("result")
    assert _git(mini_repo, "rev-parse", "HEAD")[1] == before
    assert "evolution/" not in _git(mini_repo, "branch", "--list", "evolution/*")[1]


def test_validate_patch_rejects_symlink_bypassing_denylist(mini_repo, sf, monkeypatch):
    """攻击⑤b：符号链接指向**仓库内**的禁改文件（link.py → core/config.py）。

    这一形态 canonical **不逃逸**，只能靠符号链接判据拦下；若只做 canonical 检查，
    写操作会**穿透软链**改到 `app/core/config.py`——禁改清单被整体绕过。
    """
    os.symlink("core/config.py", str(mini_repo / "backend" / "app" / "link.py"))
    _git(mini_repo, "add", "-A")
    _git(mini_repo, "commit", "-q", "-m", "add symlink to denied file")
    before = _git(mini_repo, "rev-parse", "HEAD")[1]

    _stub_llm(monkeypatch, _mk_diff("backend/app/link.py"))
    _stub_gate(monkeypatch)
    out = ce.execute_c_item(_item(files=["backend/app/link.py"]), sf, "2026-09-08", repo_root=mini_repo)

    assert out["status"] == "rejected", out.get("result")
    assert "符号链接" in out["result"], out.get("result")
    assert _git(mini_repo, "rev-parse", "HEAD")[1] == before
    assert "must-not-be-touched" in (
        mini_repo / "backend" / "app" / "core" / "config.py").read_text(encoding="utf-8")


def test_validate_patch_rejects_workflow_config_and_migration(tmp_path):
    """攻击⑥⑦：改 CI 工作流 / 改配置或迁移。"""
    for path in (".github/workflows/ci.yml", "backend/.github/workflows/ci.yml"):
        reason = ce._validate_patch(_mk_diff(path), [path], tmp_path)
        assert reason is not None, path
    for path in ("backend/app/core/config.py", "backend/migrations/versions/x.py"):
        reason = ce._validate_patch(_mk_diff(path), [path], tmp_path)
        assert reason and ("受保护面" in reason or "禁改" in reason), reason


def test_validate_patch_rejects_tests_even_when_declared(tmp_path):
    """攻击⑧（diff 层）：即便议程把测试文件也声明了，diff 级授权仍必须拒。"""
    reason = ce._validate_patch(
        _mk_diff("backend/tests/test_demo.py"), ["backend/tests/test_demo.py"], tmp_path)
    assert reason is not None


def test_validate_patch_rejects_add_delete_rename_and_duplicates(tmp_path):
    """攻击⑨：新增 / 删除 / 重命名；以及同一文件重复出现（构造可疑）。"""
    add = ("diff --git a/backend/app/new.py b/backend/app/new.py\n"
           "new file mode 100644\n--- /dev/null\n+++ b/backend/app/new.py\n@@ -0,0 +1 @@\n+x\n")
    assert "add" in (ce._validate_patch(add, ["backend/app/new.py"], tmp_path) or "")

    delete = ("diff --git a/backend/app/demo.py b/backend/app/demo.py\n"
              "deleted file mode 100644\n--- a/backend/app/demo.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n")
    assert "delete" in (ce._validate_patch(delete, ["backend/app/demo.py"], tmp_path) or "")

    rename = ("diff --git a/backend/app/a.py b/backend/app/b.py\n"
              "rename from backend/app/a.py\nrename to backend/app/b.py\n"
              "--- a/backend/app/a.py\n+++ b/backend/app/b.py\n@@ -1 +1 @@\n-x\n+y\n")
    assert "rename" in (ce._validate_patch(rename, ["backend/app/a.py"], tmp_path) or "")

    dup = _mk_diff("backend/app/demo.py") + _mk_diff("backend/app/demo.py")
    assert "多次" in (ce._validate_patch(dup, ["backend/app/demo.py"], tmp_path) or "")


def test_validate_patch_accepts_legit_single_file(tmp_path):
    """反套套逻辑：合法的单文件修改必须放行（否则上面全是「恒拒」= 判据失效）。"""
    assert ce._validate_patch(_DIFF, ["backend/app/demo.py"], tmp_path) is None


# ---------------------------------------------------------------- git 权威复核（应用后）


def test_verify_applied_detects_unapproved_change(mini_repo):
    """判据与被判对象**不同源**：即便第一层（本模块的 diff 解析）被绕过，
    第二层读的是 git 的 `diff --name-status`，必须独立报出越界。"""
    (mini_repo / "backend" / "app" / "demo.py").write_text(
        'def add(a, b):\n    return a + b  # rogue\n', encoding="utf-8")
    reason = ce._verify_applied(mini_repo, ["backend/app/other.py"])
    assert reason and "不一致" in reason
    assert "demo.py" in reason  # 越界项


def test_verify_applied_rejects_empty_and_non_modify(mini_repo):
    # 工作区无改动但批准集合非空 → 报「未落地」（比通用的"不一致"更可诊断）
    assert "未落地" in (ce._verify_applied(mini_repo, ["backend/app/demo.py"]) or "")
    (mini_repo / "backend" / "app" / "demo.py").unlink()
    reason = ce._verify_applied(mini_repo, ["backend/app/demo.py"])
    assert reason and ("D" in reason or "不被允许" in reason)


def test_verify_applied_accepts_clean_modify(mini_repo):
    (mini_repo / "backend" / "app" / "demo.py").write_text(
        'def add(a, b):\n    """两数相加。"""\n    return a + b  # ok\n', encoding="utf-8")
    assert ce._verify_applied(mini_repo, ["backend/app/demo.py"]) is None


# ---------------------------------------------------------------- 门禁


def test_gate_timeout_blocks_real_sleep(tmp_path, monkeypatch):
    """攻击⑩：门禁超时（真实 sleep，走 `_run_gate` 的超时分支）。"""
    monkeypatch.setattr(ce, "GATE_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(ce, "_gate_commands", lambda w, c: [(tmp_path, ["sleep", "5"])])
    ok, msg = ce._run_gate(tmp_path, [])
    assert ok is False and "超时" in msg


def test_gate_timeout_rejects_full_cycle(mini_repo, sf, monkeypatch):
    """端到端：门禁超时 → rejected、主分支零接触、沙箱分支清理。"""
    before = _git(mini_repo, "rev-parse", "HEAD")[1]
    _stub_llm(monkeypatch, _DIFF)
    monkeypatch.setattr(ce, "GATE_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(ce, "_gate_commands",
                        lambda w, c: [(w / "backend", ["sleep", "5"])])
    out = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)
    assert out["status"] == "rejected" and "超时" in out["result"]
    assert _git(mini_repo, "rev-parse", "HEAD")[1] == before
    assert "evolution/" not in _git(mini_repo, "branch", "--list", "evolution/*")[1]


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


# ---------------------------------------------------------------- 沙箱全链（真 git）


def test_full_cycle_proposes_without_merging(mini_repo, sf, monkeypatch):
    """合法 diff + 门禁过 → **只在隔离分支留下待审阅 commit**，主分支零改动。

    加固前此用例断言「合并回主分支」，那正是被移除的能力（审计 O1）：
    应用内 LLM 不应具备自改代码并落地的权限——落地一律走 `codex/*` → PR → CI → 审查。
    """
    before = _git(mini_repo, "rev-parse", "HEAD")[1]
    _stub_llm(monkeypatch, _DIFF)
    _stub_gate(monkeypatch, fail=False)
    item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)

    assert item["status"] == "executed", item.get("result")
    assert item["commit"] and item["files_changed"] == ["backend/app/demo.py"]

    # 1) 不再合并：主分支 HEAD 与工作区文件均未变
    assert _git(mini_repo, "rev-parse", "HEAD")[1] == before
    assert "用法：add(1, 2) == 3" not in (mini_repo / "backend/app/demo.py").read_text(encoding="utf-8")
    # 2) 隔离分支**保留**且带着那个 commit（供人工审阅 / 取用）
    assert item["branch"].startswith("evolution/")
    assert item["branch"] in _git(mini_repo, "branch", "--list", "evolution/*")[1]
    assert item["commit"] == _git(mini_repo, "rev-parse", "--short", item["branch"])[1]
    assert "用法：add(1, 2) == 3" in _git(
        mini_repo, "show", f"{item['branch']}:backend/app/demo.py")[1]
    # 3) patch 已归档到仓库外（沙箱会被删，patch 必须留下供人工审阅）
    assert item["merged"] is False and item["review_required"] is True
    patch_path = Path(item["patch_path"])
    assert patch_path.exists() and "diff --git" in patch_path.read_text(encoding="utf-8")
    # 4) 明确暂存：沙箱内的临时 patch 文件**未**被卷入 commit
    assert ".evo.patch" not in _git(mini_repo, "show", "--stat", item["branch"])[1]
    # 5) 审计留痕
    with sf() as db:
        rows = db.query(AgentAudit).filter(AgentAudit.action == "code.apply").all()
        assert len(rows) == 1 and rows[0].target.startswith(item["commit"])


def test_worktree_cleanup_failure_does_not_lose_proposal(mini_repo, sf, monkeypatch, caplog):
    """攻击⑪：worktree 清理失败必须**留痕且不吞掉提议结果**（静默会掩盖残留、撞下次 add）。"""
    real_git = ce._git

    def flaky_git(args, cwd, timeout=60):
        if args[:2] == ["worktree", "remove"]:
            return 1, "fatal: 模拟清理失败"
        return real_git(args, cwd, timeout)

    _stub_llm(monkeypatch, _DIFF)
    _stub_gate(monkeypatch)
    monkeypatch.setattr(ce, "_git", flaky_git)

    with caplog.at_level("WARNING", logger="app.services.code_executor"):
        item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)

    assert item["status"] == "executed", item.get("result")
    assert "worktree 清理失败" in caplog.text


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


# ---------------------------------------------------------------- 脱敏 / 结构性约束


def test_redact_scrubs_credentials_and_truncates():
    scrubbed = ce._redact("Authorization: Bearer abcdefghijklmnop end")
    assert "abcdefghijklmnop" not in scrubbed and "[REDACTED]" in scrubbed
    assert "sk-abcdefghijklmnop" not in ce._redact("key=sk-abcdefghijklmnop")
    assert "ghp_ABCDEFGHIJKLMNOPQRST" not in ce._redact("token ghp_ABCDEFGHIJKLMNOPQRST")
    assert "hunter2" not in ce._redact("password: hunter2")
    assert len(ce._redact("x" * 500)) == 200
    assert "\n" not in ce._redact("a\nb")


def _git_call_literals(src: str) -> list[list[str]]:
    """AST 取出所有 `_git([...])` 调用的字面量参数（f-string / 星号 / 表达式以占位符表示）。

    刻意不用字符串匹配：模块 docstring 里就写着「禁止 git add -A」，
    用 `"add -A" in src` 会被自己的文档命中（自我指涉的假红）。
    """
    found: list[list[str]] = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_git" and node.args and isinstance(node.args[0], ast.List)):
            continue
        lits: list[str] = []
        for e in node.args[0].elts:
            if isinstance(e, ast.Constant) and isinstance(e.value, str):
                lits.append(e.value)
            elif isinstance(e, ast.JoinedStr):
                lits.append("<fstring>")
            elif isinstance(e, ast.Starred):
                lits.append("*<expr>")
            else:
                lits.append("<expr>")
        found.append(lits)
    return found


def test_source_bans_unscoped_staging_and_auto_merge():
    """结构性约束：不得无范围暂存（`add -A`）、不得自动合并/推送。"""
    calls = _git_call_literals(Path(ce.__file__).read_text(encoding="utf-8"))
    assert calls, "未解析到任何 _git([...]) 调用——判据失效（AST 口径过期）"

    for args in calls:
        assert "-A" not in args, f"出现无范围暂存：{args}"
        assert args[0] not in ("merge", "push"), f"出现落地动作：{args}"

    adds = [a for a in calls if a and a[0] == "add"]
    assert adds, "未解析到暂存调用——判据失效"
    assert all("--" in a for a in adds), f"暂存必须显式限定路径：{adds}"
