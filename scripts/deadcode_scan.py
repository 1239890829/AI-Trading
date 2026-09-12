#!/usr/bin/env python3
"""deadcode_scan.py —— 防腐化扫描：顶层符号「仅定义处出现」候选清单。

定位：**月度手动跑**的防腐化工具（2026-09-13 裁定，不进 CI——固化成硬门禁需要维护
路由端点/装饰器/tests 调用三类豁免清单，收益与成本相抵，见 docs/retro-and-gaps.md
§6.5b #5）。用法：

    python3 scripts/deadcode_scan.py             # 打印全量候选
    python3 scripts/deadcode_scan.py --selftest  # 扫描器自证（合成语料断言）

扫描面：
- Python（backend/app、backend/tests、backend/scripts、scripts）：顶层 def/class/赋值；
- 前端（apps/web/{app,components,lib,hooks}）：**具名** export。default export 不扫——
  Next 约定入口（page/layout/route）与「import 后改名」都让名字失真，扫了必误报。

判据：符号名在全仓 git-tracked 文本文件中的出现次数（字符串/注释一并计入——
getattr、注册表字符串引用因此天然保守，宁可漏报不误报）。
- 出现次数 == 1（仅定义处）→ 死代码候选；
- 前端具名 export 的全部出现都在定义文件内 → 「过宽 export」候选（正解去 export 而非删）。

三类**必须排除**的假阳性（每类都真实踩过）：
1. **装饰器注册**：@app.middleware / @router.get / @pytest.fixture / pydantic 校验器 /
   SQLAlchemy listens_for 等——被框架按名字之外的方式调用，名字只出现一次 ≠ 死。
   ⚠️ 装饰器行不含被装饰函数名，「只数名字」的扫描器天然漏判（_perf_middleware 事故，
   2026-09-13：被 @app.middleware("http") 注册的活跃中间件被上轮扫描误判为零引用）；
2. **约定入口**：Next.js app/ 约定文件、alembic 迁移（不在定义扫描面内，但其文本参与计数）；
3. **跨名引用**：`import X as Y` 后用 Y——X 仍出现在 import 行，计数 ≥ 2，天然保守。

输出只打印，不改任何文件；删除动作一律人工逐项复核后走 Edit/safe-trash.sh。
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PY_DIRS = ("backend/app", "backend/tests", "backend/scripts", "scripts")
FE_DIRS = ("apps/web/app", "apps/web/components", "apps/web/lib", "apps/web/hooks")

# 装饰器命中 ⇒ 该符号被框架注册/隐式调用，不进候选（假阳性排除 #1）
DECORATOR_EXCLUDE = re.compile(
    r"(router|app|ws)\s*\.\s*(get|post|put|delete|patch|head|options|middleware"
    r"|websocket|on_event|exception_handler)"
    r"|\bfixture\b"
    r"|\b(field_)?validator\b|root_validator|computed_field"
    r"|\blistens_for\b|\bhybrid_(property|method)\b|\bvalidates\b"
)

# 装饰器命中 ⇒ 同上，但属于「属性级装饰」（property/staticmethod 等），同样排除
ATTR_EXCLUDE = re.compile(r"property|setter|staticmethod|classmethod")

IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
FE_EXPORT = re.compile(
    r"^export\s+(?:declare\s+)?(?:async\s+)?(?:const|function|class|type|interface|enum)\s+([A-Za-z_$][\w$]*)",
    re.M,
)
FE_EXPORT_LIST = re.compile(r"^export\s*\{([^}]+)\}", re.M)
SKIP_FILES = ("package-lock.json",)
SKIP_SUFFIX = (".lock", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".parquet")


@dataclass
class Candidate:
    kind: str  # py-dead / fe-dead / fe-internal
    name: str
    file: str
    line: int
    total: int
    note: str = ""


def tracked_files(files: dict[str, str] | None) -> list[str]:
    if files is not None:  # 自证语料
        return list(files)
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    names = []
    for raw in out.stdout.split(b"\0"):
        rel = raw.decode("utf-8", "replace")
        if not rel or rel.startswith(".workbuddy"):
            continue
        base = rel.rsplit("/", 1)[-1]
        if base in SKIP_FILES or rel.endswith(SKIP_SUFFIX):
            continue
        names.append(rel)
    return names


def read_all(files: dict[str, str] | None, rels: list[str]) -> dict[str, str]:
    if files is not None:
        return files
    out = {}
    for rel in rels:
        try:
            out[rel] = (ROOT / rel).read_text("utf-8", "replace")
        except OSError:
            pass
    return out


def _in_dirs(rel: str, dirs: tuple[str, ...]) -> bool:
    return any(rel == d or rel.startswith(d + "/") for d in dirs)


def _py_definitions(rel: str, text: str):
    """产出 (line, name, excluded, note)。excluded=True 表示被框架注册/约定收集，不进候选。"""
    is_test = rel.startswith("backend/tests/") or "/tests/" in rel
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            decs = []
            for d in node.decorator_list:
                try:
                    decs.append(ast.unparse(d))
                except Exception:
                    decs.append("")
            dec_src = " | ".join(decs)
            excluded = bool(DECORATOR_EXCLUDE.search(dec_src))
            note = "decorated: " + dec_src.split(" | ")[0][:60] if excluded else ""
            if not excluded and is_test and (
                (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"))
                or (isinstance(node, ast.ClassDef) and node.name.startswith("Test"))
            ):
                excluded = True
                note = "pytest 约定入口（test_* / Test*），框架按名收集"
            yield node.lineno, node.name, excluded, note
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    yield node.lineno, t.id, False, ""
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield node.lineno, node.target.id, False, ""


def scan(files: dict[str, str] | None) -> list[Candidate]:
    rels = tracked_files(files)
    texts = read_all(files, rels)

    total_counter: Counter[str] = Counter()
    file_counter: dict[str, Counter[str]] = {}
    for rel, text in texts.items():
        c = Counter(m.group(0) for m in IDENT.finditer(text))
        file_counter[rel] = c
        total_counter.update(c)

    candidates: list[Candidate] = []

    # Python：顶层 def/class/赋值
    for rel, text in texts.items():
        if not rel.endswith(".py") or not _in_dirs(rel, PY_DIRS):
            continue
        for line, name, excluded, note in _py_definitions(rel, text):
            if excluded or name.startswith("__"):
                continue
            if total_counter[name] <= 1:
                candidates.append(Candidate("py-dead", name, rel, line, total_counter[name], note))

    # 前端：具名 export
    for rel, text in texts.items():
        if not rel.endswith((".ts", ".tsx")) or not _in_dirs(rel, FE_DIRS):
            continue
        named: list[tuple[int, str]] = []
        for m in FE_EXPORT.finditer(text):
            named.append((text.count("\n", 0, m.start()) + 1, m.group(1)))
        for m in FE_EXPORT_LIST.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            for part in m.group(1).split(","):
                name = part.strip().split(" as ")[0].strip()
                if name:
                    named.append((line, name))
        for line, name in named:
            total = total_counter[name]
            if total <= 1:
                candidates.append(Candidate("fe-dead", name, rel, line, total))
            elif total == file_counter.get(rel, Counter())[name] and total > 1:
                candidates.append(
                    Candidate("fe-internal", name, rel, line, total, "仅定义文件内使用（含 import 行），export 过宽")
                )

    candidates.sort(key=lambda c: (c.kind, c.file, c.line))
    return candidates


_SELFTEST_FILES = {
    "backend/app/demo.py": (
        "import functools\n"
        "from app.core import ttl_cache as tc\n"
        "\n"
        "def dead_function():\n"
        "    return 1\n"
        "\n"
        "@app.middleware('http')\n"
        "async def active_middleware(request, call_next):\n"
        "    return await call_next(request)\n"
        "\n"
        "@router.get('/x')\n"
        "def active_route():\n"
        "    return 2\n"
        "\n"
        "@pytest.fixture()\n"
        "def active_fixture():\n"
        "    return 3\n"
        "\n"
        "@field_validator('q')\n"
        "def active_validator(cls, v):\n"
        "    return v\n"
        "\n"
        "REGISTRY = {}\n"
        "\n"
        "def kept_via_string():\n"
        "    return 4\n"
        "\n"
        "REGISTRY['kept_via_string'] = kept_via_string\n"
        "\n"
        "def kept_via_alias():\n"
        "    return 5\n"
        "\n"
        "import app.predict.service as svc\n"
        "ALIAS_USE = svc.kept_via_alias\n"
        "\n"
        "def internal_only():\n"
        "    return internal_only  # 同文件自引用：Python 无 export，不算候选\n"
    ),
    "backend/tests/test_demo.py": (
        "import pytest\n"
        "\n"
        "def test_demo_collects_by_convention():\n"
        "    assert True\n"
        "\n"
        "class TestGroup:\n"
        "    def test_inner(self):\n"
        "        assert True\n"
    ),
    "apps/web/lib/demo.ts": (
        "export const FE_DEAD = 1;\n"
        "export const FE_INTERNAL = 2;\n"
        "const local = FE_INTERNAL + 1;\n"
        "export const usedLocal = local;\n"
        "export default function NotScanned() {\n"
        "  return null;\n"
        "}\n"
    ),
    "apps/web/app/page.tsx": (
        "import { usedLocal } from '../lib/demo';\n"
        "export default function Page() {\n"
        "  return usedLocal;\n"
        "}\n"
    ),
}


def selftest() -> int:
    found = {(c.kind, c.name): c for c in scan(_SELFTEST_FILES)}

    def has(kind: str, name: str) -> bool:
        return (kind, name) in found

    checks = [
        ("py 死函数入候选", has("py-dead", "dead_function")),
        ("@app.middleware 注册的函数不入候选", not has("py-dead", "active_middleware")),
        ("@router.get 路由不入候选", not has("py-dead", "active_route")),
        ("@pytest.fixture 不入候选", not has("py-dead", "active_fixture")),
        ("pydantic 校验器不入候选", not has("py-dead", "active_validator")),
        ("字符串引用保活", not has("py-dead", "kept_via_string")),
        ("import-as 别名保活", not has("py-dead", "kept_via_alias")),
        ("同文件自引用（Python）不算候选", not has("py-dead", "internal_only")),
        ("前端死 export 入候选", has("fe-dead", "FE_DEAD")),
        ("前端过宽 export 入 internal 候选", has("fe-internal", "FE_INTERNAL")),
        ("前端 default export 不扫", not has("fe-dead", "NotScanned")),
        ("pytest 约定测试函数不入候选", not has("py-dead", "test_demo_collects_by_convention")),
        ("pytest 约定测试类不入候选", not has("py-dead", "TestGroup")),
    ]
    failed = [msg for msg, ok in checks if not ok]
    for msg, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {msg}")
    if failed:
        print(f"selftest FAILED: {len(failed)} 项")
        return 1
    print(f"selftest PASSED: {len(checks)} 项断言全过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true", help="运行扫描器自证（合成语料断言）")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    candidates = scan(None)
    by_kind: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_kind.setdefault(c.kind, []).append(c)

    print(f"扫描面：git-tracked 文件（排除锁文件/二进制）；Python={','.join(PY_DIRS)}；前端具名 export={','.join(FE_DIRS)}")
    print(f"候选合计 {len(candidates)}：py-dead {len(by_kind.get('py-dead', []))} / "
          f"fe-dead {len(by_kind.get('fe-dead', []))} / fe-internal {len(by_kind.get('fe-internal', []))}\n")
    for kind in ("py-dead", "fe-dead", "fe-internal"):
        rows = by_kind.get(kind, [])
        if not rows:
            continue
        print(f"== {kind} ({len(rows)}) ==")
        for c in rows:
            note = f"  # {c.note}" if c.note else ""
            print(f"  {c.file}:{c.line}  {c.name}  (total={c.total}){note}")
        print()
    print("提醒：候选 ≠ 死代码。删除前必须逐项人工复核（装饰器/字符串注册/约定入口/证据链载体），")
    print("并先看定义处上下文——2026-09-13 的 _perf_middleware 事故：名字零引用但被装饰器注册。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
