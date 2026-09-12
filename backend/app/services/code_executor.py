"""C 类代码执行器（docs/summary/ai-evolution.md P1-⑤）——AI 自主改代码，最危险能力。

安全设计（纵深防御，任何一层拒绝即终止）：
1. **白名单**：仅 backend/app/** 与 backend/tests/** 的 ``*.py``（前端/脚本/文档 v1 明确
   deferred——node_modules 沙箱复杂度高且 LLM 改 TSX 风险更大，不冒进）。
2. **禁改清单**：migrations（DB 结构是全局红线）、config.py（配置面）、__init__ 导出面、
   conftest.py（测试基建）。命中即 rejected。
3. **工作区干净**：主工作区有未提交改动 → rejected（避免与人工改动冲突，merge 必须干净）。
4. **每日上限**：C 类每日至多 1 次（audit 表计数，跨议程重生成也稳）。
5. **git apply --check**：LLM 输出的 unified diff 必须能干净应用，否则 rejected。
6. **回归门禁**：沙箱内 pytest 全量 + pyflakes（600s 超时），任一失败 → 丢弃沙箱。
7. **commit/丢弃**：门禁过 → 沙箱分支独立 commit（留 diffstat）→ ff-only 合并回主分支；
   失败 → worktree/分支全清，主分支零接触。
8. **留痕**：每步结果进议程 item.result；审计记 commit hash 与文件清单。

v1 明确不做的：前端改动、migrations、跨仓库、自动 push（合并只在本地）。
"""
from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path

from app.core.config import settings
from app.services.evolution import PROJECT_ROOT

log = __import__("logging").getLogger(__name__)

#: 文件白名单：仅这些前缀下的 .py 可被 C 类修改
ALLOWED_PREFIXES = ("backend/app/", "backend/tests/")
ALLOWED_SUFFIX = ".py"

#: 禁改清单（即使落在白名单目录内）：DB 迁移、配置面、包导出面、测试基建
DENIED_SUBSTRINGS = (
    "/migrations/", "app/core/config.py", "app/core/db.py",
    "conftest.py", "/__init__.py", "app/core/ttl_cache.py",
)

#: 门禁超时（秒）：全量 pytest 是大头
GATE_TIMEOUT_SECONDS = 600

#: 每日 C 类上限（防 AI 高频自改代码；audit 表计数）
C_DAILY_MAX = 1

MAX_FILES = 3
MAX_CONTEXT_LINES = 160


# ---------------------------------------------------------------- 预检


def _validate_files(files: list[str]) -> str | None:
    """返回拒绝原因（None=通过）。顺序：数量 → 后缀 → 禁改清单 → 白名单。"""
    if not files:
        return "未提供目标文件（files 为空）"
    if len(files) > MAX_FILES:
        return f"文件数 {len(files)} 超上限 {MAX_FILES}"
    for f in files:
        norm = f if f.startswith("backend/") else f"backend/{f.lstrip('/')}"
        if not norm.endswith(ALLOWED_SUFFIX):
            return f"{f}：仅允许 .py（前端/脚本/文档 v1 暂缓）"
        # 禁改清单先于白名单：migrations 等是全局红线，语义比"不在白名单"更准
        if any(d in f"/{norm}" for d in DENIED_SUBSTRINGS):
            return f"{f}：命中禁改清单（迁移/配置/导出面/测试基建）"
        if not any(norm.startswith(p) for p in ALLOWED_PREFIXES):
            return f"{f}：不在白名单（backend/app、backend/tests 之外）"
    return None


def _git(args: list[str], cwd: Path, timeout: int = 60) -> tuple[int, str]:
    """跑 git 命令，返回 (rc, 输出)。不抛异常（调用方按 rc 判断）。"""
    try:
        p = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"git {' '.join(args[:2])} 超时 {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


def _main_worktree_clean(root: Path) -> bool:
    rc, out = _git(["status", "--porcelain"], root)
    return rc == 0 and out == ""


def _c_executed_today(session_factory) -> int:
    """今日已执行的 C 类数（audit 计数；跨 agenda 重生成也稳）。

    cutoff 必须用 datetime 对象（evolution._bj_cutoff_today，北京 0 点）——SQLite 存
    "YYYY-MM-DD HH:MM:SS" 空格分隔，与 "…T00:00:00" 字符串比较恒 False。
    """
    from sqlalchemy import select

    from app.models.agent import AgentAudit
    from app.services.evolution import _bj_cutoff_today

    with session_factory() as db:
        rows = db.execute(
            select(AgentAudit).where(
                AgentAudit.action == "code.apply",
                AgentAudit.at >= _bj_cutoff_today(),
            )
        ).scalars().all()
        return len(rows)


# ---------------------------------------------------------------- LLM patch 生成

_PATCH_SYSTEM = (
    "你是代码修改器。给定目标文件的当前内容与修改要求，输出**一个 unified diff**"
    "（git apply 兼容：a/<path> b/<path> 前缀、相对仓库根、3 行上下文）。"
    "只输出 diff 本身，不要 markdown 围栏、不要解释文字。改动必须最小化——"
    "只改达成要求所需的行。禁止修改 import 结构以外的任何无关代码。"
)


def _read_context(root: Path, files: list[str]) -> str:
    parts: list[str] = []
    for f in files:
        p = root / f
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        shown = lines[:MAX_CONTEXT_LINES]
        tail = f"\n…（截断，共 {len(lines)} 行）" if len(lines) > MAX_CONTEXT_LINES else ""
        parts.append(f"===== {f} ({len(lines)} 行) =====\n" + "\n".join(shown) + tail)
    return "\n\n".join(parts)


def _extract_diff(raw: str) -> str | None:
    """从 LLM 输出提取 unified diff（容忍 markdown 围栏）。"""
    text = raw.strip()
    m = re.search(r"```(?:diff)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    start = None
    for i, line in enumerate(text.splitlines()):
        if line.startswith("diff --git ") or line.startswith("--- a/"):
            start = i
            break
    if start is None:
        return None
    diff = "\n".join(text.splitlines()[start:]).strip()
    return diff or None


def _llm_patch(item: dict, context: str) -> str:
    """读上下文 + 修改要求 → unified diff（同步：调用链整体在 to_thread 里，阻塞无碍）。"""
    from app.core.config import settings
    from app.core.llm_client import chat_completion

    user = (
        f"修改要求：{item.get('action')}\n"
        f"发现：{item.get('finding')}\n"
        f"预期效果：{item.get('expected_effect')}\n\n"
        f"目标文件当前内容：\n{context}"
    )
    return chat_completion(
        base_url=settings.review_llm_base_url,
        api_key=settings.review_llm_api_key,
        model=settings.review_llm_model,
        messages=[{"role": "system", "content": _PATCH_SYSTEM},
                  {"role": "user", "content": user}],
        provider=settings.llm_provider,
        cli_path=settings.llm_cli_path,
        timeout=180.0,
    )


# ---------------------------------------------------------------- 门禁


def _gate_commands(worktree: Path, changed: list[str]) -> list[tuple[Path, list[str]]]:
    """回归门禁命令（C 类 v1 仅 .py：pytest 全量 + pyflakes；可注入便于测试）。

    解释器：显式配置 agent_venv_python > 主工作区 backend/.venv（绝对路径可跨
    worktree 用）> 系统 python3。
    """
    pytest_bin = settings.agent_venv_python or str(PROJECT_ROOT / "backend" / ".venv" / "bin" / "python")
    if not Path(pytest_bin).exists():
        pytest_bin = "python3"
    cmds: list[tuple[Path, list[str]]] = [
        (worktree / "backend", [pytest_bin, "-m", "pytest", "-q",
                                "--basetemp=/tmp/pytest-evo-sandbox"]),
        (worktree / "backend", [pytest_bin, "-m", "pyflakes", "app", "tests"]),
    ]
    return cmds


def _run_gate(worktree: Path, changed: list[str]) -> tuple[bool, str]:
    """跑门禁；返回 (ok, 摘要)。任一失败即 False。"""
    for cwd, cmd in _gate_commands(worktree, changed):
        try:
            p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                               timeout=GATE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return False, f"门禁超时（>{GATE_TIMEOUT_SECONDS}s）：{' '.join(cmd[1:3])}"
        if p.returncode != 0:
            tail = (p.stdout + p.stderr).strip().splitlines()
            return False, f"门禁失败 rc={p.returncode}（{' '.join(cmd[1:3])}）：{tail[-1][:200] if tail else ''}"
    return True, "pytest 全量 + pyflakes 全绿"


# ---------------------------------------------------------------- 主流程


def execute_c_item(item: dict, session_factory, agenda_date: str,
                   *, repo_root: Path | None = None) -> dict:
    """C 类执行主流程：预检 → 沙箱 → LLM patch → apply --check → 门禁 → commit → 合并。"""
    root = repo_root or PROJECT_ROOT
    files = [str(f) for f in (item.get("files") or [])]
    if not settings.agent_code_change_enabled:
        return {**item, "status": "deferred",
                "result": "C 类代码执行器已关闭（ASHARE_AGENT_CODE_CHANGE=0）"}

    # 1) 预检（便宜检查在前：文件合法性 → 每日上限 → 工作区干净）
    reason = _validate_files(files)
    if reason:
        return {**item, "status": "rejected", "result": reason}
    if _c_executed_today(session_factory) >= C_DAILY_MAX:
        return {**item, "status": "deferred", "result": f"今日 C 类代码改动已达上限（{C_DAILY_MAX}）"}
    if not _main_worktree_clean(root):
        return {**item, "status": "deferred",
                "result": "主工作区有未提交改动（AI 改码要求干净工作区，避免与人工改动冲突）"}

    # 2026-09-08 用户指令：改动前必须先创建任务（留痕任务中心可见）
    from app.services.agent_tasks import record_mutation, update_mutation_result

    mutation_id = record_mutation(
        source="agenda", kind="code_change",
        summary=f"C类代码改动：{item.get('finding', '')[:60]}（files={files}）",
        detail={"agenda_date": agenda_date},
    )

    # 2) 沙箱（worktree + 独立分支）
    tag = f"{agenda_date.replace('-', '')}-{uuid.uuid4().hex[:6]}"
    branch = f"evolution/{tag}"
    sandbox = Path("/tmp") / f"evo-sandbox-{tag}"
    rc, out = _git(["worktree", "add", "-b", branch, str(sandbox), "HEAD"], root)
    if rc != 0:
        update_mutation_result(mutation_id, "failed", f"建沙箱失败：{out[:150]}")
        return {**item, "status": "failed", "result": f"建沙箱失败：{out[:150]}"}

    try:
        # 3) 读上下文 → LLM diff
        context = _read_context(sandbox, files)
        raw = _llm_patch(item, context)
        diff = _extract_diff(raw or "")
        if not diff:
            return {**item, "status": "rejected", "result": "LLM 未产出可识别的 unified diff"}

        # 4) git apply --check（合法性与冲突预检；patch 落沙箱文件供 git 读）
        patch_file = sandbox / ".evo.patch"
        patch_file.write_text(diff + "\n", encoding="utf-8")
        rc, out = _git(["apply", "--check", str(patch_file)], sandbox)
        if rc != 0:
            return {**item, "status": "rejected", "result": f"diff 无法干净应用：{out[:200]}"}
        rc, out = _git(["apply", str(patch_file)], sandbox)
        if rc != 0:
            update_mutation_result(mutation_id, "failed", f"diff 应用失败：{out[:200]}")
            return {**item, "status": "failed", "result": f"diff 应用失败：{out[:200]}"}

        # 5) 回归门禁
        ok, gate = _run_gate(sandbox, files)
        if not ok:
            update_mutation_result(mutation_id, "failed", f"回归门禁拦截：{gate}")
            return {**item, "status": "rejected", "result": f"回归门禁拦截：{gate}"}

        # 6) 沙箱分支独立 commit（留痕）
        rc, out = _git(["add", "-A"], sandbox)
        rc, out = _git(["commit", "-m",
                        f"evolution: {item.get('finding', '')[:80]} (agenda {agenda_date})"], sandbox)
        if rc != 0:
            update_mutation_result(mutation_id, "failed", f"沙箱 commit 失败：{out[:150]}")
            return {**item, "status": "failed", "result": f"沙箱 commit 失败：{out[:150]}"}
        rc, commit = _git(["rev-parse", "--short", "HEAD"], sandbox)
        rc, diffstat = _git(["diff", "--stat", "HEAD~1..HEAD"], sandbox)

        # 7) ff-only 合并回主分支（主工作区在预检时已确认干净）
        rc, out = _git(["merge", "--ff-only", branch], root)
        if rc != 0:
            update_mutation_result(mutation_id, "failed", f"合并失败（改动保留在分支 {branch}）：{out[:150]}")
            return {**item, "status": "failed",
                    "result": f"合并失败（改动保留在分支 {branch}）：{out[:150]}"}

        # 8) 审计
        with session_factory() as db:
            from app.models.agent import AgentAudit

            db.add(AgentAudit(actor="ai", action="code.apply",
                              target=f"{commit}:{','.join(files)}"))
            db.commit()
        result = f"已合入 {commit}（{gate}）；文件：{', '.join(files)}"
        # 2026-09-08 用户指令：策略改动必须以回测数据为依据——涉及 picks/ 的
        # 合入自动跑回放对比（与上一份基线量化对照）；回放失败/超时只附告警，
        # 不回滚已过门禁的合入（回滚由 experiments 30 日守护承担）。
        replay_block = None
        if any(f.startswith("backend/app/picks/") or "/picks/" in f for f in files):
            try:
                from app.services.replay_gate import run_comparison

                replay_block = run_comparison(days=10, commit=str(commit), force=True)
                result += (
                    f"；回放对比[{replay_block.get('verdict')}]: "
                    + replay_block.get("report", "").split("\n\n")[0].replace("\n", " ")
                )
            except Exception as exc:  # noqa: BLE001  回放失败不吞合入结果
                log.warning("replay comparison failed: %s", exc)
                replay_block = {"ok": False, "verdict": "error", "report": str(exc)}
        update_mutation_result(mutation_id, "succeeded", result)
        return {**item, "status": "executed",
                "result": result,
                "mutation_task_id": mutation_id,
                "replay_comparison": replay_block,
                "commit": commit, "files_changed": files, "diffstat": diffstat[:400]}
    finally:
        _git(["worktree", "remove", "--force", str(sandbox)], root, timeout=30)
        _git(["branch", "-D", branch], root, timeout=30)
        _cleanup_patch(sandbox)


def _cleanup_patch(sandbox: Path) -> None:
    with_dir = Path(str(sandbox))
    if with_dir.exists():
        import contextlib

        with contextlib.suppress(OSError):
            (with_dir / ".evo.patch").unlink(missing_ok=True)
