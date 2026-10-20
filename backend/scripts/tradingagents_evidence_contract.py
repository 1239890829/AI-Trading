"""Typed source ledger and additive caller contract for the isolated TA LAB.

No network, model, file, or state mutation occurs here. The relation checker is
deliberately finite: explicit snapshot price-field comparisons, same-record
aware clock copies, named-news before/after-as-of claims, and bounded governance
source attributions. It does not verify arbitrary
entailment, peer fidelity, attention, causality, source truth, or investment value.
PASS only means no contradiction was found in that checked scope.
"""
from datetime import datetime, timezone
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
from itertools import combinations
import json
import re

from app.core.bjtime import BJ_TZ
from scripts.tradingagents_adapter_contract import has_bound_issuer_excerpt

VERSION = 'typed-evidence-ledger-v1'


def build_grounding_contract(packet, policy):
    """Build every case-specific annotation from this packet, never a prior prompt.

    A reusable policy must contain rules only. Reject already-rendered indices
    rather than append a contradictory current index to stale case facts.
    """
    from scripts.tradingagents_source_gate import source_semantics

    if not isinstance(policy, str) or not policy.strip():
        raise ValueError('A nonempty source-independent policy is required')
    if re.search(r'(?m)^(?:SOURCE_INDEX|SOURCE_CAPABILITIES_V2|EVIDENCE_RELATIONS_V1)\b', policy):
        raise ValueError('Rendered source annotations cannot be reused as policy')
    facts = packet.get('facts') or {}
    index = {
        'version': 'packet-source-index-v3', 'as_of': packet.get('as_of'),
        'security': facts.get('security'), 'facts_canonical_sha256': _sha(_canonical(facts)),
        'pointer_base': '/facts', 'not_semantic_gold': True,
        'news': [{'id': row.get('record_id'), 'pointer': '/candidate_news/' + str(i),
                  'source_value_sha256': _sha(_canonical(row))}
                 for i, row in enumerate(facts.get('candidate_news') or [])],
    }
    return (policy.rstrip() + '\nSOURCE_INDEX (字段定位辅助，不是语义gold):\n'
            + _canonical(index) + '\nSOURCE_CAPABILITIES_V2 (有限输入能力，不是语义gold):\n'
            + _canonical(source_semantics(packet)) + '\n' + build_contract(packet))


PRICE_FIELDS = ('price', 'open', 'high', 'low', 'prev_close')
ALIASES = {
    'price': r'快照价格|当前价格|最新价|现价|(?<![A-Za-z_])price(?![A-Za-z_])',
    'open': r'开盘价?|(?<![A-Za-z_])open(?![A-Za-z_])',
    'high': r'最高价?|高点|(?<![A-Za-z_])high(?![A-Za-z_])',
    'low': r'最低价?|低点|(?<![A-Za-z_])low(?![A-Za-z_])',
    'prev_close': r'前收盘?价?|昨收价?|(?<![A-Za-z_])prev_close(?![A-Za-z_])',
}
FIELD = '(?:' + '|'.join(ALIASES.values()) + ')'
NUMBER = r'(?:\s*(?:为|是|[:：=])?\s*-?\d+(?:\.\d+)?\s*(?:元|CNY)?)?'
DIRECT = re.compile('(?P<lhs>' + FIELD + ')' + NUMBER
                    + r'\s*(?P<op>不等于|等于|高于|低于|大于|小于|>=|<=|!=|==|>|<|=)\s*'
                    + '(?P<rhs>' + FIELD + ')', re.I)
PAIRED = re.compile('(?P<lhs>' + FIELD + ')' + NUMBER
                    + r'\s*(?:与|和|及|and)\s*(?P<rhs>' + FIELD + ')' + NUMBER
                    + r'\s*(?P<op>不相同|不相等|不同|相同|相等|一致|identical|equal)', re.I)
OPS = {'不等于': '!=', '不相同': '!=', '不相等': '!=', '不同': '!=', '!=': '!=',
       '等于': '=', '相同': '=', '相等': '=', '一致': '=', 'identical': '=', 'equal': '=',
       '=': '=', '==': '=', '大于': '>', '高于': '>', '>': '>', '小于': '<', '低于': '<',
       '<': '<', '>=': '>=', '<=': '<='}
GOVERNANCE = {'largest_shareholder': r'第一大股东', 'controlling_shareholder': r'控股股东',
              'actual_controller': r'实际控制人|实控人', 'control_right': r'控制权'}
GOVERNANCE_STATUS = {'not_effective': r'尚未生效|未生效', 'not_completed': r'尚未完成|未完成',
                     'acquirer_undetermined': r'受让方(?:尚)?未(?:定|确定)'}


def _governance_source(row, pointer):
    mentions = []
    for field in ('title', 'summary'):
        text = row.get(field)
        if not isinstance(text, str):
            continue
        for family, patterns in (('concept', GOVERNANCE), ('reported_status', GOVERNANCE_STATUS),
                                 ('plan_or_condition', {'plan': r'拟|计划', 'possible': r'可能'})):
            for kind, pattern in patterns.items():
                for match in re.finditer(pattern, text):
                    mentions.append({'family': family, 'kind': kind, 'pointer': pointer + '/' + field,
                                     'start': match.start(), 'end': match.end(), 'quote': match.group(),
                                     'field_sha256_utf8': _sha(text)})
    return {'mentions': mentions, 'current_legal_state': 'NOT_INFERRED_FROM_PLANS_OR_SILENCE',
            'concepts_are_not_interchangeable': True,
            'textual_mentions_are_not_independent_legal_verification': True}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _identity(value):
    match = re.fullmatch(r'(\d{6})(?:\.(SH|SS|SZ|BJ))?', str(value).upper())
    return (match.group(1), {'SS': 'SH'}.get(match.group(2), match.group(2))) if match else (None, None)


def _clock(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            raise ValueError('Naive source clock')
        return parsed, {'original': value, 'beijing': parsed.astimezone(BJ_TZ).isoformat(),
                        'utc_offset_seconds': int(parsed.utcoffset().total_seconds())}
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None, {'original': value, 'beijing': None, 'utc_offset_seconds': None}


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (ValueError, InvalidOperation):
        return None


def _field(pointer, value):
    return {'pointer': pointer, 'value_sha256': _sha(_canonical(value))}


def _subject(row, security):
    """A bounded leading-name/code check, not general document-subject NLP."""
    symbol, market = _identity(security.get('symbol'))
    market = market or security.get('market')
    metadata = [_identity(x) for x in row.get('source_symbols', [])]
    title = str(row.get('title') or '').lstrip('【 [')
    name = security.get('name')
    codes = [_identity(x) for x in re.findall(r'\d{6}(?:\.(?:SH|SS|SZ|BJ))?', title, re.I)]
    local = bool(symbol and (title.startswith(symbol) or name and title.startswith(name)))
    if codes and any(code != symbol or suffix and suffix != market for code, suffix in codes):
        multiple = any(code == symbol for code, _ in codes)
        status, eligible = 'OTHER_OR_MULTIPLE_EXPLICIT_SUBJECTS', None if multiple else False
    elif local and any(code == symbol and (not suffix or suffix == market) for code, suffix in metadata):
        status, eligible = 'LEADING_TARGET_AND_METADATA_AGREE', True
    elif metadata and all(code != symbol for code, _ in metadata):
        status, eligible = 'OTHER_DECLARED_SECURITY', False
    elif has_bound_issuer_excerpt(row, security) and any(
            code == symbol and (not suffix or suffix == market) for code, suffix in metadata):
        status, eligible = 'BOUND_SAME_DOCUMENT_ISSUER_EXCERPT', True
    else:
        status, eligible = 'UNKNOWN_REQUIRES_BODY_REVIEW', None
    return {'status': status, 'eligible': eligible,
            'method': 'leading-title or bound issuer excerpt plus metadata; not full body-subject or source-truth proof',
            'do_not_infer_subject_from_metadata_alone': True}


def build_ledger(packet):
    """Return JSON-safe provenance/eligibility and all compatible price pairs.

    Price levels are comparable with one another. Volume, amount, percentages,
    and changes are not silently compared with price levels or converted to new
    units. Text audit mentions retain their source span, not a global audit label.
    """
    facts = packet.get('facts') or {}
    security = facts.get('security') or {}
    as_of, as_of_info = _clock(packet.get('as_of', facts.get('as_of')))
    documents = []
    for index, row in enumerate(facts.get('candidate_news') or []):
        if not isinstance(row, dict):
            continue
        pointer = '/facts/candidate_news/' + str(index)
        published, pub_info = _clock(row.get('published_at'))
        available, avail_info = _clock(row.get('available_at'))
        before = {'published': published <= as_of if published and as_of else None,
                  'available': available <= as_of if available and as_of else None}
        eligible = all(before.values()) if all(v is not None for v in before.values()) else None
        order = published <= available if published and available else None
        if order is False and eligible:
            eligible = None
        mentions = []
        for key in ('title', 'summary'):
            text = row.get(key)
            if not isinstance(text, str):
                continue
            for match in re.finditer(r'未经审计|未审计|经审计|已审计|\bunaudited\b|\baudited\b', text, re.I):
                mentions.append({'pointer': pointer + '/' + key, 'start': match.start(), 'end': match.end(),
                                 'quote': match.group(), 'field_sha256_utf8': _sha(text),
                                 'scope': 'this textual mention only; retain its company/report/period scope'})
        audit = {'status': 'DECLARED' if row.get('audit_status') is not None else 'UNKNOWN',
                 'declaration': row.get('audit_status'), 'mentions': mentions,
                 'do_not_transfer_to_other_document_or_period': True}
        issuer = {'status': 'DECLARED' if row.get('issuer') is not None else 'UNKNOWN',
                  'declaration': row.get('issuer'), 'source_label': row.get('source'),
                  'source_label_is_not_primary_issuer_proof': True}
        if row.get('audit_status') is not None:
            audit['provenance'] = _field(pointer + '/audit_status', row['audit_status'])
        if row.get('issuer') is not None:
            issuer['provenance'] = _field(pointer + '/issuer', row['issuer'])
        documents.append({'record_id': row.get('record_id'), 'pointer': pointer,
                          'source_sha256': _sha(_canonical(row)), 'subject': _subject(row, security),
                          'time': {'published_at': pub_info, 'available_at': avail_info,
                                   'at_or_before_as_of': before, 'published_before_available': order,
                                   'eligible': eligible}, 'audit': audit, 'issuer': issuer,
                          'governance': _governance_source(row, pointer)})
    snapshot = facts.get('market_snapshot') or {}
    data = snapshot.get('data') or {}
    symbol, suffix = _identity(data.get('symbol'))
    target, target_suffix = _identity(security.get('symbol'))
    market = security.get('market') or target_suffix
    received, received_info = _clock(data.get('received_at'))
    usable = bool(target and symbol == target and data.get('market') == market
                  and (not suffix or suffix == market) and snapshot.get('status') == 'observed'
                  and snapshot.get('received_before_as_of') is True and received and as_of and received <= as_of)
    fields = {}
    for key in PRICE_FIELDS:
        value = _decimal(data.get(key))
        if value is not None:
            fields[key] = {**_field('/facts/market_snapshot/data/' + key, data[key]),
                           'decimal': str(value), 'unit': 'source_price_unit', 'dimension': 'price_level'}
    pairs = []
    for left, right in combinations(fields, 2):
        lhs, rhs = Decimal(fields[left]['decimal']), Decimal(fields[right]['decimal'])
        pairs.append({'lhs': left, 'rhs': right, 'operator': '=' if lhs == rhs else '>' if lhs > rhs else '<',
                      'source_pointers': [fields[left]['pointer'], fields[right]['pointer']],
                      'same_record_sha256': _sha(_canonical(snapshot))})
    return {'version': VERSION, 'facts_sha256': _sha(_canonical(facts)), 'security': dict(security),
            'as_of': as_of_info, 'documents': documents,
            'snapshot': {'usable': usable, 'pointer': '/facts/market_snapshot',
                         'source_sha256': _sha(_canonical(snapshot)), 'received_at': received_info,
                         'fields': fields, 'comparisons': pairs,
                         'comparison_scope': 'all unordered pairs of supplied price levels; not price paths, units conversion, or account fills'},
            'attention': {'status': 'NOT_ESTABLISHED_BY_THIS_LEDGER',
                          'unknown_is_not_absent': True,
                          'research_interest_is_not_measured_market_attention': True,
                          'limit': 'News framing or a positive price field does not measure market-attention level/change. Explicit source statements still require attribution and source review.'},
            'not_semantic_gold': True}


def build_contract(packet):
    """Return an additive layer; caller retains/relabels all prior constraints.

    Deliberately independent of packet.grounding_contract/common_scope to avoid
    a self-referential contract hash. All comparison arms must receive this layer.
    """
    ledger = build_ledger(packet)
    compact = {'ledger_sha256': _sha(_canonical(ledger)), 'as_of': ledger['as_of'],
               'documents': [{'record_id': d['record_id'], 'pointer': d['pointer'],
                              'subject': d['subject'], 'time': d['time'],
                              'audit_status': d['audit']['status'], 'audit_mentions': d['audit']['mentions'],
                              'issuer_status': d['issuer']['status'],
                              'governance_concepts': sorted({m['kind'] for m in d['governance']['mentions']
                                                            if m['family'] == 'concept'}),
                              'current_legal_state': d['governance']['current_legal_state']}
                             for d in ledger['documents']],
               'snapshot_usable': ledger['snapshot']['usable'],
               'snapshot_price_values': {k: v['decimal'] for k, v in ledger['snapshot']['fields'].items()},
               'same_snapshot_relations': [{k: row[k] for k in ('lhs', 'operator', 'rhs')}
                                           for row in ledger['snapshot']['comparisons']],
               'attention': ledger['attention']}
    return ('EVIDENCE_RELATIONS_V1（追加层；原角色、辩论、工具、七报告和记忆保留，情绪schema允许明确弃权）\n'
            '1. 角色方向不要求创造相反事实。引用或反驳对方时按本次同伴定位协议绑定其实际原句'
            '（逐字引用或核验短ID），保留若/待证/未做到等条件；'
            '可批评证据强度或观察价值，不得把条件说成对方已预设、已证明。没有支持的反论可直说。\n'
            '2. 研究者认为值得关注是研究建议；市场关注已存在、可观察或上升是另一个经验判断。'
            '没有合格观测就写未知，不等于市场没有关注。事件可能引发关注可作明确条件推断，'
            '不得在后段/表格压缩成已观测事实。新闻语气的主观分数不补足该证据。\n'
            '3. 数值相等/大小/变化先按同对象、同快照源字段复算；规则中的条件例子不是本例记录。'
            '同文列出正确数值却写矛盾关系也是错误；随后正确否定路径不能消除前提错误。'
            '本层关系只是原数值的确定性比较，不是新行情、历史路径或成交证明。\n'
            '4. 每条消息分别核正文主体、published_at与available_at和时区。审计限定保留原公司/报告期范围；'
            '媒体来源标签不等于交易所/主发行人。未声明的审计或发行主体未知，不能跨文借用。\n'
            '5. 每角色只写本职新增信息：短结论、少量关键有源支持、真实反证/条件、未知与改判条件。'
            '不整段重抄packet和前报告，不为填表制造信号，也不靠全弃权删除已给合法事实。'
            '输出前核事实/自身推断/条件/未知是否混写；需更正前角色时明确原错与正确源字段。'
            '这不是额外思维链或模型调用要求。\n'
            '6. 以下ledger是有限输入定位/比较，不是独立source truth、人工gold或语义正确率；'
            '未解析的说法仍需来源审查。\n'
            'NEWS_SCORE_BASIS_V2：仅当SentimentReport给出非空数值或自愿给新闻语气数值刻度时，'
            '在narrative/content中恰好加入一个<NEWS_SCORE_BASIS>纯JSON</NEWS_SCORE_BASIS>块。'
            'JSON格式为{"sources":[{"record_id":"实际合格新闻ID","field":"title或summary",'
            '"quote":"该字段内4至240字逐字短引文"}],"justification":"仅据所引新闻文字解释主观语气"}。'
            'sources须非空，逐项主体和双时刻合格。overall_score/overall_band只能依据这些新闻文字；'
            '不得把盘中正涨幅、快照、换手或selector判断作为该刻度的方向/数值依据。'
            '独立行情背景仍可讨论，但不能在块外另写评分因行情而偏多。'
            '合格新闻原文本身报道上涨、价格或偏离可按新闻语气分析，不禁这些词；'
            '无合格材料时overall_score与overall_band同时为null，并解释缺失；'
            '明确弃权不需虚构来源块，不编中性值，不能在narrative另写数值刻度。'
            'Trader/PM投资评级可综合行情，不是此新闻语气刻度，不能混为一类。'
            '逐字引用只证明来源绑定，不证明解释正确；仍需全文来源审核。\n'
            'GOVERNANCE_SOURCE_SCOPE_V1：第一大股东、控股股东、实际控制人/实控人、控制权是不同概念，'
            '不得互换或把“第一大股东可能变更”写成来源报告“控制权可能变化”。'
            '可明确提出需另证的作者条件假说，但不能伪作新闻原句或已知法律状态。'
            '拟议、计划、可能只保留来源当时的限定；它们不证明as_of时已完成，也不证明尚未生效、'
            '尚未完成或受让方未定。源未给进展/受让方时，写材料未证完成、当前状态/受让方未知。'
            '源确有明确状态时保留其来源日期和归属，不升级为独立核实的当前法律事实。'
            'ticktime是来源报价时钟，received_at是本机接收时刻，不能互换；'
            '封板标记属于selection记录，不归给不含该字段的market_snapshot行情。\n'
            + PEER_POLICY + '\n' + _canonical(compact))


def _field_name(value):
    return next(key for key, pattern in ALIASES.items() if re.fullmatch(pattern, value, re.I))


def _nonasserted(clause, start, end):
    prefix, suffix = clause[:start], clause[end:]
    # A preceding unrelated denial cannot pardon an assertion after a contrast.
    prefix = re.split(r'但是|然而|但|不过|事实上', prefix)[-1]
    if re.search(r'不能不(?:承认|确认)|并非不能|不是不', prefix):
        return None
    if re.search(r'(?:若|如果|假如|假设|\bif\b)\s*[^。；]*$', prefix, re.I):
        return 'CONDITIONAL_NOT_OBSERVED'
    # A comma starts a separate assertion unless a preceding conditional scope
    # was established above. An unrelated earlier denial is not its negation.
    prefix = re.split(r'[,，]', prefix)[-1]
    if re.search(r'(?:不能|无法|不足以)(?:据此|直接)?(?:证明|认定|断言|确认|推断)|并非|不等于|并不|不是', prefix):
        return 'NEGATED_OR_UNPROVEN'
    if re.match(r'\s*(?:尚未证实|未获证实|仍待核验|并非事实|不成立)', suffix):
        return 'NEGATED_OR_UNPROVEN'
    if (re.search(r'原报告|旧稿|错误(?:地)?称|误称', prefix)
            and re.search(r'错误|有误|应更正|应纠正|不成立|并非事实|源数不同|数值不同', suffix[:90])):
        return 'QUOTED_ERROR_EXPLICITLY_CORRECTED'
    return None


def _governance_attributions(clause, offset, text, documents, ledger):
    """Only absent-concept/status direct source attributions, not general NLP.

    An exact named source or the target's eligible news pool is checked. Unknown
    aliases, multi-source attribution and author hypotheses need human review.
    """
    violations, observations, checked = [], [], 0
    target, market = _identity(ledger['security'].get('symbol'))[0], ledger['security'].get('market')
    codes = re.findall(r'(?<!\d)\d{6}(?:\.(?:SH|SS|SZ|BJ))?(?!\d)', clause, re.I)
    if any(_identity(c)[0] != target or _identity(c)[1] and _identity(c)[1] != market for c in codes):
        return violations, observations, checked
    ids = [key for key in documents if re.search(r'(?<![A-Za-z0-9_-])' + re.escape(key)
                                                + r'(?![A-Za-z0-9_-])', clause)]
    if len(ids) > 1:
        return violations, observations, checked
    pool = [documents[ids[0]]] if ids else list(documents.values())
    pool = [d for d in pool if d['subject']['eligible'] is True and d['time']['eligible'] is True]
    marker = r'新闻|消息|摘要|原文|公告'
    if ids:
        marker += '|' + re.escape(ids[0])
    attribution = '(?:' + marker + r').{0,45}(?:报告|报道|称|披露|显示|写明|表明|明确(?:为|称)?|含有?|限定为)'
    patterns = [(kind, '(?:' + pattern + r').{0,12}(?:变化|变更|转移|改变|易主)', 'concept')
                for kind, pattern in GOVERNANCE.items()]
    patterns += [(kind, pattern, 'reported_status') for kind, pattern in GOVERNANCE_STATUS.items()]
    for kind, pattern, family in patterns:
        for match in re.finditer(pattern, clause):
            prefix = clause[:match.start()]
            local_prefix = re.split(r'[,，]', prefix)[-1]
            if not re.search(attribution, local_prefix):
                continue
            start, end = offset + match.start(), offset + match.end()
            row = {'kind': 'SOURCE_GOVERNANCE_ATTRIBUTION', 'concept_or_status': kind,
                   'start': start, 'end': end, 'claim_sha256_utf8': _sha(text[start:end]),
                   'source_record_ids': [d['record_id'] for d in pool]}
            scope = _nonasserted(clause, match.start(), match.end())
            if not scope and (re.search(r'(?:不能|不得|不可|不应)(?:把|将|写|说|称|归|视|推)|'
                                        r'不支持|未(?:曾)?报告|没有(?:报告|披露|称)', local_prefix)
                              or re.search(r'是否.{0,8}$', local_prefix)):
                scope = 'NEGATED_OR_UNPROVEN'
            if scope:
                observations.append({**row, 'status': scope})
                continue
            if not pool:
                observations.append({**row, 'status': 'ELIGIBLE_TARGET_SOURCE_SCOPE_UNKNOWN'})
                continue
            checked += 1
            if any(any(m['family'] == family and m['kind'] == kind for m in d['governance']['mentions'])
                   for d in pool):
                observations.append({**row, 'status': 'SOURCE_CONCEPT_OR_STATUS_PRESENT_NOT_GENERAL_ENTAILMENT'})
            else:
                violations.append({**row, 'reason': 'ATTRIBUTED_GOVERNANCE_CONCEPT_OR_STATUS_NOT_IN_SOURCE',
                                   'source_pointers': [d['pointer'] for d in pool]})
    return violations, observations, checked


def _source_clock_claims(clause, offset, packet):
    """Check explicit same-record clock copies, not arbitrary time-language claims."""
    facts = packet.get('facts') or {}
    records = {}
    for index, row in enumerate(facts.get('candidate_news') or []):
        if isinstance(row, dict) and isinstance(row.get('record_id'), str):
            records.setdefault(row['record_id'], []).append((row, '/facts/candidate_news/' + str(index)))
    snapshot = facts.get('market_snapshot') or {}
    if isinstance(snapshot.get('record_id'), str):
        records.setdefault(snapshot['record_id'], []).append((snapshot.get('data') or {}, '/facts/market_snapshot/data'))
    mentions = [(match, key) for key in records for match in re.finditer(
        r'(?<![A-Za-z0-9_-])' + re.escape(key) + r'(?![A-Za-z0-9_-])', clause)]
    if len({key for _, key in mentions}) != 1:
        return [], [], 0
    key = mentions[0][1]
    if len(records[key]) != 1:
        return [], [], 0
    source, pointer = records[key][0]
    names = {'published_at': 'published_at', 'available_at': 'available_at', 'received_at': 'received_at',
             '发布时间': 'published_at', '可用时间': 'available_at', '接收时间': 'received_at'}
    fields = re.compile(r'(?<![A-Za-z0-9_])(?:published_at|available_at|received_at)(?![A-Za-z0-9_])'
                        r'|发布时间|可用时间|接收时间')
    clock = re.compile(
        r'[\s`"“”]*' r'(?:为|是|不是|并非|[:：=])?' r'[\s`"“”]*'
        r'(?:(?P<day>\d{4}-\d{2}-\d{2})[T ]+)?(?P<hms>\d{2}:\d{2}:\d{2})'
        r'(?P<fraction>\.\d{1,6})?\s*(?P<zone>Z|UTC|[+-]\d{2}:\d{2})(?![A-Za-z0-9:+-])')
    violations, observations, checked = [], [], 0
    for field in fields.finditer(clause):
        prior = [match for match, _ in mentions if match.end() <= field.start()]
        match = clock.match(clause, field.end())
        if not prior or match is None:
            continue
        name = names[field.group()]
        expected, _ = _clock(source.get(name))
        day = match['day']
        inherited = day is None
        if inherited:
            dates = set(re.findall(r'\d{4}-\d{2}-\d{2}', clause[prior[-1].end():field.start()]))
            if len(dates) != 1:
                continue
            day = dates.pop()
        zone = '+00:00' if match['zone'] in ('Z', 'UTC') else match['zone']
        claimed, _ = _clock(day + 'T' + match['hms'] + (match['fraction'] or '') + zone)
        if inherited and (expected is None or claimed is None
                          or expected.astimezone(claimed.tzinfo).date().isoformat() != day):
            continue  # A missing/ambiguous date is not manufactured from the source.
        start, end = offset + field.start(), offset + match.end()
        record = {'kind': 'EXPLICIT_SOURCE_CLOCK_VALUE', 'record_id': key, 'field': name,
                  'start': start, 'end': end, 'claim_sha256_utf8': _sha(clause[field.start():match.end()]),
                  'source_pointer': pointer + '/' + name, 'date_inherited_from_same_record_clause': inherited}
        value_start = match.start('hms') if inherited else match.start('day')
        assertions = list(re.finditer(r'但是|然而|但|不过|事实上|而实际|实际|现已|确已', clause[:value_start]))
        local = assertions[-1].end() if assertions else 0
        scope = _nonasserted(clause[local:], value_start - local, match.end() - local)
        if scope:
            observations.append({**record, 'status': scope})
            continue
        checked += 1
        if expected is None:
            violations.append({**record, 'reason': 'SOURCE_CLOCK_FIELD_UNPROVEN'})
            continue
        digits = len(match['fraction'] or '') - 1 if match['fraction'] else 0
        unit = 10 ** (6 - digits)
        def displayed(value):
            if value is None:
                return None
            value = value.astimezone(timezone.utc)
            return value.replace(microsecond=value.microsecond // unit * unit)
        if claimed is not None and displayed(claimed) == displayed(expected):
            observations.append({**record, 'status': 'SOURCE_CLOCK_MATCHES_DECLARED_PRECISION',
                                 'fractional_digits': digits,
                                 'source_clock_sha256': _sha(_canonical(source[name])),
                                 'full_source_clock_and_asof_eligibility_unchanged': True})
        else:
            violations.append({**record, 'reason': 'SOURCE_CLOCK_VALUE_CONTRADICTED'})
    return violations, observations, checked


def evaluate_relations(text, packet):
    """Check finite price relations, named-news times and direct attributions.

    Unrecognized or explicitly other-object/date comparisons are observations,
    never licenses to borrow the target snapshot. No output is rewritten.
    """
    ledger = build_ledger(packet)
    result = {'status': 'NOT_APPLICABLE', 'violations': [], 'observations': [],
              'ledger_sha256': _sha(_canonical(ledger)),
              'checked_scope': {'families': ['EXPLICIT_SNAPSHOT_PRICE_RELATION', 'NAMED_NEWS_ASOF_ORDER',
                                            'SOURCE_GOVERNANCE_ATTRIBUTION', 'EXPLICIT_SOURCE_CLOCK_VALUE'],
                                'not_checked': ['arbitrary entailment', 'peer fidelity', 'attention measurement',
                                                'source truth', 'causality', 'investment effect',
                                                'unbound, naive or ambiguous report clocks and natural-language dates'],
                                'does_not_rewrite_report': True, 'PASS_is_not_semantic_accuracy': True}}
    if not isinstance(text, str):
        return result
    security = ledger['security']
    target = _identity(security.get('symbol'))[0]
    docs = {d['record_id']: d for d in ledger['documents'] if isinstance(d['record_id'], str)}
    checked = 0
    for clause_match in re.finditer(r'[^。！？\n；;]+', text):
        clause, offset = clause_match.group(), clause_match.start()
        failures, notes, count = _source_clock_claims(clause, offset, packet)
        result['violations'].extend(failures)
        result['observations'].extend(notes)
        checked += count
        failures, notes, count = _governance_attributions(clause, offset, text, docs, ledger)
        result['violations'].extend(failures)
        result['observations'].extend(notes)
        checked += count
        matches = sorted([m for pattern in (DIRECT, PAIRED) for m in pattern.finditer(clause)], key=lambda m: m.start())
        for match in matches:
            start, end = offset + match.start(), offset + match.end()
            row = {'kind': 'EXPLICIT_SNAPSHOT_PRICE_RELATION', 'start': start, 'end': end,
                   'claim_sha256_utf8': _sha(text[start:end])}
            scope = _nonasserted(clause, match.start(), match.end())
            codes = re.findall(r'(?<!\d)\d{6}(?:\.(?:SH|SS|SZ|BJ))?(?!\d)', clause, re.I)
            dates = re.findall(r'\d{4}-\d{2}-\d{2}', clause)
            target_market = security.get('market')
            if (any(_identity(x)[0] != target or _identity(x)[1] and _identity(x)[1] != target_market for x in codes)
                    or any(day != str(ledger['as_of']['beijing'] or '')[:10] for day in dates)):
                scope = 'OTHER_OR_AMBIGUOUS_OBJECT_OR_DATE_NOT_LICENSED'
            if scope:
                result['observations'].append({**row, 'status': scope})
                continue
            checked += 1
            left, right = _field_name(match['lhs']), _field_name(match['rhs'])
            fields = ledger['snapshot']['fields']
            if not ledger['snapshot']['usable'] or left not in fields or right not in fields:
                result['violations'].append({**row, 'reason': 'COMPARABLE_ELIGIBLE_SNAPSHOT_INPUTS_UNPROVEN'})
                continue
            lhs, rhs = Decimal(fields[left]['decimal']), Decimal(fields[right]['decimal'])
            operator = OPS[match['op'].lower()]
            supported = {'=': lhs == rhs, '!=': lhs != rhs, '>': lhs > rhs, '<': lhs < rhs,
                         '>=': lhs >= rhs, '<=': lhs <= rhs}[operator]
            detail = {**row, 'source_pointers': [fields[left]['pointer'], fields[right]['pointer']],
                      'expected_relation': '=' if lhs == rhs else '>' if lhs > rhs else '<'}
            if supported:
                result['observations'].append({**detail, 'status': 'SUPPORTED_SAME_SNAPSHOT_RELATION'})
            else:
                result['violations'].append({**detail, 'reason': 'SOURCE_PRICE_VALUES_CONTRADICT_RELATION'})
        ids = [key for key in docs if re.search(r'(?<![A-Za-z0-9_-])' + re.escape(key) + r'(?![A-Za-z0-9_-])', clause)]
        if len(ids) != 1:
            continue
        for match in re.finditer(r'不晚于|早于|晚于', clause):
            tail = clause[match.end():match.end() + 90]
            if not re.search(r'as_of|观察时点|分析时点|本例时点|本次时点', tail, re.I):
                continue
            start, end = offset + match.start(), offset + match.end()
            row = {'kind': 'NAMED_NEWS_ASOF_ORDER', 'record_id': ids[0], 'start': start, 'end': end,
                   'claim_sha256_utf8': _sha(text[start:end])}
            scope = _nonasserted(clause, match.start(), match.end())
            if scope:
                result['observations'].append({**row, 'status': scope})
                continue
            checked += 1
            document = docs[ids[0]]
            prefix = clause[:match.start()]
            clocks = ['published', 'available']
            if '可用时间' not in prefix and 'available_at' not in prefix and ('发布时间' in prefix or 'published_at' in prefix):
                clocks = ['published']
            elif '发布时间' not in prefix and 'published_at' not in prefix and ('可用时间' in prefix or 'available_at' in prefix):
                clocks = ['available']
            values = [_clock(document['time'][key + '_at']['original'])[0] for key in clocks]
            cutoff = _clock(ledger['as_of']['original'])[0]
            def holds(value):
                if value is None or cutoff is None:
                    return False
                return value > cutoff if match.group() == '晚于' else value < cutoff if match.group() == '早于' else value <= cutoff
            if all(holds(value) for value in values):
                result['observations'].append({**row, 'status': 'SUPPORTED_SOURCE_CLOCK_ORDER'})
            else:
                result['violations'].append({**row, 'reason': 'SOURCE_CLOCK_ORDER_CONTRADICTED_OR_UNKNOWN',
                                             'source_pointer': document['pointer'], 'source_clock_keys': clocks})
    result['status'] = 'FAIL' if result['violations'] else 'PASS' if checked else 'NOT_APPLICABLE'
    return result


def _score_nonuse(text, start, end):
    prefix = re.split(r'但是|然而|但|不过|[,，;；。\n]', text[:start])[-1]
    suffix = re.split(r'[;；。\n]', text[end:])[0][:80]
    return bool(re.search(r'(?:不|未|不能)(?:使用|采用|依据|基于|纳入)|不作为|不参与', prefix)
                or re.search(r'不(?:参与|作为|纳入).{0,12}(?:评分|评级|刻度)|仅(?:作|为)独立背景', suffix))


def evaluate_score_basis(value, packet, role=None):
    """Check a declared NEWS-only score, once on the whole original report.

    A numeric SentimentReport uses one bounded JSON evidence block in its
    narrative. The evidence-aware schema can instead explicitly abstain with
    paired null score/band; it must not declare a numeric score in the narrative.
    Trade/PM ratings do not require this block.
    Other arms require it only if they voluntarily declare a numeric news-tone
    score. Exact source quotes do not establish the interpretation's entailment.
    """
    result = {'status': 'NOT_APPLICABLE', 'violations': [], 'observations': [],
              'ledger_sha256': _sha(_canonical(build_ledger(packet))),
              'checked_scope': {'family': 'DECLARED_NEWS_ONLY_SCORE_BASIS',
                                'not_checked': ['arbitrary entailment', 'unwritten provider reasoning',
                                                'source truth', 'sentiment calibration'],
                                'does_not_rewrite_report': True, 'PASS_is_not_semantic_accuracy': True}}
    if not isinstance(value, dict):
        return result
    typed = 'overall_score' in value and isinstance(value.get('narrative'), str)
    text = value.get('narrative') if typed else value.get('content', '')
    if not isinstance(text, str):
        return result
    voluntary = bool(re.search(r'(?:新闻(?:文本)?(?:语气|情绪)|news[- ]only.{0,10}(?:tone|sentiment))'
                               r'.{0,45}\d+(?:\.\d+)?\s*/\s*10', text, re.I))
    # Other scales are permitted. Detect explicit numeric declarations only in
    # a paragraph that declares a news-tone measure; ordinary price discussion
    # or a portfolio rating is not a numeric news score.
    for paragraph in re.split(r'\n\s*\n', text):
        news_tone = (re.search(r'新闻(?:文本)?|news[- ]only', paragraph, re.I)
                     and re.search(r'语气|情绪|tone|sentiment', paragraph, re.I))
        numeric_score = re.search(
            r'(?:overall_score\s*[:=：]|(?:新闻(?:文本)?)?(?:语气|情绪)(?:主观)?'
            r'(?:评分|刻度|分数)\s*[:=：为是]?)\s*[-+−]?\d+(?:\.\d+)?', paragraph, re.I)
        voluntary = voluntary or bool(news_tone and numeric_score)
    if not typed and not voluntary:
        return result
    pointer = '/narrative' if typed else '/content'
    def violation(reason, start=0, end=0):
        result['violations'].append({'kind': 'NEWS_SCORE_BASIS', 'reason': reason,
                                     'json_pointer': pointer, 'start': start, 'end': end,
                                     'claim_sha256_utf8': _sha(text[start:end])})
    if typed and (value.get('overall_score') is None or value.get('overall_band') is None):
        if value.get('overall_score') is not None or value.get('overall_band') is not None:
            violation('SCORE_AND_BAND_MUST_ABSTAIN_TOGETHER')
        elif voluntary:
            violation('NULL_SCORE_REPORT_DECLARES_NUMERIC_NEWS_SCORE', 0, min(len(text), 80))
        elif not text.strip():
            violation('ABSTENTION_REQUIRES_EXPLANATION')
        else:
            result['observations'].append({'status': 'EXPLICIT_NUMERIC_SCORE_ABSTENTION',
                                            'does_not_establish_source_absence': True})
        result['status'] = 'FAIL' if result['violations'] else 'PASS'
        return result
    opening, closing = '<NEWS_SCORE_BASIS>', '</NEWS_SCORE_BASIS>'
    blocks = list(re.finditer(re.escape(opening) + r'(.*?)' + re.escape(closing), text, re.S))
    if len(blocks) != 1 or text.count(opening) != 1 or text.count(closing) != 1:
        violation('ONE_NEWS_SCORE_BASIS_BLOCK_REQUIRED', 0, min(len(text), 80))
    def unique(pairs):
        obj = {}
        for key, item in pairs:
            if key in obj:
                raise ValueError('Duplicate basis key')
            obj[key] = item
        return obj
    def invalid_constant(_):
        raise ValueError('Nonfinite basis value')
    ledger = build_ledger(packet)
    facts = packet.get('facts') or {}
    documents = {d['record_id']: d for d in ledger['documents'] if isinstance(d['record_id'], str)}
    records = [row for row in facts.get('candidate_news', []) if isinstance(row, dict)]
    counts = Counter(row.get('record_id') for row in records if isinstance(row.get('record_id'), str))
    news = {row['record_id']: row for row in records if isinstance(row.get('record_id'), str)}
    explicit_refs = [r'market_snapshot', r'/selection\b', r'selection\.evidence',
                     r'行情快照|盘中快照|行情字段|盘中(?:正)?涨幅字段|换手率字段|成交量字段']
    for block in (facts.get('market_snapshot'), facts.get('selection')):
        if isinstance(block, dict) and isinstance(block.get('record_id'), str):
            explicit_refs.append(re.escape(block['record_id']))
    references = re.compile('|'.join(explicit_refs), re.I)
    if len(blocks) == 1:
        block = blocks[0]
        try:
            if len(block[1]) > 8192:
                raise ValueError('Oversize basis')
            basis = json.loads(block[1], object_pairs_hook=unique, parse_constant=invalid_constant)
            if (not isinstance(basis, dict) or set(basis) != {'sources', 'justification'}
                    or not isinstance(basis['sources'], list) or not 1 <= len(basis['sources']) <= 8
                    or not isinstance(basis['justification'], str) or not 4 <= len(basis['justification']) <= 800):
                raise ValueError('Invalid basis shape')
            seen = set()
            for source in basis['sources']:
                if (not isinstance(source, dict) or set(source) != {'record_id', 'field', 'quote'}
                        or not isinstance(source['record_id'], str) or source['field'] not in ('title', 'summary')
                        or not isinstance(source['quote'], str) or not 4 <= len(source['quote']) <= 240):
                    raise ValueError('Invalid source binding shape')
                identity = (source['record_id'], source['field'], source['quote'])
                if identity in seen:
                    raise ValueError('Duplicate source binding')
                seen.add(identity)
                doc = documents.get(source['record_id'])
                row = news.get(source['record_id'])
                if counts[source['record_id']] != 1:
                    violation('SCORE_SOURCE_RECORD_ID_NOT_UNIQUE', block.start(), block.end())
                elif not doc or doc['subject']['eligible'] is not True or doc['time']['eligible'] is not True:
                    violation('SCORE_SOURCE_SUBJECT_OR_DUAL_TIME_UNPROVEN', block.start(), block.end())
                elif not isinstance(row.get(source['field']), str) or source['quote'] not in row[source['field']]:
                    violation('SCORE_SOURCE_QUOTE_NOT_EXACT', block.start(), block.end())
                else:
                    result['observations'].append({'record_id': source['record_id'], 'field': source['field'],
                                                   'quote_sha256_utf8': _sha(source['quote']),
                                                   'source_field_sha256_utf8': _sha(row[source['field']]),
                                                   'status': 'EXACT_ELIGIBLE_SOURCE_BINDING_NOT_ENTAILMENT'})
            for match in references.finditer(basis['justification']):
                if not _score_nonuse(basis['justification'], match.start(), match.end()):
                    violation('MARKET_OR_SELECTION_REFERENCE_IN_SCORE_BASIS', block.start(), block.end())
        except (ValueError, TypeError, KeyError):
            violation('INVALID_NEWS_SCORE_BASIS_JSON_OR_SHAPE', block.start(), block.end())
    # Preserve positions while excluding the block: source quotes may themselves
    # legitimately contain price/rise/deviation words. Outside the block, only
    # explicit score-because-market clauses are checked, not all price discussion.
    masked = text
    for block in reversed(blocks):
        masked = masked[:block.start()] + ' ' * (block.end() - block.start()) + masked[block.end():]
    for sentence in re.finditer(r'[^。！？\n;；]+', masked):
        part = sentence.group()
        if not re.search(r'(?:评级|评分|分数|刻度|overall_score|overall_band).{0,90}'
                         r'(?:因为|由于|基于|依据|主因|源自|because|based|due to)', part, re.I):
            continue
        for match in references.finditer(part):
            if not _score_nonuse(part, match.start(), match.end()):
                violation('MARKET_FIELD_USED_IN_NEWS_SCORE_EXPLANATION',
                          sentence.start() + match.start(), sentence.start() + match.end())
    result['status'] = 'FAIL' if result['violations'] else 'PASS'
    return result


def evaluate_terminal_output(value):
    """Check explanatory terminal text after the original codec/schema validates.

    This does not validate tool permission, a schema, sources or semantics. A
    nonempty tool proposal is not a terminal report and still needs the caller's
    original offered-tool validation. Optional numeric trading fields may remain
    null; they are not a substitute for the role's required explanatory text.
    """
    result = {'status': 'PASS', 'violations': [], 'observations': [],
              'checked_scope': {'version': 'terminal-output-usability-v2',
                                'scope': 'nonblank terminal text with letters, not punctuation/numeric fragments',
                                'not_checked': ['schema', 'tool permission', 'source truth',
                                                'semantic correctness', 'investment value'],
                                'requires_prior_codec_and_schema_validation': True,
                                'does_not_rewrite_output': True}}
    if not isinstance(value, dict):
        result['status'] = 'FAIL'
        result['violations'].append({'reason': 'TERMINAL_OUTPUT_NOT_OBJECT'})
        return result
    tools = value.get('tool_calls')
    if tools:
        if (not isinstance(tools, list) or any(not isinstance(call, dict)
                or not isinstance(call.get('id'), str) or not call['id'].strip()
                or not isinstance(call.get('name'), str) or not call['name'].strip()
                or not isinstance(call.get('arguments'), dict) for call in tools)):
            result['status'] = 'FAIL'
            result['violations'].append({'reason': 'UNRECOGNIZED_TOOL_PROPOSAL_SHAPE'})
        else:
            result['status'] = 'NOT_APPLICABLE'
            result['observations'].append({'status': 'NONTERMINAL_TOOL_PROPOSAL',
                                           'offered_tool_validation_still_required': True})
        return result
    fields = {name for name in ('content', 'narrative', 'rationale', 'reasoning', 'investment_thesis')
              if name in value}
    markers = [('narrative', ('overall_score', 'overall_band')),
               ('rationale', ('recommendation', 'strategic_actions')),
               ('reasoning', ('action', 'entry_price', 'stop_loss', 'position_sizing')),
               ('investment_thesis', ('rating', 'executive_summary', 'price_target', 'time_horizon'))]
    fields.update(field for field, names in markers if any(name in value for name in names))
    if not fields:
        result['violations'].append({'reason': 'NO_RECOGNIZED_TERMINAL_TEXT'})
    for field in sorted(fields):
        text = value.get(field)
        if not isinstance(text, str) or not text.strip():
            result['violations'].append({'reason': 'MISSING_OR_BLANK_TERMINAL_TEXT',
                                         'json_pointer': '/' + field,
                                         'text_sha256_utf8': _sha(text) if isinstance(text, str) else None})
        elif not any(char.isalpha() for char in text):
            result['violations'].append({'reason': 'NONEXPLANATORY_TERMINAL_FRAGMENT',
                                         'json_pointer': '/' + field,
                                         'text_sha256_utf8': _sha(text)})
    result['status'] = 'FAIL' if result['violations'] else 'PASS'
    return result


PEER_REQUIRED = {
    'Bull Researcher': [], 'Bear Researcher': ['Bull Researcher'],
    'Research Manager': ['Bull Researcher', 'Bear Researcher'], 'Trader': ['Research Manager'],
    'Aggressive Analyst': ['Trader'], 'Conservative Analyst': ['Aggressive Analyst'],
    'Neutral Analyst': ['Aggressive Analyst', 'Conservative Analyst'],
    'Portfolio Manager': ['Trader', 'Aggressive Analyst', 'Conservative Analyst', 'Neutral Analyst'],
}
PEER_ALIASES = {
    'Bull Researcher': r'Bull(?: Researcher)?|(?:多头|多方)(?=观点|论点|认为|称|把|将|所说|所述|主张|结论|建议|理由|论证|的|预设|若|会|可能|说|指出)',
    'Bear Researcher': r'Bear(?: Researcher)?|(?:空头|空方)(?=观点|论点|认为|称|把|将|所说|所述|主张|结论|建议|理由|论证|的|预设|若|会|可能|说|指出)',
    'Research Manager': r'Research Manager|研究经理', 'Trader': r'Trader|交易员',
    'Aggressive Analyst': r'Aggressive Analyst|激进(?:分析师|角色|方|观点|论点|者)|进取风险角色',
    'Conservative Analyst': r'Conservative Analyst|保守(?:分析师|角色|方|观点|论点|者)|审慎风险角色',
    'Neutral Analyst': r'Neutral Analyst|中性(?:分析师|角色|方|观点|论点)',
    'Portfolio Manager': r'Portfolio Manager|投资组合经理|组合经理',
    'Market Analyst': r'Market Analyst|市场分析师', 'News Analyst': r'News Analyst|新闻分析师',
    'Fundamentals Analyst': r'Fundamentals Analyst|基本面分析师',
    'Sentiment Analyst': r'Sentiment Analyst|情绪分析师',
}
PEER_POLICY = (
    'TERMINAL_TEXT_V1：所有角色及独立单模型的终态解释文字须非空，不能仅空白；'
    '原工具提案可有空content但不是终态报告，仍须原工具许可及schema校验。\n'
    'PEER_EVIDENCE_V1：真实图后八角色引用实际同伴时，仅在原文字字段内的一块'
    '<PEER_EVIDENCE>{"citations":[{"peer_id":"调用方实际ID","field":"/content",'
    '"quote":"该可见字段内完整原句"}]}</PEER_EVIDENCE>列逐字原话；不新增原schema字段。'
    'peer_id和可见字段由调用方提供，不自己编身份或计算哈希。引文保留完整句的若/未/不足等限定；'
    '可以连引完整句，不取条件后件或伪造引号内结论。块中不得另写同伴结论的自由转述。'
    '块外只写自己的事实分析、反证、未知和条件，不再用其他角色名/别称或“对方主张”等来归属观点。'
    '自己的分析不冒充同伴已说过的话；没有分歧可明确一致，不制造冲突。'
    '无已发言对手的首发多头给空citations并直接论证，不补造空方主张。'
    '调用方要求的实际前置角色必须引用；缺实际资料应报输入缺失，不编引文。'
    '四分析师及独立单模型不需要此块，也不会收到真实TA同伴输出；可明确写自己的不同假说，'
    '不得声称实际其他Agent已经发言。逐字引用只证明同伴说过，不证明市场事实或自己批评公平；'
    '原始市场/新闻事实仍回溯packet，整体语义仍须全文审查。')


def _peer_leaves(value, pointer=''):
    if isinstance(value, str):
        yield pointer or '/', value
    elif isinstance(value, dict):
        for key, child in value.items():
            token = str(key).replace('~', '~0').replace('/', '~1')
            yield from _peer_leaves(child, pointer + '/' + token)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _peer_leaves(child, pointer + '/' + str(index))


def _peer_context(context):
    if not isinstance(context, dict):
        raise ValueError('INVALID_PEER_CONTEXT')
    role, arm = context.get('role'), context.get('arm')
    if role not in PEER_REQUIRED:
        if context.get('accepted_peers') or context.get('required_peer_roles'):
            raise ValueError('NONAPPLICABLE_ROLE_MUST_NOT_RECEIVE_PEERS')
        return False, {}, {}
    if (not isinstance(context.get('case_id'), str) or not context['case_id']
            or not isinstance(arm, str) or not arm.startswith('ta-')):
        raise ValueError('INVALID_PEER_CALLER_IDENTITY')
    required = context.get('required_peer_roles')
    if (not isinstance(required, list) or len(required) != len(set(required))
            or set(required) != set(PEER_REQUIRED[role])):
        raise ValueError('NATIVE_REQUIRED_PEER_ROLES_MISMATCH')
    sources = context.get('accepted_peers')
    if not isinstance(sources, list) or len(sources) > 24:
        raise ValueError('INVALID_ACCEPTED_PEERS')
    peers, index = {}, []
    for row in sources:
        if not isinstance(row, dict):
            raise ValueError('INVALID_PEER_RECORD')
        peer_id, output = row.get('peer_id'), row.get('output')
        if (not isinstance(peer_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', peer_id)
                or peer_id in peers or row.get('role') not in PEER_ALIASES or row['role'] == role
                or not isinstance(row.get('call_id'), int) or isinstance(row['call_id'], bool)
                or row['call_id'] < 1 or not isinstance(output, dict) or output.get('tool_calls')
                or row.get('output_sha256') != _sha(_canonical(output))):
            raise ValueError('PEER_IDENTITY_HASH_OR_TERMINAL_MISMATCH')
        if (row.get('case_id', context['case_id']) != context['case_id']
                or row.get('arm', arm) != arm or row.get('accepted', True) is not True
                or row.get('state', 'accepted') not in ('accepted', 'succeeded')):
            raise ValueError('PEER_NOT_ACCEPTED_SAME_CASE_AND_ARM')
        leaves = dict(_peer_leaves(output))
        visible = row.get('visible_fields')
        if (not isinstance(visible, list) or not visible or len(visible) > 16
                or len(visible) != len(set(visible))
                or any(not isinstance(key, str) or key not in leaves or len(leaves[key].strip()) < 8
                       for key in visible)):
            raise ValueError('PEER_VISIBLE_FIELDS_UNPROVEN')
        peers[peer_id] = {**row, 'visible_text': {key: leaves[key] for key in visible}}
        index.append({key: row[key] for key in ('peer_id', 'role', 'call_id', 'output_sha256')})
        index[-1]['allowed_string_fields'] = list(visible)
    if not set(required) <= {row['role'] for row in sources}:
        raise ValueError('REQUIRED_ACCEPTED_PEER_MISSING')
    return True, peers, {'case_id': context['case_id'], 'arm': arm, 'role': role,
                         'required_peer_roles': list(required), 'peers': index}


def build_peer_contract(context):
    """Return identity-only visible-peer metadata, or raise before inference.

    Broker establishes accepted/samecase/arm provenance and exact visibility in
    the original native messages. This pure module verifies its supplied binding;
    it does not independently read graph state or expose another arm's reports.
    """
    applicable, _, index = _peer_context(context)
    return '<PEER_CONTEXT>' + _canonical(index) + '</PEER_CONTEXT>' if applicable else ''


def _peer_sentence_span(text, quote):
    if not isinstance(quote, str) or not 8 <= len(quote) <= 2048 or quote != quote.strip():
        return None
    # Chinese sentence punctuation and line breaks are explicit boundaries.
    # Semicolons retain following qualifications. English periods are
    # conservative (ticker/decimal safe).
    separators = r'[。！？!?\n]'
    decoration = re.compile(r'\s*(?:(?:#{1,6}|>|[-*+])\s*|\d+[.)、]\s+|[*_`]+\s*)*')
    for match in re.finditer(re.escape(quote), text):
        start, end = match.span()
        if any(start < nested.end() and end > nested.start() for nested in
               re.finditer(r'<PEER_EVIDENCE>.*?</PEER_EVIDENCE>', text, re.S)):
            continue
        prior = list(re.finditer(separators, text[:start]))
        left = text[prior[-1].end() if prior else 0:start]
        if not decoration.fullmatch(left):
            continue
        if quote[-1] in '。！？!?':
            return start, end
        next_boundary = re.search(separators, text[end:])
        right = text[end:end + next_boundary.start()] if next_boundary else text[end:]
        if not decoration.fullmatch(right):
            continue
        # Omitting a terminal punctuation mark is a trimmed sentence.
        if next_boundary and next_boundary.group() != '\n':
            continue
        return start, end
    return None


def evaluate_peer_evidence(value, context):
    """Bind real visible text; require source review of actual peer use.

    Identity, visibility, malformed evidence and invented quoted text fail hard.
    Format findings do not prove unfair reasoning. Every required-peer consumer
    needs a separate exact source review before consumption, including reports
    with perfectly formatted quotations. No review is synthesized here.
    """
    result = {'status': 'NOT_APPLICABLE', 'violations': [], 'observations': [],
              'format_findings': [], 'review_required': False,
              'checked_scope': {'version': 'peer-evidence-v1', 'does_not_rewrite_output': True,
                                'no_required_peers_may_omit_empty_block': True,
                                'required_peer_consumers_need_exact_source_review': True,
                                'format_findings_are_not_semantic_verdicts': True,
                                'not_checked': ['arbitrary entailment', 'fair criticism',
                                                'unrecognized aliases or implicit pronouns', 'source truth'],
                                'PASS_is_not_semantic_accuracy': True}}
    try:
        applicable, peers, index = _peer_context(context)
    except (ValueError, TypeError):
        result.update(status='FAIL', caller_context_error=True)
        result['violations'].append({'reason': 'INVALID_PEER_CONTEXT', 'author_override_allowed': False})
        return result
    if not applicable:
        return result
    result['peer_context_sha256'] = _sha(_canonical(index))
    leaves = list(_peer_leaves(value))
    blocks = [(pointer, text, match) for pointer, text in leaves for match in
              re.finditer(r'<PEER_EVIDENCE>(.*?)</PEER_EVIDENCE>', text, re.S)]
    def fail(reason, **details):
        result['violations'].append({'reason': reason, 'author_override_allowed': False, **details})
    def format_issue(reason, **details):
        result['format_findings'].append({'reason': reason, **details})
        result['review_required'] = True
    if not isinstance(value, dict) or value.get('tool_calls'):
        fail('PEER_REPORT_MUST_BE_TERMINAL_OBJECT')
    cited = set()
    result['review_required'] = bool(context['required_peer_roles'])
    opening = sum(text.count('<PEER_EVIDENCE>') for _, text in leaves)
    closing = sum(text.count('</PEER_EVIDENCE>') for _, text in leaves)
    if not blocks and not opening and not closing:
        # Missing format never rewrites the report or skips outside attribution.
        if context['required_peer_roles']:
            format_issue('MISSING_PEER_EVIDENCE_BLOCK')
        else:
            result['observations'].append({'status': 'IMPLICIT_EMPTY_NO_REQUIRED_PEERS',
                                           'original_output_unchanged': True})
    elif len(blocks) != 1 or opening != 1 or closing != 1:
        fail('REQUIRE_EXACTLY_ONE_PEER_EVIDENCE_BLOCK')
    else:
        pointer, _, block = blocks[0]
        def unique(pairs):
            obj = {}
            for key, child in pairs:
                if key in obj:
                    raise ValueError('Duplicate peer evidence key')
                obj[key] = child
            return obj
        try:
            if len(block.group(1)) > 32768:
                raise ValueError('Peer evidence too long')
            obj = json.loads(block.group(1), object_pairs_hook=unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite')))
            if (not isinstance(obj, dict) or set(obj) != {'citations'}
                    or not isinstance(obj['citations'], list) or len(obj['citations']) > 16):
                raise ValueError('Invalid peer citation shape')
            seen = set()
            for citation in obj['citations']:
                if not isinstance(citation, dict) or set(citation) != {'peer_id', 'field', 'quote'}:
                    raise ValueError('Invalid peer citation keys')
                identity = tuple(citation[key] for key in ('peer_id', 'field', 'quote'))
                if not all(isinstance(x, str) for x in identity) or identity in seen:
                    raise ValueError('Duplicate or invalid citation')
                seen.add(identity)
                peer = peers.get(citation['peer_id'])
                source = peer['visible_text'].get(citation['field']) if peer else None
                quote = citation['quote']
                if (not isinstance(source, str) or not 8 <= len(quote) <= 2048
                        or quote != quote.strip() or quote not in source):
                    fail('PEER_ID_FIELD_OR_EXACT_SUBSTRING_NOT_BOUND', json_pointer=pointer,
                         peer_id=citation['peer_id'], field=citation['field'])
                    continue
                span = _peer_sentence_span(source, quote)
                if span is None:
                    start = source.index(quote)
                    span = start, start + len(quote)
                    format_issue('PEER_QUOTE_NOT_FULL_SENTENCE', json_pointer=pointer,
                                 peer_id=citation['peer_id'], field=citation['field'])
                cited.add(peer['role'])
                result['review_required'] = True
                result['observations'].append({'peer_id': peer['peer_id'], 'role': peer['role'],
                    'call_id': peer['call_id'], 'output_sha256': peer['output_sha256'],
                    'field': citation['field'], 'source_start': span[0], 'source_end': span[1],
                    'source_field_sha256_utf8': _sha(source), 'quote_sha256_utf8': _sha(citation['quote']),
                    'status': 'EXACT_VISIBLE_PEER_SUBSTRING_NOT_ENTAILMENT'})
        except (ValueError, TypeError, KeyError):
            fail('INVALID_PEER_EVIDENCE_JSON_OR_SHAPE', json_pointer=pointer)
    missing = set(context['required_peer_roles']) - cited
    if missing:
        format_issue('REQUIRED_PEER_ROLES_NOT_CITED', missing_roles=sorted(missing))
    aliases = [pattern for role, pattern in PEER_ALIASES.items() if role != context['role']]
    aliases.append(r'对方(?:认为|主张|称|的(?:结论|观点|论据)|把|将|所说|所述|预设)|其他角色(?:认为|主张|称)')
    outside = re.compile(r'(?<![A-Za-z])(?:' + '|'.join(aliases) + r')(?![A-Za-z])', re.I)
    for pointer, text in leaves:
        masked = re.sub(r'<PEER_EVIDENCE>.*?</PEER_EVIDENCE>', lambda m: ' ' * len(m.group()), text, flags=re.S)
        for match in outside.finditer(masked):
            format_issue('OUTSIDE_BLOCK_EXPLICIT_PEER_ATTRIBUTION', json_pointer=pointer,
                         start=match.start(), end=match.end(), text_sha256_utf8=_sha(text),
                         claim_sha256_utf8=_sha(text[match.start():match.end()]))
    result['status'] = ('FAIL' if result['violations'] else
                        'REQUIRES_PEER_REVIEW' if result['review_required'] else 'PASS')
    return result
