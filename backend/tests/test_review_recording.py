"""A document archive is not implementation of the recommendation it describes."""
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.watchlist import Base
from app.models.agent import AgentAudit
from app.review.models import ReviewActionItemRow
from app.services.evolution import _sync_review_items


@pytest.fixture
def sf():
    engine = create_engine('sqlite://', poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def add_item(sf, status='pending'):
    with sf() as db:
        row = ReviewActionItemRow(review_id='r1', trade_date='20261015', title='核对策略偏移',
                                  category='strategy', priority='P1', status=status,
                                  resolution_note='人工保留' if status != 'pending' else '')
        db.add(row)
        db.commit()
        return row.id


@pytest.mark.parametrize('prior', ['pending', 'confirmed', 'rejected', 'applied', 'reverted'])
def test_archive_does_not_claim_implementation_or_overwrite_human_disposition(sf, prior):
    key = add_item(sf, prior)
    meta = {'id': str(key), 'category': 'strategy', 'title': '核对策略偏移'}
    agenda = {'inputs': {'review': {'available': True, 'trade_date': '20261015', 'action_items': [meta]}}}
    _sync_review_items(agenda, [{'class': 'B', 'status': 'executed', 'review_item': meta,
                               'result': '已写入 docs/' 'evolution/2026-10-14.md'}], sf)
    with sf() as db:
        row = db.get(ReviewActionItemRow, key)
        assert row.status == ('recorded' if prior == 'pending' else prior)
        if prior != 'pending':
            assert row.resolution_note == '人工保留'
        if prior == 'pending':
            assert '尚待实施' in row.resolution_note
            log = db.execute(select(AgentAudit).where(AgentAudit.action == 'review.disposition')).scalar_one()
            assert json.loads(log.before)['status'] == 'pending'
            assert json.loads(log.after)['status'] == 'recorded'


def test_exact_archive_repair_is_audited_idempotent_and_rejects_plan_drift(sf):
    from app.review.archive_repair import repair_archive_dispositions
    key = add_item(sf, 'applied')
    other = add_item(sf, 'applied')
    with sf() as db:
        db.get(ReviewActionItemRow, key).resolution_note = 'AI 大脑自动执行：已写入 docs/' 'evolution/2026-10-14.md'
        db.commit()
    plan = repair_archive_dispositions(sf)
    assert [i['id'] for i in plan['items']] == [key] and plan['changed'] == 0
    with pytest.raises(ValueError, match='未写入'):
        repair_archive_dispositions(sf, expected_digest='wrong')
    out = repair_archive_dispositions(sf, expected_digest=plan['digest'])
    assert out['changed'] == 1
    with sf() as db:
        assert db.get(ReviewActionItemRow, key).status == 'recorded'
        assert db.get(ReviewActionItemRow, other).status == 'applied'
        audit = db.execute(select(AgentAudit).where(AgentAudit.action == 'review.archive_repair')).scalar_one()
        assert json.loads(audit.before)['note'].startswith('AI 大脑自动执行')
    assert repair_archive_dispositions(sf)['items'] == []


def test_archive_repair_restore_preserves_later_human_disposition(sf):
    from app.review.archive_repair import repair_archive_dispositions, restore_archive_dispositions
    key = add_item(sf, 'applied')
    original = 'AI 大脑自动执行：已写入 docs/' 'evolution/2026-10-14.md'
    with sf() as db:
        db.get(ReviewActionItemRow, key).resolution_note = original; db.commit()
    digest = repair_archive_dispositions(sf)['digest']
    repair_archive_dispositions(sf, expected_digest=digest)
    assert restore_archive_dispositions(sf, digest)['restored'] == 1
    with sf() as db:
        assert db.get(ReviewActionItemRow, key).resolution_note == original
    repair_archive_dispositions(sf, expected_digest=digest)
    with sf() as db:
        db.get(ReviewActionItemRow, key).status = 'confirmed'; db.commit()
    with pytest.raises(ValueError, match='已有变化'):
        restore_archive_dispositions(sf, digest)
    with sf() as db:
        assert db.get(ReviewActionItemRow, key).status == 'confirmed'
