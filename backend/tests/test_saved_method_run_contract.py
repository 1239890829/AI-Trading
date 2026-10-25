"""Stored method projections preserve decisions and distinguish corrupt from empty."""
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionRun, OpportunityDecisionSnapshot
from app.models.watchlist import Base
from app.picks.opportunity_learning import build_intraday_records
from app.picks.strategy_registry import saved_method_runs


@pytest.fixture
def sf(tmp_path, monkeypatch):
    monkeypatch.setattr('app.core.bjtime.beijing_now', lambda: datetime(2026, 10, 25, 12, tzinfo=BJ_TZ))
    engine = create_engine(f"sqlite:///{tmp_path / 'methods.db'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def test_real_builder_gate_decisions_remain_passed_unknown_and_rejected(sf):
    decisions = ['passed', 'unknown', 'rejected', 'blocked']
    payload = {'themes': [{'theme': '契约样本', '_candidate_audit': [
        {'symbol': f'60000{i}', 'hard_gate_decision': decision,
         'candidate_decision': 'unknown', 'reason': f'原理由{i}'}
        for i, decision in enumerate(decisions)
    ]}]}
    run_id, records = build_intraday_records(payload, trade_date='2026-10-23', as_of=datetime(2026, 10, 23, 10))
    with sf() as db:
        db.add(OpportunityDecisionRun(run_id=run_id, trade_date='2026-10-23', as_of=datetime(2026, 10, 23, 10),
            scenario='intraday_opportunity', strategy_version='s1', feature_version='f1', evidence_digest='fixture'))
        for i, record in enumerate(records):
            db.add(OpportunityDecisionSnapshot(**{**record, 'snapshot_id': f'snapshot-{i}',
                'evidence': json.dumps(record['evidence'])}))
        db.commit()
    run = saved_method_runs(sf)['intraday_watch']
    assert [row['decision'] for row in run['items']] == decisions
    assert [row['state'] for row in run['items']] == ['candidate', 'unknown', 'rejected', 'rejected']
    assert [row['reason'] for row in run['items']] == [f'原理由{i}' for i in range(4)]
    assert all(row['stage'] == 'hard_gate' for row in run['items'])


@pytest.mark.parametrize('items,rejected,expected_state,counts', [
    ('not-json', '[]', 'partial', (None, 0)),
    ('[]', '[]', 'saved', (0, 0)),
    ('null', '{}', 'error', (None, None)),
    ('[{"symbol":"600000","score":0},null]', '[]', 'partial', (None, 0)),
])
def test_invalid_lists_never_become_known_zero(sf, items, rejected, expected_state, counts):
    with sf() as db:
        db.add(DailyPickSet(date='2026-10-23', items=items, rejected=rejected, meta='{}'))
        db.commit()
    run = saved_method_runs(sf)['daily_picks']
    assert run['state'] == expected_state
    assert (run['counts']['selected'], run['counts']['rejected_saved']) == counts
    if expected_state != 'saved':
        assert 'JSON 损坏或结构不符' in run['reason']
    if '600000' in items:
        assert run['items'][0]['score'] == 0


def test_corrupt_metadata_retains_readable_rows_but_not_complete_state(sf):
    with sf() as db:
        db.add(DailyPickSet(date='2026-10-23', items='[{"symbol":"600000"}]', meta='not-json'))
        db.commit()
    run = saved_method_runs(sf)['daily_picks']
    assert run['state'] == 'partial' and '运行元数据' in run['reason']
    assert run['counts']['selected'] == 1 and run['counts']['candidates'] is None
    assert run['source_version'] is None


def test_corrupt_rank_evidence_preserves_unknown_not_zero_selection(sf):
    with sf() as db:
        db.add(OpportunityDecisionRun(run_id='broken', trade_date='2026-10-23', as_of=datetime(2026, 10, 23, 10),
            scenario='intraday_opportunity', strategy_version='s1', feature_version='f1', evidence_digest='fixture'))
        db.add(OpportunityDecisionSnapshot(snapshot_id='broken-row', run_id='broken', trade_date='2026-10-23',
            as_of=datetime(2026, 10, 23, 10), scenario='intraday_opportunity', stage='rank', symbol='600000',
            decision='ranked', rank=1, strategy_version='s1', feature_version='f1', evidence='not-json'))
        db.commit()
    run = saved_method_runs(sf)['intraday_watch']
    assert run['state'] == 'partial' and run['counts']['selected'] is None
    assert run['items'][0]['state'] == 'unknown'
    assert run['items'][0]['decision'] == 'ranked'
