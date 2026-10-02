"""Offline recovery bundles. No app startup, credentials, writes to sources or sends."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import time
from datetime import datetime, timezone

HOLD = 'RESTORE_HOLD.json'
ROOTS = ('data', 'backend/data')
MAX_BYTES = 8 * 1024**3
MAX_FILES = 100_000


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _relative(name: str) -> Path:
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name or '\x00' in name:
        raise ValueError('unsafe bundle path')
    if p.as_posix() != name or not any(name.startswith(r + '/') for r in ROOTS):
        raise ValueError('path outside declared data roots')
    return Path(*p.parts)


def _safe(root: Path, relative: Path) -> Path:
    p = root
    for part in relative.parts:
        p = p / part
        if p.is_symlink():
            raise ValueError('symlink in recovery path')
    return p


def _json(path: Path, value: object) -> None:
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())


def _sync(path: Path) -> None:
    with path.open('rb') as f:
        os.fsync(f.fileno())


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sqlite_inventory(path: Path) -> dict:
    """Read-only content fingerprints, never return row values or recipient payloads."""
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        integrity = [r[0] for r in db.execute('PRAGMA integrity_check')]
        if integrity != ['ok']:
            raise ValueError('SQLite integrity check failed')
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        tables = {}
        for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            digests = []
            for row in db.execute('SELECT * FROM ' + _quote(name)):
                values = [({'blob': base64.b64encode(v).decode()} if isinstance(v, bytes) else v) for v in row]
                digests.append(hashlib.sha256(json.dumps(values, ensure_ascii=True, separators=(',', ':')).encode()).digest())
            h = hashlib.sha256()
            for digest in sorted(digests):
                h.update(digest)
            tables[name] = {'rows': len(digests), 'sha256': h.hexdigest(),
                            'columns': [list(r) for r in db.execute('PRAGMA table_info(' + _quote(name) + ')')]}
        revision = []
        if 'alembic_version' in tables:
            revision = [r[0] for r in db.execute('SELECT version_num FROM alembic_version ORDER BY version_num')]
        return {'schema_sha256': hashlib.sha256(json.dumps(schema, ensure_ascii=True).encode()).hexdigest(),
                'tables': tables, 'revision': revision,
                'foreign_key_violations': len(db.execute('PRAGMA foreign_key_check').fetchall())}


def _files(root: Path) -> dict[str, tuple[int, int, int, int]]:
    files = {}
    for prefix in ROOTS:
        base = _safe(root, Path(prefix))
        if not base.exists():
            continue
        for p in sorted(base.rglob('*')):
            if p.is_symlink():
                raise ValueError('symlink in source data tree')
            if not p.is_file():
                continue
            name = p.relative_to(root).as_posix()
            # Old recovery copies and OS debris are not current business facts.
            if p.name in {'.DS_Store', '.gitkeep', '.gitignore'} or '.bak-' in p.name:
                continue
            if p.name.endswith(('-wal', '-shm')):
                continue  # SQLite backup includes committed WAL pages.
            if p.name.endswith('.duckdb.wal') and p.stat().st_size:
                raise ValueError('DuckDB writer must close/checkpoint before backup')
            if p.name == '.env' or p.suffix in {'.key', '.pem'} or p.name == HOLD:
                raise ValueError('secret or recovery hold in source data')
            if name == 'backend/data/ashare.db' and p.stat().st_size == 0:
                continue  # Historical unused placeholder, not the configured database.
            st = p.stat()
            files[name] = (st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev)
    if not files:
        raise ValueError('no data to back up')
    if len(files) > MAX_FILES or sum(v[0] for v in files.values()) > MAX_BYTES:
        raise ValueError('recovery bundle exceeds bounded file/byte budget')
    return files


def _destination(source: Path, destination: Path) -> Path:
    # Check unresolved components before resolve so a symlink cannot hide an alias.
    destination = destination.absolute()
    _safe(Path(destination.anchor), Path(*destination.parts[1:]))
    destination = destination.resolve()
    if destination == source or source.is_relative_to(destination):
        raise ValueError('destination overlaps source')
    for prefix in ROOTS:
        if destination.is_relative_to(source / prefix):
            raise ValueError('destination inside source data')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)  # exclusive reservation, never overwrite an existing directory
    return destination


def backup(source: Path, destination: Path, *, quiescent: bool, code_head: str) -> dict:
    if not quiescent:
        raise ValueError('all declared data writers must be stopped before backup')
    source = source.resolve(strict=True)
    before = _files(source)
    started = time.monotonic()
    captured_at = datetime.now(timezone.utc).isoformat()
    destination = _destination(source, destination)
    try:
        payload = destination / 'payload'
        payload.mkdir(mode=0o700)
        entries = []
        for name, identity in before.items():
            src = _safe(source, _relative(name))
            dst = payload / _relative(name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            item = {'path': name, 'kind': 'sqlite' if src.suffix in {'.db', '.sqlite', '.sqlite3'} else 'file'}
            if item['kind'] == 'sqlite':
                with sqlite3.connect(src.as_uri() + '?mode=ro', uri=True) as db, sqlite3.connect(dst) as out:
                    def bounded_backup(*_):
                        if time.monotonic() - started > 120:
                            raise TimeoutError('SQLite backup exceeded offline budget')
                    db.backup(out, pages=1024, progress=bounded_backup)
                item['sqlite'] = sqlite_inventory(dst)
            else:
                source_hash = _sha(src)
                shutil.copyfile(src, dst)
                if _sha(dst) != source_hash or _sha(src) != source_hash:
                    raise ValueError('source changed while backing up')
            _sync(dst)
            item.update(size=dst.stat().st_size, sha256=_sha(dst))
            entries.append(item)
        if _files(source) != before:
            raise ValueError('source inventory changed; no consistent backup committed')
        manifest = {'version': 1, 'captured_at': captured_at, 'completed_at': datetime.now(timezone.utc).isoformat(),
                    'code_head': code_head, 'consistency': 'operator_quiesced_all_declared_writers',
                    'roots': list(ROOTS), 'excluded': ['OS debris', 'old .bak- recovery copies', 'SQLite WAL/SHM folded by backup',
                                                     'empty backend/data/ashare.db placeholder'],
                    'files': entries, 'backup_seconds': round(time.monotonic() - started, 6)}
        _json(destination / 'manifest.json', manifest)
        return manifest
    except BaseException:
        shutil.rmtree(destination)
        raise


def verify(bundle: Path) -> dict:
    bundle = bundle.absolute()
    _safe(Path(bundle.anchor), Path(*bundle.parts[1:]))
    manifest_path = _safe(bundle, Path('manifest.json'))
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('version') != 1 or manifest.get('roots') != list(ROOTS):
        raise ValueError('unknown recovery manifest')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not entries or len(entries) > MAX_FILES:
        raise ValueError('invalid recovery file list')
    names = []
    total = 0
    for item in entries:
        relative = _relative(item['path'])
        path = _safe(bundle, Path('payload') / relative)
        names.append(item['path'])
        if item['kind'] not in {'file', 'sqlite'} or not path.is_file():
            raise ValueError('missing or invalid recovery file')
        total += path.stat().st_size
        if total > MAX_BYTES or path.stat().st_size != item['size'] or _sha(path) != item['sha256']:
            raise ValueError('recovery size or digest mismatch')
        if item['kind'] == 'sqlite' and sqlite_inventory(path) != item['sqlite']:
            raise ValueError('SQLite fact or schema mismatch')
    if len(names) != len(set(names)):
        raise ValueError('duplicate recovery path')
    actual = []
    for p in (bundle / 'payload').rglob('*'):
        if p.is_symlink():
            raise ValueError('symlink in bundle')
        if p.is_file():
            actual.append(p.relative_to(bundle / 'payload').as_posix())
    if sorted(actual) != sorted(names):
        raise ValueError('unmanifested recovery file')
    return manifest


def restore(bundle: Path, destination: Path) -> dict:
    manifest = verify(bundle)
    if destination.resolve().is_relative_to(bundle.resolve()):
        raise ValueError('restore destination overlaps backup bundle')
    started = time.monotonic()
    destination = _destination(bundle.resolve(), destination)
    try:
        hold = {'state': 'blocked_pending_operator_reconciliation', 'captured_at': manifest['captured_at'],
                'code_head': manifest['code_head'], 'outbound': 'disabled',
                'leases_and_external_acceptance': 'unknown; do not replay',
                'instructions': 'Keep hold until schema, scopes, watermarks, pending intents and external effects are reconciled.'}
        parents = {destination / 'data'}
        parents.update((destination / _relative(i['path'])).parent for i in manifest['files'] if i['kind'] == 'sqlite')
        for parent in parents:
            parent.mkdir(parents=True, exist_ok=True)
            _json(parent / HOLD, hold)
        # Hold precedes the first restored fact: an interrupted copy remains blocked.
        for item in manifest['files']:
            relative = _relative(item['path'])
            src = _safe(bundle, Path('payload') / relative)
            dst = destination / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            if _sha(dst) != item['sha256']:
                raise ValueError('restored content digest mismatch')
            _sync(dst)
        receipt = {'state': 'restored_on_hold', 'files': len(manifest['files']),
                   'captured_at': manifest['captured_at'], 'restore_seconds': round(time.monotonic() - started, 6),
                   'production_rpo_seconds': None, 'production_rto_seconds': None}
        _json(destination / 'restore-receipt.json', receipt)
        return receipt
    except BaseException:
        shutil.rmtree(destination)
        raise
