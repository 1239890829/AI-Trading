"""Manual price-rule research, isolated from selection, notification and paper owners.

The existing registry owns the rule identities. Immutable local batches retain
all matches, non-matches and unknowns; reads never open the market DB or run rules.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import tempfile
import time as clock

from app.core import bjtime
from app.market.marketdb_freshness import DEFAULT_DB_PATH, _to_date
from app.picks._strategy_reference_rules import (DEFINITION_DIGEST, evaluate_reference_rules,
                                               is_reference_stock_code, reference_rule_metadata)
from app.picks.selection_history import SavedRunMissing

ROOT = Path(__file__).resolve().parents[2] / 'data' / 'picks' / 'reference-runs'
METHODS = tuple('reference:' + key for key in reference_rule_metadata())
CONTRACT = 'adjusted-close-actual-volume-v1'
SEMANTICS = '手动价格研究；匹配不等于正式入选、通知、模拟资格或收益证据。'
LIMITATIONS = ['原库已有A股代码行；当前存续身份及历史全集未认证。',
              '复权收盘序列为当前版本；不具备当时发布版本证明。',
              '未核历史ST、停牌及可成交价格；不生成交易指令或胜率。',
              '源库有日线不证明本系统取得该日所有证券；缺档保留未知。']


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def _day(value):
    if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
        raise ValueError('日期必须是YYYY-MM-DD')
    return value


def _method(value):
    if value not in METHODS:
        raise ValueError('未知研究方法')
    return value.split(':', 1)[1]


def _bounds(offset, limit):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('invalid page bounds')


def _quality(path):
    raw = path.read_bytes()
    report = json.loads(raw)
    if report.get('available') is not True or report.get('status') not in {'ok', 'warn'}:
        raise ValueError('来源质量报告未通过；不能生成研究结果')
    bad = {}
    for issue in report.get('issues', []):
        if issue.get('severity') == 'error':
            raise ValueError('来源质量报告有error')
        if issue.get('check') == 'daily_k_adj.single_day_jump':
            for row in issue.get('sample', []):
                if len(row) >= 2 and (day := _to_date(row[1])):
                    bad[(row[0], day.isoformat())] = 'adjusted_price_discontinuity'
    return hashlib.sha256(raw).hexdigest(), bad, {'status': report['status'], 'synced_at': report.get('synced_at'),
        'scope': '源报告近窗抽查；不是全历史质量认证', 'issues': report.get('issues', [])}


def _identity(batch, code):
    entry = batch['methods'][code]
    return {'method': 'reference:' + code, 'run_id': batch['run_id'], 'trade_date': batch['trade_date'],
            'source_version': batch['source_version'], 'content_version': batch['content_version'],
            'strategy_version': entry['version'], 'feature_version': CONTRACT, 'version_state': 'bound',
            'run_at': batch['run_at'], 'state': 'saved'}


def _load(path):
    if path.is_symlink():
        raise SavedRunMissing('研究存档不能是链接')
    with path.open('rb') as handle:
        raw = handle.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise SavedRunMissing('研究存档超出读取上限')
    batch = json.loads(raw)
    claimed = batch.pop('content_version')
    if hashlib.sha256(_json(batch).encode()).hexdigest() != claimed or batch['run_id'] != path.stem:
        raise SavedRunMissing('研究存档内容身份不符')
    batch['content_version'] = claimed
    _day(batch['trade_date'])
    if batch['trade_date'] > bjtime.beijing_now().date().isoformat():
        raise SavedRunMissing('未来研究记录不可读')
    return batch


def run_batch(params=None, *, db_path=None, root=None, checkpoint=lambda: None):
    """One bounded streaming pass for three frozen rules; no network or model call."""
    params = params or {}
    if not isinstance(params, dict) or set(params) - {'trade_date'}:
        raise ValueError('只接受trade_date，不接受路径、SQL、模型或阈值')
    requested = _day(params['trade_date']) if 'trade_date' in params else None
    path, root = Path(db_path or DEFAULT_DB_PATH), Path(root or ROOT)
    if not path.is_file() or path.is_symlink():
        raise ValueError('本机日线仓不可用')
    now = bjtime.beijing_now()
    # Before the closing data can be complete, a same-day bar is not admissible.
    completed = now.date() if now.timetz().replace(tzinfo=None) >= time(16) else now.date() - timedelta(days=1)
    if requested and requested > completed.isoformat():
        raise ValueError('只可使用已完成交易日')
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.writer.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('研究筛选正在运行；请读取原任务') from None
        checkpoint()
        started = clock.perf_counter()
        import duckdb

        quality_path = path.parent / 'quality_report.json'
        quality_hash, bad, quality = _quality(quality_path)
        source_hash = _digest(path)
        with duckdb.connect(str(path), read_only=True) as con:
            con.execute('BEGIN TRANSACTION')
            cap = int(datetime.combine(completed + timedelta(days=1), time(), tzinfo=bjtime.BJ_TZ).timestamp() * 1000) - 1
            sessions_ms = [row[0] for row in con.execute('SELECT DISTINCT date_ms FROM daily_k WHERE date_ms <= ? ORDER BY date_ms', [cap]).fetchall()]
            sessions = [_to_date(ms).isoformat() for ms in sessions_ms if _to_date(ms)]
            through = requested or (sessions[-1] if sessions else None)
            if through is None or through not in sessions:
                raise ValueError('指定日期没有源交易日；不回落其他日期')
            end = int(datetime.combine(date.fromisoformat(through) + timedelta(days=1), time(), tzinfo=bjtime.BJ_TZ).timestamp() * 1000) - 1
            reader_hash = _digest(Path(__file__))
            source_version = hashlib.sha256(_json({'source_sha256': source_hash, 'quality_sha256': quality_hash,
                'reader_sha256': reader_hash,
                'definition': DEFINITION_DIGEST, 'contract': CONTRACT, 'trade_date': through}).encode()).hexdigest()
            output = root / (source_version + '.json')
            if output.exists():
                batch = _load(output)
                if _digest(path) != source_hash or _digest(quality_path) != quality_hash:
                    raise RuntimeError('读取期间源版本发生变化')
                return {'kind': 'reference_strategies', 'runs': [_identity(batch, code) for code in reference_rule_metadata()], 'reused': True}
            entries = {code: {'version': meta['version'], 'definition': meta['definition'],
                'definition_supplement': meta['supplement'], 'items': []} for code, meta in reference_rule_metadata().items()}
            cursor = con.execute('''SELECT r.thscode, r.date_ms, a.close_adj, r.volume
                FROM daily_k r LEFT JOIN daily_k_adj a ON r.thscode=a.thscode AND r.date_ms=a.date_ms
                WHERE r.date_ms <= ? ORDER BY r.thscode,r.date_ms''', [end])

            def rows():
                while chunk := cursor.fetchmany(4096):
                    yield from chunk

            excluded = 0
            for symbol, group in itertools.groupby(rows(), key=lambda row: row[0]):
                checkpoint()
                if not is_reference_stock_code(symbol):
                    excluded += 1
                    for _ in group:
                        pass
                    continue
                bars = []
                for _, ms, close, volume in group:
                    day = _to_date(ms)
                    bars.append({'date': day.isoformat() if day else None, 'close': close, 'volume': volume,
                                 'quality_issue': bad.get((symbol, day.isoformat() if day else None))})
                verdicts = evaluate_reference_rules(bars, through=through, expected_sessions=sessions)
                for code, verdict in verdicts.items():
                    entries[code]['items'].append({'item_id': symbol, 'symbol': symbol[:6], 'thscode': symbol,
                        'name': symbol[:6], 'stage': 'research', 'state': verdict['state'], 'decision': verdict['state'],
                        'score': None, 'reason': '、'.join(verdict['reason_codes']), **verdict})
            for entry in entries.values():
                entry['items'].sort(key=lambda row: ({'matched': 0, 'unknown': 1, 'not_matched': 2}[row['state']], row['thscode']))
                counts = Counter(row['state'] for row in entry['items'])
                entry['counts'] = {'universe': len(entry['items']), 'eligible': counts['matched'] + counts['not_matched'],
                                   **{key: counts[key] for key in ('matched', 'not_matched', 'unknown')}}
            checkpoint()
            if _digest(path) != source_hash or _digest(quality_path) != quality_hash:
                raise RuntimeError('扫描期间源版本变化；未保存混合版本结果')
            batch = {'run_id': source_version, 'source_version': source_version, 'trade_date': through,
                'source_completed_date': through, 'source_as_of': None, 'source_cutoff_at': through + 'T16:00:00+08:00',
                'source_as_of_semantics': '原始首次发布时间未知；source_cutoff_at仅为筛选日界限',
                'run_at': now.isoformat(), 'definition_digest': DEFINITION_DIGEST, 'feature_version': CONTRACT,
                'source_sha256': source_hash, 'quality_sha256': quality_hash, 'reader_sha256': reader_hash, 'quality': quality,
                'excluded_non_stock_codes': excluded, 'methods': entries, 'data_limitations': LIMITATIONS,
                'elapsed_ms': round((clock.perf_counter() - started) * 1000, 3), 'semantics': SEMANTICS}
            batch['content_version'] = hashlib.sha256(_json(batch).encode()).hexdigest()
            fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=root)
            try:
                with os.fdopen(fd, 'w') as handle:
                    handle.write(_json(batch))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, output)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return {'kind': 'reference_strategies', 'runs': [_identity(batch, code) for code in entries], 'reused': False}


def list_runs(method, *, trade_date=None, source_version=None, strategy_version=None, source_version_missing=False,
              offset=0, limit=30, root=None):
    code = _method(method)
    _bounds(offset, limit)
    if trade_date:
        _day(trade_date)
    items = []
    # Bound the local directory read. Old immutable batches are never regenerated.
    paths = sorted(Path(root or ROOT).glob('*.json'))
    if len(paths) > 1000:
        raise ValueError('保存目录超过1000批；需归档后再读取，不截断冒充完整历史')
    for path in paths:
        try:
            batch = _load(path)
            identity = _identity(batch, code)
        except (OSError, ValueError, KeyError, TypeError, SavedRunMissing):
            raise SavedRunMissing('研究目录存在损坏记录；未静默丢弃') from None
        if ((not trade_date or identity['trade_date'] == trade_date)
            and (not source_version or identity['source_version'] == source_version)
            and (not strategy_version or identity['strategy_version'] == strategy_version)
            and not source_version_missing):
            items.append(identity)
    items.sort(key=lambda row: (row['trade_date'], row['run_at'], row['run_id']), reverse=True)
    more = offset + limit < len(items)
    return {'method': method, 'items': items[offset:offset + limit], 'coverage': {
        'offset': offset, 'limit': limit, 'total': len(items), 'returned': len(items[offset:offset + limit]),
        'next_offset': offset + limit if more else None, 'possibly_truncated': more, 'history_semantics': SEMANTICS}}


def get_run(method, run_id, *, trade_date, content_version, source_version=None, strategy_version=None,
            source_version_missing=False, offset=0, limit=200, stage=None, row_state='all', root=None):
    code = _method(method)
    _bounds(offset, limit)
    if type(row_state) is not str or row_state not in {'all', 'matched', 'not_matched', 'unknown'}:
        raise ValueError('invalid reference row_state')
    _day(trade_date)
    if not re.fullmatch('[0-9a-f]{64}', run_id) or stage not in {None, 'research'}:
        raise SavedRunMissing('研究运行身份不符')
    try:
        batch = _load(Path(root or ROOT) / (run_id + '.json'))
        identity = _identity(batch, code)
    except (OSError, ValueError, KeyError, TypeError):
        raise SavedRunMissing('研究运行不存在或损坏') from None
    if (trade_date != identity['trade_date'] or content_version != identity['content_version']
        or source_version != identity['source_version'] or source_version_missing
        or strategy_version is not None and strategy_version != identity['strategy_version']):
        raise SavedRunMissing('请求版本已不存在；不回落最近结果')
    entry = batch['methods'][code]
    # Identity and the complete saved content digest have already been checked.
    # Filter saved states before slicing; no counts/order-derived offset guesses.
    batch_total = len(entry['items'])
    rows = entry['items'] if row_state == 'all' else [row for row in entry['items'] if row['state'] == row_state]
    total = len(rows)
    more = offset + limit < total
    return {**identity, 'row_state': row_state, 'items': rows[offset:offset + limit], 'counts': entry['counts'],
        **{key: batch[key] for key in ('source_completed_date', 'source_as_of', 'source_cutoff_at', 'source_as_of_semantics',
            'definition_digest', 'data_limitations', 'semantics', 'elapsed_ms')},
        'definition': entry['definition'], 'definition_supplement': entry['definition_supplement'],
        'data_state': 'historical_research', 'coverage': {'offset': offset, 'limit': limit, 'total': total, 'batch_total': batch_total,
            'returned': len(rows[offset:offset + limit]), 'next_offset': offset + limit if more else None,
            'possibly_truncated': more, 'history_semantics': SEMANTICS}}
