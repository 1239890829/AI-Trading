"""Offline audit preserves missing facts and never turns observations into fills."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/audit/paper_funnel_audit.py"
NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
SCHEMA = """
CREATE TABLE daily_pick_set(date TEXT,items TEXT,meta TEXT,created_at TEXT);
CREATE TABLE opportunity_decision_run(run_id TEXT,trade_date TEXT,scenario TEXT);
CREATE TABLE opportunity_decision_snapshot(id INTEGER PRIMARY KEY,snapshot_id TEXT,run_id TEXT,
 trade_date TEXT,as_of TEXT,created_at TEXT,scenario TEXT,stage TEXT,symbol TEXT,decision TEXT,
 strategy_version TEXT,feature_version TEXT,rank INTEGER,data_state TEXT,evidence TEXT);
CREATE TABLE alert_event(id INTEGER PRIMARY KEY,symbol TEXT,triggered_at TEXT,snapshot TEXT);
CREATE TABLE buy_point_consumption(event_key TEXT,event_id INTEGER,consumer TEXT,payload TEXT,
 state TEXT,reason TEXT,attempts INTEGER,expires_at_ms INTEGER,updated_at_ms INTEGER);
CREATE TABLE notification_outbox(id INTEGER,event_id INTEGER,channel TEXT,state TEXT,
 reason TEXT,created_at_ms INTEGER);
CREATE TABLE paper_order(id INTEGER,scope TEXT,side TEXT,status TEXT,created_at TEXT,updated_at TEXT);
CREATE TABLE paper_action_receipt(scope TEXT,request_id TEXT,draft TEXT,result TEXT,created_at TEXT);
CREATE TABLE paper_reset_backup(scope TEXT,restored INTEGER,created_at TEXT);
CREATE TABLE paper_account(cash REAL,private_note TEXT);
CREATE TABLE paper_position(symbol TEXT,quantity INTEGER);
"""


def module():
    spec = importlib.util.spec_from_file_location("paper_funnel_audit_test", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "isolated.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO paper_account VALUES(123456789,'PRIVATE_ACCOUNT_MARKER')")
        db.execute("INSERT INTO paper_position VALUES('PRIVATE_POSITION_MARKER',123)")
    return path


def snapshot(path, *, sid="s1", symbol="600001", day="2026-10-23", clock="10:00:00",
             decision="rejected", stage="notification", evidence=None, created=None, rank=1):
    if evidence is None:
        evidence = {"gate_decision": "rejected", "gate_reason": "置信档不足", "confidence_tier": "observe",
                    "observation_only": True, "buy_range": None, "vetoes": [],
                    "execution_contract": {"decision_id": "D-" + sid, "decision_version": "V-" + sid,
                        "executable_snapshot": {"state": "stale", "age_seconds": 120}}}
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO opportunity_decision_run VALUES(?,?,?)", (sid, day, "buy_point"))
        db.execute("INSERT INTO opportunity_decision_snapshot(snapshot_id,run_id,trade_date,as_of,created_at,"
                   "scenario,stage,symbol,decision,strategy_version,feature_version,rank,data_state,evidence) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (sid, sid, day, day + " " + clock,
                   created or day + " 02:00:01", "buy_point" if stage == "notification" else "intraday_opportunity",
                   stage, symbol, decision, "strategy-current", "feature-current", rank, "ready",
                   json.dumps(evidence, ensure_ascii=False) if not isinstance(evidence, str) else evidence))


def test_repeated_rejections_keep_first_blocker_and_independent_recorded_gaps(database):
    snapshot(database, sid="early", clock="09:31:00", evidence={"gate_decision": "rejected",
        "gate_reason": "快照无现价", "confidence_tier": "observe", "buy_range": None,
        "observation_only": True, "vetoes": []})
    snapshot(database, sid="later", clock="10:00:00")
    result = module().audit(database, now=NOW)
    n = result["notification"]
    assert n["raw_rows"] == 2 and n["distinct_day_symbols"] == 1
    assert n["groups"][0]["first"]["first_blocker"] == "快照无现价"
    assert n["first_blockers"] == {"快照无现价": 1}
    assert n["all_recorded_first_blockers"] == {"快照无现价": 1, "置信档不足": 1}
    assert n["independent_recorded_gaps"]["confidence_not_executable"] == 2
    assert n["independent_recorded_gaps"]["buy_range_missing"] == 2
    assert n["records"][1]["decision_version"] == "V-later"
    assert result["conclusion"]["state"] == "no_recorded_pass"


def test_future_and_broken_rows_are_unknown_never_claimed_test_fixtures(database):
    snapshot(database, sid="future", decision="eligible", created="2026-10-28 10:00:00",
             evidence={"gate_decision": "passed", "execution_contract": {"decision_id": "d", "decision_version": "v"}})
    snapshot(database, sid="broken", evidence="{not-json PRIVATE_PAYLOAD_MARKER")
    result = module().audit(database, now=NOW)
    assert result["notification"]["raw_rows"] == 2
    assert result["notification"]["recorded_passed_rows"] == 0
    assert result["conclusion"]["state"] == "unknown_records_prevent_global_claim"
    assert {row["code"] for row in result["unknowns"]} >= {"created_in_future", "invalid_json"}
    raw = json.dumps(result)
    assert "PRIVATE_PAYLOAD_MARKER" not in raw and "fixture" not in raw
    assert result["status"] == "partial"


def test_daily_generation_is_not_the_first_entry_contract(database):
    with sqlite3.connect(database) as db:
        for day, meta, created in [
            ("2026-10-21", {"generated_at": "2026-10-21T09:26:00+08:00"}, "2026-10-21 01:26:01"),
            ("2026-10-22", {}, "2026-10-22 01:26:01"),
            ("2026-10-23", {"generated_at": "2026-10-23T09:26:00+08:00"}, "2026-10-28 01:26:01"),
        ]:
            db.execute("INSERT INTO daily_pick_set VALUES(?,?,?,?)", (day, '[{"symbol":"600001"}]', json.dumps(meta), created))
    ev = {"selection_entry_contract": "selection-entry-v1", "within_display_capacity": True, "effective_limit": 8}
    snapshot(database, sid="rank1", day="2026-10-21", stage="rank", decision="ranked", evidence=ev)
    snapshot(database, sid="rank2", day="2026-10-21", stage="rank", decision="ranked", evidence=ev)
    snapshot(database, sid="old-contract", day="2026-10-22", stage="rank", decision="ranked", evidence={})
    result = module().audit(database, now=NOW)
    assert result["daily"]["saved_dates"] == 3
    assert result["daily"]["normal_generation_day_symbols"] == 1
    assert result["daily"]["first_entry_states"] == {"unknown": 3}
    assert result["intraday"]["raw_ranked_rows"] == 3
    assert result["intraday"]["first_entry_day_symbols"] == 1
    assert result["intraday"]["contract_ranked_rows"] == 2
    assert result["intraday"]["repeat_contract_ranked_rows"] == 1
    assert result["intraday"]["legacy_or_unqualified_ranked_rows"] == 1
    assert result["intraday"]["without_buy_point_day_symbols"] == 1
    assert result["denominators"]["generation_and_first_entry_union"] == 1


def test_pass_and_consumption_link_by_exact_original_decision_version(database):
    def evidence(sid):
        return {"gate_decision": "passed", "confidence_tier": "executable", "buy_range": {"low": 9, "high": 11},
            "execution_contract": {"decision_id": "D-" + sid, "decision_version": "V-" + sid,
                "executable_snapshot": {"state": "ready", "age_seconds": 0}}}
    snapshot(database, sid="unlinked", evidence=evidence("unlinked"), decision="eligible")
    snapshot(database, sid="linked", symbol="600002", evidence=evidence("linked"), decision="eligible")
    payload = {"alert": {"symbol": "600002", "meta": {"trade_date": "2026-10-23",
        "execution_ref": {"decision_id": "D-linked", "decision_version": "V-linked"}}},
        "execution_result": {"opened": True, "order_id": 8}, "private_context": "PRIVATE_PAYLOAD_MARKER"}
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO buy_point_consumption VALUES('event',7,'position',?,'done','consumed',1,0,0)", (json.dumps(payload),))
        db.execute("INSERT INTO paper_order VALUES(8,'main','buy','filled','2026-10-23 02:01:00','2026-10-23 02:01:00')")
        db.execute("INSERT INTO paper_order VALUES(9,'shadow','buy','filled','2026-10-23 02:01:00','2026-10-23 02:01:00')")
    result = module().audit(database, now=NOW)
    assert result["notification"]["recorded_passed_rows"] == 2
    assert result["conclusion"]["state"] == "passed_action_needs_trace_review"
    traces = {row["snapshot_id"]: row for row in result["notification"]["records"]}
    assert traces["unlinked"]["position_consumer_count"] == 0
    assert traces["linked"]["position_consumer_count"] == 1
    assert traces["linked"]["orders"] == [{"order_id": 8, "scope": "main", "status": "filled"}]
    assert result["orders"]["main_filled_count"] == 1
    assert result["orders"]["scope_states"] == {"main:buy:filled": 1, "shadow:buy:filled": 1}
    assert "PRIVATE_PAYLOAD_MARKER" not in json.dumps(result)


def test_date_window_uses_original_trade_day_and_never_counts_shadow_as_main(database):
    snapshot(database, sid="old", day="2026-10-21")
    snapshot(database, sid="in-window", day="2026-10-23")
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO paper_order VALUES(1,'shadow','buy','filled','2026-10-23 02:00:00','2026-10-23 02:00:00')")
    result = module().audit(database, now=NOW, start_date="2026-10-23", end_date="2026-10-23")
    assert result["notification"]["raw_rows"] == 1
    assert result["notification"]["records"][0]["snapshot_id"] == "in-window"
    assert result["orders"]["main_filled_count"] == 0


@pytest.mark.parametrize("problem", ["missing_table", "unknown_columns", "invalid_daily_json"])
def test_missing_or_unknown_schema_and_corruption_are_explicit(database, problem):
    with sqlite3.connect(database) as db:
        if problem == "missing_table":
            db.execute("DROP TABLE paper_order")
        elif problem == "unknown_columns":
            db.execute("ALTER TABLE paper_order RENAME COLUMN status TO unavailable_status")
        else:
            db.execute("INSERT INTO daily_pick_set VALUES('2026-10-23','broken','{}','2026-10-23 01:26:00')")
    result = module().audit(database, now=NOW)
    assert result["status"] == "partial"
    assert result["unknowns"]
    if problem != "invalid_daily_json":
        assert result["orders"]["main_filled_count"] is None


def test_audit_cannot_read_private_tables_or_write_database(database, monkeypatch):
    audit = module()
    real = sqlite3.connect
    def guarded(connection_uri, *args, **kwargs):
        assert connection_uri.endswith("?mode=ro") and kwargs.get("uri") is True
        connection = real(connection_uri, *args, **kwargs)
        def authorize(action, arg1, *_):
            if action == sqlite3.SQLITE_READ and arg1 in {"paper_account", "paper_position"}:
                pytest.fail("private account/holding table was read")
            if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
                pytest.fail("audit attempted a database write")
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        return connection
    monkeypatch.setattr(audit.sqlite3, "connect", guarded)
    before = database.read_bytes()
    result = audit.audit(database, now=NOW)
    assert database.read_bytes() == before
    assert result["side_effects"] == {"model_calls": 0, "provider_calls": 0, "business_writes": 0}
    assert "PRIVATE_ACCOUNT_MARKER" not in json.dumps(result)


def test_missing_database_is_not_created_and_cli_reports_unknown(tmp_path):
    path, output = tmp_path / "absent.sqlite", tmp_path / "report.json"
    assert module().main(["--database", str(path), "--output", str(output)]) == 2
    assert not path.exists()
    assert json.loads(output.read_text())["status"] == "unknown"


def test_output_refuses_database_or_existing_file_and_bad_dates(database, tmp_path):
    audit = module()
    before = database.read_bytes()
    assert audit.main(["--database", str(database), "--output", str(database)]) == 2
    assert database.read_bytes() == before
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(database) + suffix)
        assert audit.main(["--database", str(database), "--output", str(sidecar)]) == 2
        assert not sidecar.exists()
    output = tmp_path / "existing.json"
    output.write_text("original")
    assert audit.main(["--database", str(database), "--output", str(output)]) == 2
    assert output.read_text() == "original"
    with pytest.raises(ValueError, match="date"):
        audit.audit(database, start_date="2026-02-30")
    with pytest.raises(ValueError, match="date"):
        audit.audit(database, start_date="2026-10-24", end_date="2026-10-23")


def test_optional_plans_only_export_action_identity_not_quantity_or_cash(database, tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "2026-10-23.json").write_text(json.dumps({"date": "2026-10-23", "decisions": [
        {"symbol": "600001", "action": "open_pending", "order_id": 5, "decision_id": "d",
         "decision_version": "v", "qty": 987654321, "cash": "PRIVATE_PLAN_MARKER"}], "exits": []}))
    result = module().audit(database, now=NOW, position_plans=plans)
    assert result["plans"]["action_states"] == {"open_pending": 1}
    assert result["plans"]["records"][0]["order_id"] == 5
    assert "PRIVATE_PLAN_MARKER" not in json.dumps(result)
    assert "987654321" not in json.dumps(result)


def test_successful_cli_writes_only_new_local_artifact(database, tmp_path, capsys):
    output = tmp_path / "artifacts" / "funnel.json"
    before = database.read_bytes()
    assert module().main(["--database", str(database), "--output", str(output),
        "--start-date", "2026-10-23", "--end-date", "2026-10-23"]) == 0
    assert database.read_bytes() == before
    assert json.loads(output.read_text())["window"]["start_date"] == "2026-10-23"
    assert output.stat().st_mode & 0o777 == 0o600
    assert "PRIVATE" not in capsys.readouterr().out


def test_naive_daily_generation_clock_is_not_assumed_to_be_beijing(database):
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO daily_pick_set VALUES(?,?,?,?)", ("2026-10-23", '[{"symbol":"600001"}]',
            '{"generated_at":"2026-10-23T09:26:00"}', "2026-10-23 01:26:01"))
    result = module().audit(database, now=NOW)
    assert result["daily"]["normal_generation_day_symbols"] == 0
    assert result["daily"]["raw_day_symbols"] == 1
    assert result["status"] == "partial"


def test_missing_snapshot_table_cannot_be_presented_as_zero_observations(database):
    with sqlite3.connect(database) as db:
        db.execute("DROP TABLE opportunity_decision_snapshot")
    result = module().audit(database, now=NOW)
    assert result["notification"]["distinct_day_symbols"] is None
    assert result["intraday"]["first_entry_day_symbols"] is None
    assert result["denominators"]["generation_and_first_entry_union"] is None
    assert result["conclusion"]["state"] == "unknown_records_prevent_global_claim"


@pytest.mark.parametrize("evidence", ['{"confidence_tier":["strong"]}',
    '{"gate_decision":"rejected","gate_decision":"passed"}', '{"age":NaN}'])
def test_wrong_or_ambiguous_json_contract_is_unknown_not_a_pass(database, evidence):
    snapshot(database, evidence=evidence)
    result = module().audit(database, now=NOW)
    assert result["status"] == "partial"
    assert result["notification"]["recorded_passed_rows"] == 0
