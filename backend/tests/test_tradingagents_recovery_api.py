"""Recovery HTTP contract through actual app auth, with no recovery execution."""
from types import ModuleType, SimpleNamespace
import sys

from fastapi.testclient import TestClient
import pytest

from app.core import device_access as access
from app.core.config import settings
from app.core.device_policy import WRITE_PATHS, needs_maintenance
from app.services import agent_tasks
from app.services.tradingagents_research import ResearchBlocked


SOURCE_TASK_ID = "a" * 32
RUN_ID = "b" * 32
ORIGIN = "http://recovery-device.test:3000"
ROUTE = f"/api/agent/tasks/{SOURCE_TASK_ID}/recover"
TEMPLATE = "/api/agent/tasks/{source_task_id}/recover"


@pytest.fixture
def api(tmp_path, monkeypatch):
    from app.main import app

    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "device-access.sqlite3")
    monkeypatch.setattr(settings, "api_token", "fixture-recovery-server")
    monkeypatch.setattr(app, "dependency_overrides", {})
    store = access.get_device_store()
    token, device = store.bootstrap(store.bootstrap_code(), "恢复测试设备", ORIGIN)
    calls = []
    task = {"id": "c" * 32, "type": "tradingagents_research", "status": "queued",
            "params": {"symbol": "002264"}}
    state = SimpleNamespace(error=None)

    async def create_recovery(source_task_id, run_id, request_app):
        calls.append((source_task_id, run_id, request_app))
        if state.error is not None:
            raise state.error
        return task

    fake = ModuleType("app.services.tradingagents_recovery")
    fake.create_recovery = create_recovery
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    headers = {"X-API-Token": settings.api_token, access.BROWSER_HEADER: "1",
               access.SESSION_HEADER: token, access.ORIGIN_HEADER: ORIGIN}

    def grant_maintenance():
        store.manage(token, ORIGIN, device["id"], "grant-maintenance")

    # No lifespan: no sources, schedulers, real database or model worker.
    return SimpleNamespace(client=TestClient(app), app=app, headers=headers, calls=calls,
                           task=task, state=state, grant=grant_maintenance)


def test_paired_device_requires_maintenance_before_recovery(api):
    response = api.client.post(ROUTE, json={"run_id": RUN_ID}, headers=api.headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "maintenance_approval_required", "code": "http_403"}
    assert api.calls == []
    assert needs_maintenance("POST", TEMPLATE)
    assert ("POST", "/agent/tasks/{source_task_id}/recover") not in WRITE_PATHS


def test_maintenance_invokes_only_exact_recovery_identity_and_returns_task(api):
    api.grant()
    response = api.client.post(ROUTE, json={"run_id": RUN_ID}, headers=api.headers)
    assert response.status_code == 200
    assert response.json() == {"data": api.task}
    assert api.calls == [(SOURCE_TASK_ID, RUN_ID, api.app)]


@pytest.mark.parametrize("headers", [{}, {"X-API-Token": "wrong"}])
def test_missing_or_invalid_server_token_never_reaches_recovery(api, headers):
    response = api.client.post(ROUTE, json={"run_id": RUN_ID}, headers=headers)
    assert response.status_code == 401
    assert api.calls == []


@pytest.mark.parametrize("extra", [
    {"model": "another-model"}, {"path": "/private/recovery"},
    {"authorization": {"approved": True}}, {"source_task_id": "d" * 32},
    {"symbol": "600276"}, {"method_sha256": "e" * 64},
])
def test_body_rejects_runtime_overrides_before_service(api, extra):
    api.grant()
    response = api.client.post(ROUTE, json={"run_id": RUN_ID, **extra}, headers=api.headers)
    assert response.status_code == 422
    assert api.calls == []


@pytest.mark.parametrize("run_id", [None, 123, True, "B" * 32, "b" * 31, "b" * 33,
                                     "g" * 32, "../" + "b" * 29, "b" * 31 + "\n"])
def test_run_id_requires_exact_lowercase_hex(api, run_id):
    api.grant()
    response = api.client.post(ROUTE, json={"run_id": run_id}, headers=api.headers)
    assert response.status_code == 422
    assert api.calls == []


@pytest.mark.parametrize("body", [{}, None, [], "not-an-object"])
def test_body_requires_one_recovery_object(api, body):
    api.grant()
    response = api.client.post(ROUTE, json=body, headers=api.headers)
    assert response.status_code == 422
    assert api.calls == []


@pytest.mark.parametrize("source_task_id", ["A" * 32, "a" * 31, "a" * 33, "g" * 32])
def test_source_task_id_requires_exact_lowercase_hex(api, source_task_id):
    api.grant()
    response = api.client.post(f"/api/agent/tasks/{source_task_id}/recover",
                               json={"run_id": RUN_ID}, headers=api.headers)
    assert response.status_code == 422
    assert api.calls == []


def test_research_blocked_uses_existing_structured_conflict(api):
    api.grant()
    blockers = [{"code": "usage_unknown", "message": "用量未知，禁止继续"}]
    api.state.error = ResearchBlocked(blockers)
    response = api.client.post(ROUTE, json={"run_id": RUN_ID}, headers=api.headers)
    assert response.status_code == 409
    assert response.json() == {"detail": "用量未知，禁止继续",
                               "code": "tradingagents_blocked", "blockers": blockers}
    assert len(api.calls) == 1


def test_service_validation_uses_unprocessable_response(api):
    api.grant()
    api.state.error = ValueError("invalid recovery identity")
    response = api.client.post(ROUTE, json={"run_id": RUN_ID}, headers=api.headers)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid recovery identity", "code": "http_422"}


def test_queue_failure_retains_existing_task_identity(api):
    api.grant()
    api.state.error = agent_tasks.TaskQueueUnavailable("c" * 32, "unknown")
    response = api.client.post(ROUTE, json={"run_id": RUN_ID}, headers=api.headers)
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "task_queue_unavailable"
    assert body["task_id"] == "c" * 32 and body["persistence"] == "unknown"
    assert len(api.calls) == 1


@pytest.mark.parametrize("error", [RuntimeError("private provider context"),
                                 OSError("/private/source/path")])
def test_unexpected_failures_do_not_expose_runtime_details(api, error):
    api.grant()
    api.state.error = error
    response = api.client.post(ROUTE, json={"run_id": RUN_ID}, headers=api.headers)
    assert response.status_code == 500
    assert response.json() == {"detail": "tradingagents_recovery_unavailable", "code": "http_500"}


@pytest.mark.parametrize("extra", [
    {"run_id": RUN_ID}, {"source_task_id": SOURCE_TASK_ID},
    {"recovery": {"run_id": RUN_ID}},
])
def test_normal_task_creation_cannot_inject_server_recovery_params(api, monkeypatch, extra):
    api.grant()
    created = []

    async def should_not_create(*args, **kwargs):
        created.append((args, kwargs))
        raise AssertionError("invalid params reached task creation")

    monkeypatch.setattr(agent_tasks, "create_task_async", should_not_create)
    response = api.client.post("/api/agent/tasks", headers=api.headers,
        json={"type": "tradingagents_research", "params": {"symbol": "002264", **extra}})
    assert response.status_code == 422
    assert created == [] and api.calls == []
