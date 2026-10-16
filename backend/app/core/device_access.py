"""Device sessions and one-use WS tickets. No market data or model calls.

This small store is separate from the busy market database: permission checks
must not wait for market-history writers. Only token hashes are persisted.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from app.core.config import REPO_ROOT

STORE_PATH = REPO_ROOT / "data" / "device-access.sqlite3"
SESSION_SECONDS = 30 * 86400
PENDING_SECONDS = 300
MAINTENANCE_SECONDS = 900
WS_TICKET_SECONDS = 60
WS_RECHECK_SECONDS = 15
SESSION_HEADER = "X-Ashare-Session"
ORIGIN_HEADER = "X-Ashare-Origin"
BROWSER_HEADER = "X-Ashare-Browser"
WS_TICKET_PREFIX = "ashare-ticket."


class AccessError(ValueError):
    def __init__(self, code: str, status: int = 403):
        super().__init__(code)
        self.code, self.status = code, status


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def valid_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        return bool(parsed.scheme in {"http", "https"} and parsed.hostname
                    and not parsed.username and not parsed.password
                    and not parsed.path and not parsed.query and not parsed.fragment
                    and parsed.port != 0)
    except ValueError:
        return False


class DeviceAccessStore:
    def __init__(self, path: Path):
        self.path = path
        self._ready = False
        self._lock = threading.Lock()

    def _initialize(self) -> None:
        with self._lock:
            if self._ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.fchmod(fd, 0o600)
            os.close(fd)
            with sqlite3.connect(self.path, timeout=1) as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS devices (
                        id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                        label TEXT NOT NULL, origin TEXT NOT NULL, state TEXT NOT NULL,
                        owner INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
                        expires REAL NOT NULL, maintenance_until REAL NOT NULL DEFAULT 0);
                    CREATE TABLE IF NOT EXISTS bootstrap (
                        id INTEGER PRIMARY KEY CHECK(id=1), token_hash TEXT NOT NULL,
                        expires REAL NOT NULL, recover INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS tickets (
                        token_hash TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                        origin TEXT NOT NULL, expires REAL NOT NULL);
                """)
            self._ready = True

    @contextmanager
    def _db(self, *, write: bool = False):
        self._initialize()
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _active(db, token: str, origin: str, now: float):
        row = db.execute("SELECT * FROM devices WHERE token_hash=?", (_hash(token),)).fetchone()
        if not row or row["state"] != "active" or row["expires"] <= now or row["origin"] != origin:
            raise AccessError("device_pairing_required", 401)
        return row

    @staticmethod
    def _public(row, now: float) -> dict:
        return {"id": row["id"], "label": row["label"], "state": row["state"],
                "owner": bool(row["owner"]), "expires_at": row["expires"],
                "maintenance_until": row["maintenance_until"],
                "maintenance": row["maintenance_until"] > now}

    @staticmethod
    def _clean(db, now: float) -> None:
        db.execute("DELETE FROM tickets WHERE expires<=?", (now,))
        db.execute("DELETE FROM devices WHERE expires<=? OR state='revoked'", (now,))

    def status(self, token: str, origin: str) -> dict:
        now = time.time()
        with self._db() as db:
            row = db.execute("SELECT * FROM devices WHERE token_hash=?", (_hash(token),)).fetchone()
            if not row or row["expires"] <= now or row["state"] == "revoked" or row["origin"] != origin:
                return {"state": "unpaired"}
            return self._public(row, now)

    def authorize(self, token: str, origin: str, *, maintenance: bool = False) -> dict:
        now = time.time()
        with self._db() as db:
            row = self._active(db, token, origin, now)
            if maintenance and row["maintenance_until"] <= now:
                raise AccessError("maintenance_approval_required")
            return self._public(row, now)

    def start(self, label: str, origin: str) -> tuple[str, dict]:
        if not valid_origin(origin) or not label.strip() or len(label) > 60:
            raise AccessError("pairing_input_invalid", 400)
        now, token, device_id = time.time(), secrets.token_urlsafe(32), secrets.token_hex(8)
        with self._db(write=True) as db:
            self._clean(db, now)
            if db.execute("SELECT count(*) FROM devices WHERE state='pending'").fetchone()[0] >= 20:
                raise AccessError("pairing_queue_full", 429)
            db.execute("INSERT INTO devices VALUES (?,?,?,?,'pending',0,?,?,0)",
                       (device_id, _hash(token), label.strip(), origin, now, now + PENDING_SECONDS))
        return token, self.status(token, origin)

    def bootstrap_code(self, *, recover: bool = False) -> str:
        now, code = time.time(), secrets.token_urlsafe(24)
        with self._db(write=True) as db:
            self._clean(db, now)
            if not recover and db.execute("SELECT 1 FROM devices WHERE owner=1 AND state='active'").fetchone():
                raise AccessError("owner_already_paired", 409)
            db.execute("INSERT OR REPLACE INTO bootstrap VALUES (1,?,?,?)",
                       (_hash(code), now + PENDING_SECONDS, int(recover)))
        return code

    def bootstrap(self, code: str, label: str, origin: str) -> tuple[str, dict]:
        if not valid_origin(origin) or not label.strip() or len(label) > 60:
            raise AccessError("pairing_input_invalid", 400)
        now, token, device_id = time.time(), secrets.token_urlsafe(32), secrets.token_hex(8)
        with self._db(write=True) as db:
            boot = db.execute("SELECT * FROM bootstrap WHERE id=1 AND token_hash=?", (_hash(code),)).fetchone()
            if not boot or boot["expires"] <= now:
                raise AccessError("bootstrap_code_invalid")
            self._clean(db, now)
            owner = db.execute("SELECT 1 FROM devices WHERE owner=1 AND state='active'").fetchone()
            if owner and not boot["recover"]:
                raise AccessError("owner_already_paired", 409)
            if boot["recover"]:
                db.execute("UPDATE devices SET state='revoked',maintenance_until=0")
                db.execute("DELETE FROM tickets")
            db.execute("DELETE FROM bootstrap")
            db.execute("INSERT INTO devices VALUES (?,?,?,?,'active',1,?,?,0)",
                       (device_id, _hash(token), label.strip(), origin, now, now + SESSION_SECONDS))
        return token, self.status(token, origin)

    def devices(self, token: str, origin: str) -> list[dict]:
        now = time.time()
        with self._db() as db:
            if not self._active(db, token, origin, now)["owner"]:
                raise AccessError("owner_approval_required")
            return [self._public(row, now) for row in db.execute(
                "SELECT * FROM devices WHERE expires>? AND state!='revoked' ORDER BY created DESC", (now,))]

    def manage(self, token: str, origin: str, device_id: str, action: str) -> dict:
        now = time.time()
        with self._db(write=True) as db:
            owner = self._active(db, token, origin, now)
            if not owner["owner"]:
                raise AccessError("owner_approval_required")
            row = db.execute("SELECT * FROM devices WHERE id=? AND expires>? AND state!='revoked'", (device_id, now)).fetchone()
            if not row:
                raise AccessError("device_not_found", 404)
            if action == "approve" and row["state"] == "pending":
                if db.execute("SELECT count(*) FROM devices WHERE state='active'").fetchone()[0] >= 100:
                    raise AccessError("device_limit_reached", 409)
                db.execute("UPDATE devices SET state='active',expires=? WHERE id=?", (now + SESSION_SECONDS, device_id))
            elif action == "revoke":
                db.execute("UPDATE devices SET state='revoked',maintenance_until=0 WHERE id=?", (device_id,))
                db.execute("DELETE FROM tickets WHERE device_id=?", (device_id,))
            elif action in {"grant-maintenance", "end-maintenance"} and row["state"] == "active":
                until = min(now + MAINTENANCE_SECONDS, row["expires"]) if action == "grant-maintenance" else 0
                db.execute("UPDATE devices SET maintenance_until=? WHERE id=?", (until, device_id))
            else:
                raise AccessError("device_action_invalid", 409)
        return {"ok": True}

    def logout(self, token: str, origin: str) -> None:
        with self._db(write=True) as db:
            row = db.execute("SELECT id FROM devices WHERE token_hash=? AND origin=?", (_hash(token), origin)).fetchone()
            if row:
                db.execute("UPDATE devices SET state='revoked',maintenance_until=0 WHERE id=?", (row["id"],))
                db.execute("DELETE FROM tickets WHERE device_id=?", (row["id"],))

    def ticket(self, token: str, origin: str) -> str:
        now, ticket = time.time(), secrets.token_urlsafe(32)
        with self._db(write=True) as db:
            row = self._active(db, token, origin, now)
            self._clean(db, now)
            if db.execute("SELECT count(*) FROM tickets WHERE device_id=?", (row["id"],)).fetchone()[0] >= 32:
                raise AccessError("ws_ticket_limit", 429)
            db.execute("INSERT INTO tickets VALUES (?,?,?,?)", (_hash(ticket), row["id"], origin, now + WS_TICKET_SECONDS))
        return WS_TICKET_PREFIX + ticket

    def consume_ticket(self, protocol: str, origin: str) -> str:
        now = time.time()
        if not protocol.startswith(WS_TICKET_PREFIX):
            raise AccessError("ws_ticket_invalid", 401)
        with self._db(write=True) as db:
            row = db.execute("SELECT t.device_id FROM tickets t JOIN devices d ON d.id=t.device_id "
                             "WHERE t.token_hash=? AND t.origin=? AND t.expires>? AND d.state='active' AND d.expires>?",
                             (_hash(protocol[len(WS_TICKET_PREFIX):]), origin, now, now)).fetchone()
            if not row:
                raise AccessError("ws_ticket_invalid", 401)
            db.execute("DELETE FROM tickets WHERE token_hash=?", (_hash(protocol[len(WS_TICKET_PREFIX):]),))
            return row["device_id"]

    def ws_active(self, device_id: str) -> bool:
        with self._db() as db:
            return bool(db.execute("SELECT 1 FROM devices WHERE id=? AND state='active' AND expires>?",
                                   (device_id, time.time())).fetchone())


@lru_cache(maxsize=4)
def _store(path: Path) -> DeviceAccessStore:
    return DeviceAccessStore(path)


def get_device_store() -> DeviceAccessStore:
    return _store(STORE_PATH)
