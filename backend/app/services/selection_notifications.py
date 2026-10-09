"""Project original selection results into immutable, in-app observation messages."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, time, timezone

from sqlalchemy import select, update

from app.core.bjtime import beijing_now, to_beijing, to_beijing_naive
from app.core.db import get_session_factory
from app.core.freshness import DEFAULT_FRESH_WITHIN_SECONDS, MAX_FUTURE_SKEW_SECONDS, Freshness
from app.models.daily_pick import DailyPickSet
from app.models.alert import AlertRule
from app.repositories.alert_repo import AlertRepository
from app.schemas.market import Quote

log = logging.getLogger(__name__)
SELECTION_RULE = '__selection_notifications__'
SELECTION_KIND = 'selection'
OBSERVATION_VALIDITY = '入选时点的研究观察记录；当前名单与条件请查看选股页。不构成买卖建议；规则档位不是胜率。'


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def selection_dedup_key(trade_date: str, symbol: str) -> str:
    return _digest(['selection:v1', trade_date, symbol])


def _time(value) -> datetime | None:
    try:
        return to_beijing(datetime.fromisoformat(str(value))) if value else None
    except (ValueError, TypeError):
        return None


def quote_audit(quote) -> dict:
    """Keep the original quote clock/quality; never replace it with generation time."""
    if quote is None:
        return {}
    return {key: (value.value if hasattr(value, 'value') else value.isoformat() if isinstance(value, datetime) else value)
            for key in ('source', 'quality', 'quality_reasons', 'data_timestamp', 'received_at')
            if (value := getattr(quote, key, None)) is not None}


def _quote_ready(item: dict, trade_date: str, generated: datetime) -> bool:
    audit = item.get('quote_audit')
    if not isinstance(audit, dict) or not audit.get('source') or not audit.get('data_timestamp') or audit.get('quality') not in {'high', 'medium'}:
        return False
    try:
        q = Quote(symbol=item['symbol'], price=item.get('price'), **audit)
        source_at = q.data_timestamp
        if source_at.tzinfo is None:
            # Quote.freshness owns the UTC interpretation of naive provider clocks.
            source_at = source_at.replace(tzinfo=timezone.utc)
        source_at = to_beijing(source_at)
        if q.price is None or q.price <= 0 or source_at.date().isoformat() != trade_date:
            return False
        # A daily observation generated after close can consume the same day's close.
        # Reuse the quote window at the existing 15:00 close, not the consumer wall clock.
        if generated.time().replace(tzinfo=None) >= time(15, 0):
            close_at = generated.replace(hour=15, minute=0, second=0, microsecond=0)
            age = (close_at - source_at).total_seconds()
            return -MAX_FUTURE_SKEW_SECONDS <= age <= DEFAULT_FRESH_WITHIN_SECONDS
        return Freshness.from_age(as_of=source_at, fresh_within=DEFAULT_FRESH_WITHIN_SECONDS).state == 'ready'
    except (ValueError, TypeError):
        return False


def _record(items: list[dict], *, trade_date: str, source: str, source_id: str,
            source_version: str, source_as_of: str, session_factory, run_id: str | None = None, db=None) -> dict:
    repo = AlertRepository(session_factory)
    if db is None:
        rules = [r for r in repo.list_rules() if r.name == SELECTION_RULE]
        rule = rules[-1] if rules else repo.create_rule(name=SELECTION_RULE, condition_type='source_event',
            scope='all', threshold=0, cooldown_seconds=0, channels=['in_app', 'log'])
    else:
        rule = db.scalar(select(AlertRule).where(AlertRule.name == SELECTION_RULE).order_by(AlertRule.id).limit(1))
        if rule is None:
            rule = AlertRule(name=SELECTION_RULE, condition_type='source_event', scope='all',
                threshold=0, cooldown_seconds=0, channels=json.dumps(['in_app', 'log']))
            db.add(rule)
            db.flush()
    created = 0
    for item in items:
        symbol = str(item.get('symbol') or '')
        evidence = {key: item[key] for key in ('bases', 'pick_basis', 'source_basis', 'confidence', 'linkage',
            'quote_audit', 'invalidations', 'vetoes', 'buy_range', 'tradability', 'seal_state',
            'related_event_refs', 'observation_only') if key in item}
        basis = item.get('pick_basis') or item.get('source_basis') or '；'.join(
            str(v) for v in (item.get('bases') or {}).values() if v)
        tier = (item.get('confidence') or {}).get('label') or (item.get('confidence') or {}).get('tier') or (item.get('linkage') or {}).get('level')
        text = f'原选股入选依据：{basis or "依据缺项，请查看原选股记录"}。'
        if tier:
            text += f'规则档位：{tier}（不是胜率）。'
        if item.get('invalidations'):
            text += f'失效条件：{"；".join(map(str, item["invalidations"]))}。'
        audit = item.get('quote_audit') or {}
        if audit.get('data_timestamp'):
            quality = {'high': '高', 'medium': '中等（降级）'}.get(audit.get('quality'), '未知')
            text += f'原报价时点：{audit["data_timestamp"]}；来源：{audit.get("source") or "未知"}；质量：{quality}。'
        text += OBSERVATION_VALIDITY
        snap = {'kind': SELECTION_KIND, 'name': item['name'], 'text': text, 'trade_date': trade_date,
                'selection_source': source, 'source_id': source_id, 'source_version': source_version,
                'source_as_of': source_as_of, 'selection_evidence': evidence, 'push_policy': 'silent'}
        if run_id:
            snap['run_id'] = run_id
        args = (rule.id, symbol, 0, 0)
        kwargs = {'dedup_key': selection_dedup_key(trade_date, symbol), 'snapshot': snap,
            'delivered_channels': ['in_app']}
        _, inserted = repo.record_trigger_once(*args, **kwargs) if db is None else repo.record_trigger_once_in_session(db, *args, **kwargs)
        created += int(inserted)
    return {'state': 'completed', 'created': created, 'selected': len(items)}


def _daily_eligible(items: list[dict], trade_date: str, generated_at: str) -> list[dict]:
    generated = _time(generated_at)
    now = beijing_now()
    return [i for i in items if isinstance(i, dict) and i.get('symbol') and i.get('name') and
                generated and generated.date().isoformat() == trade_date and now.date().isoformat() == trade_date and (generated - now).total_seconds() <= MAX_FUTURE_SKEW_SECONDS and _quote_ready(i, trade_date, generated)]


def _daily_record(items: list[dict], eligible: list[dict], *, trade_date: str, generated_at: str, session_factory, db=None) -> dict:
    version = _digest({'trade_date': trade_date, 'generated_at': generated_at, 'items': items})
    result = _record(eligible, trade_date=trade_date, source='daily', source_id=f'daily:{trade_date}:{generated_at}',
        source_version=version, source_as_of=generated_at, session_factory=session_factory, db=db) if eligible else {
            'state': 'completed', 'created': 0, 'selected': 0}
    return {**result, 'suppressed': len(items) - len(eligible), 'reason': '原报价时间或质量缺失/陈旧的结果不形成新消息' if len(items) != len(eligible) else None}


def publish_daily_selection(items: list[dict], *, trade_date: str, generated_at: str, session_factory=None) -> dict:
    return _daily_record(items, _daily_eligible(items, trade_date, generated_at), trade_date=trade_date,
        generated_at=generated_at, session_factory=session_factory or get_session_factory())


def retry_daily_selection(*, trade_date: str | None = None, session_factory=None) -> dict:
    """Retry only the latest persisted daily version; failures do not revoke its picks."""
    sf = session_factory or get_session_factory()
    day = trade_date or beijing_now().date().isoformat()
    with sf() as db:
        row = db.scalar(select(DailyPickSet).where(DailyPickSet.date == day))
        if row is None:
            return {'state': 'idle'}
        raw_meta, raw_items = row.meta, row.items
        meta = json.loads(raw_meta or '{}')
        if (meta.get('selection_notifications') or {}).get('state') == 'completed':
            return meta['selection_notifications']
        items = json.loads(raw_items or '[]')
        generated = meta.get('generated_at')
    version_match = (DailyPickSet.date == day, DailyPickSet.meta == raw_meta, DailyPickSet.items == raw_items)
    try:
        with sf() as db:
            # Conditional UPDATE both checks the exact persisted version and takes
            # SQLite's write lock. A newer generation cannot commit between this
            # boundary and the event/receipt commit; SELECT alone cannot do that.
            locked = db.execute(update(DailyPickSet).where(*version_match).values(meta=DailyPickSet.meta))
            if locked.rowcount != 1:
                return {'state': 'superseded', 'created': 0, 'reason': 'daily_version_changed'}
            eligible = _daily_eligible(items, day, generated)
            receipt = _daily_record(items, eligible, trade_date=day, generated_at=generated, session_factory=sf, db=db)
            db.execute(update(DailyPickSet).where(DailyPickSet.date == day).values(
                meta=json.dumps({**meta, 'selection_notifications': receipt}, ensure_ascii=False)))
            db.commit()
        return receipt
    except Exception as exc:
        log.exception('daily selection saved; notification projection failed and remains retryable')
        receipt = {'state': 'failed', 'reason': type(exc).__name__}
    # The failed event batch was rolled back, including every dedup reservation.
    # Only its unchanged original version may receive the failure receipt.
    with sf() as db:
        db.execute(update(DailyPickSet).where(*version_match).values(
            meta=json.dumps({**meta, 'selection_notifications': receipt}, ensure_ascii=False)))
        db.commit()
    return receipt


def publish_intraday_selection(payload: dict, *, trade_date: str, snapshot_as_of: str,
        snapshot_state: str, run_id: str, fresh_within: float, session_factory=None) -> dict:
    """Consume the normal original top list only after its exact run is durable."""
    from app.models.opportunity_learning import OpportunityDecisionRun, OpportunityDecisionSnapshot
    from app.picks.intraday_opportunity import top_watch_stocks
    sf = session_factory or get_session_factory()
    as_of = _time(snapshot_as_of)
    if not as_of or snapshot_state != 'ready' or as_of.date().isoformat() != trade_date or Freshness.from_age(as_of=as_of, fresh_within=fresh_within).state != 'ready':
        return {'state': 'suppressed', 'reason': 'snapshot_not_fresh_ready', 'created': 0}
    with sf() as db:
        run = db.get(OpportunityDecisionRun, run_id)
        if run is None or run.trade_date != trade_date or run.as_of != to_beijing_naive(as_of) or run.data_state != 'ready':
            raise ValueError('selection run is not the exact ready archived fact')
        latest = db.scalar(select(OpportunityDecisionRun).where(
            OpportunityDecisionRun.trade_date == trade_date,
            OpportunityDecisionRun.scenario == run.scenario).order_by(
            OpportunityDecisionRun.as_of.desc(), OpportunityDecisionRun.run_id.desc()).limit(1))
        if latest.run_id != run_id:
            return {'state': 'suppressed', 'reason': 'run_superseded', 'created': 0}
        ranked = set(db.scalars(select(OpportunityDecisionSnapshot.symbol).where(
            OpportunityDecisionSnapshot.run_id == run_id, OpportunityDecisionSnapshot.stage == 'rank',
            OpportunityDecisionSnapshot.decision == 'ranked', OpportunityDecisionSnapshot.data_state == 'ready')))
    items = [i for i in top_watch_stocks(payload)['items'] if i.get('symbol') in ranked and i.get('name')]
    return _record(items, trade_date=trade_date, source='intraday', source_id=run_id,
        source_version=run_id, source_as_of=snapshot_as_of, session_factory=sf, run_id=run_id) if items else {
            'state': 'completed', 'created': 0, 'selected': 0}
