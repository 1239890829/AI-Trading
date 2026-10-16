"""Revision-checked read projection; no market cache or history backfill.

SQLite data_version is local to a connection. The memo lives on that same
physical pooled connection; total_changes also detects its own writes. Never
reuse a version across connections. In-memory/shared test connections bypass it.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_session_factory
from app.core.bjtime import beijing_now
from app.core.ttl_cache import TTLCache
from app.picks.validation import validation_report


def _revision(connection):
    driver = connection.connection.driver_connection
    return (connection.exec_driver_sql('PRAGMA data_version').scalar_one(), driver.total_changes)


def _envelope(report):
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return report, hashlib.sha256(encoded).hexdigest(), len(encoded)


def read_validation(start, end, *, horizon='d1_close', scope='all', session_factory=None, calendar=None):
    sf = session_factory or get_session_factory()
    if calendar is None:
        from app.market.trade_calendar import known_trade_days
        calendar = known_trade_days()
    calendar = tuple(calendar)
    engine = getattr(sf, 'kw', {}).get('bind')
    args = dict(horizon=horizon, scope=scope, calendar=calendar)
    # No cache unless the engine owns separate, exclusive file connections.
    if (engine is None or not hasattr(engine, 'pool') or engine.dialect.name != 'sqlite' or isinstance(engine.pool, StaticPool)
            or engine.url.database in (None, '', ':memory:')):
        report, revision, _ = _envelope(validation_report(start, end, session_factory=sf, **args))
        return report, revision
    with engine.connect() as connection:
        before = _revision(connection)
        memo = connection.info.get('selection_validation_memo')
        if memo is None:
            # One small result per pooled connection. Age only bounds memory;
            # every use checks committed data and the actual calendar again.
            memo = TTLCache('selection_validation_projection', 120, maxsize=1)
            connection.info['selection_validation_memo'] = memo
        now = beijing_now()
        close_stage = (now.date(), (now.hour, now.minute) >= (15, 0))
        key = (before, start, end, horizon, scope, calendar, close_stage)
        hit, value = memo.get(key)
        if hit:
            return deepcopy(value)
        bound = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False)
        report, revision, size = _envelope(validation_report(start, end, session_factory=bound, **args))
        # A concurrent commit can make a multi-query result transitional. Do not
        # freeze it as current; the next request must recompute from the DB.
        if before == _revision(connection) and size <= 2_000_000:
            memo.set(key, deepcopy((report, revision)))
        return report, revision
