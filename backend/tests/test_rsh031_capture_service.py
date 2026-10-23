from __future__ import annotations

import fcntl
import json
import signal
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, timedelta

import pytest

from scripts import rsh031_capture_service as s


class Clock:
    def __init__(self, value="2026-10-26T09:54:59+08:00"):
        self.value = s.stamp(value)

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = s.stamp(value)

    def sleep(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def batch(tmp_path, monkeypatch):
    root = tmp_path / "artifacts/batch"
    root.mkdir(parents=True)
    source, calendar = tmp_path / "source.parquet", tmp_path / "calendar.json"
    source.write_bytes(b"frozen-universe")
    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(61)]
    calendar.write_text(json.dumps({"source_url": "https://www.sse.com.cn/verified-calendar",
                                  "days": [str(d) for d in days if d.weekday() < 5]}))
    monkeypatch.setattr(s, "validate_universe", lambda _path: 1)
    manifest = s.freeze(root, source, calendar, s.START, s.UNTIL)
    return root, manifest, source, calendar


def writer(clock, calls, *, code=0, corrupt=False, reason=None):
    def leaf(mode, attempt, universe, timeout, stop, now, pause, root):
        calls.append({"mode": mode, "at": clock().isoformat(), "universe": universe.read_bytes()})
        day = str(clock().date())
        folder = attempt / day
        folder.mkdir()
        receipt = {"mode": mode, "trade_date": day, "probe": False, "source": "free_fixture",
                   "captured_started_at": clock().isoformat()}
        if mode == "morning":
            raw = (json.dumps({"thscode": "000001.SZ", "received_at": clock().isoformat(),
                               "source_at": clock().isoformat()}) + "\n").encode()
            (folder / "morning.jsonl").write_bytes(raw)
            receipt.update(rows_sha256="bad" if corrupt else s.digest(raw),
                           universe_sha256=s.digest(universe.read_bytes()), decision_asof=clock().isoformat(),
                           coverage={"universe": 1, "usable": 0, "complete_for_research": False})
        else:
            pools = [{"pool": k, "trade_date": day, "received_at": clock().isoformat(), "total": 0, "items": []}
                     for k in ["limit-up-pool", "limit-break-pool"]]
            raw = json.dumps(pools).encode()
            (folder / "close-pools.json").write_bytes(raw)
            receipt.update(pools_sha256=s.digest(raw), captured_finished_at=clock().isoformat(),
                           counts={p["pool"]: 0 for p in pools})
        (folder / (mode + ".json")).write_text(json.dumps(receipt))
        return {"returncode": code, "reason": reason, "drained": True}
    return leaf


def read_slot(root, day="2026-10-26", mode="morning"):
    return json.loads((root / "slots" / day / (mode + ".json")).read_text())


def owner(batch, clock=None, **kw):
    root, manifest, _, _ = batch
    clock = clock or Clock("2026-10-26T09:56:00+08:00")
    calls = []
    return s.CaptureService(root, manifest, now=clock, leaf=writer(clock, calls, **kw)), calls, clock


def test_due_clocks_publication_and_repeat_are_read_only_after_completion(batch):
    root = batch[0]
    run, calls, clock = owner(batch, Clock())
    run.tick()
    assert calls == []
    clock.set("2026-10-26T09:55:00+08:00")
    run.tick(); run.tick()
    saved = read_slot(root)
    assert len(calls) == 1 and saved["state"] == "completed"
    assert saved["scheduled_at"] == "2026-10-26T09:55:00+08:00"
    assert saved["validated_result"]["quality"]["complete_for_research"] is False
    assert saved["validated_result"]["source_clock_min"] == clock().isoformat()
    assert "not HTTP" in saved["validated_result"]["hash_scope"]
    clock.set("2026-10-26T15:24:59+08:00"); run.tick()
    assert len(calls) == 1
    clock.set("2026-10-26T15:25:00+08:00"); run.tick()
    assert [c["mode"] for c in calls] == ["morning", "close"]
    assert read_slot(root, mode="close")["state"] == "completed"
    assert (root / "captures/2026-10-26/morning/morning.jsonl").exists()
    assert (root / "captures/2026-10-26/close/close-pools.json").exists()


def test_late_start_keeps_missed_slots_and_never_backfills(batch):
    run, calls, clock = owner(batch, Clock("2026-10-27T10:01:00+08:00"))
    run.tick()
    assert calls == []
    for day, mode in [("2026-10-26", "morning"), ("2026-10-26", "close"), ("2026-10-27", "morning")]:
        assert read_slot(batch[0], day, mode)["state"] == "missing"
    original = (batch[0] / "slots/2026-10-26/morning.json").read_bytes()
    clock.set("2026-10-26T09:56:00+08:00"); run.tick()
    assert calls == [] and (batch[0] / "slots/2026-10-26/morning.json").read_bytes() == original


def test_before_batch_today_weekend_and_expiry_never_call_source(batch):
    run, calls, clock = owner(batch, Clock("2026-10-23T20:30:00+08:00"))
    run.tick()
    assert not (batch[0] / "slots").exists()
    clock.set("2026-10-31T09:56:00+08:00"); run.tick()
    closed = json.loads((batch[0] / "slots/2026-10-31/closed.json").read_text())
    assert closed["state"] == "not_applicable_market_closed" and calls == []
    clock.set("2026-11-20T16:31:00+08:00")
    assert run.run() == 0 and calls == []
    assert json.loads((batch[0] / "terminal.json").read_text())["state"] == "deadline"


def test_started_unknown_is_interrupted_without_rerunning(batch):
    run, calls, _ = owner(batch)
    def crash(*_args):
        raise RuntimeError("simulated owner crash")
    run.leaf = crash
    with pytest.raises(RuntimeError, match="owner crash"):
        run.tick()
    assert read_slot(batch[0])["state"] == "started"
    run, calls, _ = owner(batch)
    run.reconcile(); run.tick()
    assert read_slot(batch[0])["state"] == "interrupted"
    assert read_slot(batch[0])["execution_outcome"] == "unknown" and calls == []


def test_validated_publish_crash_resumes_original_without_leaf(batch, monkeypatch):
    run, calls, _ = owner(batch)
    finish = run.finish_slot
    def crash(*_args):
        raise OSError("publication interrupted")
    monkeypatch.setattr(run, "finish_slot", crash)
    with pytest.raises(OSError, match="publication interrupted"):
        run.tick()
    assert read_slot(batch[0])["state"] == "started"
    assert read_slot(batch[0])["leaf_result"]["returncode"] == 0
    monkeypatch.setattr(run, "finish_slot", finish)
    run.reconcile(); run.tick()
    assert read_slot(batch[0])["state"] == "completed" and len(calls) == 1


def test_corrupted_row_hash_and_failed_leaf_never_publish(batch):
    run, calls, _ = owner(batch, corrupt=True)
    with pytest.raises(ValueError, match="payload hash"):
        run.tick()
    assert not (batch[0] / "captures/2026-10-26/morning").exists()
    assert (batch[0] / "attempts/2026-10-26-morning/2026-10-26/morning.jsonl").exists()
    assert len(calls) == 1


def test_failed_return_is_terminal_not_retried(batch):
    run, calls, _ = owner(batch, code=1)
    with pytest.raises(RuntimeError, match="leaf failed"):
        run.tick()
    run.reconcile(); run.tick()
    assert read_slot(batch[0])["state"] == "failed" and len(calls) == 1


def test_first_write_failure_remains_original_and_does_not_launch(batch, monkeypatch):
    run, calls, _ = owner(batch)
    def refused(*_args, **_kw):
        raise PermissionError("first durable write refused")
    monkeypatch.setattr(s, "write_json", refused)
    with pytest.raises(PermissionError, match="first durable write refused"):
        run.run()
    assert calls == []


def test_stop_is_normal_and_interrupted_return_is_never_published(batch):
    run, calls, _ = owner(batch)
    run.stop = lambda: True
    assert run.run() == 0 and calls == []
    assert json.loads((batch[0] / "terminal.json").read_text())["state"] == "stop_requested"
    run, calls, _ = owner(batch, code=-15, reason="stop_requested")
    run.tick()
    assert read_slot(batch[0])["state"] == "interrupted"
    assert read_slot(batch[0])["leaf_result"]["drained"] is True
    assert not (batch[0] / "captures/2026-10-26/morning").exists()


def test_restart_uses_frozen_bytes_not_replaced_original(batch):
    root, manifest, source, calendar = batch
    source.write_bytes(b"replaced-original")
    assert s.freeze(root, source, calendar, s.START, s.UNTIL) == manifest
    run, calls, _ = owner(batch); run.tick()
    assert calls[0]["universe"] == b"frozen-universe"


def test_interrupted_input_copy_preserves_partial_bytes_but_next_freeze_can_finish(batch, tmp_path, monkeypatch):
    root, _, source, calendar = batch
    fresh = root.parent / "new-batch"
    fresh.mkdir()
    fdopen = s.os.fdopen
    first = True
    @contextmanager
    def interrupted(fd, *args, **kw):
        nonlocal first
        with fdopen(fd, *args, **kw) as stream:
            if not first:
                yield stream
                return
            first = False
            class Partial:
                def write(self, data):
                    stream.write(data[:len(data)//2]); stream.flush()
                    raise OSError("simulated copy interruption")
            yield Partial()
    monkeypatch.setattr(s.os, "fdopen", interrupted)
    with pytest.raises(OSError, match="copy interruption"):
        s.freeze(fresh, source, calendar, s.START, s.UNTIL)
    monkeypatch.setattr(s.os, "fdopen", fdopen)
    partials = {p.relative_to(fresh): p.read_bytes() for p in fresh.rglob("*") if p.is_file()}
    manifest = s.freeze(fresh, source, calendar, s.START, s.UNTIL)
    assert manifest["files_sha256"]["universe.parquet"] == s.digest(source.read_bytes())
    # Partial bytes are retained; they are never published as the fixed input.
    assert partials and all(p.name.endswith(".pending") for p in partials)
    for relative, value in partials.items():
        assert (fresh/"inputs"/relative.name).read_bytes() == value


def test_conflicting_fixed_input_is_never_overwritten(batch):
    root, _, source, calendar = batch
    fresh = root.parent / "conflicting-batch"
    (fresh/"inputs.pending").mkdir(parents=True)
    original = fresh/"inputs.pending/universe.parquet"
    original.write_bytes(b"another complete input")
    with pytest.raises(ValueError, match="differs from input"):
        s.freeze(fresh, source, calendar, s.START, s.UNTIL)
    assert original.read_bytes() == b"another complete input"


def test_manifest_calendar_drift_is_rejected_even_when_input_hashes_match(batch):
    root, manifest, source, calendar = batch
    (root / "inputs/manifest.json").write_text(json.dumps({**manifest, "trading_days": []}))
    with pytest.raises(ValueError, match="calendar|manifest"):
        s.freeze(root, source, calendar, s.START, s.UNTIL)


def test_frozen_input_drift_and_boundaries_refused(batch, tmp_path):
    root, _, source, calendar = batch
    (root / "inputs/universe.parquet").write_bytes(b"drift")
    with pytest.raises(ValueError, match="input changed"):
        s.freeze(root, source, calendar, s.START, s.UNTIL)
    with pytest.raises(ValueError, match="authorized"):
        s.freeze(root, source, calendar, s.START, s.UNTIL + timedelta(days=1))
    (root / "link").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        s.tree_bytes(root)
    with pytest.raises(ValueError, match="outside"):
        s.checked(tmp_path / "outside", root)


def test_completed_artifacts_are_reverified_on_restart(batch):
    run, calls, _ = owner(batch); run.tick()
    (batch[0] / "captures/2026-10-26/morning/morning.jsonl").write_bytes(b"drift")
    with pytest.raises(ValueError, match="hash"):
        run.reconcile()
    assert len(calls) == 1


def test_undrained_return_zero_cannot_publish(batch):
    run, _, _ = owner(batch)
    original = run.leaf
    run.leaf = lambda *args: {**original(*args), "drained": False}
    with pytest.raises(ValueError, match="drain"):
        run.tick()
    assert not (batch[0] / "captures/2026-10-26/morning").exists()


def test_real_parquet_identity_validation(tmp_path):
    import pandas as pd
    path = tmp_path / "u.parquet"
    pd.DataFrame({"Symbol": [f"{i:06}.SZ" for i in range(4000)], "rs_hyname": [None]*4000}).to_parquet(path)
    s.validate_universe(path)
    pd.DataFrame({"Symbol": ["000001.SZ"]*4000, "rs_hyname": [None]*4000}).to_parquet(path)
    with pytest.raises(ValueError, match="universe"):
        s.validate_universe(path)


def test_timeout_signals_then_drains_only_owned_process_and_uses_minimal_environment(tmp_path, monkeypatch):
    class Process:
        pid, returncode = 987654, None
        waited = []
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            self.waited.append(timeout)
            if timeout is not None:
                raise subprocess.TimeoutExpired("fake leaf", timeout)
            self.returncode = -signal.SIGKILL
    proc, killed, launch = Process(), [], []
    monkeypatch.setattr(s.subprocess, "Popen", lambda *a, **kw: launch.append((a, kw)) or proc)
    monkeypatch.setattr(s.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    ticks = iter(range(10)); monkeypatch.setattr(s.time, "monotonic", lambda: next(ticks))
    attempt = tmp_path / "2026-10-26-morning"
    attempt.mkdir()
    result = s.collect("morning", attempt, tmp_path/"u.parquet", .5, lambda: False,
                       Clock("2026-10-26T09:56:00+08:00"), lambda _: None, tmp_path)
    assert result == {"returncode": -signal.SIGKILL, "reason": "leaf_timeout", "drained": True}
    assert killed == [(proc.pid, signal.SIGTERM), (proc.pid, signal.SIGKILL)] and proc.waited == [5, None]
    args, kw = launch[0]
    assert args[0][0] == sys.executable and kw["cwd"] == attempt and kw["start_new_session"] is True
    assert set(kw["env"]) == {"PATH", "PYTHONPATH", "PYTHONNOUSERSITE", "TZ", "LANG"}


def test_lock_blocks_before_source_and_plist_never_installs(batch, monkeypatch, capsys):
    root, _, source, calendar = batch
    monkeypatch.setattr(s, "REPO", root.parent.parent)
    args = ["capture", "run", "--output", str(root), "--universe", str(source), "--calendar", str(calendar)]
    monkeypatch.setattr(sys, "argv", args)
    with (root / "service.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            s.main()
    monkeypatch.setattr(sys, "argv", [args[0], "print-plist", *args[2:]])
    assert s.main() == 0
    import plistlib
    data = plistlib.loads(capsys.readouterr().out.encode())
    assert data["Label"] == "com.ashare.rsh031.capture" and data["RunAtLoad"] is True
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    assert len(data["StartCalendarInterval"]) == 10
    assert {(v["Hour"], v["Minute"]) for v in data["StartCalendarInterval"]} == {(9, 45), (15, 10)}
    assert data["StandardErrorPath"] == str(root / "service.stderr.log")
    assert data["StandardOutPath"] == str(root / "service.stdout.log")
    assert "EnvironmentVariables" not in data and not (root / "terminal.json").exists()


def test_old_atomic_pending_does_not_permanently_block_new_health(batch):
    root = batch[0]
    residue = root / "health.json.pending"
    residue.write_text("prior-crash")
    s.write_json(root / "health.json", {"state": "waiting"}, root)
    assert json.loads((root / "health.json").read_text())["state"] == "waiting"
    assert residue.read_text() == "prior-crash"


def test_once_write_interruption_never_exposes_partial_receipt_and_retry_keeps_residue(batch, monkeypatch):
    root = batch[0]
    path = root / "slots/2026-10-26/morning.json"
    fsync = s.os.fsync
    monkeypatch.setattr(s.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("simulated interrupted write")))
    with pytest.raises(OSError, match="interrupted write"):
        s.write_json(path, {"state": "started"}, root, once=True)
    leftovers = list(path.parent.glob(".morning.json.*.pending"))
    assert len(leftovers) == 1 and not path.exists()
    residue = leftovers[0].read_bytes()
    monkeypatch.setattr(s.os, "fsync", fsync)
    s.write_json(path, {"state": "interrupted"}, root, once=True)
    with pytest.raises(FileExistsError):
        s.write_json(path, {"state": "overwrite refused"}, root, once=True)
    assert json.loads(path.read_text()) == {"state": "interrupted"}
    assert leftovers[0].read_bytes() == residue


@pytest.mark.parametrize("clock,stopped", [("2026-10-31T12:00:00+08:00", True),
                                            ("2026-11-21T12:00:00+08:00", False)])
def test_stop_or_expiry_precedes_any_new_slot_write(batch, clock, stopped):
    run, calls, _ = owner(batch, Clock(clock))
    run.stop = lambda: stopped
    assert run.run() == 0
    assert calls == [] and not (batch[0] / "slots").exists()


def test_capacity_retains_terminal_space_and_exits_normally(batch, monkeypatch):
    run, calls, _ = owner(batch)
    def cannot_start(*_args):
        raise AssertionError("capacity must stop before launching a leaf")
    run.leaf = cannot_start
    monkeypatch.setattr(s, "MAX_BYTES", s.tree_bytes(batch[0]) + 8192)
    assert run.run() == 0 and calls == []
    assert json.loads((batch[0] / "terminal.json").read_text())["state"] == "artifact_capacity"


def test_status_requires_live_lock_and_fresh_health_including_pre_batch_wait(batch):
    root = batch[0]
    now = s.stamp("2026-10-23T21:00:00+08:00")
    s.write_json(root/"health.json", {"state":"waiting_before_batch", "checked_at":now.isoformat(),
                                     "service_pid":999999, "until":s.UNTIL.isoformat()}, root)
    assert s.status(root, now)["observed_state"] == "not_running"
    with (root/"service.lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert s.status(root, now)["observed_state"] == "running_waiting_before_batch"
        assert s.status(root, now+timedelta(minutes=3))["observed_state"] == "locked_health_stale"


def test_orphan_after_first_started_write_refusal_is_preserved_without_replay(batch, monkeypatch):
    run, calls, _ = owner(batch)
    save = s.write_json
    def refuse_started(path, value, *args, **kw):
        if value.get("state") == "started":
            raise PermissionError(13, "fixture denied")
        return save(path, value, *args, **kw)
    monkeypatch.setattr(s, "write_json", refuse_started)
    with pytest.raises(PermissionError):
        run.tick()
    attempt = batch[0] / "attempts/2026-10-26-morning"
    assert attempt.is_dir() and not (batch[0]/"slots/2026-10-26/morning.json").exists()
    (attempt / "orphan-proof.txt").write_text("keep original evidence")
    monkeypatch.setattr(s, "write_json", save)
    restarted, new_calls, _ = owner(batch)
    restarted.reconcile(); restarted.tick(); restarted.reconcile()
    state = read_slot(batch[0])
    assert state["state"] == "interrupted" and state["execution_outcome"] == "unknown"
    assert state["reason"] == "orphan_attempt_without_started"
    assert calls == new_calls == [] and (attempt/"orphan-proof.txt").read_text() == "keep original evidence"


def test_completed_reconciliation_does_not_rewrite_original_completion_clock(batch):
    run, _, clock = owner(batch)
    run.tick()
    path = batch[0]/"slots/2026-10-26/morning.json"
    original = path.read_bytes()
    clock.set("2026-10-27T08:00:00+08:00")
    run.reconcile()
    assert path.read_bytes() == original


def test_publish_completed_write_refusal_reconciles_original_without_new_source(batch, monkeypatch):
    run, calls, _ = owner(batch)
    save = run.save
    def refused(path, value, **kw):
        if value.get("state") == "completed":
            raise PermissionError(13, "completion fixture refusal")
        return save(path, value, **kw)
    monkeypatch.setattr(run, "save", refused)
    with pytest.raises(PermissionError):
        run.tick()
    assert (batch[0]/"captures/2026-10-26/morning/morning.jsonl").exists()
    assert read_slot(batch[0])["state"] == "started"
    monkeypatch.setattr(run, "save", save)
    run.reconcile(); run.tick()
    assert read_slot(batch[0])["state"] == "completed" and len(calls) == 1


def test_only_provider_change_rejects_capture_with_scripts_unchanged(batch, tmp_path, monkeypatch):
    dependency = tmp_path / "repo/backend/app/providers/tencent.py"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("UNIT = 1\n")
    monkeypatch.setattr(s, "REPO", dependency.parents[3])
    manifest = {**batch[1], "code_sha256": s.code_identity()}
    run, calls, _ = owner(batch)
    run.manifest = manifest
    dependency.write_text("UNIT = 100\n")
    with pytest.raises(ValueError, match="code changed"):
        run.tick()
    assert calls == [] and not (batch[0]/"attempts").exists()


def test_collecting_health_allows_real_leaf_deadline_but_not_indefinite_stall(batch):
    root = batch[0]
    now = s.stamp("2026-10-26T09:55:00+08:00")
    s.write_json(root/"health.json", {"state":"collecting", "checked_at":now.isoformat()}, root)
    with (root/"service.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert s.status(root, now+timedelta(seconds=240))["observed_state"] == "running_collecting"
        assert s.status(root, now+timedelta(seconds=301))["observed_state"] == "locked_health_stale"


def test_once_before_batch_has_no_source_and_idle_wait_is_bounded(batch):
    clock = Clock("2026-10-23T20:30:00+08:00")
    run, calls, _ = owner(batch, clock)
    assert run.run(once=True) == 0 and calls == [] and not (batch[0]/"slots").exists()
    health_writes, pauses = [], []
    original_save = run.save
    def save(path, value, **kw):
        if path.name == "health.json":
            health_writes.append(clock())
        original_save(path, value, **kw)
    def pause(seconds):
        pauses.append(seconds)
        clock.sleep(seconds)
        if len(pauses) == 5:
            run.stop = lambda: True
    run.save, run.pause = save, pause
    assert run.run() == 0 and calls == []
    assert pauses == [30]*5
    assert health_writes[1] - health_writes[0] == timedelta(seconds=60)


def test_prelaunch_clock_jump_never_starts_leaf(batch, monkeypatch):
    run, calls, clock = owner(batch)
    identity = s.code_identity
    def jump_after_preflight():
        clock.set("2026-10-26T10:01:00+08:00")
        return identity()
    monkeypatch.setattr(s, "code_identity", jump_after_preflight)
    run.tick()
    assert calls == [] and read_slot(batch[0])["state"] == "missing"
    assert not (batch[0]/"attempts").exists()


def test_collector_rechecks_window_before_process_launch(tmp_path, monkeypatch):
    attempt = tmp_path / "2026-10-26-morning"
    attempt.mkdir()
    monkeypatch.setattr(s.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("late source must not run"))
    result = s.collect("morning", attempt, tmp_path/"u", 240, lambda:False,
                       Clock("2026-10-26T10:00:01+08:00"), lambda _:None, tmp_path)
    assert result["reason"] == "launch_window_missed" and result["drained"] is True
    assert list(attempt.iterdir()) == []


def test_stop_after_started_before_popen_is_interrupted_and_normal_exit(batch, monkeypatch):
    run, _, _ = owner(batch)
    stops = iter([False, False, False, True])
    run.stop, run.leaf = lambda: next(stops), s.collect
    monkeypatch.setattr(s.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("stopped leaf must not launch"))
    assert run.run(once=True) == 0
    state = read_slot(batch[0])
    assert state["state"] == "interrupted" and state["leaf_result"] == {
        "returncode":None, "reason":"stop_requested", "drained":True}
    assert not (batch[0]/"captures").exists()


def test_window_lost_after_started_is_missing_not_source_failure(batch):
    run, calls, _ = owner(batch)
    run.leaf = lambda *_args: {"returncode":None, "reason":"launch_window_missed", "drained":True}
    run.tick()
    state = read_slot(batch[0])
    assert state["state"] == "missing" and state["reason"] == "launch_window_missed_no_backfill"
    assert calls == [] and not (batch[0]/"captures").exists()


def test_expired_cli_does_not_retry_failing_freeze_or_require_original_inputs(batch, monkeypatch):
    class AfterBatch(s.datetime):
        @classmethod
        def now(cls, tz=None):
            return s.stamp("2026-11-21T00:00:00+08:00").astimezone(tz)
    root = batch[0]
    monkeypatch.setattr(s, "datetime", AfterBatch)
    monkeypatch.setattr(s, "REPO", root.parent.parent)
    monkeypatch.setattr(s, "freeze", lambda *_args: pytest.fail("expired batch must not restart freeze"))
    monkeypatch.setattr(sys, "argv", ["capture", "run", "--output", str(root), "--universe", "missing", "--calendar", "missing"])
    assert s.main() == 0


def test_status_cli_uses_live_lock_observation(batch, monkeypatch, capsys):
    root = batch[0]
    s.write_json(root/"health.json", {"state":"waiting", "checked_at":s.datetime.now(s.BJ).isoformat()}, root)
    monkeypatch.setattr(s, "REPO", root.parent.parent)
    monkeypatch.setattr(sys, "argv", ["capture", "status", "--output", str(root)])
    assert s.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["observed_state"] == "not_running" and result["lock_held"] is False
