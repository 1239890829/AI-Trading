"""题材热度前向落库（app/picks/heat_history.py）回归测试。

锁四类行为：
1. 字段映射：收盘事实 → JSONL 行，tag 稳定排序，环境字段全行共享。
2. 幂等：文件已存在即跳过且**不碰行情**（磁盘哨兵前置）；题材为空不落盘
   （留待下个 tick 重试，绝不拿空文件冒充当日记录）。
3. 诚实降级：日历不可用 / 数据归属日与日历不一致 → 显式 reason，不落错文件。
4. 读取器：按日期升序拼接，损坏文件跳过不中断，limit_days 截窗。
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.picks import heat_history as hh


def _facts(**over) -> dict:
    facts = {
        "pool_date": "2026-09-01",
        "pool_count": 42,
        "themes": {
            "种业": {"limit_up": 3, "max_boards": 4, "leader_symbol": "600598",
                     "leader_pct": 10.0, "pct": 2.8},
            "AI应用": {"limit_up": 5, "max_boards": 2, "leader_symbol": "300001",
                       "leader_pct": 20.0, "pct": None},
        },
        "env": {"phase": "发酵", "promo_percentile": 49.2},
        "missing": [],
    }
    facts.update(over)
    return facts


@pytest.fixture
def heat_dir(tmp_path, monkeypatch):
    d = tmp_path / "heat"
    monkeypatch.setattr(hh, "HEAT_DIR", d)
    return d


@pytest.fixture
def fake_clock(monkeypatch):
    """交易日历桩：2026-09-01。"""
    import app.market.trade_calendar as tc

    async def _fake_trading_days(_provider):
        return [date(2026, 9, 1)]
    monkeypatch.setattr(tc, "trading_days", _fake_trading_days)
    monkeypatch.setattr(tc, "last_trade_date", lambda days, asof=None: date(2026, 9, 1))


def _patch_facts(monkeypatch, facts, calls: list):
    import app.picks.review_intraday as ri

    async def _stub(state):
        calls.append(state)
        return facts
    monkeypatch.setattr(ri, "collect_closing_facts", _stub)


def _state():
    return SimpleNamespace(hub=SimpleNamespace(provider=None))


# ---------------------------------------------------------------- 纯函数


def test_heat_rows_from_facts_maps_fields():
    rows = hh.heat_rows_from_facts(_facts())
    assert [r["tag"] for r in rows] == ["AI应用", "种业"]  # tag 稳定排序
    r = rows[1]
    assert r["date"] == "2026-09-01"
    assert r["limit_up"] == 3 and r["max_boards"] == 4
    assert r["leader_symbol"] == "600598" and r["leader_pct"] == 10.0
    assert r["pct"] == 2.8  # 匹配不到板块的题材如实 None（见 AI应用行）
    assert rows[0]["pct"] is None
    for row in rows:  # 环境与池计数全行共享
        assert row["phase"] == "发酵" and row["promo_percentile"] == 49.2
        assert row["pool_count"] == 42


# ---------------------------------------------------------------- record_daily_heat


def test_record_daily_heat_happy_path(heat_dir, fake_clock, monkeypatch):
    calls: list = []
    _patch_facts(monkeypatch, _facts(), calls)
    out = asyncio.run(hh.record_daily_heat(_state()))
    assert out["recorded"] == 2 and out["date"] == "2026-09-01"
    path = heat_dir / "20260901.jsonl"
    assert path.exists()
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 2 and lines[0]["tag"] == "AI应用"
    assert not list(heat_dir.glob("*.tmp"))  # 原子写：tmp 已 replace 掉


def test_record_daily_heat_already_recorded_skips_fetch(heat_dir, fake_clock, monkeypatch):
    path = heat_dir / "20260901.jsonl"
    heat_dir.mkdir(parents=True)
    path.write_text('{"date": "2026-09-01"}\n', encoding="utf-8")

    import app.picks.review_intraday as ri

    async def _must_not_call(state):  # 磁盘哨兵前置：绝不拉收盘事实
        raise AssertionError("collect_closing_facts should not be called")
    monkeypatch.setattr(ri, "collect_closing_facts", _must_not_call)

    out = asyncio.run(hh.record_daily_heat(_state()))
    assert out == {"recorded": 0, "reason": "already_recorded", "date": "2026-09-01"}


def test_record_daily_heat_empty_themes_no_file(heat_dir, fake_clock, monkeypatch):
    calls: list = []
    _patch_facts(monkeypatch, _facts(themes={}, missing=["涨停池不可用"]), calls)
    out = asyncio.run(hh.record_daily_heat(_state()))
    assert out["recorded"] == 0 and out["reason"] == "empty_themes"
    assert not list(heat_dir.glob("*.jsonl"))  # 下个 tick 重试，不冒充已记录


def test_record_daily_heat_calendar_unavailable(heat_dir, monkeypatch):
    import app.market.trade_calendar as tc

    async def _boom(_provider):
        raise RuntimeError("日历炸了")
    monkeypatch.setattr(tc, "trading_days", _boom)
    out = asyncio.run(hh.record_daily_heat(_state()))
    assert out["recorded"] == 0 and out["reason"] == "calendar_unavailable"


def test_record_daily_heat_date_mismatch(heat_dir, fake_clock, monkeypatch):
    """事实归属日 ≠ 日历日（跨午夜竞态）→ 不落错名文件。"""
    _patch_facts(monkeypatch, _facts(pool_date="2026-08-29"), [])
    out = asyncio.run(hh.record_daily_heat(_state()))
    assert out["recorded"] == 0 and out["reason"] == "date_mismatch"
    assert not list(heat_dir.glob("*.jsonl"))


def test_record_daily_heat_env_missing_still_records_but_warns(
    heat_dir, fake_clock, monkeypatch
):
    """env 缺失照常落库（题材事实比环境更难重取），但必须 warning 留痕——
    首跑实测（2026-09-02）：重启补落时快照未预热，env 静默变 null。"""
    # 不走 caplog：TestClient 测试的 basicConfig 会改 root logger 状态，
    # 全量跑时 caplog 捕获不到（test_paper_audit 同款坑），直接桩掉模块 log。
    warnings_seen: list[str] = []
    _noop = lambda *a, **k: None  # noqa: E731
    monkeypatch.setattr(
        hh, "log",
        SimpleNamespace(
            warning=lambda fmt, *a: warnings_seen.append(fmt),
            info=_noop, debug=_noop, error=_noop, exception=_noop,
        ),
    )
    calls: list = []
    _patch_facts(
        monkeypatch,
        _facts(env={"phase": None, "promo_percentile": None},
               missing=["情绪环境不可用（快照未预热）"]),
        calls,
    )
    out = asyncio.run(hh.record_daily_heat(_state()))
    assert out["recorded"] == 2  # 不阻断落库
    path = heat_dir / "20260901.jsonl"
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert all(r["phase"] is None and r["promo_percentile"] is None for r in lines)
    assert any("env missing" in m for m in warnings_seen)


# ---------------------------------------------------------------- load_heat_rows


def test_load_heat_rows_sorted_skip_corrupt_and_limit(heat_dir):
    heat_dir.mkdir(parents=True)
    (heat_dir / "20260901.jsonl").write_text(
        '{"date": "2026-09-01", "tag": "种业"}\n', encoding="utf-8")
    (heat_dir / "20260829.jsonl").write_text(
        '{"date": "2026-08-29", "tag": "AI应用"}\n{broken\n', encoding="utf-8")
    (heat_dir / "20260828.jsonl").write_text(
        '{"date": "2026-08-28", "tag": "低空经济"}\n', encoding="utf-8")

    rows = hh.load_heat_rows()
    # 逐行跳过：0829 的坏行丢弃、好行保留，其余文件完整
    assert [r["date"] for r in rows] == ["2026-08-28", "2026-08-29", "2026-09-01"]
    assert rows[1]["tag"] == "AI应用"  # 0829 的好行仍在
    assert [r["date"] for r in hh.load_heat_rows(limit_days=1)] == ["2026-09-01"]


# ------------------------------------------------- skyrocket 档案的读写同源守卫
#
# 事故（2026-09-14 实测，非推断）：写侧 `heat_history.SKYROCKET_PATH` 是
# `REPO_ROOT / "data" / ...`（**绝对** ⇒ 仓库根），读侧
# `distinctiveness.SKYROCKET_PATH` 曾是裸相对 `Path("data/picks/heat/skyrocket.jsonl")`
# （⇒ 按进程 CWD 解析 ⇒ 标准启动下落 `backend/data/picks/heat/`）。
# 两者是**不同文件**，而 `load_skyrocket_hits` 在文件缺失时**静默返回 `{}`**
# ⇒ 权重 20/100 的 `W_SPIKE` 分项在生产中恒 0，且没有任何降级标记。
# 生产数据实测：写侧 83 个 symbol / 100 次入榜，读侧默认路径命中 **0**。
#
# 下面三条是**成对**的：正例（写得进就读得到）／反例（缺档案必须显式披露，
# 不许静默给 0）／结构（读侧不得自持路径常量，从形式上消灭"再分叉"的可能）。


class _SkyProvider:
    """飙升榜桩：只回固定行，不打网络。"""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    async def get_skyrocket_list(self, period: str) -> list[dict]:  # noqa: ARG002
        return self._rows


_SKY_ROWS = [
    {"rank": 1, "symbol": "600519", "name": "贵州茅台", "heat": 99.0, "rank_change": 0},
    {"rank": 2, "symbol": "000001", "name": "平安银行", "heat": 88.0, "rank_change": 3},
]


@pytest.fixture
def sky_env(tmp_path, monkeypatch):
    """档案整体搬到 tmp——**只 patch 写侧两个属性**。

    读侧是"调用时取写侧属性"（`distinctiveness.skyrocket_path()`），因此**不需要**
    第二处 patch。若将来读侧又自持一份常量，本夹具就 patch 不到它、下面第一条
    用例立刻变红——这正是我们要的**结构性**保护。
    """
    d = tmp_path / "heat"
    monkeypatch.setattr(hh, "HEAT_DIR", d)
    monkeypatch.setattr(hh, "SKYROCKET_PATH", d / "skyrocket.jsonl")
    return d


def test_skyrocket_reader_reads_what_writer_wrote(sky_env, fake_clock):
    """**读写同源**（回归守卫）：写侧落盘后，读侧默认路径必须命中同一份文件。

    这条守卫的形式很关键——它**只 patch 写侧**。若读侧再出现一份自持路径，
    读写就会各指一处，此用例立刻判红（而不是像事故那样两边都静默成功）。
    """
    from app.picks import distinctiveness as dv

    state = SimpleNamespace(hub=SimpleNamespace(provider=_SkyProvider(_SKY_ROWS)))
    out = asyncio.run(hh.record_daily_skyrocket(state))
    assert out["recorded"] == len(_SKY_ROWS) and out["date"] == "2026-09-01"

    assert dv.skyrocket_path() == sky_env / "skyrocket.jsonl", "读侧没有跟着写侧走"
    assert dv.load_skyrocket_hits({"600519", "000001"}) == {"600519": 1, "000001": 1}, (
        "写侧已落盘而读侧读不到 = 读写分叉（W_SPIKE 分项会静默恒 0）")
    assert dv.skyrocket_note()["available"] is True


def test_skyrocket_missing_archive_is_disclosed_not_silent(sky_env):
    """**反例（成对）**：档案不存在时命中为 0 **必须显式披露**，不许静默给 0。

    只测正例等于只守一个方向：事故的表现恰恰是"读不到也不报错"，
    所以必须另有一条钉子钉住"读不到时要说出来"。
    """
    from app.picks import distinctiveness as dv

    assert not (sky_env / "skyrocket.jsonl").exists()
    assert dv.load_skyrocket_hits({"600519"}) == {}   # 缺档案 → 空（既有语义不变）
    note = dv.skyrocket_note()
    assert note["available"] is False
    assert note["note"] and "skyrocket_60d" in note["note"], (
        "缺档案时必须说明该分项按 0 计，否则「画像 0」会被误读成「确实没上过榜」")


def test_distinctiveness_does_not_own_path_constants():
    """**结构性防线**：读侧不得自持 skyrocket / lhb 路径常量。

    路径写两处 = 迟早分叉（一方改锚定、另一方没改，且两边都不报错）。
    读侧只允许"引用写侧单点"，故本用例直接对源码断言：
    `app/picks/distinctiveness.py` 的**模块级赋值**里不许出现这两个名字。
    """
    import ast
    from pathlib import Path as _P

    from app.picks import distinctiveness as dv

    tree = ast.parse(_P(dv.__file__).read_text(encoding="utf-8"))
    assigned: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            assigned |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned.add(node.target.id)
    assert "SKYROCKET_PATH" not in assigned, (
        "读侧又自持了 SKYROCKET_PATH —— 必须改为调用 `heat_history.SKYROCKET_PATH`")
    assert "LHB_DIR" not in assigned, (
        "读侧又自持了 LHB_DIR —— 上榜次数走 `lhb_archive.count_symbol_hits` 的默认参数")
    # 写侧仍是唯一真相源，且它自己必须是绝对锚定
    assert hh.SKYROCKET_PATH == hh.HEAT_DIR / "skyrocket.jsonl"
    assert hh.HEAT_DIR.is_absolute(), (
        "写侧 HEAT_DIR 变成了相对路径——解析基准会退化为进程 CWD，读写分叉会复现")
