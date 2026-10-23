"""Bounded RSH-031 capture, independent of an interactive model conversation.

Run or print a user LaunchAgent plist; installation is an explicit operator step.
The calendar is an operator-verified input, not verified by its URL alone. Leaf
hashes cover normalized saved rows/pools, never the original HTTP response.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BJ = ZoneInfo("Asia/Shanghai")
REPO = Path(__file__).resolve().parents[2]
LEAF = Path(__file__).with_name("rsh031_forward_capture.py")
START = date(2026, 10, 26)
UNTIL = datetime(2026, 11, 20, 16, 30, tzinfo=BJ)
MAX_BYTES = 512 * 1024 * 1024
CONTROL_RESERVE = 64 * 1024
SLOTS = {"morning": (clock_time(9, 55), clock_time(10)),
         "close": (clock_time(15, 25), clock_time(16, 30))}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_identity() -> dict:
    owner = REPO / "backend/app"
    files = sorted(owner.rglob("*.py"))
    if not files or any(p.is_symlink() or not p.is_file() for p in files):
        raise ValueError("capture dependency tree invalid")
    rows = [(str(p.relative_to(owner)), digest(p.read_bytes())) for p in files]
    return {"service": digest(Path(__file__).read_bytes()), "leaf": digest(LEAF.read_bytes()),
            "backend_app_python_tree": digest(json.dumps(rows).encode())}


def stamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("aware clock required")
    return result.astimezone(BJ)


def checked(path: Path, root: Path) -> Path:
    path = Path(os.path.abspath(path))
    if path != root and root not in path.parents:
        raise ValueError("path outside bound output")
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError("symlink refused")
        if part == root:
            break
    return path


def tree_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        checked(path, root)
        mode = path.stat().st_mode
        if stat.S_ISREG(mode):
            total += path.stat().st_size
        elif not stat.S_ISDIR(mode):
            raise ValueError("nonregular artifact refused")
    return total


def write_bytes(path: Path, data: bytes, root: Path, *, once=False) -> None:
    checked(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if tree_bytes(root) + len(data) > MAX_BYTES:
        raise ValueError("artifact capacity reached")
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".pending", dir=path.parent)
    temporary = checked(Path(name), root)
    with os.fdopen(fd, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())
    if once:
        os.link(temporary, path, follow_symlinks=False)  # Atomic publish, refuse an existing receipt.
        temporary.unlink()
    else:
        os.replace(temporary, path)
    sync_directory(path.parent)


def write_json(path: Path, value: dict, root: Path, *, once=False) -> None:
    write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode(), root, once=once)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_universe(path: Path) -> int:
    # Same frozen SH/SZ identity contract as forward_capture.universe, no app imports.
    import pandas as pd
    frame = pd.read_parquet(path, columns=["Symbol", "rs_hyname"])
    codes = frame.Symbol.astype(str)
    codes = codes[codes.str.fullmatch(r"[0-9]{6}\.(?:SH|SZ)")]
    if len(codes) < 4000 or codes.duplicated().any():
        raise ValueError("invalid frozen SH/SZ universe")
    return len(codes)


def freeze(root: Path, universe: Path, calendar: Path, start: date, until: datetime) -> dict:
    if not START <= start <= until.date() <= UNTIL.date() or until > UNTIL or (until.date() - start).days > 30:
        raise ValueError("batch outside authorized dates")
    binding = {"start": str(start), "until": until.isoformat(),
               "input_path_hashes": {"universe": digest(str(universe.absolute()).encode()),
                                     "calendar": digest(str(calendar.absolute()).encode())},
               "code_sha256": code_identity()}
    inputs = root / "inputs"
    if inputs.exists():
        checked(inputs, root)
        result = json.loads((inputs / "manifest.json").read_text())
        if any(result.get(key) != value for key, value in binding.items()):
            raise ValueError("frozen batch binding changed")
        for name, sha in result["files_sha256"].items():
            if digest(checked(inputs / name, root).read_bytes()) != sha:
                raise ValueError("frozen input changed")
        cal = json.loads((inputs / "calendar.json").read_text())
        if result["trading_days"] != [day for day in cal["days"] if str(start) <= day <= str(until.date())]:
            raise ValueError("frozen calendar manifest changed")
        return result
    pending = checked(root / "inputs.pending", root)
    pending.mkdir(exist_ok=True)
    payloads = {}
    for name, source in [("universe.parquet", universe), ("calendar.json", calendar)]:
        if source.is_symlink() or not source.is_file():
            raise ValueError("input must be a readable regular file")
        if source.stat().st_size > MAX_BYTES - CONTROL_RESERVE - tree_bytes(root):
            raise ValueError("artifact capacity reached")
        data = source.read_bytes()
        target = checked(pending / name, root)
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("incomplete freeze differs from input")
        else:
            write_bytes(target, data, root, once=True)
        payloads[name] = data
    universe_count = validate_universe(pending / "universe.parquet")
    cal = json.loads(payloads["calendar.json"])
    days = [date.fromisoformat(day) for day in cal["days"]]
    if (not str(cal.get("source_url", "")).startswith("https://www.sse.com.cn/")
            or not days or days != sorted(set(days)) or days[0] > start or days[-1] < until.date()
            or any(day.weekday() >= 5 for day in days)):
        raise ValueError("calendar coverage or identity invalid")
    result = {**binding, "files_sha256": {name: digest(value) for name, value in payloads.items()},
              "trading_days": [str(day) for day in days if start <= day <= until.date()], "universe_count": universe_count,
              "code_scope": "service, leaf and bounded backend/app Python source tree",
              "hash_scope": "frozen input bytes and normalized leaf artifacts; not HTTP response bytes"}
    manifest = pending / "manifest.json"
    if manifest.exists():
        if json.loads(manifest.read_text()) != result:
            raise ValueError("pending manifest differs")
    else:
        write_json(manifest, result, root, once=True)
    os.rename(pending, inputs)
    sync_directory(root)
    return result


def verify_capture(folder: Path, mode: str, day: str, manifest: dict, root: Path) -> dict:
    names = [mode + ".json", "morning.jsonl" if mode == "morning" else "close-pools.json"]
    if sorted(path.name for path in folder.iterdir()) != sorted(names):
        raise ValueError("unexpected capture file set")
    files = {name: checked(folder / name, root).read_bytes() for name in names}
    receipt = json.loads(files[mode + ".json"])
    if receipt.get("mode") != mode or receipt.get("trade_date") != day or receipt.get("probe") is not False:
        raise ValueError("leaf receipt binding invalid")
    begin, end = [datetime.combine(date.fromisoformat(day), t, BJ) for t in SLOTS[mode]]
    if not begin <= stamp(receipt["captured_started_at"]) <= end:
        raise ValueError("leaf source start outside window")
    raw = files[names[1]]
    field = "rows_sha256" if mode == "morning" else "pools_sha256"
    if digest(raw) != receipt.get(field):
        raise ValueError("leaf saved payload hash mismatch")
    source_clocks, received_clocks = [], []
    if mode == "morning":
        if receipt.get("universe_sha256") != manifest["files_sha256"]["universe.parquet"]:
            raise ValueError("leaf used another universe")
        if not begin <= stamp(receipt["decision_asof"]) <= end:
            raise ValueError("leaf decision outside window")
        rows = [json.loads(line) for line in raw.splitlines()]
        if len(rows) != manifest["universe_count"] or len(rows) != receipt["coverage"]["universe"] or len({row["thscode"] for row in rows}) != len(rows):
            raise ValueError("leaf row identity invalid")
        for row in rows:
            received_clocks.append(stamp(row["received_at"]).isoformat())
            if row.get("source_at"):
                source_clocks.append(stamp(row["source_at"]).isoformat())
        quality = receipt["coverage"]
    else:
        pools = json.loads(raw)
        if not begin <= stamp(receipt["captured_finished_at"]) <= end:
            raise ValueError("leaf finish outside window")
        if (len(pools) != 2 or {p["pool"] for p in pools} != {"limit-up-pool", "limit-break-pool"}
                or any(p["trade_date"] != day or p["total"] != len(p["items"])
                       or receipt["counts"].get(p["pool"]) != p["total"] for p in pools)):
            raise ValueError("leaf pool identity invalid")
        symbols = [row["symbol"] for pool in pools for row in pool["items"]]
        if len(symbols) != len(set(symbols)) or any(not re.fullmatch(r"\d{6}", code) for code in symbols):
            raise ValueError("leaf pools overlap or have invalid symbols")
        received_clocks = [stamp(pool["received_at"]).isoformat() for pool in pools]
        quality = {"counts": receipt["counts"], "scope": "close source facts only"}
    return {"files_sha256": {name: digest(value) for name, value in files.items()},
            "source_clock_min": min(source_clocks, default=None), "source_clock_max": max(source_clocks, default=None),
            "received_clock_min": min(received_clocks, default=None), "received_clock_max": max(received_clocks, default=None),
            "source": receipt["source"], "quality": quality, "hash_scope": manifest["hash_scope"]}


def drain(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    else:
        process.wait()


def collect(mode, attempt, universe, timeout, stop, now, pause, root):
    env = {"PATH": os.defpath, "PYTHONPATH": str(REPO / "backend"),
           "PYTHONNOUSERSITE": "1", "TZ": "Asia/Shanghai", "LANG": "C.UTF-8"}
    day = date.fromisoformat(attempt.name[:10])
    begin, end = [datetime.combine(day, t, BJ) for t in SLOTS[mode]]
    if stop():
        return {"returncode": None, "reason": "stop_requested", "drained": True}
    if not begin <= now().astimezone(BJ) <= end:
        return {"returncode": None, "reason": "launch_window_missed", "drained": True}
    with (attempt / "stdout.log").open("xb") as stdout, (attempt / "stderr.log").open("xb") as stderr:
        process = subprocess.Popen([sys.executable, str(LEAF), mode, "--universe", str(universe),
                                    "--output", str(attempt)], cwd=attempt, env=env,
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        deadline = time.monotonic() + timeout
        next_size_check = time.monotonic() + 5
        try:
            reason = None
            while process.poll() is None:
                if stop():
                    reason = "stop_requested"
                elif time.monotonic() >= deadline:
                    reason = "leaf_timeout"
                elif now().astimezone(BJ) > end:
                    reason = "source_window_ended"
                elif time.monotonic() >= next_size_check and tree_bytes(root) >= MAX_BYTES - CONTROL_RESERVE:
                    reason = "artifact_capacity"
                if reason:
                    drain(process)
                    return {"returncode": process.returncode, "reason": reason, "drained": True}
                if time.monotonic() >= next_size_check:
                    next_size_check = time.monotonic() + 5
                pause(.2)
            process.wait()
            return {"returncode": process.returncode, "reason": None, "drained": True}
        except BaseException:
            try:
                drain(process)
            except Exception:
                pass  # Preserve the first error, never claim the attempt succeeded.
            raise


class CaptureService:
    def __init__(self, root, manifest, *, now=None, pause=time.sleep, stop=lambda: False, leaf=collect):
        self.root, self.manifest = root, manifest
        self.now = now or (lambda: datetime.now(BJ))
        self.pause, self.stop, self.leaf = pause, stop, leaf
        self.started = self.now().isoformat()

    def save(self, path, value, **kw):
        write_json(path, value, self.root, **kw)

    def finish_slot(self, path, state):
        published = checked(self.root / "captures" / state["trade_date"] / state["mode"], self.root)
        attempt = checked(self.root / state["attempt"], self.root)
        if not published.exists():
            source = checked(attempt / state["trade_date"], self.root)
            if verify_capture(source, state["mode"], state["trade_date"], self.manifest, self.root) != state["validated_result"]:
                raise ValueError("publication payload changed")
            published.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, published)
            sync_directory(published.parent)
        if verify_capture(published, state["mode"], state["trade_date"], self.manifest, self.root) != state["validated_result"]:
            raise ValueError("published payload changed")
        if state["state"] == "completed":
            return  # Revalidation must not change the original completion clock.
        self.save(path, {**state, "state": "completed", "finished_at": self.now().isoformat(),
                         "published": str(published.relative_to(self.root))})

    def reconcile(self):
        # A crash between attempt mkdir and durable started cannot authorize replay.
        for attempt in (self.root / "attempts").glob("*"):
            checked(attempt, self.root)
            match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})-(morning|close)", attempt.name)
            if not match or not attempt.is_dir():
                raise ValueError("unbound orphan attempt")
            day, mode = match.groups()
            if day not in self.manifest["trading_days"]:
                raise ValueError("orphan attempt outside frozen calendar")
            path = self.root / "slots" / day / (mode + ".json")
            if not path.exists():
                scheduled, end = [datetime.combine(date.fromisoformat(day), t, BJ) for t in SLOTS[mode]]
                self.save(path, {"state": "interrupted", "trade_date": day, "mode": mode,
                                 "reason": "orphan_attempt_without_started", "execution_outcome": "unknown",
                                 "scheduled_at": scheduled.isoformat(), "window_end": end.isoformat(),
                                 "observed_at": self.now().isoformat(), "finished_at": self.now().isoformat(),
                                 "attempt": str(attempt.relative_to(self.root))}, once=True)
        for path in (self.root / "slots").glob("*/*.json"):
            state = json.loads(checked(path, self.root).read_text())
            if state["state"] == "completed":
                self.finish_slot(path, state)
            if state["state"] == "started":
                if state.get("validated_result") and state.get("leaf_result", {}).get("returncode") == 0 and state["leaf_result"].get("drained") is True:
                    self.finish_slot(path, state)
                else:
                    self.save(path, {**state, "state": "interrupted", "finished_at": self.now().isoformat(),
                                     "reason": "prior_started_without_verified_terminal", "execution_outcome": "unknown"})

    def tick(self):
        now = self.now().astimezone(BJ)
        for day in [date.fromisoformat(self.manifest["start"]) + timedelta(days=i) for i in range(31)]:
            if day > min(now.date(), stamp(self.manifest["until"]).date()):
                break
            if str(day) not in self.manifest["trading_days"]:
                path = self.root / "slots" / str(day) / "closed.json"
                if not path.exists():
                    self.save(path, {"state": "not_applicable_market_closed", "trade_date": str(day)}, once=True)
                continue
            for mode, times in SLOTS.items():
                path = checked(self.root / "slots" / str(day) / (mode + ".json"), self.root)
                if path.exists():
                    continue
                scheduled, end = [datetime.combine(day, t, BJ) for t in times]
                state = {"trade_date": str(day), "mode": mode, "scheduled_at": scheduled.isoformat(),
                         "window_end": end.isoformat(), "observed_at": now.isoformat(),
                         "manifest_sha256": digest(json.dumps(self.manifest, sort_keys=True).encode())}
                if now > end:
                    self.save(path, {**state, "state": "missing", "reason": "window_missed_no_backfill"}, once=True)
                elif now >= scheduled and not self.stop() and now < stamp(self.manifest["until"]):
                    self.capture(path, state, (end - now).total_seconds())

    def capture(self, path, state, remaining):
        # Source code and frozen bytes must still be exactly the startup identity.
        if self.manifest["code_sha256"] != code_identity():
            raise ValueError("capture code changed")
        for name, sha in self.manifest["files_sha256"].items():
            if digest(checked(self.root / "inputs" / name, self.root).read_bytes()) != sha:
                raise ValueError("frozen input changed")
        actual = self.now().astimezone(BJ)
        if self.stop() or actual < stamp(state["scheduled_at"]):
            return
        remaining = (min(stamp(state["window_end"]), stamp(self.manifest["until"])) - actual).total_seconds()
        if remaining <= 0:
            self.save(path, {**state, "state": "missing", "reason": "prelaunch_window_missed_no_backfill"}, once=True)
            return
        attempt = checked(self.root / "attempts" / (state["trade_date"] + "-" + state["mode"]), self.root)
        attempt.mkdir(parents=True, exist_ok=False)
        sync_directory(attempt.parent)
        started = {**state, "state": "started", "started_at": self.now().isoformat(),
                   "service_pid": os.getpid(), "attempt": str(attempt.relative_to(self.root))}
        self.save(path, started, once=True)
        self.save(self.root / "health.json", {"state": "collecting", "service_pid": os.getpid(),
                                             "service_started_at": self.started, "checked_at": self.now().isoformat(),
                                             "slot": str(path.relative_to(self.root)), "until": self.manifest["until"]})
        try:
            result = self.leaf(state["mode"], attempt, self.root / "inputs/universe.parquet", min(240, remaining),
                               self.stop, self.now, self.pause, self.root)
            if result.get("drained") is not True:
                raise ValueError("leaf has not drained")
            if result["returncode"] is None and result["reason"] == "launch_window_missed":
                self.save(path, {**started, "state": "missing", "finished_at": self.now().isoformat(),
                                 "reason": "launch_window_missed_no_backfill", "leaf_result": result})
                return
            if result["returncode"] != 0 or result["reason"]:
                self.save(path, {**started, "state": "interrupted" if result["reason"] == "stop_requested" else "failed",
                                 "finished_at": self.now().isoformat(), "leaf_result": result})
                if result["reason"] != "stop_requested":
                    raise RuntimeError("leaf failed; original attempt retained")
                return
            verified = verify_capture(attempt / state["trade_date"], state["mode"], state["trade_date"], self.manifest, self.root)
            validated = {**started, "leaf_result": result, "validated_result": verified}
            self.save(path, validated)
            self.finish_slot(path, validated)
        except BaseException:
            # A durable validated result is reconciled on restart, never regenerated.
            raise

    def run(self, *, once=False):
        last_health = None
        try:
            self.reconcile()
            while True:
                now = self.now().astimezone(BJ)
                reason = "stop_requested" if self.stop() else "deadline" if now >= stamp(self.manifest["until"]) else "artifact_capacity" if tree_bytes(self.root) >= MAX_BYTES - CONTROL_RESERVE else None
                health = {"service_pid": os.getpid(), "service_started_at": self.started,
                          "checked_at": now.isoformat(), "state": reason or ("waiting_before_batch" if now.date() < date.fromisoformat(self.manifest["start"]) else "waiting"), "until": self.manifest["until"]}
                if reason:
                    self.save(self.root / "health.json", health)
                    self.save(self.root / "terminal.json", {**health, "exit_code": 0})
                    return 0
                if last_health is None or (now - last_health).total_seconds() >= 60:
                    self.save(self.root / "health.json", health)
                    last_health = now
                self.tick()
                if once:
                    return 0
                current = self.now().astimezone(BJ)
                due = [datetime.combine(date.fromisoformat(day), times[0], BJ) for day in self.manifest["trading_days"] for times in SLOTS.values()]
                wake = min([stamp(self.manifest["until"])] + [d for d in due if d > current])
                self.pause(max(.05, min(30, (wake - current).total_seconds())))
        except BaseException as error:
            try:
                self.save(self.root / "terminal.json", {"state": "failed", "error_type": type(error).__name__,
                                                       "checked_at": self.now().isoformat(), "exit_code": 1})
                if not (self.root / "first-error.json").exists():
                    self.save(self.root / "first-error.json", {"error_type": type(error).__name__, "errno": getattr(error, "errno", None),
                                                              "checked_at": self.now().isoformat()}, once=True)
            except Exception:
                pass
            raise


def status(root: Path, now: datetime) -> dict:
    result = {name: json.loads(checked(root / name, root).read_text()) if (root / name).exists() else None
              for name in ["health.json", "terminal.json"]}
    lock = checked(root / "service.lock", root)
    held = False
    if lock.exists():
        with lock.open("r") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                held = True
    health = result["health.json"]
    age = (now - stamp(health["checked_at"])).total_seconds() if health else None
    limit = 300 if health and health.get("state") == "collecting" else 120
    result.update(lock_held=held, health_age_seconds=age,
                  observed_state="not_running" if not held else "locked_health_stale" if age is None or not 0 <= age <= limit else "running_" + health["state"])
    return result


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "status", "print-plist"])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--universe", type=Path)
    parser.add_argument("--calendar", type=Path)
    parser.add_argument("--start", type=date.fromisoformat, default=START)
    parser.add_argument("--until", type=stamp, default=UNTIL)
    parser.add_argument("--once", action="store_true", help="one scheduling pass; never bypass a source window")
    return parser.parse_args()


def main():
    args = arguments()
    artifacts = REPO / "artifacts"
    root = checked(args.output, artifacts)
    if root == artifacts:
        raise ValueError("a separate batch output is required")
    if args.command == "status":
        print(json.dumps(status(root, datetime.now(BJ))))
        return 0
    if not args.universe or not args.calendar:
        raise ValueError("calendar and universe are required")
    if args.command == "print-plist":
        argv = [sys.executable, str(Path(__file__).resolve()), "run", "--output", str(root),
                "--universe", str(args.universe.absolute()), "--calendar", str(args.calendar.absolute()),
                "--start", str(args.start), "--until", args.until.isoformat()]
        plist = {"Label": "com.ashare.rsh031.capture", "ProgramArguments": argv,
                 "WorkingDirectory": str(REPO / "backend"), "RunAtLoad": True,
                 "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 60,
                 "StandardOutPath": str(root / "service.stdout.log"),
                 "StandardErrorPath": str(root / "service.stderr.log"),
                 "StartCalendarInterval": [{"Weekday": d, "Hour": h, "Minute": m} for d in range(1, 6) for h, m in [(9, 45), (15, 10)]]}
        print(plistlib.dumps(plist).decode(), end="")
        return 0
    # Deadline/STOP also apply when freeze or filesystem access failed earlier.
    if datetime.now(BJ) >= args.until or checked(root / "STOP", root).exists():
        return 0
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked(root, artifacts)
    lock_fd = os.open(checked(root / "service.lock", root), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = freeze(root, args.universe, args.calendar, args.start, args.until)
        stopped = False

        def stop(_sig, _frame):
            nonlocal stopped
            stopped = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, stop)
        return CaptureService(root, manifest, stop=lambda: stopped or checked(root / "STOP", root).exists()).run(once=args.once)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # One bounded line per failed launch; no exception text, request or context.
        print(json.dumps({"state": "failed", "error_type": type(exc).__name__, "errno": getattr(exc, "errno", None),
                          "checked_at": datetime.now(BJ).isoformat()}), file=sys.stderr)
        raise SystemExit(1)
