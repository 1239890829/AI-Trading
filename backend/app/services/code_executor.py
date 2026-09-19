"""C 类代码提案（IMP-046）：默认关闭，开启后也只生成待审补丁。

允许：读取明确文件、生成文本 diff、路径授权、git apply --check 静态检查、
归档与审计。禁止：应用补丁、创建工作树/分支、提交/合并/推送、宿主 pytest、
pyflakes 或策略回放。worktree 不是 OS 沙箱；只禁改 tests 不能阻止 app 导入执行。

路径/白名单/受保护面与历史审计继续保留。静态检查不代表代码正确或安全，
proposed 不是 executed/applied；真正实施由获准开发者走 codex/* → PR → CI → 审查。
本模块不提供可由配置开关开启的宿主执行路径。模型预算、隔离运行器和参数晋级
仍由 IMP-046 的其它切片处理；本轮不增加新模型或修改运行开关。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from app.core.config import settings
from app.services.evolution import PROJECT_ROOT

log = __import__("logging").getLogger(__name__)

#: 文件白名单：仅这些前缀下的 .py 可被 C 类修改。
#: ⚠️ `backend/tests/` 于 2026-09-15 加固时**移出**——测试在宿主解释器执行，见模块 docstring 第 1 条。
ALLOWED_PREFIXES = ("backend/app/",)
ALLOWED_SUFFIX = ".py"

#: 禁改清单（即使落在白名单目录内）：DB 迁移、配置面、包导出面、测试基建
DENIED_SUBSTRINGS = (
    "/migrations/", "app/core/config.py", "app/core/db.py",
    "conftest.py", "/__init__.py", "app/core/ttl_cache.py",
)

#: 受保护目录段（**任意层级**出现即命中）：CI 工作流、治理、文档、技能、迁移、测试
_PROTECTED_DIR_SEGMENTS = frozenset({
    ".github", ".gitlab", ".circleci", ".workbuddy", ".workbuddy-ai", "artifacts",
    "docs", "skills", "migrations", "tests",
})

#: 受保护文件名（精确）
_PROTECTED_BASENAMES = frozenset({
    "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "README.md",
    "pyproject.toml", "setup.py", "setup.cfg", "alembic.ini",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "Dockerfile", "docker-compose.yml", "docker-compose.prod.yml",
})

#: 受保护文件名模式（fnmatch）
_PROTECTED_NAME_PATTERNS = (
    "requirements*.txt", "requirements*.lock", "tsconfig*.json",
    "next.config.*", "eslint.config.*", ".eslintrc*",
    "docker-compose*", "credentials*", "secrets*", ".env*",
)

#: 受保护后缀（凭据 / 工作流 / 配置类）
_PROTECTED_SUFFIXES = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".yml", ".yaml", ".toml", ".ini", ".lock",
})

#: 门禁超时（秒）：全量 pytest 是大头
GATE_TIMEOUT_SECONDS = 600  # 历史兼容，不再用于启动宿主进程
MAX_PATCH_BYTES = 128 * 1024

#: 每日 C 类上限（防 AI 高频自改代码；audit 表计数）
C_DAILY_MAX = 1

MAX_FILES = 3
MAX_CONTEXT_LINES = 160

#: patch 归档目录（**gitignored**；沙箱会被删除，patch 必须留在任务临时检出之外供人工审阅）
PATCH_ARCHIVE_DIR = PROJECT_ROOT / "artifacts" / "evolution-patches"


# ---------------------------------------------------------------- 脱敏


#: ⚠️ 顺序有意义：**具体形态先于通用键值对**。反例——`key: Bearer <token>` 若先跑通用
#: 规则，`\S+` 只吃掉 `Bearer` 就停，**token 会原样留下**（写测试时实测到的漏检）。
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)\b(api[_-]?key|apikey|token|secret|password|passwd|authorization)\b"
               r"\s*[:=]\s*(?:bearer\s+)?\S+"),
)


def _redact(text: object, limit: int = 200) -> str:
    """把外部输出压成单行并抹掉疑似凭据，再截断。

    审计要求「保留完整审计记录，但不得在日志中泄露凭据或完整敏感上下文」——
    门禁 / git 的输出会进 result 与审计表，必须先过这一层。
    """
    s = " ".join(str(text).split())
    for pat in _SECRET_PATTERNS:
        s = pat.sub("[REDACTED]", s)
    return s[:limit]


# ---------------------------------------------------------------- 路径判据


def _normalize_repo_path(raw: str) -> str:
    """把议程 / patch 给出的路径归一为**仓库根相对** POSIX 路径。

    ⚠️ 兼容既有语义（`_validate_files` 的判据与既有测试依赖它）：未以 ``backend/``
    开头的相对路径一律视为 backend 下（议程历史上写的就是 backend 内的短路径）。
    """
    s = str(raw).strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    s = s.lstrip("/")
    if not s.startswith("backend/"):
        s = f"backend/{s}"
    return s


def _path_shape_reason(raw: str) -> str | None:
    """路径**形态**判据（纯字符串，不依赖仓库）。返回拒绝原因（None=通过）。"""
    s = str(raw).strip()
    if not s:
        return "路径为空"
    if "\\" in s:
        return "路径含反斜杠（仅接受 POSIX 相对路径）"
    if s.startswith("~"):
        return "路径含 ~ 展开（不接受 home 相对路径）"
    if re.match(r"^[A-Za-z]:", s):
        return "含 Windows 盘符（仅接受 POSIX 相对路径）"
    if s.startswith("/") or PurePosixPath(s).is_absolute():
        return "绝对路径不被允许（必须是仓库根相对路径）"
    if s.endswith("/"):
        return "目标是目录而非文件"
    parts = PurePosixPath(s).parts
    if any(p == ".." for p in parts):
        return "路径含 .. 穿越"
    if not parts:
        return "路径为空或为当前目录"
    return None


def _has_symlink_component(root: Path, rel: str) -> bool:
    """rel 的任一路径组件（含自身）在 root 内是符号链接 → True。

    只检查 root **之内**的组件：macOS 上 `/tmp` 本身是软链，若连 root 一起判会
    把所有沙箱误杀（假红）；真正的逃逸形态是「仓库内某一段被指向仓库外」。
    """
    cur = root
    for part in PurePosixPath(rel).parts:
        if part in ("", ".", "/"):
            continue
        cur = cur / part
        try:
            if cur.is_symlink():
                return True
        except OSError:
            return True  # 判不出来时按不安全处理（fail-closed）
    return False


def _escape_reason(root: Path, rel: str) -> str | None:
    """canonical 校验：必须落在仓库根内，且路径上不得有符号链接。"""
    try:
        target = (root / rel).resolve()
        base = root.resolve()
    except (OSError, RuntimeError):
        return "路径无法解析"
    if target != base and not target.is_relative_to(base):
        return "路径逃出仓库根（canonical 解析后不在仓库内）"
    if _has_symlink_component(root, rel):
        return "路径上存在符号链接（可能被用于逃逸到仓库外）"
    return None


def _is_protected(norm: str) -> bool:
    """受保护面（工作流 / 依赖配置 / 迁移 / 凭据 / 治理文档）。norm 为仓库根相对路径。"""
    p = PurePosixPath(norm)
    if any(seg in _PROTECTED_DIR_SEGMENTS for seg in p.parts[:-1]):
        return True
    name = p.name
    if name in _PROTECTED_BASENAMES:
        return True
    if p.suffix.lower() in _PROTECTED_SUFFIXES:
        return True
    return any(fnmatch(name, pat) for pat in _PROTECTED_NAME_PATTERNS)


# ---------------------------------------------------------------- 预检


def _validate_files(files: list[str]) -> str | None:
    """返回拒绝原因（None=通过）。顺序：数量 → 形态 → 后缀 → 禁改 → 白名单 → 受保护。"""
    if not files:
        return "未提供目标文件（files 为空）"
    if len(files) > MAX_FILES:
        return f"文件数 {len(files)} 超上限 {MAX_FILES}"
    for f in files:
        shape = _path_shape_reason(f)
        if shape:
            return f"{f}：{shape}"
        norm = _normalize_repo_path(f)
        if not norm.endswith(ALLOWED_SUFFIX):
            return f"{f}：仅允许 .py（前端/脚本/文档 v1 暂缓）"
        # 禁改清单先于白名单：migrations 等是全局红线，语义比"不在白名单"更准
        if any(d in f"/{norm}" for d in DENIED_SUBSTRINGS):
            return f"{f}：命中禁改清单（迁移/配置/导出面/测试基建）"
        if not any(norm.startswith(p) for p in ALLOWED_PREFIXES):
            return f"{f}：不在白名单（仅允许 backend/app 下的 .py；tests 在宿主执行，v1 禁止）"
        # 受保护面放在白名单之后：白名单外的路径本就已被拒，此层是**纵深防御**
        # （防未来白名单扩张时把工作流/凭据/治理文件一并放开）
        if _is_protected(norm):
            return f"{f}：命中受保护面（工作流/依赖配置/迁移/凭据/治理文档）"
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
    """今日提案尝试数：新 code.propose 与历史 code.apply 一起计入。

    在模型请求前记尝试，失败也占上限；不把未知请求当作没有消费。
    这是现有每日限制，不等于全局模型预算或跨进程原子预留。
    """
    from sqlalchemy import select

    from app.models.agent import AgentAudit
    from app.services.evolution import _bj_cutoff_today

    with session_factory() as db:
        rows = db.execute(
            select(AgentAudit).where(
                AgentAudit.action.in_(("code.apply", "code.propose")),
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
    "**只允许修改上面列出的目标文件**：不得新增、删除、重命名任何文件。"
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


# ---------------------------------------------------------------- diff 级授权


@dataclass(frozen=True)
class _PatchEntry:
    """unified diff 中一个文件段。"""

    status: str       # modify | add | delete | rename | copy
    old_path: str     # 仓库根相对；/dev/null 时为空串
    new_path: str


_DIFF_GIT_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)\s*$")


def _strip_ab_prefix(s: str) -> str:
    s = s.strip()
    if s == "/dev/null":
        return s
    if s.startswith("a/") or s.startswith("b/"):
        return s[2:]
    return s


def _parse_patch(diff: str) -> list[_PatchEntry]:
    """解析 unified diff 的**全部**文件头。

    ⚠️ `---` / `+++` **只在 hunk 之外**解析：hunk 内的删除行若原文是 `-- x`，该行会被
    渲染成 `--- x`，若不区分就会把它当成文件头（误判路径）。
    """
    entries: list[_PatchEntry] = []
    seg_a = seg_b = ""
    seg_old: str | None = None
    seg_new: str | None = None
    seg_flag = "modify"
    in_hunk = False

    def flush() -> None:
        nonlocal seg_a, seg_b, seg_old, seg_new, seg_flag
        if not (seg_a or seg_b or seg_old or seg_new):
            return
        old = seg_old if seg_old is not None else seg_a
        new = seg_new if seg_new is not None else seg_b
        status = seg_flag
        if status == "modify":
            if seg_old == "/dev/null":
                status = "add"
            elif seg_new == "/dev/null":
                status = "delete"
            elif seg_a and seg_b and seg_a != seg_b:
                status = "rename"
        entries.append(_PatchEntry(
            status=status,
            old_path="" if old == "/dev/null" else old,
            new_path="" if new == "/dev/null" else new,
        ))
        seg_a = seg_b = ""
        seg_old = seg_new = None
        seg_flag = "modify"

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            in_hunk = False
            m = _DIFF_GIT_RE.match(line)
            if m:
                seg_a, seg_b = m.group("a"), m.group("b")
            else:
                # 退化形态（路径含空格/引号等）：整段保留，交由后续形态校验拒绝
                rest = line[len("diff --git "):].strip()
                seg_a = seg_b = rest
        elif line.startswith("@@"):
            in_hunk = True
        elif line.startswith("rename from ") or line.startswith("rename to "):
            seg_flag = "rename"
        elif line.startswith("copy from ") or line.startswith("copy to "):
            seg_flag = "copy"
        elif line.startswith("new file mode"):
            seg_flag = "add"
        elif line.startswith("deleted file mode"):
            seg_flag = "delete"
        elif not in_hunk and line.startswith("--- "):
            seg_old = _strip_ab_prefix(line[4:])
        elif not in_hunk and line.startswith("+++ "):
            seg_new = _strip_ab_prefix(line[4:])
    flush()
    return entries


def _validate_patch(diff: str, declared: list[str], root: Path) -> str | None:
    """应用**之前**的 diff 级授权。返回拒绝原因（None=通过）。

    审计要求逐条落地：未声明文件 / 绝对路径 / `..` 穿越 / 仓库外 / 符号链接逃逸 /
    重命名与删除 / 工作流·配置·迁移·凭据·治理文件。
    """
    entries = _parse_patch(diff)
    if not entries:
        return "patch 未解析出任何文件头（不是可识别的 unified diff）"

    approved = {_normalize_repo_path(f) for f in declared}
    seen: set[str] = set()

    for e in entries:
        label = e.new_path or e.old_path or "<未知路径>"

        if e.status != "modify":
            return (f"{label}：patch 含 {e.status} 操作，v1 仅允许修改既有文件"
                    f"（新增/删除/重命名/复制一律拒绝）")
        if not e.new_path:
            return f"{label}：patch 文件头缺少目标路径"
        if e.old_path != e.new_path:
            return f"{label}：patch 含重命名（{e.old_path} → {e.new_path}），v1 拒绝"

        shape = _path_shape_reason(e.new_path)
        if shape:
            return f"{label}：{shape}"

        norm = _normalize_repo_path(e.new_path)
        if norm in seen:
            return f"{label}：同一文件在 patch 中出现多次（构造可疑）"
        seen.add(norm)

        escape = _escape_reason(root, norm)
        if escape:
            return f"{label}：{escape}"

        if norm not in approved:
            return (f"{label}：patch 修改了议程**未声明**的文件"
                    f"（已声明={sorted(approved) or '无'}）")

        # 白名单 / 禁改 / 受保护：复用同一条判据（两套规则必然漂移）
        reason = _validate_files([norm])
        if reason:
            return reason

    if seen != approved:
        return (f"patch 覆盖集合与批准集合不一致（批准={sorted(approved)}；"
                f"patch 实际={sorted(seen)}）")

    return None


def _verify_applied(sandbox: Path, approved: list[str]) -> str | None:
    """应用**之后**以 git 为权威事实来源复核。返回拒绝原因（None=通过）。

    ⚠️ 判据与被判对象**不同源**：这里读的是 git 的 `diff --name-status`，不是本模块
    的 diff 解析结果——两者同源会「一致地少、谁都不报」。
    """
    rc, out = _git(["diff", "--name-status", "--no-renames"], sandbox)
    if rc != 0:
        return f"无法读取实际变更集合：{_redact(out, 150)}"

    actual: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        actual[parts[-1].strip()] = parts[0].strip()

    approved_set = {_normalize_repo_path(f) for f in approved}
    extra = sorted(set(actual) - approved_set)
    missing = sorted(approved_set - set(actual))
    if extra or missing:
        return (f"实际变更集合与批准集合不一致"
                f"（越界={extra or '无'}；未落地={missing or '无'}）")
    if not actual:
        return "未观察到任何实际变更（patch 为空操作）"
    for path, status in actual.items():
        if status != "M":
            return f"{path}：实际变更类型 {status} 不被允许（v1 仅允许 M=修改）"
    return None


# ---------------------------------------------------------------- 门禁


def _gate_commands(worktree: Path, changed: list[str]) -> list[tuple[Path, list[str]]]:
    """历史调用方兼容：没有已验证隔离运行器，不返回宿主执行命令。"""
    return []


def _run_gate(worktree: Path, changed: list[str]) -> tuple[bool, str]:
    """失败关闭：空命令不是全绿，应用内补丁不得在宿主导入或执行。"""
    return False, "未配置经验证的 OS 隔离运行器；宿主门禁禁止执行，补丁仅供审阅"


def _archive_patch(diff: str, tag: str) -> Path | None:
    """把 patch 落盘到归档目录（沙箱随后会被删除，patch 必须留在任务临时检出之外供人工审阅）。"""
    try:
        PATCH_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        path = PATCH_ARCHIVE_DIR / f"{tag}.patch"
        path.write_text(diff + "\n", encoding="utf-8")
        return path
    except OSError as exc:  # noqa: BLE001
        log.warning("patch 归档失败：%s", exc)
        return None


# ---------------------------------------------------------------- 主流程


def execute_c_item(item: dict, session_factory, agenda_date: str,
                   *, repo_root: Path | None = None) -> dict:
    """生成待审补丁并静态检查；无应用、测试、回放或 Git 写操作。"""
    root = repo_root or PROJECT_ROOT
    safe = {**item, "merged": False, "review_required": True, "code_applied": False,
            "gate_ran": False, "commit": None, "branch": None, "files_changed": [],
            "proposed_files": [], "base_commit": None, "patch_path": None,
            "mutation_task_id": None, "replay_comparison": None, "diffstat": "",
            "apply_check": False, "patch_sha256": None}

    def result(status: str, note: str, **fields) -> dict:
        return {**safe, "status": status, "result": note, **fields}

    if not settings.agent_code_change_enabled:
        return result("deferred", "C 类代码执行器已关闭（ASHARE_AGENT_CODE_CHANGE_ENABLED=0）："
                      "仅保留建议、议程或 patch 预览，不得修改、提交或合并代码")
    raw_files = item.get("files")
    files = [str(f) for f in raw_files] if isinstance(raw_files, list) else []
    reason = _validate_files(files)
    if reason:
        return result("rejected", reason)
    files = [_normalize_repo_path(f) for f in files]
    for name in files:
        reason = _escape_reason(root, name)
        if reason:
            return result("rejected", f"{name}：{reason}")
        if not (root / name).is_file():
            return result("rejected", f"{name}：目标文件不存在或不是普通文件")
    if _c_executed_today(session_factory) >= C_DAILY_MAX:
        return result("deferred", f"今日 C 类提案尝试已达上限（{C_DAILY_MAX}）")
    if not _main_worktree_clean(root):
        return result("deferred", "主工作区有未提交改动；提案需干净且稳定的基点，避免误读人工改动")
    rc, base = _git(["rev-parse", "HEAD"], root)
    if rc or not re.fullmatch(r"[0-9a-f]{40,64}", base):
        return result("deferred", "无法确认提案基点；不读取或生成补丁")
    safe.update(base_commit=base, proposed_files=files)
    tag = f"proposal-{uuid.uuid4().hex}"
    from app.models.agent import AgentAudit
    from app.services.agent_tasks import record_mutation, update_mutation_result

    mutation_id = None
    audit_id = None
    out = result("failed", "代码提案中断；没有应用补丁或运行宿主门禁")
    try:
        fingerprints = {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in files}
        mutation_id = record_mutation(
            source="agenda", kind="code_proposal",
            summary=f"待审代码提案：{str(item.get('finding') or '')[:60]}",
            detail={"agenda_date": agenda_date, "base_commit": base,
                    "review_required": True, "code_applied": False},
        )
        safe["mutation_task_id"] = mutation_id
        # 在模型请求前记一次尝试；失败同样占每日上限，历史 code.apply 也计数。
        with session_factory() as db:
            audit = AgentAudit(actor="ai", action="code.propose", target=tag,
                               task_id=mutation_id, before=json.dumps({"base_commit": base, "files": files}))
            db.add(audit)
            db.commit()
            db.refresh(audit)
            audit_id = audit.id
        raw = _llm_patch(item, _read_context(root, files))
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PATCH_BYTES:
            out = result("rejected", "模型输出缺失或超过补丁文本上限")
            return out
        diff = _extract_diff(raw)
        if not diff:
            out = result("rejected", "LLM 未产出可识别的 unified diff")
            return out
        reason = _validate_patch(diff, files, root)
        if reason:
            out = result("rejected", f"patch 授权校验拦截：{reason}")
            return out
        current_rc, current_base = _git(["rev-parse", "HEAD"], root)
        if (current_rc or current_base != base or not _main_worktree_clean(root)
                or any(hashlib.sha256((root / f).read_bytes()).hexdigest() != h
                       for f, h in fingerprints.items())):
            out = result("deferred", "生成期间基点或文件发生变化；未应用补丁，须按新版本重新审阅")
            return out
        archived = _archive_patch(diff, tag)
        if archived is None:
            out = result("failed", "补丁归档失败；没有可交付提案，未应用或执行代码")
            return out
        safe["patch_path"] = str(archived)
        rc, check_note = _git(["apply", "--check", str(archived)], root)
        if rc:
            out = result("rejected", f"diff 无法干净应用（仅静态检查）：{_redact(check_note)}")
            return out
        out = result("proposed", f"已归档待审补丁 {archived}（基点 {base[:12]}）；仅路径及适用性静态检查通过，"
                     "未应用、未运行测试或回放、未创建分支/提交。"
                     "尚无经验证的 OS 隔离执行器，实际实施须走 codex/* → PR → 完整 CI → 审查。",
                     apply_check=True, patch_sha256=hashlib.sha256(archived.read_bytes()).hexdigest())
        return out
    except Exception as exc:  # 单条失败可见；不暴露模型原文或凭据。
        out = result("failed", f"代码提案失败：{_redact(exc)}；未应用或执行代码")
        return out
    finally:
        if mutation_id is not None:
            try:
                update_mutation_result(mutation_id, "succeeded" if out["status"] == "proposed" else "failed",
                                       out["result"])
                if audit_id is not None:
                    with session_factory() as db:
                        audit = db.get(AgentAudit, audit_id)
                        if audit is not None:
                            audit.after = json.dumps({"status": out["status"], "patch_path": out.get("patch_path"),
                                                      "code_applied": False, "review_required": True})
                            db.commit()
            except Exception as exc:
                out.update(status="failed", result=f"提案留痕回填失败：{_redact(exc)}；未应用或执行代码")
