import copy
import json

import pytest

from scripts.selection_explanation_contract import prepare_cases, validate_response


def sample():
    return prepare_cases([{'symbol': '600127', 'name': '公开示例', 'date': '2026-09-21',
        'bases': {'capital': '原记录：资金净流入1亿', 'news': '仅有关联，尚未形成订单'},
        'invalidations': ['跌破原记录参考线则条件失效']}])


def valid(cases):
    return {'items': [{'id': c['id'], 'input_sha256': c['input_sha256'],
        'state': 'insufficient' if c['purpose'] in {'relation', 'review'} else 'unverified',
        'summary': '记录有资金支持，仍需核对后续条件。' if c['purpose'] not in {'relation', 'review'} else '未提供对应资料，当前无法判断。',
        'evidence': [] if c['purpose'] in {'relation', 'review'} else [{'field': 'basis.capital', 'quote': '资金净流入1亿'}],
        'conditions': copy.deepcopy(c['required_conditions'])} for c in cases]}


def test_preserves_four_distinct_questions_without_faking_news_or_outcomes():
    cases = sample()
    assert [c['purpose'] for c in cases] == ['selection', 'relation', 'risk', 'review']
    assert len(validate_response(cases, json.dumps(valid(cases), ensure_ascii=False))) == 4
    assert all(not c['events_provided'] and not c['outcomes_provided'] for c in cases)
    assert all(c['facts']['basis.news'] == '仅有关联，尚未形成订单' for c in cases)


@pytest.mark.parametrize('failure', ['identity', 'hash', 'quote', 'condition', 'fabricated_review', 'fabricated_relation', 'investment', 'duplicate', 'missing'])
def test_rejects_wrong_stock_unbound_claim_or_omitted_condition(failure):
    cases = sample()
    out = valid(cases)
    row = out['items'][0]
    if failure == 'identity':
        row['id'] = '600128:selection'
    elif failure == 'hash':
        row['input_sha256'] = 'wrong'
    elif failure == 'quote':
        row['evidence'][0]['quote'] = '资金净流入10亿'
    elif failure == 'condition':
        row['conditions'] = []
    elif failure == 'fabricated_review':
        out['items'][3]['state'] = 'unverified'
    elif failure == 'fabricated_relation':
        out['items'][1]['state'] = 'unverified'
    elif failure == 'investment':
        row['summary'] = '建议买入，必涨。'
    elif failure == 'duplicate':
        out['items'][1] = copy.deepcopy(row)
    else:
        out['items'].pop()
    with pytest.raises(ValueError):
        validate_response(cases, json.dumps(out, ensure_ascii=False))
