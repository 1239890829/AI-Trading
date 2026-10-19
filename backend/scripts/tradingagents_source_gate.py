"""Bounded, deterministic source-capability gate for this LAB.

This is not a general natural-language entailment verifier. It recognizes the
declared price-path/aggregate/execution claim families below. Unknown wording,
source truth, causality, forecasts and investment quality still need source
review. PASS means no violation was found in this finite checked scope.

Optional positive daily evidence uses facts.price_history with explicit symbol,
market, version, price_basis, frequency='1d', sessions, and observations. Every
observation has session, prev_close, close, final=True, session_close_at,
published_at and available_at. Each day's prev_close must equal the preceding
close, including a supplied first-day prev_close. facts.trading_calendar must
have market, version, complete=True, window_start, window_end, sessions,
published_at and available_at. The requested window must be a contiguous slice
of that explicit calendar. A list of observed dates does not prove continuity.
No formatted table, news tone or model report is converted into daily prices.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from app.core.bjtime import BJ_TZ

VERSION = 'bounded-source-capability-v1'
DAILY = re.compile(
    r'连续(?:\s*[一二三四五六七八九十\d]+\s*(?:个)?(?:交易日|日|天))?\s*(?:上涨|收涨|走高|上行|抬升|攀升|反弹)(?:\s*[一二三四五六七八九十\d]+\s*(?:个)?(?:交易日|日|天))?'
    r'|(?:[一二三四五六七八九十\d]+)?连涨(?:\s*[一二三四五六七八九十\d]+\s*(?:个)?(?:交易日|日|天))?'
    r'|(?:每日|每天|逐日|各交易日|每个交易日).{0,8}(?:上涨|收涨|走高|上行|抬升)'
    r'|(?:收盘价|价格).{0,5}(?:一日高过一日|一天比一天高|一日比一日高)'
    r'|(?:持续|接连)(?:上涨|上行|走高)(?:\s*[一二三四五六七八九十\d]+\s*(?:个)?(?:交易日|日|天))?'
    r'|(?:rose|rises|rising|higher).{0,12}(?:every\s+day|consecutive\s+(?:days|sessions))'
    r'|(?:consecutive|successive)\s+(?:daily\s+)?(?:gains|rises|advances)', re.I)
AGGREGATE = re.compile(r'累计(?:收益率|收益|回报率|回报|涨跌幅|涨幅|上涨)(?!偏离)')
CONTINUOUS_TRADE = re.compile(r'(?:连续|持续|不间断|全程)(?:成交|有成交|可成交|交易(?!日))|continuous\s+(?:trading|execution)', re.I)
EXECUTION = re.compile(r'(?:保证|必然|一定|确定|能够|可以|可)(?:顺利)?(?:成交|买到|卖出|买入|卖掉)|guaranteed\s+(?:fill|execution)', re.I)
FUTURE = re.compile(r'(?:随后|之后|后续|接下来|明日|明天|未来).{0,14}(?:必然|一定|保证|确定|将会).{0,10}(?:封板|涨停|上涨|成交|反弹)|(?:必然|一定|保证).{0,8}(?:封板|涨停)')
SNAPSHOT_UNIVERSAL = re.compile(r'(?:open|开盘).{0,50}(?:low|最低).{0,35}(?:必然|必定|始终|一直|全程|废信息)', re.I)
NEGATIVE = re.compile(
    r'(?:不能|无法|不足以|不应|不宜|尚不能|尚无法|未能|未见证据)(?:据此|仅凭|从中|直接)?'
    r'(?:证明|认定|确认|推断|推出|说明|支持|断言|保证|还原|确定|得出|视为|判断|生成|推导|外推|理解为|当作|当成|解释为|等同于|变成|转为)'
    r'|(?:不代表|不等于|并不意味着|不意味着|不是.{0,8}证据|尚未证实|未获证实|缺乏证据证明)'
    r'|不(?:证明|支持|保证|意味着|等于)'
    r'|不能(?:把|将).{0,28}(?:当作|视为|解释为|等同于|推出)'
    r'|(?:不能|无法|不应)(?:直接)?(?:由|把|将).{0,30}(?:推导|推演为|推演成|说成|变成|转成|认定为)'
    r'|(?:cannot|does\s+not|insufficient\s+to)\s+(?:prove|establish|infer|confirm|guarantee)', re.I)
CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _identity(value):
    match = re.fullmatch(r'(\d{6})(?:\.(SH|SS|SZ|BJ))?', str(value).upper())
    return (match.group(1), {'SS': 'SH'}.get(match.group(2), match.group(2))) if match else (None, None)


def _eligible_times(row, as_of):
    published, available = _time(row.get('published_at')), _time(row.get('available_at'))
    return bool(as_of and published and available and published <= available <= as_of)


def _news_subject(row, security):
    symbol = security.get('symbol')
    ids = {_identity(value)[0] for value in row.get('source_symbols', [])}
    body = str(row.get('title', '')) + '\n' + str(row.get('summary', ''))
    codes = set(re.findall(r'(?<!\d)\d{6}(?!\d)', body))
    return bool(symbol and symbol in ids and (symbol in codes or security.get('name') and security['name'] in body)
                and not (codes and symbol not in codes))


def _source_daily_subject(clause, start, security):
    # A document mentioning the target does not make every company's path the
    # target's path. Only this explicit, local target construction is licensed.
    names = [re.escape(str(value)) for value in (security.get('name'), security.get('symbol')) if value]
    target = '(?:' + '|'.join(names) + ')'
    return bool(names and re.search(target + r'(?:[（(]\d{6}(?:\.(?:SH|SZ|BJ))?[）)])?'
                                  r'(?:的)?(?:A股股票|A股|股票|股价|收盘价)?(?:近日|已|近)?\s*$', clause[:start]))


def _daily_capability(facts, security, as_of):
    block = facts.get('price_history')
    result = {'supported': False, 'pointer': '/facts/price_history', 'reason': 'NO_TYPED_DAILY_HISTORY'}
    if not isinstance(block, dict):
        return result
    symbol, suffix = _identity(block.get('symbol'))
    market = block.get('market')
    if (symbol != security.get('symbol') or market != security.get('market') or suffix and suffix != market
            or block.get('frequency') != '1d' or not block.get('version') or not block.get('price_basis')):
        return {**result, 'reason': 'IDENTITY_VERSION_OR_DAILY_BASIS_UNPROVEN'}
    sessions, observations = block.get('sessions'), block.get('observations')
    if (not isinstance(sessions, list) or not isinstance(observations, list) or len(sessions) < 2
            or any(not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) for day in sessions)
            or sessions != sorted(set(sessions)) or len(observations) != len(sessions)):
        return {**result, 'reason': 'SESSION_CALENDAR_OR_COMPLETE_COVERAGE_UNPROVEN'}
    calendar = facts.get('trading_calendar')
    if (not isinstance(calendar, dict) or calendar.get('market') != market or not calendar.get('version')
            or calendar.get('complete') is not True or not _eligible_times(calendar, as_of)):
        return {**result, 'reason': 'COMPLETE_ELIGIBLE_SESSION_CALENDAR_UNPROVEN'}
    full_sessions = calendar.get('sessions')
    if (not isinstance(full_sessions, list) or not full_sessions
            or any(not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) for day in full_sessions)
            or full_sessions != sorted(set(full_sessions)) or not calendar.get('window_start')
            or not calendar.get('window_end') or calendar['window_start'] > full_sessions[0]
            or calendar['window_end'] < full_sessions[-1] or calendar['window_start'] > sessions[0]
            or calendar['window_end'] < sessions[-1]):
        return {**result, 'reason': 'INVALID_COMPLETE_SESSION_CALENDAR'}
    selected = [day for day in full_sessions if sessions[0] <= day <= sessions[-1]]
    if selected != sessions:
        return {**result, 'reason': 'NONCONTIGUOUS_OR_UNCOVERED_SESSION_WINDOW'}
    closes, previous_closes, paths = [], [], []
    for index, (day, row) in enumerate(zip(sessions, observations)):
        if not isinstance(row, dict):
            return {**result, 'reason': 'INVALID_DAILY_ROW'}
        close_time = _time(row.get('session_close_at'))
        pub = _time(row.get('published_at'))
        row_symbol, row_market = _identity(row.get('symbol', block['symbol']))
        if (row.get('session') != day or row.get('final') is not True or not close_time
                or close_time.date().isoformat() != day or not _eligible_times(row, as_of)
                or not pub or close_time > pub or row_symbol != symbol or row_market and row_market != market
                or row.get('market', market) != market
                or row.get('price_basis', block['price_basis']) != block['price_basis']
                or row.get('version', block['version']) != block['version']):
            return {**result, 'reason': 'ROW_IDENTITY_FINALITY_VERSION_OR_DUAL_TIME_UNPROVEN'}
        try:
            value, previous = Decimal(str(row.get('close'))), Decimal(str(row.get('prev_close')))
            if not value.is_finite() or value <= 0 or not previous.is_finite() or previous <= 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            return {**result, 'reason': 'INVALID_CLOSE'}
        closes.append(value)
        previous_closes.append(previous)
        paths.append({'pointer': '/facts/price_history/observations/' + str(index),
                      'row_sha256': _sha(_canonical(row)), 'session': day,
                      'published_at': row['published_at'], 'available_at': row['available_at']})
    if any(previous != prior for previous, prior in zip(previous_closes[1:], closes[:-1])):
        return {**result, 'reason': 'PREVIOUS_CLOSE_CHAIN_MISMATCH'}
    increases = [close > previous for previous, close in zip(previous_closes, closes)]
    trailing = 0
    for increase in reversed(increases):
        if not increase:
            break
        trailing += 1
    return {'supported': True, 'kind': 'PROVIDED_FINAL_DAILY_CLOSE_WINDOW', 'symbol': symbol, 'market': market,
            'version': block['version'], 'price_basis': block['price_basis'], 'sessions': sessions,
            'source_sha256': _sha(_canonical(block)), 'source_rows': paths,
            'calendar_pointer': '/facts/trading_calendar', 'calendar_sha256': _sha(_canonical(calendar)),
            'calendar_version': calendar['version'], 'calendar_published_at': calendar['published_at'],
            'calendar_available_at': calendar['available_at'], 'first_previous_close': str(previous_closes[0]),
            'positive_close_steps': increases, 'trailing_up_sessions': trailing,
            'cumulative_return_percent': str((closes[-1] / previous_closes[0] - 1) * 100),
            'limit': 'Provided final daily closes only; no intraday, future, execution or independent source-truth proof.'}


def source_semantics(packet):
    """Derive capabilities only from the supplied original source packet."""
    facts = packet.get('facts', {})
    security = facts.get('security', {})
    security = {**security, 'symbol': _identity(security.get('symbol'))[0]}
    as_of = _time(packet.get('as_of', facts.get('as_of')))
    reported, cumulative = [], []
    for index, row in enumerate(facts.get('candidate_news', [])):
        if not isinstance(row, dict) or not _eligible_times(row, as_of) or not _news_subject(row, security):
            continue
        for field in ('title', 'summary'):
            value = row.get(field, '')
            if not isinstance(value, str):
                continue
            metadata = {'record_id': row.get('record_id'), 'pointer': '/facts/candidate_news/' + str(index) + '/' + field,
                        'field_sha256_utf8': _sha(value), 'published_at': row['published_at'], 'available_at': row['available_at']}
            for offset, clause in _clauses(value):
                for match in DAILY.finditer(clause):
                    if (not _nonasserted(clause, match.start(), match.end(), 'DAILY_PRICE_PATH')
                            and not _outside_daily_scope(clause, match.start())
                            and _source_daily_subject(clause, match.start(), security)):
                        reported.append({**metadata, 'phrase': match.group(), 'start': offset + match.start(),
                                         'end': offset + match.end(),
                                         'subject_proof': 'EXPLICIT_LOCAL_TARGET_BEFORE_REPORTED_PATH',
                                         'kind': 'REPORTED_SOURCE_STATEMENT_NOT_INDEPENDENT_DAILY_PATH'})
            if re.search(r'(?:偏离值|偏离幅度).{0,12}累计|累计.{0,8}(?:偏离值|偏离幅度)', value):
                cumulative.append({**metadata, 'kind': 'CUMULATIVE_RELATIVE_DEVIATION_NOT_ABSOLUTE_DAILY_PATH'})
    snapshot = facts.get('market_snapshot', {})
    current_change = {'supported': False, 'reason': 'NO_ELIGIBLE_TYPED_CURRENT_QUOTE'}
    if isinstance(snapshot, dict) and isinstance(snapshot.get('data'), dict):
        data = snapshot['data']
        symbol, suffix = _identity(data.get('symbol'))
        received = _time(data.get('received_at'))
        if (symbol == security.get('symbol') and data.get('market') == security.get('market')
                and (not suffix or suffix == security.get('market')) and snapshot.get('status') == 'observed'
                and snapshot.get('received_before_as_of') is True and received and as_of and received <= as_of
                and isinstance(data.get('change_pct'), (str, int, float)) and not isinstance(data.get('change_pct'), bool)):
            try:
                change = Decimal(str(data['change_pct']))
                if change.is_finite():
                    current_change = {'supported': True, 'kind': 'REPORTED_CURRENT_INTRADAY_CHANGE_FIELD',
                                      'pointer': '/facts/market_snapshot/data/change_pct',
                                      'field_sha256': _sha(_canonical(data['change_pct'])), 'value_percent': str(change),
                                      'received_at': data['received_at'], 'source_clock_value': data.get('ticktime'),
                                      'source_full_datetime_independently_proven': False,
                                      'limit': 'Observed intraday field only; no final daily/history/account-return proof.'}
            except InvalidOperation:
                pass
    return {'version': VERSION, 'as_of': packet.get('as_of', facts.get('as_of')), 'security': security,
            'source_packet_sha256': _sha(_canonical(facts)), 'reported_daily_path_statements': reported,
            'capability_trust': 'Only normalized frozen source facts are consumed; a supplied calendar completeness flag is not independently verified real-market truth.',
            'cumulative_relative_deviation_sources': cumulative,
            'daily_close_capability': _daily_capability(facts, security, as_of),
            'current_intraday_change_capability': current_change,
            'snapshot': {'pointer': '/facts/market_snapshot', 'present': bool(snapshot),
                         'scope': snapshot.get('semantics', 'UNKNOWN') if isinstance(snapshot, dict) else 'UNKNOWN',
                         'independent_intraday_path': False, 'future_price': False, 'account_execution': False},
            'not_human_gold_or_general_entailment': True}


def _clauses(report):
    # Local scope, never a whole-document disclaimer. Contrastive clauses are
    # separate so a correct denial cannot wash a later assertion green.
    for outer in re.finditer(r'[^。；;\n，,]+', report):
        # Retain offsets while denying a preceding negative scope after a new
        # affirmative/contrastive connective. This is a finite scope rule.
        start = 0
        for connective in re.finditer(r'但是|然而|不过|可是|但|却|反而|而是|并且|而且|同时|此外|因此|所以|事实上', outer.group()):
            if connective.start() > start:
                yield outer.start() + start, outer.group()[start:connective.start()]
            start = connective.end()
        if start < len(outer.group()):
            yield outer.start() + start, outer.group()[start:]


def _nonasserted(clause, start, end, kind, paragraph_prefix=''):
    before, after = clause[:start], clause[end:]
    presupposed = bool(re.match(r'\s*(?:之?后|以后|的(?:风险|回撤|抛压|压力|影响|约束))', after))
    if not presupposed and kind in ('DAILY_PRICE_PATH', 'ABSOLUTE_CUMULATIVE_RETURN'):
        # A denied rewrite can list several wrong conclusions. Keep this
        # limited to one local paragraph and reject a new affirmative clause.
        context = paragraph_prefix + before
        heads = list(re.finditer(r'(?:不能|无法|不应)(?:直接)?(?:改写为|改写成|写成|解释为|当作|当成|等同于)', context))
        if heads:
            head = heads[-1]
            tail = context[head.end():]
            if (not re.search(r'(?:不是|并非)\s*$', context[:head.start()])
                    and not re.search(r'但是|然而|不过|可是|但|却|反而|而是|实际|事实|因此|所以|确实|已经|现已|证明|认定|说明|意味着|可见|另一|另外|并且|而且|此外|同时|是|存在|显示|确认|肯定|可以|能够|保证', tail)
                    and not re.search(r'是事实|已成立|属实|已证实|确定|无疑|本来|存在|发生|已发生|确实', after)):
                return 'EXPLICIT_DENIED_REWRITE_LIST_NOT_PRICE_ASSERTION'
    if re.search(r'(?:若|如果|假如|倘若|假设)\s*$', before) or re.search(
            r'(?:若|如果|假如|倘若|假设).{0,28}(?:数据|行情|逐日记录)(?:证实|证明|显示|确认|支持)\s*$', before):
        return 'CONDITIONAL_OR_EXPLICIT_UNKNOWN'
    if not presupposed and re.search(r'(?:是否|有待核实|待核验|不能排除)\s*$', before):
        return 'CONDITIONAL_OR_EXPLICIT_UNKNOWN'
    if (not presupposed and NEGATIVE.search(before[-55:])
            and not re.search(r'(?:不是|并非|并不是)(?:不能|无法|不足以)', before[-55:])
            and not (kind == 'DAILY_PRICE_PATH' and DAILY.search(before[-55:]))):
        return 'EXPLICIT_NON_ENTAILMENT_OR_UNKNOWN'
    if re.match(r'\s*(?:尚未证实|未被证实|无法确认|仍待核验|并未得到证实|不是.{0,8}证据)', after):
        return 'EXPLICIT_NON_ENTAILMENT_OR_UNKNOWN'
    if kind == 'ABSOLUTE_CUMULATIVE_RETURN' and re.search(r'(?:并非|(?:也|并)?不是)(?:个股|本股|该股|股票|证券|账户)?\s*$', before):
        return 'EXPLICIT_NON_ENTAILMENT_OR_UNKNOWN'
    if (kind == 'DAILY_PRICE_PATH' and not presupposed
            and re.search(r'(?:并非|不是)\s*$', before) and re.match(r'(?:证明|证据)', after)):
        return 'EXPLICIT_NON_ENTAILMENT_OR_UNKNOWN'
    if kind == 'ACCOUNT_EXECUTABILITY' and re.search(r'(?:并不|不|不能|无法|未能|不可)\s*$', before):
        return 'EXPLICIT_NON_ENTAILMENT_OR_UNKNOWN'
    if (kind == 'ACCOUNT_EXECUTABILITY' and re.search(r'(?:并非|不是).{0,12}$', before)
            and re.match(r'(?:证明|证据|结论)', after)):
        return 'EXPLICIT_NON_ENTAILMENT_OR_UNKNOWN'
    if (kind == 'ACCOUNT_EXECUTABILITY' and re.match(r'证据(?:[、，,；;。\s]|$)', after)
            and re.search(r'(?:以下|下列)(?:所需|缺失|待补)?证据', paragraph_prefix)
            and re.search(r'需要|需补|缺失|未提供|当前包内均无|才可能改变|若|只有', paragraph_prefix)):
        return 'CONDITIONAL_MISSING_EVIDENCE_REQUIREMENT_NOT_EXECUTION_ASSERTION'
    if kind in ('GUARANTEED_FUTURE_PRICE', 'SNAPSHOT_UNIVERSAL_PATH') and re.search(
            r'(?:并非|不是|并不|不|未必)(?:必然|必定|一定|始终|一直|全程|保证)', clause[start:end]):
        return 'EXPLICIT_NON_ENTAILMENT_OR_UNKNOWN'
    return None


def _outside_daily_scope(clause, start):
    # Annual/non-price measures are not silently promoted into daily-price
    # claims. This is an explicit unverified scope observation, not source proof.
    before = clause[:start]
    return bool(re.search(r'[一二三四五六七八九十\d]+\s*年\s*$', before)
                or re.search(r'(?:营收|收入|净利润|利润|订单量).{0,8}$', before))


def _count(phrase):
    counts = re.findall(r'([一二三四五六七八九十]+|\d+)\s*(?:个)?(?:交易日|日|天)', phrase)
    if not counts:
        counts = re.findall(r'([一二三四五六七八九十]+|\d+)连涨', phrase)
    if not counts:
        return 2
    parsed = []
    for value in counts:
        if value.isdigit():
            parsed.append(int(value))
        elif value in CN:
            parsed.append(CN[value])
        elif re.fullmatch(r'[一二三四五六七八九]?十[一二三四五六七八九]?', value):
            left, right = value.split('十')
            parsed.append(CN.get(left, 1) * 10 + CN.get(right, 0))
        else:
            return None
    return parsed[0] if len(set(parsed)) == 1 and parsed[0] > 0 else None


def _attributed(clause, match, semantics):
    before = clause[:match.start()]
    for source in semantics['reported_daily_path_statements']:
        identity = source.get('record_id')
        target = re.escape(str(semantics['security'].get('name') or ''))
        direct = r'(?:原文)?(?:称|报道|写道|载明|引述|摘录|记载|[:：])\s*[“"「]?\s*(?:' + target + r')?\s*$'
        if (identity and re.search(r'(?<![A-Za-z0-9_-])' + re.escape(identity) + r'(?![A-Za-z0-9_-])', before[-100:])
                and match.group() == source['phrase']
                and re.search(direct, before[-80:])):
            return {'kind': 'ATTRIBUTED_SOURCE_ONLY', 'source_pointer': source['pointer'],
                    'record_id': identity, 'not_independently_verified_daily_path': True}
    return None


def _explicit_daily_binding_error(clause, start, semantics):
    # Only literal six-digit codes and an immediately preceding ISO date/range
    # are parsed. Natural-language names and dates remain source-review work.
    security = semantics['security']
    for literal in re.findall(r'(?<![A-Za-z0-9])(\d{6}(?:\.(?:SH|SS|SZ|BJ))?)(?![A-Za-z0-9])', clause, re.I):
        symbol, market = _identity(literal)
        if symbol != security.get('symbol') or market and market != security.get('market'):
            return 'EXPLICIT_OTHER_SECURITY_NOT_SUPPORTED_BY_TARGET_SOURCES'
    daily = semantics['daily_close_capability']
    if daily.get('supported'):
        dates = re.search(r'(\d{4}-\d{2}-\d{2})(?:\s*(?:至|到|—|–|~|to|through)\s*'
                          r'(\d{4}-\d{2}-\d{2}))?(?:期间|之间|内)?\s*$', clause[:start], re.I)
        if dates and any(day < daily['sessions'][0] or day > daily['sessions'][-1] for day in dates.groups() if day):
            return 'EXPLICIT_ISO_DATES_OUTSIDE_PROVIDED_DAILY_WINDOW'
    return None


def _aggregate_binding_error(clause, semantics, window_start, window_end):
    # A matching number does not bind a claim to the target or source window.
    # Only literal security codes and ISO dates are parsed, as in daily claims.
    security = semantics['security']
    for literal in re.findall(r'(?<![A-Za-z0-9])(\d{6}(?:\.(?:SH|SS|SZ|BJ))?)(?![A-Za-z0-9])', clause, re.I):
        symbol, market = _identity(literal)
        if symbol != security.get('symbol') or market and market != security.get('market'):
            return 'EXPLICIT_OTHER_SECURITY_NOT_SUPPORTED_BY_TARGET_SOURCES'
    dates = re.findall(r'\d{4}-\d{2}-\d{2}', clause)
    if dates and dates not in ([window_end], [window_start, window_end]):
        return 'EXPLICIT_ISO_DATES_DO_NOT_BIND_TO_AGGREGATE_SOURCE_WINDOW'
    return None


def evaluate_report(report, packet):
    """Do not alter text. FAIL blocks this bounded scope, not an entire domain."""
    if not isinstance(report, str):
        raise TypeError('report must be the original string')
    semantics = source_semantics(packet)
    violations, observations = [], []
    families = [('DAILY_PRICE_PATH', DAILY), ('ABSOLUTE_CUMULATIVE_RETURN', AGGREGATE),
                ('CONTINUOUS_TRADE_PATH', CONTINUOUS_TRADE), ('ACCOUNT_EXECUTABILITY', EXECUTION),
                ('GUARANTEED_FUTURE_PRICE', FUTURE), ('SNAPSHOT_UNIVERSAL_PATH', SNAPSHOT_UNIVERSAL)]
    daily = semantics['daily_close_capability']
    for offset, clause in _clauses(report):
        paragraph_start = max(report.rfind('\n', 0, offset), report.rfind('。', 0, offset), report.rfind(';', 0, offset), report.rfind('；', 0, offset)) + 1
        paragraph_prefix = report[paragraph_start:offset]
        for kind, pattern in families:
            for match in pattern.finditer(clause):
                record = {'kind': kind, 'start': offset + match.start(), 'end': offset + match.end(),
                          'claim_text_sha256_utf8': _sha(match.group()), 'report_sha256_utf8': _sha(report)}
                scope = _nonasserted(clause, match.start(), match.end(), kind, paragraph_prefix)
                if scope:
                    observations.append({**record, 'status': scope})
                    continue
                if kind == 'DAILY_PRICE_PATH':
                    if _outside_daily_scope(clause, match.start()):
                        observations.append({**record, 'status': 'OUTSIDE_DAILY_PRICE_FAMILY_NOT_VERIFIED'})
                        continue
                    binding_error = _explicit_daily_binding_error(clause, match.start(), semantics)
                    if binding_error:
                        violations.append({**record, 'reason': binding_error, 'source_capability_sha256': _sha(_canonical(semantics))})
                        continue
                    attributed = _attributed(clause, match, semantics)
                    if attributed:
                        observations.append({**record, 'status': 'ATTRIBUTED_NOT_DAILY_VERIFIED', **attributed})
                        continue
                    intraday = bool(re.search(r'盘中|分时|分钟|全程|一直|一路|不间断', clause))
                    needed = _count(match.group())
                    if daily.get('supported') and needed is not None and not intraday and daily['trailing_up_sessions'] >= needed:
                        observations.append({**record, 'status': 'SUPPORTED_IN_PROVIDED_DAILY_CLOSE_WINDOW',
                                             'source_sha256': daily['source_sha256'], 'sessions': daily['sessions']})
                        continue
                    reason = 'DAILY_HISTORY_DOES_NOT_SHOW_REQUIRED_CLOSE_PATH' if daily.get('supported') else 'DAILY_CLOSE_PATH_NOT_PROVEN_BY_AVAILABLE_SOURCES'
                    if intraday:
                        reason = 'DAILY_OR_CUMULATIVE_SNAPSHOT_NOT_INTRADAY_PATH'
                    if needed is None:
                        reason = 'EXPLICIT_DAY_COUNT_AMBIGUOUS_OR_UNSUPPORTED'
                elif kind == 'ABSOLUTE_CUMULATIVE_RETURN':
                    suffix = clause[match.end():]
                    number = re.match(r'\s*(?:为|达到|约为|约|达)?\s*([+-]?\d+(?:\.\d+)?)\s*%', suffix)
                    current = semantics['current_intraday_change_capability']
                    if (match.group() == '累计涨幅' and current.get('supported') and number
                            and Decimal(number.group(1)) == Decimal(current['value_percent'])
                            and re.search(r'该时点|快照|截至|盘中|当前|当时|该报价|此报价', clause)):
                        current_day = _time(semantics['as_of']).astimezone(BJ_TZ).date().isoformat()
                        binding_error = _aggregate_binding_error(clause, semantics, current_day, current_day)
                        if binding_error:
                            violations.append({**record, 'reason': binding_error, 'source_capability_sha256': _sha(_canonical(semantics))})
                            continue
                        observations.append({**record, 'status': 'ATTRIBUTED_CURRENT_INTRADAY_CHANGE_NOT_DAILY_PATH',
                                             'source_pointer': current['pointer'], 'source_field_sha256': current['field_sha256']})
                        continue
                    if daily.get('supported') and number and Decimal(number.group(1)) == Decimal(daily['cumulative_return_percent']):
                        binding_error = _aggregate_binding_error(clause, semantics, daily['sessions'][0], daily['sessions'][-1])
                        if binding_error:
                            violations.append({**record, 'reason': binding_error, 'source_capability_sha256': _sha(_canonical(semantics))})
                            continue
                        observations.append({**record, 'status': 'RECOMPUTED_PROVIDED_DAILY_WINDOW_RETURN', 'source_sha256': daily['source_sha256']})
                        continue
                    reason = ('CUMULATIVE_RETURN_VALUE_OR_WINDOW_NOT_RECOMPUTABLE' if daily.get('supported')
                              else 'RELATIVE_DEVIATION_OR_SINGLE_SNAPSHOT_NOT_ABSOLUTE_WINDOW_RETURN')
                else:
                    reason = 'AVAILABLE_SOURCE_CAPABILITY_DOES_NOT_PROVE_' + kind
                violations.append({**record, 'reason': reason, 'source_capability_sha256': _sha(_canonical(semantics))})
    return {'status': 'FAIL' if violations else 'PASS', 'violations': violations, 'observations': observations,
            'source_semantics_sha256': _sha(_canonical(semantics)), 'gate_version': VERSION,
            'checked_scope': {'families': [name for name, _ in families], 'scope': 'finite Chinese/English proposition families and local scopes only',
                              'not_checked': ['arbitrary natural-language entailment', 'source truth', 'causality', 'investment effect', 'unknown synonyms',
                                              'unparsed natural-language security names or dates; these still require source review'],
                              'PASS_is_not_semantic_accuracy': True, 'no_model_calls': True, 'does_not_rewrite_report': True}}
