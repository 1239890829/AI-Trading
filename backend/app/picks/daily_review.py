"""Daily selection observations bound to one Beijing day and saved generation.

The producer generates today's candidates. Only a result available before the open
can use the whole day's OHLC; an intraday generation has no post-selection path in
this reader. Price observations never prove fills or T+1 realizable returns.
"""
from __future__ import annotations

from datetime import date, datetime
import json
import logging
from typing import Any

from sqlalchemy import select, update

from app.core.bjtime import BJ_TZ, beijing_now
from app.core.freshness import DEFAULT_FRESH_WITHIN_SECONDS
from app.models.daily_pick import DailyPickReview, DailyPickSet
from app.picks.engine import classify_failure, review_entry_quality
from app.picks.review_contract import (
    REVIEW_VERSION, bound_review, day_keys, finite_number, generated_time,
    object_json, selection_version,
)

log = logging.getLogger(__name__)


async def batch_quotes(hub, symbols: list[str]) -> dict[str, Any]:
    from app.services.quote_enrich import fetch_quotes_batched
    return await fetch_quotes_batched(hub, symbols)


def _close_quote(q, day: str, now) -> bool:
    if q is None or not getattr(q, 'source', None):
        return False
    quality = getattr(q, 'quality', None)
    quality = getattr(quality, 'value', quality)
    stamp = getattr(q, 'data_timestamp', None)
    if quality not in ('high', 'medium') or stamp is None or stamp.tzinfo is None:
        return False
    stamp = stamp.astimezone(BJ_TZ)
    close = now.replace(hour=15, minute=0, second=0, microsecond=0)
    age = (close - stamp).total_seconds()
    return stamp.date().isoformat() == day and 0 <= age <= DEFAULT_FRESH_WITHIN_SECONDS


def _source(q) -> dict:
    stamp = getattr(q, 'data_timestamp', None)
    quality = getattr(q, 'quality', None)
    return {'source': getattr(q, 'source', None), 'as_of': stamp.isoformat() if stamp else None,
            'quality': getattr(quality, 'value', quality)}


def _reference_ready(item: dict, generated: datetime | None, day: str) -> bool:
    audit = item.get('quote_audit')
    price = finite_number(item.get('price'))
    if not price or price <= 0 or not isinstance(audit, dict) or not generated or (generated.hour, generated.minute) >= (15, 0):
        return False
    if not audit.get('source') or audit.get('quality') not in ('high', 'medium'):
        return False
    try:
        stamp = datetime.fromisoformat(str(audit.get('data_timestamp') or '').replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return False
        stamp = stamp.astimezone(BJ_TZ)
        return stamp.date().isoformat() == day and 0 <= (generated - stamp).total_seconds() <= DEFAULT_FRESH_WITHIN_SECONDS
    except (ValueError, TypeError):
        return False


async def generate_daily_review(hub, snapshot_service, session_factory, *, trade_date: date | str | None = None) -> dict:
    now = beijing_now()
    day, compact = day_keys(trade_date or now.date())
    if day > now.date().isoformat():
        raise ValueError('未来日期没有已发生的选股表现')
    with session_factory() as db:
        rows = db.scalars(select(DailyPickSet).where(DailyPickSet.date.in_((day, compact)))).all()
        row = next((r for r in rows if r.date == day), rows[0] if rows else None)
        if row is None:
            raise ValueError(f'{day} 尚无当日组合可复盘')
        raw_date, raw_items, raw_meta = row.date, row.items, row.meta
        items = json.loads(raw_items or '[]')
        meta = object_json(raw_meta)
        if not items:
            raise ValueError('组合为空')
        if day != now.date().isoformat():
            saved = db.scalars(select(DailyPickReview).where(DailyPickReview.date.in_((day, compact)))).all()
            current_version = selection_version(day, meta, items)
            saved.sort(key=lambda r: (r.selection_version != current_version, r.date != day, -r.id))
            seen, selected = set(), []
            for record in saved:
                if record.symbol not in seen:
                    selected.append(record)
                    seen.add(record.symbol)
            saved = selected
            reviews = [{'date': day, 'symbol': r.symbol, 'name': r.name, 'verdict': r.verdict,
                        'reason_category': r.reason_category, 'excess_pct': r.excess_pct,
                        'note': r.note, 'selection_version': r.selection_version, 'review_context': object_json(r.review_context),
                        'binding_state': 'bound' if bound_review(r.review_context, day=day, meta=meta, items=items, symbol=r.symbol, persisted_version=r.selection_version) else 'legacy_or_superseded'}
                       for r in saved]
            return {'date': day, 'reviews': reviews, 'market_pct': None, 'state': 'historical_read_only',
                    'note': '只读已保存归因；缺历史窗口不使用今天行情补写'}
    if (now.hour, now.minute) < (15, 0):
        return {'date': day, 'reviews': [], 'market_pct': None, 'state': 'pending_close',
                'note': '尚未收盘，完整表现窗口未结束'}

    generated = generated_time(meta)
    version = selection_version(day, meta, items)
    preopen = bool(version and generated and generated <= now and generated.hour * 60 + generated.minute < 570)
    window_kind = 'open_to_close' if preopen else 'unavailable_post_selection_path'
    window_reason = None if preopen else '入选发生在开盘后或生成时点不明，缺入选后路径；全日OHLC不能证明买点触及'
    if generated and generated.hour * 60 + generated.minute >= 900:
        window_reason = '盘后生成：本日入选后的交易观察窗口尚不存在'

    market_quote = None
    try:
        market_quote = next((q for q in hub.get_indices() if q.symbol in ('000001', 'sh000001')), None)
    except Exception:
        pass
    market_pct = None
    if preopen and _close_quote(market_quote, day, now):
        base_open, base_close = finite_number(market_quote.open), finite_number(market_quote.price)
        if base_open and base_open > 0 and base_close and base_close > 0:
            market_pct = (base_close / base_open - 1) * 100
    review_phase = None
    try:
        from app.services.market_context import compute_market_sentiment
        review_phase = (await compute_market_sentiment(hub, snapshot_service) or {}).get('phase')
    except Exception as exc:
        log.warning('picks review: sentiment failed: %s', exc)

    quotes = await batch_quotes(hub, [i['symbol'] for i in items])
    reviews = []
    for it in items:
        sym = it['symbol']
        q = quotes.get(sym)
        close_ready = _close_quote(q, day, now)
        start = finite_number(q.open) if close_ready else None
        close = finite_number(q.price) if close_ready else None
        change = (close / start - 1) * 100 if preopen and start and start > 0 and close and close > 0 else None
        excess = round(change - market_pct, 2) if change is not None and market_pct is not None else None
        entry = review_entry_quality(buy_range=it.get('buy_range'),
            day_open=start if preopen else None,
            day_high=q.high if preopen and close_ready else None,
            day_low=q.low if preopen and close_ready else None,
            day_close=close if preopen else None, observation_only=bool(it.get('observation_only')))
        if window_reason:
            entry['basis'] = window_reason
        category, note = classify_failure(excess_pct=excess, entry=entry, market_phase=review_phase)
        if excess is None:
            note = f'[窗口或基准缺失] {note}'
        if not close_ready:
            note += '；缺本日可信收盘源报价，未用接收时间补充'
        note += f'；{entry["basis"]}'
        verdict = {'missed': 'flat', 'entry_bad': 'bad', 'sentiment_misread': 'bad',
                   'logic_failed': 'bad', 'gone_well': 'good', 'flat': 'flat'}.get(category, 'flat')
        reference = finite_number(it.get('price'))
        reference_ready = _reference_ready(it, generated, day)
        reference_audit = it.get('quote_audit') if isinstance(it.get('quote_audit'), dict) else {}
        context = {'contract_version': REVIEW_VERSION, 'selection_version': version, 'symbol': sym,
            'performance_date': day, 'generated_at': meta.get('generated_at'), 'window_kind': window_kind,
            'window_start': f'{day}T09:30:00+08:00' if preopen else meta.get('generated_at'),
            'window_end': f'{day}T15:00:00+08:00', 'quote': _source(q), 'benchmark': _source(market_quote),
            'eligible_for_stats': bool(preopen and close_ready and excess is not None and not it.get('observation_only')),
            'observation_only': bool(it.get('observation_only')), 'window_reason': window_reason,
            'echelon_role': it.get('echelon_role'), 'market_phase': meta.get('market_phase'),
            'reference_price': reference, 'reference_as_of': reference_audit.get('data_timestamp'),
            'reference_window_reason': None if reference_ready else '参考价源时点/质量与本次入选窗口不匹配，不能用本次生成时间替代旧价源时间',
            'reference_return_pct': round((close / reference - 1) * 100, 2) if close and reference and reference > 0 and reference_ready else None,
            'semantics': '价格观察代理，不证明成交或A股T+1可实现收益'}
        reviews.append({'date': day, 'symbol': sym, 'name': it.get('name'), 'verdict': verdict,
                        'reason_category': category, 'excess_pct': excess, 'note': note,
                        'entry': entry, 'market_phase': review_phase, 'review_context': context})

    if version is None:
        return {'date': day, 'reviews': reviews, 'market_pct': None, 'state': 'unbound_generation',
                'note': '生成身份缺失或不一致；显示缺口，不改写已有历史证据'}

    with session_factory() as db:
        locked = db.execute(update(DailyPickSet).where(DailyPickSet.date == raw_date,
                             DailyPickSet.items == raw_items, DailyPickSet.meta == raw_meta).values(meta=DailyPickSet.meta))
        if locked.rowcount != 1:
            db.rollback()
            return {'date': day, 'reviews': [], 'market_pct': None, 'state': 'superseded',
                    'note': '组合版本在复盘期间改变；旧结果未写入新版本'}
        for r in reviews:
            existing = db.scalar(select(DailyPickReview).where(DailyPickReview.date == day,
                                                                DailyPickReview.symbol == r['symbol'],
                                                                DailyPickReview.selection_version == version).order_by(DailyPickReview.id.desc()))
            fields = {key: r[key] for key in ('name', 'verdict', 'reason_category', 'excess_pct', 'note')}
            fields['review_context'] = json.dumps(r['review_context'], ensure_ascii=False)
            if existing is None:
                db.add(DailyPickReview(date=day, symbol=r['symbol'], selection_version=version, **fields))
            else:
                for key, value in fields.items():
                    setattr(existing, key, value)
        db.commit()
    return {'date': day, 'reviews': reviews, 'market_pct': market_pct, 'state': 'completed'}
