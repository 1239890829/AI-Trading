"""影子跳过分支留痕（N4，`app/picks/shadow.py`）单测。

**背景**：2026-09-04 复盘立了 N1–N9，其中 N4 是「影子持仓从未执行且无留痕，
无法区分『没执行』与『执行了但 0 成交』」。到 2026-09-11 核实：`shadow_loop` 只有
「执行完成」会落盘，其余分支（非交易日 / 窗口未到 / 闸门不可判 / runner 未就绪）
**只打日志** ⇒ 那些日子在台账上是空白，排查只能翻日志。

本文件锁住留痕的**两条必须守住的性质**（比"能写文件"重要得多）：

1. **不覆盖已执行的摘要**——若当天已经跑过，绝不能把它覆盖成 skipped，
   那等于把真实执行记录抹掉，比不留痕更糟（会让人以为那天没跑）；
2. **同原因幂等**——循环每 60s 一拍，窗口未到会反复触发，
   原因没变就不重写，避免每分钟一次无谓 IO（也避免文件 mtime 一直变）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.picks import shadow as sh


@pytest.fixture()
def shadow_dir(tmp_path, monkeypatch):
    """把影子台账目录指向临时目录。"""
    d = tmp_path / "shadow"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sh, "_shadow_dir", lambda: d)
    return d


def _read(d: Path, day: str) -> dict | None:
    p = d / f"{day}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ---------------------------------------------------------------- 基本落盘


def test_skip_is_persisted_with_reason(shadow_dir):
    p = sh.persist_shadow_skip("2026-09-11", sh.SKIP_NOT_TRADING_DAY)
    assert p is not None and p.exists()
    rec = _read(shadow_dir, "2026-09-11")
    assert rec["skipped"] == sh.SKIP_NOT_TRADING_DAY
    assert rec["skipped_reason"] == sh.SKIP_NOT_TRADING_DAY
    assert rec["day"] == "2026-09-11"
    assert rec["updated_at"]


def test_skip_records_extra_context(shadow_dir):
    """跳过要带上「为什么」——光写个 reason 不够排查（比如闸门不可判的 caveat）。"""
    sh.persist_shadow_skip("2026-09-11", sh.SKIP_GATE_NOT_JUDGEABLE,
                           caveat="竞价数据缺失", gate_summary={"executable": 0})
    rec = _read(shadow_dir, "2026-09-11")
    assert rec["caveat"] == "竞价数据缺失"
    assert rec["gate_summary"] == {"executable": 0}


# ---------------------------------------------------------------- 性质一：不覆盖已执行


def test_skip_never_overwrites_a_real_execution(shadow_dir):
    """**最关键的一条**：已执行过就绝不能覆盖成 skipped。

    否则等于把真实执行记录抹掉——比不留痕更糟。
    """
    day = "2026-09-11"
    executed = {"day": day, "bought": [{"symbol": "600519", "qty": 100}],
                "sold": [], "skipped": []}
    (shadow_dir / f"{day}.json").write_text(json.dumps(executed, ensure_ascii=False),
                                            encoding="utf-8")

    assert sh.persist_shadow_skip(day, sh.SKIP_WINDOW_CLOSED) is None
    assert _read(shadow_dir, day) == executed, "真实执行摘要被覆盖了"


def test_execution_after_skip_replaces_the_skip_record(shadow_dir):
    """反过来：先记了 skip，之后真执行了 ⇒ 执行摘要应当**覆盖** skip。"""
    day = "2026-09-11"
    sh.persist_shadow_skip(day, sh.SKIP_WINDOW_CLOSED)
    assert _read(shadow_dir, day)["skipped"] == sh.SKIP_WINDOW_CLOSED

    # 模拟 run_morning 落盘（走同一个 _persist 路径的等价写入）
    (shadow_dir / f"{day}.json").write_text(
        json.dumps({"day": day, "bought": [{"symbol": "000001"}], "sold": [], "skipped": []},
                   ensure_ascii=False), encoding="utf-8")
    rec = _read(shadow_dir, day)
    assert rec["bought"] and not rec.get("skipped_reason")


# ---------------------------------------------------------------- 性质二：同原因幂等


def test_same_reason_is_idempotent(shadow_dir):
    """循环每 60s 一拍，窗口未到会反复触发 ⇒ 同原因不重写。"""
    day = "2026-09-11"
    p1 = sh.persist_shadow_skip(day, sh.SKIP_WINDOW_CLOSED, minute_of_day=500)
    p2 = sh.persist_shadow_skip(day, sh.SKIP_WINDOW_CLOSED, minute_of_day=501)
    assert p1 is not None
    assert p2 is None, "同原因重复写入（会造成每分钟一次无谓 IO）"
    assert _read(shadow_dir, day)["minute_of_day"] == 500


def test_reason_change_is_recorded(shadow_dir):
    """原因变了要更新（窗口未到 → 非交易日，是不同信息）。"""
    day = "2026-09-11"
    sh.persist_shadow_skip(day, sh.SKIP_WINDOW_CLOSED)
    sh.persist_shadow_skip(day, sh.SKIP_NOT_TRADING_DAY)
    assert _read(shadow_dir, day)["skipped_reason"] == sh.SKIP_NOT_TRADING_DAY


def test_repeated_beats_do_not_rewrite_file(tmp_path, monkeypatch):
    """**集成式验证**：连打 20 拍同一原因，文件内容与 mtime 都不应变化。"""
    d = tmp_path / "shadow"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sh, "_shadow_dir", lambda: d)
    day = "2026-09-11"
    sh.persist_shadow_skip(day, sh.SKIP_WINDOW_CLOSED)
    p = d / f"{day}.json"
    mtime0 = p.stat().st_mtime_ns
    body0 = p.read_text(encoding="utf-8")
    for i in range(20):
        sh.persist_shadow_skip(day, sh.SKIP_WINDOW_CLOSED, minute_of_day=500 + i)
    assert p.stat().st_mtime_ns == mtime0, "文件被重复重写"
    assert p.read_text(encoding="utf-8") == body0


# ---------------------------------------------------------------- 常量与读取


def test_skip_constants_are_stable_strings():
    """跳过原因是**落盘字符串**，改名等于台账历史断裂 ⇒ 钉住取值。"""
    assert sh.SKIP_NOT_TRADING_DAY == "not_trading_day"
    assert sh.SKIP_WINDOW_CLOSED == "window_not_open"
    assert sh.SKIP_GATE_NOT_JUDGEABLE == "gate_not_judgeable"
    assert sh.SKIP_NO_RUNNER == "runner_not_ready"


def test_load_execution_log_returns_none_when_missing(shadow_dir):
    assert sh.load_execution_log("1970-01-01") is None


def test_load_execution_log_returns_none_on_corrupt_file(shadow_dir):
    (shadow_dir / "2026-09-11.json").write_text("{ broken", encoding="utf-8")
    assert sh.load_execution_log("2026-09-11") is None
