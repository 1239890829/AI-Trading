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


def test_daily_rejected_reason_uses_producer_fields_not_technical_score(sf):
    rejected = [
        {'symbol': '600000', 'score': 47.2, 'tech': 61.1, 'selection_reason': 'below_min_score',
         'score_shortfall': 2.8, 'bases': {'tech': '原技术依据'}, 'vetoes': []},
        {'symbol': '600001', 'tech': 80, 'selection_reason': 'capacity_or_replacement', 'score_shortfall': 0},
        {'symbol': '600002', 'reason': 99, 'tech': 61.1, 'selection_reason': 'future_reason'},
        {'symbol': '600003', 'reason': '原文本理由', 'selection_reason': 'below_min_score', 'score_shortfall': 1},
        {'symbol': '600004', 'tech': 61.1, 'selection_reason': 'below_min_score'},
    ]
    with sf() as db:
        db.add(DailyPickSet(date='2026-10-23', items='[{"symbol":"600005","bases":{"tech":"原评分依据"}}]',
            rejected=json.dumps(rejected), meta='{"min_pick_score":99}'))
        db.commit()
    rows = saved_method_runs(sf)['daily_picks']['items']
    assert rows[0]['reason'] == '' and rows[0]['basis'] == {'tech': '原评分依据'}
    assert rows[1]['reason'] == '低于原入选分线；原记录差额 2.8 分'
    assert rows[1]['score'] == 47.2 and rows[1]['basis'] == {'tech': '原技术依据'}
    assert rows[2]['reason'] == '原记录为容量或换股约束；具体原因未区分'
    assert rows[3]['reason'] == '原入选原因未识别（future_reason）'
    assert rows[4]['reason'] == '原文本理由'
    assert rows[5]['reason'] == '低于原入选分线；原差额未记录'
    assert all('61.1' not in row['reason'] for row in rows)


def test_rank_rows_remain_visible_after_large_hard_gate_archive(sf):
    with sf() as db:
        db.add(OpportunityDecisionRun(run_id='large', trade_date='2026-10-23', as_of=datetime(2026, 10, 23, 10),
            scenario='intraday_opportunity', strategy_version='s1', feature_version='f1', evidence_digest='fixture',
            candidate_rows=477, hard_gate_rows=477, rank_rows=8))
        common = dict(run_id='large', trade_date='2026-10-23', as_of=datetime(2026, 10, 23, 10),
            scenario='intraday_opportunity', strategy_version='s1', feature_version='f1')
        for i in range(210):
            db.add(OpportunityDecisionSnapshot(**common, snapshot_id=f'hard-{i}', stage='hard_gate',
                symbol=str(600000+i), decision='passed', evidence='{}'))
        db.flush()
        for i in range(8):
            db.add(OpportunityDecisionSnapshot(**common, snapshot_id=f'rank-{i}', stage='rank',
                symbol=str(600000+i), decision='ranked', rank=i+1,
                evidence=json.dumps({'effective_limit':5,'within_display_capacity':i<5})))
        db.commit()
    run = saved_method_runs(sf)['intraday_watch']
    assert len(run['items']) == 200 and run['possibly_truncated'] is True
    assert [row['stage'] for row in run['items'][:8]] == ['rank'] * 8
    assert [row['symbol'] for row in run['items'] if row['state']=='selected'] == [str(600000+i) for i in range(5)]
    assert run['counts'] == {'candidate_rows':477,'hard_gate_rows':477,'rank_rows':8,'selected':5}
    with sf() as db:
        db.get(OpportunityDecisionRun, 'large').rank_rows = 9
        db.commit()
    assert saved_method_runs(sf)['intraday_watch']['counts']['selected'] is None
