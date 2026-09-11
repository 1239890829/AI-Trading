"""因子评估产物的运行时访问层 + 月度复核调度单测（S2-11，2026-09-11）。

**为什么单独测**：收敛前 `evolution.py` 的 `factor_ic` 是硬编码
`{"available": False, "note": "月度复核（factor_ic_review）到期接入"}`——
`app/factors` 跑完了全量评估（37 因子 × 4 窗口），产物却**零运行时消费**。
本模块把这最后一米接上，用例要同时锁住三件事：读得到、读不出来要说清楚、
**不能退回硬编码**。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.core.bjtime import BJ_TZ
from app.factors import evaluate as ev
from app.factors import report as fr


def _write_report(tmp_path, *, generated_at: str | None, factors=None, summary=None):
    p = tmp_path / "eval_report.json"
    p.write_text(json.dumps({
        "generated_at": generated_at,
        "protocol": {}, "universe": {"stocks": 100}, "data_quality": {},
        "factors": factors if factors is not None else [],
        "summary": summary if summary is not None else {"pass": [], "conditional": [], "fail": []},
    }, ensure_ascii=False), encoding="utf-8")
    return p


def _sample_factors():
    return [
        {"name": "mom20", "category": "momentum", "verdict": "PASS", "coverage": 0.99,
         "best_horizon": 20, "windows": {"20": {"ic_mean": 0.031, "ic_std": 0.1,
                                                "icir": 0.31, "pos_rate": 0.55, "n_days": 2419}}},
        {"name": "mom5", "category": "momentum", "verdict": "FAIL", "coverage": 0.99,
         "best_horizon": 20, "windows": {"20": {"ic_mean": -0.034, "ic_std": 0.14,
                                                "icir": -0.24, "pos_rate": 0.40, "n_days": 2419}}},
        {"name": "vola20", "category": "volatility", "verdict": "PASS", "coverage": 0.97,
         "best_horizon": 5, "windows": {"5": {"ic_mean": -0.048, "ic_std": 0.11,
                                              "icir": -0.44, "pos_rate": 0.33, "n_days": 2400}}},
    ]


# ---------------------------------------------------------------- 读取与三态

def test_load_report_missing_file_returns_none(monkeypatch, tmp_path):
    """报告缺失 → None。**不抛异常**，由调用方决定降级（缓存层不吞业务决策）。"""
    monkeypatch.setattr(fr, "REPORT_PATH", tmp_path / "nope.json")
    assert fr.load_report() is None


def test_load_report_corrupted_returns_none(monkeypatch, tmp_path):
    """报告损坏 = 缺失。坏数据不得被当"没有有效因子"用（红线 2）。"""
    bad = tmp_path / "eval_report.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(fr, "REPORT_PATH", bad)
    assert fr.load_report() is None


def test_freshness_reports_age_and_stale(monkeypatch, tmp_path):
    now = datetime.now()
    p = _write_report(tmp_path, generated_at=(now - timedelta(days=3)).isoformat())
    monkeypatch.setattr(fr, "REPORT_PATH", p)

    fresh = fr.freshness(max_age_days=40)
    assert fresh["available"] is True
    assert fresh["stale"] is False
    assert fresh["age_days"] == 3

    p2 = _write_report(tmp_path, generated_at=(now - timedelta(days=100)).isoformat())
    monkeypatch.setattr(fr, "REPORT_PATH", p2)
    stale = fr.freshness(max_age_days=40)
    assert stale["stale"] is True
    assert stale["age_days"] == 100
    assert "超过" in (stale["reason"] or "")


def test_freshness_missing_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "REPORT_PATH", tmp_path / "nope.json")
    out = fr.freshness()
    assert out["available"] is False
    assert out["reason"]


# ---------------------------------------------------------------- top_factors

def test_top_factors_only_pass_and_sorted_by_abs_icir(monkeypatch, tmp_path):
    p = _write_report(tmp_path, generated_at=datetime.now().isoformat(),
                      factors=_sample_factors(),
                      summary={"pass": ["mom20", "vola20"], "conditional": [], "fail": ["mom5"]})
    monkeypatch.setattr(fr, "REPORT_PATH", p)

    out = fr.top_factors(10, verdicts=("PASS",))
    assert [r["name"] for r in out] == ["vola20", "mom20"]  # |−0.44| > |0.31|
    assert all(r["verdict"] == "PASS" for r in out)


def test_top_factors_direction_comes_from_measured_ic(monkeypatch, tmp_path):
    """方向**必须由实测 IC 定**，不能取 `library.FactorDef.note` 里的"预期方向"。"""
    p = _write_report(tmp_path, generated_at=datetime.now().isoformat(),
                      factors=_sample_factors())
    monkeypatch.setattr(fr, "REPORT_PATH", p)
    by_name = {r["name"]: r for r in fr.top_factors(10)}
    assert by_name["mom20"]["direction"] == 1     # ic +0.031
    assert by_name["mom5"]["direction"] == -1     # ic −0.034（负 IC 亦是信息）


def test_top_factors_missing_report_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "REPORT_PATH", tmp_path / "nope.json")
    assert fr.top_factors() == []


# ---------------------------------------------------------------- ic_evidence

def test_ic_evidence_carries_counts_and_caveat(monkeypatch, tmp_path):
    p = _write_report(tmp_path, generated_at=datetime.now().isoformat(),
                      factors=_sample_factors(),
                      summary={"pass": ["mom20", "vola20"], "conditional": ["x"], "fail": ["mom5"]})
    monkeypatch.setattr(fr, "REPORT_PATH", p)

    out = fr.ic_evidence()
    assert out["available"] is True
    assert out["counts"] == {"pass": 2, "conditional": 1, "fail": 1}
    assert out["top"]
    # 样本内结论必须自带未做样本外验证的声明（准入三级态 KB-DEC-019）
    assert "样本外" in out["caveat"]


def test_ic_evidence_unavailable_when_report_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "REPORT_PATH", tmp_path / "nope.json")
    out = fr.ic_evidence()
    assert out["available"] is False
    assert out["note"]


# ---------------------------------------------------------------- 闭环守卫

def test_evolution_no_longer_hardcodes_factor_ic_unavailable():
    """**定点回归**：`factor_ic` 不得退回硬编码的「不可用」。

    收敛前它是 `{"available": False, "note": "月度复核（factor_ic_review）到期接入"}`——
    议程一路证据永远是"没有数据"，而评估产物就在磁盘上。
    """
    src = (fr.REPORT_PATH.parents[2] / "app" / "services" / "evolution.py").read_text(encoding="utf-8")
    assert "到期接入" not in src, "factor_ic 又退回硬编码空值了"
    assert "_collect_factor_ic()" in src


def test_evolution_collect_factor_ic_returns_shaped_payload(monkeypatch, tmp_path):
    """真正调一次：形状必须符合 `{"available": ...}` 契约。"""
    from app.services import evolution as evo

    p = _write_report(tmp_path, generated_at=datetime.now().isoformat(),
                      factors=_sample_factors(),
                      summary={"pass": ["mom20"], "conditional": [], "fail": []})
    monkeypatch.setattr(fr, "REPORT_PATH", p)
    out = evo._collect_factor_ic()
    assert out["available"] is True
    assert "counts" in out and "top" in out


# ---------------------------------------------------------------- 调度门控（纯函数）

def _aware(y, m, d, h=18, mi=0):
    """生产路径里 `now` 来自 `beijing_now()`（**aware**）——测试必须用同一语义，
    否则测的是一条生产上根本不会走的路径。"""
    return datetime(y, m, d, h, mi, tzinfo=BJ_TZ)


def test_should_run_eval_fresh_report_does_not_run(monkeypatch, tmp_path):
    p = _write_report(tmp_path, generated_at=datetime.now().isoformat())
    monkeypatch.setattr(ev, "DEFAULT_OUT_PATH", p)
    monkeypatch.setattr(ev, "_load_eval_state", lambda: {})
    assert ev.should_run_eval(_aware(2026, 10, 5), run_day=1, max_age_days=40) is False


def test_should_run_eval_aware_now_vs_naive_generated_at(monkeypatch, tmp_path):
    """**定点回归（2026-09-11 实测踩到）**：`now` 是 aware、`generated_at` 是北京 naive。

    两者直接相减抛 TypeError；原实现用 `except: pass` 吞掉 ⇒ **永远按超期处理** ⇒
    每次服务启动都跑一遍 37 因子全历史扫描（分钟级），而报告其实只 4 天新鲜。

    修法是**统一时区语义后再比**，不是吞异常。本用例钉死：新鲜的报告不触发重跑。
    """
    fresh = (datetime.now() - timedelta(days=4)).isoformat()  # naive，与产物口径一致
    p = _write_report(tmp_path, generated_at=fresh)
    monkeypatch.setattr(ev, "DEFAULT_OUT_PATH", p)
    monkeypatch.setattr(ev, "_load_eval_state", lambda: {})
    assert ev.should_run_eval(_aware(2026, 9, 11), run_day=1, max_age_days=40) is False


def test_should_run_eval_before_run_day_does_not_run(monkeypatch, tmp_path):
    """报告已过期，但还没到月初 run_day → 不跑。"""
    p = _write_report(tmp_path, generated_at=(datetime.now() - timedelta(days=100)).isoformat())
    monkeypatch.setattr(ev, "DEFAULT_OUT_PATH", p)
    monkeypatch.setattr(ev, "_load_eval_state", lambda: {})
    assert ev.should_run_eval(_aware(2026, 10, 5), run_day=20, max_age_days=40) is False


def test_should_run_eval_already_attempted_today_does_not_run(monkeypatch, tmp_path):
    """失败后不得每个检查周期都重跑——37 因子全历史扫描是分钟级开销。"""
    p = _write_report(tmp_path, generated_at=(datetime.now() - timedelta(days=100)).isoformat())
    monkeypatch.setattr(ev, "DEFAULT_OUT_PATH", p)
    now = _aware(2026, 10, 5)
    monkeypatch.setattr(ev, "_load_eval_state", lambda: {"last_attempt_ymd": now.date().isoformat()})
    assert ev.should_run_eval(now, run_day=1, max_age_days=40) is False


def test_should_run_eval_stale_and_due_runs(monkeypatch, tmp_path):
    p = _write_report(tmp_path, generated_at=(datetime.now() - timedelta(days=100)).isoformat())
    monkeypatch.setattr(ev, "DEFAULT_OUT_PATH", p)
    monkeypatch.setattr(ev, "_load_eval_state", lambda: {})
    assert ev.should_run_eval(_aware(2026, 10, 5), run_day=1, max_age_days=40) is True


def test_should_run_eval_missing_report_is_treated_as_stale(monkeypatch, tmp_path):
    """报告不存在 → 该跑（而不是永远不跑）。"""
    monkeypatch.setattr(ev, "DEFAULT_OUT_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(ev, "_load_eval_state", lambda: {})
    assert ev.should_run_eval(_aware(2026, 10, 5), run_day=1, max_age_days=40) is True
