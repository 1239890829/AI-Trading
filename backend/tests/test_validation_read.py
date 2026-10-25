"""Freshness of conditional validation reads, including late corrections."""
from datetime import date
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.models.watchlist import Base
from app.models.daily_pick import DailyPickSet
from app.picks import validation_read as reader


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'validation.db'}", pool_size=1, max_overflow=1)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(DailyPickSet(date='2026-10-12', items=json.dumps([{'symbol':'600127','name':'原名称'}]), meta='{}'))
        db.commit()
    yield factory
    engine.dispose()


def read(sf, **kwargs):
    return reader.read_validation('2026-10-12', '2026-10-12', session_factory=sf, calendar=kwargs.pop('calendar', []), **kwargs)


def count_compute(monkeypatch):
    original = reader.validation_report
    calls = []
    def compute(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(reader, 'validation_report', compute)
    return calls


def test_unchanged_revision_reuses_projection_without_sharing_mutable_values(sf, monkeypatch):
    calls = count_compute(monkeypatch)
    first, revision = read(sf)
    first['entries'][0]['name'] = 'caller mutation'
    second, same_revision = read(sf)
    assert len(calls) == 1
    assert revision == same_revision
    assert second['entries'][0]['name'] == '原名称'


@pytest.mark.parametrize('external', [False, True])
def test_old_row_edit_and_delete_invalidate_even_without_new_ids(sf, monkeypatch, external):
    calls = count_compute(monkeypatch)
    first, revision = read(sf)
    engine = create_engine(sf.kw['bind'].url, poolclass=NullPool) if external else sf.kw['bind']
    writer = sessionmaker(bind=engine)
    try:
        with writer() as db:
            row = db.scalar(select(DailyPickSet))
            row.items = json.dumps([{'symbol':'600127','name':'修订名称'}])
            db.commit()
        second, updated = read(sf)
        assert updated != revision
        assert second['entries'][0]['name'] == '修订名称'
        with writer() as db:
            db.delete(db.scalar(select(DailyPickSet)))
            db.commit()
        third, deleted = read(sf)
        assert third['selection']['count'] == 0
        assert deleted != updated and len(calls) == 3
    finally:
        if external:
            engine.dispose()


def test_calendar_and_scope_change_recompute(sf, monkeypatch):
    calls = count_compute(monkeypatch)
    read(sf)
    read(sf, calendar=[date(2026,10,12)])
    read(sf, scope='intraday')
    assert len(calls) == 3


def test_failed_calculation_is_not_cached(sf, monkeypatch):
    original = reader.validation_report
    monkeypatch.setattr(reader, 'validation_report', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('injected')))
    with pytest.raises(RuntimeError, match='injected'):
        read(sf)
    monkeypatch.setattr(reader, 'validation_report', original)
    assert read(sf)[0]['selection']['count'] == 1


def test_commit_during_calculation_never_freezes_a_transitional_result(sf, monkeypatch):
    original = reader.validation_report
    other = create_engine(sf.kw['bind'].url, poolclass=NullPool)
    calls = []
    def compute(*args, **kwargs):
        calls.append(1)
        report = original(*args, **kwargs)
        if len(calls) == 1:
            with sessionmaker(bind=other)() as db:
                row = db.scalar(select(DailyPickSet))
                row.items = json.dumps([{'symbol':'600127','name':'迟到更正'}])
                db.commit()
        return report
    monkeypatch.setattr(reader, 'validation_report', compute)
    try:
        read(sf)
        second, _ = read(sf)
        assert second['entries'][0]['name'] == '迟到更正'
        assert len(calls) == 2
    finally:
        other.dispose()


def test_close_maturity_boundary_invalidates_without_database_write(sf, monkeypatch):
    from datetime import datetime
    from app.core.bjtime import BJ_TZ
    calls = count_compute(monkeypatch)
    monkeypatch.setattr(reader, 'beijing_now', lambda: datetime(2026,10,16,14,59,tzinfo=BJ_TZ))
    read(sf)
    monkeypatch.setattr(reader, 'beijing_now', lambda: datetime(2026,10,16,15,0,tzinfo=BJ_TZ))
    read(sf)
    assert len(calls) == 2


def test_version_identity_correction_invalidates_same_row_revision(sf):
    before, revision = read(sf)
    assert before['version_groups'][0]['method_version'] is None
    with sf() as db:
        row = db.scalar(select(DailyPickSet))
        row.meta = json.dumps({'selection_entries':{'600127':{'strategy_version':'original-m','feature_version':'original-f'}}})
        db.commit()
    after, corrected = read(sf)
    assert revision != corrected
    assert after['version_groups'][0]['method_version']=='original-m'
    assert {group['scope'] for group in read(sf,scope='daily')[0]['version_groups']} == {'daily'}
    assert read(sf,scope='intraday')[0]['version_groups']==[]
