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


# ---------------------------------------------------------------- 做T决策库（P2-28① 最后一项）


def test_minute_decisions_without_source_is_explicit():
    out = _run("minute_decisions", ToolContext(provider=object()), symbol="600519")
    assert "无数据源" in out


def test_minute_decisions_rejects_bad_limit(monkeypatch):
    out = _run("minute_decisions", ToolContext(provider=object(), session_factory=_sf([])),
               limit="abc")
    assert "参数不合法" in out


def test_minute_decisions_empty_is_stated(monkeypatch):
    import app.market.minute_decisions as md

    monkeypatch.setattr(md, "list_decisions", lambda *a, **kw: [])
    out = _run("minute_decisions", ToolContext(provider=object(), session_factory=_sf([])),
               symbol="600519")
    assert "暂无记录" in out


def test_minute_decisions_explains_open_is_not_failure(monkeypatch):
    """**关键纪律**：open = 未到结算窗口，不是失败。

    若不加这句，模型看到一堆 open 可能读成"决策全错"。
    """
    import app.market.minute_decisions as md

    items = [
        {"symbol": "600519", "trigger_ts": "10:05", "bias": "buy", "signal_price": 10.1,
         "realized_spread_pct": 0.4, "outcome": "correct"},
        {"symbol": "600519", "trigger_ts": "10:30", "bias": "buy", "signal_price": 10.2,
         "realized_spread_pct": None, "outcome": "open"},
    ]
    monkeypatch.setattr(md, "list_decisions", lambda *a, **kw: items)
    out = _run("minute_decisions", ToolContext(provider=object(), session_factory=_sf([])),
               symbol="600519")
    assert "600519" in out
    assert "correct 1" in out and "open 1" in out, "必须给出各 outcome 的计数"
    assert "不是失败" in out


def test_minute_decisions_failure_is_reported():
    def boom():
        raise RuntimeError("db down")

    out = _run("minute_decisions", ToolContext(provider=object(), session_factory=boom))
    assert "失败" in out


def test_minute_decisions_registered():
    import app.assistant.tools as T

    specs = getattr(T, "TOOLS", None) or getattr(T, "TOOL_SPECS", {})
    assert "minute_decisions" in specs
    assert T.tool_label("minute_decisions") == "做T决策"


# ---------------------------------------------------------------- 预警触发记录（清单外补登记）


def test_alert_events_without_source_is_explicit():
    out = _run("alert_events", ToolContext(provider=object()))
    assert "无数据源" in out


def test_alert_events_rejects_bad_limit():
    out = _run("alert_events", ToolContext(provider=object(), session_factory=_sf([])),
               limit="xyz")
    assert "参数不合法" in out


def test_alert_events_empty_is_stated(monkeypatch):
    import app.repositories.alert_repo as ar

    monkeypatch.setattr(ar.AlertRepository, "list_events", lambda self, **kw: [])
    out = _run("alert_events", ToolContext(provider=object(), session_factory=_sf([])))
    assert "暂无触发记录" in out


def test_alert_events_renders_rows_and_declares_beijing_time(monkeypatch):
    """`triggered_at` 是北京时间（beijing_now_naive），**不得再 +8h**——
    输出里必须点明"时间为北京时间"，避免模型二次换算。
    """
    import datetime as _dt

    import app.repositories.alert_repo as ar

    ev = _Row(id=7, rule_id=1, symbol="600519", trigger_value=10.5, threshold=10.0,
              triggered_at=_dt.datetime(2026, 9, 11, 14, 59, 31))
    monkeypatch.setattr(ar.AlertRepository, "list_events", lambda self, **kw: [ev])
    out = _run("alert_events", ToolContext(provider=object(), session_factory=_sf([])))
    assert "600519" in out
    assert "14:59:31" in out, "时间必须原样展示（北京时间），不得被二次偏移"
    assert "北京时间" in out


def test_alert_events_filters_by_symbol(monkeypatch):
    import app.repositories.alert_repo as ar

    evs = [_Row(id=1, rule_id=1, symbol="600519", trigger_value=1, threshold=1,
                triggered_at=None),
            _Row(id=2, rule_id=1, symbol="000001", trigger_value=1, threshold=1,
                 triggered_at=None)]
    monkeypatch.setattr(ar.AlertRepository, "list_events", lambda self, **kw: evs)
    out = _run("alert_events", ToolContext(provider=object(), session_factory=_sf([])),
               symbol="600519")
    assert "600519" in out and "000001" not in out


def test_alert_events_failure_is_reported():
    def boom():
        raise RuntimeError("db down")

    out = _run("alert_events", ToolContext(provider=object(), session_factory=boom))
    assert "失败" in out


def test_alert_events_registered():
    import app.assistant.tools as T

    specs = getattr(T, "TOOLS", None) or getattr(T, "TOOL_SPECS", {})
    assert "alert_events" in specs
    assert T.tool_label("alert_events") == "预警记录"
