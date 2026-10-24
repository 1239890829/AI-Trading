"""Owned in-flight calls are distinct from lost usage; no provider is invoked."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import json
import multiprocessing
import os
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import beijing_now
from app.core.config import settings, TRADINGAGENTS_SELECTED_MODEL
from app.models.agent import AgentResourceUsage, AgentTask
from app.models.watchlist import Base
from app.services import agent_budget as budget
from app.services import selected_model_lease as lease
from app.services import tradingagents_research as research


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated.db'}", connect_args={"timeout": 10, "check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    root = tmp_path / "research"
    root.mkdir()
    now = beijing_now()
    identity = {"transport": {"provider": "codex_cli", "model": TRADINGAGENTS_SELECTED_MODEL, "effort": "medium"}}
    artifact = {"version": 1, "mode": "RESEARCH_ONLY_AUTHORIZED", "consumer": "selected_stock_explanation",
                "user_authorization": "EXPLICIT", "net_gain_verified": False, "production_eligible": False,
                "method_identity": identity, "created_at": (now - timedelta(minutes=1)).isoformat(),
                "valid_until": (now + timedelta(days=1)).isoformat()}
    path = root / "authorization.json"
    path.write_text(json.dumps(artifact))
    monkeypatch.setattr(research, "ROOT", root)
    monkeypatch.setattr(lease, "ROOT", None)
    monkeypatch.setattr(settings, "tradingagents_research_enabled", True)
    monkeypatch.setattr(settings, "tradingagents_research_concurrency", 3)
    monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(budget, "get_session_factory", lambda: sf)
    yield sf, root, research._digest(identity)
    engine.dispose()


def reserve(env, symbol="600001"):
    sf, _, _ = env
    task_id = os.urandom(16).hex()
    with sf() as db:
        db.add(AgentTask(id=task_id, type="tradingagents_research", status="running", params=json.dumps({"symbol": symbol})))
        db.commit()
    return budget.reserve(budget.SCOPE_SELECTED_RESEARCH_LLM, purpose="tradingagents.market_analyst", kind="model",
                          provider="codex_cli", model=TRADINGAGENTS_SELECTED_MODEL, task_id=task_id,
                          timeout_seconds=30, session_factory=sf)


def take(env, row):
    sf, _, method = env
    return lease.acquire(row["id"], sf, method_sha256=method)


def test_three_real_owned_starts_overlap_without_rewriting_usage(env):
    sf, _, _ = env
    rows = [reserve(env, str(600001 + i)) for i in range(3)]
    held = [take(env, row) for row in rows]
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda row: budget.start(row["id"], sf), rows))
        assert set(lease.active_usage_ids(sf, budget._today())) == {row["id"] for row in rows}
        # Only selected research can recognize these owned calls, never another consumer.
        budget.ensure_model_usage_known(sf, consumer_scope=budget.SCOPE_SELECTED_RESEARCH_LLM)
        with pytest.raises(budget.BudgetError, match="用量未知"):
            budget.ensure_model_usage_known(sf, consumer_scope=budget.SCOPE_ALERT_TRIAGE_LLM)
        with sf() as db:
            stored = db.query(AgentResourceUsage).all()
            assert all(row.state == "started" and row.usage_known == 0 and row.input_tokens is None for row in stored)
    finally:
        for handle in held:
            handle.close()
    with pytest.raises(budget.BudgetError) as error:
        budget.ensure_model_usage_known(sf, consumer_scope=budget.SCOPE_SELECTED_RESEARCH_LLM)
    assert error.value.code == "usage_unknown"


def test_capacity_slot_is_held_through_finish_and_drain(env, monkeypatch):
    sf, _, _ = env
    monkeypatch.setattr(settings, "tradingagents_research_concurrency", 1)
    first, second = reserve(env), reserve(env, "600002")
    held = take(env, first)
    budget.start(first["id"], sf)
    waiting = threading.Event()
    def waiter():
        waiting.set()
        return take(env, second)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(waiter)
        assert waiting.wait(1)
        time.sleep(0.06)
        assert not pending.done()
        budget.finish(first["id"], state="succeeded", usage={"input_tokens": 2, "output_tokens": 1}, session_factory=sf)
        # A durable completion alone does not release the provider/drain ownership slot.
        time.sleep(0.06)
        assert not pending.done()
        held.close()
        following = pending.result(timeout=2)
        following.close()


@pytest.mark.parametrize("change", ["released", "expired", "inode", "terminal", "task_terminal", "wrong_mode", "symlink", "revoked", "method", "usage_identity", "task_identity"])
def test_lost_or_changed_ownership_never_hides_unknown(env, monkeypatch, change):
    sf, root, _ = env
    row = reserve(env)
    held = take(env, row)
    budget.start(row["id"], sf)
    path = root / "model-inflight" / (row["id"] + ".json")
    assert lease.active_usage_ids(sf, budget._today()) == (row["id"],)
    if change == "released":
        held.close()
    elif change == "expired":
        actual = lease.time.time()
        monkeypatch.setattr(lease.time, "time", lambda: actual + 1000)
    elif change == "inode":
        body = path.read_bytes()
        path.unlink()
        path.write_bytes(body)
        path.chmod(0o600)
    elif change == "terminal":
        budget.finish(row["id"], state="failed", usage=None, session_factory=sf)
    elif change == "task_terminal":
        with sf() as db:
            db.get(AgentTask, row["task_id"]).status = "failed"
            db.commit()
    elif change in {"usage_identity", "task_identity"}:
        with sf() as db:
            if change == "usage_identity":
                db.get(AgentResourceUsage, row["id"]).model = "unexpected-model"
            else:
                db.get(AgentTask, row["task_id"]).params = '{"symbol":"600002"}'
            db.commit()
    elif change == "wrong_mode":
        path.chmod(0o644)
    elif change == "symlink":
        destination = path.with_suffix(".old")
        path.rename(destination)
        path.symlink_to(destination)
    else:
        monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", "0" * 64)
        if change == "method":
            authorization = root / "authorization.json"
            value = json.loads(authorization.read_text())
            value["method_identity"]["changed"] = True
            authorization.write_text(json.dumps(value))
            monkeypatch.setattr(settings, "tradingagents_research_authorization_digest", hashlib.sha256(authorization.read_bytes()).hexdigest())
    try:
        assert lease.active_usage_ids(sf, budget._today()) == ()
        with pytest.raises(budget.BudgetError) as error:
            budget.ensure_model_usage_known(sf, consumer_scope=budget.SCOPE_SELECTED_RESEARCH_LLM)
        assert error.value.code == "usage_unknown"
    finally:
        held.close()


def _child_owner(sf, method, usage_id, connection):
    held = lease.acquire(usage_id, sf, method_sha256=method)
    connection.send(bool(held))
    connection.recv()
    held.close()


def test_os_owner_death_keeps_started_usage_and_blocks_next_call(env):
    sf, _, method = env
    row = reserve(env)
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe()
    process = context.Process(target=_child_owner, args=(sf, method, row["id"], child))
    process.start()
    try:
        assert parent.poll(3) and parent.recv() is True
        budget.start(row["id"], sf)
        assert lease.active_usage_ids(sf, budget._today()) == (row["id"],)
        process.terminate()
        process.join(3)
        assert not process.is_alive()
        assert lease.active_usage_ids(sf, budget._today()) == ()
        with pytest.raises(budget.BudgetError) as error:
            reserve(env, "600002")
        assert error.value.code == "usage_unknown"
        with sf() as db:
            original = db.get(AgentResourceUsage, row["id"])
            assert original.state == "started" and original.input_tokens is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(3)
        parent.close()
        child.close()


def test_missing_owner_method_or_unstarted_reservation_has_no_exemption(env):
    sf, _, _ = env
    row = reserve(env)
    assert lease.acquire(row["id"], sf, method_sha256="0" * 64) is None
    held = take(env, row)
    assert lease.active_usage_ids(sf, budget._today()) == ()
    held.close()
    with sf() as db:
        db.get(AgentTask, row["task_id"]).cancel_requested_at = beijing_now().replace(tzinfo=None)
        db.commit()
    assert take(env, row) is None


class _Recorder:
    def __init__(self, task_id):
        self.task_id = task_id
    def checkpoint(self):
        pass
    async def add_async(self, **kwargs):
        pass


@pytest.mark.parametrize("concurrency", [1, 3])
def test_controlled_twelve_stage_accounted_runner_preserves_all_calls(env, monkeypatch, concurrency):
    """Scheduling measurement only; the delayed runner is not a real model graph."""
    import asyncio
    from pathlib import Path
    from app.services import tradingagents_driver as driver
    from app.services.tradingagents_native.cli_codec import build_cli_schema
    sf, root, method = env
    monkeypatch.setattr(settings, "tradingagents_research_concurrency", concurrency)
    class Delayed:
        def __init__(self):
            self.active, self.peak, self.calls = 0, 0, 0
            self.lock = threading.Lock()
        def method_identity(self):
            return {"provider": "codex_cli", "model": TRADINGAGENTS_SELECTED_MODEL, "effort": "medium"}
        def infer(self, messages, schema, *, mark_launch, timeout, role=None):
            mark_launch()
            with self.lock:
                self.active += 1
                self.calls += 1
                self.peak = max(self.peak, self.active)
            try:
                time.sleep(0.04)
                return {"structured_output": {"content": "controlled runner original", "tool_calls": []},
                        "usage": {"input_tokens": 2, "output_tokens": 1}}
            finally:
                with self.lock:
                    self.active -= 1
    model = Delayed()
    native = driver.NativeTradingAgentsDriver(upstream_dir=root / "unused-upstream", python=Path("/missing"), dependencies=root, transport=model)
    task_ids = [os.urandom(16).hex() for _ in range(3)]
    with sf() as db:
        for index, task_id in enumerate(task_ids):
            db.add(AgentTask(id=task_id, type="tradingagents_research", status="running", params=json.dumps({"symbol": str(600001 + index)})))
        db.commit()
    async def exercise():
        limiter = asyncio.Semaphore(concurrency)
        async def stock(task_id):
            async with limiter:
                rec = _Recorder(task_id)
                for call_id, role in enumerate(research.ROLES, 1):
                    await native._infer_accounted(rec, budget, [{"role": "user", "content": "controlled fixture"}],
                        build_cli_schema(), role, call_id, budget_scope=budget.SCOPE_SELECTED_RESEARCH_LLM, method_sha256=method)
        started = time.perf_counter()
        await asyncio.gather(*(stock(task_id) for task_id in task_ids))
        return time.perf_counter() - started
    elapsed = asyncio.run(exercise())
    assert model.calls == 36 and model.peak == concurrency and model.active == 0
    with sf() as db:
        rows = db.query(AgentResourceUsage).all()
        assert len(rows) == 36 and all(row.state == "succeeded" and row.usage_known == 1 for row in rows)
        assert sum(row.input_tokens for row in rows) == 72 and sum(row.output_tokens for row in rows) == 36
    assert lease.active_usage_ids(sf, budget._today()) == ()
    print(json.dumps({"fixture_only": True, "stocks": 3, "sequential_stages_per_stock": 12,
                      "capacity": concurrency, "physical_fixture_calls": model.calls,
                      "peak_active": model.peak, "elapsed_seconds": round(elapsed, 4)}))


def test_usage_finish_failure_releases_live_proof_but_keeps_unknown_and_original(env, monkeypatch):
    import asyncio
    from pathlib import Path
    from app.services import tradingagents_driver as driver
    from app.services.tradingagents_native.cli_codec import build_cli_schema
    sf, root, method = env
    task_id = os.urandom(16).hex()
    with sf() as db:
        db.add(AgentTask(id=task_id, type="tradingagents_research", status="running", params='{"symbol":"600001"}'))
        db.commit()
    original = []
    class Returned:
        def method_identity(self):
            return {"provider": "codex_cli", "model": TRADINGAGENTS_SELECTED_MODEL, "effort": "medium"}
        def infer(self, messages, schema, *, mark_launch, timeout, role=None):
            mark_launch()
            return {"structured_output": {"content": "saved once", "tool_calls": []}, "usage": {"input_tokens": 2, "output_tokens": 1}}
    native = driver.NativeTradingAgentsDriver(upstream_dir=root, python=Path("/missing"), dependencies=root, transport=Returned())
    def unavailable(*args, **kwargs):
        raise RuntimeError("isolated accounting commit failure")
    monkeypatch.setattr(budget, "finish", unavailable)
    with pytest.raises(RuntimeError, match="accounting commit"):
        asyncio.run(native._infer_accounted(_Recorder(task_id), budget, [{"role": "user", "content": "fixture"}],
            build_cli_schema(), "Market Analyst", 1, capture_output=original.append,
            budget_scope=budget.SCOPE_SELECTED_RESEARCH_LLM, method_sha256=method))
    assert original == [{"content": "saved once", "tool_calls": []}]
    assert lease.active_usage_ids(sf, budget._today()) == ()
    with pytest.raises(budget.BudgetError) as error:
        budget.ensure_model_usage_known(sf, consumer_scope=budget.SCOPE_SELECTED_RESEARCH_LLM)
    assert error.value.code == "usage_unknown"
