"""Pure read projections. Never select, label outcomes, fetch quotes or send notifications."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
import json
import heapq
from fractions import Fraction
import math
from statistics import mean, median
from sqlalchemy import and_, or_, select
from app.core.bjtime import BJ_TZ, beijing_now, to_beijing
from app.core.db import get_session_factory
from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionSnapshot as Snapshot
from app.models.real_position import RealTrade
from app.picks.selection_entry import CONTRACT, _object, _number


def _clock(value):
    try:
        t = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return t.astimezone(BJ_TZ) if t.tzinfo else None
    except (ValueError, TypeError):
        return None


def _daily_range(start, end):
    return or_(and_(DailyPickSet.date >= start, DailyPickSet.date <= end),
               and_(DailyPickSet.date >= start.replace('-', ''), DailyPickSet.date <= end.replace('-', '')))


def _daily_rows(db, start, end):
    from app.picks.review_contract import iso_day
    # ISO supersedes the retained compact-date legacy row of the same calendar day.
    days = {}
    for row in db.scalars(select(DailyPickSet).where(_daily_range(start, end)).order_by(DailyPickSet.date.desc())):
        day = iso_day(row.date)
        if day and (day not in days or '-' in row.date):
            days[day] = row
    return days


def selection_rows(db, start, end, *, symbol=None, daily_rows=None, preserve_versions=False):
    entries = []
    days = _daily_rows(db, start, end) if daily_rows is None else daily_rows
    for day, row in days.items():
        meta = _object(row.meta)
        try:
            items = json.loads(row.items or '[]')
        except (ValueError, TypeError):
            items = []
        names = {i.get('symbol'): i.get('name') for i in items if isinstance(i, dict)} if isinstance(items, list) else {}
        recorded = meta.get('selection_entries')
        recorded = recorded if isinstance(recorded, dict) else {}
        for member in sorted(set(names) | set(recorded)):
            if symbol is not None and member != symbol:
                continue
            e = _object(recorded.get(member))
            entries.append(dict(id=f'daily:{day}:{member}', date=day, symbol=member,
                name=e.get("name") or names.get(member) or member, scope='daily', snapshot_id=None, state=e.get('state', 'missing'),
                selected_at=e.get('selected_at'), reference_price=_number(e.get('reference_price'), positive=True),
                reference_change_pct=_number(e.get('reference_change_pct')), quote_as_of=e.get('quote_as_of'),
                version=e.get('source_version'), method_version=e.get('strategy_version'), feature_version=e.get('feature_version'), reason='；'.join(f'{k}：{v}' for k,v in _object(e.get('selection_basis')).items()) or e.get('reason') or '首次入选的完整依据尚未归档，未用当前版本补填',
                original_shape=_object(e.get('original_features')).get('stage'), original_environment=_object(e.get('rule_context')).get('market_phase'),
                conditions=[str(v) for v in e.get('invalidations') or []], return_pct=None, outcome_state='no_matching_archive'))
    rows = db.scalars(select(Snapshot).where(Snapshot.trade_date >= start, Snapshot.trade_date <= end,
        Snapshot.scenario == 'intraday_opportunity', Snapshot.stage == 'rank', Snapshot.decision == 'ranked',
        *([Snapshot.symbol == symbol] if symbol else []))
        .order_by(Snapshot.created_at, Snapshot.id).limit(50001)).all()
    if len(rows) > 50000:
        raise ValueError('归档过多，请缩短范围；未截断统计分母')
    seen = set()
    for row in rows:
        e = _object(row.evidence)
        at = to_beijing(row.created_at.replace(tzinfo=timezone.utc) if row.created_at.tzinfo is None else row.created_at)
        key = (row.trade_date, row.symbol, row.strategy_version, row.feature_version) if preserve_versions else (row.trade_date, row.symbol)
        limit = e.get('effective_limit')
        if (key in seen or e.get('selection_entry_contract') != CONTRACT or e.get('within_display_capacity') != 1
                or not isinstance(limit, (int, float)) or row.rank is None or not 1 <= row.rank <= limit
                or at.date().isoformat() != row.trade_date):
            continue
        seen.add(key)
        price = _number(row.entry_price, positive=True)
        sources = e.get('theme_sources')
        original_theme = _object(sources[0]) if isinstance(sources, list) and sources else {}
        entries.append(dict(id=f'intraday:{row.trade_date}:{row.symbol}', date=row.trade_date, symbol=row.symbol,
            name=row.name or row.symbol, scope='intraday', snapshot_id=row.snapshot_id,
            state='recorded' if price and row.data_state == 'ready' else 'partial', selected_at=at.isoformat(),
            reference_price=price, reference_change_pct=_number(e.get('change_pct')), version=row.run_id, method_version=row.strategy_version, feature_version=row.feature_version,
            quote_as_of=row.as_of.replace(tzinfo=BJ_TZ).isoformat(),
            reason=str(e.get('reason') or e.get('selection_reason') or row.source_theme or '原候选排名已归档'),
            conditions=[str(e[k]) for k in ('entry_condition', 'invalidation', 'risk_reason') if e.get(k)],
            original_shape=e.get('stage') or original_theme.get('stage'),
            original_environment=e.get('market_phase'),
            return_pct=None, outcome_state='pending'))
    daily_projections = db.scalars(select(Snapshot).where(Snapshot.trade_date >= start,
        Snapshot.trade_date <= end, Snapshot.scenario == 'daily_selection_validation',
        Snapshot.stage == 'validation', Snapshot.decision == 'observed',
        *([Snapshot.symbol == symbol] if symbol else [])).limit(50001)).all()
    if len(daily_projections) > 50000:
        raise ValueError('归档过多，请缩短范围；未截断统计分母')
    by_id = {e['id']: e for e in entries if e['scope'] == 'daily'}
    for row in daily_projections:
        evidence = _object(row.evidence)
        entry = by_id.get(evidence.get('selection_id'))
        original = _object(evidence.get('original_entry'))
        if entry and entry['selected_at'] == original.get('selected_at') and entry['version'] == original.get('source_version') and entry['reference_price'] == row.entry_price:
            entry['snapshot_id'] = row.snapshot_id
            entry['outcome_state'] = 'pending'
    return sorted(entries, key=lambda e: (e['date'], e['selected_at'] or '', e['id']))


def validate_trade_selection(db, symbol, ref, executed):
    if not ref or executed.tzinfo is None:
        raise ValueError('买入必须关联成交前已存在的同股入选，并填写准确成交时间')
    parts = ref.split(':')
    if len(parts) != 3 or parts[0] not in ('daily', 'intraday') or parts[2] != symbol:
        raise ValueError('入选关联与成交股票不一致')
    try:
        day = date.fromisoformat(parts[1]).isoformat()
    except ValueError as exc:
        raise ValueError('入选身份无效') from exc
    e = next((e for e in selection_rows(db, day, day, symbol=symbol) if e['id'] == ref), None)
    joined = _clock(e.get('selected_at')) if e else None
    if not e or e['state'] != 'recorded' or not joined or joined >= executed.astimezone(BJ_TZ):
        raise ValueError('入选必须有效且严格早于实际成交，不允许用后续入选倒填')
    return {k: e[k] for k in ('id', 'date', 'symbol', 'scope', 'selected_at', 'reference_price', 'version', 'reason')}


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def trade_episodes(trades, *, calendar, as_of):
    groups, episodes = defaultdict(list), []
    for t in trades:
        groups[t.symbol].append(t)
    for symbol, rows in groups.items():
        rows.sort(key=lambda t: (t.traded_at, t.executed_at or datetime.min, t.id or 0))
        c = None
        ambiguous_days = {t.traded_at for t in rows if t.executed_at is None}
        tainted = False
        for t in rows:
            executed = t.executed_at.replace(tzinfo=timezone.utc) if t.executed_at else None
            if c is None:
                c = dict(id=t.id, symbol=symbol, name=t.name or symbol, opened_at=to_beijing(executed).isoformat() if executed else None,
                    open_date=t.traded_at, closed_at=None, close_date=None, quantity=0, cost=0.0, proceeds=0.0,
                    buy_fee=0.0, sell_fee=0.0, selection_ref=t.selection_ref, entry_reason=_object(t.selection_evidence).get('reason') or '原依据未记录', issues=set(), fill_ids=[], return_pct=None)
            c['fill_ids'].append(t.id)
            if tainted:
                c['issues'].add('此前持仓流水无法配平')
            if t.traded_at in ambiguous_days:
                c['issues'].add('同日存在无准确时刻流水，顺序未核')
            if not executed:
                c['issues'].add('缺准确成交时刻')
            if not t.fee_known:
                c['issues'].add('费用未确认完整')
            if not (_finite(t.fill_price) and t.fill_price > 0 and isinstance(t.quantity, int) and t.quantity > 0 and _finite(t.fee) and t.fee >= 0):
                c['issues'].add('流水数值无效')
                continue
            if t.side == 'buy':
                evidence = _object(t.selection_evidence)
                joined = _clock(evidence.get('selected_at'))
                if (not t.selection_ref or evidence.get('id') != t.selection_ref or evidence.get('symbol') != symbol
                        or not joined or not executed or joined >= executed):
                    c['issues'].add('缺有效的成交前入选关联')
                c['quantity'] += t.quantity
                c['cost'] += t.fill_price * t.quantity
                c['buy_fee'] += t.fee
            elif t.side == 'sell':
                if t.quantity > c['quantity']:
                    c['issues'].add('卖出超过已记录持仓')
                    tainted = True
                c['quantity'] -= t.quantity
                c['proceeds'] += t.fill_price * t.quantity
                c['sell_fee'] += t.fee
            else:
                c['issues'].add('流水方向无效')
            if c['quantity'] <= 0:
                c['close_date'] = t.traded_at
                c['closed_at'] = to_beijing(executed).isoformat() if executed else None
                episodes.append(c); c = None
        if c:
            episodes.append(c)
    days = sorted(set(calendar))
    for e in episodes:
        start, end = e['open_date'], e['close_date'] or as_of.isoformat()
        covered = bool(days and start and end and days[0].isoformat() <= start <= end <= days[-1].isoformat())
        held = [d.isoformat() for d in days if start <= d.isoformat() <= end] if covered else None
        e['holding_trade_days'], e['exposure_days'] = len(held) if held is not None else None, held
        try:
            e['holding_calendar_days'] = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        except ValueError:
            e['holding_calendar_days'] = None
        e['state'] = 'open' if not e['close_date'] else 'excluded' if e['issues'] or e['quantity'] else 'closed'
        cost = e['cost'] + e['buy_fee']
        if e['state'] == 'closed' and cost > 0:
            e['return_pct'] = round(e['proceeds'] - e['sell_fee'] - cost, 2) / cost * 100
        e['issues'] = sorted(e['issues'])
    return sorted(episodes, key=lambda e: (e['open_date'], e['id'] or 0))


def _win_curve(episodes):
    """Accumulate each completed episode once; preserve exact float means and medians."""
    lower, upper, curve = [], [], []
    total, wins, count = Fraction(), 0, 0
    for episode in sorted((e for e in episodes if e['state'] == 'closed'),
                          key=lambda e: (e['closed_at'] or '', e['id'] or 0)):
        value = episode['return_pct']
        if value is None:
            continue
        count += 1
        wins += value > 0
        total += Fraction(value)
        heapq.heappush(lower, -value)
        heapq.heappush(upper, -heapq.heappop(lower))
        if len(upper) > len(lower):
            heapq.heappush(lower, -heapq.heappop(upper))
        middle = -lower[0] if len(lower) > len(upper) else (-lower[0] + upper[0]) / 2
        curve.append(dict(date=episode['close_date'], episode_id=episode['id'], denominator=count,
                          win_rate=wins/count*100, mean_pct=float(total/count), median_pct=middle))
    return curve


def _coverage(entries, *, calendar, horizon):
    """Separate absent evidence from outcomes that have not matured; no weekday guesses."""
    steps = {'d0_close': 0, 'd1_close': 1, 'd3_close': 3, 'd5_close': 5}[horizon]
    days = sorted({d.isoformat() for d in calendar})
    positions = {day: i for i, day in enumerate(days)}
    labels = {'observed': '已有同口径结果', 'original_missing': '原入选依据缺项',
              'not_due': '观察尚未到期', 'calendar_unknown': '观察日历未覆盖',
              'result_missing': '已到期，结果待补', 'basis_mismatch': '结果口径不一致'}
    counts = dict.fromkeys(labels, 0)
    now = beijing_now()
    versions = defaultdict(list)
    for entry in entries:
        if entry['state'] != 'recorded':
            state = 'original_missing'
        elif entry['return_pct'] is not None:
            state = 'observed'
        else:
            target = entry.get('outcome_date')
            index = positions.get(entry['date'])
            if not target and index is not None and index + steps < len(days):
                target = days[index + steps]
            not_closed = target == now.date().isoformat() and (now.hour, now.minute) < (15, 0)
            if target and (target > now.date().isoformat() or not_closed):
                state = 'not_due'
            elif entry['outcome_state'] == 'basis_or_window_unavailable':
                state = 'basis_mismatch'
            else:
                state = 'calendar_unknown' if not target else 'result_missing'
        entry['coverage_state'] = state
        counts[state] += 1
        versions[(entry['scope'], entry.get('method_version'), entry.get('feature_version'))].append(entry)
    groups = []
    for (scope, method, feature), rows in sorted(versions.items(), key=lambda pair: str(pair[0])):
        values = [e['return_pct'] for e in rows if e['return_pct'] is not None]
        groups.append(dict(scope=scope, method_version=method, feature_version=feature,
                           version_state='recorded' if method and feature else 'missing',
                           count=len(rows), observed=len(values), observed_days=len({e['date'] for e in rows if e['return_pct'] is not None}),
                           mean_pct=mean(values) if values else None, median_pct=median(values) if values else None))
    strata_rows = defaultdict(list)
    for entry in entries:
        at = _clock(entry.get('selected_at'))
        band = ('盘前' if (at.hour, at.minute) < (9, 30) else '上午' if at.hour < 12 else '下午' if (at.hour, at.minute) < (15, 0) else '盘后') if at else '未记录'
        for dimension, value in [('entry_time', band), ('shape', entry.get('original_shape')), ('environment', entry.get('original_environment'))]:
            strata_rows[(dimension, str(value or '未记录'), entry['scope'], entry.get('method_version'), entry.get('feature_version'))].append(entry)
    strata = []
    for (dimension, label, scope, method, feature), rows in sorted(strata_rows.items(), key=lambda pair: str(pair[0])):
        values = [e['return_pct'] for e in rows if e['return_pct'] is not None]
        strata.append(dict(dimension=dimension, label=label, scope=scope, method_version=method, feature_version=feature,
                           count=len(rows), observed=len(values), observed_days=len({e['date'] for e in rows if e['return_pct'] is not None}),
                           mean_pct=mean(values) if values else None, median_pct=median(values) if values else None))
    return dict(reasons=[dict(state=state, label=label, count=counts[state]) for state, label in labels.items()],
                versions=groups, strata=strata, basis='按原始方法与特征版本分组；未记录版本不补成当前版本。观察日历仅用现有官方记录。')


def _payoff(values):
    wins, losses = [v for v in values if v > 0], [v for v in values if v < 0]
    return dict(mean_win_pct=mean(wins) if wins else None, mean_loss_pct=mean(losses) if losses else None,
                payoff_ratio=mean(wins)/abs(mean(losses)) if wins and losses else None,
                profit_factor=sum(wins)/abs(sum(losses)) if losses else None,
                net_expectancy_pct=mean(values) if values else None,
                drawdown_pct=None, drawdown_basis='缺少逐日完整账户净值，不能用重叠成交回合拼出账户回撤')


def _version_identity(scope, method, feature):
    method = method if isinstance(method, str) and method else None
    feature = feature if isinstance(feature, str) and feature else None
    return scope, method, feature


def _selection_projection(entries, known_days, *, calendar, horizon):
    values = [e['return_pct'] for e in entries if e['return_pct'] is not None]
    by_day = defaultdict(list)
    for entry in entries:
        by_day[entry['date']].append(entry)
    daily = []
    for day in sorted(set(by_day) | set(known_days)):
        members = by_day[day]
        observed = [e['return_pct'] for e in members if e['return_pct'] is not None]
        daily.append(dict(date=day, count=len(members), observed=len(observed),
            mean_pct=mean(observed) if observed else None, median_pct=median(observed) if observed else None))
    return dict(selection=dict(count=len(entries), observed=len(values), missing=len(entries)-len(values),
                    mean_pct=mean(values) if values else None, median_pct=median(values) if values else None),
                daily=daily, coverage=_coverage(entries, calendar=calendar, horizon=horizon))


def _version_groups(entries, version_days, *, calendar, horizon):
    groups = defaultdict(list)
    for entry in entries:
        groups[_version_identity(entry['scope'], entry.get('method_version'), entry.get('feature_version'))].append(entry)
    result = []
    for identity in sorted(set(groups) | set(version_days), key=str):
        rows = groups[identity]
        scope, method, feature = identity
        observed_days = sorted({row['date'] for row in rows if row['return_pct'] is not None})
        dates = sorted(set(version_days.get(identity, ())) | {row['date'] for row in rows})
        result.append(dict(key=json.dumps(identity, ensure_ascii=False, separators=(',', ':')),
            scope=scope, method_version=method, feature_version=feature,
            version_state='recorded' if method and feature else 'missing', entry_ids=[row['id'] for row in rows],
            window=dict(first_entry_date=dates[0] if dates else None, last_entry_date=dates[-1] if dates else None,
                        observed_start=observed_days[0] if observed_days else None,
                        observed_end=observed_days[-1] if observed_days else None,
                        observed_days=len(observed_days), observed_dates=observed_days),
            **_selection_projection(rows, dates, calendar=calendar, horizon=horizon)))
    return result


def validation_report(start, end, *, session_factory=None, calendar=None, horizon='d1_close', scope='all'):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if start != first.isoformat() or end != last.isoformat():
        raise ValueError('日期须使用 YYYY-MM-DD 格式')
    if first > last or (last-first).days > 366 or last > beijing_now().date():
        raise ValueError('请选择已发生且不超过367天的范围')
    if horizon not in ('d0_close', 'd1_close', 'd3_close', 'd5_close') or scope not in ('all', 'daily', 'intraday'):
        raise ValueError('统计范围或观察周期无效')
    sf = session_factory or get_session_factory()
    if calendar is None:
        from app.market.trade_calendar import known_trade_days
        calendar = known_trade_days()
    with sf() as db:
        daily_rows = _daily_rows(db, start, end)
        entries = selection_rows(db, start, end, daily_rows=daily_rows, preserve_versions=True)
        version_days = defaultdict(set)
        if scope != 'intraday':
            daily_entry_days = {entry['date'] for entry in entries if entry['scope'] == 'daily'}
            for day, row in daily_rows.items():
                # Only a genuinely saved empty list proves a zero-selection day.
                # Nonempty entries carry their own original version below.
                try:
                    empty = json.loads(row.items) == []
                except (ValueError, TypeError):
                    empty = False
                if empty and day not in daily_entry_days:
                    contract = _object(_object(row.meta).get('selection_contract'))
                    version_days[_version_identity('daily', contract.get('strategy_version'), contract.get('feature_version'))].add(day)
        known_days = set(daily_rows) if scope != 'intraday' else set()
        if scope != 'daily':
            from app.models.opportunity_learning import OpportunityDecisionRun
            from app.picks.opportunity_learning import FEATURE_VERSION
            runs = db.execute(select(OpportunityDecisionRun.trade_date, OpportunityDecisionRun.strategy_version,
                OpportunityDecisionRun.feature_version, OpportunityDecisionRun.rank_rows).where(
                OpportunityDecisionRun.trade_date >= start, OpportunityDecisionRun.trade_date <= end,
                OpportunityDecisionRun.scenario == 'intraday_opportunity', OpportunityDecisionRun.data_state == 'ready').limit(50001)).all()
            if len(runs) > 50000:
                raise ValueError('运行归档过多，请缩短范围；未截断版本分母')
            known_days.update(run.trade_date for run in runs if run.feature_version == FEATURE_VERSION)
            for run in runs:
                if run.rank_rows == 0:
                    version_days[_version_identity('intraday', run.strategy_version, run.feature_version)].add(run.trade_date)
        from app.picks.opportunity_learning import read_effective_outcomes, PRICE_BASIS_VERSION
        outcomes = read_effective_outcomes(db, [e['snapshot_id'] for e in entries if e['snapshot_id']], horizon)
        trades = db.scalars(select(RealTrade).where(RealTrade.traded_at <= end).order_by(RealTrade.traded_at, RealTrade.id).limit(50001)).all()
        if len(trades) > 50000:
            raise ValueError('成交流水超过上限；未截断胜率分母')
        episodes = trade_episodes(trades, calendar=calendar, as_of=last)
        legacy = sum(not t.executed_at or not t.selection_ref and t.side == 'buy' for t in trades if start <= t.traded_at <= end)
    observation_now = beijing_now()
    for e in entries:
        o = outcomes.get(e['snapshot_id'])
        if o:
            e.update(outcome_state=o.state, outcome_date=o.target_date, outcome_reason=o.reason, tradability=o.fill_state)
            join_clock = _clock(e['selected_at'])
            close_clock = datetime.fromisoformat(o.target_date).replace(hour=15, tzinfo=BJ_TZ)
            if (join_clock and join_clock < close_clock and e['state'] == 'recorded' and o.state == 'labeled' and o.price_basis_version == PRICE_BASIS_VERSION
                    and _finite(o.return_pct) and close_clock <= observation_now and e['reference_price'] == o.reference_price):
                e['return_pct'] = o.return_pct
            elif o.state == 'labeled':
                e['outcome_state'] = 'basis_or_window_unavailable'
            e['outcome_reason'] = o.reason if e['return_pct'] is None else None
        e['observation_calendar_days'] = max(0, (last-date.fromisoformat(e['date'])).days)
    if scope != 'all':
        entries = [e for e in entries if e['scope'] == scope]
    # Group before the default cross-source day/symbol deduplication. A daily
    # method and an intraday method with the same names remain separate facts.
    version_entries = [dict(entry, id=f"{entry['id']}@{entry['snapshot_id']}" if entry['scope']=='intraday' else entry['id'],
                            selection_refs=[entry['id']]) for entry in entries]
    version_groups = _version_groups(version_entries, version_days, calendar=calendar, horizon=horizon)
    unique = {}
    for e in entries:
        key = e['date'], e['symbol']
        if key not in unique:
            unique[key] = dict(e, selection_refs=[e['id']])
        elif e['id'] not in unique[key]['selection_refs']:
            unique[key]['selection_refs'].append(e['id'])
    entries = list(unique.values())
    def avg(v): return mean(v) if v else None
    def med(v): return median(v) if v else None
    observed = [e['return_pct'] for e in entries if e['return_pct'] is not None]
    daily = []
    by_day = defaultdict(list)
    for entry in entries:
        by_day[entry['date']].append(entry)
    for day in sorted(set(by_day) | known_days):
        members = by_day[day]
        values = [e['return_pct'] for e in members if e['return_pct'] is not None]
        daily.append(dict(date=day, count=len(members), observed=len(values), mean_pct=avg(values), median_pct=med(values)))
    episodes = [e for e in episodes if start <= (e['close_date'] or e['open_date']) <= end]
    values = [e['return_pct'] for e in episodes if e['state'] == 'closed' and e['return_pct'] is not None]
    curve = _win_curve(episodes)
    exposures = [e['exposure_days'] for e in episodes]
    covered = all(d is not None for d in exposures)
    cohorts = []
    by_reason = defaultdict(list)
    for episode in episodes:
        by_reason[episode['entry_reason']].append(episode)
    for reason, group in sorted(by_reason.items()):
        returns = [e['return_pct'] for e in group if e['state'] == 'closed' and e['return_pct'] is not None]
        cohorts.append(dict(reason=reason, total=len(group), denominator=len(returns),
            wins=sum(v > 0 for v in returns), losses=sum(v < 0 for v in returns),
            mean_pct=avg(returns), median_pct=med(returns)))
    coverage = _coverage(entries, calendar=calendar, horizon=horizon)
    return dict(start=start, end=end, scope=scope, horizon=horizon, coverage=coverage,
        version_entries=version_entries, version_groups=version_groups,
        selection=dict(count=len(entries), observed=len(observed), missing=len(entries)-len(observed), mean_pct=avg(observed), median_pct=med(observed)),
        actual=dict(**_payoff(values), denominator=len(values), win_rate=sum(v > 0 for v in values)/len(values)*100 if values else None,
            mean_pct=avg(values), median_pct=med(values), wins=sum(v > 0 for v in values), losses=sum(v < 0 for v in values), flat=sum(v == 0 for v in values),
            open=sum(e['state'] == 'open' for e in episodes), excluded=sum(e['state'] == 'excluded' for e in episodes), legacy_fills=legacy,
            exposure_trade_days=len(set(d for days in exposures for d in days)) if covered else None,
            episode_trade_days=sum(len(days) for days in exposures) if covered else None),
        entries=entries, episodes=episodes, daily=daily, win_curve=curve, cohorts=cohorts,
        basis=dict(actual='人工成交流水，未经券商核验；按清仓日统计，未平仓按首买日；含双边已确认费用',
            selection='日期筛选原入选日；每日同股去重，结果读取至当前已归档且到期的固定收盘观察，不代表成交或买入胜率',
            coverage='缺原时点、同基准结果或完整费用时保留未知；只使用已归档且同参考价的固定周期结果，旧缺项不倒填',
            improvement='原依据与失效条件→复盘建议→人工确认→工程证据→前向验证；不自动改选股或通知规则'))
