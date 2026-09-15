"""分层依赖守卫（S2-4）：业务层不得反向 import API 层。

**为什么需要**：`generate_picks` 住在 route 里时，常驻调度
（`app/picks/picks_autogen.py`）只能**反向 import 路由**并伪造
`SimpleNamespace(app=app)` 当 request——这正是「业务逻辑住在边缘」的签名。
抽离管线（S2-4）只解决了当下的那一处；**没有守卫，下一个人会再写一遍**。

规则（`app/` 内，AST 级，函数内导入同样命中）：

| 层 | 允许 import | 禁止 import |
|---|---|---|
| `app/api/**`、`app/main.py`、`app/bootstrap/**` | 全部 | —（装配层） |
| `app/picks`、`app/services`、`app/market`、`app/events`、`app/review` … | `app.core` / 同层 / 下游 | `app.api` |

**例外**：`app/api/deps.py` 自身属于装配层；`app/api/routes/*` 之间同包复用不在此规则范围。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"

#: 装配层（允许向下依赖任意层）
#: ⚠️ `bootstrap/` 于 2026-09-15（`IMP-027` 装配重构）加入：它是 `main.py` 切出的
#: 启动装配（服务构建 + 常驻循环声明），与 `main.py` 同层——**必须显式登记**，
#: 否则会落进"既非装配层也非业务层"的第三类而被静默跳过：跳过本身无害，
#: 但会让"装配层有哪些"这件事只能靠数文件反推（`skipped − 2` 那类口径）。
_ASSEMBLY_PREFIXES = ("api/", "bootstrap/")
_ASSEMBLY_FILES = {"main.py"}

#: 不得反向依赖 API 层的业务层前缀
_BUSINESS_PREFIXES = (
    "picks/",
    "services/",
    "market/",
    "events/",
    "review/",
    "sentiment/",
    "factors/",
    "risk/",
    "paper/",
    "news/",
    "predict/",
    "assistant/",
    "notifiers/",
    "data_providers/",
    "research/",
)


def _iter_py():
    for p in sorted(APP_DIR.rglob("*.py")):
        yield p, p.relative_to(APP_DIR).as_posix()


def _imported_modules(path: Path) -> set[str]:
    """AST 收集**所有**被导入的模块名（含函数内导入）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def _is_business(rel: str) -> bool:
    if rel in _ASSEMBLY_FILES or rel.startswith(_ASSEMBLY_PREFIXES):
        return False
    return rel.startswith(_BUSINESS_PREFIXES)


@pytest.mark.parametrize("path,rel", list(_iter_py()), ids=lambda v: v if isinstance(v, str) else "")
def test_business_layer_never_imports_api(path: Path, rel: str):
    """业务层不得 import `app.api`（装配层除外）。"""
    if not _is_business(rel):
        pytest.skip("装配层/其他：不受本规则约束")
    bad = sorted(m for m in _imported_modules(path) if m == "app.api" or m.startswith("app.api."))
    assert not bad, (
        f"{rel} 反向依赖 API 层：{bad}。"
        "业务逻辑应住在 services/ 或对应领域包，由 api 层调用它，而不是反过来。"
    )


def test_picks_autogen_calls_pipeline_not_route():
    """**S2-4 定点回归位**：调度器调用服务层管线，不 import 路由、不伪造 request。

    用 AST 判定而不是文本匹配——docstring 里会**叙述**这段历史（"此前是反向
    import …"），文本匹配会把说明文字误判成违规。
    """
    path = APP_DIR / "picks" / "picks_autogen.py"
    mods = _imported_modules(path)
    assert not any(m == "app.api" or m.startswith("app.api.") for m in mods), mods
    assert "app.services.picks_pipeline" in mods
    # 「伪造 request」的载体是 `types.SimpleNamespace`；没导入它就不可能再伪造
    assert "types" not in mods


def test_route_picks_delegates_to_pipeline():
    """route 只做依赖装配：`generate_picks` 本体不再内联管线。"""
    src = (APP_DIR / "api" / "routes" / "picks.py").read_text(encoding="utf-8")
    assert "generate_picks_pipeline" in src
    # 管线里的关键步骤不该再出现在路由文件里（抽干净了才算抽离）
    for leaked in ("_deep_score_candidates", "apply_replacement_threshold", "assemble_card"):
        assert leaked not in src, f"路由里仍有管线残留：{leaked}"


def test_routes_do_not_import_each_others_privates():
    """路由之间不得互相 import **私有**名（下划线开头）。

    S2-4 的另一半：`_load_snapshot_map` / `_default_trade_date_async` 曾是
    `api/routes/market.py` 的私有实现，却被 `picks_intraday.py` 与
    `theme_catalog.py` 当公共 API 用（`from app.api.routes.market import _load_snapshot_map`）。
    跨模块依赖别人的私有名 = market.py 一重构，消费方**静默**失效。
    实现已上移到 `services/market_snapshot.py`，本测试防止它回流。
    """
    offenders: list[str] = []
    for path, rel in _iter_py():
        if not rel.startswith("api/routes/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.api.routes."):
                privates = [a.name for a in node.names if a.name.startswith("_") and not a.name.startswith("__")]
                if privates:
                    offenders.append(f"{rel} ← {node.module}: {privates}")
    assert not offenders, "路由间跨模块私有导入：" + "；".join(offenders)



#: 已登记的**历史失效惰性导入**：豁免，但**留名**（不是静默忽略）。
#: 每条都指向账本缺陷号；修掉一条就删一条——下面的
#: `test_dead_import_exemptions_are_still_real` 会因"豁免已不存在"而变红，
#: 逼你清理（与 `test_cross_end_contract` 的 exempt 清单同款纪律）。
#: 键取 `相对路径::模块::名字`，刻意不含行号——行号会随改动漂移，
#: 用它作键会让豁免在无关改动后静默失效。
_KNOWN_DEAD_IMPORTS = {
    # BUG-008（2026-09-15 本守卫发现）：`_daily_plan` 的两段在
    # `contextlib.suppress(Exception)` 里导入不存在的模块 ⇒ 简报「今日计划」的
    # ①昨日复盘结论 与 ②未完成 action_items **恒为空**（功能静默死亡）。
    # 正确来源：`app.review.storage.get_report` / `app.review.models.ReviewReportRow`。
    "picks/morning_brief.py::app.picks.review_store::get_report",
    "picks/morning_brief.py::app.models.review::ReviewReport",
}


def _iter_app_imports():
    """`app/` 内全部 `from app.x import y` → (相对路径, 模块, 名字, 行号)。"""
    for path, rel in _iter_py():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.level == 0 and node.module):
                continue
            if not node.module.startswith("app."):
                continue
            for alias in node.names:
                if alias.name != "*":
                    yield rel, node.module, alias.name, node.lineno


def test_function_level_imports_are_resolvable():
    """**函数内导入必须真能解析**（2026-09-15 新增，事故直出）。

    为什么单设一道：本仓大量采用「函数内导入」避免循环依赖（route 尤其重）。
    这类导入写错名字时，`ImportError` 会被调用处宽泛的 `except Exception`
    （或 `contextlib.suppress`）吞成一句 warning ⇒ **门禁全绿、功能静默失效**。

    实测事故（2026-09-15）：`picks_intraday` 把 `attach_participants` 写在
    `tradability` 的导入行里（实际它在 `intraday_opportunity`），结果是
    "题材联动挖掘失败（ImportError）"写进 payload、候选清单恒为空——
    而当时 3067 条后端测试**没有一条**发现它，是启动服务看真实渲染才暴露的。
    同一道守卫当场又抓出两处**既有**失效导入（见 `_KNOWN_DEAD_IMPORTS`）。

    做法：AST 取出全部 `from app.x import y`（忽略 `*`），`importlib` 真导入 +
    名字校验；名字取不到时**再试子模块**（`from app.api.routes import picks`
    导入的是模块，不是包的属性——只查 `hasattr` 会误报）。

    与 `test_business_layer_never_imports_api` 的分工：那条管**方向**，
    这条管**名字是否存在**。方向对、名字错同样是事故。
    """
    import importlib

    bad: list[str] = []
    for rel, module, name, lineno in _iter_app_imports():
        key = f"{rel}::{module}::{name}"
        if key in _KNOWN_DEAD_IMPORTS:
            continue
        try:
            mod = importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 — 导入失败本身就是要报的错
            bad.append(f"{rel}:{lineno} 无法导入 {module}（{exc!r}）")
            continue
        if hasattr(mod, name):
            continue
        try:  # 子模块导入（包属性里看不到，但 import 是合法的）
            importlib.import_module(f"{module}.{name}")
        except Exception:  # noqa: BLE001
            bad.append(f"{rel}:{lineno} {module} 无 {name}")
    assert not bad, "函数内导入无法解析（会被调用处 except 吞成静默失效）：\n" + "\n".join(bad)


def test_dead_import_exemptions_are_still_real():
    """豁免清单必须条条命真——修好一条却忘了删豁免，这里变红。

    没有这条，`_KNOWN_DEAD_IMPORTS` 会长成一张只增不减的"历史垃圾清单"，
    把守卫的覆盖面一点点吃掉（豁免最怕的不是多，而是没人回头看）。
    """
    seen = {f"{rel}::{module}::{name}" for rel, module, name, _ in _iter_app_imports()}
    stale = sorted(k for k in _KNOWN_DEAD_IMPORTS if k not in seen)
    assert not stale, f"这些豁免已不存在（已修好或已改名），请从清单里删掉：{stale}"
