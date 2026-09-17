"""**交易日时效**双禁守卫（F7，2026-09-14）。

## 为什么需要它

2026-09-14 用户连续两次报障（「怎么今天全都是休市」/「盘面板块的全部数据还是十一号的」），
闭证后根因只有一个反模式，却有**多处独立实例**：

> **交易日相关数据的缓存/判定，用「固定时长」表达时效；
> 而它真实的时效语义是「必须覆盖今天」。**

配套的第二个错法：**把 `unknown`（未判定）塌缩成「确定」**——
`is_trade_day(days, today)` 在日历未覆盖今天时返回 `False`，
被当成「**确认休市**」（而不是「不知道」），于是全站标休市、盘前简报不生成、
哨兵与盘中监控静默跳过。

已修的两个实例（12h 日历 TTL / 24h 自建日历缓存）有 `test_dated_consistency.py`
的行为守卫钉住；**但那只覆盖"已发生的实例"**——当时并没有任何东西能拦住
「下次再写一个 `cache_on(hub, "provider.trading_days", 86400)`，
或在另一个模块写 `if not is_trade_day(days, today): return`」。

本文件的四条守卫正是补这个缺口（写法与 `test_bjtime.py` 的权威唯一性守卫同源：
**宁严勿松 + 只认机械判据**）。

## 四条不变量

| 守卫 | 不变量 | 判据 |
|---|---|---|
| B1 | 二态原语 `is_trade_day()` 只许在权威模块里出现 | `app/`+`scripts/` AST 扫描调用点 |
| B2 | **不得给交易日语义的缓存起名**（时效不能用时长表达） | `cache_on` 缓存名 token 扫描 |
| B3 | 一切 ≥6h 的缓存 TTL 必须**逐条登记理由** | TTL 常量求值 + 登记表集合相等 |
| B4 | 防回潮：`market_snapshot` 不得再自建日历缓存；覆盖判据不得被删 | 定点断言 |

## 判据为什么用 AST 而不是文本匹配（B1）

修完 6 处后，`app/` 里仍有 **7 处** `is_trade_day(` 字样出现在**注释与 docstring**
（说明"原实现是这么写的"）。文本匹配会把它们全判为违规，于是只能靠自豁免糊过去
（`test_bjtime.py` 的 `SELF` 就是这么来的）。AST 只认**真正的调用节点**，
说明性文字天然不误伤——同 `test_import_lint` 用 AST 的动因。

## 本守卫的**判定面与常驻局限**（诚实标注）

- B3 只扫两种**已知的** TTL 载体签名：`cache_on(holder, name, ttl)` 与
  `TTLCache._cached(name, ttl, key, factory)`。**若将来新增第三种间接层
  （自己包一层 cache 工厂），B3 扫不到**——需同步把它加进 `_TTL_CARRIERS`。
- 不经 `cache_on`/`_cached` 的缓存（如直接 `TTLCache(...)`、`functools.lru_cache`）
  不在判定面内。
- B1 的判定面是 `app/` 与 `scripts/`，**不含 `tests/`**（测试需要直接驱动二态原语
  来校验它自己的语义）。
- **B1 的判据盲区已部分收口（B1b，2026-09-14）**：`X in days_list` / `days_list.index(X)`
  一类**本地变量赋值**不产生 `ast.Call` ⇒ 原 B1 扫不到。现存实例曾被记为
  `api/routes/market.py:331` 的 `is_trade_day = now_bj.date() in days_list`
  （其 `except` 分支退化为 `now_bj.weekday() < 5`，故**同族但宿主路径不同**：
  只影响 15:05 后的惰性补录闸门，不影响用户可见的「休市」判定）。
  **该处已改为三态并顺手改名 `trade_day_ok`**（不遮蔽二态原语），
  同时新增 **B1b**：外部模块**不得自行绑定 `is_trade_day` 这个名字**——
  命名即语义声明，复用该名字等于宣称「这里有一个二态交易日布尔」。
  ⚠️ **仍存的盲区**：B1b 只认「名字」，若有人换个变量名写等价的二态比较
  （如 `ok = today in days_list`），**两道守卫都扫不到** ⇒
  **B1/B1b 全绿 ≠ 全仓已三态化**。
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.source_ast import read_source_ast

BACKEND = Path(__file__).resolve().parents[1]

#: 权威模块：交易日判定的唯一实现处，独占二态原语 `is_trade_day()`。
#: 它内部两处调用（`is_trade_day_on` 的收敛、`market_open_state` 的 covered 分支）
#: 是**合法的**——它们自己负责了覆盖守卫。
AUTHORITY = "app/market/trade_calendar.py"

#: 交易日语义的缓存名 token：命中即禁止。
#: 理由：这类缓存的时效语义是「必须覆盖今天」，**无法**用固定时长正确表达。
_DATE_NAME_TOKENS = ("trading_days", "trade_date", "calendar", "snapshot", "daily", "日历")

#: 长 TTL 阈值：≥ 6h 的缓存足以跨过「午夜 / 开盘」边界，必须显式论证。
LONG_TTL_SEC = 6 * 3600

#: TTL 载体的调用签名 → 「TTL 是第几个位置参数」。
_TTL_CARRIERS: dict[str, int] = {
    "cache_on": 2,   # core/ttl_cache.cache_on(holder, name, ttl, *, maxsize)
    "_cached": 1,    # services/akshare_ext.AkshareExtService._cached(name, ttl, key, factory)
}

#: 已登记的 ≥6h 缓存位点 → 理由（键 = (相对路径, 缓存名)：「缓存名」比行号稳定得多，
#: 行号会随无关改动漂移，把守卫变成"每逢改文件就红"的噪声源）。
#: **新增任何一处 ≥6h 缓存都必须在此显式论证**，否则 B3 变红。
#: 登记的门槛不是"TTL 长"本身，而是必须说清：**为什么它不会因跨日而陈旧**。
#: 判据锚点 = 2026-09-14 的根因——「时效语义是**必须覆盖今天**，却用固定时长表达」。
LONG_TTL_REGISTRY: dict[tuple[str, str], str] = {
    ("app/api/routes/market_flow.py", "market.board-fund.main-board"): (
        "**不适用「覆盖今天」判据**：缓存内容 = 个股的所属板块/行业归属"
        "（`get_company_profile` 的 board_groups），语义是「这只股是做什么的」，"
        "**不带交易日维度**——成分调整才变，与「今天是不是交易日」无关；"
        "键是 symbol（非日期），因此不存在'午夜前填充 ⇒ 次日不含今天'的失效形态"
        "（该形态的前提正是'缓存内容按日切片'）。6h 的作用是避免自选轮询反复打 F10。"
        "**宿主文件变更记录**：本键原为 `app/api/routes/market.py`，2026-09-15 `IMP-005` 批 3 "
        "把该文件按业务域切片（共 8 分片 + 门面），本位点随 `board-fund` 域落入 `market_flow.py`；"
        "**缓存语义与 TTL 均未变，仅搬家**——守卫按 `(宿主文件, 位点名)` 集合相等判定，"
        "故改名后正确报红（[[KB-ENG-95]] 的『位点宿主变更』形态）。"
    ),
    ("app/services/akshare_ext.py", "ext-macro-cpi"): (
        "**不适用「覆盖今天」判据**：`macro_china_cpi` = 月度 CPI 序列，"
        "披露周期为月（发布日期之外数据不变），无交易日维度、键为 `\"all\"` 不含日期。"
        "残余影响：12h 内最后一次刷新若恰好跨过月度发布时点，序列末点最多滞后 12h；"
        "属**展示型序列的末点滞后**，不参与任何「今天是交易日 / 数据属于哪一天」的判定，"
        "与 2026-09-14 的缺陷（全站标休市 / 简报不生成）不同类。"
    ),
    ("app/services/akshare_ext.py", "ext-margin-acct"): (
        "**不适用「覆盖今天」判据**：`stock_margin_account_info` = 融资融券账户统计序列，"
        "键为 `\"all\"`（整条序列，非按日切片），调用方取最近若干行展示，"
        "不参与「今天是交易日 / 数据属于哪一天」的判定。"
        "⚠️ **残余（已识别、未改）**：该源为**日频披露**，12h TTL 会让序列末点"
        "最多滞后半天（披露时点之后最长 12h 才刷新）——若要求末点即时，"
        "应下调 TTL（属缓存时效调参，按「改进先提后做」不在本轮范围内）。"
    ),
}

#: TTL 无法静态求值的位点 → 理由（fail-closed：无法证明它 < 6h，就得有人来说清）。
UNRESOLVED_TTL_REGISTRY: dict[tuple[str, str], str] = {
    ("app/services/akshare_ext.py", "<cache_on@_cached>"): (
        "**转发层，自身不含时效语义**：`AkshareExtService._cached(name, ttl, key, factory)`"
        "（:133）把形参 `ttl` 原样交给 `cache_on(self, cache_name, ttl)`；"
        "真实 TTL 一律由各业务方法在调用处给出，而**那些调用点已被 B3 覆盖**"
        "（`_cached` 为 `_TTL_CARRIERS` 之一，idx=1 即 TTL 实参）："
        "实测全部 ≤3600s，唯 `ext-macro-cpi` / `ext-margin-acct` 为 43200s "
        "且已逐条登记于 `LONG_TTL_REGISTRY`。本位点无法静态求值只是因为读到的是形参。"
    ),
}


# ---------------------------------------------------------------- 源码遍历


def _iter_sources(*subdirs: str):
    for sub in subdirs:
        for p in sorted((BACKEND / sub).rglob("*.py")):
            yield p, p.relative_to(BACKEND).as_posix()


def _parse(path: Path) -> ast.Module | None:
    try:
        return read_source_ast(path)
    except SyntaxError:  # pragma: no cover - 语法错误由 pyflakes/pytest 先报
        return None


def _callee_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _const_int(node: ast.AST, mods: dict[str, int]) -> int | None:
    """求值一个小整数常量表达式（只允许常数、模块级常量名、`*` `+` 与条件表达式）。

    `300 if period == "daily" else 60` 取两支的**最大值**（保守方向：宁可高估 TTL，
    让"可能 ≥6h"的位点暴露出来，也不要低估后放行）。
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return None
        if isinstance(node.value, int):
            return node.value
        if isinstance(node.value, float) and float(node.value).is_integer():
            return int(node.value)
        return None
    if isinstance(node, ast.Name):
        return mods.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Add)):
        left = _const_int(node.left, mods)
        right = _const_int(node.right, mods)
        if left is None or right is None:
            return None
        return left * right if isinstance(node.op, ast.Mult) else left + right
    if isinstance(node, ast.IfExp):
        a = _const_int(node.body, mods)
        b = _const_int(node.orelse, mods)
        if a is None or b is None:
            return None
        return max(a, b)
    return None


def _module_consts(tree: ast.Module) -> tuple[dict[str, int], dict[str, str]]:
    """收模块级常量：名字 → 整数值 / 名字 → 字符串值（单层，不做跨模块解析）。"""
    ints: dict[str, int] = {}
    strs: dict[str, str] = {}
    for n in tree.body:
        if not isinstance(n, ast.Assign) or len(n.targets) != 1:
            continue
        t = n.targets[0]
        if not isinstance(t, ast.Name):
            continue
        if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            strs[t.id] = n.value.value
            continue
        v = _const_int(n.value, ints)
        if v is not None:
            ints[t.id] = v
    return ints, strs


def _str_value(node: ast.AST, strs: dict[str, str]) -> str | None:
    """取字符串实参：字面量或模块级常量名（f-string / 表达式返回 None）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return strs.get(node.id)
    return None


def _enclosing_names(tree: ast.Module) -> dict[int, str]:
    """`id(节点)` → 最近的**内层**函数名。

    BFS 顺序下「后写覆盖先写」⇒ 最内层函数胜出（`ast.walk` 先给外层函数、
    再给内层函数，内层对其子节点的赋值发生得更晚）。

    用途：TTL 实参取不到「缓存名」时的**稳定**回退标识。实例
    `akshare_ext._cached()` 内部的转发层调用就属此列；若用行号做键，
    该文件任何一次改动都会让登记表条目「凭空失效」⇒ 守卫沦为噪声源。
    """
    out: dict[int, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(n):
                out[id(child)] = n.name
    return out


# ---------------------------------------------------------------- B1


def _binary_trade_day_calls(tree: ast.Module) -> list[int]:
    """AST 找 `is_trade_day(...)` 的**调用节点**（不含说明文字）。

    刻意只匹配 `is_trade_day`：`board_flow._is_trade_day` 是它自己的
    **可注入间接层**，别名指向三态 `is_trade_day_on`，是合法用法。
    """
    hits: list[int] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            name = _callee_name(n)
            if name == "is_trade_day" and not name.startswith("_"):
                hits.append(n.lineno)
    return hits


def test_b1_binary_trade_day_primitive_confined_to_authority():
    """B1 · 二态 `is_trade_day()` 只许在 `trade_calendar.py` 内部使用。

    *回退即红*：把任一处 `is_trade_day_on(today, days)` 改回
    `is_trade_day(days, today)` ⇒ 该文件立刻出现在违规列表里。

    为什么必须禁：二态原语**无法表达「未判定」**，调用方必然要写
    `if not is_trade_day(...)`，于是把 `unknown` 静默塌缩成「确定休市」——
    这正是 2026-09-14 那 6 处缺陷的共同形态（盘前简报不生成、哨兵与盘中
    监控静默 idle、复盘跳过）。
    """
    offenders: list[str] = []
    for path, rel in _iter_sources("app", "scripts"):
        if rel == AUTHORITY:
            continue
        tree = _parse(path)
        if tree is None:
            continue
        for lineno in _binary_trade_day_calls(tree):
            offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "以下位置直接调用了二态 `is_trade_day()`，应改为三态 "
        "`is_trade_day_on(d, days)`（`None` = 未判定，不得当「非交易日」用）：\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------- B1b


def _binds_trade_day_name(tree: ast.Module) -> list[int]:
    """找**绑定** `is_trade_day` 这个名字的节点行号（赋值 / 定义 / for / 海象）。

    与 B1 互补：B1 抓「调用」，B1b 抓「**自己算一个二态布尔却复用这个名字**」——
    `is_trade_day = now_bj.date() in days_list` 不产生 `ast.Call`，原 B1 扫不到
    （2026-09-14 真实漏检，见 `api/routes/market.py` 原第 331 行）。
    """
    hits: list[int] = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.name == "is_trade_day":
                hits.append(n.lineno)
        targets: list[ast.expr] = []
        if isinstance(n, ast.Assign):
            targets = list(n.targets)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            targets = [n.target]
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            targets = [n.target]
        elif isinstance(n, ast.NamedExpr):
            targets = [n.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "is_trade_day":
                hits.append(n.lineno)
    return hits


def test_b1b_binary_trade_day_name_not_rebound_outside_authority():
    """B1b · 外部模块**不得自行绑定** `is_trade_day` 这个名字。

    *回退即红*：在任一非权威模块写 `is_trade_day = <任意表达式>` ⇒ 变红。

    为什么按**名字**禁：名字是**语义声明**。`is_trade_day` 在本仓的既有含义
    就是「二态原语（无未判定态）」，外部模块复用它等于宣称「这里有一个二态
    交易日布尔」——而二态布尔**无法表达「未判定」**，用它做闸门必然把
    `unknown` 静默塌缩成「确定非交易日」。2026-09-14 的
    `api/routes/market.py:331` 正是这样漏过了原 B1（只认 `ast.Call`）。

    注意 `_is_trade_day`（前导下划线）**不受限**：`board_flow` 的同名成员是
    它自己的**可注入间接层**（别名指向三态 `is_trade_day_on`），是合法用法，
    与二态原语不是一回事。判据写窄会造出假阴性、写太宽会误伤——此处取精确名。
    """
    offenders: list[str] = []
    for path, rel in _iter_sources("app", "scripts"):
        if rel == AUTHORITY:
            continue
        tree = _parse(path)
        if tree is None:
            continue
        for lineno in _binds_trade_day_name(tree):
            offenders.append(f"{rel}:{lineno}")
    assert not offenders, (
        "以下位置自行绑定了 `is_trade_day` 这个名字（该名字在本仓 = 二态原语，"
        "无法表达「未判定」）。请改用三态 `is_trade_day_on(d, days)`（`None` = 未判定），"
        "并换一个不遮蔽原语的名字（如 `trade_day_ok`）：\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------- B2


def test_b2_no_cache_named_after_trading_days():
    """B2 · **不得给交易日语义的数据起缓存名**（时效不能用「时长」表达）。

    *回退即红*：加回 `cache_on(hub, "provider.trading_days", 86400)` ⇒ 变红。

    为什么是「按名字禁」而不是「按时长禁」：名字是**语义声明**——
    一个叫 `trading_days` 的缓存，其正确性取决于「里面有没有今天」，
    而不是「活了多少秒」。2026-09-14 两处缺陷（12h、24h）都栽在这里：
    源头日历是**尾随窗口、不含未来日期**，任何午夜前填充的缓存**必然不含次日**，
    于是"TTL 还没到"与"数据已经过期"完全脱钩。
    """
    offenders: list[str] = []
    for path, rel in _iter_sources("app", "scripts"):
        tree = _parse(path)
        if tree is None:
            continue
        ints, strs = _module_consts(tree)
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call) or _callee_name(n) != "cache_on":
                continue
            if len(n.args) < 2:
                continue
            name = _str_value(n.args[1], strs)
            if name is None:
                continue  # 动态名：由 B3 的 UNRESOLVED 通道兜底
            hit = [t for t in _DATE_NAME_TOKENS if t in name]
            if hit:
                offenders.append(f"{rel}:{n.lineno}: name={name!r}（命中 {hit}）")
    assert not offenders, (
        "以下缓存以**交易日语义**命名，不得用固定时长表达时效——"
        "应直接调用 `trade_calendar`（其 `_is_fresh()` 用「覆盖今天」判据）"
        "或改为不缓存：\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------- B3


def _long_ttl_sites() -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], str]]:
    """扫两种 TTL 载体，返回 (≥6h 位点 → ttl, 无法求值位点 → 实参文本)。"""
    long_ttl: dict[tuple[str, str], int] = {}
    unresolved: dict[tuple[str, str], str] = {}
    for path, rel in _iter_sources("app", "scripts"):
        tree = _parse(path)
        if tree is None:
            continue
        ints, strs = _module_consts(tree)
        enclosing = _enclosing_names(tree)
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            carrier = _callee_name(n)
            if carrier not in _TTL_CARRIERS:
                continue
            idx = _TTL_CARRIERS[carrier]
            if len(n.args) <= idx:
                continue
            # 位点标识：优先用「缓存名」（cache_on 的 arg1 / _cached 的 arg0），
            # 取不到就退回**所属函数名**，最后才退回行号（尽力而为，稳定优先）。
            name = None
            if carrier == "cache_on" and len(n.args) > 1:
                name = _str_value(n.args[1], strs)
            elif carrier == "_cached" and n.args:
                name = _str_value(n.args[0], strs)
            if name is None:
                fn = enclosing.get(id(n))
                name = f"<{carrier}@{fn}>" if fn else f"<{carrier}@{rel}:{n.lineno}>"
            key = (rel, name)

            ttl = _const_int(n.args[idx], ints)
            if ttl is None:
                try:
                    unresolved[key] = ast.unparse(n.args[idx])
                except Exception:  # pragma: no cover
                    unresolved[key] = "<无法反解析>"
                continue
            if ttl >= LONG_TTL_SEC:
                long_ttl[key] = ttl
    return long_ttl, unresolved


def test_b3_long_ttl_caches_are_registered_with_reason():
    """B3 · 一切 ≥6h 的缓存 TTL 必须逐条登记理由（**新增即红**）。

    *回退即红*：把 `market_snapshot` 的 24h 日历缓存加回来 ⇒ 新位点不在登记表 ⇒ 变红。

    判据是**集合相等**（不只是"登记表里的都合法"）：新出现的 ≥6h 位点要报错，
    **被删掉的登记条目同样要报错**——否则登记表会悄悄腐烂成"一堆没人管的旧理由"。
    """
    found, _unresolved = _long_ttl_sites()
    new = sorted(set(found) - set(LONG_TTL_REGISTRY))
    stale = sorted(set(LONG_TTL_REGISTRY) - set(found))
    assert not new, (
        "以下缓存 TTL ≥ 6h 但未登记理由。≥6h 足以跨过午夜/开盘边界，"
        "请先在 `LONG_TTL_REGISTRY` 里写明「为何它不会跨日陈旧」（或改用覆盖判据）：\n  "
        + "\n  ".join(f"{rel}::{name} = {found[(rel, name)]}s" for rel, name in new)
    )
    assert not stale, (
        "`LONG_TTL_REGISTRY` 里以下条目已不存在（位点被删/改名），请同步清理，"
        "否则登记表会腐烂成无人核对的旧理由：\n  "
        + "\n  ".join(f"{rel}::{name}" for rel, name in stale)
    )


def test_b3b_unresolved_ttls_are_registered():
    """B3b · TTL 无法静态求值的位点必须登记（fail-closed：证不了它 < 6h 就得有人来说）。

    *回退即红*：新增一处 `cache_on(state, "x", some_ttl_var)` 而不登记 ⇒ 变红。
    """
    _long, unresolved = _long_ttl_sites()
    new = sorted(set(unresolved) - set(UNRESOLVED_TTL_REGISTRY))
    stale = sorted(set(UNRESOLVED_TTL_REGISTRY) - set(unresolved))
    assert not new, (
        "以下位点的 TTL 无法静态求值，未登记。请确认其量级（<6h 可改为字面量；"
        "≥6h 需登记进 `LONG_TTL_REGISTRY`）后在 `UNRESOLVED_TTL_REGISTRY` 写明：\n  "
        + "\n  ".join(f"{rel}::{name}  实参={unresolved[(rel, name)]}" for rel, name in new)
    )
    assert not stale, (
        "`UNRESOLVED_TTL_REGISTRY` 里以下条目已不存在，请同步清理：\n  "
        + "\n  ".join(f"{rel}::{name}" for rel, name in stale)
    )


# ---------------------------------------------------------------- B4 防回潮


def test_b4_market_snapshot_no_longer_owns_a_calendar_cache():
    """B4 · `services/market_snapshot.py` 不得再自建日历缓存（防回潮）。

    它曾以 `cache_on(hub, "provider.trading_days", 86400)` 自建 24h 缓存，
    且与 `api/routes/market` **共用同一实例**（`cache_on` 以 `(holder,name)` 为键、
    **首次 TTL 生效**）⇒ 14 处调用点的默认日期集体回退一天。
    组件已删；删掉的东西**必须有守卫拦着不让回来**（同 `test_bjtime` §3 的思路）。

    判据用 AST（该文件注释里仍**叙述**着这段被删的历史，文本匹配会误伤）。
    """
    path = BACKEND / "app/services/market_snapshot.py"
    tree = _parse(path)
    assert tree is not None
    calls = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call) and _callee_name(n) == "cache_on"]
    assert not calls, (
        f"`app/services/market_snapshot.py` 又出现了 cache_on 调用（行 {calls}）："
        "交易日历必须走 `trade_calendar` 的覆盖判据，不得在此自建时长缓存"
    )


def test_b4_coverage_based_freshness_still_exists():
    """B4 · 覆盖判据本体不得被删/被改回「只看时长」。

    这两条断言是「判据还在」的最小钉子：`_is_fresh()` 是覆盖判据的唯一实现，
    `_SESSION_OPEN` 是它的第二条覆盖路径（「快照抓于今日 09:30 后」）。
    若有人把 `_is_fresh` 改回纯 TTL，`test_dated_consistency.py` 的 G1 会红；
    这里再钉住「函数与常量都还在」，避免被整体删除后变成 AttributeError 式误报。
    """
    src = (BACKEND / AUTHORITY).read_text(encoding="utf-8")
    assert "def _is_fresh(" in src, "覆盖判据 `_is_fresh()` 被删了"
    assert "_SESSION_OPEN" in src, "覆盖判据的第二条路径（09:30 下界）被删了"
    assert "覆盖今天" in src, "`_is_fresh()` 必须写明「覆盖今天」的判据语义（防止被改回纯时长）"
