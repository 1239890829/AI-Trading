"""真实数据目录的写入口守卫（KB-ENG-63）。

## 为什么需要

端到端测试会**间接**写真实数据文件——触发点不在被测对象里，而在"扫荡式"测试上：
`test_endpoint_smoke.py` 按 openapi 遍历 **132 个 GET 端点**，其中
`/api/events/impact`、`/api/picks/leader-archive` 会走 `leader_archive.get_archive()`
⇒ TTL 到期即用测试的 MockProvider **覆盖真实档案**；写入口模块自己毫不知情，
`leader_archive.py` 的测试覆盖率为 0 也毫无异常。

**实测口径**（2026-09-12，非推断）：以运行时刻为界 `find backend/data -type f -newermt <时刻>`，
整场 2540 项 pytest 只写两个真实文件——`data/leader_archive.json`、`data/cognition_gaps.jsonl`；
其余 20 个"模块级 + 指向仓库 data/"的路径常量实测零写入。

## 本文件的三件事（对应三层防线）

1. **清点**（`test_data_path_inventory_matches_source`）：`app/` 下所有"模块级 + 指向仓库
   `data/`"的路径常量必须登记在 `_DECLARED`。新增一个未登记的常量 → 立刻失败，
   逼着回答"它会不会被测试写到"。这是 KB-ENG-60 说的**覆盖守卫**：不留"我检查过的都没问题"
   这种自证式结论，让**新增对象自动纳入**检查面。
2. **注入校验**（`test_test_writable_paths_are_sandboxed`）：登记为"测试可写"的常量，
   运行期必须已被 `conftest.py` 改指沙箱。**删掉 conftest 的隔离块 → 这里立刻变红**
   （这就是这道守卫的自检：隔离失效不能是静默的）。
3. **功能回归**：直接调两个写入入口，断言落点就是沙箱。

## 边界（诚实声明）

- 只覆盖 `data/`。`docs/` 下的写入口（如 KB-ENG-19 的 `docs/evolution/`）由各自的
  fixture 隔离，不在本守卫扫描面内——**新增 `docs/` 写入口时本文件不会报警**。
- 不比对真实文件的 mtime：常驻后端（8000）同时会写 `backend/data/`，做前后比对会引入
  **偶发假失败**（工程上比"漏检"更糟——假失败会训练人忽略门禁）。
  真正的保证来自路径不等式本身：写入口只写模块属性指向的路径，属性已不是真实路径 ⇒
  测试不可能写真实文件。
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
#: 注意层级：本文件在 `backend/tests/`，故 `parents[1]` 才是 `backend/`
#: （app/ 里的模块是 `parents[2]` 指向 backend——层级不同，别照抄）。
_BACKEND_DIR = Path(__file__).resolve().parents[1]
#: ⚠️ **真实数据目录有两处**（2026-09-14 修）：`backend/data/` 与**仓库根 `data/`**。
#: 只判前者是本节曾经的隐性漏洞：`REPO_ROOT` 派生的常量（`parents[3]` = 仓库根）
#: 全部落在**仓库根** `data/` 下，而 `_REPO_DATA not in parents` 对它们恒为真
#: ⇒ 那道断言对这些路径**等于没判**（注入验证 I1 实测：删掉隔离块仍全绿）。
#: 这正是"看起来在守、实际不守"的形态——判据的**覆盖面**与判据本身同等重要。
_REPO_ROOT_DIR = _BACKEND_DIR.parent
_REAL_DATA_DIRS: tuple[Path, ...] = (_BACKEND_DIR / "data", _REPO_ROOT_DIR / "data")


def _is_real_data_path(p: Path) -> bool:
    p = Path(p)
    return any(d == p or d in p.parents for d in _REAL_DATA_DIRS)

#: 登记表：`<app/ 下的相对路径>:<常量名>` → (测试是否会写到它, 说明)
#:
#: 「是」= 必须在 conftest 里改指沙箱（本文件第 2 项守卫会验）；
#: 「否」= 保留真实路径，理由写清楚（新增常量时必须逐条判断，不许默认填否）。
_DECLARED: dict[str, tuple[bool, str]] = {
    # --- 已隔离：实测被测试写到的入口（第三批：审计钩子实测，见 conftest 注释）---
    "services/leader_archive.py:ARCHIVE_PATH": (
        True, "全端点冒烟经 /api/events/impact·/api/picks/leader-archive 触发 get_archive 覆盖缓存"),
    "assistant/cognition.py:GAP_LOG_PATH": (
        True, "test_assistant 经 /api/assistant/chat 触发 record_gap 追加留痕"),
    "predict/storage.py:REPORT_DIR": (
        True, "test_predict 直调 save_report 落 2099 年假报告（审计钩子实测 7 个真实文件）"),
    # --- REPO_ROOT / BACKEND_ROOT 派生（扫描面补上传递闭包后才可见）---
    "review/storage.py:REPORT_DIR": (
        True, "已由 conftest 第一批隔离（此前靠 __file__ 判据不可见，故从未登记）"),
    "market/marketdb_sync.py:STATE_PATH": (
        False, "由市场库同步调度写；审计钩子实测零写入（涉及写入的用例传 tmp 出参）"),
    "picks/morning_brief.py:BRIEF_DIR": (
        False, "由盘中简报写入；审计钩子实测整场 pytest 零写入（brief_for_today 无简报即空转）"),
    "picks/heat_history.py:HEAT_DIR": (False, "由调度写（题材热度史），审计钩子实测零写入"),
    "review/config.py:METHODOLOGY_DIR": (False, "方法论只读目录（写入走人工/CLI），实测零写入"),
    # --- DuckDB 只读（测试不写库；涉及写入的用例都注入 tmp 路径） ---
    "factors/evaluate.py:DEFAULT_DB_PATH": (False, "DuckDB 只读"),
    "market/chip.py:DEFAULT_DB_PATH": (False, "DuckDB 只读"),
    "market/marketdb_freshness.py:DEFAULT_DB_PATH": (False, "DuckDB 只读（只查新鲜度）"),
    "picks/lurk_pool.py:DB": (False, "DuckDB 只读"),
    "picks/distinctiveness.py:MARKETDB_PATH": (False, "DuckDB 只读（画像评分；涉及写入的用例注入 tmp 出参）"),
    "picks/rps.py:DEFAULT_DB_PATH": (False, "DuckDB 只读"),
    "research/strategy_verify.py:DEFAULT_DB_PATH": (False, "DuckDB 只读"),
    "services/shadow_eval.py:DEFAULT_DB_PATH": (False, "DuckDB 只读（写入路径由入参给）"),
    # --- 由调度 / CLI 写，测试不触发该路径 ---
    "factors/evaluate.py:DEFAULT_OUT_PATH": (False, "由因子评估 CLI 写，测试传 tmp 出参"),
    "factors/evaluate.py:EVAL_STATE_PATH": (False, "由议程写，测试走注入"),
    "factors/report.py:REPORT_PATH": (False, "由因子报告 CLI 写，测试传 tmp 出参"),
    "market/board_flow.py:_STORE_DIR": (False, "由调度写（盘后榜单），测试用例注入 tmp"),
    "market/fund_flow.py:_FLOW_STORE": (False, "由调度写（日频资金流），测试打源不落盘"),
    "market/minute_backfill.py:parquet_dir_default": (False, "只读 parquet 目录"),
    "market/minute_backfill.py:parquet_dir_tdx": (False, "只读 parquet 目录"),
    "market/minute_decisions.py:SCAN_DIR": (False, "由调度写；用例 monkeypatch 到 tmp_path"),
    "market/trade_calendar.py:_PERSIST_PATH": (False, "写前有劣质备源守卫（<80% 不覆盖官方日历）"),
    "picks/position_engine.py:_PLAN_DIR": (False, "由盘中计划器写，测试用例注入 tmp"),
    "research/verify_registry.py:VERIFY_DIR": (False, "由核验 CLI 写，测试传 tmp 出参"),
    "sentiment/metric_history.py:_STORE_PATH": (False, "由调度写；用例注入内存/tmp"),
    "services/replay_gate.py:BASELINE_PATH": (False, "只读基线（写入走 CLI 出参）"),
}

_HOWTO = """\
新增"模块级 + 指向仓库 data/"的路径常量时必须做二选一：
  · 会被测试写到（尤其被 test_endpoint_smoke 这类扫荡式用例间接触发）→ 先去
    tests/conftest.py 的「运行期落盘隔离」块里改指沙箱，再登记为 True；
  · 不会被测试写到 → 登记为 False 并写清理由（不许留空、不许默认填否）。"""


def _scan_source() -> dict[str, str]:
    """扫描 `app/` 下模块级的路径常量，返回 `key → 赋值表达式源码`。

    ⚠️ **判据含传递闭包**（2026-09-14 补，KB-ENG-72 同族）：
    仅看「表达式里有没有 `__file__`」会漏掉整个 `REPO_ROOT` 派生族——
    `REPORT_DIR = REPO_ROOT / "data" / "review" / "predictions"` 里没有 `__file__`，
    而 `REPO_ROOT = Path(__file__).resolve().parents[3]` 在**同一个文件**里。
    实测代价：`app/predict/storage.py:REPORT_DIR` 因此长期不在扫描面内，
    而它恰好是唯一被测试写真实文件的那个（审计钩子实测 7 个 2099 年假报告
    落在生产目录 `data/review/predictions/`）。

    故分两步：先在**全仓**范围内收敛出「`__file__` 派生的常量名集合」
    （`REPO_ROOT` 等，含跨文件 import 的用法——`heat_history.py` 就是
    `from app.picks.morning_brief import REPO_ROOT`），再判定各常量是否引用它们。
    名字判据是启发式，但**方向是安全的**：多收 → 逼登记；少收才是漏检。
    """
    parsed: list[tuple[Path, dict[str, str]]] = []
    for py in sorted(_APP_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        consts: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names, value = [node.target.id], node.value
            else:
                continue
            if value is None or not names:
                continue
            src = ast.unparse(value)
            for name in names:
                consts[name] = src
        parsed.append((py, consts))

    # 传递闭包：`__file__` 直接派生 ⇒ 引用这些名字的常量也算派生（迭代到不动点）
    derived: set[str] = set()
    for _, consts in parsed:
        derived |= {n for n, s in consts.items() if "__file__" in s}
    changed = True
    while changed:
        changed = False
        for _, consts in parsed:
            for n, s in consts.items():
                if n in derived:
                    continue
                if any(re.search(rf"\b{re.escape(d)}\b", s) for d in derived):
                    derived.add(n)
                    changed = True

    found: dict[str, str] = {}
    for py, consts in parsed:
        for name, src in consts.items():
            if name not in derived or "'data'" not in src:
                continue
            found[f"{py.relative_to(_APP_DIR).as_posix()}:{name}"] = src
    return found


def test_data_path_inventory_matches_source():
    """**覆盖守卫**：源码里的 data 路径常量与登记表必须逐项对齐（新增/删除都失败）。"""
    scanned = set(_scan_source())
    declared = set(_DECLARED)

    undeclared = sorted(scanned - declared)
    assert not undeclared, (
        f"这些模块级 data 路径常量还没有登记：{undeclared}\n{_HOWTO}")

    stale = sorted(declared - scanned)
    assert not stale, (
        f"登记表里有源码已不存在的项（常量改名/删除后请同步）：{stale}")


def test_test_writable_paths_are_sandboxed():
    """**注入校验**：登记为 True 的常量，运行期必须已被 conftest 改指沙箱。

    删掉 conftest 的隔离块 → 本用例立刻变红（隔离失效必须可见，不能静默）。
    """
    import app.assistant.cognition as cognition
    import app.predict.storage as predict_storage
    import app.review.storage as review_storage
    import app.services.leader_archive as leader_archive

    writable = [k for k, (flag, _) in _DECLARED.items() if flag]
    assert writable, "登记表里没有任何「测试可写」项，疑似登记被误清空"

    modules = {
        "services/leader_archive.py:ARCHIVE_PATH": leader_archive,
        "assistant/cognition.py:GAP_LOG_PATH": cognition,
        "predict/storage.py:REPORT_DIR": predict_storage,
        "review/storage.py:REPORT_DIR": review_storage,
    }
    for key in writable:
        module = modules.get(key)
        assert module is not None, f"{key} 登记为可写但没有对应的模块映射（守卫自身需同步）"
        attr = key.split(":", 1)[1]
        current = Path(getattr(module, attr))
        assert not _is_real_data_path(current), (
            f"{key} 仍指向真实数据目录：{current}\n"
            f"conftest.py 的「运行期落盘隔离」块被删或失效了。\n{_HOWTO}")


# ---------------------------------------------------------------- 功能回归


class _StubProvider:
    """涨停池桩：不落真实文件、不打网络（days=2 → 只问两天）。"""

    async def get_limit_up_pool(self, trade_date):
        return []


def test_leader_archive_write_lands_in_sandbox():
    """`get_archive(force=True)` 的重建结果必须落在沙箱，而不是真实档案。"""
    from app.services import leader_archive as la

    real = _BACKEND_DIR / "data" / "leader_archive.json"
    sandbox = Path(la.ARCHIVE_PATH)
    assert sandbox != real

    archive = asyncio.run(la.get_archive(_StubProvider(), days=2, force=True))

    assert sandbox.exists(), "重建后沙箱里应有档案文件"
    on_disk = json.loads(sandbox.read_text(encoding="utf-8"))
    assert on_disk["built_at"] == archive["built_at"]
    assert "themes" in on_disk


def test_record_gap_write_lands_in_sandbox():
    """`record_gap()` 的留痕必须落在沙箱（真实台账不再被每轮全量追加一行）。"""
    from app.assistant import cognition as cog

    real = _BACKEND_DIR / "data" / "cognition_gaps.jsonl"
    sandbox = Path(cog.GAP_LOG_PATH)
    assert sandbox != real

    rec = cog.record_gap("我没有该股的龙虎榜数据", "数据路径守卫用例")
    assert rec is not None, "留痕失败（返回 None）"
    assert sandbox.exists()

    lines = [json.loads(l) for l in sandbox.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(r.get("at") == rec["at"] for r in lines), "沙箱台账里没有本次留痕"


def test_predict_save_report_lands_in_sandbox(tmp_path):
    """`save_report()` 的落盘必须落在沙箱（生产目录 `data/review/predictions/`
    里有真实预判 `20260831.json`，测试若用真实日期会静默覆盖真数据）。

    与上两条配对：这一条是**功能回归**（真写一次），上一条是**结构断言**。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.watchlist import Base
    from app.predict import storage as ps
    from app.predict.schemas import PredictionReport

    # ⚠️ 真实路径在**仓库根** data/，不是 backend/data/（见文件头的两处数据目录说明）
    real = _REPO_ROOT_DIR / "data" / "review" / "predictions"
    sandbox = Path(ps.REPORT_DIR)
    assert sandbox != real and not _is_real_data_path(sandbox)

    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    report = PredictionReport(created_at="2026-09-14T00:00:00Z", context="weekend",
                              target_date="20991231")
    ps.save_report(sf, report)

    assert (sandbox / "20991231.json").exists(), "落盘没进沙箱"
    assert not (real / "20991231.json").exists(), "落到了真实生产目录"


def test_sandbox_root_not_inside_repo_data():
    """**前提钉死**（§6.5b #7，2026-09-13 裁定：挂账边界转结构保证）。

    `test_test_writable_paths_are_sandboxed` 用 `_is_real_data_path()` 判定
    「仍指向真实数据」——该判据隐含前提：**沙箱自身不在任何真实 data/ 里**
    （否则合法的沙箱路径也会命中同一不等式 ⇒ 恒假报）。当前 conftest 用
    `tempfile.mkdtemp`（/tmp 下）⇒ 前提天然成立；本用例把前提显式化——
    若将来有人把沙箱挪进 data/，这里会红并直接给出两条修法，而不是留一道
    说不清何时触发的隐性误报。

    2026-09-14 增补：真实数据目录有**两处**（`backend/data` 与**仓库根 `data/`**），
    本用例与判据用同一函数 ⇒ 两处一起覆盖，不会出现"只守了一半"。
    """
    import app.assistant.cognition as cognition
    import app.predict.storage as predict_storage
    import app.services.leader_archive as leader_archive

    for current in (Path(cognition.GAP_LOG_PATH), Path(leader_archive.ARCHIVE_PATH),
                    Path(predict_storage.REPORT_DIR)):
        assert not _is_real_data_path(current), (
            f"沙箱路径 {current} 位于真实数据目录之内——`_is_real_data_path` 判据"
            f"（覆盖 {[str(d) for d in _REAL_DATA_DIRS]}）会把合法沙箱误判为真实路径。二选一："
            "①把 conftest 的 _DATA_SANDBOX 挪出 data/（现状 tempfile.mkdtemp）；"
            "②把判据改成与真实文件路径的精确比较。改动前先读本文件头部边界说明。"
        )
