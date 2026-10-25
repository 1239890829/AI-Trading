"""A/B cohorts use original joint identities, before cross-source deduplication."""
from datetime import date, datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.bjtime import BJ_TZ
from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionRun, OpportunityDecisionSnapshot, OpportunityOutcomeLabel
from app.models.watchlist import Base
from app.picks.opportunity_learning import PRICE_BASIS_VERSION
from app.picks.selection_entry import CONTRACT, archive_daily_observations
from app.picks.validation import selection_rows, validation_report


@pytest.fixture
def sf(monkeypatch):
    monkeypatch.setattr('app.picks.validation.beijing_now', lambda: datetime(2026, 10, 25, 12, tzinfo=BJ_TZ))
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def daily(sf, day, *, method='same', feature='f1', result=None, empty=False):
    entry = dict(state='recorded', selected_at=f'{day}T10:00:00+08:00',
        quote_as_of=f'{day}T09:59:59+08:00', reference_price=10.0, source_version=f'daily-{day}',
        strategy_version=method, feature_version=feature)
    entries = {} if empty else {'600000': entry}
    with sf() as db:
        db.add(DailyPickSet(date=day, items='[]' if empty else '[{"symbol":"600000"}]',
            meta=json.dumps({'selection_entries':entries, 'selection_contract':{'strategy_version':method,'feature_version':feature}})))
        db.commit()
    if entries:
        archive_daily_observations(day, entries, session_factory=sf)
        with sf() as db:
            snapshot = db.scalar(select(OpportunityDecisionSnapshot).where(OpportunityDecisionSnapshot.trade_date==day))
            if result is not None:
                db.add(OpportunityOutcomeLabel(snapshot_id=snapshot.snapshot_id, horizon='d1_close',
                    target_date=(date.fromisoformat(day)+timedelta(days=1)).isoformat(), state='labeled', return_pct=result,
                    reference_price=10.0, price_basis_version=PRICE_BASIS_VERSION))
                db.commit()


def intraday(sf, day, *, method='same', feature='f1', result=5.0, suffix='', hour=10):
    at = datetime.fromisoformat(f'{day}T10:00:00').replace(hour=hour)
    with sf() as db:
        db.add(OpportunityDecisionRun(run_id=f'intra-{day}{suffix}',trade_date=day,as_of=at,scenario='intraday_opportunity',
            strategy_version=method,feature_version=feature,data_state='ready',rank_rows=1,evidence_digest='fixture'))
        db.add(OpportunityDecisionSnapshot(snapshot_id=f'snap-{day}{suffix}',run_id=f'intra-{day}{suffix}',trade_date=day,as_of=at,
            created_at=at.replace(tzinfo=BJ_TZ).astimezone(timezone.utc).replace(tzinfo=None),
            scenario='intraday_opportunity',stage='rank',decision='ranked',rank=1,
            symbol='600000',entry_price=10.0,strategy_version=method,feature_version=feature,data_state='ready',
            evidence=json.dumps({'selection_entry_contract':CONTRACT,'within_display_capacity':True,'effective_limit':8})))
        if result is not None:
            db.add(OpportunityOutcomeLabel(snapshot_id=f'snap-{day}{suffix}',horizon='d1_close',target_date=(date.fromisoformat(day)+timedelta(days=1)).isoformat(),
                state='labeled',return_pct=result,reference_price=10.0,price_basis_version=PRICE_BASIS_VERSION))
        db.commit()


def read(sf, scope='all', horizon='d1_close'):
    return validation_report('2026-10-12','2026-10-15',scope=scope,horizon=horizon,session_factory=sf,
        calendar=[date(2026,10,n) for n in range(12,17)])


def test_joint_scope_identity_preserves_both_sources_before_all_dedup(sf):
    daily(sf,'2026-10-12',result=0.0)
    intraday(sf,'2026-10-12',result=5.0)
    report = read(sf)
    assert report['selection']['count'] == 1 and report['selection']['mean_pct'] == 0.0
    groups = {row['scope']:row for row in report['version_groups']}
    assert groups['daily']['key'] != groups['intraday']['key']
    assert groups['daily']['selection']['mean_pct'] == 0.0
    assert groups['intraday']['selection']['mean_pct'] == 5.0
    assert len(report['version_entries']) == 2
    assert report['entries'][0]['selection_refs'] == ['daily:2026-10-12:600000','intraday:2026-10-12:600000']
    assert report['actual']['denominator'] == 0 and report['actual']['win_rate'] is None
    assert {row['scope'] for row in read(sf,scope='daily')['version_groups']} == {'daily'}
    assert {row['scope'] for row in read(sf,scope='intraday')['version_groups']} == {'intraday'}


def test_feature_and_unknown_versions_do_not_merge_or_gain_current_identity(sf):
    daily(sf,'2026-10-12',feature='f1',result=0.0)
    daily(sf,'2026-10-13',feature='f2',result=None)
    daily(sf,'2026-10-14',method=None,feature=None,result=None)
    intraday(sf,'2026-10-14',method='',feature='',result=None)
    groups = read(sf)['version_groups']
    assert len(groups) == 4
    missing = [row for row in groups if row['version_state']=='missing']
    assert {row['scope'] for row in missing} == {'daily','intraday'}
    assert all(row['method_version'] is None and row['feature_version'] is None for row in missing)
    f2 = next(row for row in groups if row['feature_version']=='f2')
    assert f2['selection']['observed']==0 and f2['selection']['missing']==1
    assert f2['selection']['mean_pct'] is None and f2['window']['observed_start'] is None
    assert read(sf,horizon='d3_close')['version_groups'][0]['selection']['observed'] == 0


def test_same_scope_same_day_method_and_feature_changes_survive_only_in_version_reader(sf):
    intraday(sf,'2026-10-12',method='m1',feature='f1',result=0.0)
    intraday(sf,'2026-10-12',method='m1',feature='f2',result=5.0,suffix='-feature',hour=11)
    intraday(sf,'2026-10-12',method='m2',feature='f2',result=-5.0,suffix='-method',hour=12)
    report=read(sf)
    # This assertion turns red with the former first-day/symbol-only reader.
    assert {(g['method_version'],g['feature_version'],g['selection']['mean_pct']) for g in report['version_groups']}=={
        ('m1','f1',0.0),('m1','f2',5.0),('m2','f2',-5.0)}
    with sf() as db:
        original = selection_rows(db,'2026-10-12','2026-10-15')
        versioned = selection_rows(db,'2026-10-12','2026-10-15',preserve_versions=True)
    assert len(original)==1 and original[0]['version']=='intra-2026-10-12'
    assert original[0]['id']=='intraday:2026-10-12:600000'
    assert len(versioned)==3
    assert report['selection']['count']==1 and report['selection']['mean_pct']==0.0
    assert report['entries'][0]['selection_refs']==['intraday:2026-10-12:600000']
    assert len(report['version_entries'])==3
    assert len({row['id'] for row in report['version_entries']})==3


def test_only_saved_empty_days_become_version_zero_days(sf):
    daily(sf,'2026-10-12',empty=True)
    with sf() as db:
        db.add(DailyPickSet(date='2026-10-13',items='broken',meta='{}'))
        db.commit()
    group, = read(sf)['version_groups']
    assert group['daily'] == [dict(date='2026-10-12',count=0,observed=0,mean_pct=None,median_pct=None)]
    assert group['selection']['count']==0 and group['selection']['mean_pct'] is None
    assert group['window']['observed_days']==0


def test_route_preserves_group_identity_and_conditional_revision(sf,monkeypatch):
    import asyncio
    from app.api.routes import selection_validation as route
    daily(sf,'2026-10-12',result=0.0)
    report = read(sf)
    monkeypatch.setattr(route,'read_validation',lambda *_args,**_kwargs:(report,'a'*64))
    response = asyncio.run(route.report(start='2026-10-12',end='2026-10-15',horizon='d1_close',scope='all',known_revision=None))
    assert response['data']['version_groups'][0]['selection']['mean_pct']==0.0
    assert response['meta']['version_identity']==['scope','method_version','feature_version']
    unchanged = asyncio.run(route.report(start='2026-10-12',end='2026-10-15',horizon='d1_close',scope='all',known_revision='a'*64))
    assert unchanged['data'] is None and unchanged['meta']['not_modified'] is True
