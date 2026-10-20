"""Behavioral fixtures are not a model-quality admission."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from app.core.bjtime import beijing_now
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
        driver.StoredCompanyPacketBuilder()("600000", beijing_now(), None)


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
    with pytest.raises(ValueError, match="mismatch"):
        transport.decode(events(value), "", 0, '{}')
    for host in ("command_execution", "mcp_tool_call", "web_search"):
        with pytest.raises(TransportError):
            transport.decode(events(value, host=host), "", 0, json.dumps(value))
    with pytest.raises(TransportError, match="auth_or_permission"):
        transport.decode(events(value, status=401), "", 1, json.dumps(value))
    with pytest.raises(ValueError):
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
    first = driver.validate_packet(builder("600000", at + timedelta(seconds=1), None), "600000")
    second = driver.validate_packet(builder("600000", at + timedelta(days=1), None), "600000")
    assert first == second and first["as_of"] == profile["checked_at"]
    assert driver.digest(first) == driver.digest(second)


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
    raw = driver.StoredCompanyPacketBuilder()(symbol, at + timedelta(seconds=1), None)
    value = driver.validate_packet(raw, symbol)
    row = value["facts"]["candidate_news"][0]
    assert row["source_symbols"] == [symbol] and row["document_issuer"]["quote"] == quote
    assert row["document_issuer"]["source_sha256"] == doc["sha256"]
    assert value["facts"]["security"]["market"] is None and "exchange" not in value["identity"]
    assert driver.evidence.build_ledger(value)["documents"][0]["subject"]["eligible"] is True
    assert value["sentiment_adapter"]["has_evidence"] is True
    # Legacy missing page identity is not fabricated into a bound source.
    del profile["documents"][0]["identity_page"]
    unbound = driver.validate_packet(driver.StoredCompanyPacketBuilder()(symbol, at + timedelta(seconds=1), None), symbol)
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
