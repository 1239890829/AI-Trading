"""Roundtrip persistent facts and fail-closed restore boundaries, never production data."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import create_engine

from app.core import db as core_db
from app.core.recovery import HOLD, backup, restore, sqlite_inventory, verify


def seed(tmp_path):
    root = tmp_path / 'source'
    p = root / 'data' / 'ashare.db'
    p.parent.mkdir(parents=True)
    with sqlite3.connect(p) as c:
        c.execute('CREATE TABLE facts(id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, value BLOB)')
        c.executemany('INSERT INTO facts(scope,value) VALUES(?,?)', [('main', b'\x00\xff'), ('hunting-shadow', None)])
        c.execute('CREATE TABLE pending(id TEXT PRIMARY KEY, state TEXT, lease TEXT)')
        c.execute("INSERT INTO pending VALUES('old-intent','sending','old-lease')")
        c.execute('CREATE TABLE notification_read_state(seen_before INTEGER, clear_before INTEGER)')
        c.execute('INSERT INTO notification_read_state VALUES(123,100)')
    file = root / 'backend/data/position_plans/2026-09-30.json'
    file.parent.mkdir(parents=True)
    file.write_text('{"pending":true}')
    return root


def test_roundtrip_preserves_schema_rows_sequences_watermarks_and_pending(tmp_path, monkeypatch):
    source = seed(tmp_path)
    before = sqlite_inventory(source / 'data/ashare.db')
    bundle, restored = tmp_path / 'bundle', tmp_path / 'restored'
    manifest = backup(source, bundle, quiescent=True, code_head='frozen-code')
    assert len(manifest['files']) == 2
    assert verify(bundle) == manifest
    receipt = restore(bundle, restored)
    assert receipt['state'] == 'restored_on_hold'
    assert receipt['production_rpo_seconds'] is None
    assert sqlite_inventory(restored / 'data/ashare.db') == before
    assert sqlite_inventory(source / 'data/ashare.db') == before
    assert (restored / 'backend/data/position_plans/2026-09-30.json').read_text() == '{"pending":true}'
    marker = restored / 'data' / HOLD
    assert json.loads(marker.read_text())['outbound'] == 'disabled'
    engine = create_engine('sqlite:///' + str(restored / 'data/ashare.db'))
    monkeypatch.setattr(core_db, '_engine', engine)
    with pytest.raises(RuntimeError, match='on hold'):
        core_db.get_engine()
    # Only explicit operator release in this isolated fixture permits subsequent writes.
    marker.unlink()
    try:
        assert core_db.get_engine() is engine
        with sqlite3.connect(restored / 'data/ashare.db') as c:
            c.execute("INSERT INTO facts(scope) VALUES('shadow')")
            assert c.execute('SELECT max(id) FROM facts').fetchone()[0] == 3
            assert c.execute('SELECT state,lease FROM pending').fetchone() == ('sending', 'old-lease')
            assert c.execute('SELECT seen_before,clear_before FROM notification_read_state').fetchone() == (123,100)
    finally:
        engine.dispose()


def test_unstarted_engine_hold_prevents_creation_or_migration(tmp_path, monkeypatch):
    p = tmp_path / 'missing.db'
    (tmp_path / HOLD).write_text('{}')
    monkeypatch.setattr(core_db, '_engine', None)
    monkeypatch.setattr(core_db.settings, 'database_url', 'sqlite:///' + str(p))
    with pytest.raises(RuntimeError, match='on hold'):
        core_db.get_engine()
    assert not p.exists()


def test_quiescent_required_and_existing_destination_not_touched(tmp_path):
    source = seed(tmp_path)
    bundle = tmp_path / 'bundle'
    with pytest.raises(ValueError, match='stopped'):
        backup(source, bundle, quiescent=False, code_head='x')
    assert not bundle.exists()
    bundle.mkdir()
    (bundle / 'user-file').write_text('keep')
    with pytest.raises(FileExistsError):
        backup(source, bundle, quiescent=True, code_head='x')
    assert (bundle / 'user-file').read_text() == 'keep'


@pytest.mark.parametrize('mutation', ['corrupt', 'missing', 'extra', 'escape', 'duplicate', 'unknown'])
def test_untrusted_or_damaged_bundle_rejected_before_restore(tmp_path, mutation):
    source = seed(tmp_path)
    bundle, dest = tmp_path / 'bundle', tmp_path / 'restored'
    backup(source, bundle, quiescent=True, code_head='x')
    manifest = json.loads((bundle / 'manifest.json').read_text())
    p = bundle / 'payload' / manifest['files'][0]['path']
    if mutation == 'corrupt':
        p.write_bytes(b'corrupt')
    elif mutation == 'missing':
        p.unlink()
    elif mutation == 'extra':
        (bundle / 'payload/data/extra.json').write_text('{}')
    elif mutation == 'escape':
        manifest['files'][0]['path'] = '../escape'
    elif mutation == 'duplicate':
        manifest['files'].append(manifest['files'][0])
    else:
        manifest['version'] = 2
    (bundle / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        restore(bundle, dest)
    assert not dest.exists()


def test_source_and_destination_symlinks_rejected(tmp_path):
    source = seed(tmp_path)
    outside = tmp_path / 'outside'
    outside.write_text('private')
    (source / 'data/link').symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        backup(source, tmp_path / 'bundle', quiescent=True, code_head='x')
    (source / 'data/link').unlink()
    linked = tmp_path / 'linked'
    linked.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        backup(source, linked / 'bundle', quiescent=True, code_head='x')


def test_source_mutation_discards_partial_backup(tmp_path, monkeypatch):
    import app.core.recovery as mod
    source = seed(tmp_path)
    original = mod.shutil.copyfile
    def moving(src, dst):
        result = original(src, dst)
        Path(src).write_text('{"changed":true}')
        return result
    monkeypatch.setattr(mod.shutil, 'copyfile', moving)
    bundle = tmp_path / 'bundle'
    with pytest.raises(ValueError, match='changed'):
        backup(source, bundle, quiescent=True, code_head='x')
    assert not bundle.exists()


def test_sqlite_committed_wal_and_corruption(tmp_path):
    source = seed(tmp_path)
    p = source / 'data/ashare.db'
    with sqlite3.connect(p) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute("INSERT INTO facts(scope) VALUES('wal-committed')")
        c.commit()
        bundle = tmp_path / 'bundle'
        backup(source, bundle, quiescent=True, code_head='x')
        assert sqlite_inventory(bundle / 'payload/data/ashare.db')['tables']['facts']['rows'] == 3
    p.write_bytes(b'not sqlite')
    with pytest.raises(sqlite3.DatabaseError):
        backup(source, tmp_path / 'bad', quiescent=True, code_head='x')
    assert not (tmp_path / 'bad').exists()


def test_duckdb_uncheckpointed_writer_refused(tmp_path):
    source = seed(tmp_path)
    (source / 'data/market.duckdb.wal').write_bytes(b'uncheckpointed')
    with pytest.raises(ValueError, match='DuckDB'):
        backup(source, tmp_path / 'bundle', quiescent=True, code_head='x')


def test_hold_precedes_copy_and_interruption_does_not_allow_start(tmp_path, monkeypatch):
    import app.core.recovery as mod
    source = seed(tmp_path)
    bundle, dest = tmp_path / 'bundle', tmp_path / 'restored'
    backup(source, bundle, quiescent=True, code_head='x')
    original = mod.shutil.copyfile
    def check_hold(src, dst):
        assert (dest / 'data' / HOLD).is_file()
        original(src, dst)
        raise OSError('simulated interrupted copy')
    monkeypatch.setattr(mod.shutil, 'copyfile', check_hold)
    with pytest.raises(OSError, match='interrupted'):
        restore(bundle, dest)
    assert not dest.exists()
    assert verify(bundle)


def test_payload_root_symlink_and_destination_overlap_rejected(tmp_path):
    source = seed(tmp_path)
    bundle = tmp_path / 'bundle'
    backup(source, bundle, quiescent=True, code_head='x')
    with pytest.raises(ValueError, match='overlaps'):
        restore(bundle, bundle / 'restored')
    moved = tmp_path / 'moved-payload'
    (bundle / 'payload').rename(moved)
    (bundle / 'payload').symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        restore(bundle, tmp_path / 'restored')


def test_database_alias_cannot_bypass_hold(tmp_path, monkeypatch):
    real = tmp_path / 'restored'
    real.mkdir()
    p = real / 'ashare.db'
    p.touch()
    (real / HOLD).write_text('{}')
    alias = tmp_path / 'alias.db'
    alias.symlink_to(p)
    monkeypatch.setattr(core_db, '_engine', None)
    monkeypatch.setattr(core_db.settings, 'database_url', 'sqlite:///' + str(alias))
    with pytest.raises(RuntimeError, match='on hold'):
        core_db.get_engine()


def test_restored_business_consumers_can_continue_without_scope_or_watermark_loss(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from app.core.migrations import run_migrations
    from app.models.paper import PaperAccount, PaperOrder
    from app.models.real_position import RealTrade
    from app.paper.engine import PaperTradingEngine
    from app.paper.reconcile import reconcile
    from app.repositories.watchlist_repo import WatchlistRepository
    from app.services.notification_read_state import load_state, save_state
    from app.services.real_position_service import load_positions
    from app.services import agent_params as ap
    source = tmp_path / 'source'
    (source / 'data').mkdir(parents=True)
    original = create_engine('sqlite:///' + str(source / 'data/ashare.db'))
    run_migrations(original)
    sf = sessionmaker(bind=original, expire_on_commit=False)
    WatchlistRepository(sf).add('600127')
    save_state({'seen_before': 300, 'clear_before': 200, 'read_ids': [4]}, session_factory=sf)
    with sf() as s:
        for scope in ('main', 'shadow', 'hunting_shadow'):
            s.add(PaperAccount(scope=scope, initial_cash=10000, cash=8994.99))
            s.add(PaperOrder(scope=scope, symbol='600127', side='buy', price=10, quantity=100, status='pending'))
        s.add(RealTrade(symbol='600127', side='buy', fill_price=10, quantity=100, traded_at='2026-09-30'))
        s.commit()
    import app.services.agent_tasks as at
    monkeypatch.setattr(ap, 'get_session_factory', lambda: sf)
    monkeypatch.setattr(at, 'get_session_factory', lambda: sf)
    change = ap.propose('picks_style_offsets_json', {'发酵': {'echelon': 0.04}}, session_factory=sf)
    original.dispose()
    backup(source, tmp_path / 'bundle', quiescent=True, code_head='x')
    restored = tmp_path / 'restored'
    restore(tmp_path / 'bundle', restored)
    engine = create_engine('sqlite:///' + str(restored / 'data/ashare.db'))
    restored_sf = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(ap, 'get_session_factory', lambda: restored_sf)
    monkeypatch.setattr(at, 'get_session_factory', lambda: restored_sf)
    try:
        assert run_migrations(engine) == 'upgraded'  # explicitly targeted offline migration, no app start
        repo = WatchlistRepository(restored_sf)
        assert repo.list_symbols() == ['600127']
        repo.add('000001'); assert repo.remove('600127')
        assert repo.list_symbols() == ['000001']
        save_state({'seen_before': 100, 'clear_before': 50, 'read_ids': []}, session_factory=restored_sf)
        assert load_state(restored_sf)['clear_before'] == 200
        assert load_state(restored_sf)['seen_before'] == 300
        assert load_positions(restored_sf)[0].quantity == 100
        with restored_sf() as s:
            trade = s.query(RealTrade).one(); trade.quantity = 200; s.commit()
        assert load_positions(restored_sf)[0].quantity == 200
        with restored_sf() as s:
            s.delete(s.query(RealTrade).one()); s.commit()
        assert load_positions(restored_sf) == []
        async def forbidden_quote(_):
            raise AssertionError('cancel must not fetch market data')
        paper = PaperTradingEngine(restored_sf, forbidden_quote, scope='shadow')
        with restored_sf() as s:
            order_id = s.query(PaperOrder).filter_by(scope='shadow').one().id
        assert paper.cancel(order_id).status == 'cancelled'
        with restored_sf() as s:
            assert s.query(PaperOrder).filter_by(scope='main').one().status == 'pending'
            assert s.query(PaperOrder).filter_by(scope='hunting_shadow').one().status == 'pending'
        assert reconcile(restored_sf)['ok']
        ap.apply_change(change['id'], session_factory=restored_sf)
        assert ap.list_changes(session_factory=restored_sf)[0]['status'] == 'applied'
        ap.rollback_change(change['id'], session_factory=restored_sf)
        assert ap.list_changes(session_factory=restored_sf)[0]['status'] == 'rolled_back'
        assert (restored / 'data' / HOLD).exists()  # offline tests do not release application hold
    finally:
        engine.dispose()
        from app.picks import style_router
        style_router.set_override_provider(None)
