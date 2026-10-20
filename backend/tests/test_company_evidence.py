"""Finite worker failures, durable billing and publication; no live provider/model."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.scheduler import SCHEDULER_SWITCH_ATTRS
from app.events import company_impact as ci
from app.events import company_state as store
from app.events.company_reasoning import compile_analysis
from app.models.agent import AgentResourceUsage
from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionRun as Run, OpportunityDecisionSnapshot as Snapshot
from app.models.watchlist import Base
from app.services import agent_budget
from app.services import company_evidence as worker

NOW = datetime.fromisoformat("2026-10-20T10:00:00+08:00")
SYMBOL = "003021"


class Recorder:
    task_id = "a" * 32

    def __init__(self):
        self.steps = []
        self.canceled = False

    def checkpoint(self):
        if self.canceled:
            raise asyncio.CancelledError

    async def checkpoint_async(self):
        self.checkpoint()

    def add(self, **step):
        self.steps.append(step)

    async def add_async(self, **step):
        self.add(**step)


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    clock = [NOW]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(ci, "ROOT", tmp_path / "company")
    monkeypatch.setattr(ci, "datetime", Clock)
    monkeypatch.setattr(worker, "_now", lambda: clock[0])
    monkeypatch.setattr(worker, "get_session_factory", lambda: sf)
    monkeypatch.setattr(agent_budget, "beijing_now", lambda: clock[0])
    monkeypatch.setattr(agent_budget, "beijing_now_naive", lambda: clock[0].replace(tzinfo=None))
    for key, value in {"llm_provider": "openai", "review_llm_model": "fixture-model",
                       "review_llm_base_url": "https://fixture.invalid", "review_llm_api_key": "synthetic",
                       "agent_daily_llm_budget": 8, "company_evidence_enabled": False,
                       "company_evidence_batch_size": 4}.items():
        monkeypatch.setattr(worker.settings, key, value)
    profile = {"symbol": SYMBOL, "company_name": "夹具驱动公司", "short_name": "夹具驱动",
               "status": "ready", "checked_at": (NOW - timedelta(minutes=1)).isoformat(), "missing": [],
               "cache_until": (NOW + timedelta(hours=6)).isoformat(), "documents": [
                   {"id": "report", "symbol": SYMBOL, "company_name": "夹具驱动公司",
                    "title": "2026年半年度报告", "period": "2026H1",
                    "url": "https://static.cninfo.com.cn/finalpage/2026-08-15/1234567890.PDF",
                    "published_at": "2026-08-15T23:59:59+08:00", "received_at": "2026-10-20T09:00:00+08:00",
                    "sha256": "b" * 64, "identity_quote": "夹具驱动公司证券代码003021",
                    "excerpts": [{"kind": "product", "page": 2, "quote": "公司主营为光模块的研发、生产和销售。"}]}]}
    item = {"event_id": 10, "state": "active", "is_active": True,
            "interpretation_ref": {"event_id": 10, "version_id": 20, "observation_id": 30, "state": "active"},
            "source_context": {"observation_id": 30, "title": "夹具驱动公司签订光模块合同",
                               "summary": "合同尚需后续交付验收。", "url": "https://fixture.invalid/news"},
            "timing": {"available_at": "2026-10-20T09:30:00+08:00", "received_at": "2026-10-20T09:30:00+08:00",
                       "source_published_at": "2026-10-20T09:20:00+08:00"},
            "source": {"content_hash": "c" * 64}}
    calls, collected, items = [], [], [item]
    real_events = worker._events

    def collect(symbol, **kwargs):
        collected.append((symbol, kwargs))
        value = deepcopy(profile)
        value["symbol"] = symbol
        if value["documents"]:
            value["documents"][0].update(symbol=symbol, identity_quote="夹具驱动公司证券代码" + symbol)
        return value

    def model(*args, **kwargs):
        calls.append((args, kwargs))
        public = json.loads(args[3][1]["content"])
        kwargs["usage_callback"]({"input_tokens": 10, "output_tokens": 20})
        return json.dumps([{"id": p["id"], "event_id": p["events"][0]["event_id"], "status": "linked", "links": [
            {"fact_id": "report:0", "phrase": "光模块", "anchor": "光模块", "channel": "order_execution", "bridge_fact_id": None}],
            "assessment": {"fact_id": "report:0", "company_phrase": "光模块", "event_phrase": "光模块", "reason_code": "supported"},
            "missing_codes": ["order", "exposure"]} for p in public], ensure_ascii=False)

    monkeypatch.setattr(worker, "collect_company", collect)
    monkeypatch.setattr(worker, "_event", lambda *a, **kw: next((deepcopy(v) for v in items
                        if kw.get("event_id") in (None, v["event_id"])), None))
    monkeypatch.setattr(worker, "_events", lambda *a, **kw: deepcopy(items))
    monkeypatch.setattr(worker, "bounded_chat_completion", model)
    yield SimpleNamespace(sf=sf, clock=clock, item=item, profile=profile, calls=calls,
                          collected=collected, root=ci.ROOT, model=model, items=items, real_events=real_events,
                          app=SimpleNamespace(state=SimpleNamespace()))
    engine.dispose()


def run(env, params=None, rec=None):
    return asyncio.run(worker.run_company_evidence(rec or Recorder(), params or {"symbols": [SYMBOL]}, env.app))


def event_item(env, event_id):
    item = deepcopy(env.item)
    item["event_id"] = event_id
    item["interpretation_ref"].update(event_id=event_id, version_id=event_id + 10, observation_id=event_id + 20)
    item["source_context"]["observation_id"] = event_id + 20
    return item


def receipts(env):
    return [p for p in env.root.rglob("*.json") if len(p.stem) == 64]


def usage_rows(env):
    with env.sf() as db:
        return list(db.execute(select(AgentResourceUsage)).scalars())


def publish_author(env):
    packet = worker._packet(SYMBOL, env.profile, env.item)
    selected = {"id": packet["id"], "status": "linked", "links": [
        {"fact_id": "report:0", "phrase": "光模块", "anchor": "光模块", "channel": "order_execution"}],
        "missing_codes": ["order", "exposure"]}
    analysis, _ = compile_analysis(packet, selected)
    review = {"input_sha256": ci.digest(packet), "output_sha256": ci.digest(analysis),
              "protocol_sha256": ci.digest(ci.messages(packet)), "prompt_sha256": "d" * 64,
              "raw_output_sha256": "e" * 64, "comparison_sha256": "f" * 64,
              "role": "author", "verdict": "PASS_EXPLANATION_ONLY", "independent_gold": False,
              "net_improvement": True, "rationale": "合成夹具，只核已审核解释保留，不是人工gold。",
              "reviewed_at": env.clock[0].isoformat()}
    return ci.publish(packet, analysis, review, model={"provider": "fixture", "model": "author-fixture"},
                      generated_at=env.clock[0].isoformat())


def test_valid_author_explanation_keeps_current_pointer_after_collection_without_model(env):
    key = publish_author(env)
    pointer = env.root / SYMBOL / "10-20-30.current"
    original = pointer.read_bytes()
    result = run(env)
    assert result["model_calls"] == 0 and result["symbols"][0]["verification"] == "author_source_reviewed_not_gold"
    assert len(env.collected) == 1 and store.read_profile(SYMBOL) == env.profile
    assert not env.calls and not usage_rows(env)
    assert pointer.read_bytes() == original == key.encode("ascii")
    assert ci.read_saved(env.item, SYMBOL, now=NOW)["verification"] == "author_source_reviewed_not_gold"
    status = store.read_status(SYMBOL, now=NOW, enabled=True)
    assert status["state"] == "ready" and "自动引用约束" not in status["reason"]


@pytest.mark.parametrize("change", ["expired", "source_drift"])
def test_invalidated_author_does_not_block_automatic_evidence(env, change):
    key = publish_author(env)
    original = (env.root / SYMBOL / f"{key}.json").read_bytes()
    if change == "expired":
        env.clock[0] += timedelta(days=8)
    else:
        env.item["source_context"]["summary"] += "本条来源后来修订了交付条件。"
    result = run(env)
    assert result["model_calls"] == 1 and len(env.calls) == 1
    assert (env.root / SYMBOL / f"{key}.json").read_bytes() == original
    assert ci.read_saved(env.item, SYMBOL, now=env.clock[0])["verification"] == "automated_evidence_bound_not_gold"


def test_author_arriving_during_model_call_is_preserved_by_atomic_publisher_guard(env, monkeypatch):
    author_keys = []
    def author_arrives(*args, **kwargs):
        raw = env.model(*args, **kwargs)
        author_keys.append(publish_author(env))
        return raw
    monkeypatch.setattr(worker, "bounded_chat_completion", author_arrives)
    with pytest.raises(ValueError, match="author-reviewed"):
        run(env)
    assert len(env.calls) == 1 and usage_rows(env)[0].usage_known == 1
    assert (env.root / SYMBOL / "10-20-30.current").read_text() == author_keys[0]
    assert ci.read_saved(env.item, SYMBOL, now=NOW)["verification"] == "author_source_reviewed_not_gold"
    assert len(receipts(env)) == 1


def test_real_store_publication_uses_one_shared_slot_and_no_author_claim(env):
    result = run(env)
    assert result["model_calls"] == 1 and result["symbols"][0]["state"] == "ready"
    saved = ci.read_saved(env.item, SYMBOL, now=NOW)
    assert saved["verification"] == "automated_evidence_bound_not_gold"
    assert saved["eligibility_changed"] is False and saved["business_effect_verified"] is False
    assert saved["analysis"]["chain"][0]["evidence_ids"] == ["event:title", "report:0"]
    row, = usage_rows(env)
    assert (row.scope, row.purpose, row.state, row.input_tokens, row.output_tokens, row.attempts) == (
        "autonomy_llm", "company_evidence.select", "succeeded", 10, 20, 1)
    assert row.usage_known == 1 and len(env.calls) == 1
    assert env.calls[0][1]["timeout"] == 180 and env.calls[0][1]["provider"] == "openai"


def test_registered_handler_persists_result_and_usage_in_existing_task_lifecycle(env, monkeypatch):
    from app.services import agent_tasks

    monkeypatch.setattr(agent_tasks, "get_session_factory", lambda: env.sf)
    monkeypatch.setattr(agent_tasks, "_APP", env.app)
    monkeypatch.setattr(agent_tasks, "_RUNNING", {})
    monkeypatch.setattr(agent_tasks, "_HANDLES", {})

    async def scenario():
        created = agent_tasks.create_task("company_evidence", {"symbols": [SYMBOL]})
        assert created["status"] == "queued"
        await agent_tasks._HANDLES[created["id"]]
        return agent_tasks.get_task(created["id"])

    task = asyncio.run(scenario())
    assert task["status"] == "succeeded", task
    assert task["result_ref"]["symbols"][0]["state"] == "ready"
    assert task["result_ref"]["model_calls"] == 1
    assert [step["name"] for step in task["steps"]] == ["公司公开依据", "公司判读结果"]
    row, = usage_rows(env)
    assert row.task_id == task["id"] == store.load_state(SYMBOL)["task_id"]
    assert not agent_tasks._RUNNING and not agent_tasks._HANDLES


def test_repeating_same_evidence_adds_neither_model_call_nor_receipt_or_validity(env):
    run(env)
    before = {p: p.read_bytes() for p in receipts(env)}
    saved = ci.read_saved(env.item, SYMBOL, now=NOW)
    env.clock[0] += timedelta(minutes=10)
    env.profile["checked_at"] = env.clock[0].isoformat()
    result = run(env)
    assert {p: p.read_bytes() for p in receipts(env)} == before
    assert ci.read_saved(env.item, SYMBOL, now=env.clock[0])["valid_until"] == saved["valid_until"]
    assert result["model_calls"] == 0 and result["symbols"][0]["cached"] is True
    assert len(env.calls) == len(usage_rows(env)) == 1


def test_interrupted_publish_recovers_original_packet_and_generation_without_recall(env, monkeypatch):
    real_publish = ci.publish_automatic
    monkeypatch.setattr(ci, "publish_automatic", lambda *a, **k: (_ for _ in ()).throw(OSError("fixture disk busy")))
    with pytest.raises(OSError):
        run(env)
    prior = store.load_state(SYMBOL)
    assert prior["attempt_state"] == "succeeded" and not receipts(env)
    env.clock[0] += timedelta(minutes=20)
    monkeypatch.setattr(ci, "publish_automatic", real_publish)
    result = run(env)
    assert result["model_calls"] == 0 and len(env.calls) == 1
    saved = ci.read_saved(env.item, SYMBOL, now=env.clock[0])
    assert saved["facts_captured_at"] == prior["packet"]["captured_at"]
    assert saved["generated_at"] == prior["generated_at"]


def test_old_selection_never_gets_new_generation_time_to_bypass_expiry(env, monkeypatch):
    monkeypatch.setattr(ci, "publish_automatic", lambda *a, **k: (_ for _ in ()).throw(OSError("fixture disk busy")))
    with pytest.raises(OSError):
        run(env)
    env.clock[0] += timedelta(days=8)
    result = run(env)
    assert result["symbols"][0]["state"] == "interrupted" and result["model_calls"] == 0
    assert len(env.calls) == 1 and not receipts(env)


def test_stable_key_excludes_collection_clock_but_changes_for_real_evidence(env):
    packet = worker._packet(SYMBOL, env.profile, env.item)
    model = {"provider": "fixture", "model": "fixture"}
    key = worker.generation_key(packet, model)
    changed = deepcopy(packet)
    changed["captured_at"] = "2026-10-21T10:00:00+08:00"
    assert worker.generation_key(changed, model) == key
    for field in ("event", "source", "model"):
        value = deepcopy(packet)
        if field == "event": value["event"]["version_id"] += 1
        if field == "source": value["documents"][0]["sha256"] = "d" * 64
        assert worker.generation_key(value, {**model, "model": "other"} if field == "model" else model) != key


@pytest.mark.parametrize("field,value", [
    ("title", "2026年半年度报告（修订）"),
    ("published_at", "2026-08-15T12:00:00+08:00"),
    ("received_at", "2026-10-20T09:01:00+08:00"),
    ("extraction_method", "changed-extractor"),
])
def test_generation_key_binds_full_document_identity_used_by_reader_and_publisher(env, field, value):
    packet = worker._packet(SYMBOL, env.profile, env.item)
    changed = deepcopy(packet)
    changed["documents"][0][field] = value
    model = {"provider": "fixture", "model": "fixture"}
    assert worker.generation_key(changed, model) != worker.generation_key(packet, model)


def test_legacy_projection_key_and_v3_receipt_migrate_to_valid_v4_without_republishing_old_input(env, monkeypatch):
    from app.events import company_sources

    current_method = company_sources.EXTRACTOR
    old_method = "bounded-sentences-v3"
    env.profile["extractor"] = old_method
    env.profile["documents"][0]["extraction_method"] = old_method

    def historical_projection(packet, model):
        # Exact pre-migration algorithm, independent of the current helper.
        documents = [{key: d[key] for key in ("id", "symbol", "company_name", "url", "sha256", "period", "excerpts")}
                     for d in packet["documents"]]
        return ci.digest({"symbol": packet["symbol"], "company_name": packet["company_name"],
                          "short_name": packet.get("short_name", ""), "event": packet["event"],
                          "documents": documents, "protocol": worker.PROTOCOL, "model": model})

    with monkeypatch.context() as old_runtime:
        old_runtime.setattr(company_sources, "EXTRACTOR", old_method)
        old_runtime.setattr(worker, "generation_key", historical_projection)
        assert run(env)["model_calls"] == 1
    prior = store.load_state(SYMBOL)
    model = {"provider": worker.settings.llm_provider, "model": worker.settings.review_llm_model}
    assert prior["generation_key"] == historical_projection(prior["packet"], model)
    assert ci.read_saved(env.item, SYMBOL, now=NOW) is not None
    originals = {p: p.read_bytes() for p in receipts(env)}
    old_excerpts = deepcopy(env.profile["documents"][0]["excerpts"])
    env.profile["extractor"] = current_method
    env.profile["documents"][0]["extraction_method"] = current_method
    assert historical_projection(worker._packet(SYMBOL, env.profile, env.item), model) == prior["generation_key"]

    result = run(env)
    current = store.load_state(SYMBOL)
    assert result["model_calls"] == 1 and result["symbols"][0]["state"] == "ready"
    assert len(env.calls) == len(usage_rows(env)) == 2
    assert current["generation_key"] != prior["generation_key"]
    assert current["packet"]["documents"][0]["extraction_method"] == current_method
    assert current["packet"]["documents"][0]["excerpts"] == old_excerpts
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert len(receipts(env)) == 2
    saved = ci.read_saved(env.item, SYMBOL, now=NOW)
    assert saved is not None and saved["verification"] == "automated_evidence_bound_not_gold"
    assert run(env)["model_calls"] == 0 and len(env.calls) == 2


@pytest.mark.parametrize("raw", ["not JSON", "[]", '[{"id":"foreign"}]', '[{"id":"003021:10:20:30"},{"id":"003021:10:20:30"}]'])
def test_bad_batch_keeps_facts_and_known_cost_without_publishing_or_auto_retry(env, monkeypatch, raw):
    def invalid(*args, **kwargs):
        env.calls.append(1)
        kwargs["usage_callback"]({"input_tokens": 11, "output_tokens": 7})
        return raw
    monkeypatch.setattr(worker, "bounded_chat_completion", invalid)
    with pytest.raises((ValueError, TypeError)):
        run(env)
    assert store.load_state(SYMBOL)["state"] == "invalid_response"
    assert store.read_profile(SYMBOL)["documents"] == env.profile["documents"]
    assert not receipts(env)
    row, = usage_rows(env)
    assert row.usage_known == 1 and row.output_tokens == 7
    assert run(env)["model_calls"] == 0 and len(env.calls) == 1


def test_budget_block_keeps_collected_facts_and_never_starts_model(env, monkeypatch):
    monkeypatch.setattr(worker.settings, "agent_daily_llm_budget", 0)
    with pytest.raises(agent_budget.BudgetError):
        run(env)
    state = store.load_state(SYMBOL)
    assert state["state"] == "budget_blocked" and state["attempt_state"] == "not_started"
    assert store.read_profile(SYMBOL) and not env.calls and not usage_rows(env) and not receipts(env)


def test_cancel_after_reservation_releases_only_unstarted_slot(env, monkeypatch):
    rec = Recorder()
    reserve = agent_budget.reserve

    def cancel_after_reserve(*args, **kwargs):
        lease = reserve(*args, **kwargs)
        rec.canceled = True
        return lease

    monkeypatch.setattr(agent_budget, "reserve", cancel_after_reserve)
    with pytest.raises(asyncio.CancelledError):
        run(env, rec=rec)
    assert not usage_rows(env) and not env.calls
    assert store.load_state(SYMBOL)["attempt_state"] == "not_started"
    assert store.load_state(SYMBOL)["state"] == "canceled"


def test_unavailable_http_configuration_is_not_billed_as_an_unknown_call(env, monkeypatch):
    monkeypatch.setattr(worker.settings, "review_llm_api_key", "")
    with pytest.raises(worker.LLMError):
        run(env)
    assert not usage_rows(env) and not env.calls
    assert store.load_state(SYMBOL)["attempt_state"] == "not_started"


def test_started_unknown_usage_blocks_new_call_and_same_input_is_not_resent(env, monkeypatch):
    def failed(*args, **kwargs):
        env.calls.append(1)
        raise worker.LLMError("safe fixture failure")
    monkeypatch.setattr(worker, "bounded_chat_completion", failed)
    with pytest.raises(worker.LLMError):
        run(env)
    row, = usage_rows(env)
    assert row.usage_known == 0 and row.input_tokens is None and row.state == "failed"
    assert run(env)["model_calls"] == 0 and len(env.calls) == 1
    env.item["interpretation_ref"]["version_id"] += 1
    with pytest.raises(agent_budget.BudgetError, match="未知"):
        run(env)
    assert len(env.calls) == 1 and not receipts(env)


@pytest.mark.parametrize("change", ["event", "profile"])
def test_live_drift_after_generation_keeps_selection_unpublished(env, monkeypatch, change):
    def drifting(*args, **kwargs):
        raw = env.model(*args, **kwargs)
        if change == "event":
            env.item["interpretation_ref"]["version_id"] += 1
        else:
            changed = deepcopy(env.profile)
            changed["documents"][0]["excerpts"][0]["quote"] = "公司主营为连接器的研发、生产和销售。"
            store.put_profile(changed)
        return raw
    monkeypatch.setattr(worker, "bounded_chat_completion", drifting)
    result = run(env)
    assert result["symbols"][0]["state"] == "interrupted"
    assert store.load_state(SYMBOL)["attempt_state"] == "succeeded" and not receipts(env)


def test_cancel_during_model_drains_thread_records_usage_and_cannot_publish(env, monkeypatch):
    started, release, drained = threading.Event(), threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        started.set()
        assert release.wait(3), "test must release its bounded fake model"
        raw = env.model(*args, **kwargs)
        drained.set()
        return raw
    monkeypatch.setattr(worker, "bounded_chat_completion", slow)

    async def scenario():
        task = asyncio.create_task(worker.run_company_evidence(Recorder(), {"symbols": [SYMBOL]}, env.app))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not drained.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert drained.is_set()
    asyncio.run(scenario())
    assert store.load_state(SYMBOL)["state"] == "canceled" and not receipts(env)
    row, = usage_rows(env)
    assert row.state == "canceled" and row.usage_known == 1
    assert run(env)["model_calls"] == 0 and len(env.calls) == 1


def test_cooperative_cancel_after_collection_keeps_only_facts_and_no_bill(env, monkeypatch):
    rec = Recorder()
    original = worker.collect_company
    def cancel_after_collect(*args, **kwargs):
        value = original(*args, **kwargs)
        rec.canceled = True
        return value
    monkeypatch.setattr(worker, "collect_company", cancel_after_collect)
    with pytest.raises(asyncio.CancelledError):
        run(env, rec=rec)
    assert store.load_state(SYMBOL)["state"] == "canceled"
    assert not env.calls and not usage_rows(env) and not receipts(env)


def test_task_wall_time_limit_after_model_is_interruption_not_false_invalid_reply(env):
    from app.services.agent_tasks import TaskWallTimeout

    class DeadlineRecorder(Recorder):
        def checkpoint(self):
            if env.calls:
                raise TaskWallTimeout

    with pytest.raises(TaskWallTimeout):
        run(env, rec=DeadlineRecorder())
    state = store.load_state(SYMBOL)
    assert state["state"] == "interrupted" and not receipts(env)
    assert state["missing"] == ["任务时间已用尽；当前步骤已结束，未发布回答。"]
    row, = usage_rows(env)
    assert row.usage_known == 1 and row.state == "succeeded"


@pytest.mark.parametrize("status", ["failed", "missing", "no_event"])
def test_missing_source_or_event_is_explicit_and_does_not_call_model(env, monkeypatch, status):
    if status == "no_event":
        monkeypatch.setattr(worker, "_event", lambda *a, **k: None)
        monkeypatch.setattr(worker, "_events", lambda *a, **k: [])
    else:
        env.profile.update(status=status, documents=[], missing=["来源本轮不可用或无有效披露"])
    result = run(env)
    assert result["symbols"][0]["state"] == ("source_failed" if status == "failed" else "insufficient")
    assert bool(store.read_profile(SYMBOL)) == (status == "no_event")
    assert not env.calls and not usage_rows(env)
    monkeypatch.setattr(worker.settings, "company_evidence_enabled", True)
    monkeypatch.setattr(worker, "candidate_symbols", lambda *a, **k: [SYMBOL])
    repeated = run(env, {"automatic": True})
    assert repeated["symbols"] == [] and len(env.collected) == 1


def test_source_failure_never_replaces_previously_collected_company_facts(env, monkeypatch):
    store.put_profile(env.profile)
    def source_failed(*args, **kwargs):
        raise OSError("private source detail must not enter public state")
    monkeypatch.setattr(worker, "collect_company", source_failed)
    run(env)
    assert store.read_profile(SYMBOL) == env.profile
    assert "private source detail" not in json.dumps(store.load_state(SYMBOL))
    assert not env.calls


def test_concurrent_process_lease_prevents_collection_and_duplicate_billing(env):
    with worker.worker_lease(env.root):
        with pytest.raises(RuntimeError, match="另一执行者"):
            run(env)
    assert not env.collected and not env.calls and not usage_rows(env)


@pytest.mark.parametrize("params", [{"automatic": 1}, {"automatic": "false"}, {"symbols": []},
                                     {"symbols": ["003021"] * 9}, {"symbols": ["../003021"]}, {"unknown": True}])
def test_invalid_parameters_have_no_source_or_model_side_effect(env, params):
    with pytest.raises(ValueError):
        run(env, params=params)
    assert not env.collected and not env.calls and not env.root.exists()


def _add_run(db, run, at, *, symbols=(), state="ready", day="2026-10-20"):
    db.add(Run(run_id=run, trade_date=day, as_of=at, scenario="intraday_opportunity",
               strategy_version="fixture", feature_version="fixture", evidence_digest="e" * 64, data_state=state))
    for i, symbol in enumerate(symbols, 1):
        db.add(Snapshot(snapshot_id=f"{run}-{i}", run_id=run, trade_date=day, as_of=at,
                        scenario="intraday_opportunity", stage="rank", symbol=symbol, rank=i,
                        decision="ranked", data_state="ready", strategy_version="fixture", feature_version="fixture"))


def test_candidate_window_uses_latest_real_run_and_no_future_or_foreign_rows(env):
    at = NOW.replace(tzinfo=None)
    with env.sf() as db:
        _add_run(db, "old", at - timedelta(minutes=5), symbols=["000001"])
        _add_run(db, "current", at - timedelta(minutes=1), symbols=["000002", "000003"])
        _add_run(db, "future", at + timedelta(minutes=1), symbols=["000004"])
        _add_run(db, "yesterday", at - timedelta(days=1), symbols=["000005"], day="2026-10-19")
        db.add(Snapshot(snapshot_id="foreign-date", run_id="current", trade_date="2026-10-19", as_of=at,
                        scenario="intraday_opportunity", stage="rank", symbol="000006", rank=0,
                        decision="ranked", data_state="ready", strategy_version="fixture", feature_version="fixture"))
        db.add(DailyPickSet(date="2026-10-20", created_at=NOW.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2),
                            items=json.dumps([{"symbol": "000003"}, {"symbol": "000007"}])))
        db.commit()
    assert worker.candidate_symbols(env.sf, now=NOW) == ["000002", "000003", "000007"]
    with env.sf() as db:
        _add_run(db, "empty", at)
        db.commit()
    assert worker.candidate_symbols(env.sf, now=NOW) == ["000003", "000007"]


def test_future_daily_pick_save_is_not_a_current_candidate(env):
    with env.sf() as db:
        db.add(DailyPickSet(date="2026-10-20", created_at=NOW.astimezone(timezone.utc).replace(tzinfo=None) + timedelta(seconds=1),
                            items=json.dumps([{"symbol": SYMBOL}])))
        db.commit()
    assert worker.candidate_symbols(env.sf, now=NOW) == []


def _add_low_participant(db, run, at, symbol, *, theme="机器人", change=None):
    common = dict(run_id=run, trade_date="2026-10-20", as_of=at, scenario="intraday_opportunity",
                  symbol=symbol, source_theme=theme, strategy_version="fixture", feature_version="fixture", data_state="ready")
    for stage, decision in [("candidate", "included"), ("hard_gate", "passed"), ("rank", "rejected")]:
        fields = {**common, "snapshot_id": f"{run}-{symbol}-{theme}-{stage}", "stage": stage, "decision": decision,
                  "rank": None, "evidence": json.dumps({"gate_decision": "passed", "linkage_level": "低"})}
        if change:
            change(stage, fields)
        db.add(Snapshot(**fields))


def test_real_low_participant_is_read_after_ranked_before_daily_with_no_new_qualification(env):
    at = NOW.replace(tzinfo=None) - timedelta(minutes=1)
    with env.sf() as db:
        _add_run(db, "current", at, symbols=["600001"])
        _add_low_participant(db, "current", at, "000002")
        _add_low_participant(db, "current", at, "000002", theme="精密制造")
        db.add(DailyPickSet(date="2026-10-20", created_at=NOW.astimezone(timezone.utc).replace(tzinfo=None),
                            items=json.dumps([{"symbol": "600001"}, {"symbol": "600003"}])))
        db.commit()
    assert worker.candidate_symbols(env.sf, now=NOW) == ["600001", "000002", "600003"]
    with env.sf() as db:
        rows = db.query(Snapshot).filter(Snapshot.symbol == "000002", Snapshot.stage == "rank").all()
        assert all(row.decision == "rejected" and row.rank is None for row in rows)


def test_universe_caps_distinct_intraday_at_32_then_daily_at_8_without_renaming_rank(env):
    at = NOW.replace(tzinfo=None) - timedelta(minutes=1)
    ranked = [f"{600000 + i:06d}" for i in range(1, 31)]
    daily = [f"{601000 + i:06d}" for i in range(1, 10)]
    with env.sf() as db:
        _add_run(db, "current", at, symbols=ranked)
        for i in range(1, 7):
            _add_low_participant(db, "current", at, f"{i:06d}")
        db.add(DailyPickSet(date="2026-10-20", created_at=NOW.astimezone(timezone.utc).replace(tzinfo=None),
                            items=json.dumps([{"symbol": s} for s in daily])))
        db.commit()
    assert worker.candidate_symbols(env.sf, now=NOW) == ranked + ["000001", "000002"] + daily[:8]


def test_first_unprocessed_ranked_consumer_precedes_unprocessed_low_participant(env, monkeypatch):
    at = NOW.replace(tzinfo=None) - timedelta(minutes=1)
    with env.sf() as db:
        _add_run(db, "current", at, symbols=["600001"])
        _add_low_participant(db, "current", at, "000002")
        db.commit()
    monkeypatch.setattr(worker.settings, "company_evidence_enabled", True)
    monkeypatch.setattr(worker.settings, "company_evidence_batch_size", 1)
    assert run(env, {"automatic": True})["symbols"][0]["symbol"] == "600001"


@pytest.mark.parametrize("change", ["audit_rejected", "hard_gate_rejected", "foreign_theme", "foreign_day",
                                     "foreign_time", "foreign_scenario", "low_quality", "unknown_linkage", "malformed", "no_rank"])
def test_low_consumer_requires_exact_current_participant_not_audit_or_unknown(env, change):
    at = NOW.replace(tzinfo=None) - timedelta(minutes=1)

    def corrupt(stage, fields):
        if change == "audit_rejected" and stage == "candidate": fields["decision"] = "rejected"
        if change == "hard_gate_rejected" and stage == "hard_gate": fields["decision"] = "rejected"
        if change == "foreign_theme" and stage == "hard_gate": fields["source_theme"] = "另一题材"
        if change == "foreign_day" and stage == "hard_gate": fields["trade_date"] = "2026-10-19"
        if change == "foreign_time" and stage == "hard_gate": fields["as_of"] += timedelta(seconds=1)
        if change == "foreign_scenario" and stage == "hard_gate": fields["scenario"] = "other"
        if change == "low_quality" and stage == "hard_gate": fields["data_state"] = "stale"
        if change == "unknown_linkage" and stage == "rank": fields["evidence"] = json.dumps({"gate_decision": "passed", "linkage_level": "unknown"})
        if change == "malformed" and stage == "rank": fields["evidence"] = "not json"
        if change == "no_rank" and stage == "rank": fields["stage"] = "notification"

    with env.sf() as db:
        _add_run(db, "current", at)
        _add_low_participant(db, "current", at, "000002", change=corrupt)
        db.commit()
    assert worker.candidate_symbols(env.sf, now=NOW) == []


@pytest.mark.parametrize("successor", ["empty", "degraded"])
def test_new_empty_or_degraded_run_does_not_revive_low_participant(env, successor):
    at = NOW.replace(tzinfo=None)
    with env.sf() as db:
        _add_run(db, "old", at - timedelta(minutes=1))
        _add_low_participant(db, "old", at - timedelta(minutes=1), "000002")
        _add_run(db, "new", at, state="ready" if successor == "empty" else "degraded")
        db.commit()
    assert worker.candidate_symbols(env.sf, now=NOW) == []


def test_changed_extractor_expires_cached_profile_once_but_not_unknown_model_attempt(env):
    profile = deepcopy(env.profile)
    profile["extractor"] = "old-extractor"
    store.put_profile(profile)
    store.write_state(SYMBOL, "ready", now=NOW, event_ref=worker._event_key(env.item),
                      source_status="ready", next_retry_at=(NOW + timedelta(hours=6)).isoformat())
    assert worker._due(SYMBOL, env.item) is True
    from app.events.company_sources import EXTRACTOR
    profile["extractor"] = EXTRACTOR
    store.put_profile(profile)
    assert worker._due(SYMBOL, env.item) is False
    profile["extractor"] = "old-extractor"
    store.put_profile(profile)
    store.write_state(SYMBOL, "generating", now=NOW, attempt_state="unknown")
    assert worker._due(SYMBOL, env.item) is False


@pytest.mark.parametrize("attempt", ["started", "unknown"])
def test_explicit_refresh_of_changed_facts_cannot_resend_same_event_unknown_attempt(env, attempt):
    store.write_state(SYMBOL, "interrupted", now=NOW, event_ref=worker._event_key(env.item),
                      attempt_state=attempt, generation_key="old-key")
    result = run(env)
    assert result["model_calls"] == 0 and result["symbols"][0]["state"] == "interrupted"
    assert store.read_profile(SYMBOL) and not env.calls and not usage_rows(env)


def test_extractor_refresh_with_same_public_input_never_repeats_model_or_receipt(env, monkeypatch):
    from app.events.company_sources import EXTRACTOR

    run(env)
    old_profile = deepcopy(env.profile)
    old_profile["extractor"] = "old-extractor"
    store.put_profile(old_profile)
    before = {p: p.read_bytes() for p in receipts(env)}
    env.profile["extractor"] = EXTRACTOR
    monkeypatch.setattr(worker.settings, "company_evidence_enabled", True)
    monkeypatch.setattr(worker, "candidate_symbols", lambda *a, **k: [SYMBOL])
    assert worker._due(SYMBOL, env.item) is True
    result = run(env, {"automatic": True})
    assert result["model_calls"] == 0 and result["symbols"][0]["cached"] is True
    assert len(env.calls) == 1 and {p: p.read_bytes() for p in receipts(env)} == before
    assert store.read_profile(SYMBOL)["extractor"] == EXTRACTOR and worker._due(SYMBOL, env.item) is False


@pytest.mark.parametrize("status", ["source_failed", "model_failed", "invalid_response", "budget_blocked", "interrupted", "canceled"])
def test_old_extractor_never_bypasses_failure_or_cancellation_backoff(env, status):
    old = deepcopy(env.profile)
    old["extractor"] = "old-extractor"
    store.put_profile(old)
    store.write_state(SYMBOL, status, now=NOW, event_ref=worker._event_key(env.item),
                      next_retry_at=(NOW + timedelta(hours=1)).isoformat())
    assert worker._due(SYMBOL, env.item) is False


def test_collector_returning_old_ready_profile_gets_backoff_without_model_or_tick_loop(env, monkeypatch):
    env.profile["extractor"] = "old-extractor"
    store.put_profile(env.profile)
    store.write_state(SYMBOL, "ready", now=NOW, event_ref=worker._event_key(env.item),
                      source_status="ready", next_retry_at=(NOW + timedelta(hours=6)).isoformat())
    monkeypatch.setattr(worker.settings, "company_evidence_enabled", True)
    monkeypatch.setattr(worker, "candidate_symbols", lambda *a, **k: [SYMBOL])
    result = run(env, {"automatic": True})
    assert result["symbols"][0]["state"] == "source_failed" and not env.calls
    assert worker._due(SYMBOL, env.item) is False
    run(env, {"automatic": True})
    assert len(env.collected) == 1 and store.read_profile(SYMBOL) == env.profile


def test_extractor_refresh_missing_source_preserves_old_profile_and_honors_retry(env, monkeypatch):
    old = deepcopy(env.profile)
    old["extractor"] = "old-extractor"
    store.put_profile(old)
    store.write_state(SYMBOL, "ready", now=NOW, event_ref=worker._event_key(env.item),
                      source_status="ready", next_retry_at=(NOW + timedelta(hours=6)).isoformat())
    env.profile.update(status="missing", documents=[], missing=["正式披露暂未取得。"])
    monkeypatch.setattr(worker.settings, "company_evidence_enabled", True)
    monkeypatch.setattr(worker, "candidate_symbols", lambda *a, **k: [SYMBOL])

    result = run(env, {"automatic": True})
    state = store.load_state(SYMBOL)
    assert result["symbols"][0]["state"] == "insufficient" and state["source_status"] == "missing"
    assert state["next_retry_at"] == env.profile["cache_until"] and store.read_profile(SYMBOL) == old
    assert worker._due(SYMBOL, env.item) is False
    repeated = run(env, {"automatic": True})
    assert repeated["symbols"] == [] and len(env.collected) == 1
    assert not env.calls and not usage_rows(env)


def test_automatic_selection_rotates_past_top_four_and_backfills_not_due(env, monkeypatch):
    monkeypatch.setattr(worker.settings, "company_evidence_enabled", True)
    symbols = [f"{i:06d}" for i in range(1, 10)]
    monkeypatch.setattr(worker, "candidate_symbols", lambda *a, **k: symbols)
    for symbol in symbols[:4]:
        store.write_state(symbol, "ready", now=NOW - timedelta(minutes=30), event_ref=worker._event_key(env.item),
                          next_retry_at=(NOW + timedelta(hours=1)).isoformat())
    result = run(env, {"automatic": True})
    assert [v["symbol"] for v in result["symbols"]] == symbols[4:8]
    assert len(env.calls) == 1 and len(env.collected) == 4
    result = run(env, {"automatic": True})
    assert [v["symbol"] for v in result["symbols"]] == symbols[8:]
    assert len(env.calls) == 2


def test_automatic_consumer_cap_uses_existing_receipts_and_shared_slots(env, monkeypatch):
    monkeypatch.setattr(worker.settings, "company_evidence_enabled", True)
    monkeypatch.setattr(worker, "candidate_symbols", lambda *a, **k: [SYMBOL])
    for _ in range(4):
        lease = agent_budget.reserve("autonomy_llm", purpose="company_evidence.select", kind="model", session_factory=env.sf)
        agent_budget.start(lease["id"], env.sf)
        agent_budget.finish(lease["id"], state="succeeded", usage={"input_tokens": 1, "output_tokens": 1}, session_factory=env.sf)
    with pytest.raises(agent_budget.BudgetError) as error:
        run(env, {"automatic": True})
    assert error.value.code == "company_daily_budget" and not env.calls
    assert store.load_state(SYMBOL)["state"] == "budget_blocked" and len(usage_rows(env)) == 4
    # Explicit maintenance still shares the same existing slots, no second budget table.
    run(env)
    assert len(env.calls) == 1 and len(usage_rows(env)) == 5


def test_scheduler_uses_registered_switch_existing_task_and_does_no_work_when_off(env, monkeypatch):
    from app.services import agent_tasks

    assert Settings.model_fields["company_evidence_enabled"].default is False
    assert "company_evidence_enabled" in SCHEDULER_SWITCH_ATTRS
    enqueued = []
    async def enqueue(*args, **kwargs):
        enqueued.append((args, kwargs))
    monkeypatch.setattr(agent_tasks, "create_task_async", enqueue)
    monkeypatch.setattr(worker, "candidate_symbols", lambda *a, **k: [SYMBOL])
    asyncio.run(worker.scheduler_tick(env.app))
    assert not enqueued and not env.collected and not env.calls
    monkeypatch.setattr(worker.settings, "company_evidence_enabled", True)
    asyncio.run(worker.scheduler_tick(env.app))
    assert enqueued == [(("company_evidence", {"automatic": True}), {"created_by": "scheduler"})]
    assert not env.collected and not env.calls and not usage_rows(env)


def test_empty_current_window_is_reported_without_source_or_model(env, monkeypatch):
    monkeypatch.setattr(worker, "candidate_symbols", lambda *a, **k: [])
    result = run(env, {"automatic": False})
    assert result["state"] == "no_candidates" and result["symbols"] == []
    assert not env.collected and not env.calls


def test_scheduler_exposes_queue_persistence_failure_instead_of_claiming_busy_owner(env, monkeypatch):
    from app.services import agent_tasks

    monkeypatch.setattr(worker.settings, "company_evidence_enabled", True)
    monkeypatch.setattr(worker, "candidate_symbols", lambda *a, **k: [SYMBOL])
    error = agent_tasks.TaskQueueUnavailable("f" * 32, "unknown")

    async def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(agent_tasks, "create_task_async", unavailable)
    with pytest.raises(agent_tasks.TaskQueueUnavailable) as caught:
        asyncio.run(worker.scheduler_tick(env.app))
    assert caught.value is error
    assert not env.calls and not usage_rows(env) and not env.collected


def test_event_window_compares_six_valid_distinct_events_from_twelve_persisted_rows(env, monkeypatch):
    rows = [event_item(env, i) for i in range(1, 10)]
    rows[0]["state"] = "withdrawn"
    rows[1]["timing"]["available_at"] = (NOW + timedelta(minutes=1)).isoformat()
    rows.insert(4, deepcopy(rows[3]))
    foreign = event_item(env, 99)
    foreign["event_id"] = 98
    rows.insert(0, foreign)
    calls = []
    def read(*args, **kwargs):
        calls.append(kwargs)
        return {"data": {"items": rows}}
    monkeypatch.setattr(worker, "read_attribution", read)
    monkeypatch.setattr(worker, "_events", env.real_events)
    result = worker._events(SYMBOL, env.app, env.sf)
    assert [v["event_id"] for v in result] == [3, 4, 5, 6, 7, 8]
    assert calls[0]["limit"] == 12 and len(calls) == 1
    assert not env.collected and not env.calls and not usage_rows(env)


def test_request_key_binds_whole_event_window_but_not_capture_clock(env):
    first, second = env.item, event_item(env, 11)
    case = worker._case(SYMBOL, env.profile, [first, second])
    model = {"provider": "fixture", "model": "selector"}
    key = worker.request_key(case, model)
    changed = deepcopy(case)
    changed["captured_at"] = (NOW + timedelta(minutes=1)).isoformat()
    assert worker.request_key(changed, model) == key
    changed["event_candidates"][1]["summary"] += "候选事项发生真实修订。"
    assert worker.request_key(changed, model) != key


def choose_second(env, monkeypatch, *, after=None):
    second = event_item(env, 11)
    env.items.append(second)
    def model(*args, **kwargs):
        rows = json.loads(env.model(*args, **kwargs))
        rows[0]["event_id"] = second["event_id"]
        if after:
            after()
        return json.dumps(rows, ensure_ascii=False)
    monkeypatch.setattr(worker, "bounded_chat_completion", model)
    return second


def test_nonfirst_event_choice_has_final_identity_and_never_repeats_on_discovery_reorder(env, monkeypatch):
    second = choose_second(env, monkeypatch)
    result = run(env)
    prior = store.load_state(SYMBOL)
    assert result["model_calls"] == 1 and prior["event_ref"] == worker._event_key(second)
    assert prior["packet"]["event"]["event_id"] == 11 and prior["packet"]["id"] == "company:" + SYMBOL
    assert "event_candidates" not in prior["packet"] and len(prior["case"]["event_candidates"]) == 2
    assert prior["request_key"] != prior["generation_key"]
    before = {p: p.read_bytes() for p in receipts(env)}
    env.items.reverse()
    env.items.insert(0, event_item(env, 12))
    assert run(env)["symbols"][0]["cached"] is True
    monkeypatch.setattr(worker.settings, "company_evidence_enabled", True)
    monkeypatch.setattr(worker, "candidate_symbols", lambda *a, **k: [SYMBOL])
    automatic = run(env, {"automatic": True})
    assert automatic["state"] == "no_due_candidates" and automatic["model_calls"] == 0
    assert len(env.calls) == len(usage_rows(env)) == 1
    assert {p: p.read_bytes() for p in receipts(env)} == before


def test_selected_event_outside_new_window_stays_reusable_but_withdrawal_refreshes(env, monkeypatch):
    second = choose_second(env, monkeypatch)
    run(env)
    env.items[:] = [event_item(env, 12)]
    monkeypatch.setattr(worker, "_event", lambda *a, **kw: deepcopy(second))
    assert asyncio.run(worker._current_due(SYMBOL, env.items, env.app, env.sf)) is False
    assert run(env)["model_calls"] == 0 and len(env.calls) == 1
    monkeypatch.setattr(worker, "_event", lambda *a, **kw: None)
    assert asyncio.run(worker._current_due(SYMBOL, env.items, env.app, env.sf)) is True


def test_paid_selection_recovers_original_case_after_discovery_window_changes(env, monkeypatch):
    choose_second(env, monkeypatch)
    original_publish = ci.publish_automatic
    monkeypatch.setattr(ci, "publish_automatic", lambda *a, **k: (_ for _ in ()).throw(OSError("fixture publication interrupted")))
    with pytest.raises(OSError):
        run(env)
    prior = store.load_state(SYMBOL)
    env.items.reverse()
    env.items.insert(0, event_item(env, 12))
    env.clock[0] += timedelta(minutes=10)
    monkeypatch.setattr(ci, "publish_automatic", original_publish)
    result = run(env)
    now = store.load_state(SYMBOL)
    assert result["model_calls"] == 0 and len(env.calls) == len(usage_rows(env)) == 1
    assert now["case"] == prior["case"] and now["packet"] == prior["packet"]
    assert now["request_key"] == prior["request_key"] and now["generated_at"] == prior["generated_at"]
    assert ci.read_saved(env.items[1], SYMBOL, now=env.clock[0])["generated_at"] == prior["generated_at"]


def test_unknown_window_overlap_never_shrinks_unconfirmed_identity_or_resends(env):
    second, third = event_item(env, 11), event_item(env, 12)
    original_refs = [worker._event_key(env.item), worker._event_key(second)]
    store.write_state(SYMBOL, "interrupted", now=NOW, candidate_refs=original_refs, root=env.root,
                      event_ref=None, attempt_state="unknown")
    env.items[:] = [second, third]
    assert run(env)["symbols"][0]["state"] == "interrupted"
    assert store.load_state(SYMBOL)["candidate_refs"] == original_refs
    env.items[:] = [env.item, event_item(env, 13)]
    assert run(env)["symbols"][0]["state"] == "interrupted"
    assert not env.calls and not usage_rows(env) and not receipts(env)


def test_author_on_second_candidate_is_retained_before_model(env):
    key = publish_author(env)
    env.items.insert(0, event_item(env, 11))
    result = run(env)
    assert result["symbols"][0]["verification"] == "author_source_reviewed_not_gold"
    assert store.load_state(SYMBOL)["event_ref"] == worker._event_key(env.item)
    assert (env.root / SYMBOL / "10-20-30.current").read_text() == key
    assert not env.calls and not usage_rows(env)


def test_unselected_event_changes_during_model_do_not_rebind_or_block_selected_packet(env, monkeypatch):
    second = choose_second(env, monkeypatch, after=lambda: env.item["source_context"].update(summary="未选事件后来修订。"))
    result = run(env)
    assert result["symbols"][0]["state"] == "ready" and len(env.calls) == 1
    prior = store.load_state(SYMBOL)
    assert prior["event_ref"] == worker._event_key(second)
    assert prior["case"]["event_candidates"][0]["summary"] != env.item["source_context"]["summary"]
