"""Explicit, version-bound observation rows for collector/health unit fixtures."""
import json

from app.models.daily_pick import DailyPickReview, DailyPickSet
from app.services.selection_notifications import daily_selection_version


def add_bound_reviews(db, day, rows, phase=None):
    items = [{'symbol': symbol, 'price': 10} for symbol, _, _ in rows]
    generated = f'{day}T09:26:00+08:00'
    version = daily_selection_version(day, generated, items)
    meta = {'generated_at': generated, 'selection_version': version, 'market_phase': phase}
    db.add(DailyPickSet(date=day, items=json.dumps(items), meta=json.dumps(meta)))
    for symbol, verdict, excess in rows:
        context = {'contract_version': 'daily_review_v2', 'selection_version': version,
                   'performance_date': day, 'symbol': symbol, 'window_kind': 'open_to_close',
                   'window_start': f'{day}T09:30:00+08:00', 'window_end': f'{day}T15:00:00+08:00',
                   'quote': {'source': 'fixture', 'as_of': f'{day}T15:00:00+08:00', 'quality': 'high'},
                   'benchmark': {'source': 'fixture', 'as_of': f'{day}T15:00:00+08:00', 'quality': 'high'},
                   'eligible_for_stats': True, 'market_phase': phase}
        db.add(DailyPickReview(date=day, symbol=symbol, verdict=verdict,
                              reason_category='gone_well', excess_pct=excess,
                              review_context=json.dumps(context), selection_version=version))
