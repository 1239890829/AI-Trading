"""Read existing selection owners by exact saved identity; never rerun a strategy.

DailyPickSet retains one current row per day, not every generation. Opportunity
runs are append-only. A content digest binds even legacy records and changes in
rejected evidence, which the daily selected-item identity alone does not cover.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime

from contextlib import contextmanager

from sqlalchemy import and_, case, false, func, select

from app.core import bjtime
from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionRun, OpportunityDecisionSnapshot
from app.picks.review_contract import generated_time, iso_day, selection_version

METHODS = ('daily_picks', 'intraday_watch')
STAGES = ('rank', 'hard_gate', 'candidate', 'notification')
DAILY_HISTORY = '每日每日期仅保留当前保存版本；此前被覆盖的生成和未保存候选无法补读。'
INTRADAY_HISTORY = '原决策运行与逐阶段存档；同股可能有多行，行数不是股票数或成交数。'


class SavedRunMissing(LookupError):
    """A missing or changed requested identity must never resolve to latest."""


def checked(value, kind):
    try:
        obj = json.loads(value, parse_float=_finite_float, parse_constant=_invalid_constant) if isinstance(value, str) else value
        return obj if isinstance(obj, kind) else None
    except (TypeError, ValueError):
        return None


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError('non-finite saved number')
    return parsed


def _invalid_constant(value):
    raise ValueError('invalid saved numeric constant')


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def text(value):
    return value if isinstance(value, str) and value.strip() else None


def diagnostics(row: dict, meta: dict) -> dict:
    """Project original inputs only: no current weights, thresholds or arithmetic."""
    fields = {key: checked(row.get(key), kind) for key, kind in (
        ('sub_scores', dict), ('dimension_evidence', dict), ('scoring_trace', dict), ('vetoes', list))}
    missing = [key for key, value in fields.items() if value is None]
    fields.update(selection_reason=text(row.get('selection_reason')),
                  score_shortfall=number(row.get('score_shortfall')),
                  min_pick_score=number(meta.get('min_pick_score')))
    if fields['min_pick_score'] is None:
        missing.append('min_pick_score')
    if fields['score_shortfall'] is None:
        missing.append('score_shortfall')
    known = sum(fields[key] is not None for key in ('sub_scores', 'dimension_evidence', 'scoring_trace', 'vetoes'))
    return {'state': 'saved' if not missing else 'partial' if known else 'missing', **fields, 'missing': missing}


def daily_reason(row: dict) -> str:
    if text(row.get('reason')):
        return row['reason']
    reason = row.get('selection_reason')
    if reason == 'below_min_score':
        delta = number(row.get('score_shortfall'))
        return f'低于原入选分线；原记录差额 {delta:g} 分' if delta is not None and delta >= 0 else '低于原入选分线；原差额未记录'
    if reason == 'capacity_or_replacement':
        return '原记录为容量或换股约束；具体原因未区分'
    return f'原入选原因未识别（{reason}）' if text(reason) else ''


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':'))


def _columns(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _identity(row, method, rows=()):
    hasher = hashlib.sha256(_json(_columns(row)).encode())
    if method == 'intraday_watch':
        for item in rows:
            hasher.update(b'\n')
            hasher.update(_json(_columns(item)).encode())
        return {'method': method, 'run_id': row.run_id, 'trade_date': row.trade_date,
                'source_version': row.run_id, 'content_version': hasher.hexdigest(),
                'strategy_version': text(row.strategy_version), 'feature_version': text(row.feature_version),
                'version_state': 'bound' if row.strategy_version and row.feature_version else 'legacy',
                'run_at': _intraday_clock(row.as_of), 'state': 'saved'}
    meta = checked(row.meta, dict) or {}
    original = text(meta.get('selection_version'))
    selected = checked(row.items, list)
    bound = selection_version(row.date, meta, selected) if selected is not None else None
    contract = checked(meta.get('selection_contract'), dict) or {}
    return {'method': method, 'run_id': str(row.id), 'trade_date': iso_day(row.date),
            'source_version': original, 'content_version': hasher.hexdigest(),
            'strategy_version': text(contract.get('strategy_version')), 'feature_version': text(contract.get('feature_version')),
            'version_state': 'legacy' if original is None else 'bound' if bound == original else 'mismatch',
            'run_at': text(meta.get('generated_at')), 'state': 'saved'}


def _intraday_clock(stamp):
    if not isinstance(stamp, datetime):
        return None
    return (stamp.replace(tzinfo=bjtime.BJ_TZ) if stamp.tzinfo is None else stamp.astimezone(bjtime.BJ_TZ)).isoformat()


@contextmanager
def _read_db(session_factory):
    with session_factory() as db:
        # Python sqlite legacy mode does not BEGIN for SELECT. Bind digest and
        # page reads to the same snapshot so concurrent owner changes cannot mix.
        connection = db.connection()
        if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN')
        yield db


def _bound_snapshot(row, run):
    return (row['trade_date'] == run.trade_date and row['scenario'] == run.scenario
            and row['strategy_version'] == run.strategy_version and row['feature_version'] == run.feature_version
            and row['as_of'] == run.as_of)


def _stream_fingerprints(db, runs, *, summarize=False):
    """One streaming query for this bounded owner page; no per-run ORM loading."""
    owners = {run.run_id: run for run in runs}
    digests = {key: hashlib.sha256(_json(_columns(run)).encode()) for key, run in owners.items()}
    summaries = {key: {'rows': 0, 'rank_rows': 0, 'known': True, 'selected': set(),
                       'invalid_bindings': 0, 'stages': {}} for key in owners}
    if not owners:
        return {}, summaries
    columns = list(OpportunityDecisionSnapshot.__table__.columns)
    query = (select(*columns).where(OpportunityDecisionSnapshot.run_id.in_(owners))
             .order_by(OpportunityDecisionSnapshot.run_id, OpportunityDecisionSnapshot.id)
             .execution_options(yield_per=200))
    for row in db.execute(query).mappings():
        key = row['run_id']
        digests[key].update(b'\n')
        digests[key].update(_json(dict(row)).encode())
        summary = summaries[key]
        summary['rows'] += 1
        if not summarize:
            continue
        stage = row['stage']
        summary['stages'][stage] = summary['stages'].get(stage, 0) + 1
        bound = _bound_snapshot(row, owners[key])
        if not bound:
            summary['invalid_bindings'] += 1
            summary['known'] = False
        if stage != 'rank':
            continue
        summary['rank_rows'] += 1
        evidence = checked(row['evidence'], dict)
        if evidence is None:
            summary['known'] = False
            continue
        capacity = number(evidence.get('effective_limit'))
        rank = row['rank']
        valid = (isinstance(capacity, int) and capacity >= 1 and isinstance(rank, int) and rank >= 1
                 and isinstance(evidence.get('within_display_capacity'), bool)
                 and evidence['within_display_capacity'] == (rank <= capacity))
        if row['decision'] == 'ranked':
            summary['known'] = summary['known'] and valid
            if bound and valid and evidence['within_display_capacity']:
                summary['selected'].add(row['symbol'])
        elif row['decision'] not in {'unknown', 'rejected', 'blocked'}:
            summary['known'] = False
    return {key: digest.hexdigest() for key, digest in digests.items()}, summaries


def _not_future(row, method, now):
    if method != 'daily_picks':
        return True
    clock = generated_time(checked(row.meta, dict) or {})
    return clock is None or clock <= now


def _scope(method, trade_date=None):
    if method not in METHODS:
        raise ValueError('该方法没有已保存运行读取入口')
    now = bjtime.beijing_now()
    day = iso_day(trade_date) if trade_date is not None else None
    if trade_date is not None and (day is None or day > now.date().isoformat()):
        raise ValueError('日期必须是已发生的有效交易日期；不推断是否开市')
    return now, day


def _query(method, now, day):
    if method == 'daily_picks':
        key = func.replace(DailyPickSet.date, '-', '')
        query = select(DailyPickSet).where(key <= now.date().strftime('%Y%m%d'), func.length(key) == 8)
        if day:
            query = query.where(key == day.replace('-', ''))
        return query.order_by(key.desc(), DailyPickSet.id.desc())
    query = select(OpportunityDecisionRun).where(
        OpportunityDecisionRun.scenario == 'intraday_opportunity',
        OpportunityDecisionRun.trade_date <= now.date().isoformat(),
        OpportunityDecisionRun.as_of <= now.replace(tzinfo=None))
    if day:
        query = query.where(OpportunityDecisionRun.trade_date == day)
    return query.order_by(OpportunityDecisionRun.trade_date.desc(), OpportunityDecisionRun.as_of.desc(), OpportunityDecisionRun.run_id.desc())


def _json_text(column, path):
    value = func.json_extract(column, path)
    return case((func.json_valid(column) == 1,
                 case((and_(func.json_type(column, path) == 'text', func.length(func.trim(value)) > 0), value),
                      else_=None)), else_=None)


def list_method_runs(session_factory, method, *, trade_date=None, source_version=None,
                     strategy_version=None, source_version_missing=False, offset=0, limit=30):
    now, day = _scope(method, trade_date)
    if offset < 0 or not 1 <= limit <= 200 or source_version is not None and source_version_missing:
        raise ValueError('运行列表参数冲突或范围无效')
    query = _query(method, now, day)
    if method == 'daily_picks':
        source = _json_text(DailyPickSet.meta, '$.selection_version')
        strategy = _json_text(DailyPickSet.meta, '$.selection_contract.strategy_version')
    else:
        source, strategy = OpportunityDecisionRun.run_id, OpportunityDecisionRun.strategy_version
    if source_version is not None:
        query = query.where(source == source_version)
    if source_version_missing:
        query = query.where(source.is_(None) if method == 'daily_picks' else false())
    if strategy_version is not None:
        query = query.where(strategy == strategy_version)
    with _read_db(session_factory) as db:
        # Finite owner scan even when legacy clock/date fields are not usable.
        owners = db.scalars(query.offset(offset).limit(limit + 1)).all()
        more = len(owners) > limit
        scanned = owners[:limit]
        visible = [row for row in scanned if _not_future(row, method, now)]
        fingerprints, summaries = _stream_fingerprints(db, visible) if method == 'intraday_watch' else ({}, {})
        items = []
        for row in visible:
            identity = _identity(row, method)
            if identity['trade_date'] is None:
                continue
            if method == 'intraday_watch':
                identity['content_version'] = fingerprints[row.run_id]
            elif checked(row.meta, dict) is None or checked(row.items, list) is None or identity['version_state'] == 'mismatch':
                identity['state'] = 'partial'
            items.append(identity)
    return {'method': method, 'items': items, 'coverage': {
        'offset': offset, 'limit': limit, 'returned': len(items), 'scanned': len(scanned),
        'excluded_source_rows': len(scanned) - len(items),
        'fingerprint_rows': sum(summary['rows'] for summary in summaries.values()),
        'next_offset': offset + len(scanned) if more else None, 'possibly_truncated': more,
        'scope': 'bounded_saved_owner_directory',
        'history_semantics': DAILY_HISTORY if method == 'daily_picks' else INTRADAY_HISTORY}}


def _daily(row, identity, *, offset=0, limit=200):
    meta = checked(row.meta, dict)
    lists = [checked(row.items, list), checked(row.rejected, list)]
    valid = [entries is not None and all(isinstance(e, dict) for e in entries) for entries in lists]
    problems = [] if meta is not None else ['运行元数据 JSON 损坏或结构不符']
    problems += [f'{label} JSON 损坏或结构不符' for label, ok in zip(('入选名单', '拒绝名单'), valid) if not ok]
    if identity['version_state'] == 'mismatch':
        problems.append('原入选版本与当前保存内容不一致，不作为已核版本')
    meta = meta or {}
    items, total = [], 0
    for state, entries in zip(('selected', 'rejected'), lists):
        for index, item in enumerate(entries or []):
            if not isinstance(item, dict):
                continue
            total += 1
            if total <= offset or total > offset + limit:
                continue
            items.append({'symbol': str(item.get('symbol') or ''), 'name': str(item.get('name') or item.get('symbol') or ''),
                'item_id': f'{state}:{index}', 'state': state, 'score': number(item.get('score')),
                'rank': number(item.get('rank')), 'reason': daily_reason(item),
                'basis': checked(item.get('bases'), dict) or {}, 'diagnostics': diagnostics(item, meta)})
    funnel = checked(meta.get('selection_funnel'), dict) or {}
    return {**identity, 'state': 'saved' if not problems else 'partial' if any(valid) or total else 'error',
        'reason': '；'.join(problems), 'elapsed_ms': None,
        'counts': {'candidates': number(meta.get('candidate_count')), 'scored': number(funnel.get('scored')),
                   'selected': len(lists[0]) if valid[0] else None, 'rejected_saved': len(lists[1]) if valid[1] else None},
        'criteria': {'min_pick_score': number(meta.get('min_pick_score')), 'max_picks': number(meta.get('max_picks'))},
        'items': items, '_items_total': total, 'semantics': 'saved_selection_not_execution_or_fill'}


def _intraday(run, rows, identity, summary):
    items, problems = [], []
    for row in rows:
        original = checked(row.evidence, dict)
        bound = _bound_snapshot(_columns(row), run)
        if original is None or not bound:
            problems.append('部分原快照依据损坏或日期/方法/版本/时点不符')
        evidence = original or {}
        capacity = number(evidence.get('effective_limit'))
        rank_valid = (isinstance(capacity, int) and capacity >= 1 and isinstance(row.rank, int) and row.rank >= 1
                      and isinstance(evidence.get('within_display_capacity'), bool)
                      and evidence['within_display_capacity'] == (row.rank <= capacity))
        selected = row.stage == 'rank' and row.decision == 'ranked' and rank_valid and evidence['within_display_capacity'] and bound
        candidate = row.stage == 'hard_gate' and row.decision == 'passed' or row.stage == 'rank' and row.decision == 'ranked' and rank_valid
        state = ('unknown' if not bound else 'selected' if selected else 'rejected' if row.decision in {'rejected','blocked'}
                 else 'candidate' if candidate else 'unknown')
        items.append({'item_id': row.snapshot_id, 'snapshot_id': row.snapshot_id,
            'symbol': row.symbol, 'name': row.name or row.symbol, 'state': state,
            'stage': row.stage, 'decision': row.decision, 'rank': row.rank, 'source_theme': row.source_theme,
            'score': number(evidence.get('score')), 'reason': text(evidence.get('reason')) or text(evidence.get('filter_reason')) or text(evidence.get('linkage_basis')) or row.decision,
            'basis': checked(evidence.get('bases'), dict) or {}, 'diagnostics': diagnostics(evidence, {}),
            'identity_bound': bound, 'evidence': evidence})
    complete = summary['rows'] == run.records_total and summary['rank_rows'] == run.rank_rows
    if not complete:
        problems.append('原运行计数与可读存档行不一致，不将缺失记录当零入选')
    if summary['invalid_bindings']:
        problems.append('存在日期/方法/版本/时点不符的原快照')
    if not summary['known']:
        problems.append('原排序记录缺少可核入选容量或身份，不推算入选数')
    return {**identity, 'state': 'partial' if problems else 'saved', 'reason': '；'.join(dict.fromkeys(problems)),
        'elapsed_ms': None, 'data_state': run.data_state,
        'counts': {'candidate_rows': run.candidate_rows, 'hard_gate_rows': run.hard_gate_rows, 'rank_rows': run.rank_rows,
                   'selected': len(summary['selected']) if summary['known'] and complete else None}, 'items': items,
        'semantics': 'saved_run_rows_not_unique_stock_outcomes_or_fills'}


def get_method_run(session_factory, method, run_id, *, trade_date, content_version,
                   source_version=None, strategy_version=None, source_version_missing=False,
                   offset=0, limit=200, stage=None):
    now, day = _scope(method, trade_date)
    if (offset < 0 or not 1 <= limit <= 200 or stage is not None and stage not in STAGES
            or source_version is not None and source_version_missing):
        raise ValueError('运行详情分页或阶段无效')
    if method == 'daily_picks' and stage is not None:
        raise ValueError('每日名单没有盘中阶段过滤')
    with _read_db(session_factory) as db:
        query = _query(method, now, day)
        if method == 'daily_picks':
            if not str(run_id).isdigit():
                raise SavedRunMissing('原运行不存在或身份已变化；未回退最近运行')
            row = db.scalar(query.where(DailyPickSet.id == int(run_id)))
        else:
            row = db.scalar(query.where(OpportunityDecisionRun.run_id == run_id))
        if row is None or not _not_future(row, method, now):
            raise SavedRunMissing('原运行不存在或日期/方法不符；每日被覆盖版本无法补读，未回退最近运行')
        identity = _identity(row, method)
        summary = None
        if method == 'intraday_watch':
            fingerprints, summaries = _stream_fingerprints(db, [row], summarize=True)
            identity['content_version'], summary = fingerprints[row.run_id], summaries[row.run_id]
        if (content_version != identity['content_version']
                or source_version is not None and source_version != identity['source_version']
                or source_version_missing and identity['source_version'] is not None
                or strategy_version is not None and strategy_version != identity['strategy_version']):
            raise SavedRunMissing('原运行内容或原版本已变化；未回退最近运行，请重新选择已存批次')
        if method == 'daily_picks':
            detail = _daily(row, identity, offset=offset, limit=limit)
            total = detail.pop('_items_total')
            fingerprint_rows = 1
        else:
            total = summary['rows'] if stage is None else summary['stages'].get(stage, 0)
            page_query = select(OpportunityDecisionSnapshot).where(OpportunityDecisionSnapshot.run_id == row.run_id)
            if stage is not None:
                page_query = page_query.where(OpportunityDecisionSnapshot.stage == stage)
            page = db.scalars(page_query.order_by(OpportunityDecisionSnapshot.id).offset(offset).limit(limit)).all()
            detail = _intraday(row, page, identity, summary)
            fingerprint_rows = summary['rows']
    more = offset + len(detail['items']) < total
    detail['possibly_truncated'] = more
    detail['coverage'] = {'offset': offset, 'limit': limit, 'total': total, 'returned': len(detail['items']),
        'next_offset': offset + len(detail['items']) if more else None, 'possibly_truncated': more,
        'fingerprint_rows': fingerprint_rows,
        'stage': stage, 'history_semantics': DAILY_HISTORY if method == 'daily_picks' else INTRADAY_HISTORY}
    return detail
