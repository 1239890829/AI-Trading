"""Manual research lifecycle; isolated files/SQLite, no provider or model calls."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.decisions_client import CHOICES, MODEL, DecisionsError
from app.events import company_impact as ci
from app.events.company_state import put_profile
from app.models.agent import AgentResourceUsage
from app.services import agent_budget
from app.services import company_decisions as worker

NOW = datetime.fromisoformat("2026-10-30T11:00:00+08:00")
PARAMS = {"symbol": "003021", "event_id": 10, "version_id": 20, "observation_id": 30}
USAGE = {"input_tokens": 100, "output_tokens": 0, "total_tokens": 100,
         "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
         "output_tokens_details": {"reasoning_tokens": 0}}


class Recorder:
    task_id = "d" * 32
    canceled = False

    def checkpoint(self):
        if self.canceled:
            raise asyncio.CancelledError

    async def checkpoint_async(self):
        self.checkpoint()

    async def add_async(self, **kwargs):
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated.db'}")
    AgentResourceUsage.__table__.create(engine)
    sf, clock = sessionmaker(bind=engine), [NOW]
    monkeypatch.setattr(ci, "ROOT", tmp_path / "company")
    monkeypatch.setattr(worker, "get_session_factory", lambda: sf)
    monkeypatch.setattr(worker, "beijing_now", lambda: clock[0])
    monkeypatch.setattr(agent_budget, "beijing_now", lambda: clock[0])
    monkeypatch.setattr(agent_budget, "beijing_now_naive", lambda: clock[0].replace(tzinfo=None))
    monkeypatch.setattr(agent_budget.settings, "agent_daily_llm_budget", 8)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(decisions_research_enabled=True,
        decisions_api_key="synthetic-secret", decisions_proxy="", decisions_timeout_seconds=30))
    profile = {"symbol": PARAMS["symbol"], "company_name": "夹具驱动公司", "short_name": "夹具驱动",
        "status": "ready", "checked_at": "2026-10-30T10:00:00+08:00", "missing": [],
        "cache_until": "2026-10-30T16:00:00+08:00", "documents": [{"id": "report",
        "symbol": PARAMS["symbol"], "company_name": "夹具驱动公司", "title": "2026年半年度报告", "period": "2026H1",
        "url": "https://static.cninfo.com.cn/finalpage/2026-08-15/1234567890.PDF",
        "published_at": "2026-08-15T23:59:59+08:00", "received_at": "2026-10-30T09:00:00+08:00",
        "sha256": "b" * 64, "identity_quote": "夹具驱动公司证券代码003021",
        "excerpts": [{"kind": "product", "page": 2, "quote": "公司主营为光模块的研发、生产和销售。"}]}]}
    put_profile(profile)
    item = {"interpretation_ref": {**{k: PARAMS[k] for k in worker.REF_FIELDS}, "state": "active"},
        "source_context": {"observation_id": 30, "title": "夹具驱动公司签订光模块合同", "summary": "合同尚需交付验收。"},
        "timing": {"available_at": "2026-10-30T09:30:00+08:00", "received_at": "2026-10-30T09:30:00+08:00",
                   "source_published_at": "2026-10-30T09:20:00+08:00"}, "source": {"content_hash": "c" * 64}}
    monkeypatch.setattr(worker, "_event", lambda *a, **kw: deepcopy(item))
    calls = []

    def model(request, **kwargs):
        calls.append(deepcopy(request))
        kwargs["usage_callback"](deepcopy(USAGE))
        answers = [{"type": "choice", "name": q["name"], "choice": "supported", "confidence": 0.8,
            "probabilities": [{"value": v, "probability": 0.7 if v == "supported" else 0.1} for v in CHOICES]}
            for q in request["questions"]]
        return {"model": MODEL, "answers": answers, "usage": deepcopy(USAGE),
                "response_sha256": "a" * 64, "elapsed_seconds": 0.25}

    monkeypatch.setattr(worker, "create_decisions", model)
    app = SimpleNamespace(state=SimpleNamespace(decisions_evidence_dir=tmp_path / "decisions"))
    yield SimpleNamespace(sf=sf, clock=clock, profile=profile, item=item, calls=calls, app=app,
                          root=tmp_path / "decisions", model=model)
    engine.dispose()


def run(env, params=None, rec=None):
    return asyncio.run(worker.run_company_decisions(rec or Recorder(), params or PARAMS, env.app))


def read(env, **kwargs):
    return worker.read_company_decisions(**PARAMS, app=env.app, **kwargs)


def rows(env):
    with env.sf() as db:
        return list(db.scalars(select(AgentResourceUsage)))


def saved(env):
    return next((env.root / PARAMS["symbol"]).glob("[a-f0-9]" * 64 + ".json"))


def test_generate_save_read_reuse_is_scoped_and_preserves_old_explanations(env):
    old = ci.ROOT / PARAMS["symbol"] / "old-explanation.json"
    old.write_text('{"historical":true}')
    before = {p: p.read_bytes() for p in ci.ROOT.rglob("*") if p.is_file()}
    value = run(env)
    assert value["state"] == "ready" and not value["cached"]
    assert value["eligibility_changed"] is False and value["human_gold_count"] == 0
    assert value["research_only"] is True and value["probabilities_are_accuracy"] is False
    assert value["result"]["items"][0]["fact_id"] == "report:0"
    assert read(env)["result"] == value["result"]
    env.clock[0] += timedelta(minutes=1)
    assert run(env)["cached"] is True and len(env.calls) == 1
    assert len(rows(env)) == 1 and rows(env)[0].usage_known == 1
    assert rows(env)[0].scope == agent_budget.SCOPE_AUTONOMY_LLM and rows(env)[0].purpose == worker.PURPOSE
    assert {p: p.read_bytes() for p in before} == before
    stored = saved(env).read_text()
    assert "synthetic-secret" not in stored and "光模块" not in stored and "request" not in json.loads(stored)


def test_fresh_recheck_of_identical_source_does_not_create_billable_identity(env):
    run(env)
    env.profile["checked_at"] = "2026-10-30T10:30:00+08:00"
    put_profile(env.profile)
    assert run(env)["cached"] is True and len(env.calls) == 1


def test_event_raw_identity_change_invalidates_even_with_unchanged_text(env):
    run(env)
    old = saved(env).read_bytes()
    env.item["source"]["content_hash"] = "e" * 64
    assert read(env)["state"] == "stale" and saved(env).read_bytes() == old


def test_builder_sends_only_declared_public_event_fields(env):
    packet = worker._packet(PARAMS["symbol"], env.profile, env.item)
    packet["event"]["private_account"] = "DO_NOT_SEND"
    packet["account"] = "DO_NOT_SEND"
    prepared = worker.prepare_packet(packet)
    assert "DO_NOT_SEND" not in json.dumps(prepared["request"])
    assert len(prepared["request"]["questions"]) == len(prepared["facts"])


def test_read_never_creates_and_historical_read_never_backfills(env):
    assert read(env)["state"] == "not_requested" and not env.root.exists()
    run(env)
    before = {p: p.read_bytes() for p in env.root.rglob("*") if p.is_file()}
    assert read(env, response_as_of=NOW - timedelta(seconds=1))["state"] == "historical_excluded"
    assert {p: p.read_bytes() for p in before} == before and len(env.calls) == 1


def test_readiness_is_only_saved_evidence_and_read_gate_without_side_effects(env, monkeypatch):
    before = {p: p.read_bytes() for p in ci.ROOT.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("a read must not write, reserve, repair, collect or call a model")

    monkeypatch.setattr(worker, "create_decisions", forbidden)
    monkeypatch.setattr(worker, "atomic_json", forbidden)
    for method in ("reserve", "start", "finish", "release", "recover_stale_reservations"):
        monkeypatch.setattr(agent_budget, method, forbidden)
    value = read(env)
    assert value["state"] == "not_requested" and value["result"] is None
    assert value["source_ready"] and value["configured"] and value["can_request"]
    assert value["reason_code"] is None
    assert not env.root.exists() and rows(env) == []
    assert {p: p.read_bytes() for p in before} == before
    assert "synthetic-secret" not in json.dumps(value)
    assert read(env, response_as_of=NOW)["state"] == "historical_excluded"
    assert not read(env, response_as_of=NOW)["can_request"]


@pytest.mark.parametrize("cause,reason", [("disabled", "disabled"), ("key", "not_configured"),
    ("profile", "company_evidence_missing"), ("stale", "company_evidence_stale"),
    ("event", "event_unavailable_or_changed")])
def test_readiness_exposes_actual_missing_condition_without_starting(env, monkeypatch, cause, reason):
    if cause == "disabled":
        worker.settings.decisions_research_enabled = False
    elif cause == "key":
        worker.settings.decisions_api_key = ""
    elif cause == "profile":
        monkeypatch.setattr(worker, "read_profile", lambda _: None)
    elif cause == "stale":
        env.clock[0] += timedelta(days=1)
    else:
        env.item["interpretation_ref"]["version_id"] += 1
    value = read(env)
    assert value["state"] == "not_requested" and not value["can_request"]
    assert value["reason_code"] == reason
    assert value["source_ready"] == (cause in {"disabled", "key"})
    assert value["configured"] == (cause not in {"disabled", "key"})
    assert not env.root.exists() and rows(env) == [] and env.calls == []


def test_readiness_keeps_unknown_usage_and_existing_result_read_is_not_generation(env):
    run(env)
    assert read(env)["can_request"] is False
    env.item["interpretation_ref"]["version_id"] += 1
    lease = agent_budget.reserve(agent_budget.SCOPE_AUTONOMY_LLM, purpose="other",
        kind="model", task_id="e" * 32, provider="other", model="other",
        timeout_seconds=10, max_retries=0, input_chars=20, session_factory=env.sf)
    agent_budget.start(lease["id"], env.sf)
    agent_budget.finish(lease["id"], state="failed", usage=None, session_factory=env.sf)
    before = [agent_budget.dump(row) for row in rows(env)]
    value = worker.read_company_decisions(**{**PARAMS, "version_id": 21}, app=env.app)
    assert value["source_ready"] and value["configured"] and not value["can_request"]
    assert value["reason_code"] == "usage_unknown"
    assert [agent_budget.dump(row) for row in rows(env)] == before and len(env.calls) == 1


def test_source_drift_keeps_old_receipt_and_hides_old_judgment(env):
    run(env)
    path, before = saved(env), saved(env).read_bytes()
    env.profile["documents"][0]["excerpts"][0]["quote"] += "该产品已停止供货。"
    put_profile(env.profile)
    assert read(env)["state"] == "stale" and read(env)["result"] is None
    assert path.read_bytes() == before
    run(env)
    assert len(env.calls) == 2 and path.read_bytes() == before


@pytest.mark.parametrize("patch", [{"version_id": 99}, {"observation_id": 99}, {"event_id": 99}])
def test_foreign_version_is_rejected_before_model(env, patch):
    with pytest.raises(DecisionsError, match="event_unavailable_or_changed"):
        run(env, {**PARAMS, **patch})
    assert not env.calls and not rows(env)


@pytest.mark.parametrize("patch", [{"symbol": "AAPL"}, {"event_id": True}, {"instructions": "override"}])
def test_only_fixed_ashare_identity_is_accepted(env, patch):
    with pytest.raises(ValueError):
        run(env, {**PARAMS, **patch})
    assert not env.calls and not rows(env)


def test_missing_stale_or_future_saved_evidence_never_starts_collection(env):
    env.clock[0] += timedelta(days=1)
    with pytest.raises(DecisionsError, match="company_evidence_stale"):
        run(env)
    assert not env.calls and not rows(env)


def test_failed_source_status_cannot_be_upgraded_by_preserved_documents(env):
    env.profile["status"] = "failed"
    put_profile(env.profile)
    with pytest.raises(DecisionsError, match="company_evidence_unavailable"):
        run(env)
    assert not env.calls and not rows(env)


def test_disabled_stops_new_calls_but_does_not_hide_saved_result(env):
    run(env)
    worker.settings.decisions_research_enabled = False
    assert run(env)["cached"] is True and read(env)["state"] == "ready"
    env.item["source_context"]["summary"] += "来源更新。"
    with pytest.raises(DecisionsError, match="disabled"):
        run(env)
    assert len(env.calls) == 1


@pytest.mark.parametrize("known", [False, True])
def test_failed_attempt_preserves_meter_and_is_never_repeated(env, monkeypatch, known):
    count = []

    def fail(request, **kwargs):
        count.append(1)
        if known:
            kwargs["usage_callback"](deepcopy(USAGE))
        raise DecisionsError("invalid_answer" if known else "timeout")

    monkeypatch.setattr(worker, "create_decisions", fail)
    with pytest.raises(DecisionsError):
        run(env)
    assert rows(env)[0].usage_known == int(known)
    assert rows(env)[0].input_tokens == (100 if known else None)
    assert run(env)["state"] == "interrupted" and len(count) == 1
    assert read(env)["result"] is None


def test_shared_same_day_unknown_blocks_new_consumer_without_exception(env):
    lease = agent_budget.reserve(agent_budget.SCOPE_AUTONOMY_LLM, purpose="old.test", kind="model", session_factory=env.sf)
    agent_budget.start(lease["id"], env.sf)
    agent_budget.finish(lease["id"], state="unknown", session_factory=env.sf)
    with pytest.raises(agent_budget.BudgetError, match="未知"):
        run(env)
    assert not env.calls and len(rows(env)) == 1 and rows(env)[0].usage_known == 0


def test_previous_day_unknown_is_retained_without_inventing_today_block(env):
    env.clock[0] -= timedelta(days=1)
    lease = agent_budget.reserve(agent_budget.SCOPE_AUTONOMY_LLM, purpose="old.test", kind="model", session_factory=env.sf)
    agent_budget.start(lease["id"], env.sf)
    agent_budget.finish(lease["id"], state="unknown", session_factory=env.sf)
    env.clock[0] = NOW
    assert run(env)["state"] == "ready"
    with env.sf() as db:
        prior = db.get(AgentResourceUsage, lease["id"])
        assert prior.state == "unknown" and prior.usage_known == 0 and prior.input_tokens is None


def test_db_failure_recovers_saved_answer_without_repeating_model(env, monkeypatch):
    original, failures = agent_budget.finish, [True]

    def fail_once(*args, **kwargs):
        if failures.pop() if failures else False:
            raise RuntimeError("synthetic database unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(agent_budget, "finish", fail_once)
    with pytest.raises(RuntimeError, match="database"):
        run(env)
    assert json.loads(saved(env).read_text())["state"] == "response_saved"
    assert rows(env)[0].state == "started" and rows(env)[0].usage_known == 0
    assert read(env)["result"] is None
    recovered = run(env)
    assert recovered["state"] == "ready" and recovered["cached"] is True and len(env.calls) == 1
    assert rows(env)[0].state == "succeeded" and rows(env)[0].input_tokens == 100


def test_receipt_write_failure_does_not_discard_known_successful_answer(env, monkeypatch):
    original, failed = worker.atomic_json, []

    def fail_once(path, value):
        if value.get("state") == "response_saved" and not failed:
            failed.append(True)
            raise OSError("synthetic storage pause")
        return original(path, value)

    monkeypatch.setattr(worker, "atomic_json", fail_once)
    with pytest.raises(OSError, match="storage pause"):
        run(env)
    assert rows(env)[0].state == "succeeded" and rows(env)[0].input_tokens == 100
    assert read(env)["state"] == "accounting_pending"
    assert run(env)["state"] == "ready" and len(env.calls) == 1


def test_invalid_answer_with_usage_recovers_failed_meter_only(env, monkeypatch):
    finish, calls = agent_budget.finish, []

    def bad_answer(request, **kwargs):
        calls.append(1)
        kwargs["usage_callback"](deepcopy(USAGE))
        raise DecisionsError("invalid_answer")

    monkeypatch.setattr(worker, "create_decisions", bad_answer)
    monkeypatch.setattr(agent_budget, "finish", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("synthetic db failure")))
    with pytest.raises(RuntimeError, match="db failure"):
        run(env)
    assert rows(env)[0].state == "started" and rows(env)[0].usage_known == 0
    monkeypatch.setattr(agent_budget, "finish", finish)
    assert run(env)["state"] == "interrupted" and calls == [1]
    assert rows(env)[0].state == "failed" and rows(env)[0].input_tokens == 100
    assert read(env)["result"] is None


@pytest.mark.parametrize("intervening_start", [False, True])
def test_failed_release_recovers_only_exact_never_started_reservation(env, monkeypatch, intervening_start):
    release = agent_budget.release

    class BeforeStart(Recorder):
        calls = 0

        def checkpoint(self):
            self.calls += 1
            if self.calls == 3:
                raise RuntimeError("synthetic before start")

    monkeypatch.setattr(agent_budget, "release", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("synthetic release failure")))
    with pytest.raises(RuntimeError, match="release failure"):
        run(env, rec=BeforeStart())
    row = rows(env)[0]
    assert row.state == "reserved" and row.started_at is None and not env.calls
    monkeypatch.setattr(agent_budget, "release", release)
    if intervening_start:
        agent_budget.start(row.id, env.sf)
        with pytest.raises(DecisionsError, match="recovery_evidence_invalid"):
            run(env)
        assert not env.calls and rows(env)[0].id == row.id and rows(env)[0].state == "started"
    else:
        assert run(env)["state"] == "ready" and len(env.calls) == 1
        assert len(rows(env)) == 1 and rows(env)[0].id != row.id


def test_future_result_is_not_read_or_reused_and_expiry_never_extends(env):
    run(env)
    path = saved(env)
    original = path.read_bytes()
    value = json.loads(original)
    value["generated_at"] = (NOW + timedelta(minutes=1)).isoformat()
    path.write_text(json.dumps(value))
    assert read(env)["result"] is None
    with pytest.raises(DecisionsError, match="receipt_time_invalid"):
        run(env)
    path.write_bytes(original)
    env.clock[0] = NOW.replace(hour=17)
    env.profile["checked_at"] = env.clock[0].isoformat()
    env.profile["cache_until"] = (env.clock[0] + timedelta(hours=6)).isoformat()
    put_profile(env.profile)
    assert run(env)["state"] == "stale" and read(env)["state"] == "stale"
    assert path.read_bytes() == original and len(env.calls) == 1


def test_cancel_drains_real_thread_and_keeps_completed_answer_and_usage(env, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    rec = Recorder()

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return env.model(*args, **kwargs)

    monkeypatch.setattr(worker, "create_decisions", blocked)

    async def scenario():
        task = asyncio.create_task(worker.run_company_decisions(rec, PARAMS, env.app))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4)
            rec.canceled = True
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and rows(env)[0].state == "started"
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert rows(env)[0].state == "succeeded" and rows(env)[0].usage_known == 1
    assert read(env)["state"] == "ready" and len(env.calls) == 1


def test_invalid_saved_answer_never_reads_as_valid(env):
    run(env)
    path = saved(env)
    value = json.loads(path.read_text())
    value["response"]["answers"][0]["choice"] = "buy"
    path.write_text(json.dumps(value))
    assert read(env)["result"] is None
    with pytest.raises(DecisionsError):
        run(env)
    assert len(env.calls) == 1


def test_all_questions_refusal_is_visible_not_a_negative_fact(env, monkeypatch):
    def refusal(*args, **kwargs):
        value = env.model(*args, **kwargs)
        value["answers"] = [{"type": "refusal", "name": "fact_0"}]
        return value

    monkeypatch.setattr(worker, "create_decisions", refusal)
    result = run(env)["result"]
    assert result["coverage"] == {"total": 1, "answered": 0, "refused": 1}
    assert result["items"][0]["label"] == "模型拒答" and "choice" not in result["items"][0]
