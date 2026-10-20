"""Pure contracts for the isolated TradingAgents adapter.

No app, provider, filesystem or network access. These checks bind evidence and
scope; they do not decide whether an interpretation or peer criticism is fair.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def sha(body):
    return hashlib.sha256(body).hexdigest()


def digest(value):
    return sha(canonical(value).encode())


def has_bound_issuer_excerpt(row, security):
    """Check an explicit same-document issuer excerpt, not source truth.

    The source collector must verify the quoted page against the original file.
    A portal label, source_symbols, or a company's mention in a government
    bulletin alone cannot provide this binding. The binding establishes the
    document issuer only, never every subsidiary/event claim in its body.
    """
    proof = row.get('document_issuer')
    if not isinstance(proof, dict):
        return False
    symbol = security.get('symbol')
    quote, legal_name = proof.get('quote'), proof.get('legal_name')
    page = proof.get('physical_page')
    return bool(isinstance(symbol, str) and re.fullmatch(r'\d{6}', symbol)
                and proof.get('symbol') == symbol
                and isinstance(quote, str) and isinstance(legal_name, str) and legal_name
                and legal_name in quote and re.search(r'(?<!\d)' + symbol + r'(?!\d)', quote)
                and isinstance(page, int) and not isinstance(page, bool) and page > 0
                and isinstance(proof.get('document_id'), str) and proof['document_id']
                and proof['document_id'] == row.get('document_id')
                and isinstance(proof.get('source_sha256'), str)
                and re.fullmatch(r'[0-9a-f]{64}', proof['source_sha256'])
                and proof['source_sha256'] == row.get('source_sha256')
                and proof.get('quote_sha256') == sha(quote.encode()))


def verify_review_content(review,binding,verdict):
    """Validate the review body, after its path and byte hash were verified."""
    if (not isinstance(review,dict) or review.get('binding')!=binding
        or review.get('verdict')!=verdict
        or review.get('reviewed_complete_original_own_output') is not True
        or review.get('reviewed_actual_visible_peer_bodies_and_frozen_facts') is not True
        or not isinstance(review.get('reason'),str) or not review['reason'].strip()):
        raise ValueError('Review body binding, verdict or complete-source-read mismatch')
    return True


def _aware(value):
    if not isinstance(value,str):raise ValueError('Explicit source timestamp required')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:raise ValueError('Timezone required')
    return result


def _date(value):
    if not isinstance(value,str) or len(value)!=10:raise ValueError('Expected YYYY-MM-DD')
    result=datetime.fromisoformat(value).date()
    if result.isoformat()!=value:raise ValueError('Expected YYYY-MM-DD')
    return result


_TOOL_ARGUMENTS={
    'get_stock_data':('symbol','start_date','end_date'),
    'get_news':('ticker','start_date','end_date'),
    'get_indicators':('symbol','indicator','curr_date','look_back_days'),
    'get_fundamentals':('ticker','curr_date'),
    'get_balance_sheet':('ticker','freq','curr_date'),
    'get_cashflow':('ticker','freq','curr_date'),
    'get_income_statement':('ticker','freq','curr_date'),
    'get_insider_transactions':('ticker','curr_date'),
    'get_verified_market_snapshot':('symbol','curr_date','look_back_days'),
    'get_global_news':('curr_date','look_back_days','limit'),
    'get_macro_indicators':('indicator','curr_date','look_back_days'),
    'get_prediction_markets':('query','limit','curr_date'),
}


def validate_tool_scope(packet,method,args=(),kwargs=None):
    """Fail closed on foreign instruments, future/range/date substitution.

    Single-date frozen values only represent packet.trade_date. A historical
    range query may use dated rows/articles from the supplied frozen subset;
    successful filtering is never a claim of complete historical coverage.
    Date comparisons use the packet's explicit aware as_of offset, not the host.
    """
    try:
        names=_TOOL_ARGUMENTS[method];params=dict(kwargs or {})
        if len(args)>len(names) or set(params)-set(names):raise ValueError('Unknown tool argument')
        for name,value in zip(names,args):
            if name in params:raise ValueError('Duplicate tool argument')
            params[name]=value
        as_of=_aware(packet['as_of']);trade_date=_date(packet['trade_date'])
        if as_of.date()!=trade_date:raise ValueError('Packet as_of/trade_date disagreement')
        symbol=params.get('symbol',params.get('ticker'))
        if names[0] in ('symbol','ticker') and symbol!=packet['ticker']:
            raise ValueError('Requested instrument differs from frozen packet')
        scope={'method':method,'ticker':packet['ticker'],'packet_as_of':packet['as_of'],
               'packet_trade_date':packet['trade_date'],'requested_arguments':params,
               'coverage':'partial_frozen_packet_only; unknown is not observed absence'}
        if method in ('get_stock_data','get_news'):
            start,end=_date(params.get('start_date')),_date(params.get('end_date'))
            if start>end or end>trade_date:raise ValueError('Invalid or future requested range')
            scope.update(start_date=start.isoformat(),end_date=end.isoformat())
        else:
            requested=params.get('curr_date') or packet['trade_date']
            if _date(requested)!=trade_date:raise ValueError('No version for requested historical/future date')
            scope.update(start_date='0001-01-01',end_date=trade_date.isoformat())
        for key in ('look_back_days','limit'):
            v=params.get(key)
            if v is not None and (not isinstance(v,int) or isinstance(v,bool) or v<=0):
                raise ValueError('Positive bounded request count required')
        return {'status':'PASS','scope':scope}
    except (KeyError,TypeError,ValueError) as error:
        return {'status':'FAIL','code':'ScopeMismatch','reason':str(error)}


def _articles_in_scope(articles,packet,scope):
    if not isinstance(articles,list):raise ValueError('Dated article list required')
    cutoff=_aware(packet['as_of']);start,end=_date(scope['start_date']),_date(scope['end_date'])
    kept=[];excluded=[]
    for article in articles:
        reason=None
        try:
            if not isinstance(article,dict) or packet['ticker'] not in [str(s).split('.')[0] for s in article.get('source_symbols',[])]:
                raise ValueError('subject_unbound_or_other_instrument')
            published,available=_aware(article.get('published_at')),_aware(article.get('available_at'))
            day=published.astimezone(cutoff.tzinfo).date()
            if not start<=day<=end:raise ValueError('outside_requested_publication_range')
            if published>cutoff or available>cutoff or available.astimezone(cutoff.tzinfo).date()>end:
                raise ValueError('not_available_by_requested_cutoff')
        except (ValueError,TypeError):reason='unknown_scope_or_outside_requested_scope'
        if reason:excluded.append({'record_id':article.get('record_id') if isinstance(article,dict) else None,'reason':reason})
        else:kept.append(deepcopy(article))
    return {'status':'partial_frozen_subset','articles':kept,'excluded_records':excluded,
            'empty_subset_does_not_prove_no_news':True}


def project_tool_value(packet,method,value,scope):
    """Return a copy limited to requested scope; never silently substitute data."""
    if method=='get_news':return _articles_in_scope(value,packet,scope)
    if method=='get_fundamentals' and isinstance(value,dict) and 'articles' in value:
        result=deepcopy(value);subset=_articles_in_scope(value['articles'],packet,scope)
        result.update(articles=subset['articles'],excluded_records=subset['excluded_records'])
        return result
    if method=='get_stock_data':
        if not isinstance(value,list):raise ValueError('Unsupported OHLCV representation; cannot certify requested range')
        kept=[]
        for row in value:
            if not isinstance(row,dict):raise ValueError('Dated OHLCV row required')
            day=_date(row.get('Date',row.get('date',row.get('trade_date'))))
            if scope['start_date']<=day.isoformat()<=scope['end_date']:kept.append(deepcopy(row))
        return {'status':'partial_frozen_subset','rows':kept,'complete_history':False}
    return deepcopy(value)


def _leaves(value,pointer=''):
    if isinstance(value,str):yield pointer or '/',value
    elif isinstance(value,dict):
        for key,child in value.items():yield from _leaves(child,pointer+'/'+str(key).replace('~','~0').replace('/','~1'))
    elif isinstance(value,list):
        for index,child in enumerate(value):yield from _leaves(child,pointer+'/'+str(index))


class PeerContextError(ValueError):
    """Invalid actual predecessor identity/visibility; never an author override."""


PEER_ID = re.compile(r'(?<![A-Za-z0-9_./?#=&%\-])p\d+-f\d+-s\d+(?![A-Za-z0-9_./?#=&%\-])')


def _pointer(value, pointer):
    if not isinstance(pointer,str) or not pointer.startswith('/'):
        raise PeerContextError('Invalid visible field pointer')
    for token in pointer[1:].split('/'):
        token=token.replace('~1','/').replace('~0','~')
        try:value=value[int(token)] if isinstance(value,list) else value[token]
        except (ValueError,IndexError,KeyError,TypeError) as error:
            raise PeerContextError('Visible field does not exist') from error
    if not isinstance(value,str) or not value.strip():
        raise PeerContextError('Visible predecessor field must contain text')
    return value


def _segments(text):
    """Exact contiguous sentence/line spans. Keep semicolon conditions together.

    This is an identity index, not a claim that a sentence entails an argument.
    Existing citation markup is not indexed as new independent peer evidence.
    """
    import re
    excluded=[(m.start(),m.end()) for m in re.finditer(
        r'<(?:PEER_EVIDENCE|PEER_REFS)>.*?</(?:PEER_EVIDENCE|PEER_REFS)>',text,re.S)]
    for match in re.finditer(r'[^。！？\n]+[。！？]?',text):
        start,end=match.span()
        while start<end and text[start].isspace():start+=1
        while end>start and text[end-1].isspace():end-=1
        quote=text[start:end]
        if len(quote)<8 or any(start<b and end>a for a,b in excluded):continue
        if PEER_ID.search(quote) and not any(c.isalnum() for c in PEER_ID.sub('',quote)):
            continue
        if re.fullmatch(r'[\s#*`_\-:：A-Za-z]+',quote):continue
        yield start,end,quote


def build_predecessor_index(context):
    """Build JSON-safe short IDs only for required, native-visible predecessors.

    context is broker-owned: case_id/arm/role/required_peer_roles/accepted_peers.
    Outputs remain in native history. Entries resolve exact original spans and
    hashes; models select IDs and never supply text, roles or computed hashes.
    """
    required=context.get('required_peer_roles')
    if not isinstance(required,list) or any(not isinstance(r,str) or not r for r in required) or len(required)!=len(set(required)):
        raise PeerContextError('Invalid required predecessor roles')
    peers=context.get('accepted_peers')
    if not isinstance(peers,list):raise PeerContextError('Invalid accepted predecessor list')
    by_role={};seen_ids=set()
    for peer in peers:
        if (peer.get('case_id')!=context.get('case_id') or peer.get('arm')!=context.get('arm')
            or peer.get('accepted') is not True or peer.get('state')!='accepted'
            or not isinstance(peer.get('call_id'),int) or isinstance(peer.get('call_id'),bool)
            or peer.get('output_sha256')!=digest(peer.get('output'))
            or not isinstance(peer.get('peer_id'),str) or peer['peer_id'] in seen_ids
            or not isinstance(peer.get('output'),dict) or peer['output'].get('tool_calls')):
            raise PeerContextError('Unaccepted, foreign, duplicate or altered predecessor')
        seen_ids.add(peer['peer_id'])
        if peer.get('role') in by_role:raise PeerContextError('Duplicate completed predecessor role')
        by_role[peer.get('role')]=peer
    entries=[]
    for role in required:
        peer=by_role.get(role)
        if peer is None:raise PeerContextError('Required actual predecessor unavailable: '+role)
        fields=peer.get('visible_fields')
        if not isinstance(fields,list) or len(fields)!=len(set(fields)) or not fields:
            raise PeerContextError('Required predecessor has no native-visible fields')
        count=0
        for field_index,field in enumerate(sorted(fields)):
            text=_pointer(peer['output'],field)
            for segment_index,(start,end,quote) in enumerate(_segments(text)):
                entries.append({'segment_id':f"{peer['peer_id']}-f{field_index}-s{segment_index}",
                    'peer_id':peer['peer_id'],'role':role,'call_id':peer['call_id'],'field':field,
                    'output_sha256':peer['output_sha256'],'field_sha256':sha(text.encode()),
                    'start':start,'end':end,'quote':quote,'quote_sha256':sha(quote.encode())})
                count+=1
        if not count:raise PeerContextError('Required predecessor has no indexable visible body: '+role)
    return {'protocol':'REQUIRED_PREDECESSOR_SEGMENTS_V2','case_id':context.get('case_id'),
            'arm':context.get('arm'),'role':context.get('role'),'required_peer_roles':required,
            'peer_context_sha256':digest(context),'entries':entries,
            'does_not_establish_fair_paraphrase_or_market_truth':True}


def build_predecessor_contract(context):
    index=build_predecessor_index(context)
    if not index['required_peer_roles']:
        return ('REQUIRED_PREDECESSOR_SEGMENTS_V2: 本角色没有已发言的必需同伴。'
                '不要求引用块；直接依据原始事实论证，不编造尚未发言角色的观点。')
    # The exact sentence is shown once in the index so the model selects an ID,
    # not a long manually recopied quote. Native reports are never replaced.
    visible={'protocol':index['protocol'],'case_id':index['case_id'],'role':index['role'],
             'required_peer_roles':index['required_peer_roles'],
             'segments':[{k:e[k] for k in ('segment_id','role','field','quote')} for e in index['entries']]}
    return ('REQUIRED_PREDECESSOR_SEGMENTS_V2: 保留原角色全部职责。引用每个实际必需同伴时，'
            '在原文字字段内直接写实际短ID，或用一个或多个 '
            '<PEER_REFS>{"citations":[{"segment_id":"实际短ID"}]}</PEER_REFS>。'
            '仅选下列真实可见句段ID；不用抄长句、角色或哈希，不编ID。引用只是定位同伴原话，'
            '不保证自己的批评成立；自己的解释须忠实保留原句条件，无分歧可明确一致。'
            '未发言角色不需反驳。完整原报告与原生依赖继续保留。\n<PREDECESSOR_INDEX>'
            +canonical(visible)+'</PREDECESSOR_INDEX>')


def _unique_json(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate reference key')
        result[key]=value
    return result


def evaluate_predecessor_refs(value,context):
    """Resolve IDs; require semantic review for actual required peer use.

    FAIL is a hard binding/protocol failure. REQUIRES_PEER_REVIEW is never PASS.
    This finite API does not decide whether a paraphrase or criticism is fair.
    """
    import re
    try:index=build_predecessor_index(context)
    except (PeerContextError,ValueError,TypeError) as error:
        return {'status':'FAIL','review_required':False,'violations':[{'code':'INVALID_PEER_CONTEXT','detail':str(error)}]}
    result={'status':'PASS','review_required':False,'violations':[],
            'peer_context_sha256':index['peer_context_sha256'],'predecessor_index_sha256':digest(index),
            'resolved_citations':[],'checked_scope':'Actual required predecessor ID/span identity only; semantic fairness needs source review.'}
    texts=[text for _,text in _leaves(value)];joined='\n'.join(texts)
    opens=sum(t.count('<PEER_REFS>') for t in texts);closes=sum(t.count('</PEER_REFS>') for t in texts)
    blocks=[m for text in texts for m in re.finditer(r'<PEER_REFS>(.*?)</PEER_REFS>',text,re.S)]
    required=set(index['required_peer_roles'])
    if opens==closes==0 and not required:
        # No artificial empty-block obligation on the first speaker. An actual
        # attribution still needs author review rather than a regex truth claim.
        if re.search(r'对方(?:认为|主张|称|预设|的观点)|(?:多头|空头|交易员|积极方|保守方|中立方)(?:认为|主张|称|预设)',joined):
            result.update(status='REQUIRES_PEER_REVIEW',review_required=True)
        return result
    if opens != closes or opens != len(blocks) or opens > 16:
        result['violations'].append({'code':'PREDECESSOR_REFERENCE_BLOCK_MISSING_OR_MALFORMED'})
    else:
        try:
            known={e['segment_id']:e for e in index['entries']};seen=set();covered=set()
            selected=[]
            for block in blocks:
                data=json.loads(block.group(1),object_pairs_hook=_unique_json)
                if not isinstance(data,dict) or set(data)!={'citations'} or not isinstance(data['citations'],list):raise ValueError('Invalid citation envelope')
                if not 1<=len(data['citations'])<=128:raise ValueError('Bounded nonempty citations required')
                for citation in data['citations']:
                    if not isinstance(citation,dict) or set(citation)!={'segment_id'}:raise ValueError('Only segment_id may be model supplied')
                    selected.append(citation['segment_id'])
            # Parse every block before masking. Invalid JSON cannot fall back
            # to IDs elsewhere. URLs are not plain-text peer references.
            for text in texts:
                masked=re.sub(r'<PEER_REFS>.*?</PEER_REFS>',lambda m:' '*len(m.group()),text,flags=re.S)
                masked=re.sub(r'https?://\S+|www\.\S+',lambda m:' '*len(m.group()),masked)
                selected.extend(m.group() for m in PEER_ID.finditer(masked))
            if len(selected)>2048:raise ValueError('Too many references')
            for sid in selected:
                if not isinstance(sid,str) or sid not in known:raise ValueError('Unknown segment ID')
                if sid in seen:continue
                seen.add(sid);entry=known[sid];covered.add(entry['role'])
                result['resolved_citations'].append(entry)
            if not required.issubset(covered):raise ValueError('Required predecessor role not cited')
        except (ValueError,TypeError,KeyError) as error:
            result['violations'].append({'code':'PREDECESSOR_REFERENCE_NOT_BOUND','detail':str(error)})
    if result['violations']:result.update(status='FAIL',review_required=False)
    elif required or result['resolved_citations']:result.update(status='REQUIRES_PEER_REVIEW',review_required=True)
    return result


PEER_POLICY = (
    'TERMINAL_TEXT_V1：所有角色及独立单模型的终态解释文字须非空，不能仅空白；'
    '原工具提案可有空content但不是终态报告，仍须原工具许可及schema校验。\n'
    'REQUIRED_PREDECESSOR_SEGMENTS_V2：完整原报告、角色职责与原生依赖保持。'
    '只有调用方列明的已发言必需前置角色需要定位；在原文字字段内直接写实际短ID，或用有界引用块'
    '<PEER_REFS>{"citations":[{"segment_id":"调用方实际短ID"}]}</PEER_REFS>选择真实可见句段。'
    '不用抄长句或哈希，不编ID，也不把调用方政策冒充同伴说过的话。'
    '首发无实际对手时不要求空引用块，不补造未发言立场。分析师与单模型基线不收到TA同伴原答，'
    '也不承担同伴引用义务。可以自由分析、转述和提出反证，但须保留同伴条件和不确定性；'
    '无实际分歧可明确一致。短ID只证明原句来源，不证明自己的批评公平或市场事实正确。'
    '实验中的中间原答是未验证观点，来源/引用诊断不得冒充验证通过；整体质量由完整原答逐源复核。')
