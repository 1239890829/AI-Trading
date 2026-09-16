"""provider 能力注册表反射双向锚定。

注册表是手写静态真值（app/services/provider_capabilities.py），最大的风险是
**漂移**：provider 方法改名/删除后注册表还在声称"有"（幽灵能力）；composite
链上加了新消费方法而注册表不知道（盲区）。两个方向都反射到**类定义本身**，
注册表与代码不同步时测试必红——不依赖任何人记得手工同步。
"""
from __future__ import annotations

import ast
from pathlib import Path

import app.data_providers.ths as ths_mod
from app.data_providers import (
    CompositeProvider,
    EastmoneyProvider,
    SinaProvider,
    ThsFuyaoProvider,
    TencentProvider,
)
from app.services import provider_capabilities as pc

_PROVIDER_CLASSES = {
    "ths": ThsFuyaoProvider,
    "tencent": TencentProvider,
    "eastmoney": EastmoneyProvider,
    "sina": SinaProvider,
}


def test_declared_methods_exist_on_provider_class():
    """方向①：SUPPORTED/STUB ⇒ 方法真实存在于 provider 类（防幽灵能力）。"""
    for src, cls in _PROVIDER_CLASSES.items():
        caps = pc.capabilities_of(src)
        assert caps, f"{src} 未入注册表"
        for method, entry in caps.items():
            if entry["level"] in (pc.SUPPORTED, pc.STUB):
                assert hasattr(cls, method), (
                    f"{src}.{method} 声明为 {entry['level']} 但类上不存在——"
                    "方法改名/删除后注册表未同步"
                )


def test_composite_public_methods_covered_by_every_source():
    """方向②：composite 全部公共消费方法必须在每源条目中显式出现。

    UNSUPPORTED 也要列（"链上不会调它"本身是信息，盲区不是）；
    get_limit_up_ladder 是链外直调，允许是注册表多出的键。
    """
    # composite 自有生命周期/观测面/本地配置面：不向数据源分发，不属于能力消费面。
    # - aclose / breaker_state / provider_health：喂 /api/system/providers
    # - budget_for（S2-3）：读本模块常量，纯本地计算，与"哪个源会什么"无关
    # 新增公共方法时**必须在此显式登记或补进注册表**——这条断言就是为此存在的，
    # 别改成 `startswith("_")` 之外的通配（那会放过真正的漏登记）。
    composite_own = {"aclose", "breaker_state", "provider_health", "budget_for"}
    public = {
        name
        for name, obj in vars(CompositeProvider).items()
        if callable(obj) and not name.startswith("_") and name not in composite_own
    }
    assert public, "反射拿到空方法面——CompositeProvider 结构变了？"
    assert {"get_quotes", "get_kline", "get_trading_days"} <= public  # 锚定反射有效
    for src, caps in pc.CAPABILITIES.items():
        missing = public - set(caps)
        assert not missing, f"{src} 注册表缺少 composite 消费方法条目: {sorted(missing)}"


def test_known_single_points():
    """单点风险清单抽查：唯一 SUPPORTED 源挂掉即无人兜底的方法。"""
    sp = pc.single_point_methods()
    assert sp.get("get_trading_days") == "ths"
    assert sp.get("get_hot_stock_list") == "ths"
    assert sp.get("get_limit_up_ladder") == "ths"
    assert sp.get("get_minute_line") == "tencent"
    assert sp.get("get_trades") == "eastmoney"
    assert sp.get("get_capital_flow") == "sina"
    assert sp.get("get_board_rankings") == "sina"


def test_stub_not_counted_as_support():
    """STUB 名存实亡：不得进 providers_supporting / single_points。"""
    for src, caps in pc.CAPABILITIES.items():
        for method, entry in caps.items():
            if entry["level"] == pc.STUB:
                assert src not in pc.providers_supporting(method), (
                    f"{src}.{method} 是 STUB 却进了 SUPPORTED 清单"
                )
    # 抽查已知恒空占位
    assert "tencent" not in pc.providers_supporting("get_limit_up_pool")
    assert "ths" not in pc.providers_supporting("get_order_book")
    assert "sina" not in pc.providers_supporting("get_longhu_records")


def test_registry_shape():
    """注册表形状契约（2026-09-08 P0-4 起端点已删——注册表保留为防腐化锚与
    单点告警选点依据，本用例改为直接锚定注册表自身）。"""
    for method, src in pc.single_point_methods().items():
        assert src in pc.CAPABILITIES, f"single_point {method} 的源 {src} 不在注册表"
        # 单点方法必须真实存在于该源（防空壳注册）
        entry = pc.CAPABILITIES[src].get(method)
        assert entry is not None and entry["level"] == pc.SUPPORTED
    for src, caps in pc.CAPABILITIES.items():
        for method, entry in caps.items():
            assert entry.get("level") in pc.LEVELS, f"{src}.{method} level 非法"


#: `GOV-016` 定点回归（2026-09-16）：这些 fuyao 端点**官方有、本项目未接入**，
#: 而 `ths.py` 模块 docstring 曾把它们列进「已实现」能力清单 ⇒ 读者据此以为已接入
#: （[[KB-ENG-85]] 同族：不产生死链、不产生未登记文件，**只产生错误的读者预期**）。
#: 判据**双向自洽**：docstring 说「未接入」⇒ 全 `app/` 生产代码里就**不该**出现该路径。
#: 因此本用例不靠人记得同步——谁真把端点接上了，它先红、逼着同轮改 docstring。
_THS_UNWIRED_ENDPOINTS = (
    "special-data/limit-down-pool",
    "financials/indicators",
    "financials/income-statements",
    "financials/balance-sheets",
    "financials/cash-flow-statements",
    "valuations/snapshot",
    "meta/tickers/search",
)


def _app_code_string_literals() -> list[tuple[str, str]]:
    """`app/**/*.py` 里**非 docstring** 的字符串字面量（端点路径只会出现在这类里）。

    必须排除 docstring：docstring 的职责恰恰是**提到**这些路径（标为未接入）。
    只剥模块 docstring 不够——函数 docstring 里也可能引用端点名（`ths.py` 就有一处）。
    """
    # `app` 是**命名空间包**（无 `__init__.py`）⇒ `app.__file__ is None`，不能拿它定位根。
    root = Path(ths_mod.__file__).parents[1]
    out: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        doc_ids: set[int] = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_ids.add(id(body[0].value))
        lits = [
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids
        ]
        out.append((str(path.relative_to(root)), "\n".join(lits)))
    return out


def _doc_bullets(doc: str) -> list[str]:
    """把模块 docstring 拆成 `- ` 起头的条目（含缩进续行）→ 每条的整段文本。

    ⚠️ 不能用裸子串 `"未接入" in doc` 判「两栏结构存在」：正文的 ⚠️ 说明里
    同样会写到「已实现 / 未接入」字样 ⇒ 栏标题被改掉后断言**照样通过**
    （首版实测：注入 `[C]` 未红，即此因）。必须锚在**条目首行**上。
    """
    bullets: list[list[str]] = []
    for ln in doc.splitlines():
        if ln.startswith("- "):
            bullets.append([ln])
        elif bullets and ln[:2] == "  " and ln.strip():
            bullets[-1].append(ln.strip())
    return [" ".join(b) for b in bullets]


def test_ths_docstring_unimplemented_endpoints_stay_unwired():
    """`GOV-016`：docstring 标「未接入」的端点，生产代码里必须真的没有。"""
    bullets = _doc_bullets(ths_mod.__doc__ or "")
    impl = next((b for b in bullets if b.startswith("- **已实现**")), None)
    unwired = next((b for b in bullets if b.startswith("- **官方有端点但本项目未接入**")), None)
    assert impl is not None and unwired is not None, (
        "ths.py docstring 的「已实现 / 未接入」两栏结构缺失"
        " ⇒ GOV-016 的判定面被破坏（本用例判红而非跳过）"
    )

    hits = [
        f"{rel} 出现 {ep}"
        for rel, lits in _app_code_string_literals()
        for ep in _THS_UNWIRED_ENDPOINTS
        if ep in lits
    ]
    assert not hits, (
        "docstring 声称「未接入」、生产代码里却已出现该端点：\n  "
        + "\n  ".join(hits)
        + "\n⇒ 要么接错了源，要么 docstring 没同轮同步（GOV-016）"
    )

    # 归属自洽：每个未接入端点必须在**未接入栏**、且**不在已实现栏**。
    # 后半句是关键——「挪到未接入栏」若做成「两栏都写」，读者的错误预期并未消除。
    misplaced = [ep for ep in _THS_UNWIRED_ENDPOINTS if ep not in unwired]
    assert not misplaced, f"未接入栏缺 {misplaced} ⇒ 清单被删或写错了栏（GOV-016 要求「不要直接删」）"
    leaked = [ep for ep in _THS_UNWIRED_ENDPOINTS if ep in impl]
    assert not leaked, f"已实现栏出现 {leaked} ⇒ 未接入端点被当成了已具备能力（GOV-016 复发）"
