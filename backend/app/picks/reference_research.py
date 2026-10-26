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
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time as clock

from app.core import bjtime
from app.market.marketdb_freshness import DEFAULT_DB_PATH, _to_date
from app.picks._strategy_reference_rules import (DEFINITION_DIGEST, iter_reference_rules,
                                               is_reference_stock_code, reference_rule_metadata)
from app.picks.selection_history import SavedRunMissing

ROOT = Path(__file__).resolve().parents[2] / 'data' / 'picks' / 'reference-runs'
METHODS = tuple('reference:' + key for key in reference_rule_metadata())
CONTRACT = 'adjusted-ohlc-actual-volume-turnover-study-v2'
SEMANTICS = '手动价格研究；匹配不等于正式入选、通知、模拟资格或收益证据。'
LIMITATIONS = ['原库已有A股代码行；当前存续身份及历史全集未认证。',
              '复权收盘序列为当前版本；不具备当时发布版本证明。',
              'OHLC以同日复权收盘/原始收盘比例换算，非源方原生复权OHLC；成交量、原币成交额不复权。',
              'L1-L2下影条件要求实体非零；平箱体位置、全平价格RSI均保留未知。',
              '未核历史ST、停牌及可成交价格；参考价上涨率不是模拟成交胜率。',
              '源库有日线不证明本系统取得该日所有证券；缺档保留未知。']
HORIZONS = (1, 3, 5)
COST_BPS = 35
STUDY_SESSIONS = 60


def _outcome_counters():
    return {str(h): {'signals': 0, 'matured': 0, 'immature': 0, 'missing': 0,
                     'wins': 0, 'sum_return_pct': 0.0} for h in HORIZONS}


def _add_outcome(counters, day, prices, sessions, positions):
    """Exact source-session endpoints; mature missing quotes never become pending."""
    index = positions[day]
    for horizon in HORIZONS:
        row = counters[str(horizon)]
        row['signals'] += 1
        if index + horizon >= len(sessions):
            row['immature'] += 1
            continue
        entry, exit_price = prices.get(day), prices.get(sessions[index+horizon])
        if entry is None or exit_price is None:
            row['missing'] += 1
            continue
        change = (exit_price/entry-1)*100
        if not math.isfinite(change):
            row['missing'] += 1
            continue
        row['matured'] += 1
        row['wins'] += change > 0
        row['sum_return_pct'] += change


def _outcome_summary(counters):
    return {key: {**{k: v for k, v in value.items() if k != 'sum_return_pct'},
        'positive_rate': value['wins']/value['matured'] if value['matured'] else None,
        'mean_return_pct': value['sum_return_pct']/value['matured'] if value['matured'] else None,
        'stress_mean_return_pct': value['sum_return_pct']/value['matured']-COST_BPS/100 if value['matured'] else None}
        for key, value in counters.items()}


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
            'strategy_version': entry['version'], 'feature_version': batch.get('feature_version', 'adjusted-close-actual-volume-v1'), 'version_state': 'bound',
            'run_at': batch['run_at'], 'state': 'saved'}


def _load(path, *, method=None):
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
    storage = batch.get('storage_version', 1)
    if storage not in (1, 2):
        raise SavedRunMissing('未知研究存储版本')
    if storage == 2:
        if len(raw) > 2*1024*1024:
            raise SavedRunMissing('研究清单超出读取上限')
        for code, entry in batch['methods'].items():
            ref = entry.get('items_ref')
            if (not isinstance(ref, dict) or set(ref) != {'file', 'sha256', 'bytes', 'rows'}
                    or ref['file'] != f"{batch['run_id']}.{code}.rows"
                    or not re.fullmatch('[a-z][a-z0-9_]{0,63}', code)
                    or not re.fullmatch('[0-9a-f]{64}', str(ref['sha256']))
                    or type(ref['bytes']) is not int or not 0 <= ref['bytes'] <= 64*1024*1024
                    or type(ref['rows']) is not int or ref['rows'] < 0 or ref['rows'] != entry['counts']['universe']):
                raise SavedRunMissing('研究分片身份不符')
            shard = path.parent/ref['file']
            info = shard.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size != ref['bytes']:
                raise SavedRunMissing('研究分片缺失或大小已变')
            if code != method:
                continue
            with shard.open('rb') as handle:
                rows_raw = handle.read(64*1024*1024+1)
            if len(rows_raw) != ref['bytes'] or hashlib.sha256(rows_raw).hexdigest() != ref['sha256']:
                raise SavedRunMissing('研究分片缺失或内容已变')
            items = json.loads(rows_raw)
            if not isinstance(items, list) or len(items) != ref['rows']:
                raise SavedRunMissing('研究分片分母不符')
            entry['items'] = items
    return batch


def _atomic_artifact(path, raw):
    if path.is_symlink():
        raise SavedRunMissing('研究存档不能是链接')
    if path.exists():
        if path.stat().st_size != len(raw) or path.read_bytes() != raw:
            raise SavedRunMissing('已有不可变研究存档内容冲突')
        return
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save_batch(batch, root):
    """Only the small, final manifest publishes the complete immutable batch."""
    manifest = {**batch, 'storage_version': 2, 'methods': {}}
    manifest.pop('content_version', None)
    for code, entry in batch['methods'].items():
        raw = _json(entry['items']).encode()
        if len(raw) > 64*1024*1024:
            raise SavedRunMissing('研究单方法分片超出原读取上限')
        name = f"{batch['run_id']}.{code}.rows"
        _atomic_artifact(root/name, raw)
        manifest['methods'][code] = {key: value for key, value in entry.items() if key != 'items'}
        manifest['methods'][code]['items_ref'] = {'file': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                                                'bytes': len(raw), 'rows': len(entry['items'])}
    manifest['content_version'] = hashlib.sha256(_json(manifest).encode()).hexdigest()
    _atomic_artifact(root/(batch['run_id']+'.json'), _json(manifest).encode())
    return manifest


def _ordered_source_rows(con, extra, cap):
    """Merge bounded sorted source streams instead of building a full history hash join."""
    def stream(cursor):
        while chunk := cursor.fetchmany(4096):
            yield from chunk

    def key(row):
        return (row[0] is None, row[0] or '', row[1] is None, row[1] or 0)

    codes = [row[0] for row in con.execute('SELECT DISTINCT thscode FROM daily_k WHERE date_ms<=? ORDER BY thscode', [cap]).fetchall()]
    # A global ten-year wide sort can exceed DuckDB's minimum pinned buffers at
    # 256MB. Sorting at most 32 complete securities preserves every source row.
    for offset in range(0, len(codes), 32):
        group = codes[offset:offset+32]
        values = [code for code in group if code is not None]
        predicate = ('thscode IN ('+','.join('?' for _ in values)+')') if values else 'FALSE'
        if None in group:
            predicate = '('+predicate+' OR thscode IS NULL)'
        raw_cursor, adjusted_cursor = con.cursor(), con.cursor()
        try:
            raw_cursor.execute('SELECT thscode,date_ms,volume,'+extra+
                               ' FROM daily_k WHERE date_ms<=? AND '+predicate+' ORDER BY thscode,date_ms', [cap, *values])
            adjusted_cursor.execute('SELECT thscode,date_ms,close_adj FROM daily_k_adj WHERE date_ms<=? AND '+predicate+' ORDER BY thscode,date_ms', [cap, *values])
            adjusted = iter(stream(adjusted_cursor))
            pending = next(adjusted, None)
            previous_key, previous_close, previous_issue = None, None, None
            for raw in stream(raw_cursor):
                raw_key = key(raw)
                if raw_key != previous_key:
                    while pending is not None and key(pending) < raw_key:
                        pending = next(adjusted, None)
                    close, issue = None, None
                    if pending is not None and key(pending) == raw_key:
                        close = pending[2]
                        pending = next(adjusted, None)
                        if pending is not None and key(pending) == raw_key:
                            close, issue = None, 'duplicate_adjusted_date'
                            while pending is not None and key(pending) == raw_key:
                                pending = next(adjusted, None)
                    previous_key, previous_close, previous_issue = raw_key, close, issue
                yield raw[0], raw[1], previous_close, *raw[2:], previous_issue
        finally:
            raw_cursor.close()
            adjusted_cursor.close()


def run_batch(params=None, *, db_path=None, root=None, checkpoint=lambda: None):
    """One bounded streaming pass for eleven frozen rules; no network or model call."""
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
        with tempfile.TemporaryDirectory(prefix='ashare-reference-scan-') as scratch, duckdb.connect(
                str(path), read_only=True, config={'threads': 1, 'memory_limit': '256MB', 'temp_directory': scratch,
                                                  'preserve_insertion_order': False}) as con:
            con.execute('BEGIN TRANSACTION')
            cap = int(datetime.combine(completed + timedelta(days=1), time(), tzinfo=bjtime.BJ_TZ).timestamp() * 1000) - 1
            sessions_ms = [row[0] for row in con.execute('SELECT DISTINCT date_ms FROM daily_k WHERE date_ms <= ? ORDER BY date_ms', [cap]).fetchall()]
            sessions = [_to_date(ms).isoformat() for ms in sessions_ms if _to_date(ms)]
            through = requested or (sessions[-1] if sessions else None)
            if through is None or through not in sessions:
                raise ValueError('指定日期没有源交易日；不回落其他日期')
            reader_hash = _digest(Path(__file__))
            from app.picks import _strategy_reference_rules as rule_source
            rules_hash = _digest(Path(rule_source.__file__))
            source_version = hashlib.sha256(_json({'source_sha256': source_hash, 'quality_sha256': quality_hash,
                'reader_sha256': reader_hash, 'rules_source_sha256': rules_hash,
                'definition': DEFINITION_DIGEST, 'contract': CONTRACT, 'trade_date': through}).encode()).hexdigest()
            output = root / (source_version + '.json')
            if output.exists():
                batch = _load(output)
                if batch.get('storage_version') == 2:
                    # An explicit successful reuse checks the entire saved batch.
                    # Ordinary indexes remain small and details verify their one shard.
                    for entry in batch['methods'].values():
                        ref = entry['items_ref']
                        if _digest(root/ref['file']) != ref['sha256']:
                            raise SavedRunMissing('已有研究分片内容已变；不重新计算覆盖原档')
                if _digest(path) != source_hash or _digest(quality_path) != quality_hash:
                    raise RuntimeError('读取期间源版本发生变化')
                return {'kind': 'reference_strategies', 'runs': [_identity(batch, code) for code in reference_rule_metadata()], 'reused': True}
            through_sessions = [day for day in sessions if day <= through]
            positions = {day: i for i, day in enumerate(through_sessions)}
            study_start = through_sessions[max(0, len(through_sessions)-STUDY_SESSIONS)]
            entries = {code: {'version': meta['version'], 'definition': meta['definition'],
                'definition_supplement': meta['supplement'], 'items': [],
                'outcome_counters': _outcome_counters()} for code, meta in reference_rule_metadata().items()}
            # Legacy minimal stores can still evaluate the original close/volume rules.
            # Missing actual OHLC/turnover makes only dependent new hypotheses unknown.
            columns = {row[0] for row in con.execute('DESCRIBE daily_k').fetchall()}
            extra = ','.join(key if key in columns else 'NULL AS '+key
                             for key in ('open_price', 'high_price', 'low_price', 'close_price', 'turnover'))
            signal_cap = int(datetime.combine(date.fromisoformat(through)+timedelta(days=1), time(), tzinfo=bjtime.BJ_TZ).timestamp()*1000)-1
            excluded = 0
            for symbol, group in itertools.groupby(_ordered_source_rows(con, extra, signal_cap), key=lambda row: row[0]):
                checkpoint()
                if not is_reference_stock_code(symbol):
                    excluded += 1
                    for _ in group:
                        pass
                    continue
                bars = []
                prices = {}
                for _, ms, close, volume, op, high, low, raw_close, turnover, join_issue in group:
                    day = _to_date(ms)
                    day_key = day.isoformat() if day else None
                    issue = bad.get((symbol, day_key)) or join_issue
                    valid_close = type(close) in (int, float) and math.isfinite(close) and close > 0
                    factor = close/raw_close if valid_close and type(raw_close) in (int, float) and math.isfinite(raw_close) and raw_close > 0 else None
                    prices[day_key] = close if valid_close and not issue else None
                    def adjusted(value):
                        return value*factor if factor is not None and type(value) in (int, float) and math.isfinite(value) and value > 0 else None
                    bars.append({'date': day_key, 'close': close, 'volume': volume, 'turnover': turnover,
                                 'open': adjusted(op), 'high': adjusted(high), 'low': adjusted(low), 'quality_issue': issue})
                verdicts = None
                for signal_day, observations in iter_reference_rules(bars, through=through, since=study_start,
                                                                      expected_sessions=through_sessions):
                    if signal_day in positions:
                        for code, observation in observations.items():
                            if observation['state'] == 'matched':
                                _add_outcome(entries[code]['outcome_counters'], signal_day, prices, through_sessions, positions)
                    if signal_day == through:
                        verdicts = observations
                if verdicts is None:
                    raise ValueError('研究未产生准确请求日结果')
                for code, verdict in verdicts.items():
                    entries[code]['items'].append({'item_id': symbol, 'symbol': symbol[:6], 'thscode': symbol,
                        'name': symbol[:6], 'stage': 'research', 'state': verdict['state'], 'decision': verdict['state'],
                        'score': None, 'reason': '、'.join(verdict['reason_codes']), **verdict})
            for entry in entries.values():
                entry['items'].sort(key=lambda row: ({'matched': 0, 'unknown': 1, 'not_matched': 2}[row['state']], row['thscode']))
                counts = Counter(row['state'] for row in entry['items'])
                entry['counts'] = {'universe': len(entry['items']), 'eligible': counts['matched'] + counts['not_matched'],
                                   **{key: counts[key] for key in ('matched', 'not_matched', 'unknown')}}
                entry['outcomes'] = {'state': 'descriptive_reference', 'window_start': study_start,
                    'signal_through': through, 'outcome_through': through, 'cost_bps': COST_BPS,
                    'calendar_basis': 'source_session_union', 'horizons': _outcome_summary(entry.pop('outcome_counters')),
                    'return_identity': 'adjusted_close_to_close_proxy', 'production_promotion_eligible': False,
                    'semantics': '近60个源交易日历史重算信号；参考价上涨率非成交胜率，35bps仅成本压力；无独立盲测或生产准入。'}
            checkpoint()
            if (_digest(path) != source_hash or _digest(quality_path) != quality_hash
                    or _digest(Path(__file__)) != reader_hash or _digest(Path(rule_source.__file__)) != rules_hash):
                raise RuntimeError('扫描期间源版本变化；未保存混合版本结果')
            batch = {'run_id': source_version, 'source_version': source_version, 'trade_date': through,
                'source_completed_date': through, 'source_as_of': None, 'source_cutoff_at': through + 'T16:00:00+08:00',
                'source_as_of_semantics': '原始首次发布时间未知；source_cutoff_at仅为筛选日界限',
                'run_at': now.isoformat(), 'definition_digest': DEFINITION_DIGEST, 'feature_version': CONTRACT,
                'source_sha256': source_hash, 'quality_sha256': quality_hash, 'reader_sha256': reader_hash,
                'rules_source_sha256': rules_hash, 'quality': quality,
                'excluded_non_stock_codes': excluded, 'methods': entries, 'data_limitations': LIMITATIONS,
                'elapsed_ms': round((clock.perf_counter() - started) * 1000, 3), 'semantics': SEMANTICS}
            batch = _save_batch(batch, root)
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
            if code not in batch['methods']:
                continue  # Old immutable three-rule batches never contained newer methods.
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
        batch = _load(Path(root or ROOT) / (run_id + '.json'), method=code)
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
        'outcomes': entry.get('outcomes'),
        'data_state': 'historical_research', 'coverage': {'offset': offset, 'limit': limit, 'total': total, 'batch_total': batch_total,
            'returned': len(rows[offset:offset + limit]), 'next_offset': offset + limit if more else None,
            'possibly_truncated': more, 'history_semantics': SEMANTICS}}
