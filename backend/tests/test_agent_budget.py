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


def test_model_policy_bounds_timeout_and_retry(sf):
    with pytest.raises(ab.BudgetError) as timeout:
        ab.reserve(
            ab.SCOPE_AUTONOMY_LLM, purpose="slow", kind="model",
            timeout_seconds=181, session_factory=sf,
        )
    assert timeout.value.code == "timeout_budget_exceeded"
    with pytest.raises(ab.BudgetError) as retry:
        ab.reserve(
            ab.SCOPE_AUTONOMY_LLM, purpose="retry", kind="model",
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


def test_input_budget_blocks_before_reservation(sf, monkeypatch):
    monkeypatch.setattr(settings, "agent_model_max_input_chars", 10)
    with pytest.raises(ab.BudgetError) as err:
        ab.reserve(
            ab.SCOPE_AUTONOMY_LLM, purpose="too-large", kind="model",
            input_chars=11, timeout_seconds=30, session_factory=sf,
        )
    assert err.value.code == "input_budget_exceeded"
    assert ab.budget_status(sf)["llm_used"] == 0


def test_output_budget_records_failed_and_refuses_success(sf, monkeypatch):
    monkeypatch.setattr(settings, "agent_model_max_output_chars", 5)
    row = ab.reserve(
        ab.SCOPE_AUTONOMY_LLM, purpose="oversized-output", kind="model",
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
