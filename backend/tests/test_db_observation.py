"""Observability never changes SQLite semantics or records SQL/parameters."""
import json
import sqlite3

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.core import db_observation as obs


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
