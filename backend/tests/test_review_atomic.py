"""Report persistence fault injection; SQLite remains the current-version authority."""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.watchlist import Base
from app.review import storage
from app.review.models import ReviewReportRow
from app.review.schemas import ReviewData, ReviewReport, ModelUsage


def report(title='first'):
    return ReviewReport(trade_date='20990201', review_id=f'RV-20990201-{title}',
                        model=ModelUsage(requested='rules', actual='rules'),
                        data=ReviewData(trade_date='20990201', market={'trade_date':'20990201'},
                                        trading={'trade_date':'20990201'}), summary=title)


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def row(sf):
    with sf() as db:
        return db.execute(select(ReviewReportRow).where(
            ReviewReportRow.trade_date == '20990201')).scalar_one()


def test_failed_db_commit_preserves_previously_published_file(monkeypatch, sf):
    storage.save_report(sf, report())
    old = row(sf)
    path = Path(old.report_path)
    original = path.read_bytes()
    def broken():
        db = sf()
        db.commit = lambda: (_ for _ in ()).throw(RuntimeError('commit injected'))
        return db
    with pytest.raises(RuntimeError, match='commit injected'):
        storage.save_report(broken, report('second'))
    assert row(sf).payload == old.payload
    assert path.read_bytes() == original
    assert storage.get_report(sf, '20990201').summary == 'first'


def test_report_versions_remain_distinct_and_old_file_is_not_overwritten(sf):
    storage.save_report(sf, report())
    first = row(sf)
    storage.save_report(sf, report('second'))
    second = row(sf)
    assert second.report_path != first.report_path
    assert json.loads(Path(first.report_path).read_text())['summary'] == 'first'
    assert json.loads(Path(second.report_path).read_text())['summary'] == 'second'


def test_file_failure_keeps_current_database_report(monkeypatch, sf):
    storage.save_report(sf, report())
    old = row(sf)
    real_replace = storage.os.replace
    def fail_replace(src, dst):
        if Path(dst).is_relative_to(storage.REPORT_DIR):
            raise OSError('disk injected')
        return real_replace(src, dst)
    monkeypatch.setattr(storage.os, 'replace', fail_replace)
    with pytest.raises(OSError, match='disk injected'):
        storage.save_report(sf, report('second'))
    assert row(sf).payload == old.payload
    assert not list(storage.REPORT_DIR.rglob('*.tmp'))


def test_missing_projection_rebuilds_only_committed_payload(sf):
    storage.save_report(sf, report())
    Path(row(sf).report_path).unlink()
    path = storage.repair_report_projection(sf, '20990201')
    assert json.loads(path.read_text())['summary'] == 'first'
    assert str(path) == row(sf).report_path


def test_generated_action_cannot_claim_it_has_been_implemented(sf):
    from app.review.schemas import ActionItem
    generated = report()
    generated.action_items = [ActionItem(title='新建议', category='process', expected_impact='待验证',
        evidence='只有提案', status='applied', resolution_note='模型声称已发布')]
    storage.save_report(sf, generated)
    saved = storage.get_report(sf, generated.trade_date).action_items[0]
    assert saved.status == 'pending'
    assert saved.resolution_note == ''
    current = row(sf)
    for raw in (json.loads(current.payload), json.loads(Path(current.report_path).read_text())):
        assert raw['action_items'][0]['status'] == 'pending'
        assert raw['action_items'][0]['resolution_note'] == ''


def test_disposition_details_survive_regeneration_and_are_visible_in_report(sf):
    from app.review.schemas import ActionItem
    generated = report()
    generated.action_items = [ActionItem(title='改进', category='process', expected_impact='待验证', evidence='证据')]
    storage.save_report(sf, generated)
    item = storage.get_report(sf, generated.trade_date).action_items[0]
    note = '实施：PR #12；上线版本：abc；复测：仍需真实交易窗口'
    storage.update_action_item_status(sf, item.id, 'applied', note,
        implementation_evidence={'implementation_ref':'PR #12','release_version':'abc','verification_ref':'工程已复验；实盘效果待观察'},
        expect_trade_date=generated.trade_date, expect_category='process', expect_title='改进')
    generated.review_id += '-again'
    storage.save_report(sf, generated)
    item = storage.get_report(sf, generated.trade_date).action_items[0]
    assert item.status == 'applied' and item.resolution_note == note
    assert item.implementation_evidence['release_version'] == 'abc'
    assert item.resolved_at and item.disposition_basis == 'management_record_not_release_verification'


def test_mismatched_disposition_identity_cannot_show_or_operate_old_claim(sf):
    from app.review.schemas import ActionItem
    from app.review.models import ReviewActionItemRow
    generated = report()
    generated.action_items = [ActionItem(title='建议', category='process', expected_impact='待验证', evidence='证据')]
    storage.save_report(sf, generated)
    with sf() as db:
        item = db.scalars(select(ReviewActionItemRow)).one()
        item.title = '另一条建议'
        item.status = 'applied'
        db.commit()
    saved = storage.get_report(sf, generated.trade_date).action_items[0]
    assert saved.id == '' and saved.status == 'pending'
    assert saved.disposition_basis == 'disposition_unavailable'


def test_applied_requires_all_implementation_release_and_verification_references(sf):
    from app.review.schemas import ActionItem
    generated = report()
    generated.action_items = [ActionItem(title='改进', category='process', expected_impact='待验证', evidence='证据')]
    storage.save_report(sf, generated)
    item = storage.get_report(sf, generated.trade_date).action_items[0]
    for evidence in ({}, {'implementation_ref':'PR #12'}, {'implementation_ref':'PR #12','release_version':'abc','verification_ref':' '}):
        with pytest.raises(ValueError, match='登记实施需要'):
            storage.update_action_item_status(sf, item.id, 'applied', '声称完成', implementation_evidence=evidence,
                expect_trade_date=generated.trade_date, expect_category='process', expect_title='改进')
    assert storage.get_report(sf, generated.trade_date).action_items[0].status == 'pending'
