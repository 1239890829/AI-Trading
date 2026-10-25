"""Bounded, read-only links to saved selections. Relations never imply a catalyst."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from app.core.bjtime import BJ_TZ
from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionRun, OpportunityDecisionSnapshot


def _json(value, expected):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, expected) else expected()
    except (ValueError, TypeError):
        return expected()


def saved_selection_context(session_factory, *, now: datetime) -> dict:
    """Use each owner's actual latest saved date; never label it today's selection."""
    now = now.astimezone(BJ_TZ)
    today = now.date().isoformat()
    items, missing = [], []
    with session_factory() as db:
        daily = db.scalar(select(DailyPickSet).where(DailyPickSet.date <= today)
                          .order_by(DailyPickSet.date.desc()).limit(1))
        if daily:
            meta = _json(daily.meta, dict)
            entries = _json(meta.get('selection_entries'), dict)
            try:
                daily_items = json.loads(daily.items) if isinstance(daily.items, str) else daily.items
                if not isinstance(daily_items, list) or any(not isinstance(x, dict) for x in daily_items):
                    raise ValueError('invalid saved items')
            except (ValueError, TypeError):
                daily_items = []
                missing.append('每日名单损坏或格式未知，不能据此认定零入选')
            for item in daily_items:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get('symbol') or '')
                if len(symbol) != 6 or not symbol.isdigit():
                    continue
                entry = _json(entries.get(symbol), dict)
                items.append({'symbol': symbol, 'name': str(item.get('name') or symbol),
                    'trade_date': daily.date, 'scope': 'daily',
                    'source_version': meta.get('selection_version'),
                    'event_refs': entry.get('event_refs') or item.get('related_event_refs') or [],
                    'entry_state': entry.get('state', 'missing')})
        else:
            missing.append('尚无已保存每日名单')
        run = db.scalar(select(OpportunityDecisionRun).where(
            OpportunityDecisionRun.trade_date <= today,
            OpportunityDecisionRun.as_of <= now.replace(tzinfo=None),
            OpportunityDecisionRun.scenario == 'intraday_opportunity')
            .order_by(OpportunityDecisionRun.trade_date.desc(), OpportunityDecisionRun.as_of.desc(),
                      OpportunityDecisionRun.run_id.desc()).limit(1))
        if run:
            rows = db.scalars(select(OpportunityDecisionSnapshot).where(
                OpportunityDecisionSnapshot.run_id == run.run_id,
                OpportunityDecisionSnapshot.stage == 'rank',
                OpportunityDecisionSnapshot.decision == 'ranked')
                .order_by(OpportunityDecisionSnapshot.rank, OpportunityDecisionSnapshot.id).limit(200)).all()
            seen = set()
            for row in rows:
                evidence = _json(row.evidence, dict)
                capacity = evidence.get('effective_limit')
                if (row.trade_date != run.trade_date or row.symbol in seen or evidence.get('within_display_capacity') is not True
                        or isinstance(capacity, bool) or not isinstance(capacity, int)
                        or row.rank is None or not 1 <= row.rank <= capacity):
                    continue
                seen.add(row.symbol)
                items.append({'symbol': row.symbol, 'name': row.name or row.symbol,
                    'trade_date': row.trade_date, 'scope': 'intraday', 'source_version': run.run_id,
                    'event_refs': evidence.get('event_refs') or evidence.get('related_event_refs') or [],
                    'entry_state': 'saved_rank'})
        else:
            missing.append('尚无已保存盘中运行')
    return {'state': 'partial' if missing else 'saved', 'items': items, 'missing': missing,
            'semantics': 'saved_selection_relation_not_causality_or_fill'}


def selection_links(event: dict, context: dict) -> list[dict]:
    symbols = {str(d.get('target')) for d in event.get('directions', [])
               if d.get('target_type') == 'symbol' and d.get('matched_by') != 'llm_aux'}
    if event.get('source_symbol'):
        symbols.add(str(event['source_symbol']))
    links = []
    for item in context.get('items', []):
        refs = [ref for ref in item.get('event_refs') or [] if isinstance(ref, dict)
                and str(ref.get('event_id')) == str(event.get('id'))]
        if refs or item['symbol'] in symbols:
            current = event.get('interpretation_ref') or {}
            complete = [r for r in refs if r.get('version_id') is not None and r.get('observation_id') is not None]
            if any(r['version_id'] == current.get('version_id') and r['observation_id'] == current.get('observation_id') for r in complete):
                ref_state = 'current_version'
            elif complete and current.get('version_id') is not None and current.get('observation_id') is not None:
                ref_state = 'different_version'
            else:
                ref_state = 'unknown'
            links.append({k: item.get(k) for k in ('symbol', 'name', 'trade_date', 'scope', 'source_version')} |
                         {'relation': 'original_evidence' if refs else 'same_symbol',
                          'original_event_refs': refs, 'reference_state': ref_state})
    return links
