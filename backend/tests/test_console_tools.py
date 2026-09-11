"""助手工具第三批（P2-28①）：factor_profile / param_changes / agent_tasks。

三者都用既有数据源（`factors.report` 纯函数 + 已注入的 `session_factory`），一起测。
**本文件锁住两条核心纪律**：

1. **因子档案的口径必须随结论一起给出**——「样本内、未做样本外验证」。只报 IC/ICIR
   而不带这句，模型可能直接把因子当选股权重用了（那是把探索性结论当成了既定事实）。
2. 数据源缺失/为空时如实说明，**不用空结果冒充"没有"**。
"""
from __future__ import annotations

import asyncio

from app.assistant.tools import ToolCall, ToolContext, run_tool


def _run(name: str, ctx: ToolContext, **args) -> str:
    return asyncio.run(run_tool(ToolCall(name, args), ctx, cache=None))


# ---------------------------------------------------------------- 假数据源


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return self


class _Session:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, _stmt):
        return _Scalars(self._rows)


def _sf(rows):
    return lambda: _Session(rows)


class _Row:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# ---------------------------------------------------------------- 因子档案


def test_factor_profile_renders_counts_and_keeps_caliber(monkeypatch):
    """结论必须**连同口径**给出：样本内、未做样本外验证。"""
    ev = {
        "available": True, "stale": False, "age_days": 4, "max_age_days": 40,
        "counts": {"pass": 16, "conditional": 7, "fail": 14},
        "top": [{"name": "corr_pv20", "category": "volume_price", "horizon": 20,
                 "ic_mean": -0.0694, "icir": -0.7332, "direction": -1, "verdict": "PASS"}],
        "caveat": "IC 由本地全历史回测算出，样本内结论；未做样本外验证前不得直接当选股权重",
    }
    import app.factors.report as fr

    monkeypatch.setattr(fr, "ic_evidence", lambda *a, **kw: ev)
    out = _run("factor_profile", ToolContext(provider=object()))
    assert "PASS 16" in out
    assert "corr_pv20" in out
    # 口径必须出现——否则模型会拿它直接当权重用
    assert "样本内" in out and "样本外" in out


def test_factor_profile_unavailable_is_explicit(monkeypatch):
    import app.factors.report as fr

    monkeypatch.setattr(fr, "ic_evidence",
                        lambda *a, **kw: {"available": False, "reason": "报告缺失"})
    out = _run("factor_profile", ToolContext(provider=object()))
    assert "无可用" in out and "报告缺失" in out


def test_factor_profile_flags_staleness(monkeypatch):
    import app.factors.report as fr

    monkeypatch.setattr(fr, "ic_evidence", lambda *a, **kw: {
        "available": True, "stale": True, "age_days": 90, "max_age_days": 40,
        "counts": {}, "top": [], "caveat": "样本内结论",
    })
    out = _run("factor_profile", ToolContext(provider=object()))
    assert "超期" in out


def test_factor_profile_failure_is_reported(monkeypatch):
    import app.factors.report as fr

    def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(fr, "ic_evidence", boom)
    out = _run("factor_profile", ToolContext(provider=object()))
    assert "失败" in out


# ---------------------------------------------------------------- 参数变更 / 任务中心


def test_param_changes_without_source_is_explicit():
    out = _run("param_changes", ToolContext(provider=object()))
    assert "无数据源" in out


def test_param_changes_renders_rows():
    rows = [_Row(id=3, key="picks_min_pick_score", before="50", after="55",
                 status="draft", created_at=None)]
    out = _run("param_changes", ToolContext(provider=object(), session_factory=_sf(rows)))
    assert "picks_min_pick_score" in out
    assert "50" in out and "55" in out


def test_param_changes_empty_is_stated():
    out = _run("param_changes", ToolContext(provider=object(), session_factory=_sf([])))
    assert "暂无" in out


def test_agent_tasks_without_source_is_explicit():
    out = _run("agent_tasks", ToolContext(provider=object()))
    assert "无数据源" in out


def test_agent_tasks_renders_rows():
    rows = [_Row(id=1, type="review", status="succeeded", risk_level="L1",
                 created_at=None)]
    out = _run("agent_tasks", ToolContext(provider=object(), session_factory=_sf(rows)))
    assert "review" in out and "succeeded" in out and "L1" in out


def test_agent_tasks_failure_is_reported():
    def boom():
        raise RuntimeError("db down")

    out = _run("agent_tasks", ToolContext(provider=object(), session_factory=boom))
    assert "失败" in out


def test_all_three_registered():
    import app.assistant.tools as T

    specs = getattr(T, "TOOLS", None) or getattr(T, "TOOL_SPECS", {})
    for k in ("factor_profile", "param_changes", "agent_tasks"):
        assert k in specs
        assert T.tool_label(k)
