"""Bounded author-team counterexamples, not independent review or human gold."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.events import company_impact as ci
from app.events import company_state as state
from app.events.company_reasoning import compile_analysis
from app.services import company_evidence as worker


def packet(title, summary, quote):
    return {"schema": ci.SCHEMA, "id": "600000:10:20:30", "symbol": "600000",
            "company_name": "合成精密股份有限公司", "short_name": "合成精密",
            "captured_at": "2026-10-20T08:00:00+08:00",
            "event": {"event_id": 10, "version_id": 20, "observation_id": 30,
                      "title": title, "summary": summary, "available_at": "2026-10-20T07:30:00+08:00"},
            "documents": [{"id": "h1", "symbol": "600000", "company_name": "合成精密股份有限公司",
                           "title": "合成半年度报告", "period": "2026H1",
                           "url": "https://static.cninfo.com.cn/finalpage/2026-08-15/1234567890.PDF",
                           "published_at": "2026-08-15T23:59:59+08:00",
                           "received_at": "2026-10-20T07:50:00+08:00", "sha256": "a" * 64,
                           "identity_quote": "合成精密股份有限公司证券代码600000",
                           "excerpts": [{"kind": "product", "page": 18, "quote": quote}]}]}


def selected(channel="demand"):
    return {"id": "600000:10:20:30", "status": "linked", "links": [
        {"fact_id": "h1:0", "phrase": "精密连接器", "anchor": "精密连接器", "channel": channel}],
        "missing_codes": ["customer", "order", "exposure"]}


def test_explicit_product_exclusion_is_not_a_manufacturing_role():
    source = packet("精密连接器采购需求增长", "行业精密连接器需求增加。",
                    "本公司主要产品不包括精密连接器。")
    analysis, rejected = compile_analysis(source, selected())
    assert analysis["status"] == "insufficient" and analysis["chain"] == []
    assert rejected


def test_contract_for_another_object_cannot_supply_the_selected_product_channel():
    source = packet("合成精密披露精密连接器业务", "精密连接器订单暂无变化；合成精密签署办公楼租赁合同。",
                    "本公司主要产品为精密连接器。")
    analysis, rejected = compile_analysis(source, selected("order_execution"))
    assert analysis["status"] == "insufficient" and analysis["chain"] == []
    assert rejected


def test_paid_success_recovery_keeps_original_capture_instead_of_retimestamping_input(tmp_path, monkeypatch):
    current = datetime.fromisoformat("2026-10-20T10:00:00+08:00")
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current.astimezone(tz or timezone.utc)
    monkeypatch.setattr(ci, "datetime", Clock)
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    monkeypatch.setattr(worker, "_now", lambda: current)
    monkeypatch.setattr(worker, "get_session_factory", lambda: object())
    monkeypatch.setattr(worker, "settings", SimpleNamespace(
        llm_provider="fixture", review_llm_model="selector", company_evidence_batch_size=4))
    source = packet("精密连接器采购需求增长", "行业精密连接器需求增加。", "本公司主要产品为精密连接器。")
    profile = {"symbol": source["symbol"], "company_name": source["company_name"], "short_name": source["short_name"],
               "checked_at": "2026-10-20T07:55:00+08:00", "status": "ready", "missing": [],
               "documents": deepcopy(source["documents"]), "cache_until": "2026-10-21T07:55:00+08:00"}
    state.put_profile(profile, root=tmp_path)
    item = {"event_id": 10, "state": "active", "is_active": True,
            "interpretation_ref": {"event_id": 10, "version_id": 20, "observation_id": 30, "state": "active"},
            "source_context": {"observation_id": 30, "title": source["event"]["title"], "summary": source["event"]["summary"]},
            "timing": {"available_at": source["event"]["available_at"]}}
    original = worker._packet("600000", profile, item)
    original["captured_at"] = source["captured_at"]
    model = {"provider": "fixture", "model": "selector"}
    key = worker.generation_key(original, model)
    state.write_state("600000", "generating", now=datetime.fromisoformat("2026-10-20T09:00:00+08:00"),
                      root=tmp_path, task_id="old-owner", event_ref=worker._event_key(item), generation_key=key,
                      attempt_state="succeeded", selected=selected(), generated_at="2026-10-20T09:00:00+08:00",
                      usage_id="b" * 32, packet=original)
    monkeypatch.setattr(worker, "_event", lambda *_a, **_kw: item)
    monkeypatch.setattr(worker, "collect_company", lambda *_a, **_kw: deepcopy(profile))
    monkeypatch.setattr(worker.agent_budget, "reserve", lambda *_a, **_kw: pytest.fail("paid input was called again"))
    monkeypatch.setattr(worker, "bounded_chat_completion", lambda *_a, **_kw: pytest.fail("model called during recovery"))
    async def immediate(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(worker, "_drain_stage", immediate)
    rec = SimpleNamespace(task_id="recovery-owner", checkpoint=lambda: None, add=lambda **_: None)
    result = asyncio.run(worker.run_company_evidence(rec, {"symbols": ["600000"]}, None))
    assert result["model_calls"] == 0 and result["symbols"][0]["state"] == "ready"
    saved = ci.read_saved(item, "600000", now=current, root=tmp_path)
    assert saved is not None and saved["facts_captured_at"] == original["captured_at"]
