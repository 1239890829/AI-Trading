"""Bounded connection/statement observations, never SQL text or user parameters.

Connection hold duration includes Python work and commit/rollback. It is an
upper bound on potential lock duration, not proof of the actual lock owner.
Dedicated retention keeps lock errors and this process's longest completed
write-seen connection holds; ordinary reads cannot evict those observations.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from itertools import count
from threading import Lock
import sys
import time

from sqlalchemy import event

_recent = deque(maxlen=64)
_LOCK_ERROR_CAPACITY = 32
_WRITE_HOLD_CAPACITY = 16
_lock_errors = deque(maxlen=_LOCK_ERROR_CAPACITY)
_longest_write_holds = []
_guard = Lock()
_ids = count(1)
_SLOW_MS = 300


def _caller():
    frame = sys._getframe(2)
    try:
        for _ in range(48):
            if frame is None:
                break
            filename = frame.f_code.co_filename.replace('\\', '/')
            if '/app/' in filename and not filename.endswith(('/core/db.py', '/core/db_observation.py')):
                return 'app/' + filename.rsplit('/app/', 1)[1] + ':' + frame.f_code.co_name
            frame = frame.f_back
        return 'external_or_unknown'
    finally:
        del frame


def _record(info, kind, *, duration_ms, error=None):
    row = {'kind': kind, 'connection_use': info.get('id'), 'owner': info.get('owner'),
           'duration_ms': round(duration_ms, 2), 'write_seen': info.get('write_seen', False),
           'error_type': error, 'monotonic_at': time.monotonic(),
           'observed_at': datetime.now(timezone.utc).isoformat()}
    with _guard:
        _recent.append(row)
        if kind == 'lock_error':
            _lock_errors.append(row)
        if kind == 'connection_hold' and row['write_seen']:
            _longest_write_holds.append(row)
            _longest_write_holds.sort(key=lambda entry: entry['duration_ms'], reverse=True)
            del _longest_write_holds[_WRITE_HOLD_CAPACITY:]


def snapshot():
    with _guard:
        rows = [row.copy() for row in _recent]
        lock_errors = [row.copy() for row in _lock_errors]
        write_holds = [row.copy() for row in _longest_write_holds]
    return {'recent': rows, 'capacity': 64, 'basis': 'bounded observations, not a request denominator; connection hold is not measured lock time',
            'lock_errors': lock_errors, 'lock_error_capacity': _LOCK_ERROR_CAPACITY,
            'longest_write_holds': write_holds, 'write_hold_capacity': _WRITE_HOLD_CAPACITY,
            'write_hold_basis': 'longest completed write_seen connections in this process, not proven lock owners or measured lock time'}


def install(engine):
    if engine.dialect.name != 'sqlite' or getattr(engine, '_ashare_observed', False):
        return
    engine._ashare_observed = True

    @event.listens_for(engine.pool, 'checkout')
    def checkout(_dbapi, record, _proxy):
        record.info['_ashare_db_read'] = {'id': next(_ids), 'owner': _caller(),
                                        'start': time.monotonic(), 'write_seen': False}

    @event.listens_for(engine, 'before_cursor_execute')
    def before(connection, _cursor, statement, _parameters, context, _many):
        context._ashare_statement_start = time.monotonic()
        info = connection.info.get('_ashare_db_read')
        if info is not None:
            verb = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ''
            if verb in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'ALTER', 'CREATE', 'DROP'}:
                info['write_seen'] = True

    @event.listens_for(engine, 'after_cursor_execute')
    def after(connection, _cursor, _statement, _parameters, context, _many):
        elapsed = (time.monotonic() - context._ashare_statement_start) * 1000
        if elapsed >= _SLOW_MS:
            _record(connection.info.get('_ashare_db_read', {}), 'slow_statement', duration_ms=elapsed)

    @event.listens_for(engine, 'handle_error')
    def error(context):
        connection = context.connection
        if connection is not None and not connection.invalidated:
            info = connection.info.get('_ashare_db_read', {})
            message = str(context.original_exception).lower()
            if 'locked' in message or 'busy' in message:
                _record(info, 'lock_error', duration_ms=(time.monotonic()-info.get('start', time.monotonic()))*1000,
                        error=type(context.original_exception).__name__)

    @event.listens_for(engine.pool, 'checkin')
    def checkin(_dbapi, record):
        info = record.info.pop('_ashare_db_read', None)
        if info is not None:
            elapsed = (time.monotonic() - info['start']) * 1000
            if elapsed >= _SLOW_MS:
                _record(info, 'connection_hold', duration_ms=elapsed)
