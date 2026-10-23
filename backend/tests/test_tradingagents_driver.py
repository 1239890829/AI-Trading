"""Behavioral fixtures are not a model-quality admission."""
import asyncio
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest

from app.core.bjtime import beijing_now
from app.schemas.market import Quality
from app.services import tradingagents_driver as driver
from app.services import tradingagents_research as research
from app.services.tradingagents_native import codex_transport as transport
from app.services.tradingagents_native.cli_codec import build_cli_schema
from app.services.tradingagents_native.transport_process import TransportError


class Recorder:
    task_id = "a" * 32
    def __init__(self):
        self.steps, self.canceled = [], False
    def checkpoint(self):
        if self.canceled:
            raise asyncio.CancelledError
    async def checkpoint_async(self):
        self.checkpoint()
    async def add_async(self, **row):
        self.steps.append(row)


class Budget:
    SCOPE_AUTONOMY_LLM = "autonomy_llm"
    def __init__(self):
        self.events, self.unknown = [], False
    def check_model_input(self, chars):
        if chars > 250000:
            raise ValueError("input bound")
    def reserve(self, scope, **row):
        if self.unknown:
            raise RuntimeError("usage_unknown")
        self.events.append(("reserve", row))
        return {"id": "usage-fixture"}
    def start(self, key):
        self.events.append(("start", key))
        self.unknown = True
    def finish(self, key, **row):
        self.events.append(("finish", row))
        self.unknown = not isinstance(row.get("usage"), dict)
    def release(self, key):
        self.events.append(("release", key))


class Typed:
    def __init__(self, *, fail=False, unknown=False, start=True):
        self.fail, self.unknown, self.start, self.calls = fail, unknown, start, 0
    def method_identity(self):
        return {"provider": "fixture", "model": "fixture-fixed", "effort": "medium"}
    def infer(self, messages, schema, *, mark_launch, timeout, role=None):
        if self.start:
            mark_launch()
            self.calls += 1
        if self.fail:
            raise RuntimeError("fixture provider failed")
        return {"structured_output": {"content": "isolated engineering fixture", "tool_calls": []},
                "usage": None if self.unknown else {"input_tokens": 17, "output_tokens": 8},
                "codex_terminal": {"type": "turn.completed", "turn_count": 1}}


def packet():
    at = beijing_now() - timedelta(seconds=1)
    facts = {"symbol": "600000", "candidate_news": [], "task": "仅供工程夹具，不是市场证据"}
    return {"case_id": "fixture", "ticker": "600000", "trade_date": at.date().isoformat(),
            "as_of": at.isoformat(), "facts": facts, "evidence_sha256": driver.digest(facts),
            "identity": {"company_name": "公开夹具公司"}, "market_data": {}, "upstream_sha": research.UPSTREAM_SHA}


def native(tmp_path, typed=None):
    return driver.NativeTradingAgentsDriver(upstream_dir=tmp_path / "upstream", python=Path("/missing/python"),
        dependencies=tmp_path / "deps", transport=typed or Typed())


def test_disabled_consumer_never_collects_or_creates(tmp_path, monkeypatch):
    called = []
    d = driver.NativeTradingAgentsDriver(upstream_dir=tmp_path, python=tmp_path / "missing",
        dependencies=tmp_path, transport=Typed(), packet_builder=lambda *args: called.append(args))
    app = SimpleNamespace(state=SimpleNamespace(tradingagents_research_driver=d))
    def blocked(*args, **kwargs):
        raise research.ResearchBlocked([{"code": "disabled", "message": "fixture disabled"}])
    monkeypatch.setattr(research, "require_start", blocked)
    with pytest.raises(research.ResearchBlocked):
        asyncio.run(d.run_accounted(rec=Recorder(), params={"symbol": "600000"}, app=app,
            budget=Budget(), store_root=tmp_path / "out", required_model_slots=22, verification=research.VERIFICATION))
    assert not called and not (tmp_path / "out").exists()


def test_fixed_transport_rejects_executable_drift_and_unknown_env(tmp_path):
    exe, auth = tmp_path / "codex", tmp_path / "auth"
    exe.write_text("fixture binary"); auth.write_text("fixture reference only")
    d = driver.CodexTypedTransport(exe, auth, "fixture-fixed", "medium", driver.file_sha(exe))
    assert "auth" not in json.dumps(d.method_identity())
    exe.write_text("changed")
    with pytest.raises(driver.DriverUnavailable):
        d.method_identity()
    d = driver.CodexTypedTransport(exe, auth, "fixture-fixed", "medium", driver.file_sha(exe), {"TOKEN": "x"})
    with pytest.raises(driver.DriverUnavailable):
        d.method_identity()


def test_packet_hash_symbol_time_and_private_scope():
    original = packet()
    value = driver.validate_packet(original, "600000")
    assert value["sentiment_adapter"]["has_evidence"] is False
    assert value["facts"] == original["facts"] and "common_scope" not in original
    for mutated in [dict(original, ticker="000001"), dict(original, evidence_sha256="0" * 64),
                    dict(original, as_of="2026-01-01T00:00:00")]:
        with pytest.raises((driver.DriverUnavailable, ValueError)):
            driver.validate_packet(mutated, "600000")
    private = deepcopy(original); private["facts"]["account"] = {"balance": 100}
    private["evidence_sha256"] = driver.digest(private["facts"])
    with pytest.raises(driver.DriverUnavailable, match="private_input"):
        driver.validate_packet(private, "600000")


def test_read_only_builder_missing_profile_does_not_collect(monkeypatch):
    monkeypatch.setattr(driver, "read_profile", lambda symbol: None)
    with pytest.raises(driver.DriverUnavailable, match="public_company_evidence_unavailable"):
        asyncio.run(driver.StoredCompanyPacketBuilder()("600000", beijing_now(), None))


def test_durable_call_lifecycle_and_unknown_not_zero(tmp_path):
    async def run():
        rec, budget = Recorder(), Budget()
        value = await native(tmp_path)._infer_accounted(rec, budget, [{"role": "user", "content": "fixture"}],
                                                       build_cli_schema(), "Bull Researcher", 1)
        assert value["call_id"] == 1
        assert [row[0] for row in budget.events] == ["reserve", "start", "finish"]
        assert budget.events[-1][1]["usage"] == {"input_tokens": 17, "output_tokens": 8}
        assert budget.events[0][1]["max_retries"] == 0 and rec.steps[0]["llm"]["usage_id"]
        unknown = Budget()
        await native(tmp_path, Typed(unknown=True))._infer_accounted(rec, unknown,
            [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1)
        assert unknown.unknown and unknown.events[-1][1]["usage"] is None
        with pytest.raises(RuntimeError, match="usage_unknown"):
            await native(tmp_path)._infer_accounted(rec, unknown,
                [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bear Researcher", 2)
    asyncio.run(run())


def test_started_failure_unknown_and_unstarted_release(tmp_path):
    async def run():
        for started in (True, False):
            budget = Budget()
            with pytest.raises(RuntimeError):
                await native(tmp_path, Typed(fail=True, start=started))._infer_accounted(Recorder(), budget,
                    [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1)
            assert [row[0] for row in budget.events] == (["reserve", "start", "finish"] if started else ["reserve", "release"])
            if started:
                assert budget.unknown and budget.events[-1][1]["state"] == "failed"
    asyncio.run(run())


@pytest.mark.parametrize("verified", [True, False, "untyped"])
def test_failed_transport_consumes_only_verified_final_usage(tmp_path, verified):
    usage = {"input_tokens": 17, "output_tokens": 3}
    class Failed(Typed):
        def infer(self, messages, schema, *, mark_launch, timeout, role=None):
            mark_launch()
            if verified == "untyped":
                error = RuntimeError("untrusted exception usage")
                error.usage = usage
                error.final_usage_verified = True
                raise error
            raise TransportError({"kind": "timeout", "timed_out": True, "usage": usage},
                                 verified_final_usage=usage if verified else None)
    async def run():
        budget = Budget()
        with pytest.raises(RuntimeError):
            await native(tmp_path, Failed())._infer_accounted(Recorder(), budget,
                [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1)
        terminal = budget.events[-1][1]
        assert terminal["state"] == "failed" and terminal["attempts"] == 1
        assert terminal["usage"] == (usage if verified is True else None)
        assert budget.unknown is (verified is not True)
    asyncio.run(run())


def test_input_bound_rejects_before_reserve(tmp_path):
    async def run():
        budget, typed = Budget(), Typed()
        with pytest.raises(ValueError, match="input bound"):
            await native(tmp_path, typed)._infer_accounted(Recorder(), budget,
                [{"role": "user", "content": "x" * 250001}], build_cli_schema(), "Bull Researcher", 1)
        assert not budget.events and typed.calls == 0
    asyncio.run(run())


def test_cancel_drains_actual_thread_before_terminal():
    async def run():
        entered, release, finished = Event(), Event(), Event()
        def work():
            entered.set(); release.wait(5); finished.set()
        pending = asyncio.create_task(driver.drain_thread(work))
        await asyncio.to_thread(entered.wait, 2)
        pending.cancel(); await asyncio.sleep(0.02)
        assert not pending.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert finished.is_set()
    asyncio.run(run())


def test_native_visible_required_peer_identity():
    raw = {"content": "完整同伴条件句，不能改成无条件结论。", "tool_calls": []}
    req = {"case_id": "fixture", "arm": "ta-luna", "role": "Bear Researcher",
           "messages": [{"role": "user", "content": raw["content"]}]}
    peer = {"peer_id": "p1", "role": "Bull Researcher", "call_id": 1, "case_id": "fixture", "arm": "ta-luna",
            "accepted": True, "state": "accepted", "output_sha256": driver.digest(raw), "output": raw}
    context = driver.NativeTradingAgentsDriver._peer_context(req, [peer])
    assert context["required_peer_roles"] == ["Bull Researcher"]
    assert context["accepted_peers"][0]["visible_fields"] == ["/content"]
    assert not driver.NativeTradingAgentsDriver._peer_context(dict(req, messages=[]), [peer])["accepted_peers"]


def test_sandbox_has_exact_socket_and_no_network_or_child_permission(tmp_path):
    value = driver.sandbox_policy(python=tmp_path / "python", dependencies=tmp_path / "venv",
        upstream=tmp_path / "upstream", code=tmp_path / "code", run=tmp_path / "run", socket_path=tmp_path / "ipc.sock")
    assert "(deny default)" in value and "network-outbound" in value
    assert "network*" not in value and "process-fork" not in value
    assert str(tmp_path / "ipc.sock") in value and "/[.]env" in value


def events(value, *, host=None, status=None):
    rows = [{"type": "thread.started"}, {"type": "turn.started"}]
    if host:
        rows.append({"type": "item.completed", "item": {"type": host}})
    if status:
        rows.append({"type": "error", "http_status": status})
    rows += [{"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(value)}},
             {"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 9}}]
    return "\n".join(json.dumps(row) for row in rows)


def test_typed_protocol_does_not_accept_prose_or_host_tools():
    value = {"content": "fixture", "tool_calls": []}
    assert transport.decode(events(value), "", 0, json.dumps(value))["wire_structured_output"] == value
    with pytest.raises(TransportError, match="final_schema_failure"):
        transport.decode(events(value), "", 0, '{}')
    for host in ("command_execution", "mcp_tool_call", "web_search"):
        with pytest.raises(TransportError):
            transport.decode(events(value, host=host), "", 0, json.dumps(value))
    with pytest.raises(TransportError, match="auth_or_permission"):
        transport.decode(events(value, status=401), "", 1, json.dumps(value))
    with pytest.raises(TransportError, match="event_stream_failure"):
        transport.decode('plain result', "", 0, '{}')


def test_upstream_source_changes_or_additions_block(tmp_path, monkeypatch):
    upstream, native_root = tmp_path / "upstream", tmp_path / "native"
    upstream.mkdir(); native_root.mkdir(); (upstream / "a.py").write_text("fixture = 1\n")
    manifest = {"upstream_sha": research.UPSTREAM_SHA, "source_hashes": {"a.py": driver.file_sha(upstream / "a.py")}}
    (native_root / "upstream-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(driver, "NATIVE", native_root)
    d = driver.NativeTradingAgentsDriver(upstream_dir=upstream, python=Path(__file__),
        dependencies=tmp_path, transport=Typed(), sandbox_executable=__file__)
    monkeypatch.setattr(d, "_dependency_identity", lambda: {"fixture_dependency_tested_separately": True})
    assert d.method_identity()["native_full_graph"]
    (upstream / "b.py").write_text("fixture = 2")
    with pytest.raises(driver.DriverUnavailable, match="surface_drift"):
        d.method_identity()
    (upstream / "b.py").unlink(); (upstream / "a.py").write_text("fixture = 3")
    with pytest.raises(driver.DriverUnavailable, match="source_drift"):
        d.method_identity()


@pytest.mark.parametrize("changed_policy", ["tradingagents_research.py", "agent_budget.py"])
def test_actual_method_identity_pins_selected_authorization_and_atomic_budget_policy(tmp_path, monkeypatch, changed_policy):
    upstream, native_root = tmp_path / "upstream", tmp_path / "native"
    upstream.mkdir(); native_root.mkdir(); (upstream / "a.py").write_text("fixture = 1\n")
    manifest = {"upstream_sha": research.UPSTREAM_SHA, "source_hashes": {"a.py": driver.file_sha(upstream / "a.py")}}
    (native_root / "upstream-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(driver, "NATIVE", native_root)
    d = driver.NativeTradingAgentsDriver(upstream_dir=upstream, python=Path(__file__),
        dependencies=tmp_path, transport=Typed(), sandbox_executable=__file__)
    monkeypatch.setattr(d, "_dependency_identity", lambda: {"fixture_dependency_tested_separately": True})
    original = d.method_identity()
    owner_path = Path(driver.__file__).with_name(changed_policy)
    assert original["source_files"][changed_policy] == driver.file_sha(owner_path)
    original_sha = driver.file_sha
    monkeypatch.setattr(driver, "file_sha", lambda path: "e" * 64 if Path(path) == owner_path else original_sha(path))
    assert driver.digest(d.method_identity()) != driver.digest(original)


def test_peer_fail_and_pending_review_are_distinct():
    good = {"peer": {"status": "REQUIRES_PEER_REVIEW"}, "score_basis": {"status": "PASS"}, "source_fields": []}
    bad = deepcopy(good); bad["peer"]["status"] = "FAIL"
    result = driver.NativeTradingAgentsDriver._review_summary([good, bad])
    assert result == {"finite_failures": 1, "peer_review_required": 1,
                      "source_verified": False, "independent_human_gold": False}


def test_completed_role_fallback_is_blocked_before_budget_or_model():
    value = packet()
    request = {"role": "Research Manager", "case_id": value["case_id"], "arm": "ta-luna",
               "evidence_sha256": value["evidence_sha256"], "response_protocol": "codex_cli_output_schema_v1"}
    with pytest.raises(driver.DriverUnavailable, match="completed_role_regeneration_refused"):
        driver.NativeTradingAgentsDriver._guard_request(request, value, [{"role": "Research Manager"}])
    driver.NativeTradingAgentsDriver._guard_request(request, value, [])


def test_identical_complete_reuses_but_partial_unknown_does_not(tmp_path):
    value, identity, run_id = packet(), {"fixture": True}, "b" * 32
    claim = tmp_path / "claim.json"
    claim.write_text(json.dumps({"run_id": run_id, "input_sha256": driver.digest(value), "method_sha256": driver.digest(identity)}))
    with pytest.raises(driver.DriverUnavailable, match="needs_native_recovery"):
        driver.NativeTradingAgentsDriver._reuse_complete(tmp_path, claim, value, identity)
    folder = tmp_path / run_id; folder.mkdir()
    complete = {"state": "completed", "run_id": run_id, "input_sha256": driver.digest(value),
                "method_sha256": driver.digest(identity), "reports": {name: "fixture report" for name in driver.REPORTS}}
    (folder / "result.json").write_text(json.dumps(complete))
    bound = json.loads(claim.read_text()); bound["result_sha256"] = driver.file_sha(folder / "result.json")
    claim.write_text(json.dumps(bound))
    assert driver.NativeTradingAgentsDriver._reuse_complete(tmp_path, claim, value, identity) == complete
    complete["reports"]["market_report"] = ""
    (folder / "result.json").write_text(json.dumps(complete))
    with pytest.raises(driver.DriverUnavailable, match="digest_drift"):
        driver.NativeTradingAgentsDriver._reuse_complete(tmp_path, claim, value, identity)


def test_disabled_bootstrap_does_not_even_read_manifest():
    class Unreadable:
        def __fspath__(self):
            raise AssertionError("disabled must not inspect host paths")
    assert driver.driver_from_server_manifest(Unreadable(), enabled=False) is None


def test_normal_builder_same_sources_reach_same_claim_despite_later_click(monkeypatch):
    at = beijing_now() - timedelta(days=1)
    profile = {"symbol": "600000", "company_name": "公开夹具公司", "checked_at": at.isoformat(),
        "documents": [{"id": "public-fixture", "title": "公开工程文档", "url": "https://example.org/public.pdf",
            "sha256": "a" * 64, "published_at": (at - timedelta(hours=1)).isoformat(),
            "received_at": at.isoformat(), "excerpts": [{"quote": "这是公开工程夹具，不是市场结论。"}]}]}
    monkeypatch.setattr(driver, "read_profile", lambda symbol: deepcopy(profile))
    builder = driver.StoredCompanyPacketBuilder()
    first = driver.validate_packet(asyncio.run(builder("600000", at + timedelta(seconds=1), None)), "600000")
    second = driver.validate_packet(asyncio.run(builder("600000", at + timedelta(days=1), None)), "600000")
    assert driver.source_input_identity(first) == driver.source_input_identity(second) and first["as_of"] == profile["checked_at"]
    assert first["facts"] == second["facts"]


def test_profile_issuer_provenance_reaches_ledger_without_exchange_guess(monkeypatch):
    at = beijing_now() - timedelta(seconds=1)
    symbol, company = "920001", "公开夹具股份有限公司"
    quote = company + "证券代码" + symbol
    doc = {"id": "fixture-public", "title": "年度公开披露", "url": "https://example.org/public.pdf",
           "sha256": "a" * 64, "published_at": (at - timedelta(hours=1)).isoformat(),
           "received_at": at.isoformat(), "identity_quote": quote, "identity_page": 1,
           "excerpts": [{"quote": "公司披露公开经营片段，须保持限定。"}]}
    profile = {"symbol": symbol, "company_name": company, "checked_at": at.isoformat(), "documents": [doc]}
    monkeypatch.setattr(driver, "read_profile", lambda _: deepcopy(profile))
    raw = asyncio.run(driver.StoredCompanyPacketBuilder()(symbol, at + timedelta(seconds=1), None))
    value = driver.validate_packet(raw, symbol)
    row = value["facts"]["candidate_news"][0]
    assert row["source_symbols"] == [symbol] and row["document_issuer"]["quote"] == quote
    assert row["document_issuer"]["source_sha256"] == doc["sha256"]
    assert value["facts"]["security"]["market"] is None and "exchange" not in value["identity"]
    assert driver.evidence.build_ledger(value)["documents"][0]["subject"]["eligible"] is True
    assert value["sentiment_adapter"]["has_evidence"] is True
    # Legacy missing page identity is not fabricated into a bound source.
    del profile["documents"][0]["identity_page"]
    unbound = driver.validate_packet(asyncio.run(driver.StoredCompanyPacketBuilder()(symbol, at + timedelta(seconds=1), None)), symbol)
    assert "document_issuer" not in unbound["facts"]["candidate_news"][0]
    assert unbound["sentiment_adapter"]["has_evidence"] is False


def test_dependency_contents_environment_and_read_cache(tmp_path):
    env = tmp_path / "venv"; (env / "bin").mkdir(parents=True)
    py = env / "bin/python"; py.write_text("offline executable fixture")
    (env / "pyvenv.cfg").write_text("version = fixture")
    packages = env / "lib/python3.11/site-packages"; packages.mkdir(parents=True)
    code = packages / "provider.py"; code.write_text("version = 1")
    manifest = tmp_path / "dependencies.json"
    driver.freeze_dependency_manifest(py, env, manifest)
    d = driver.NativeTradingAgentsDriver(upstream_dir=tmp_path, python=py, dependencies=env,
        transport=Typed(), dependency_manifest=manifest)
    original = d._dependency_identity()
    assert original["python_environment_bound"] and original["files_verified"] == 2
    code.write_text("version = 2")
    assert d._dependency_identity() == original  # read-only cached status
    with pytest.raises(driver.DriverUnavailable, match="dependency_content_drift"):
        d._dependency_identity(force=True)  # every execution checks actual bytes
    with pytest.raises(driver.DriverUnavailable):
        d._dependency_identity()  # a detected failure cannot be hidden by cache


def test_paid_invalid_original_is_saved_before_audit_failure(tmp_path):
    original = {"content": "{", "tool_calls": []}
    req = {"role": "Bull Researcher", "case_id": "fixture", "arm": "ta-luna", "messages": []}
    context = driver.NativeTradingAgentsDriver._peer_context(req, [])
    driver.NativeTradingAgentsDriver._record_output(tmp_path, req, original, 1)
    with pytest.raises(driver.DriverUnavailable, match="terminal_report_unusable"):
        driver.NativeTradingAgentsDriver._audit_output(tmp_path, packet(), req, original, context, 1)
    saved = json.loads((tmp_path / "model-outputs/1.json").read_text())
    assert saved["structured_output"] == original and saved["accepted_at_capture"] is False
    assert saved["output_sha256"] == driver.digest(original)
    with pytest.raises(driver.DriverUnavailable, match="overwrite_refused"):
        driver.NativeTradingAgentsDriver._record_output(tmp_path, req, {"content": "different"}, 1)


def test_completed_checkpoint_cleanup_is_exact_and_not_report_or_memory(tmp_path):
    graph = tmp_path / "graph"; folder = graph / "cache/checkpoints"; folder.mkdir(parents=True)
    for suffix in (".db", ".db-wal", ".db-shm"):
        (folder / ("600000" + suffix)).write_text("completed source-context bytes")
    other = folder / "000001.db"; other.write_text("unrelated pending native recovery")
    report = graph / "final-state.json"; report.write_text("original reports")
    driver.NativeTradingAgentsDriver._remove_completed_checkpoints(graph, "600000")
    assert not list(folder.glob("600000.db*"))
    assert other.read_text() == "unrelated pending native recovery" and report.read_text() == "original reports"


def test_paid_original_precedes_failed_task_step_database_observation(tmp_path):
    class BrokenRecorder(Recorder):
        async def add_async(self, **row):
            raise RuntimeError("fixture task-step DB failure")
    async def run():
        rec, budget, typed = BrokenRecorder(), Budget(), Typed()
        d = native(tmp_path, typed)
        request = {"role": "Bull Researcher"}
        with pytest.raises(RuntimeError, match="task-step DB failure"):
            await d._infer_accounted(rec, budget, [{"role": "user", "content": "fixture"}],
                build_cli_schema(), "Bull Researcher", 1,
                capture_output=lambda value: d._record_output(tmp_path, request, value, 1))
        saved = json.loads((tmp_path / "model-outputs/1.json").read_text())
        assert saved["structured_output"] == {"content": "isolated engineering fixture", "tool_calls": []}
        assert saved["accepted_at_capture"] is False and typed.calls == 1
        assert [event[0] for event in budget.events] == ["reserve", "start", "finish"]
        assert budget.events[-1][1]["usage"] == {"input_tokens": 17, "output_tokens": 8}
    asyncio.run(run())


def test_failed_original_capture_still_finishes_reported_usage(tmp_path):
    def broken_capture(value):
        raise OSError("fixture output disk failure")
    async def run():
        budget, typed = Budget(), Typed()
        with pytest.raises(OSError, match="output disk failure"):
            await native(tmp_path, typed)._infer_accounted(Recorder(), budget,
                [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1,
                capture_output=broken_capture)
        assert typed.calls == 1 and [e[0] for e in budget.events] == ["reserve", "start", "finish"]
        assert budget.events[-1][1]["state"] == "failed"
        assert budget.events[-1][1]["usage"] == {"input_tokens": 17, "output_tokens": 8}
        assert budget.events[-1][1]["output_chars"] > 0 and budget.unknown is False
    asyncio.run(run())


def test_finish_busy_retries_only_the_database_not_the_paid_transport(tmp_path):
    class Contended(Budget):
        def __init__(self):
            super().__init__()
            self.finishes = 0
        def finish(self, key, **row):
            self.finishes += 1
            if self.finishes <= 2:
                raise sqlite3.OperationalError("database is locked")
            return super().finish(key, **row)
    async def run():
        budget, typed = Contended(), Typed()
        result = await native(tmp_path, typed)._infer_accounted(Recorder(), budget,
            [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1)
        assert result["call_id"] == 1 and typed.calls == 1
        assert budget.finishes == 3 and budget.unknown is False
        assert budget.events[-1][1]["state"] == "succeeded"
    asyncio.run(run())


def test_cancel_during_real_transport_drain_preserves_returned_usage(tmp_path):
    entered, release = Event(), Event()
    class Delayed(Typed):
        def infer(self, *args, **kwargs):
            result = super().infer(*args, **kwargs)
            entered.set()
            assert release.wait(5)
            return result
    async def run():
        budget, typed = Budget(), Delayed()
        task = asyncio.create_task(native(tmp_path, typed)._infer_accounted(Recorder(), budget,
            [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert typed.calls == 1 and budget.unknown is False
        assert budget.events[-1][1]["state"] == "failed"
        assert budget.events[-1][1]["usage"] == {"input_tokens": 17, "output_tokens": 8}
    asyncio.run(run())


def test_paid_usage_receipt_precedes_output_and_exhausted_database_writes(tmp_path):
    path = tmp_path / "usage-receipts/1.json"
    class Locked(Budget):
        def __init__(self):
            super().__init__()
            self.finishes = 0
        def finish(self, key, **row):
            self.finishes += 1
            saved = json.loads(path.read_text())
            assert saved["usage_id"] == key and saved["usage"] == row["usage"]
            assert saved["output_sha256"] == driver.digest({"content": "isolated engineering fixture", "tool_calls": []})
            raise sqlite3.OperationalError("database is locked")
    async def run():
        budget, typed = Locked(), Typed()
        originals = []
        def capture(value):
            assert json.loads(path.read_text())["usage_known"] is True
            originals.append(value)
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            await native(tmp_path, typed)._infer_accounted(Recorder(), budget,
                [{"role": "user", "content": "PRIVATE_REQUEST_MUST_NOT_BE_SAVED"}], build_cli_schema(),
                "Bull Researcher", 1, capture_output=capture, usage_receipt_path=path, method_sha256="f" * 64)
        saved = json.loads(path.read_text())
        assert typed.calls == 1 and budget.finishes == 4 and budget.unknown is True
        assert len(originals) == 1 and saved["usage"] == {"input_tokens": 17, "output_tokens": 8}
        assert saved["task_id"] == Recorder.task_id and saved["role"] == "Bull Researcher" and saved["call_id"] == 1
        assert saved["method_sha256"] == "f" * 64 and len(saved["terminal_sha256"]) == 64
        assert saved["terminal_kind"] == "typed_response" and saved["automatic_reconciliation"] is False
        assert "PRIVATE_REQUEST" not in path.read_text() and "isolated engineering fixture" not in path.read_text()
        assert not {"messages", "prompt", "context", "structured_output", "auth_file"} & saved.keys()
        with pytest.raises(driver.DriverUnavailable, match="usage_receipt_identity_or_replay"):
            await native(tmp_path, typed)._infer_accounted(Recorder(), budget,
                [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1,
                usage_receipt_path=path, method_sha256="f" * 64)
        assert typed.calls == 1
    asyncio.run(run())


def test_receipt_survives_output_capture_failure_and_preserves_known_finish(tmp_path):
    path = tmp_path / "usage-receipts/1.json"
    def fail_capture(value):
        assert json.loads(path.read_text())["usage_known"] is True
        raise OSError("original output disk failed")
    async def run():
        typed, budget = Typed(), Budget()
        with pytest.raises(OSError, match="output disk failed"):
            await native(tmp_path, typed)._infer_accounted(Recorder(), budget,
                [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1,
                usage_receipt_path=path, method_sha256="f" * 64, capture_output=fail_capture)
        assert typed.calls == 1 and budget.unknown is False
        assert budget.events[-1][1]["state"] == "failed"
        assert json.loads(path.read_text())["terminal_kind"] == "typed_response"
    asyncio.run(run())


def test_rejected_answer_keeps_verified_terminal_usage_without_replaying_or_succeeding(tmp_path):
    path = tmp_path / "usage-receipts/1.json"
    usage = {"input_tokens": 17, "output_tokens": 3, "cache_write_input_tokens": 0}
    class Rejected(Typed):
        def infer(self, messages, schema, *, mark_launch, **kwargs):
            mark_launch()
            self.calls += 1
            rows = [{"type": "thread.started"}, {"type": "turn.started"},
                    {"type": "item.completed", "item": {"type": "agent_message", "text": "PRIVATE_INVALID_JSON"}},
                    {"type": "turn.completed", "usage": usage}]
            return transport.decode("\n".join(map(json.dumps, rows)), "PRIVATE_STDERR", 0,
                "PRIVATE_INVALID_JSON", verified_cli_version=transport.RECOVERABLE_ERROR_CLI_VERSION)
    async def run():
        typed, budget, originals = Rejected(), Budget(), []
        rec = Recorder()
        with pytest.raises(TransportError, match="final_schema_failure"):
            await native(tmp_path, typed)._infer_accounted(rec, budget,
                [{"role": "user", "content": "PRIVATE_REQUEST"}], build_cli_schema(), "Bull Researcher", 1,
                usage_receipt_path=path, method_sha256="f" * 64, capture_output=originals.append)
        saved = json.loads(path.read_text())
        finishes = [row for kind, row in budget.events if kind == "finish"]
        assert typed.calls == len(finishes) == 1 and finishes[0]["state"] == "failed"
        assert finishes[0]["usage"] == usage and budget.unknown is False
        assert saved["usage_known"] is True and saved["terminal_kind"] == "verified_transport_failure"
        assert saved["usage"] == usage and saved["output_sha256"] is None
        assert not originals and not rec.steps and "PRIVATE_" not in path.read_text()
    asyncio.run(run())


@pytest.mark.parametrize("usage,reason", [
    ({"input_tokens": 152010, "output_tokens": 288, "cached_input_tokens": 75776,
      "cache_write_input_tokens": 0, "reasoning_output_tokens": 77}, None),
    ({"input_tokens": 17, "output_tokens": 3, "cached_input_tokens": 18}, "cache_tokens_exceed_input"),
    ({"input_tokens": 17, "output_tokens": 3, "total_tokens": 20}, "unsupported_usage_field"),
    ({"input_tokens": 17, "output_tokens": True}, "noninteger_token_count"),
])
def test_receipt_keeps_terminal_numeric_evidence_before_database_failure(tmp_path, usage, reason):
    path = tmp_path / "usage-receipts/1.json"
    class Observed(Typed):
        def infer(self, *args, **kwargs):
            return {**super().infer(*args, **kwargs), "usage": usage}
    class Locked(Budget):
        def finish(self, key, **row):
            saved = json.loads(path.read_text())
            observed = saved["usage_observation"]
            assert observed["version"] == 1 and observed["projection_rejection"] == reason
            assert observed["numeric_fields"]["input_tokens"] == usage["input_tokens"]
            assert saved["usage_known"] is (reason is None)
            assert row["usage"] == (usage if reason is None else None)
            raise sqlite3.OperationalError("database is locked")
    async def run():
        typed, budget = Observed(), Locked()
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            await native(tmp_path, typed)._infer_accounted(Recorder(), budget,
                [{"role": "user", "content": "PRIVATE_REQUEST_MUST_NOT_BE_SAVED"}], build_cli_schema(),
                "Fundamentals Analyst", 1, usage_receipt_path=path, method_sha256="f" * 64)
        assert typed.calls == 1 and "PRIVATE_REQUEST" not in path.read_text()
    asyncio.run(run())


def test_generic_terminal_failure_diagnostic_is_durable_before_locked_finish(tmp_path):
    path = tmp_path / "usage-receipts/1.json"
    usage = {"input_tokens": 7, "output_tokens": 9}
    class Rejected(Typed):
        def infer(self, *args, **kwargs):
            super().infer(*args, **kwargs)
            body = events({"content": "PRIVATE_ANSWER", "tool_calls": []}, status=429)
            return transport.decode(body, "PRIVATE_STDERR", 1, None)
    class Locked(Budget):
        def finish(self, key, **row):
            saved = json.loads(path.read_text())
            diagnostic = saved["transport_diagnostic"]
            assert diagnostic["kind"] == "codex_typed_terminal_failure"
            assert diagnostic["exit_code"] == 1 and diagnostic["http_status"] == 429
            assert diagnostic["turn_count"] == 1
            assert saved["usage_observation"]["numeric_fields"] == usage
            assert saved["usage"] is None and not saved["usage_known"] and row["usage"] is None
            assert "PRIVATE_" not in path.read_text()
            raise sqlite3.OperationalError("database is locked")
    async def run():
        typed = Rejected()
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            await native(tmp_path, typed)._infer_accounted(Recorder(), Locked(),
                [{"role": "user", "content": "PRIVATE_REQUEST"}], build_cli_schema(), "Bear Researcher", 1,
                usage_receipt_path=path, method_sha256="f" * 64)
        assert typed.calls == 1
    asyncio.run(run())


@pytest.mark.parametrize("verified", [True, False, "untyped"])
def test_failed_transport_journal_never_promotes_unverified_usage(tmp_path, verified):
    path = tmp_path / "usage-receipts/1.json"
    usage = {"input_tokens": 17, "output_tokens": 3}
    class Failed(Typed):
        def infer(self, messages, schema, *, mark_launch, **kwargs):
            mark_launch()
            self.calls += 1
            if verified == "untyped":
                error = RuntimeError("untrusted usage")
                error.usage, error.final_usage_verified = usage, True
                raise error
            raise TransportError({"kind": "timeout", "usage": usage, "context": "NEVER_SAVE_CONTEXT"},
                                 verified_final_usage=usage if verified else None)
    async def run():
        typed, budget = Failed(), Budget()
        with pytest.raises(RuntimeError):
            await native(tmp_path, typed)._infer_accounted(Recorder(), budget,
                [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1,
                usage_receipt_path=path, method_sha256="f" * 64)
        saved = json.loads(path.read_text())
        assert typed.calls == 1 and saved["usage_known"] is (verified is True)
        assert saved["usage"] == (usage if verified is True else None)
        assert saved["output_sha256"] is None and budget.unknown is (verified is not True)
        assert "NEVER_SAVE_CONTEXT" not in path.read_text()
    asyncio.run(run())


def test_observed_timeout_counts_are_retained_without_promoting_unverified_stream(tmp_path):
    usage = {"input_tokens": 17, "output_tokens": 3, "cache_write_input_tokens": 0}
    class Unverified(Typed):
        def infer(self, *args, **kwargs):
            super().infer(*args, **kwargs)
            raise TransportError({"kind": "timeout", "usage_evidence": "single_final_turn_not_established"},
                                 observed_terminal_usage=usage)
    async def run():
        path = tmp_path / "usage-receipts/1.json"
        typed, budget = Unverified(), Budget()
        with pytest.raises(TransportError, match="timeout"):
            await native(tmp_path, typed)._infer_accounted(Recorder(), budget,
                [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1,
                usage_receipt_path=path, method_sha256="f" * 64)
        saved = json.loads(path.read_text())
        assert saved["usage"] is None and not saved["usage_known"] and budget.unknown
        assert saved["usage_observation"]["numeric_fields"] == usage
        assert saved["usage_observation"]["projection_rejection"] is None
        assert saved["usage_observation"]["terminal_evidence"] == "single_final_turn_not_established"
        assert typed.calls == 1 and budget.events[-1][1]["usage"] is None
    asyncio.run(run())


@pytest.mark.parametrize("transport_failed", [False, True])
def test_cancel_drain_journals_returned_usage_before_failed_finish(tmp_path, transport_failed):
    path = tmp_path / "usage-receipts/1.json"
    entered, release = Event(), Event()
    class Delayed(Typed):
        def infer(self, *args, **kwargs):
            result = super().infer(*args, **kwargs)
            entered.set()
            assert release.wait(5)
            if transport_failed:
                raise TransportError({"kind": "timeout"},
                    verified_final_usage={"input_tokens": 17, "output_tokens": 3})
            return result
    async def run():
        typed, budget, originals = Delayed(), Budget(), []
        task = asyncio.create_task(native(tmp_path, typed)._infer_accounted(Recorder(), budget,
            [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1,
            usage_receipt_path=path, method_sha256="f" * 64, capture_output=originals.append))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done() and not path.exists()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        saved = json.loads(path.read_text())
        assert typed.calls == 1 and len(originals) == (0 if transport_failed else 1)
        assert saved["usage_known"] is True
        assert saved["usage"] == {"input_tokens": 17, "output_tokens": 3 if transport_failed else 8}
        assert budget.unknown is False and budget.events[-1][1]["state"] == "failed"
    asyncio.run(run())


@pytest.mark.parametrize("usage", [None, {"input_tokens": "17", "output_tokens": 8},
                                 {"input_tokens": 17, "output_tokens": True}])
def test_terminal_receipt_keeps_missing_or_invalid_usage_unknown(tmp_path, usage):
    class Uncertain(Typed):
        def infer(self, *args, **kwargs):
            return {**super().infer(*args, **kwargs), "usage": usage}
    async def run():
        budget, typed = Budget(), Uncertain()
        path = tmp_path / "usage-receipts/1.json"
        await native(tmp_path, typed)._infer_accounted(Recorder(), budget,
            [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1,
            usage_receipt_path=path, method_sha256="f" * 64)
        saved = json.loads(path.read_text())
        assert typed.calls == 1 and budget.unknown is True
        assert saved["usage"] is None and saved["usage_known"] is False
        assert budget.events[-1][1]["usage"] is None
    asyncio.run(run())


def test_finish_commit_then_refresh_lock_is_idempotent_without_model_replay(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session, sessionmaker
    from app.models.agent import AgentResourceUsage
    from app.models.watchlist import Base
    from app.services import agent_budget
    faults = []
    class RefreshLocked(Session):
        def refresh(self, instance, *args, **kwargs):
            if isinstance(instance, AgentResourceUsage) and instance.state == "succeeded" and not faults:
                faults.append(instance.id)
                raise OperationalError("redacted finish refresh", {}, sqlite3.OperationalError("database is locked"))
            return super().refresh(instance, *args, **kwargs)
    engine = create_engine(f"sqlite:///{tmp_path / 'isolated-budget.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, class_=RefreshLocked)
    monkeypatch.setattr(agent_budget, "get_session_factory", lambda: sf)
    monkeypatch.setattr(driver.settings, "agent_daily_llm_budget", 8)
    async def run():
        typed = Typed()
        path = tmp_path / "usage-receipts/1.json"
        result = await native(tmp_path, typed)._infer_accounted(Recorder(), agent_budget,
            [{"role": "user", "content": "fixture"}], build_cli_schema(), "Bull Researcher", 1,
            usage_receipt_path=path, method_sha256="f" * 64)
        saved = json.loads(path.read_text())
        with sf() as db:
            rows = db.query(AgentResourceUsage).all()
            assert len(rows) == 1 and rows[0].id == saved["usage_id"] == faults[0]
            assert rows[0].state == "succeeded" and rows[0].usage_known == 1
            assert rows[0].input_tokens == 17 and rows[0].output_tokens == 8
        assert result["call_id"] == 1 and typed.calls == 1
    try:
        asyncio.run(run())
    finally:
        engine.dispose()


def _public_profile(symbol, at):
    return {"symbol": symbol, "company_name": "公开夹具股份有限公司", "checked_at": (at - timedelta(days=2)).isoformat(),
        "documents": [{"id": "fixture-public", "title": "公开披露", "url": "https://example.org/public.pdf",
            "sha256": "a" * 64, "published_at": (at - timedelta(days=3)).isoformat(),
            "received_at": (at - timedelta(days=2)).isoformat(), "excerpts": [{"quote": "仅供离线行为验收的公开片段。"}]}]}


def _market_app(at, count=240, *, source="tencent"):
    from app.schemas.market import Kline, Quote
    class Free:
        name = source
        calls = []
        async def get_kline(self, symbol, timeframe, start, end):
            self.calls.append(("history", symbol, timeframe, start, end))
            return [Kline(symbol=symbol, timeframe="1d", ts=at - timedelta(days=i + 1),
                open=10, high=12, low=9, close=11, volume=20, amount=220,
                source=source, quality="high", received_at=at - timedelta(seconds=1)) for i in range(count)]
    class Financial:
        name = "eastmoney"
        calls = []
        async def get_financials(self, symbol, periods):
            self.calls.append((symbol, periods))
            return [{"symbol": symbol, "source": "eastmoney", "report_date": "2026-06-30",
                "notice_date": "2026-08-20", "revenue": 1e8, "net_profit": 1e7, "eps": 1.2,
                "revenue_yoy": 12.5, "netcash_operate_ps": 0.7}]
    class Forbidden:
        name = "ths"
        def __getattr__(self, name):
            raise AssertionError("THS must never be touched")
    free, financial = Free(), Financial()
    free.calls, financial.calls = [], []
    quote = Quote(symbol="600000", name="公开夹具股份有限公司", market="SH", price=11,
        open=10, high=12, low=9, prev_close=10, volume=2000, amount=22000,
        source="tencent", quality="high", data_timestamp=at - timedelta(seconds=2), received_at=at - timedelta(seconds=1))
    hub = SimpleNamespace(provider=SimpleNamespace(providers=[Forbidden(), free, financial]), stale_after=10,
                          get_quotes=lambda symbols: [quote])
    return SimpleNamespace(state=SimpleNamespace(hub=hub)), free, financial, quote


def test_explicit_builder_uses_cache_and_existing_free_owners_only(monkeypatch):
    at = beijing_now()
    monkeypatch.setattr(driver, "read_profile", lambda _: _public_profile("600000", at))
    app, owner, financial, quote = _market_app(at)
    built = asyncio.run(driver.StoredCompanyPacketBuilder()("600000", at, app))
    assert len(owner.calls) == len(financial.calls) == 1
    assert (owner.calls[0][4] - owner.calls[0][3]).days == 400
    assert len(built["market_data"]["ohlcv"]) == 240
    assert built["facts"]["market_snapshot"]["data"]["ticktime"] == quote.data_timestamp.isoformat()
    assert built["facts"]["security"]["market"] == "SH"
    history = built["facts"]["price_history"]
    assert history["observations"][0]["volume"] == 20
    assert history["source_publication_clock"] is None and history["complete_history"] is False
    summary = built["facts"]["fundamentals"]
    assert summary["periods"][0]["revenue_yoy"] == 12.5
    assert summary["units"]["revenue_yoy"] == "percent" and "完整三张财务报表" in built["facts"]["missing"]
    assert built["facts"]["market_inputs"]["history"]["normalized_payload_bytes"] > 100
    assert driver.validate_packet(built, "600000")["facts"] == built["facts"]


@pytest.mark.parametrize("mutate", [lambda q: setattr(q, "data_timestamp", None),
    lambda q: setattr(q, "quality", Quality.stale), lambda q: setattr(q, "symbol", "000001"),
    lambda q: setattr(q, "data_timestamp", beijing_now() + timedelta(days=1)),
    lambda q: setattr(q, "high", 1), lambda q: setattr(q, "amount", -1)])
def test_snapshot_does_not_replace_bad_source_clock_with_receive_time(mutate):
    at = beijing_now(); app, _, _, quote = _market_app(at)
    mutate(quote)
    value, audit = driver.StoredCompanyPacketBuilder._snapshot(app.state.hub, "600000", at)
    assert value is None and audit["status"] == "unavailable"


def test_history_bounded_basis_units_and_today_not_final():
    at = beijing_now(); builder = driver.StoredCompanyPacketBuilder()
    app, owner, _, _ = _market_app(at, 400, source="eastmoney")
    history, audit = asyncio.run(builder._history(app.state.hub.provider, "600000", at))
    assert len(history["observations"]) == 300 and len(owner.calls) == 1
    assert history["observations"][-1]["session"] < at.date().isoformat()
    assert history["observations"][0]["volume"] == 2000
    assert audit["normalized_response_sha256"] and history["units"]["source_volume_unit"] == "lots"
    original = owner.get_kline
    async def raw(*args, **kwargs):
        rows = await original(*args, **kwargs); rows[0].quality_reasons = ["腾讯未返回前复权序列，使用不复权价格"]
        return rows
    owner.get_kline = raw
    rejected, reason = asyncio.run(builder._history(app.state.hub.provider, "600000", at))
    assert rejected is None and "adjustment_unproven" in reason["reason"]


def test_history_foreign_duplicate_and_current_only_are_unavailable():
    at = beijing_now(); builder = driver.StoredCompanyPacketBuilder()
    for kind in ("foreign", "duplicate", "current"):
        app, owner, _, _ = _market_app(at, 2)
        original = owner.get_kline
        async def bad(*args, _kind=kind, _original=original, **kwargs):
            rows = await _original(*args, **kwargs)
            if _kind == "foreign": rows[0].symbol = "000001"
            elif _kind == "duplicate": rows[1].ts = rows[0].ts
            else:
                for row in rows: row.ts = at
                rows = rows[:1]
            return rows
        owner.get_kline = bad
        value, audit = asyncio.run(builder._history(app.state.hub.provider, "600000", at))
        assert value is None and audit["status"] == "unavailable"


def test_financial_identity_dates_and_unknown_publication_are_preserved():
    at = beijing_now(); app, _, financial, _ = _market_app(at)
    original = financial.get_financials
    async def changed(*args):
        rows = await original(*args)
        return [dict(rows[0], symbol="000001"), dict(rows[0], notice_date="2099-01-01"),
                dict(rows[0], report_date="invalid"), dict(rows[0], notice_date=None)]
    financial.get_financials = changed
    summary, audit = asyncio.run(driver.StoredCompanyPacketBuilder()._financials(app.state.hub.provider, "600000", at))
    assert len(summary["periods"]) == 1 and len(audit["excluded_rows"]) == 3
    row = summary["periods"][0]
    assert row["notice_date_precision"] == "unknown" and row["source_publication_clock"] is None
    assert row["original_intraday_availability_proven"] is False


def test_source_version_ignores_capture_nonce_but_not_market_content(monkeypatch):
    at = beijing_now(); monkeypatch.setattr(driver, "read_profile", lambda _: _public_profile("600000", at))
    app, _, _, _ = _market_app(at)
    first = asyncio.run(driver.StoredCompanyPacketBuilder()("600000", at, app))
    second = deepcopy(first)
    second["as_of"] = (at + timedelta(seconds=3)).isoformat()
    second["facts"]["as_of"] = second["as_of"]
    second["capture_receipt"]["captured_at"] = second["as_of"]
    for audit in second["facts"]["market_inputs"].values():
        audit["received_at"] = second["as_of"]; audit["elapsed_ms"] = 999
        audit["normalized_response_sha256"] = "0" * 64
    for name in ("price_history", "fundamentals"):
        second["facts"][name]["received_at"] = second["as_of"]
        for row in second["facts"][name].get("observations", second["facts"][name].get("periods", [])):
            row["received_at"] = second["as_of"]
    second["facts"]["market_snapshot"]["data"]["received_at"] = second["as_of"]
    assert driver.source_input_identity(first) == driver.source_input_identity(second)
    second["facts"]["market_snapshot"]["data"]["price"] += 0.1
    assert driver.source_input_identity(first) != driver.source_input_identity(second)
    identity = driver.StoredCompanyPacketBuilder().method_identity()
    assert identity["history_calendar_days"] == 400 and identity["history_max_rows"] == 300
    assert "market/normalizer.py" in identity["owner_sources"]


def test_complete_semantic_reuse_preserves_original_asof_and_partial_stays_blocked(tmp_path, monkeypatch):
    at = beijing_now(); monkeypatch.setattr(driver, "read_profile", lambda _: _public_profile("600000", at))
    app, _, _, _ = _market_app(at)
    original = asyncio.run(driver.StoredCompanyPacketBuilder()("600000", at, app))
    later = deepcopy(original); later["as_of"] = (at + timedelta(seconds=3)).isoformat()
    later["facts"]["as_of"] = later["as_of"]
    source = driver.source_input_identity(original); identity = {"fixed": "method"}
    run_id = "b" * 32; folder = tmp_path / run_id; folder.mkdir(); claim = tmp_path / "claim.json"
    binding = {"run_id": run_id, "input_sha256": driver.digest(original),
               "source_input_sha256": source, "method_sha256": driver.digest(identity)}
    claim.write_text(json.dumps(binding))
    with pytest.raises(driver.DriverUnavailable, match="needs_native_recovery"):
        driver.NativeTradingAgentsDriver._reuse_complete(tmp_path, claim, later, identity)
    result = {**binding, "state": "completed", "as_of": original["as_of"],
              "reports": {key: "prior complete original" for key in driver.REPORTS}}
    output = folder / "result.json"; output.write_text(json.dumps(result))
    claim.write_text(json.dumps({**binding, "result_sha256": driver.file_sha(output)}))
    reused = driver.NativeTradingAgentsDriver._reuse_complete(tmp_path, claim, later, identity)
    assert reused["as_of"] == original["as_of"] and reused["as_of"] != later["as_of"]
    # Failed network diagnostics cannot become a new source version just because
    # a transient exception includes a different timestamp or request URL.
    one, two = deepcopy(original), deepcopy(original)
    one["facts"]["market_inputs"]["history"]["reason"] = "network failure t1"
    two["facts"]["market_inputs"]["history"]["reason"] = "network failure t2"
    assert driver.source_input_identity(one) == driver.source_input_identity(two)


def test_news_reads_existing_store_only_at_explicit_capture_and_preserves_raw_binding(monkeypatch):
    at = beijing_now(); monkeypatch.setattr(driver, "read_profile", lambda _: _public_profile("600000", at))
    calls = []; sf = lambda: None
    row = {"record_id": "existing-public-1", "title": "来源原始标题", "summary": "公开来源原始摘要",
        "url": "https://finance.eastmoney.com/public.html", "published_at": (at - timedelta(hours=1)).isoformat(),
        "available_at": (at - timedelta(minutes=20)).isoformat(), "source_symbols": [],
        "normalized_content_sha256": "b" * 64, "source_kind": "collected_public_news",
        "source_ref": {"event_id": 1, "version_id": 2, "observation_id": 3}}
    def read(symbol, *, session_factory, as_of, limit):
        calls.append((symbol, session_factory, as_of, limit))
        return {"candidate_news": [deepcopy(row)], "source_status": "ready", "method": "fixture-readonly",
                "captured_at": at.isoformat(), "missing": ["原文不证明公司关系"]}
    monkeypatch.setattr(driver, "read_news_packet", read)
    app = SimpleNamespace(state=SimpleNamespace(event_store=SimpleNamespace(_sf=sf)))
    builder = driver.StoredCompanyPacketBuilder()
    builder.method_identity()
    assert calls == []
    built = asyncio.run(builder("600000", at, app))
    assert len(calls) == 1 and calls[0][1] is sf and calls[0][3] == 12
    assert built["facts"]["candidate_news"][-1] == row
    assert built["facts"]["candidate_news"][-1]["source_symbols"] == []
    assert built["facts"]["market_inputs"]["news"]["status"] == "ready"
    assert built["facts"]["as_of"] >= row["available_at"]


def test_staged_worker_uses_existing_bjtime_authority(tmp_path):
    files = driver._stage_worker(tmp_path / "worker")
    assert files["bjtime.py"] == driver.file_sha(driver.bjtime.__file__)
    staged = (tmp_path / "worker/worker.py").read_text()
    assert "from bjtime import beijing_now" in staged
    assert "patch.object(graph_module, 'get_current_date', lambda: beijing_now().date().isoformat())" in staged


@pytest.mark.parametrize("amount,available", [(-1, False), (None, True), (0, True)])
def test_optional_history_amount_is_nonnegative_or_unknown(amount, available):
    at = beijing_now(); app, owner, _, _ = _market_app(at, 2, source="eastmoney")
    original = owner.get_kline
    async def rows(*args, **kwargs):
        values = await original(*args, **kwargs)
        values[0].amount = amount
        return values
    owner.get_kline = rows
    history, audit = asyncio.run(driver.StoredCompanyPacketBuilder()._history(app.state.hub.provider, "600000", at))
    assert bool(history) is available
    if available:
        assert history["observations"][-1]["amount"] == amount
    else:
        assert "invalid_history_amount" in audit["reason"]


def _prepared_fixture(source):
    from app.services.tradingagents_native.indicator_table import INDICATORS, build_indicator_table
    value = deepcopy(source)
    table = build_indicator_table(value, {name: None for name in INDICATORS},
                                  {'worker_sha256': driver.file_sha(driver.NATIVE / 'worker.py'), 'fixture': True})
    value['facts']['deterministic_indicators'] = table
    value['indicator_preparation'] = {'source_evidence_sha256': source['evidence_sha256'],
        'source_packet_sha256': driver.digest(source), 'table_sha256': driver.digest(table),
        'worker_sha256': driver.file_sha(driver.NATIVE / 'worker.py'), 'computed_at': source['as_of']}
    value['evidence_sha256'] = driver.digest(value['facts'])
    return driver.validate_packet(value, value['ticker'])


def test_prepared_capture_binds_raw_table_and_formula_without_new_source_nonce(monkeypatch):
    at = beijing_now(); monkeypatch.setattr(driver, 'read_profile', lambda _: _public_profile('600000', at))
    app, _, _, _ = _market_app(at, 2)
    first = _prepared_fixture(asyncio.run(driver.StoredCompanyPacketBuilder()('600000', at, app)))
    second = _prepared_fixture(asyncio.run(driver.StoredCompanyPacketBuilder()('600000', at, app)))
    assert first['as_of'] != second['as_of']
    assert driver.source_input_identity(first) == driver.source_input_identity(second)
    for mutation in ('table', 'raw', 'formula', 'receipt'):
        changed = deepcopy(first)
        if mutation == 'table': changed['facts']['deterministic_indicators']['rows'][0][1] = '100'
        elif mutation == 'raw': changed['market_data']['ohlcv'][0]['Close'] = 100
        elif mutation == 'formula': changed['facts']['deterministic_indicators']['formula']['worker_sha256'] = '0' * 64
        else: changed['indicator_preparation']['source_evidence_sha256'] = '0' * 64
        changed['evidence_sha256'] = driver.digest(changed['facts'])
        with pytest.raises(driver.DriverUnavailable, match='indicator_preparation'):
            driver.validate_packet(changed, '600000')
    changed = deepcopy(first); changed['facts']['price_history']['observations'][0]['close'] += 1
    assert driver.source_input_identity(changed) != driver.source_input_identity(first)


def test_actual_normal_rebuild_reuses_only_complete_original_method_and_sources(tmp_path, monkeypatch):
    at = beijing_now(); monkeypatch.setattr(driver, 'read_profile', lambda _: _public_profile('600000', at))
    app, owner, _, quote = _market_app(at, 2)
    d = native(tmp_path); identity = {'method': 'fixture-formula-v1'}
    monkeypatch.setattr(d, 'method_identity', lambda: deepcopy(identity))
    monkeypatch.setattr(research, 'require_start', lambda *args: {'admission': {'method_sha256': driver.digest(identity)}})
    prepared, executed = [], []
    async def prepare(value, **kwargs):
        prepared.append(deepcopy(value))
        return _prepared_fixture(value)
    async def execute(**kwargs):
        executed.append(kwargs['packet'])
        (kwargs['run'] / 'source-audit.jsonl').write_text('')
        return {key: 'complete engineering fixture' for key in driver.REPORTS}
    monkeypatch.setattr(d, '_prepare_indicators', prepare)
    monkeypatch.setattr(d, '_execute', execute)
    async def run():
        kwargs = dict(rec=Recorder(), params={'symbol': '600000'}, app=app, budget=Budget(),
            store_root=tmp_path / 'out', required_model_slots=22, verification=research.VERIFICATION)
        one = await d.run_accounted(**kwargs)
        two = await d.run_accounted(**kwargs)
        assert two['reused_identical_complete'] and one['run_id'] == two['run_id']
        assert len(prepared) == 2 and len(owner.calls) == 2 and len(executed) == 1
        quote.price += 0.1
        three = await d.run_accounted(**kwargs)
        assert three['run_id'] != one['run_id'] and len(executed) == 2
        identity['method'] = 'fixture-formula-v2'
        four = await d.run_accounted(**kwargs)
        assert four['run_id'] != three['run_id'] and len(executed) == 3
    asyncio.run(run())


@pytest.mark.parametrize('deadline', [False, True])
def test_preparation_repeated_cancel_or_timeout_drains_term_ignoring_child(tmp_path, monkeypatch, deadline):
    async def run():
        d = native(tmp_path); monkeypatch.setattr(d, '_dependency_identity', lambda **kwargs: {})
        entered, finished = asyncio.Event(), asyncio.Event()
        signals, child = [], SimpleNamespace(pid=12345, returncode=None)
        async def communicate():
            entered.set(); await finished.wait(); child.returncode = -9
            return b'', b''
        child.communicate = communicate
        async def spawn(*args, **kwargs): return child
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
        original_wait = asyncio.wait_for
        async def short_wait(awaitable, timeout):
            return await original_wait(awaitable, 0.01 if timeout == 5 or (timeout == 180 and deadline) else timeout)
        monkeypatch.setattr(asyncio, 'wait_for', short_wait)
        def signal_child(pid, sig):
            signals.append(sig)
            if sig == driver.signal.SIGKILL: finished.set()
            elif deadline: raise ProcessLookupError('already exiting signal race')
        monkeypatch.setattr(driver.os, 'killpg', signal_child)
        task = asyncio.create_task(d._prepare_indicators(packet(), rec=Recorder(), folder=tmp_path))
        await entered.wait()
        if not deadline:
            task.cancel(); await asyncio.sleep(0.002); task.cancel()
        with pytest.raises(asyncio.TimeoutError if deadline else asyncio.CancelledError): await task
        assert child.returncode == -9 and finished.is_set()
        assert signals == [driver.signal.SIGTERM, driver.signal.SIGKILL]
        assert not list(tmp_path.glob('indicator-preparation-*'))
    asyncio.run(run())


def test_mfi_scale_is_bound_to_exact_dependency_bytes_and_does_not_rescale(tmp_path, monkeypatch):
    import importlib.util
    stage = tmp_path / 'staged'; driver._stage_worker(stage)
    monkeypatch.syspath_prepend(str(stage))
    spec = importlib.util.spec_from_file_location('mfi_worker_fixture', stage / 'worker.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    actual = 'a61c7a570059558aff2c7efa9894df60e0fa731a6c11e9cd488c310081abed71'
    known = module.mfi_scale_metadata('0.6.9', actual)
    assert known['implementation_scale_verified'] and known['range'] == [0, 1]
    assert known['initial_window_default'] == 0.5
    assert known['scale_equivalent_reference_only'] == {'upper': 0.8, 'lower': 0.2}
    assert not known['values_rescaled'] and not known['threshold_strategy_changed']
    assert 'not an observed neutral' in known['initialization_limit']
    for version, file_sha in [('0.6.9', '0' * 64), ('0.7.0', actual)]:
        unknown = module.mfi_scale_metadata(version, file_sha)
        assert unknown['implementation_scale_verified'] is False and unknown['range'] is None
        assert unknown['scale_equivalent_reference_only'] is None
        assert not unknown['values_rescaled'] and not unknown['threshold_strategy_changed']


def _node_cli(tmp_path):
    entry, node, auth = tmp_path / 'codex.js', tmp_path / 'node', tmp_path / 'auth'
    entry.write_text('#!/usr/bin/env node\n// isolated fixture entry\n')
    node.write_bytes(b'isolated native interpreter fixture'); node.chmod(0o700)
    auth.write_text('isolated existing authentication reference')
    return entry, node, auth


def test_node_interpreter_identity_is_server_pinned_and_native_cli_still_supported(tmp_path):
    entry, node, auth = _node_cli(tmp_path)
    args = dict(executable=entry, auth_file=auth, model='fixture-fixed', effort='medium', executable_sha256=driver.file_sha(entry))
    with pytest.raises(driver.DriverUnavailable, match='interpreter'):
        driver.CodexTypedTransport(**args).method_identity()
    fixed = driver.CodexTypedTransport(**args, node_executable=node, node_executable_sha256=driver.file_sha(node))
    identity = fixed.method_identity()
    assert identity['cli_interpreter'] == {'kind': 'node', 'executable_sha256': driver.file_sha(node)}
    assert str(node) not in json.dumps(identity)
    node.write_bytes(b'changed interpreter bytes')
    with pytest.raises(driver.DriverUnavailable, match='interpreter'): fixed.method_identity()
    entry.write_bytes(b'native executable fixture')
    args['executable_sha256'] = driver.file_sha(entry)
    assert driver.CodexTypedTransport(**args).method_identity()['cli_interpreter']['kind'] == 'native_or_os_executable'


@pytest.mark.parametrize('npm,isolated', [(True, True), (True, False), (False, True)])
def test_exact_interpreter_prefix_keeps_feature_preflight_before_start(tmp_path, monkeypatch, npm, isolated):
    entry, node, auth = _node_cli(tmp_path)
    if not npm: entry.write_bytes(b'native executable fixture')
    calls, marks = [], []
    def preflight(args, **kwargs):
        calls.append((args, kwargs))
        expected = [str(node.resolve()), str(entry.resolve())] if npm else [str(entry.resolve())]
        assert args[:len(expected)] == expected
        assert kwargs['env']['PATH'].split(':')[0] == str((node if npm else entry).resolve().parent)
        if len(calls) == 1:
            assert args[len(expected):] == ['--version'] and not marks
            return SimpleNamespace(returncode=0, stdout='codex-cli 0.162.1', stderr='')
        assert args[len(expected):len(expected)+2] == ['features', 'list'] and not marks
        assert all(args[args.index('--disable', i)+1] in transport.DISABLE for i in [args.index('--disable')])
        output = '\n'.join(flag + ' stable ' + ('true' if not isolated and index == 0 else 'false') for index, flag in enumerate(transport.DISABLE))
        return SimpleNamespace(returncode=0, stdout=output, stderr='')
    class BeforeModel(Exception): pass
    def mark(): marks.append(True); raise BeforeModel
    monkeypatch.setattr(transport.subprocess, 'run', preflight)
    monkeypatch.setattr(transport, 'launch', lambda *a, **kw: pytest.fail('No model process is allowed'))
    with pytest.raises(BeforeModel if isolated else ValueError):
        transport.codex_infer([{'role': 'user', 'content': 'unused synthetic'}], build_cli_schema(),
            model='fixture-fixed', effort='medium', mark_launch=mark, executable=entry, auth_file=auth,
            node_executable=node if npm else None, node_executable_sha256=driver.file_sha(node) if npm else None)
    assert len(calls) == 2 and len(marks) == int(isolated)


def test_server_manifest_preserves_explicit_interpreter_identity(tmp_path, monkeypatch):
    entry, node, auth = _node_cli(tmp_path)
    manifest = {'version': 1, 'upstream_sha': research.UPSTREAM_SHA,
        **{key: str(tmp_path / key) for key in ('upstream_dir', 'python', 'dependencies', 'dependency_manifest')},
        'transport': {'executable': str(entry), 'auth_file': str(auth), 'model': 'fixture-fixed', 'effort': 'medium',
            'executable_sha256': driver.file_sha(entry), 'node_executable': str(node), 'node_executable_sha256': driver.file_sha(node)}}
    path = tmp_path / 'server-method.json'; path.write_text(json.dumps(manifest))
    monkeypatch.setattr(driver.NativeTradingAgentsDriver, 'method_identity', lambda self: self.transport.method_identity())
    result = driver.driver_from_server_manifest(path, enabled=True)
    assert result.transport.node_executable == node
    assert result.method_identity()['cli_interpreter']['executable_sha256'] == driver.file_sha(node)
    manifest['transport']['node_executable'] = 'relative-node'; path.write_text(json.dumps(manifest))
    with pytest.raises(driver.DriverUnavailable, match='path_invalid'):
        driver.driver_from_server_manifest(path, enabled=True)


@pytest.mark.parametrize("mutation", ["close_from_average", "wrong_day", "wrong_indicator", "missing_projection"])
def test_named_latest_projection_cannot_be_relabelled_even_with_rebound_outer_hashes(mutation):
    source = packet()
    day = (date.fromisoformat(source['trade_date']) - timedelta(days=1)).isoformat()
    source['facts']['price_history'] = {'observations': [
        {'session': day, 'open': 35.70, 'high': 37.68, 'low': 34.50, 'close': 36.30, 'volume': 15333100.0}]}
    source['market_data'] = {'ohlcv': [
        {'Date': day, 'Open': 35.70, 'High': 37.68, 'Low': 34.50, 'Close': 36.30, 'Volume': 15333100.0}]}
    source['evidence_sha256'] = driver.digest(source['facts'])
    valid = _prepared_fixture(source)
    changed = deepcopy(valid)
    table = changed['facts']['deterministic_indicators']
    latest = table['latest_session_projection']
    if mutation == 'close_from_average':
        latest['frozen_history_close'] = 37.6382  #Observed historical close/MA confusion.
    elif mutation == 'wrong_day':
        latest['session'] = source['trade_date']  #A later capture day is not the history session.
    elif mutation == 'wrong_indicator':
        latest['indicators']['close_50_sma'] = '37.8326'  #Never borrow an earlier-day MA.
    else:
        table.pop('latest_session_projection')
    changed['indicator_preparation']['table_sha256'] = driver.digest(table)
    changed['evidence_sha256'] = driver.digest(changed['facts'])
    with pytest.raises(driver.DriverUnavailable, match='indicator_preparation'):
        driver.validate_packet(changed, '600000')


def test_no_history_projection_cannot_invent_a_close():
    valid = _prepared_fixture(packet())
    table = valid['facts']['deterministic_indicators']
    assert table['latest_session_projection']['frozen_history_close'] is None
    table['latest_session_projection']['frozen_history_close'] = 0
    valid['indicator_preparation']['table_sha256'] = driver.digest(table)
    valid['evidence_sha256'] = driver.digest(valid['facts'])
    with pytest.raises(driver.DriverUnavailable, match='indicator_preparation'):
        driver.validate_packet(valid, '600000')
