"""Jev assistant tool router: bounded groups, fail-open behavior, and telemetry."""
from __future__ import annotations

import pytest

import app.assistant.jev_tool_router as jr
from app.assistant.prompt import build_system_prompt
from app.assistant.tools.registry import TOOL_SPECS, tool_manifest
from app.core import jev_client
from app.core.config import settings


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    jev_client._reset_metrics_for_tests()
    monkeypatch.setattr(settings, "jev_enabled", True)
    monkeypatch.setattr(settings, "jev_assistant_tool_mode", "shadow")
    monkeypatch.setattr(settings, "jev_assistant_tool_min_noul", 0.60)
    monkeypatch.setattr(settings, "jev_assistant_tool_max_groups", 3)


def _result(probabilities: dict[str, float]):
    return {
        "ok": True,
        "model": "jev-test",
        "answers": {
            group: {"type": "noul", "noul": probabilities.get(group, 0.05)}
            for group in jr.TOOL_GROUPS
        },
        "usage": {"input_tokens": 100},
        "latency_ms": 3,
    }


def test_groups_cover_tool_registry_exactly():
    jr.validate_group_coverage()
    assert jr.all_group_tools() == set(TOOL_SPECS)


def test_router_selects_multiple_relevant_groups(monkeypatch):
    probs = {g: 0.05 for g in jr.TOOL_GROUPS}
    probs.update(events_news=0.94, account_positions=0.88)
    monkeypatch.setattr(jr, "evaluate", lambda *_a, **_k: _result(probs))

    out = jr.route_tool_groups("我持仓里的股票今天有什么重要公告和事件？")
    expected = set(jr.TOOL_GROUPS["events_news"]["tools"]) | set(
        jr.TOOL_GROUPS["account_positions"]["tools"]
    )
    assert out["ok"] is True and out["narrowed"] is True
    assert set(out["groups"]) == {"events_news", "account_positions"}
    assert set(out["tools"]) == expected

def test_router_no_group_above_threshold_fails_open(monkeypatch):
    monkeypatch.setattr(jr, "evaluate", lambda *_a, **_k: _result({}))
    out = jr.route_tool_groups("解释一下什么叫 T+1")
    assert out["ok"] is True and out["narrowed"] is False
    assert set(out["tools"]) == set(TOOL_SPECS)
    assert out["reason"] == "no_group_above_threshold"


def test_router_invalid_jev_shape_fails_open(monkeypatch):
    bad = _result({})
    bad["answers"]["market_realtime"]["noul"] = "bad"
    monkeypatch.setattr(jr, "evaluate", lambda *_a, **_k: bad)
    out = jr.route_tool_groups("看一下大盘")
    assert out["narrowed"] is False
    assert set(out["tools"]) == set(TOOL_SPECS)
    assert out["reason"] == "invalid_noul"


def test_router_off_never_calls_jev(monkeypatch):
    monkeypatch.setattr(settings, "jev_assistant_tool_mode", "off")
    monkeypatch.setattr(
        jr, "evaluate", lambda *_a, **_k: pytest.fail("off mode must not call Jev")
    )
    out = jr.route_tool_groups("看一下贵州茅台")
    assert out["reason"] == "off" and out["narrowed"] is False


def test_tool_manifest_can_narrow_prompt_without_changing_registry():
    text = tool_manifest({"quotes", "kline"})
    assert "{{tool:quotes|" in text
    assert "{{tool:kline|" in text
    assert "{{tool:paper|" not in text
    assert "paper" in TOOL_SPECS
    with pytest.raises(ValueError):
        tool_manifest({"not_a_real_tool"})


def test_system_prompt_uses_narrowed_manifest_only():
    text = build_system_prompt(None, tools_enabled=True, tool_names={"quotes"})
    assert "{{tool:quotes|" in text
    assert "{{tool:backtest|" not in text

def test_shadow_observation_records_reduction_and_coverage():
    route = {
        "ok": True,
        "tools": list(jr.TOOL_GROUPS["events_news"]["tools"]),
    }
    jr.observe_route(route, ["events", "news"])
    row = jev_client.metrics_snapshot()["routing"]["assistant_tools_observed"]
    assert row["observations"] == 1
    assert row["baseline_items"] == len(TOOL_SPECS)
    assert row["selected_items"] == len(set(route["tools"]))
    assert row["coverage_checked"] == 1 and row["coverage_missed"] == 0
    assert row["item_reduction_pct"] > 0


def test_shadow_observation_detects_missed_used_tool():
    route = {"ok": True, "tools": ["events", "news"]}
    jr.observe_route(route, ["events", "paper"])
    row = jev_client.metrics_snapshot()["routing"]["assistant_tools_observed"]
    assert row["coverage_checked"] == 1 and row["coverage_missed"] == 1
    assert row["coverage_miss_rate"] == 1.0
