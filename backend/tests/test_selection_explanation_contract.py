import copy
import json

import pytest

from scripts.selection_explanation_contract import prepare_cases, validate_response


def sample(**kwargs):
    return prepare_cases([{'symbol': '600127', 'name': '公开示例', 'date': '2026-09-21',
        'bases': {'capital': '主力净流入 1 亿', 'tech': '技术面中性：2 多 / 3 空，多空分歧'},
        'invalidations': ['跌破原记录参考线则条件失效'], **kwargs}])


def valid(cases):
    return {'items': [{'id': c['id'], 'input_sha256': c['input_sha256'],
        'state': 'insufficient' if c['purpose'] in {'relation', 'review'} else 'unverified',
        'statement_ids': [] if c['purpose'] in {'relation', 'review'} else
            ['technical'] if c['purpose'] == 'risk' else ['capital', 'technical'],
        'conditions': copy.deepcopy(c['required_conditions'])} for c in cases]}


def test_preserves_four_questions_full_sources_and_conditions_without_faking_events():
    cases = sample()
    rows = validate_response(cases, json.dumps(valid(cases), ensure_ascii=False))
    assert [c['purpose'] for c in cases] == ['selection', 'relation', 'risk', 'review']
    assert all(not c['events_provided'] and not c['outcomes_provided'] for c in cases)
    assert rows[0]['summary'] == '记录显示主力资金净流入；走势指标仍有分歧。'
    assert rows[0]['evidence'][0]['quote'] == '主力净流入 1 亿'
    assert all(row['conditions'] == cases[0]['required_conditions'] for row in rows)


@pytest.mark.parametrize('label,text', [('30-60 中性带', '原记录将估值列为中性'),
    ('偏高', '原记录提示估值偏高'), ('偏低', '原记录将估值列为偏低')])
def test_preserves_explicit_valuation_label_without_new_price_thresholds(label, text):
    cases = sample(bases={'capital': '主力净流入 1 亿', 'tech': '多空分歧',
        'fundamental': f'PE 50.9（{label}）；营收增速 +85.1%'})
    rows = validate_response(cases, json.dumps(valid(cases), ensure_ascii=False))
    assert text in rows[0]['summary']
    assert f'PE 50.9（{label}）' in rows[0]['evidence'][-1]['quote']
    if '中性' in label:
        assert '偏贵' not in rows[0]['summary'] and '偏高' not in rows[0]['summary']


@pytest.mark.parametrize('source', ['PE 115.0', '市盈率未提供', 'PE -50.9（亏损，不适用估值比较）', 'PE 50.9（并非偏高）', 'PE 50.9（中性或偏高待核）'])
def test_unknown_or_inapplicable_valuation_never_infers_cheap_or_expensive(source):
    cases = sample(bases={'capital': '主力净流入 1 亿', 'tech': '多空分歧', 'fundamental': source})
    assert all(atom['id'] != 'valuation' for atom in cases[0]['statements'])


@pytest.mark.parametrize('failure', ['identity', 'hash', 'condition', 'fabricated_review',
    'fabricated_relation', 'invented_growth', 'free_prose', 'duplicate', 'missing',
    'missing_risk', 'support_as_risk', 'insufficient_assertion'])
def test_rejects_wrong_identity_unbound_claim_or_omitted_condition(failure):
    cases = sample()
    out = valid(cases)
    row = out['items'][0]
    if failure == 'identity': row['id'] = '600128:selection'
    elif failure == 'hash': row['input_sha256'] = 'wrong'
    elif failure == 'condition': row['conditions'] = []
    elif failure == 'fabricated_review': out['items'][3]['state'] = 'unverified'
    elif failure == 'fabricated_relation': out['items'][1]['state'] = 'unverified'
    elif failure == 'invented_growth': row['statement_ids'] = ['扩大LNG需求']
    elif failure == 'free_prose': row['summary'] = '估值偏贵，新增订单将带来利润增长。'
    elif failure == 'duplicate': out['items'][1] = copy.deepcopy(row)
    elif failure == 'missing': out['items'].pop()
    elif failure == 'missing_risk': row['statement_ids'] = ['capital']
    elif failure == 'support_as_risk': out['items'][2]['statement_ids'] = ['capital']
    else: row['state'] = 'insufficient'
    with pytest.raises(ValueError): validate_response(cases, json.dumps(out, ensure_ascii=False))


def test_no_progress_to_demand_or_sector_to_beneficiary_translation():
    cases = sample(bases={'news': 'LNG加注规模居前列；绿色甲醇全链贯通',
        'fundamental': '公司属于绿色燃料行业'})
    assert cases[0]['statements'] == []
    out = valid(cases)
    for row in out['items']:
        row.update(state='insufficient', statement_ids=[])
    rows = validate_response(cases, json.dumps(out, ensure_ascii=False))
    assert all('扩大' not in row['summary'] for row in rows)
    assert '未提供可核对' in rows[1]['summary']


def test_approximate_chip_evidence_keeps_qualifier_in_summary_and_full_quote():
    source = '主峰 18.93（现价上方，压力），量能比 0.658（CYQ 近似口径，as_of 2026-09-18）'
    cases = sample(bases={'capital': '主力净流入 1 亿', 'chip': source})
    out = valid(cases)
    out['items'][0]['statement_ids'] = ['capital', 'chip']
    out['items'][2]['statement_ids'] = ['chip']
    row = validate_response(cases, json.dumps(out, ensure_ascii=False))[0]
    assert '近似筹码数据' in row['summary']
    assert row['evidence'][1]['quote'] == source
