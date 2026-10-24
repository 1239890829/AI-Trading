"""Live ownership proof for selected-research calls, never a usage or permission ledger.

An existing usage row remains the sole accounting fact. A short metadata file and
held OS locks prove that its owner is still draining a bounded real call. Closing
or losing the lock, expiration, task termination or unknown completion removes
that proof; no token count or historical unknown is amended here.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from sqlalchemy import select

from app.core.bjtime import beijing_now
from app.core.config import TRADINGAGENTS_SELECTED_MODEL, settings
from app.models.agent import AgentResourceUsage, AgentTask

ROOT: Path | None = None  # Tests may substitute only their isolated directory.
SCOPE = "selected_research_llm"
MAX_METADATA = 4096
DRAIN_GRACE_SECONDS = 30


def capacity() -> int:
    return max(1, min(3, int(settings.tradingagents_research_concurrency)))


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def authorization() -> dict | None:
    """Observe the pinned original authorization; this cannot create one."""
    if not settings.tradingagents_research_enabled or not settings.tradingagents_research_authorization_digest:
        return None
    try:
        from app.services.tradingagents_research import ROOT as research_root
        path = research_root / "authorization.json"
        if research_root.is_symlink() or path.is_symlink() or path.stat().st_size > 2_500_000:
            return None
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        value = json.loads(body)
        from datetime import datetime
        start, end = (datetime.fromisoformat(value[key].replace("Z", "+00:00")) for key in ("created_at", "valid_until"))
        if (digest != settings.tradingagents_research_authorization_digest
                or start.utcoffset() is None or end.utcoffset() is None
                or not start <= beijing_now() < end
                or value.get("version") != 1
                or value.get("mode") != "RESEARCH_ONLY_AUTHORIZED"
                or value.get("consumer") != "selected_stock_explanation"
                or value.get("user_authorization") != "EXPLICIT"
                or value.get("production_eligible") is not False or value.get("net_gain_verified") is not False
                or not isinstance(value.get("method_identity"), dict)
                or value["method_identity"].get("transport", {}).get("provider") != "codex_cli"
                or value["method_identity"].get("transport", {}).get("model") != TRADINGAGENTS_SELECTED_MODEL
                or value["method_identity"].get("transport", {}).get("effort") != "medium"):
            return None
        return {"authorization_sha256": digest,
                "method_sha256": hashlib.sha256(_canonical(value["method_identity"])).hexdigest()}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _root(*, create=False) -> Path:
    if ROOT is None:
        from app.services.tradingagents_research import ROOT as research_root
        root = research_root / "model-inflight"
    else:
        root = Path(ROOT)
    if root.is_symlink() or root.parent.is_symlink():
        raise ValueError("invalid selected-model lease root")
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("unsafe selected-model lease directory")
    return root


def _file(root: Path, name: str, *, create=False, exclusive=False) -> int:
    flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    if create:
        flags |= os.O_CREAT
    if exclusive:
        flags |= os.O_EXCL
    fd = os.open(root / name, flags, 0o600)
    try:
        info = os.fstat(fd)
        current = (root / name).lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError("unsafe selected-model lease file")
        return fd
    except BaseException:
        os.close(fd)
        raise


class CallLease:
    def __init__(self, descriptor: int, slot_descriptor: int):
        self.descriptor, self.slot_descriptor = descriptor, slot_descriptor

    def close(self):
        for name in ("descriptor", "slot_descriptor"):
            fd = getattr(self, name)
            if fd is not None:
                setattr(self, name, None)
                os.close(fd)


def acquire(usage_id: str, session_factory, *, method_sha256: str, cancel_check=None) -> CallLease | None:
    """Hold a bounded capacity slot until inference and original usage finish drain.

    Legacy direct/unowned calls keep their old serial admission; they receive no
    new in-flight exemption. Real selected tasks must still pass the driver's
    original authorization checks before reserve and again immediately at start.
    """
    auth = authorization()
    if auth is None or auth["method_sha256"] != method_sha256:
        return None
    with session_factory() as db:
        row = db.get(AgentResourceUsage, usage_id)
        task = db.get(AgentTask, row.task_id) if row and row.task_id else None
        if (row is None or row.scope != SCOPE or row.kind != "model" or row.state != "reserved"
                or not row.purpose.startswith("tradingagents.")
                or row.provider != "codex_cli" or row.model != TRADINGAGENTS_SELECTED_MODEL or task is None
                or task.type != "tradingagents_research" or task.status != "running"
                or task.cancel_requested_at is not None):
            return None
        try:
            params = json.loads(task.params)
            if not isinstance(params, dict) or not re.fullmatch(r"[0-9]{6}", params.get("symbol", "")):
                return None
        except (ValueError, TypeError):
            return None
        facts = {"usage_id": row.id, "task_id": row.task_id, "budget_date": row.budget_date,
                 "scope": row.scope, "timeout_ms": row.timeout_ms, "purpose": row.purpose,
                 "provider": row.provider, "model": row.model, "symbol": params["symbol"],
                 "task_params_sha256": hashlib.sha256(_canonical(params)).hexdigest()}
    if not re.fullmatch(r"[0-9a-f]{32}", usage_id):
        raise ValueError("invalid selected-model usage identity")
    root = _root(create=True)
    slot_fd, descriptor, slot = None, None, None
    wait_until = time.monotonic() + max(1, facts["timeout_ms"] / 1000)
    try:
        while slot_fd is None:
            if cancel_check is not None:
                cancel_check()
            if authorization() != auth:
                raise ValueError("selected-model authorization changed while waiting")
            for number in range(capacity()):
                candidate = _file(root, f"slot-{number}.lock", create=True)
                try:
                    fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    slot_fd, slot = candidate, number
                    break
                except BlockingIOError:
                    os.close(candidate)
            if slot_fd is None:
                if time.monotonic() >= wait_until:
                    raise RuntimeError("selected-model capacity wait expired before launch")
                time.sleep(0.025)
        descriptor = _file(root, usage_id + ".json", create=True, exclusive=True)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inode, slot_inode = os.fstat(descriptor), os.fstat(slot_fd)
        now = time.time()
        value = {"version": 1, **facts, **auth, "owner_nonce": uuid.uuid4().hex,
                 "pid": os.getpid(), "created_epoch": now,
                 "expires_epoch": now + max(1, facts["timeout_ms"] / 1000) + DRAIN_GRACE_SECONDS,
                 "device": inode.st_dev, "inode": inode.st_ino, "slot": slot,
                 "slot_device": slot_inode.st_dev, "slot_inode": slot_inode.st_ino}
        os.write(descriptor, _canonical(value))
        os.fsync(descriptor)
        return CallLease(descriptor, slot_fd)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if slot_fd is not None:
            os.close(slot_fd)
        raise


def _locked(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    fcntl.flock(fd, fcntl.LOCK_UN)
    return False


def active_usage_ids(session_factory, date: str) -> tuple[str, ...]:
    """A released/expired/orphan/terminal call is never excluded from unknowns."""
    auth = authorization()
    if auth is None:
        return ()
    try:
        root = _root()
    except (OSError, ValueError):
        return ()
    with session_factory() as db:
        rows = db.execute(select(AgentResourceUsage, AgentTask.params).join(AgentTask, AgentTask.id == AgentResourceUsage.task_id).where(
            AgentResourceUsage.budget_date == date, AgentResourceUsage.scope == SCOPE,
            AgentResourceUsage.kind == "model", AgentResourceUsage.state == "started",
            AgentResourceUsage.usage_known == 0, AgentResourceUsage.started_at.is_not(None),
            AgentResourceUsage.purpose.like("tradingagents.%"), AgentTask.type == "tradingagents_research",
            AgentTask.status == "running").limit(33)).all()
    if len(rows) > 32:
        return ()
    valid = []
    for row, raw_params in rows:
        descriptor, slot_fd = None, None
        try:
            params = json.loads(raw_params)
            if (not re.fullmatch(r"[0-9a-f]{32}", row.id) or not isinstance(params, dict)
                    or not re.fullmatch(r"[0-9]{6}", params.get("symbol", ""))):
                continue
            descriptor = _file(root, row.id + ".json")
            info = os.fstat(descriptor)
            if not _locked(descriptor) or info.st_size > MAX_METADATA:
                continue
            body = os.pread(descriptor, MAX_METADATA + 1, 0)
            value = json.loads(body)
            now = time.time()
            if (value.get("version") != 1 or value.get("usage_id") != row.id
                    or value.get("task_id") != row.task_id or value.get("budget_date") != date
                    or value.get("scope") != SCOPE or value.get("timeout_ms") != row.timeout_ms
                    or value.get("purpose") != row.purpose or value.get("provider") != row.provider
                    or value.get("model") != row.model or row.provider != "codex_cli" or row.model != TRADINGAGENTS_SELECTED_MODEL
                    or value.get("symbol") != params["symbol"]
                    or value.get("task_params_sha256") != hashlib.sha256(_canonical(params)).hexdigest()
                    or any(value.get(key) != expected for key, expected in auth.items())
                    or (value.get("device"), value.get("inode")) != (info.st_dev, info.st_ino)
                    or not re.fullmatch(r"[0-9a-f]{32}", value.get("owner_nonce", ""))
                    or type(value.get("pid")) is not int or value["pid"] <= 0
                    or not value["created_epoch"] <= now < value["expires_epoch"]
                    or not 0 < value["expires_epoch"] - value["created_epoch"] <= row.timeout_ms / 1000 + DRAIN_GRACE_SECONDS + 1
                    or type(value.get("slot")) is not int or not 0 <= value["slot"] < capacity()):
                continue
            os.kill(value["pid"], 0)
            slot_fd = _file(root, f"slot-{value['slot']}.lock")
            slot_info = os.fstat(slot_fd)
            if ((value.get("slot_device"), value.get("slot_inode")) != (slot_info.st_dev, slot_info.st_ino)
                    or not _locked(slot_fd) or os.pread(descriptor, MAX_METADATA + 1, 0) != body):
                continue
            valid.append(row.id)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if slot_fd is not None:
                os.close(slot_fd)
    return tuple(valid)
