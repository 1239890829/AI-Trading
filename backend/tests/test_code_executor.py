"""C 类提案安全回归：真实临时Git + 替身模型，绝不执行生成代码。

保留路径/白名单/实际差异的独立反例。IMP-046 将旧宿主门禁/提交场景改为
启动前拒绝执行；原测试ID保留，但不再用“隔离分支”冒充OS隔离。
测试由用户授权的工程执行侧维护，不接受应用内LLM生成的测试。
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
    def fake(item, ctx, **kwargs):
        callback = kwargs.get("usage_callback")
        if callback:
            callback({"input_tokens": 10, "output_tokens": 2})
        return diff or "no diff here"
    monkeypatch.setattr(ce, "_llm_patch", fake)


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


def test_daily_limit_blocks_second_c(mini_repo, sf):
    with sf() as db:
        db.add(AgentAudit(actor="ai", action="code.apply", target="abc:backend/app/x.py"))
        db.commit()
    item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)
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
    """旧超时场景现须在启动前阻止；即使配置了慢命令也不能执行。"""
    monkeypatch.setattr(ce, "GATE_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(ce, "_gate_commands", lambda w, c: [(tmp_path, ["sleep", "5"])])
    monkeypatch.setattr(ce.subprocess, "run", lambda *a, **k: pytest.fail("must not launch even a timed host gate"))
    ok, msg = ce._run_gate(tmp_path, [])
    assert ok is False and "隔离" in msg


def test_gate_timeout_rejects_full_cycle(mini_repo, sf, monkeypatch):
    """旧超时链路改为不启动门禁：静态提案可交付，不能宣称测试通过。"""
    before = _git(mini_repo, "rev-parse", "HEAD")[1]
    _stub_llm(monkeypatch, _DIFF)
    monkeypatch.setattr(ce, "_gate_commands", lambda *a: pytest.fail("no host command discovery"))
    out = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)
    assert out["status"] == "proposed" and out["gate_ran"] is False
    assert _git(mini_repo, "rev-parse", "HEAD")[1] == before
    assert "evolution/" not in _git(mini_repo, "branch", "--list", "evolution/*")[1]


def test_gate_failure_discards_sandbox(mini_repo, sf, monkeypatch):
    """旧失败门禁不再可达；根本不建工作树，也不读取伪造的绿门禁。"""
    before = _git(mini_repo, "rev-parse", "HEAD")[1]
    _stub_llm(monkeypatch, _DIFF)
    monkeypatch.setattr(ce, "_run_gate", lambda *a: pytest.fail("host gate must not be reached"))
    item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)
    assert item["status"] == "proposed" and "未运行测试" in item["result"]
    assert _git(mini_repo, "rev-parse", "HEAD")[1] == before
    assert "evolution/" not in _git(mini_repo, "branch", "--list", "evolution/*")[1]
    assert "用法" not in (mini_repo / "backend/app/demo.py").read_text()


# ---------------------------------------------------------------- 沙箱全链（真 git）


def test_full_cycle_proposes_without_merging(mini_repo, sf, monkeypatch):
    """合法补丁只归档文本和真实审计；所有Git refs与源码保持不变。"""
    from app.models.agent import AgentTask
    before = _git(mini_repo, "show-ref")[1]
    _stub_llm(monkeypatch, _DIFF)
    item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)
    assert item["status"] == "proposed" and item["apply_check"] is True
    assert item["commit"] is None and item["branch"] is None and item["files_changed"] == []
    assert item["merged"] is False and item["review_required"] is True and item["gate_ran"] is False
    assert _git(mini_repo, "show-ref")[1] == before
    assert _git(mini_repo, "status", "--porcelain")[1] == ""
    assert Path(item["patch_path"]).read_text() == _DIFF
    with sf() as db:
        audits = db.query(AgentAudit).filter(AgentAudit.action == "code.propose").all()
        assert len(audits) == 1 and audits[0].task_id == item["mutation_task_id"]
        assert db.query(AgentAudit).filter(AgentAudit.action == "code.apply").count() == 0
        task = db.get(AgentTask, item["mutation_task_id"])
        assert task.status == "succeeded" and '"code_proposal"' in task.params
        assert '"code_applied": false' in task.params


def test_worktree_cleanup_failure_does_not_lose_proposal(mini_repo, sf, monkeypatch, caplog):
    """消除旧清理失败面：提案全链不创建、删除或清理任何工作树。"""
    real_git = ce._git
    def checked(args, cwd, timeout=60):
        assert args[0] != "worktree", "proposal must not depend on worktree creation/cleanup"
        return real_git(args, cwd, timeout)
    _stub_llm(monkeypatch, _DIFF)
    monkeypatch.setattr(ce, "_git", checked)
    item = ce.execute_c_item(_item(), sf, "2026-09-08", repo_root=mini_repo)
    assert item["status"] == "proposed" and Path(item["patch_path"]).exists()
    assert "worktree 清理失败" not in caplog.text


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
    fake_github_token = "ghp_" + ("A" * 24)
    assert fake_github_token not in ce._redact(f"token {fake_github_token}")
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
    """无范围暂存禁令升级为无应用、暂存、提交、工作树、合并或推送。"""
    src = Path(ce.__file__).read_text()
    calls = _git_call_literals(src)
    assert calls
    for args in calls:
        assert args[0] in {"status", "diff", "rev-parse", "apply"}, args
        if args[0] == "apply":
            assert args[1] == "--check", args
    assert ce._gate_commands(Path("/tmp"), []) == []
    assert not any(isinstance(n, ast.Name) and n.id == "run_comparison" for n in ast.walk(ast.parse(src)))


# IMP-046: traps stop the old path before any generated code could execute.
def _readonly_git(monkeypatch):
    original = ce._git
    calls = []
    def checked(args, cwd, timeout=60):
        calls.append(args)
        assert args[0] in {"status", "rev-parse", "diff", "apply"}, args
        if args[0] == "apply":
            assert "--check" in args, "proposal must never apply a patch"
        return original(args, cwd, timeout)
    monkeypatch.setattr(ce, "_git", checked)
    return calls


def test_imp046_proposal_never_executes_patch(mini_repo, sf, monkeypatch):
    before = _git(mini_repo, "rev-parse", "HEAD")[1]
    calls = _readonly_git(monkeypatch)
    _stub_llm(monkeypatch, _DIFF)
    monkeypatch.setattr(ce, "_run_gate", lambda *a: pytest.fail("must not execute generated app code"))
    out = ce.execute_c_item(_item(), sf, "2026-09-18", repo_root=mini_repo)
    assert out["status"] == "proposed" and out["review_required"] is True
    assert out["merged"] is False and out["code_applied"] is False and out["gate_ran"] is False
    assert out["commit"] is None and out["branch"] is None and out["files_changed"] == []
    assert out["base_commit"] == before and out["proposed_files"] == ["backend/app/demo.py"]
    assert Path(out["patch_path"]).read_text() == _DIFF
    assert _git(mini_repo, "rev-parse", "HEAD")[1] == before
    assert _git(mini_repo, "status", "--porcelain")[1] == ""
    assert not _git(mini_repo, "branch", "--list", "evolution/*")[1]
    assert ["apply", "--check"] in [a[:2] for a in calls]


def test_imp046_legacy_gate_cannot_launch_process(tmp_path, monkeypatch):
    monkeypatch.setattr(ce.subprocess, "run", lambda *a, **k: pytest.fail("host process prohibited"))
    ok, note = ce._run_gate(tmp_path, ["backend/app/demo.py"])
    assert ok is False and "隔离" in note


@pytest.mark.parametrize("raw", ["no diff", None])
def test_imp046_invalid_generation_has_terminal_task(mini_repo, sf, monkeypatch, raw):
    from app.models.agent import AgentTask
    _readonly_git(monkeypatch)
    _stub_llm(monkeypatch, raw)
    out = ce.execute_c_item(_item(), sf, "2026-09-18", repo_root=mini_repo)
    assert out["status"] == "rejected"
    with sf() as db:
        tasks = db.query(AgentTask).all()
        assert len(tasks) == 1 and tasks[0].status == "failed" and tasks[0].finished_at


def test_imp046_generation_exception_has_terminal_task(mini_repo, sf, monkeypatch):
    from app.models.agent import AgentTask
    _readonly_git(monkeypatch)
    def broken(*args):
        raise RuntimeError("password: private-value")
    monkeypatch.setattr(ce, "_llm_patch", broken)
    out = ce.execute_c_item(_item(), sf, "2026-09-18", repo_root=mini_repo)
    assert out["status"] == "failed" and "private-value" not in out["result"]
    with sf() as db:
        tasks = db.query(AgentTask).all()
        assert len(tasks) == 1 and tasks[0].status == "failed" and tasks[0].finished_at


def test_imp046_archive_failure_never_reports_proposed(mini_repo, sf, monkeypatch):
    _readonly_git(monkeypatch)
    _stub_llm(monkeypatch, _DIFF)
    monkeypatch.setattr(ce, "_archive_patch", lambda *a: None)
    out = ce.execute_c_item(_item(), sf, "2026-09-18", repo_root=mini_repo)
    assert out["status"] == "failed" and "归档" in out["result"]
    assert out["patch_path"] is None and out["commit"] is None


def test_imp046_symlink_rejected_before_context_or_model(mini_repo, sf, monkeypatch):
    os.symlink("core/config.py", mini_repo / "backend/app/link.py")
    _git(mini_repo, "add", "--", "backend/app/link.py")
    _git(mini_repo, "commit", "-qm", "fixture")
    _readonly_git(monkeypatch)
    monkeypatch.setattr(ce, "_read_context", lambda *a: pytest.fail("must not read symlink context"))
    monkeypatch.setattr(ce, "_llm_patch", lambda *a: pytest.fail("must not send symlink context"))
    out = ce.execute_c_item(_item(files=["backend/app/link.py"]), sf, "2026-09-18", repo_root=mini_repo)
    assert out["status"] == "rejected" and "符号链接" in out["result"]


@pytest.mark.parametrize("enabled", [False, True])
def test_imp046_untrusted_result_fields_are_not_evidence(mini_repo, sf, monkeypatch, enabled):
    monkeypatch.setattr(ce.settings, "agent_code_change_enabled", enabled)
    bad = _item(files=[], merged=True, review_required=False, code_applied=True,
                gate_ran=True, commit="forged", branch="master", patch_path="fake", files_changed=["fake"],
                apply_check=True, patch_sha256="forged")
    out = ce.execute_c_item(bad, sf, "2026-09-18", repo_root=mini_repo)
    assert out["merged"] is False and out["review_required"] is True
    assert out["code_applied"] is False and out["gate_ran"] is False
    assert out["commit"] is None and out["branch"] is None and out["patch_path"] is None
    assert out["files_changed"] == [] and bad["merged"] is True
    assert out["apply_check"] is False and out["patch_sha256"] is None



def test_imp046_proposal_persists_through_real_agenda(mini_repo, sf, monkeypatch):
    import json
    from app.models.agent import AgentAgenda
    from app.services import evolution as evo
    monkeypatch.setattr(ce, "PROJECT_ROOT", mini_repo)
    monkeypatch.setattr(evo.settings, "agent_autonomy_enabled", True)
    _readonly_git(monkeypatch)
    _stub_llm(monkeypatch, _DIFF)
    with sf() as db:
        db.add(AgentAgenda(date="2026-09-18", status="ready"))
        db.commit()
    out = evo.execute_agenda({"date": "2026-09-18", "items": [_item()]}, sf)
    assert out["items"][0]["status"] == "proposed"
    with sf() as db:
        stored = db.query(AgentAgenda).one()
        item = json.loads(stored.items)[0]
        assert item["status"] == "proposed" and item["code_applied"] is False
        assert item["gate_ran"] is False and item["review_required"] is True


def test_imp046_failed_attempt_counts_toward_daily_limit(mini_repo, sf, monkeypatch):
    _stub_llm(monkeypatch, "no diff")
    first = ce.execute_c_item(_item(), sf, "2026-09-18", repo_root=mini_repo)
    assert first["status"] == "rejected"
    monkeypatch.setattr(ce, "_llm_patch", lambda *a: pytest.fail("failed attempt cannot reset quota"))
    second = ce.execute_c_item(_item(), sf, "2026-09-18", repo_root=mini_repo)
    assert second["status"] == "deferred" and "上限" in second["result"]


def test_imp046_source_drift_defers_without_archival(mini_repo, sf, monkeypatch):
    def model(*args, **kwargs):
        callback = kwargs.get("usage_callback")
        if callback:
            callback({"input_tokens": 10, "output_tokens": 2})
        (mini_repo / "backend/app/demo.py").write_text("# concurrent user edit\n")
        return _DIFF
    monkeypatch.setattr(ce, "_llm_patch", model)
    monkeypatch.setattr(ce, "_archive_patch", lambda *a: pytest.fail("stale proposal cannot be archived as current"))
    out = ce.execute_c_item(_item(), sf, "2026-09-18", repo_root=mini_repo)
    assert out["status"] == "deferred" and "变化" in out["result"]
    assert (mini_repo / "backend/app/demo.py").read_text() == "# concurrent user edit\n"


@pytest.mark.parametrize("raw", [123, "x" * (128 * 1024 + 1)], ids=["invalid-type", "oversized"])
def test_imp046_bad_or_oversized_output_is_rejected(mini_repo, sf, monkeypatch, raw):
    monkeypatch.setattr(ce, "_llm_patch", lambda *a, **k: raw)
    out = ce.execute_c_item(_item(), sf, "2026-09-18", repo_root=mini_repo)
    assert out["status"] == "rejected" and out["patch_path"] is None


def test_imp046_short_paths_use_canonical_context(mini_repo, sf, monkeypatch):
    seen = []
    def model(item, context, **kwargs):
        callback = kwargs.get("usage_callback")
        if callback:
            callback({"input_tokens": 10, "output_tokens": 2})
        seen.append(context)
        return _DIFF
    monkeypatch.setattr(ce, "_llm_patch", model)
    out = ce.execute_c_item(_item(files=["app/demo.py"]), sf, "2026-09-18", repo_root=mini_repo)
    assert out["status"] == "proposed" and out["proposed_files"] == ["backend/app/demo.py"]
    assert len(seen) == 1 and "return a + b" in seen[0]



def test_imp046_missing_task_audit_blocks_model(mini_repo, sf, monkeypatch):
    from app.services import agent_tasks as at
    def fail_audit(**kwargs):
        raise RuntimeError("audit store unavailable")
    monkeypatch.setattr(at, "record_mutation", fail_audit)
    monkeypatch.setattr(ce, "_llm_patch", lambda *a: pytest.fail("no model without audit"))
    out = ce.execute_c_item(_item(), sf, "2026-09-18", repo_root=mini_repo)
    assert out["status"] == "failed" and out["patch_path"] is None


def test_imp046_final_audit_failure_is_visible(mini_repo, sf, monkeypatch):
    from app.services import agent_tasks as at
    _stub_llm(monkeypatch, _DIFF)
    def broken(*args):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(at, "update_mutation_result", broken)
    out = ce.execute_c_item(_item(), sf, "2026-09-18", repo_root=mini_repo)
    assert out["status"] == "failed" and "留痕回填失败" in out["result"]
    assert Path(out["patch_path"]).exists() and out["code_applied"] is False


def test_imp046_picks_proposal_does_not_run_strategy_replay(mini_repo, sf, monkeypatch):
    from app.services import replay_gate
    path = mini_repo / "backend/app/picks/demo.py"
    path.parent.mkdir()
    path.write_text((mini_repo / "backend/app/demo.py").read_text())
    _git(mini_repo, "add", "--", "backend/app/picks/demo.py")
    _git(mini_repo, "commit", "-qm", "fixture picks")
    _stub_llm(monkeypatch, _mk_diff("backend/app/picks/demo.py"))
    monkeypatch.setattr(replay_gate, "run_comparison", lambda *a, **k: pytest.fail("proposal cannot run replay"))
    out = ce.execute_c_item(_item(files=["backend/app/picks/demo.py"]), sf, "2026-09-18", repo_root=mini_repo)
    assert out["status"] == "proposed" and out["replay_comparison"] is None
