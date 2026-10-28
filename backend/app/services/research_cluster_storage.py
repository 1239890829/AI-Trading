"""Immutable, content-addressed research-cluster observations.

Only the current Beijing trading-date observation may be published. Historical
readers consume saved versions and never recalculate or fill a missed day.
"""
from __future__ import annotations

from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile

from app.core.bjtime import BJ_TZ, beijing_now
from app.services.research_clusters import (
    _clock, canonical, content_digest, empty_research_clusters, validate_day, validate_snapshot,
)


ROOT = Path(__file__).resolve().parents[2] / "data" / "research_clusters"
SCHEMA = "immutable-research-cluster-snapshot-v1"
_VERSION = re.compile(r"[0-9a-f]{64}")
_MAX_BYTES = 32 * 1024 * 1024


def _root(value: Path | None) -> Path:
    result = Path(value) if value is not None else ROOT
    if result.is_symlink():
        raise ValueError("research snapshot root must not be a symlink")
    return result


def _day_path(root: Path, trade_date: str) -> Path:
    validate_day(trade_date)
    path = root / trade_date
    if path.is_symlink():
        raise ValueError("research snapshot day must not be a symlink")
    return path


def _load(path: Path):
    if path.is_symlink() or path.stat().st_size > _MAX_BYTES:
        raise ValueError("invalid research snapshot file")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_version(path: Path, trade_date: str, version: str) -> dict:
    value = _load(path)
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or not isinstance(value.get("payload"), dict)
            or value.get("payload_sha256") != content_digest(value["payload"])):
        raise ValueError("saved research snapshot envelope corrupt")
    payload = value["payload"]
    validate_snapshot(payload)
    if payload["trade_date"] != trade_date or payload["version"] != version:
        raise ValueError("saved research snapshot identity mismatch")
    if _clock(payload["observed_at"]).astimezone(BJ_TZ).date().isoformat() != trade_date:
        raise ValueError("saved research snapshot was not observed on its trading date")
    return payload


def _write_temporary(folder: Path, value) -> Path:
    body = canonical(value)
    if len(body) > _MAX_BYTES:
        raise ValueError("research snapshot exceeds bounded size")
    fd, name = tempfile.mkstemp(prefix=".publish-", suffix=".tmp", dir=folder)
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _validate_pointer(value, trade_date: str, *, payload: dict | None = None) -> None:
    if (not isinstance(value, dict) or value.get("trade_date") != trade_date
            or not isinstance(value.get("version"), str) or not _VERSION.fullmatch(value["version"])):
        raise ValueError("research latest pointer corrupt")
    selected = _clock(value.get("selected_at"))
    now = beijing_now()
    if (selected.astimezone(BJ_TZ).date().isoformat() != trade_date
            or (now.date().isoformat() == trade_date and selected > now)
            or (payload is not None and selected < _clock(payload["observed_at"]))):
        raise ValueError("research latest pointer clock conflict")


def publish_research_clusters(payload: dict, *, root: Path | None = None) -> dict:
    """Create once; deduplicate across threads/processes without overwriting.

    Reobserving identical content preserves its first saved observation clock.
    An atomic pointer records which immutable version was most recently seen.
    A damaged existing version/pointer is an error, never silently repaired.
    """
    validate_snapshot(payload)
    incoming = deepcopy(payload)
    observed = _clock(incoming["observed_at"])
    now = beijing_now()
    day = incoming["trade_date"]
    if (observed.astimezone(BJ_TZ).date().isoformat() != day or now.date().isoformat() != day
            or observed > now):
        raise ValueError("research snapshots cannot backfill historical or future observations")
    if incoming["reference_mode"] != "current" or incoming["state"] == "unavailable":
        raise ValueError("only usable current observations may be published")
    folder = _day_path(_root(root), day)
    folder.mkdir(parents=True, exist_ok=True)
    version = incoming["version"]
    target = folder / f"{version}.json"
    pointer = folder / "latest.json"
    lock_path = folder / ".publication.lock"
    if lock_path.is_symlink() or pointer.is_symlink() or target.is_symlink():
        raise ValueError("research publication paths must not be symlinks")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current_pointer = None
        if pointer.exists():
            current_pointer = _load(pointer)
            _validate_pointer(current_pointer, day)
            # Do not hide a missing/corrupted previously published target.
            prior = _read_version(folder / f"{current_pointer['version']}.json", day, current_pointer["version"])
            _validate_pointer(current_pointer, day, payload=prior)
        if target.exists():
            stored = _read_version(target, day, version)
        else:
            temporary = _write_temporary(folder, {"schema": SCHEMA, "payload": incoming,
                                                   "payload_sha256": content_digest(incoming)})
            try:
                os.link(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            stored = incoming
        selection_clock = max(observed, _clock(stored["observed_at"]))
        if current_pointer is None or selection_clock >= _clock(current_pointer["selected_at"]):
            temporary = _write_temporary(folder, {"trade_date": day, "version": version,
                                                   "selected_at": selection_clock.isoformat()})
            try:
                os.replace(temporary, pointer)
            finally:
                temporary.unlink(missing_ok=True)
        directory_fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return deepcopy(stored)


def read_research_clusters(trade_date: str, *, version: str | None = None,
                           root: Path | None = None) -> dict:
    """Read only the recorded day/version; no factories or source access."""
    validate_day(trade_date)
    if version is not None and (not isinstance(version, str) or not _VERSION.fullmatch(version)):
        raise ValueError("full research snapshot version required")
    try:
        folder = _day_path(_root(root), trade_date)
        if version is None:
            pointer = folder / "latest.json"
            if not pointer.exists():
                # A completed version without a pointer means interrupted
                # publication. Do not silently call it an unrecorded day.
                orphaned = folder.exists() and any(folder.glob("*.json"))
                return empty_research_clusters(trade_date,
                    state="unavailable" if orphaned else "not_recorded",
                    reference_mode="saved" if orphaned else "not_recorded",
                    reason="snapshot_pointer_missing" if orphaned else "historical_cluster_not_recorded")
            value = _load(pointer)
            _validate_pointer(value, trade_date)
            selected_version = value["version"]
        else:
            selected_version = version
        path = folder / f"{selected_version}.json"
        if not path.exists():
            return empty_research_clusters(trade_date, state="not_recorded" if version else "unavailable",
                                           reference_mode="not_recorded" if version else "saved",
                                           reason="snapshot_version_missing" if version else "snapshot_pointer_target_missing")
        payload = deepcopy(_read_version(path, trade_date, selected_version))
        if version is None:
            _validate_pointer(value, trade_date, payload=payload)
        payload["reference_mode"] = "saved"
        return payload
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return empty_research_clusters(trade_date, state="unavailable", reference_mode="saved",
                                       reason="snapshot_corrupt_or_unreadable")
