"""Observability never changes SQLite semantics or records SQL/parameters."""
import json
import sqlite3
from collections import deque
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.core import db_observation as obs


@pytest.fixture
def isolated_observations(monkeypatch):
    monkeypatch.setattr(obs, '_recent', deque(maxlen=64))
    monkeypatch.setattr(obs, '_lock_errors', deque(maxlen=32), raising=False)
    monkeypatch.setattr(obs, '_longest_write_holds', [], raising=False)


def test_real_lock_and_write_hold_survive_more_than_recent_read_capacity(tmp_path, monkeypatch,
                                                                       isolated_observations):
    path = tmp_path/'retained-lock.db'
    engine = create_engine(f'sqlite:///{path}', pool_size=1, connect_args={'timeout':.01})
    # Every real read produces observations; this makes eviction deterministic
    # without sleeping, changing SQLite's lock semantics or freezing its clock.
    monkeypatch.setattr(obs, '_SLOW_MS', 0)
    obs.install(engine)
    holder = None
    try:
        with engine.begin() as conn:
            conn.execute(text('create table facts (value text)'))
            conn.execute(text('insert into facts values (:value)'), {'value': 'private-retention-fixture'})
        written = obs.snapshot()['recent'][-1]
        assert written['kind'] == 'connection_hold' and written['write_seen']
        holder = sqlite3.connect(path)
        holder.execute('begin exclusive')
        with pytest.raises(OperationalError, match='locked'):
            with engine.connect() as conn:
                conn.execute(text('select value from facts'))
        waiter = next(row for row in reversed(obs.snapshot()['recent']) if row['kind'] == 'lock_error')
        assert not waiter['write_seen']  # The failed reader is not asserted to be the lock owner.
        holder.rollback()
        for _ in range(80):
            with engine.connect() as conn:
                assert conn.execute(text('select value from facts')).scalar_one() == 'private-retention-fixture'
        result = obs.snapshot()
        assert len(result['recent']) == result['capacity'] == 64
        assert all(row['kind'] != 'lock_error' and row['connection_use'] != written['connection_use']
                   for row in result['recent'])
        assert any(row['connection_use'] == waiter['connection_use'] for row in result['lock_errors'])
        assert any(row['connection_use'] == written['connection_use'] for row in result['longest_write_holds'])
        for key in ('recent', 'lock_errors', 'longest_write_holds'):
            for row in result[key]:
                assert datetime.fromisoformat(row['observed_at']).utcoffset() == timedelta(0)
        assert 'connection hold is not measured lock time' in result['basis']
        assert 'private-retention-fixture' not in json.dumps(result)
        assert 'select value' not in json.dumps(result)
        assert 'insert into' not in json.dumps(result)
    finally:
        if holder is not None:
            holder.rollback()
            holder.close()
        engine.dispose()


def test_dedicated_retention_is_bounded_and_keeps_the_longest_write_holds(isolated_observations):
    for value in range(100):
        obs._record({'id': value, 'owner': 'external_or_unknown'}, 'lock_error',
                    duration_ms=value, error='OperationalError')
    for value in range(100):
        obs._record({'id': 100 + value, 'owner': 'external_or_unknown', 'write_seen': True},
                    'connection_hold', duration_ms=1000-value)
    for value in range(100):
        obs._record({'id': 200 + value, 'owner': 'external_or_unknown'}, 'connection_hold', duration_ms=2000)
    result = obs.snapshot()
    assert len(result['recent']) == result['capacity'] == 64
    assert len(result['lock_errors']) == result['lock_error_capacity'] == 32
    assert [row['connection_use'] for row in result['lock_errors']] == list(range(68, 100))
    assert len(result['longest_write_holds']) == result['write_hold_capacity'] == 16
    assert [row['duration_ms'] for row in result['longest_write_holds']] == list(range(1000, 984, -1))
    assert all(row['write_seen'] and row['kind'] == 'connection_hold' for row in result['longest_write_holds'])


def test_snapshot_rows_are_copies_not_mutable_internal_observations(isolated_observations):
    obs._record({'id': 501, 'owner': 'external_or_unknown', 'write_seen': True},
                'connection_hold', duration_ms=400)
    obs._record({'id': 502, 'owner': 'external_or_unknown'}, 'lock_error', duration_ms=20,
                error='OperationalError')
    first = obs.snapshot()
    first['recent'][0]['owner'] = 'changed_by_consumer'
    assert obs.snapshot()['recent'][0]['owner'] == 'external_or_unknown'
    for key in ('recent', 'lock_errors', 'longest_write_holds'):
        first[key][0]['duration_ms'] = -1
        first[key].clear()
    again = obs.snapshot()
    assert all(again[key] and all(row['duration_ms'] >= 0 for row in again[key])
               for key in ('recent', 'lock_errors', 'longest_write_holds'))


def test_connection_reuse_does_not_carry_write_state_and_ring_is_bounded(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path/'observed.db'}", pool_size=1)
    monkeypatch.setattr(obs, '_SLOW_MS', 0)
    obs.install(engine)
    obs.install(engine)  # Must not double-register callbacks.
    with engine.begin() as conn:
        conn.execute(text('create table facts (value text)'))
        conn.execute(text('insert into facts values (:value)'), {'value':'private-test-contents'})
    written = obs.snapshot()['recent'][-1]
    assert written['kind'] == 'connection_hold' and written['write_seen']
    for _ in range(40):
        with engine.connect() as conn:
            assert conn.execute(text('select value from facts')).scalar_one() == 'private-test-contents'
    read = obs.snapshot()['recent'][-1]
    assert not read['write_seen'] and read['connection_use'] != written['connection_use']
    assert len(obs.snapshot()['recent']) == 64
    assert 'private-test-contents' not in json.dumps(obs.snapshot())
    assert 'select value' not in json.dumps(obs.snapshot())
    engine.dispose()


def test_real_sqlite_lock_error_remains_error_and_identifies_the_waiter(tmp_path):
    path = tmp_path/'locked.db'
    holder = sqlite3.connect(path)
    holder.execute('create table facts (value text)')
    holder.commit()
    engine = create_engine(f'sqlite:///{path}', connect_args={'timeout':.01})
    obs.install(engine)
    holder.execute('begin exclusive')
    try:
        with pytest.raises(OperationalError, match='locked'):
            with engine.connect() as conn:
                conn.execute(text('select * from facts'))
        observation = next(r for r in reversed(obs.snapshot()['recent']) if r['kind']=='lock_error')
        assert observation['duration_ms'] >= 0
        assert observation['error_type']=='OperationalError'
        assert not observation['write_seen']
    finally:
        holder.rollback()
        holder.close()
        engine.dispose()
