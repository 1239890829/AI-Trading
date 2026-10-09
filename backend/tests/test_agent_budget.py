"""IMP-052 durable Agent quota reservation and usage accounting."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import beijing_now_naive
from app.core.config import settings
from app.models.watchlist import Base
from app.services import agent_budget as ab


def _factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'budget.db'}",
        connect_args={"timeout": 10, "check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    monkeypatch.setattr(settings, "agent_daily_llm_budget", 2)
    monkeypatch.setattr(settings, "alert_triage_daily_llm_budget", None)
    monkeypatch.setattr(settings, "agent_daily_task_budget", 2)
    monkeypatch.setattr(settings, "agent_model_max_timeout_seconds", 180.0)
    monkeypatch.setattr(settings, "agent_model_max_retries", 2)
    monkeypatch.setattr(settings, "agent_model_max_input_chars", 250_000)
    monkeypatch.setattr(settings, "agent_model_max_output_chars", 150_000)
    monkeypatch.setattr(settings, "agent_budget_reservation_ttl_seconds", 300)
    return factory


def test_two_process_like_reservations_cannot_both_take_last_slot(sf, monkeypatch):
    monkeypatch.setattr(settings, "agent_daily_llm_budget", 1)

    def take():
        try:
            row = ab.reserve(
                ab.SCOPE_AUTONOMY_LLM,
                purpose="race",
                kind="model",
                provider="openai",
                model="fixture",
                timeout_seconds=30,
                session_factory=sf,
            )
            return ("ok", row["slot"])
        except ab.BudgetError as exc:
            return (exc.code, None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        got = list(pool.map(lambda _: take(), range(2)))
    assert sorted(x[0] for x in got) == ["budget_exhausted", "ok"]
    assert [x[1] for x in got if x[0] == "ok"] == [1]


def test_stale_never_started_reservation_is_recovered(sf, monkeypatch):
    monkeypatch.setattr(settings, "agent_daily_llm_budget", 1)
    monkeypatch.setattr(settings, "agent_budget_reservation_ttl_seconds", 1)
    first = ab.reserve(
        ab.SCOPE_AUTONOMY_LLM, purpose="old", kind="model",
        timeout_seconds=30, session_factory=sf,
    )
    from app.models.agent import AgentResourceUsage
    with sf() as db:
        row = db.get(AgentResourceUsage, first["id"])
        row.created_at = beijing_now_naive() - timedelta(seconds=5)
        db.commit()
    second = ab.reserve(
        ab.SCOPE_AUTONOMY_LLM, purpose="new", kind="model",
        timeout_seconds=30, session_factory=sf,
    )
    assert second["slot"] == 1 and second["id"] != first["id"]


def test_started_unknown_usage_is_never_refunded_and_blocks_more_autonomy(sf):
    first = ab.reserve(
        ab.SCOPE_AUTONOMY_LLM, purpose="timeout", kind="model",
        provider="openai", model="fixture", timeout_seconds=30, session_factory=sf,
    )
    ab.start(first["id"], sf)
    done = ab.finish(first["id"], state="failed", usage=None,
                     error_kind="timeout", session_factory=sf)
    assert done["usage_known"] is False
    with pytest.raises(ab.BudgetError, match="用量未知") as err:
        ab.reserve(
            ab.SCOPE_AUTONOMY_LLM, purpose="later", kind="model",
            timeout_seconds=30, session_factory=sf,
        )
    assert err.value.code == "usage_unknown"
    status = ab.budget_status(sf)
    assert status["llm_used"] == 1
    assert status["token_usage_unknown_calls"] == 1
    assert status["token_accounting_complete"] is False


def test_known_usage_counts_real_tokens_not_zero_placeholders(sf):
    row = ab.reserve(
        ab.SCOPE_AUTONOMY_LLM, purpose="known", kind="model",
        timeout_seconds=30, session_factory=sf,
    )
    ab.start(row["id"], sf)
    out = ab.finish(
        row["id"], state="succeeded",
        usage={"prompt_tokens": 123, "completion_tokens": 17},
        output_chars=50, attempts=1, session_factory=sf,
    )
    assert out["input_tokens"] == 123 and out["output_tokens"] == 17
    assert out["usage_known"] is True
    status = ab.budget_status(sf)
    assert status["input_tokens"] == 123 and status["output_tokens"] == 17
    assert status["token_usage_known_calls"] == 1
    assert status["token_usage_unknown_calls"] == 0


def test_task_budget_is_cross_process_slot_based(sf, monkeypatch):
    monkeypatch.setattr(settings, "agent_daily_task_budget", 1)
    row = ab.reserve(
        ab.SCOPE_AUTONOMY_TASK, purpose="agenda:A", kind="task", session_factory=sf,
    )
    ab.start(row["id"], sf)
    ab.finish(row["id"], state="succeeded", usage={}, session_factory=sf)
    with pytest.raises(ab.BudgetError) as err:
        ab.reserve(
            ab.SCOPE_AUTONOMY_TASK, purpose="agenda:B", kind="task", session_factory=sf,
        )
    assert err.value.code == "budget_exhausted"
    status = ab.budget_status(sf)
    assert status["tasks_used"] == 1 and status["tasks_exhausted"] is True


@pytest.mark.parametrize("scope", [ab.SCOPE_AUTONOMY_LLM, ab.SCOPE_ALERT_TRIAGE_LLM])
def test_model_policy_bounds_timeout_and_retry(sf, scope):
    with pytest.raises(ab.BudgetError) as timeout:
        ab.reserve(
            scope, purpose="slow", kind="model",
            timeout_seconds=181, session_factory=sf,
        )
    assert timeout.value.code == "timeout_budget_exceeded"
    with pytest.raises(ab.BudgetError) as retry:
        ab.reserve(
            scope, purpose="retry", kind="model",
            timeout_seconds=30, max_retries=3, session_factory=sf,
        )
    assert retry.value.code == "retry_budget_exceeded"


def test_unmetered_model_receipts_share_token_accounting_without_using_slots(sf):
    a = ab.record_unmetered(
        purpose="triage.llm", provider="openai", model="m", state="succeeded",
        usage={"input_tokens": 10, "output_tokens": 2}, attempts=1,
        timeout_seconds=30, session_factory=sf,
    )
    b = ab.record_unmetered(
        purpose="triage.jev", provider="jev", model="j", state="succeeded",
        usage={"input_tokens": 20, "output_tokens": 3}, attempts=2,
        timeout_seconds=8, session_factory=sf,
    )
    assert a["slot"] is None and b["slot"] is None
    status = ab.budget_status(sf)
    assert status["llm_used"] == 0
    assert status["input_tokens"] == 30 and status["output_tokens"] == 5


def test_started_reservation_cannot_be_released(sf):
    row = ab.reserve(
        ab.SCOPE_AUTONOMY_LLM, purpose="started", kind="model",
        timeout_seconds=30, session_factory=sf,
    )
    ab.start(row["id"], sf)
    assert ab.release(row["id"], sf) is False
    assert ab.budget_status(sf)["llm_used"] == 1


@pytest.mark.parametrize("scope", [ab.SCOPE_AUTONOMY_LLM, ab.SCOPE_ALERT_TRIAGE_LLM])
def test_input_budget_blocks_before_reservation(sf, monkeypatch, scope):
    monkeypatch.setattr(settings, "agent_model_max_input_chars", 10)
    with pytest.raises(ab.BudgetError) as err:
        ab.reserve(
            scope, purpose="too-large", kind="model",
            input_chars=11, timeout_seconds=30, session_factory=sf,
        )
    assert err.value.code == "input_budget_exceeded"
    assert ab.budget_status(sf)["llm_used"] == 0
    assert ab.budget_status(sf)["triage_used"] == 0


@pytest.mark.parametrize("scope", [ab.SCOPE_AUTONOMY_LLM, ab.SCOPE_ALERT_TRIAGE_LLM])
def test_output_budget_records_failed_and_refuses_success(sf, monkeypatch, scope):
    monkeypatch.setattr(settings, "agent_model_max_output_chars", 5)
    row = ab.reserve(
        scope, purpose="oversized-output", kind="model",
        input_chars=1, timeout_seconds=30, session_factory=sf,
    )
    ab.start(row["id"], sf)
    with pytest.raises(ab.BudgetError) as err:
        ab.finish(
            row["id"], state="succeeded",
            usage={"input_tokens": 3, "output_tokens": 2},
            output_chars=6, session_factory=sf,
        )
    assert err.value.code == "output_budget_exceeded"
    receipt = ab.recent_usage(sf, limit=1)[0]
    assert receipt["state"] == "failed"
    assert receipt["error_kind"] == "output_budget_exceeded"
    assert receipt["output_chars"] == 6


def test_recent_usage_is_metadata_only(sf):
    ab.record_unmetered(
        purpose="review.llm", provider="openai", model="m", state="succeeded",
        usage={"input_tokens": 4, "output_tokens": 1}, input_chars=99, output_chars=10,
        session_factory=sf,
    )
    rows = ab.recent_usage(sf, limit=5)
    assert rows and rows[0]["purpose"] == "review.llm"
    text = str(rows[0]).lower()
    assert "prompt" not in text and "messages" not in text and "authorization" not in text


def test_resource_usage_api_exposes_summary_and_metadata_only(sf, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import agent as routes

    monkeypatch.setattr(ab, "get_session_factory", lambda: sf)
    ab.record_unmetered(
        purpose="api-test", provider="openai", model="m", state="succeeded",
        usage={"input_tokens": 5, "output_tokens": 1},
        input_chars=20, output_chars=4, session_factory=sf,
    )
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        resp = client.get("/agent/resource-usage?limit=5")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary"]["input_tokens"] == 5
    assert data["receipts"][0]["purpose"] == "api-test"
    body = str(data).lower()
    assert "prompt" not in body and "authorization" not in body


def test_daily_model_budget_does_not_reset_when_task_or_purpose_changes(sf, monkeypatch):
    """IMP-052 step ⑥: candidate/task hopping cannot manufacture a fresh model budget."""
    monkeypatch.setattr(settings, "agent_daily_llm_budget", 1)
    first = ab.reserve(
        ab.SCOPE_AUTONOMY_LLM, purpose="candidate.alpha", kind="model",
        task_id="task-alpha", timeout_seconds=30, session_factory=sf,
    )
    ab.start(first["id"], sf)
    ab.finish(
        first["id"], state="succeeded",
        usage={"input_tokens": 10, "output_tokens": 2}, session_factory=sf,
    )
    with pytest.raises(ab.BudgetError) as err:
        ab.reserve(
            ab.SCOPE_AUTONOMY_LLM, purpose="candidate.beta", kind="model",
            task_id="task-beta", timeout_seconds=30, session_factory=sf,
        )
    assert err.value.code == "budget_exhausted"
    assert ab.budget_status(sf)["llm_used"] == 1


def test_unlimited_triage_has_durable_lifecycle_beyond_eight_calls(sf):
    from app.models.agent import AgentResourceUsage

    ids = []
    for _ in range(11):
        row = ab.reserve(
            ab.SCOPE_ALERT_TRIAGE_LLM, purpose="triage.llm", kind="model",
            provider="fixture", timeout_seconds=30, session_factory=sf,
        )
        ids.append(row["id"])
        assert row["slot"] is None and row["state"] == "reserved"
        assert row["attempts"] == 0 and row["started_at"] is None
        with sf() as db:
            assert db.get(AgentResourceUsage, row["id"]).state == "reserved"
        ab.start(row["id"], sf)
        ab.finish(
            row["id"], state="succeeded",
            usage={"input_tokens": 11, "output_tokens": 2}, session_factory=sf,
        )
    assert len(set(ids)) == 11
    with sf() as db:
        receipts = [db.get(AgentResourceUsage, usage_id) for usage_id in ids]
        assert all(r.scope == ab.SCOPE_ALERT_TRIAGE_LLM and r.slot is None for r in receipts)
        assert all(r.state == "succeeded" and r.started_at and r.finished_at for r in receipts)
    status = ab.budget_status(sf)
    assert status["triage_used"] == 11 and status["triage_budget"] is None
    assert status["triage_unlimited"] is True and status["triage_exhausted"] is False
    assert status["input_tokens"] == 121 and status["output_tokens"] == 22
    assert status["llm_used"] == 0 and status["llm_budget"] == 2
    assert type(settings).model_fields["agent_daily_llm_budget"].default == 8
    for _ in range(2):
        ab.reserve(ab.SCOPE_AUTONOMY_LLM, purpose="agenda", kind="model", session_factory=sf)
    with pytest.raises(ab.BudgetError) as err:
        ab.reserve(ab.SCOPE_AUTONOMY_LLM, purpose="agenda", kind="model", session_factory=sf)
    assert err.value.code == "budget_exhausted"


@pytest.mark.parametrize("cap", [0, 2])
def test_triage_can_be_disabled_or_given_a_finite_daily_cap(sf, monkeypatch, cap):
    monkeypatch.setattr(settings, "alert_triage_daily_llm_budget", cap)
    for expected_slot in range(1, cap + 1):
        row = ab.reserve(
            ab.SCOPE_ALERT_TRIAGE_LLM, purpose="triage.llm", kind="model", session_factory=sf,
        )
        assert row["slot"] == expected_slot
    with pytest.raises(ab.BudgetError) as err:
        ab.reserve(
            ab.SCOPE_ALERT_TRIAGE_LLM, purpose="triage.llm", kind="model", session_factory=sf,
        )
    assert err.value.code == "budget_exhausted"
    status = ab.budget_status(sf)
    assert status["triage_used"] == cap and status["triage_budget"] == cap
    assert status["triage_unlimited"] is False and status["triage_exhausted"] is True
    assert status["llm_used"] == 0


def test_finite_triage_counts_unlimited_and_legacy_receipts_during_last_slot_race(sf, monkeypatch):
    legacy = ab.reserve(
        ab.SCOPE_AUTONOMY_LLM, purpose="triage.llm", kind="model", session_factory=sf,
    )
    unlimited = ab.reserve(
        ab.SCOPE_ALERT_TRIAGE_LLM, purpose="triage.llm", kind="model", session_factory=sf,
    )
    monkeypatch.setattr(settings, "alert_triage_daily_llm_budget", 3)

    def take():
        try:
            return ab.reserve(
                ab.SCOPE_ALERT_TRIAGE_LLM, purpose="triage.llm", kind="model", session_factory=sf,
            )["id"]
        except ab.BudgetError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: take(), range(2)))
    assert results.count("budget_exhausted") == 1
    receipts = {row["id"]: row for row in ab.recent_usage(sf)}
    assert len(receipts) == 3
    assert receipts[legacy["id"]]["scope"] == ab.SCOPE_AUTONOMY_LLM
    assert receipts[legacy["id"]]["slot"] == 1
    assert receipts[unlimited["id"]]["slot"] is None
    status = ab.budget_status(sf)
    assert status["triage_used"] == 3 and status["triage_exhausted"] is True
    assert status["llm_used"] == 1 and status["llm_exhausted"] is False
    ab.reserve(ab.SCOPE_AUTONOMY_LLM, purpose="agenda", kind="model", session_factory=sf)
    with pytest.raises(ab.BudgetError) as err:
        ab.reserve(ab.SCOPE_AUTONOMY_LLM, purpose="agenda", kind="model", session_factory=sf)
    assert err.value.code == "budget_exhausted"


def test_lowered_triage_cap_counts_receipts_above_new_slot_range(sf, monkeypatch):
    monkeypatch.setattr(settings, "alert_triage_daily_llm_budget", 5)
    rows = [ab.reserve(
        ab.SCOPE_ALERT_TRIAGE_LLM, purpose="triage.llm", kind="model", session_factory=sf,
    ) for _ in range(5)]
    for row in rows[:4]:
        assert ab.release(row["id"], sf) is True
    assert rows[-1]["slot"] == 5
    monkeypatch.setattr(settings, "alert_triage_daily_llm_budget", 2)
    ab.reserve(ab.SCOPE_ALERT_TRIAGE_LLM, purpose="triage.llm", kind="model", session_factory=sf)
    with pytest.raises(ab.BudgetError) as err:
        ab.reserve(ab.SCOPE_ALERT_TRIAGE_LLM, purpose="triage.llm", kind="model", session_factory=sf)
    assert err.value.code == "budget_exhausted"
    assert ab.budget_status(sf)["triage_used"] == 2


@pytest.mark.parametrize("source", [ab.SCOPE_AUTONOMY_LLM, ab.SCOPE_ALERT_TRIAGE_LLM])
@pytest.mark.parametrize("target", [ab.SCOPE_AUTONOMY_LLM, ab.SCOPE_ALERT_TRIAGE_LLM])
def test_unknown_started_model_usage_blocks_both_consuming_scopes(sf, source, target):
    first = ab.reserve(source, purpose="model.fixture", kind="model", session_factory=sf)
    ab.start(first["id"], sf)
    with pytest.raises(ab.BudgetError) as running:
        ab.reserve(target, purpose="later", kind="model", session_factory=sf)
    assert running.value.code == "usage_unknown"
    ab.finish(first["id"], state="failed", usage=None, error_kind="timeout", session_factory=sf)
    assert ab.release(first["id"], sf) is False
    with pytest.raises(ab.BudgetError) as failed:
        ab.reserve(target, purpose="later", kind="model", session_factory=sf)
    assert failed.value.code == "usage_unknown"


def test_unknown_jev_telemetry_does_not_block_triage_or_autonomy(sf):
    ab.record_unmetered(
        purpose="triage.jev", provider="jev", model="fixture", state="failed",
        usage=None, attempts=1, error_kind="http_451", session_factory=sf,
    )
    for scope in [ab.SCOPE_AUTONOMY_LLM, ab.SCOPE_ALERT_TRIAGE_LLM]:
        row = ab.reserve(scope, purpose="later", kind="model", session_factory=sf)
        assert ab.start(row["id"], sf)["state"] == "started"
        ab.finish(
            row["id"], state="succeeded",
            usage={"input_tokens": 3, "output_tokens": 1}, session_factory=sf,
        )
    status = ab.budget_status(sf)
    assert status["token_usage_unknown_calls"] == 1 and status["token_accounting_complete"] is False
    assert status["llm_used"] == 1 and status["triage_used"] == 1


@pytest.mark.parametrize("source", [ab.SCOPE_AUTONOMY_LLM, ab.SCOPE_ALERT_TRIAGE_LLM])
@pytest.mark.parametrize("target", [ab.SCOPE_AUTONOMY_LLM, ab.SCOPE_ALERT_TRIAGE_LLM])
def test_pre_reserved_model_cannot_start_after_other_usage_becomes_unknown(sf, source, target):
    first = ab.reserve(source, purpose="first", kind="model", session_factory=sf)
    waiting = ab.reserve(target, purpose="waiting", kind="model", session_factory=sf)
    ab.start(first["id"], sf)
    with pytest.raises(ab.BudgetError) as running:
        ab.start(waiting["id"], sf)
    assert running.value.code == "usage_unknown"
    ab.finish(first["id"], state="failed", usage=None, session_factory=sf)
    with pytest.raises(ab.BudgetError) as failed:
        ab.start(waiting["id"], sf)
    assert failed.value.code == "usage_unknown"
    receipt = next(row for row in ab.recent_usage(sf) if row["id"] == waiting["id"])
    assert receipt["state"] == "reserved" and receipt["attempts"] == 0
    assert receipt["started_at"] is None
    assert ab.release(waiting["id"], sf) is True


def test_pre_reserved_triage_starts_after_other_call_finishes_with_known_usage(sf):
    first = ab.reserve(ab.SCOPE_AUTONOMY_LLM, purpose="first", kind="model", session_factory=sf)
    waiting = ab.reserve(ab.SCOPE_ALERT_TRIAGE_LLM, purpose="waiting", kind="model", session_factory=sf)
    ab.start(first["id"], sf)
    ab.finish(
        first["id"], state="succeeded",
        usage={"input_tokens": 3, "output_tokens": 1}, session_factory=sf,
    )
    assert ab.start(waiting["id"], sf)["state"] == "started"


@pytest.mark.parametrize("source", [ab.SCOPE_AUTONOMY_LLM, ab.SCOPE_ALERT_TRIAGE_LLM])
def test_unknown_usage_and_count_caps_reset_on_beijing_budget_date(sf, monkeypatch, source):
    monkeypatch.setattr(ab, "_today", lambda: "2026-10-08")
    monkeypatch.setattr(settings, "alert_triage_daily_llm_budget", 1)
    first = ab.reserve(source, purpose="previous-day", kind="model", session_factory=sf)
    ab.start(first["id"], sf)
    ab.finish(first["id"], state="failed", usage=None, session_factory=sf)
    monkeypatch.setattr(ab, "_today", lambda: "2026-10-09")
    for scope in [ab.SCOPE_AUTONOMY_LLM, ab.SCOPE_ALERT_TRIAGE_LLM]:
        row = ab.reserve(scope, purpose="new-day", kind="model", session_factory=sf)
        assert row["budget_date"] == "2026-10-09" and row["slot"] == 1
    assert ab.budget_status(sf)["token_usage_unknown_calls"] == 0
    assert ab.budget_status(sf, date="2026-10-08")["token_usage_unknown_calls"] == 1


@pytest.mark.parametrize("scope", [ab.SCOPE_AUTONOMY_LLM, ab.SCOPE_ALERT_TRIAGE_LLM])
def test_previous_day_unstarted_model_lease_cannot_bypass_current_unknown_usage(sf, monkeypatch, scope):
    monkeypatch.setattr(ab, "_today", lambda: "2026-10-08")
    old = ab.reserve(scope, purpose="old", kind="model", session_factory=sf)
    monkeypatch.setattr(ab, "_today", lambda: "2026-10-09")
    current = ab.reserve(ab.SCOPE_ALERT_TRIAGE_LLM, purpose="current", kind="model", session_factory=sf)
    ab.start(current["id"], sf)
    with pytest.raises(ab.BudgetError) as expired:
        ab.start(old["id"], sf)
    assert expired.value.code == "reservation_expired"
    old_receipt = next(row for row in ab.recent_usage(sf) if row["id"] == old["id"])
    assert old_receipt["state"] == "reserved" and old_receipt["started_at"] is None
    assert ab.release(old["id"], sf) is True


@pytest.mark.parametrize("state", ["started", "succeeded", "failed"])
def test_started_model_receipt_keeps_start_idempotence_after_beijing_day_changes(sf, monkeypatch, state):
    monkeypatch.setattr(ab, "_today", lambda: "2026-10-08")
    row = ab.reserve(ab.SCOPE_AUTONOMY_LLM, purpose="old", kind="model", session_factory=sf)
    started = ab.start(row["id"], sf)
    if state != "started":
        ab.finish(row["id"], state=state, usage={"input_tokens": 1, "output_tokens": 1}, session_factory=sf)
    monkeypatch.setattr(ab, "_today", lambda: "2026-10-09")
    repeated = ab.start(row["id"], sf)
    assert repeated["state"] == state and repeated["attempts"] == 1
    assert repeated["started_at"] == started["started_at"]
