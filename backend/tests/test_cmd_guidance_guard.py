"""「起服务指引里禁止出现 `--reload`」的文本守卫（2026-09-14，IMP-001 固化）。

## 为什么需要这条守卫

`--reload` 在本仓是**已知事故成因**：它与 SQLite 锁组合会反复挂死
（2026-09-02 定位，`AGENTS.md` §6.1 明写「绝不用 `--reload`」）。但**指引文案**曾两次
教用户照抄它：

1. `AGENTS.md` §1 启动命令（账本 §七 **#19**）—— 当时**只修了文档、未扫代码**；
2. `apps/web/app/workbench/page.tsx` 后端断连时的 `setError(...)` 文案（**IMP-001**）——
   加上 `README.md` / `docs/deployment.md` / `scripts/bootstrap.sh` 共 **4 处**。

第 2 次与第 1 次**同形**：修单点 ⇒ 换个文件再犯。所以按本仓纪律
（`retro-and-gaps.md`「累计 39 处偏差、十七类，**全部都要防**」）把判据固化成常驻测试，
否则第三次只会换个文件重演。

## 判据（刻意窄）

一行**同时**含 `uvicorn` 与 `--reload` ⇒ 该行必须含**禁用标记**之一
（`禁用` / `禁止` / `勿加` / `绝不用` / `删除` / `删掉`）。

- **为什么锚 `uvicorn` 而不是「出现 `--reload` 就红」**：本仓大量文本**合法地**提到这个词
  ——讲禁用原因（`AGENTS.md` §6.1 / `docs/INDEX.md` T1）、记历史偏差
  （`retro-and-gaps.md` §七 #19 与统计段）、登记任务（`IMP-001` 行）。
  一律判红会逼人**改历史**来让门禁变绿，那是伪装成修复的破坏
  （同 `test_doc_health_empty_sections.py` 跳过 `archive/` 的理由）。
  真正要拦的是「**教用户怎么起服务**」的命令行 —— 那必然带 `uvicorn`。
- **为什么允许标记豁免而不直接禁词**：正确写法是「写命令 + 同处写明勿加」，
  这正是本轮 4 处修复的形态。判据要能**认可**这个形态，否则修完仍红。

## 扫描面

跳过**只读历史快照与本地痕迹**：`.git` / `node_modules` / `.next` / `backend/.venv` /
`data` / `docs/archive` / `.workbuddy` / 各类缓存。其余文本文件全扫。

## 与 GOV-010 的关系

本文件**刻意**分两层：判据函数对 `tmp_path` 造的正反例是 **hermetic** 的；
对真实仓库的断言是本守卫的**存在意义**（不扫仓库就等于没扫）。
故真实仓库断言只放在**一条**用例里，便于它假红时**一眼定位到「是仓库脏了、不是判据坏了」**。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: 守卫自身：反面样例（教人加 --reload 的命令）必须**字面存在**于本文件里，
#: 否则测不出判据。故扫描时**排除自身** —— 这是唯一自洽的做法：
#: 若改成「给本文件塞豁免标记」，就等于把 `PROHIBITION_MARKERS` 变成后门，
#: 「标记豁免」判据随即失效（谁都能写个标记把违规藏起来）。
SELF = Path(__file__).resolve()

#: 禁用标记：出现任一即认为「该行在讲『别这么起』」，予以豁免
PROHIBITION_MARKERS = ("禁用", "禁止", "勿加", "绝不用", "删除", "删掉")

#: 命令锚点：真正要拦的形态是「起服务命令行」，它必然含 uvicorn
CMD_ANCHOR = "uvicorn"
FLAG = "--reload"

#: 不扫的目录（只读快照 / 依赖 / 本地痕迹 / 缓存）
SKIP_DIRS = {
    ".git", "node_modules", ".next", ".venv", "data", "archive", ".workbuddy", "artifacts",
    "__pycache__", ".pytest_cache", ".turbo", "dist", "build", ".ruff_cache",
    ".mypy_cache", "coverage", "htmlcov", "site-packages", "vendor",
}

#: 名称**前缀**命中的目录一律剪枝 —— 本仓存在编号变体与并行环境，
#: 逐个枚举必然漏：`backend/.venv-research`（第二个 venv，实测 **26508** 个候选文件 =
#: 剪枝前扫描量的 **97%**，把本守卫从 <1s 拖到 80s）、`.workbuddy-ai` 等。
SKIP_DIR_PREFIXES = (".venv", ".workbuddy")

#: 只看文本类文件（二进制/大产物不读）
TEXT_SUFFIXES = {
    ".md", ".sh", ".ts", ".tsx", ".js", ".jsx", ".py", ".yml", ".yaml",
    ".json", ".txt", ".example", ".toml", ".cfg", ".html",
}

#: 单文件体积上限，避免误读大产物
MAX_BYTES = 2_000_000


def find_violations(root: Path) -> list[str]:
    """返回 `相对路径:行号: 该行内容` 形式的违规清单（判定见模块 docstring）。

    用 `os.walk` **原地剪枝**而非 `rglob`：本仓 `node_modules` / `data`（万级 parquet）
    / `.git` 体量极大，`rglob` 会先走完再过滤 —— 实测把本守卫从 **<1s 拖到 102s**，
    加进每次门禁是净负担。剪枝后只走真正要读的目录树。
    """
    root = root.resolve()
    violations: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(SKIP_DIR_PREFIXES)
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix not in TEXT_SUFFIXES or path == SELF:
                continue
            try:
                if path.stat().st_size > MAX_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if CMD_ANCHOR not in line or FLAG not in line:
                    continue
                if any(marker in line for marker in PROHIBITION_MARKERS):
                    continue
                violations.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")
    return violations


# ---------------------------------------------------------------- 判据本身（hermetic）

def test_detects_guidance_teaching_reload(tmp_path: Path) -> None:
    """反例：教用户照抄 `--reload` 的启动命令 ⇒ 必须报出。"""
    (tmp_path / "README.md").write_text(
        "```bash\nuvicorn app.main:app --reload --port 8000\n```\n", encoding="utf-8"
    )
    hits = find_violations(tmp_path)
    assert len(hits) == 1, hits
    assert "README.md:2" in hits[0]


def test_prohibition_line_is_exempt(tmp_path: Path) -> None:
    """正例：命令 + 同处写明「勿加」= 正确形态 ⇒ 不报（否则修完仍红）。"""
    for marker in PROHIBITION_MARKERS:
        d = tmp_path / marker
        d.mkdir()
        (d / "a.md").write_text(
            f"uvicorn app.main:app --port 8000  # {marker} --reload\n", encoding="utf-8"
        )
        assert find_violations(d) == [], marker


@pytest.mark.parametrize(
    "line",
    [
        "# ⚠️ 绝不用 --reload：与 SQLite 锁组合会反复挂死",
        "| **T1 起栈** | `--reload` **禁用**（与 SQLite 锁组合挂死） |",
        "无 --reload 时改完不重启=旧代码",
    ],
)
def test_bare_mention_without_command_is_ignored(tmp_path: Path, line: str) -> None:
    """纯提及（讲禁用原因 / 记历史）不含 `uvicorn` ⇒ 不受本判据约束。

    这 3 条取自仓库真实文本，钉住「判据不得宽到逼人改历史」。
    """
    (tmp_path / "x.md").write_text(line + "\n", encoding="utf-8")
    assert find_violations(tmp_path) == []


def test_skips_archive_and_workbuddy(tmp_path: Path) -> None:
    """`docs/archive/` 与 `.workbuddy/` 是只读快照 / 本地痕迹 ⇒ 不扫。

    夹具用**真实存在**的 `docs/archive/` 文件名：判据本身只看**目录名**，
    而写一个不存在的 `docs/archive/xxx.md` 会被 `doc-health` 的 F 项
    （代码注释死引用）判红 —— 那是真阳性的假阳性，不如直接用真名绕开。
    """
    bad = "uvicorn app.main:app --reload --port 8000\n"
    for rel in ("docs/archive/plan-review.md", ".workbuddy/memory/2026-09-14.md", "node_modules/pkg/a.md"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(bad, encoding="utf-8")
    assert find_violations(tmp_path) == []


def test_ignores_non_text_suffixes(tmp_path: Path) -> None:
    """二进制/未列后缀不读（避免误伤产物）。"""
    (tmp_path / "blob.bin").write_text(
        "uvicorn app.main:app --reload\n", encoding="utf-8"
    )
    assert find_violations(tmp_path) == []


# ---------------------------------------------------------------- 真实仓库（守卫生效面）

def test_repo_has_no_reload_guidance() -> None:
    """真实仓库不得再有「教用户加 `--reload`」的起服务指引。

    假红时的定位提示：本用例只依赖仓库文本，**不依赖任何模块逻辑** ——
    若它红而上面 hermetic 用例全绿，说明是**仓库里新增了违规文案**（IMP-001 同形），
    不是判据坏了。请修文案，不要把标记塞进豁免。
    """
    hits = find_violations(REPO)
    assert hits == [], (
        "发现教用户加 --reload 的起服务指引（与 AGENTS.md §6.1 冲突，会复现已知挂死事故）：\n"
        + "\n".join(hits)
        + "\n修法：删掉 --reload 并在同处写明禁用原因。"
    )
