"""回放对比门禁（`app/services/replay_gate.py`）单测（P2-17 首次真实触发后的回归网）。

**背景**：这条门禁自 2026-09-08 实现起**从未被真实执行过**——触发条件是「涉及
`backend/app/picks/` 的自动代码合入」，此前无此类合入。"已实现"被当成了"可用"。
2026-09-11 首次真实触发，**连炸三个缺陷**，全部是"从未跑过才可能存在"的类型：

1. `subprocess` 命令写死 `["python3", ...]` ⇒ 系统解释器没有项目依赖
   （`ModuleNotFoundError: pydantic_settings`）⇒ 门禁在任何脱离 venv 的环境都失败；
2. `_parse_stats` 找「日均换手%」，而报告实际输出「日均换手率」——标签从未对齐；
3. 「日均组合分」报告里**根本没有这一行**（数据其实早已算在 `result["daily"][i]["score_avg"]`，
   只是没输出）⇒ 三缺一 ⇒ 恒解析失败。

外加一个隐藏的：`| 日均换手率 | 40.0% |` 数值后是百分号不是竖线 ⇒ 正则也吃不下。

**所以本文件的核心不是"测功能"，是钉死这四处不许回潮** —— 一条常年不触发的门禁，
一旦回归没人会发现。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import replay_gate as rg

BACKEND = Path(__file__).resolve().parents[1]
REPLAY_SCRIPT = BACKEND / "scripts" / "replay_picks.py"

#: 门禁要从报告里抓的三个指标（**字面量必须与报告模板逐字一致**）
REQUIRED_LABELS = ("日均换手率", "平均持有天数", "日均组合分")


# ---------------------------------------------------------------- 防回潮：四处缺陷


def test_gate_uses_sys_executable_not_bare_python3():
    """**定点回归（缺陷 1）**：子进程必须用 `sys.executable`。

    写死 `"python3"` 会拿到系统解释器，它没有项目依赖 ⇒ 门禁必然失败，
    而且只在极少数触发条件下才跑，平时看不出来。
    """
    src = Path(rg.__file__).read_text(encoding="utf-8")
    assert '"python3"' not in src and "'python3'" not in src, (
        "回放命令不得写死 python3 —— 用 sys.executable（当前解释器）"
    )
    assert "sys.executable" in src


def test_parse_labels_match_report_template():
    """**定点回归（缺陷 2/3）**：抓的标签必须在回放脚本的报告模板里真实存在。

    这是防再次漂移的关键——当初「日均换手%」对不上「日均换手率」、
    「日均组合分」压根没输出，都是靠"门禁从不触发"才活到今天。
    """
    src = REPLAY_SCRIPT.read_text(encoding="utf-8")
    for label in REQUIRED_LABELS:
        assert f"| {label} |" in src, (
            f"回放报告模板里没有「| {label} |」这一行 —— 门禁会解析失败。"
            f"（数据其实已在 result['daily'][i]['score_avg']，补输出即可）"
        )


def test_parse_stats_handles_percent_suffix():
    """**定点回归（缺陷 4）**：换手率写成 `40.0%`，数值后是百分号不是竖线。

    少了正则里的 `%?`，该指标永远解析不到 ⇒ 三缺一 ⇒ 门禁恒 error。
    """
    report = (
        "| 指标 | 完整策略 |\n|---|---|\n"
        "| 日均换手率 | 40.0% |\n"
        "| 平均持有天数 | 2.17 |\n"
        "| 日均组合分 | 68.13 |\n"
    )
    assert rg._parse_stats(report) == {
        "avg_turnover_pct": 40.0, "avg_holding_days": 2.17, "avg_score": 68.13,
    }


def test_parse_stats_returns_none_when_any_metric_missing():
    """缺任一指标必须返回 None（**绝不臆造**），让调用方走 error 分支。"""
    assert rg._parse_stats("| 日均换手率 | 40.0% |\n") is None
    assert rg._parse_stats("") is None


# ---------------------------------------------------------------- 阈值判定


@pytest.fixture()
def gate(tmp_path, monkeypatch):
    """把基线路径与回放子进程都隔离掉，只测判定逻辑。"""
    base_file = tmp_path / "baseline.json"
    monkeypatch.setattr(rg, "BASELINE_PATH", base_file)
    captured = {}

    def _run(cmd, **_kw):
        class _P:
            returncode = 0
            stdout, stderr = "", ""

        # 报告内容由测试通过 captured["report"] 注入
        Path("/tmp/evo-replay-latest.md").write_text(captured["report"], encoding="utf-8")
        return _P()

    monkeypatch.setattr(rg.subprocess, "run", _run)
    return captured


def _set_baseline(gate, monkeypatch, *, turnover=40.0, holding=2.0, score=70.0):
    monkeypatch.setattr(rg, "_load_baseline", lambda: {
        "saved_at": "2026-09-11T00:00:00", "commit": "abc1234", "days": 10,
        "stats": {"avg_turnover_pct": turnover, "avg_holding_days": holding, "avg_score": score},
    })


def _read_saved_baseline() -> dict:
    """直读落盘文件——`_load_baseline` 在多数用例里被 monkeypatch 掉，
    用它会读到注入的假基线而不是真正写下去的那份。"""
    import json

    return json.loads(rg.BASELINE_PATH.read_text(encoding="utf-8"))


def test_first_run_creates_baseline_with_commit(gate, monkeypatch):
    """首跑无基线 ⇒ 建档（含 commit 与 stats），verdict=baseline_created。"""
    monkeypatch.setattr(rg, "_load_baseline", lambda: None)
    gate["report"] = "| 日均换手率 | 40.0% |\n| 平均持有天数 | 2.17 |\n| 日均组合分 | 68.13 |\n"
    out = rg.run_comparison(days=10, commit="28b07e9")
    assert out["ok"] is True
    assert out["verdict"] == "baseline_created"
    saved = _read_saved_baseline()
    assert saved["commit"] == "28b07e9" and saved["days"] == 10
    assert saved["stats"]["avg_score"] == 68.13


def test_degraded_when_turnover_regresses_beyond_threshold(gate, monkeypatch):
    """换手恶化 > 10pp ⇒ degraded（不自动回滚，只提示复评）。"""
    _set_baseline(gate, monkeypatch, turnover=40.0, score=70.0)
    gate["report"] = "| 日均换手率 | 55.0% |\n| 平均持有天数 | 2.0 |\n| 日均组合分 | 70.0 |\n"
    out = rg.run_comparison(days=10, commit="x")
    assert out["verdict"] == "degraded"
    assert out["deltas"]["turnover_pp"] == pytest.approx(15.0)


def test_degraded_when_score_drops_beyond_threshold(gate, monkeypatch):
    """组合分下降 > 1 分 ⇒ degraded。"""
    _set_baseline(gate, monkeypatch, turnover=40.0, score=70.0)
    gate["report"] = "| 日均换手率 | 40.0% |\n| 平均持有天数 | 2.0 |\n| 日均组合分 | 68.5 |\n"
    out = rg.run_comparison(days=10, commit="x")
    assert out["verdict"] == "degraded"
    assert out["deltas"]["score"] == pytest.approx(-1.5)


def test_ok_within_thresholds(gate, monkeypatch):
    """阈值内的小幅波动 ⇒ ok（门禁不该对正常抖动报警）。"""
    _set_baseline(gate, monkeypatch, turnover=40.0, score=70.0)
    gate["report"] = "| 日均换手率 | 45.0% |\n| 平均持有天数 | 2.0 |\n| 日均组合分 | 69.5 |\n"
    out = rg.run_comparison(days=10, commit="x")
    assert out["verdict"] == "ok"
    assert out["deltas"]["turnover_pp"] == pytest.approx(5.0)


def test_turnover_regress_ignored_when_old_baseline_is_zero(gate, monkeypatch):
    """旧基线换手为 0 时不判恶化——0 → 任何值都是"无穷大恶化"，属基线无效。"""
    _set_baseline(gate, monkeypatch, turnover=0.0, score=70.0)
    gate["report"] = "| 日均换手率 | 30.0% |\n| 平均持有天数 | 2.0 |\n| 日均组合分 | 70.0 |\n"
    out = rg.run_comparison(days=10, commit="x")
    assert out["verdict"] == "ok"


def test_error_when_stats_unparsable(gate, monkeypatch):
    """报告解析失败 ⇒ error（不是 ok）。静默把一个坏结果当通过是门禁最坏的失效。"""
    _set_baseline(gate, monkeypatch)
    gate["report"] = "回放报告残缺，没有表格"
    out = rg.run_comparison(days=10, commit="x")
    assert out["ok"] is False and out["verdict"] == "error"


def test_baseline_is_refreshed_after_each_run(gate, monkeypatch):
    """每次跑完都要写回基线——否则永远在跟一份陈旧基线比。"""
    _set_baseline(gate, monkeypatch, turnover=40.0, score=70.0)
    gate["report"] = "| 日均换手率 | 42.0% |\n| 平均持有天数 | 2.0 |\n| 日均组合分 | 71.0 |\n"
    rg.run_comparison(days=10, commit="newcommit")
    saved = _read_saved_baseline()
    assert saved["commit"] == "newcommit"
    assert saved["stats"]["avg_score"] == 71.0
