"""分层依赖守卫（S2-4）：业务层不得反向 import API 层。

**为什么需要**：`generate_picks` 住在 route 里时，常驻调度
（`app/picks/picks_autogen.py`）只能**反向 import 路由**并伪造
`SimpleNamespace(app=app)` 当 request——这正是「业务逻辑住在边缘」的签名。
抽离管线（S2-4）只解决了当下的那一处；**没有守卫，下一个人会再写一遍**。

规则（`app/` 内，AST 级，函数内导入同样命中）：

| 层 | 允许 import | 禁止 import |
|---|---|---|
| `app/api/**`、`app/main.py` | 全部 | —（装配层） |
| `app/picks`、`app/services`、`app/market`、`app/events`、`app/review` … | `app.core` / 同层 / 下游 | `app.api` |

**例外**：`app/api/deps.py` 自身属于装配层；`app/api/routes/*` 之间同包复用不在此规则范围。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"

#: 装配层（允许向下依赖任意层）
_ASSEMBLY_PREFIXES = ("api/",)
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

