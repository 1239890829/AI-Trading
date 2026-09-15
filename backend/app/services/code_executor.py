"""C 类代码执行器（docs/summary/ai-evolution.md P1-⑤）——AI 自主改代码，最危险能力。

本能力安全默认关闭，只有管理员显式设置
``ASHARE_AGENT_CODE_CHANGE_ENABLED=1`` 才能进入执行流程。关闭时仅保留上游的
建议、议程或 patch 预览，不得修改工作区、提交或合并；开关检查位于任何 LLM/Git 操作之前。

**本执行器只提议、不落地（2026-09-15 加固，审计 O1 / 账本 BUG-003）**：
产出 = patch 文件 + 隔离分支上的 commit + 审计记录，**不再合并回主分支**。
落地一律走正常流程（``codex/*`` 分支 → PR → 完整 CI → 网页版审查）。
⚠️ 用户对「Codex PR 自动合并」的授权**不构成**对应用内 LLM 自主改码的授权——两者是
**不同的授权主体与不同的执行体**，不可互推。

安全设计（纵深防御，任何一层拒绝即终止）：
1. **白名单**：仅 ``backend/app/**`` 的 ``*.py``。⚠️ ``backend/tests/**`` **第一版明确禁止**：
   门禁是在**宿主解释器**里跑 pytest 的，允许 AI 生成测试 = 允许 AI 借测试在宿主上执行
   任意代码；而本仓没有「无宿主凭据 / 无外网 / 只读挂载非必要目录 / 限 CPU·内存·进程·
   时间」的隔离容器基础设施 ⇒ 按审计要求**保持禁止**，不做「跑在 worktree 里就算隔离」的
   自欺（worktree 只是 git 层隔离，**不是 OS 安全沙箱**）。
2. **禁改清单 + 受保护面**：migrations（DB 结构）、config/db/ttl_cache（配置与基建）、
   ``__init__``（导出面）、conftest（测试基建）；以及工作流（``.github/`` 等）、依赖与
   构建配置、迁移、凭据文件、治理与文档（``AGENTS.md`` / ``docs/`` / ``skills/`` / ``.workbuddy/``）。
3. **diff 级授权（应用前）**：解析 unified diff 的**全部**文件头（``diff --git`` / ``---`` /
   ``+++`` / ``rename`` / ``copy`` / ``new file mode`` / ``deleted file mode``），逐个拒绝——
   未声明文件、绝对路径、含 ``..`` 的穿越、仓库外路径、符号链接逃逸、重命名与删除/新增。
   ⚠️ **只校验议程声明的 ``files`` 是不够的**：那校验的是**意图**，而 patch 才是**事实**；
   真正的越界发生在「声明 A、实际改 B」这一层。
4. **以 git 为权威事实来源复核（应用后）**：``git diff --name-status --no-renames`` 取
   **实际**变更集合，与批准集合逐一比对；不一致 → 立即拒绝并清理 worktree。
   ⚠️ 不复用本模块的 diff 解析结果做这条判据——**判据不能与被判对象同源**（[[KB-ENG-81]]
   同族：两侧共用一套提取器 ⇒ 一致地少、谁都不报）。
5. **工作区干净**：主工作区有未提交改动 → rejected（避免与人工改动冲突）。
6. **每日上限**：C 类每日至多 1 次（audit 表计数，跨议程重生成也稳）。
7. **git apply --check**：unified diff 必须能干净应用，否则 rejected。
8. **回归门禁**：沙箱内 pytest 全量 + pyflakes（600s 超时），任一失败 → 丢弃沙箱。
9. **暂存**：只用 ``git add -- <已验证文件>``，**禁止** ``git add -A``——无范围暂存会把
   worktree 里任何意外产物（LLM 顺带写下的文件、门禁产生的缓存）一并卷入 commit。
10. **留痕与脱敏**：每步结果进议程 item.result；审计记 commit、分支与 patch 路径；
    所有进入日志 / DB 的外部输出经 ``_redact`` 过滤凭据。

v1 明确不做的：前端改动、tests 改动、migrations、跨仓库、新增/删除/重命名文件、自动合并。
"""
from __future__ import annotations

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
    ".github", ".gitlab", ".circleci", ".workbuddy",
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
GATE_TIMEOUT_SECONDS = 600

#: 每日 C 类上限（防 AI 高频自改代码；audit 表计数）
C_DAILY_MAX = 1

MAX_FILES = 3
MAX_CONTEXT_LINES = 160

#: patch 归档目录（**gitignored**；沙箱会被删除，patch 必须留在仓库外供人工审阅）
PATCH_ARCHIVE_DIR = PROJECT_ROOT / ".workbuddy" / "evolution-patches"


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
    """今日已执行的 C 类数（audit 计数；跨 agenda 重生成也稳）。

    cutoff 必须用 datetime 对象（evolution._bj_cutoff_today，北京 0 点）——SQLite 存
    "YYYY-MM-DD HH:MM:SS" 空格分隔，与 "…T00:00:00" 字符串比较恒 False。

    ⚠️ 加固后语义变为「今日已**提议**数」（执行器不再落地），但 action 名**保持**
    `code.apply` 不变：改名字会静默把这条上限失效（计数恒 0 = 每日上限形同虚设）。
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
    """回归门禁命令（C 类 v1 仅 .py：pytest 全量 + pyflakes；可注入便于测试）。

    解释器：显式配置 agent_venv_python > 主工作区 backend/.venv（绝对路径可跨
    worktree 用）> 系统 python3。

    ⚠️ 门禁在**宿主解释器**里执行：这就是「禁止 AI 改 tests」的根本原因——生成的测试
    会以宿主的权限跑，worktree 拦不住它去读 `backend/.env` 或访问外网。
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
    """跑门禁；返回 (ok, 摘要)。任一失败即 False。输出经脱敏后才进返回值。"""
    for cwd, cmd in _gate_commands(worktree, changed):
        try:
            p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                               timeout=GATE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return False, f"门禁超时（>{GATE_TIMEOUT_SECONDS}s）：{' '.join(cmd[1:3])}"
        if p.returncode != 0:
            tail = (p.stdout + p.stderr).strip().splitlines()
            detail = _redact(tail[-1]) if tail else ""
            return False, f"门禁失败 rc={p.returncode}（{' '.join(cmd[1:3])}）：{detail}"
    return True, "pytest 全量 + pyflakes 全绿"


def _archive_patch(diff: str, tag: str) -> Path | None:
    """把 patch 落盘到归档目录（沙箱随后会被删除，patch 必须留在仓库外供人工审阅）。"""
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
    """C 类执行主流程（**只提议、不落地**）。

    预检 → 沙箱 → LLM patch → **diff 级授权** → apply --check → **git 权威复核** →
    门禁 → 明确暂存 → 隔离分支 commit → 归档 patch → 审计。
    ⚠️ **不再合并回主分支**：落地走 `codex/*` → PR → CI → 网页版审查。
    """
    root = repo_root or PROJECT_ROOT
    files = [str(f) for f in (item.get("files") or [])]
    if not settings.agent_code_change_enabled:
        return {**item, "status": "deferred",
                "result": "C 类代码执行器已关闭（ASHARE_AGENT_CODE_CHANGE_ENABLED=0）："
                          "仅保留建议、议程或 patch 预览，不得修改、提交或合并代码"}

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
        summary=f"C类代码改动提议：{item.get('finding', '')[:60]}（files={files}）",
        detail={"agenda_date": agenda_date},
    )

    # 2) 沙箱（worktree + 独立分支）
    tag = f"{agenda_date.replace('-', '')}-{uuid.uuid4().hex[:6]}"
    branch = f"evolution/{tag}"
    sandbox = Path("/tmp") / f"evo-sandbox-{tag}"
    rc, out = _git(["worktree", "add", "-b", branch, str(sandbox), "HEAD"], root)
    if rc != 0:
        update_mutation_result(mutation_id, "failed", f"建沙箱失败：{_redact(out, 150)}")
        return {**item, "status": "failed", "result": f"建沙箱失败：{_redact(out, 150)}"}

    keep_branch = False  # 仅「成功产出待审阅变更」时保留分支；其余一律清理
    try:
        # 3) 读上下文 → LLM diff
        context = _read_context(sandbox, files)
        raw = _llm_patch(item, context)
        diff = _extract_diff(raw or "")
        if not diff:
            return {**item, "status": "rejected", "result": "LLM 未产出可识别的 unified diff"}

        # 4) diff 级授权：解析全部文件头，**应用前**逐个拒绝
        reason = _validate_patch(diff, files, sandbox)
        if reason:
            update_mutation_result(mutation_id, "failed", f"patch 授权校验拦截：{reason}")
            return {**item, "status": "rejected", "result": f"patch 授权校验拦截：{reason}"}

        # 5) git apply --check（合法性与冲突预检；patch 落沙箱文件供 git 读）
        patch_file = sandbox / ".evo.patch"
        patch_file.write_text(diff + "\n", encoding="utf-8")
        rc, out = _git(["apply", "--check", str(patch_file)], sandbox)
        if rc != 0:
            return {**item, "status": "rejected", "result": f"diff 无法干净应用：{_redact(out)}"}
        rc, out = _git(["apply", str(patch_file)], sandbox)
        if rc != 0:
            update_mutation_result(mutation_id, "failed", f"diff 应用失败：{_redact(out)}")
            return {**item, "status": "failed", "result": f"diff 应用失败：{_redact(out)}"}

        # 6) 应用后以 git 为权威事实来源复核实际变更集合
        reason = _verify_applied(sandbox, files)
        if reason:
            update_mutation_result(mutation_id, "failed", f"变更集合复核拦截：{reason}")
            return {**item, "status": "rejected", "result": f"变更集合复核拦截：{reason}"}

        # 7) 回归门禁
        ok, gate = _run_gate(sandbox, files)
        if not ok:
            update_mutation_result(mutation_id, "failed", f"回归门禁拦截：{gate}")
            return {**item, "status": "rejected", "result": f"回归门禁拦截：{gate}"}

        # 8) 只暂存**已验证的明确文件**（禁止 `git add -A`：无范围暂存会卷入意外产物）
        staged = [_normalize_repo_path(f) for f in files]
        rc, out = _git(["add", "--", *staged], sandbox)
        if rc != 0:
            update_mutation_result(mutation_id, "failed", f"暂存失败：{_redact(out, 150)}")
            return {**item, "status": "failed", "result": f"暂存失败：{_redact(out, 150)}"}

        # 9) 沙箱分支独立 commit（留痕；**不合并**）
        rc, out = _git(["commit", "-m",
                        f"evolution: {item.get('finding', '')[:80]} (agenda {agenda_date})"], sandbox)
        if rc != 0:
            update_mutation_result(mutation_id, "failed", f"沙箱 commit 失败：{_redact(out, 150)}")
            return {**item, "status": "failed", "result": f"沙箱 commit 失败：{_redact(out, 150)}"}
        rc, commit = _git(["rev-parse", "--short", "HEAD"], sandbox)
        rc, diffstat = _git(["diff", "--stat", "HEAD~1..HEAD"], sandbox)

        # 10) 归档 patch（沙箱随后删除，patch 必须留在仓库外供人工审阅）
        archived = _archive_patch(diff, tag)
        keep_branch = True

        # 11) 审计（target 只记 commit 与文件清单，不含源码上下文）
        with session_factory() as db:
            from app.models.agent import AgentAudit

            db.add(AgentAudit(actor="ai", action="code.apply",
                              target=f"{commit}:{','.join(staged)}"))
            db.commit()

        result = (
            f"已生成**待审阅**变更 {commit}（{gate}）；文件：{', '.join(staged)}；"
            f"分支 {branch}（未合并）；补丁 {archived or '未归档'}。"
            "落地须走 codex/* → PR → 完整 CI → 网页版审查流程"
        )
        replay_block = None
        if any(f.startswith("backend/app/picks/") or "/picks/" in f for f in files):
            # 2026-09-08 用户指令：策略改动必须以回测数据为依据。加固后不再合并，
            # 回放对比改为**给人工审阅的证据**（对沙箱分支的 commit 跑），不再影响落地。
            try:
                from app.services.replay_gate import run_comparison

                replay_block = run_comparison(days=10, commit=str(commit), force=True)
                result += (
                    f"；回放对比[{replay_block.get('verdict')}]: "
                    + replay_block.get("report", "").split("\n\n")[0].replace("\n", " ")
                )
            except Exception as exc:  # noqa: BLE001  回放失败不吞提议结果
                log.warning("replay comparison failed: %s", _redact(exc))
                replay_block = {"ok": False, "verdict": "error", "report": _redact(exc)}
        update_mutation_result(mutation_id, "succeeded", result)
        return {**item, "status": "executed",
                "result": result,
                "mutation_task_id": mutation_id,
                "replay_comparison": replay_block,
                "commit": commit, "files_changed": staged, "diffstat": diffstat[:400],
                "branch": branch, "patch_path": str(archived) if archived else None,
                "merged": False, "review_required": True}
    finally:
        rc, out = _git(["worktree", "remove", "--force", str(sandbox)], root, timeout=30)
        if rc != 0:
            # 清理失败不静默：worktree 残留会让下次 `worktree add` 撞名，须留痕
            log.warning("worktree 清理失败（%s）：%s", sandbox.name, _redact(out, 150))
        if not keep_branch:
            _git(["branch", "-D", branch], root, timeout=30)
        _cleanup_patch(sandbox)


def _cleanup_patch(sandbox: Path) -> None:
    with_dir = Path(str(sandbox))
    if with_dir.exists():
        import contextlib

        with contextlib.suppress(OSError):
            (with_dir / ".evo.patch").unlink(missing_ok=True)
