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
    probs.update(events_news=0.94, market_structure=0.88)
    monkeypatch.setattr(jr, "evaluate", lambda *_a, **_k: _result(probs))

    out = jr.route_tool_groups("600519 今天的强势有哪些依据？")
    expected = set(jr.TOOL_GROUPS["events_news"]["tools"]) | set(
        jr.TOOL_GROUPS["market_structure"]["tools"]
    )
    assert out["ok"] is True and out["narrowed"] is True
    assert set(out["groups"]) == {"events_news", "market_structure"}
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
    out = jr.route_tool_groups("600519 今天表现怎样？")
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


@pytest.mark.parametrize("text,page,reason", [
    ("今天大盘和涨停池什么情况", None, "explicit_request"),
    ("我的持仓今天怎么样", None, "private_context"),
    ("请分析今天", {"path": "/positions"}, "private_context"),
])
def test_deterministic_or_private_requests_do_not_call_jev(monkeypatch, text, page, reason):
    monkeypatch.setattr(jr, "evaluate", lambda *_a, **_k: pytest.fail("must skip semantic call"))
    out = jr.route_tool_groups(text, page, {"quotes", "limit_up"})
    assert out["reason"] == reason
    assert set(out["tools"]) == {"quotes", "limit_up"}


def test_no_competing_groups_never_calls_jev(monkeypatch):
    monkeypatch.setattr(jr, "evaluate", lambda *_a, **_k: pytest.fail("one group isn't competition"))
    assert jr.route_tool_groups("看看今天", allowed_tools={"quotes", "kline"})["reason"] == "no_competition"


def test_only_available_candidates_and_public_page_fields_reach_model(monkeypatch):
    captured = []
    def evaluate(state, questions, **kw):
        captured.append((state, questions))
        return _result({"market_realtime": .9, "events_news": .8, "account_positions": .99})
    monkeypatch.setattr(jr, "evaluate", evaluate)
    out = jr.route_tool_groups("看看今天", {"path": "/market?note=PRIVATE_NOTE", "title": "PRIVATE_TITLE",
                                            "symbol": "600519", "context": "PRIVATE_CONTEXT"},
                               {"quotes", "news", "bogus"})
    assert out["tools"] == ["news", "quotes"]
    assert set(captured[0][1]) == {"market_realtime", "events_news"}
    assert captured[0][0]["page"] == {"path": "/market", "symbol": "600519"}
    assert "PRIVATE" not in str(captured)


def test_dependency_readiness_is_not_upstream_health():
    from app.assistant.tools import ToolContext
    ctx = ToolContext(provider=object())
    names = jr.available_tools(ctx)
    assert {"quotes", "limit_up", "kb"} <= names
    assert not {"paper", "positions", "news", "theme_members", "market_overview"} & names
    ctx.session_factory = object()
    ctx.paper_engine = object()
    ctx.event_store = object()
    ctx.theme_catalog = object()
    ctx.hub = object()
    assert jr.available_tools(ctx) == set(TOOL_SPECS)


def test_receipt_is_durable_metadata_and_not_human_gold(tmp_path):
    import json
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.models.agent import AgentAudit
    engine = create_engine("sqlite:///" + str(tmp_path / "audit.db"))
    AgentAudit.__table__.create(engine)
    sf = sessionmaker(engine)
    trace = jr.RouteTrace("shadow", {"quotes", "limit_up"}, "今天大盘和涨停池", sf)
    trace.route = {"ok": True, "narrowed": True, "tools": ["quotes"], "baseline": ["quotes", "limit_up"],
                   "reason": "jev_groups", "model": "test", "usage": {"input_tokens": 10}}
    trace.calls = [{"tool": "quotes", "state": "returned"}, {"tool": "limit_up", "state": "unavailable"},
                   {"tool": None, "state": "unregistered"}]
    trace.terminal = "completed"
    trace.save()
    with sf() as db:
        row = db.scalar(select(AgentAudit))
        before, after = json.loads(row.before), json.loads(row.after)
        assert row.action == "assistant.route"
        assert after["adopted"] is False and after["human_coverage"] is None
        assert after["executed"] == ["quotes"]
        assert after["required_missing"] == ["limit_up", "market_overview"]
        assert after["required_unavailable"] == ["market_overview"]
        assert "今天大盘" not in row.before + row.after
        assert before["input_sha256"] == trace.input_sha256
    engine.dispose()


def test_shadow_busy_has_no_queue_and_cancellation_drains_real_worker(monkeypatch):
    import asyncio
    import threading
    started, release, ended = threading.Event(), threading.Event(), threading.Event()
    def route(*a):
        started.set()
        assert release.wait(3)
        ended.set()
        return {"ok": False, "tools": ["quotes"], "reason": "test"}
    monkeypatch.setattr(jr, "route_tool_groups", route)
    saved = []
    monkeypatch.setattr(jr.RouteTrace, "save", lambda self: saved.append(self.route))
    async def run():
        first = jr.RouteTrace("shadow", {"quotes"}, "test", None)
        task = first.start("test", None)
        assert await asyncio.to_thread(started.wait, 2)
        second = jr.RouteTrace("shadow", {"quotes"}, "test", None)
        assert second.start("test", None) is None
        assert second.route["reason"] == "skipped_busy"
        cleanup = asyncio.create_task(first.finish(task))
        await asyncio.sleep(0)
        cleanup.cancel()
        await asyncio.sleep(0)
        assert not cleanup.done() and not ended.is_set()
        assert second.start("test", None) is None
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await cleanup
        assert ended.is_set() and saved[0]["reason"] == "test"
        assert not jr._ROUTE_SLOT.locked()
    try:
        asyncio.run(run())
    finally:
        release.set()


@pytest.mark.parametrize("bad,reason", [
    (None, "invalid_response"), ({"ok": True, "answers": []}, "invalid_answers"),
    ({"ok": True, "answers": {"market_realtime": {"type": "choice", "noul": .9}}}, "invalid_noul"),
])
def test_malformed_response_preserves_original_allowed_set(monkeypatch, bad, reason):
    monkeypatch.setattr(jr, "evaluate", lambda *a, **k: bad)
    out = jr.route_tool_groups("看看现在", allowed_tools={"quotes", "news"})
    assert out["tools"] == ["news", "quotes"] and out["reason"] == reason


def test_telemetry_and_audit_failure_cannot_break_finished_chat(monkeypatch):
    monkeypatch.setattr(jr, "observe_route", lambda *a: (_ for _ in ()).throw(RuntimeError("sink failed")))
    def no_db():
        raise RuntimeError("database down")
    trace = jr.RouteTrace("shadow", {"quotes"}, "public", no_db)
    trace.route = {"ok": True, "narrowed": True, "tools": ["quotes"]}
    trace.save()
