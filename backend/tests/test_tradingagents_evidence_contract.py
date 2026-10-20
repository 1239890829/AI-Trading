"""Small synthetic source fixtures, not independent human accuracy labels."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/tradingagents_evidence_contract.py'
SPEC = importlib.util.spec_from_file_location('ta_evidence_contract_test', SCRIPT)
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)


def packet(open_price='34.02', low='33.19'):
    return {'as_of': '2026-10-15T09:32:46+08:00', 'facts': {
        'security': {'symbol': '600001', 'market': 'SH', 'name': '样本股份'},
        'candidate_news': [
            {'record_id': 'news-a', 'source_symbols': ['600001.SH'], 'source': 'fixture-media',
             'title': '样本股份：预计利润增长', 'summary': '样本股份预计前三季度净利润增长。',
             'published_at': '2026-10-14T17:54:32+08:00', 'available_at': '2026-10-14T18:05:23+08:00'},
            {'record_id': 'news-b', 'source_symbols': ['000002.SZ'], 'source': 'other-media',
             'title': '另一公司(000002.SZ)半年度公告', 'summary': '另一公司上半年未经审计营业收入占比不足1%。',
             'published_at': '2026-10-14T17:00:00+08:00', 'available_at': '2026-10-14T17:01:00+08:00'}],
        'market_snapshot': {'record_id': 'quote-a', 'status': 'observed', 'received_before_as_of': True,
                            'units': {'price': 'CNY', 'volume': 'unverified', 'amount': 'unverified'},
                            'data': {'symbol': '600001', 'market': 'SH', 'price': '37.05', 'open': open_price,
                                     'high': '37.2', 'low': low, 'prev_close': '34.99', 'volume': 100,
                                     'amount': 2500, 'change_pct': '5.887',
                                     'received_at': '2026-10-15T01:32:45+00:00'}},
        'analyst_inputs': {'social': {'status': 'missing', 'missing': ['verified_social_posts']}}}}


class EvidenceContractTests(unittest.TestCase):
    def test_grounding_rebuilds_identity_clock_and_sources_for_each_packet(self):
        first = packet()
        second = deepcopy(first)
        second['as_of'] = '2026-10-19T17:00:00+08:00'
        second['facts']['security'] = {'symbol': '300123', 'market': 'SZ', 'name': '新样本'}
        second['facts']['candidate_news'] = [dict(first['facts']['candidate_news'][0],
                                                 record_id='new-source', source_symbols=['300123'],
                                                 title='新样本：签约仍待生效')]
        before = deepcopy(second)
        old = CONTRACT.build_grounding_contract(first, 'GROUNDING_CONTRACT_V6：只用本包。')
        new = CONTRACT.build_grounding_contract(second, 'GROUNDING_CONTRACT_V6：只用本包。')
        self.assertIn('new-source', new)
        self.assertIn('2026-10-19T17:00:00+08:00', new)
        self.assertNotIn('news-a', new)
        self.assertNotIn('另一公司', new)
        self.assertNotEqual(old, new)
        self.assertEqual(second, before)
        with self.assertRaisesRegex(ValueError, 'cannot be reused'):
            CONTRACT.build_grounding_contract(second, old)

    def check(self, expected, text, value=None):
        value = packet() if value is None else value
        before = deepcopy(value)
        result = CONTRACT.evaluate_relations(text, value)
        self.assertEqual(expected, result['status'], (text, result))
        self.assertEqual(value, before)
        self.assertTrue(result['checked_scope']['does_not_rewrite_report'])
        json.dumps(result, allow_nan=False)
        for row in result['violations'] + result['observations']:
            self.assertEqual(row['claim_sha256_utf8'], hashlib.sha256(text[row['start']:row['end']].encode()).hexdigest())
        return result

    def test_all_compatible_pairs_decimal_and_provenance(self):
        value = packet()
        ledger = CONTRACT.build_ledger(value)
        self.assertTrue(ledger['snapshot']['usable'])
        fields = ledger['snapshot']['fields']
        self.assertEqual(5, len(fields))
        self.assertEqual(10, len(ledger['snapshot']['comparisons']))
        self.assertNotIn('amount', fields)
        self.assertNotIn('volume', fields)
        for relation in ledger['snapshot']['comparisons']:
            lhs, rhs = Decimal(fields[relation['lhs']]['decimal']), Decimal(fields[relation['rhs']]['decimal'])
            self.assertEqual('=' if lhs == rhs else '>' if lhs > rhs else '<', relation['operator'])
            self.assertEqual([fields[relation['lhs']]['pointer'], fields[relation['rhs']]['pointer']], relation['source_pointers'])
        self.assertEqual('34.02', fields['open']['decimal'])
        self.assertEqual('33.19', fields['low']['decimal'])
        self.assertEqual(0, ledger['snapshot']['received_at']['utc_offset_seconds'])
        self.assertIn('+08:00', ledger['snapshot']['received_at']['beijing'])

    def test_no_packet_mutation_contract_not_self_referential(self):
        value = packet()
        before = deepcopy(value)
        ledger, contract = CONTRACT.build_ledger(value), CONTRACT.build_contract(value)
        self.assertEqual(value, before)
        value['grounding_contract'] = 'old constraints retained by caller'
        value['common_scope'] = 'outer trusted caller scope'
        self.assertEqual(ledger, CONTRACT.build_ledger(value))
        self.assertEqual(contract, CONTRACT.build_contract(value))
        self.assertTrue(contract.startswith('EVIDENCE_RELATIONS_V1'))
        self.assertNotIn('GROUNDING_CONTRACT_V2', contract)
        self.assertTrue(ledger['not_semantic_gold'])
        json.dumps(ledger, allow_nan=False)

    def test_subject_metadata_alone_is_not_proof(self):
        value = packet()
        docs = CONTRACT.build_ledger(value)['documents']
        self.assertTrue(docs[0]['subject']['eligible'])
        self.assertFalse(docs[1]['subject']['eligible'])
        value['facts']['candidate_news'][0]['title'] = '行业观察'
        self.assertIsNone(CONTRACT.build_ledger(value)['documents'][0]['subject']['eligible'])
        value['facts']['candidate_news'][0]['title'] = '另一公司000002.SZ与样本股份600001.SH'
        self.assertIsNone(CONTRACT.build_ledger(value)['documents'][0]['subject']['eligible'])

    def test_per_document_audit_issuer_not_borrowed(self):
        value = packet()
        first, other = CONTRACT.build_ledger(value)['documents']
        self.assertEqual('UNKNOWN', first['audit']['status'])
        self.assertEqual([], first['audit']['mentions'])
        self.assertEqual('UNKNOWN', first['issuer']['status'])
        self.assertEqual('fixture-media', first['issuer']['source_label'])
        self.assertEqual('未经审计', other['audit']['mentions'][0]['quote'])
        self.assertEqual('UNKNOWN', other['audit']['status'])
        value['facts']['candidate_news'][1]['audit_status'] = 'unaudited-specific-report'
        value['facts']['candidate_news'][1]['issuer'] = 'another-company'
        first, other = CONTRACT.build_ledger(value)['documents']
        self.assertEqual('UNKNOWN', first['audit']['status'])
        self.assertEqual('DECLARED', other['audit']['status'])
        self.assertEqual('/facts/candidate_news/1/issuer', other['issuer']['provenance']['pointer'])

    def test_attention_unknown_not_absent_not_automatic_semantic_pass(self):
        ledger = CONTRACT.build_ledger(packet())
        self.assertTrue(ledger['attention']['unknown_is_not_absent'])
        self.assertEqual('NOT_ESTABLISHED_BY_THIS_LEDGER', ledger['attention']['status'])
        for text in ['异常披露意味着关注度上升。', '对方预设利润将改善。', '这值得研究者继续关注。']:
            result = self.check('NOT_APPLICABLE', text)
            self.assertIn('attention measurement', result['checked_scope']['not_checked'])
            self.assertIn('peer fidelity', result['checked_scope']['not_checked'])
        # These families require common instructions AND real source review;
        # a finite price checker must not pretend to have verified their meaning.

    def test_actual_false_premise_is_not_pardoned_by_true_path_denial(self):
        self.check('FAIL', '虽然快照开盘价与最低价相同，不能据此断言盘中始终上涨。')
        self.check('FAIL', '开盘34.02与最低价33.19相同。')
        self.check('FAIL', 'open=low；后续不确定。')
        self.check('PASS', '开盘价高于最低价；最高价大于现价，前收盘价小于最新价。')
        self.check('PASS', 'open != low。')

    def test_equal_source_is_useful_fact_not_forced_abstention(self):
        value = packet('5.22', '5.220')
        self.check('PASS', '该快照open等于low，这只说明字段相等，不证明后续路径。', value)
        self.check('FAIL', '开盘价高于最低价。', value)
        self.check('PASS', '最高价大于最低价。', value)

    def test_conditions_negations_and_explicit_corrections(self):
        for text in ['若开盘价与最低价相同，再评估其他条件。', '无法证明开盘价等于最低价。',
                     '开盘价等于最低价尚未证实。', '原报告称“开盘价与最低价相同”，但源数不同，应更正。']:
            with self.subTest(text=text):
                self.check('NOT_APPLICABLE', text)
        for text in ['不能忽视开盘价与最低价相同后的风险。',
                     '不能证明其他事，但开盘价与最低价相同。',
                     '不能证明盘中始终上涨，开盘价与最低价相同。',
                     '无法认定未来走势，同时开盘价与最低价相同。',
                     '若资料补齐再评估，但开盘价与最低价相同已是事实。',
                     '不能不承认开盘价与最低价相同。']:
            with self.subTest(text=text):
                self.check('FAIL', text)
        self.check('NOT_APPLICABLE', '不能证明，开盘是否等于最低价仍未知。')

    def test_other_object_market_date_not_borrowed(self):
        for text in ['000002.SZ开盘价与最低价相同。', '600001.SZ开盘价与最低价相同。',
                     '2026-10-14开盘价与最低价相同。']:
            result = self.check('NOT_APPLICABLE', text)
            self.assertEqual('OTHER_OR_AMBIGUOUS_OBJECT_OR_DATE_NOT_LICENSED', result['observations'][0]['status'])
        self.check('FAIL', '600001.SH于2026-10-15开盘价与最低价相同。')

    def test_invalid_snapshot_cannot_support_relation(self):
        for key, value in [('symbol', '000002'), ('market', 'SZ'), ('received_at', '2026-10-16T00:00:00+08:00')]:
            data = packet()
            data['facts']['market_snapshot']['data'][key] = value
            self.assertFalse(CONTRACT.build_ledger(data)['snapshot']['usable'])
            self.check('FAIL', '开盘价高于最低价。', data)
        for value in [True, None, 'NaN', 'Infinity']:
            data = packet(value)
            self.assertNotIn('open', CONTRACT.build_ledger(data)['snapshot']['fields'])
            self.check('FAIL', '开盘价高于最低价。', data)

    def test_both_source_clocks_offsets_and_boundary(self):
        value = packet()
        row = value['facts']['candidate_news'][0]
        row['published_at'] = '2026-10-15T01:20:00+00:00'
        row['available_at'] = '2026-10-15T09:25:00+08:00'
        time = CONTRACT.build_ledger(value)['documents'][0]['time']
        self.assertTrue(time['eligible'])
        self.assertEqual(0, time['published_at']['utc_offset_seconds'])
        self.assertEqual(28800, time['available_at']['utc_offset_seconds'])
        self.check('PASS', 'news-a发布时间和可用时间均早于本例时点。', value)
        row['available_at'] = value['as_of']
        self.check('PASS', 'news-a可用时间不晚于as_of。', value)
        self.check('FAIL', 'news-a可用时间早于as_of。', value)
        row['available_at'] = '2026-10-15T10:00:00+08:00'
        self.assertFalse(CONTRACT.build_ledger(value)['documents'][0]['time']['eligible'])
        self.check('FAIL', 'news-a可用时间早于观察时点。', value)
        self.check('PASS', 'news-a可用时间晚于观察时点。', value)

    def test_missing_naive_inverted_clock_unknown_not_fabricated(self):
        for invalid in [None, '2026-10-14T18:05:23', 'invalid']:
            value = packet()
            value['facts']['candidate_news'][0]['available_at'] = invalid
            self.assertIsNone(CONTRACT.build_ledger(value)['documents'][0]['time']['eligible'])
            self.check('FAIL', 'news-a可用时间早于观察时点。', value)
        value = packet()
        value['facts']['candidate_news'][0]['available_at'] = '2026-10-13T18:05:23+08:00'
        self.assertIsNone(CONTRACT.build_ledger(value)['documents'][0]['time']['eligible'])

    def test_prior_single_model_date_order_defect_and_legal_denial(self):
        # Prior observed defect shape, with generic synthetic source identity.
        self.check('FAIL', 'news-a：发布时间10月14日17:54:32、可用时间18:05:23，'
                   '虽为相关消息，但晚于10月15日09:32:46的as_of，不能用于解释。')
        self.check('PASS', 'news-a发布时间和可用时间均早于10月15日09:32:46的as_of。')
        self.check('NOT_APPLICABLE', '无法证明news-a晚于本例时点。')
        self.check('NOT_APPLICABLE', '如果news-a晚于as_of，应排除。')

    def score_report(self, justification='该合格新闻使用预计增长措辞，主观语气偏正面。',
                     sources=None, before='', after=''):
        sources = sources if sources is not None else [
            {'record_id': 'news-a', 'field': 'summary', 'quote': '样本股份预计前三季度净利润增长。'}]
        block = json.dumps({'sources': sources, 'justification': justification}, ensure_ascii=False)
        return {'overall_score': 5.7, 'overall_band': 'Mildly Bullish', 'confidence': 'low',
                'narrative': before + '<NEWS_SCORE_BASIS>' + block + '</NEWS_SCORE_BASIS>' + after}

    def score_check(self, expected, report, value=None, role='Sentiment Analyst'):
        value = value if value is not None else packet()
        before = deepcopy(value), deepcopy(report)
        result = CONTRACT.evaluate_score_basis(report, value, role)
        self.assertEqual(expected, result['status'], result)
        self.assertEqual(before, (value, report))
        json.dumps(result, allow_nan=False)
        return result

    def test_actual_news_only_score_mixed_with_quote_basis_is_not_fixed_by_disclaimer(self):
        report = {'overall_score': 5.7, 'overall_band': 'Mildly Bullish', 'confidence': 'low',
                  'narrative': '这是仅新闻文本语气的主观刻度。评级略偏正面，'
                               '主要因为已有累计偏离值公告和盘中正涨幅字段。不能单独测量市场情绪。'}
        result = self.score_check('FAIL', report)
        self.assertIn('MARKET_FIELD_USED_IN_NEWS_SCORE_EXPLANATION', [r['reason'] for r in result['violations']])

    def test_bound_news_score_preserves_positive_and_mixed_opinion(self):
        result = self.score_check('PASS', self.score_report())
        self.assertEqual('EXACT_ELIGIBLE_SOURCE_BINDING_NOT_ENTAILMENT', result['observations'][0]['status'])
        mixed = self.score_report('同条文本有预计与风险的不同语气，我给主观Mixed而不声称预测正确。')
        mixed['overall_band'], mixed['overall_score'] = 'Mixed', 5
        self.score_check('PASS', mixed)

    def test_missing_score_can_abstain_without_inventing_neutral_or_source(self):
        report = {'overall_score': None, 'overall_band': None, 'confidence': 'low',
                  'narrative': '所给窗口缺少合格新闻与社交材料，无法判断，未提供分数。'}
        value = packet()
        value['facts']['candidate_news'] = []
        result = self.score_check('PASS', report, value)
        self.assertIn('EXPLICIT_NUMERIC_SCORE_ABSTENTION',
                      [row['status'] for row in result['observations']])

    def test_issuer_excerpt_closes_generic_title_gap_without_portal_inference(self):
        value = packet()
        row = value['facts']['candidate_news'][0]
        row.update(title='关于公司相关事项的公告', summary='公司预计前三季度净利润增长。',
                   document_id='notice-a', source_sha256='a' * 64)
        self.assertIsNone(CONTRACT.build_ledger(value)['documents'][0]['subject']['eligible'])
        quote = '证券代码：600001 样本股份有限公司'
        row['document_issuer'] = {'document_id': 'notice-a', 'source_sha256': 'a' * 64,
                                  'symbol': '600001', 'legal_name': '样本股份有限公司',
                                  'physical_page': 1, 'quote': quote,
                                  'quote_sha256': CONTRACT._sha(quote)}
        self.assertIs(CONTRACT.build_ledger(value)['documents'][0]['subject']['eligible'], True)
        row['document_issuer']['source_sha256'] = 'b' * 64
        self.assertIsNone(CONTRACT.build_ledger(value)['documents'][0]['subject']['eligible'])

    def test_null_pair_cannot_hide_numeric_news_score_or_half_abstention(self):
        report = {'overall_score': None, 'overall_band': None, 'confidence': 'low',
                  'narrative': '新闻情绪评分5.7/10，仍然偏乐观。'}
        self.score_check('FAIL', report)
        for band, score in [('Neutral', None), (None, 5)]:
            report.update(overall_band=band, overall_score=score,
                          narrative='缺少可核新闻证据，不能填中性分数。')
            self.score_check('FAIL', report)

    def test_valid_news_can_report_prices_rises_and_relative_deviation(self):
        value = packet()
        value['facts']['candidate_news'][0]['summary'] = '样本股份报道股价上涨及收盘价格涨幅偏离值披露。'
        report = self.score_report('所引新闻文字报道上涨及累计偏离，按新闻措辞给主观语气，不作情绪实测。',
                                   [{'record_id': 'news-a', 'field': 'summary', 'quote': '股价上涨及收盘价格涨幅偏离值披露'}])
        self.score_check('PASS', report, value)

    def test_independent_market_background_is_allowed_not_score_basis(self):
        report = self.score_report(before='独立行情背景：quote-a盘中涨幅5.8%，不代表社交实测。\n',
                                   after='\n账户未知，不作仓位提案。')
        self.score_check('PASS', report)
        self.score_check('PASS', self.score_report('本评分不依据行情快照，仅依据所引合格新闻文字。'))

    def test_explicit_market_or_selection_basis_rejected_inside_block(self):
        for reason in ['评级主要因为quote-a盘中走强而偏多。',
                       '评分依据market_snapshot的涨幅字段。',
                       '刻度基于行情快照正涨幅。']:
            with self.subTest(reason=reason):
                self.score_check('FAIL', self.score_report(reason))
        value = packet()
        value['facts']['selection'] = {'record_id': 'selector-a'}
        self.score_check('FAIL', self.score_report('评分主要依据selector-a判断该股强势。'), value)

    def test_valid_block_does_not_pardon_separate_score_because_market_assertion(self):
        report = self.score_report(after='\n评分只看新闻，但评级偏多主要因为盘中正涨幅字段。')
        result = self.score_check('FAIL', report)
        self.assertIn('MARKET_FIELD_USED_IN_NEWS_SCORE_EXPLANATION', [r['reason'] for r in result['violations']])

    def test_score_evidence_must_bind_real_eligible_exact_source(self):
        for sources in [
            [{'record_id': 'missing', 'field': 'summary', 'quote': '样本股份预计利润增长'}],
            [{'record_id': 'news-b', 'field': 'summary', 'quote': '另一公司上半年未经审计营业收入'}],
            [{'record_id': 'news-a', 'field': 'summary', 'quote': '原文没有说已经实现利润增长'}],
            [{'record_id': 'quote-a', 'field': 'summary', 'quote': '当时行情涨幅为正'}],
        ]:
            with self.subTest(sources=sources):
                self.score_check('FAIL', self.score_report(sources=sources))
        value = packet()
        value['facts']['candidate_news'][0]['available_at'] = '2026-10-16T00:00:00+08:00'
        self.score_check('FAIL', self.score_report(), value)

    def test_basis_shape_duplicates_nonfinite_and_oversize_rejected(self):
        for body in ['{}', '{"sources":[],"justification":"没有证据不能编中性"}',
                     '{"sources":[],"sources":[],"justification":"duplicate"}',
                     '{"sources":NaN,"justification":"nonfinite"}', 'x' * 8193]:
            report = self.score_report()
            report['narrative'] = '<NEWS_SCORE_BASIS>' + body + '</NEWS_SCORE_BASIS>'
            self.score_check('FAIL', report)
        report = self.score_report()
        report['narrative'] *= 2
        self.score_check('FAIL', report)

    def test_referenced_source_id_must_be_unique_not_silently_last_wins(self):
        value = packet()
        value['facts']['candidate_news'].append(deepcopy(value['facts']['candidate_news'][0]))
        result = self.score_check('FAIL', self.score_report(), value)
        self.assertIn('SCORE_SOURCE_RECORD_ID_NOT_UNIQUE', [r['reason'] for r in result['violations']])
        value = packet()
        value['facts']['candidate_news'].append(deepcopy(value['facts']['candidate_news'][1]))
        self.score_check('PASS', self.score_report(), value)

    def test_pm_trade_rating_not_forced_into_news_score_schema(self):
        self.score_check('NOT_APPLICABLE', {'rating': 'Bullish', 'investment_thesis':
                         '投资评级依据quote-a与新闻，账户参数仍未知。'}, role='Portfolio Manager')
        self.score_check('NOT_APPLICABLE', {'content': 'Rating: Hold。行情与新闻可供观察，账户未知。'},
                         role='single-baseline')

    def test_voluntary_single_news_numeric_score_has_same_contract(self):
        self.score_check('FAIL', {'content': '新闻语气主观评分5.7/10，但没有来源块。'}, role='single-baseline')
        report = self.score_report()
        content = '新闻语气评分5.7/10。\n' + report['narrative']
        self.score_check('PASS', {'content': content}, role='single-baseline')

    def test_single_explicit_other_scale_news_score_uses_same_binding(self):
        # Real prior single-output shape; its numeric range is not /10.
        declaration = '仅对上述两条合格新闻文本，给出主观语气刻度 overall_score=0.1（自定义−1至1），confidence=low。'
        self.score_check('FAIL', {'content': declaration}, role='single-baseline')
        self.score_check('PASS', {'content': declaration + '\n' + self.score_report()['narrative']})
        self.score_check('FAIL', {'content': '新闻语气评分=0.2，主观且未校准。'})
        self.score_check('NOT_APPLICABLE', {'content': '投资评级Hold，overall_score=0.1；行情与新闻分别讨论。'})

    def governance_packet(self):
        value = packet()
        value['facts']['candidate_news'][0].update(
            title='样本股份：第一大股东拟转让股份',
            summary='样本股份第一大股东拟公开征集受让方转让股份，完成后可能导致第一大股东发生变更。')
        return value

    def test_governance_ledger_keeps_concepts_conditions_and_provenance_separate(self):
        value = self.governance_packet()
        source = value['facts']['candidate_news'][0]
        row = CONTRACT.build_ledger(value)['documents'][0]['governance']
        self.assertEqual('NOT_INFERRED_FROM_PLANS_OR_SILENCE', row['current_legal_state'])
        self.assertEqual({'largest_shareholder'}, {m['kind'] for m in row['mentions'] if m['family'] == 'concept'})
        self.assertEqual({'plan', 'possible'}, {m['kind'] for m in row['mentions'] if m['family'] == 'plan_or_condition'})
        for mention in row['mentions']:
            text = source[mention['pointer'].split('/')[-1]]
            self.assertEqual(mention['quote'], text[mention['start']:mention['end']])
            self.assertEqual(mention['field_sha256_utf8'], hashlib.sha256(text.encode()).hexdigest())
        self.assertIn('ticktime是来源报价时钟', CONTRACT.build_contract(value))

    def test_actual_reported_largest_holder_to_control_upgrade_is_rejected(self):
        value = self.governance_packet()
        for text in ['合格新闻一则报告拟议股权转让及可能的控制权变化，另一则报告累计偏离。',
                     'news-a称可能的控制权变化。', 'news-a报告控股股东可能变更。',
                     'news-a披露实际控制人发生变更。', 'news-a报告实控人可能变更。',
                     '不能不承认news-a报告控制权变化。']:
            with self.subTest(text=text):
                result = self.check('FAIL', text, value)
                self.assertIn('ATTRIBUTED_GOVERNANCE_CONCEPT_OR_STATUS_NOT_IN_SOURCE',
                              [r['reason'] for r in result['violations']])
        self.check('PASS', 'news-a称第一大股东可能变更。', value)

    def test_governance_negations_corrections_hypotheses_and_other_objects(self):
        value = self.governance_packet()
        for text in ['不能证明news-a报告控制权变化。', '不得把news-a报告控制权变化当事实。',
                     'news-a没有报告控制权变化。', '若news-a报告控制权变化再核验。',
                     '作者假说：控制权可能变化，但需另证，新闻只写第一大股东。',
                     '旧稿错误称news-a报告控制权变化，但应更正为第一大股东可能变更。',
                     '000002.SZ的新闻报告控制权发生变更。']:
            with self.subTest(text=text):
                self.check('NOT_APPLICABLE', text, value)
        value['facts']['candidate_news'][0]['summary'] += '公告另称控制权可能发生变更。'
        self.check('PASS', 'news-a报告控制权可能变更。', value)

    def test_terminal_empty_actual_reply_and_whitespace_are_not_answers(self):
        # Exact observed terminal shape. Provider success did not make it usable.
        for value in [{'content': '', 'tool_calls': []}, {'content': ' \n\t', 'tool_calls': []},
                      {'tool_calls': []}, {}, {'content': None}, {'content': 0}]:
            with self.subTest(value=value):
                before = deepcopy(value)
                result = CONTRACT.evaluate_terminal_output(value)
                self.assertEqual('FAIL', result['status'])
                self.assertEqual(before, value)
                self.assertTrue(result['checked_scope']['does_not_rewrite_output'])
        self.assertEqual('PASS', CONTRACT.evaluate_terminal_output(
            {'content': '材料不足，保留未知。', 'tool_calls': []})['status'])

    def test_terminal_punctuation_or_numeric_fragments_are_not_explanations(self):
        for text in ('{', '{}', '[]', '```', '…', '123.45'):
            with self.subTest(text=text):
                value = {'content': text, 'tool_calls': []}
                before = deepcopy(value)
                result = CONTRACT.evaluate_terminal_output(value)
                self.assertEqual('FAIL', result['status'])
                self.assertEqual(before, value)
        for text in ('证据不足。', 'Unknown: missing source.', '600001资料未提供。'):
            with self.subTest(text=text):
                self.assertEqual('PASS', CONTRACT.evaluate_terminal_output(
                    {'content': text, 'tool_calls': []})['status'])

    def test_terminal_typed_text_is_required_but_numeric_nulls_are_preserved(self):
        for field, marker in [('narrative', {'overall_score': 6.0}),
                              ('rationale', {'recommendation': 'Hold'}),
                              ('reasoning', {'action': 'Hold'}),
                              ('investment_thesis', {'rating': 'Hold'})]:
            for text in ['', ' \n', None]:
                with self.subTest(field=field, text=text):
                    self.assertEqual('FAIL', CONTRACT.evaluate_terminal_output({**marker, field: text})['status'])
            self.assertEqual('FAIL', CONTRACT.evaluate_terminal_output(marker)['status'])
            self.assertEqual('PASS', CONTRACT.evaluate_terminal_output({**marker, field: '缺资料，暂不形成交易提案。'})['status'])
        value = {'action': 'Hold', 'reasoning': '账户和执行资料未知。',
                 'entry_price': None, 'stop_loss': None, 'position_sizing': None}
        before = deepcopy(value)
        self.assertEqual('PASS', CONTRACT.evaluate_terminal_output(value)['status'])
        self.assertEqual(before, value)

    def test_terminal_empty_tool_proposal_is_not_mistaken_for_empty_report(self):
        value = {'content': '', 'tool_calls': [
            {'id': 'original-codec-id', 'name': 'get_stock_data', 'arguments': {}}]}
        result = CONTRACT.evaluate_terminal_output(value)
        self.assertEqual('NOT_APPLICABLE', result['status'])
        self.assertTrue(result['observations'][0]['offered_tool_validation_still_required'])
        self.assertIn('schema', result['checked_scope']['not_checked'])
        self.assertEqual('FAIL', CONTRACT.evaluate_terminal_output({'content': '', 'tool_calls': ['bad']})['status'])

    def test_planned_source_does_not_establish_current_negative_legal_state(self):
        value = self.governance_packet()
        for text in ['news-a明确为拟/可能——尚未生效、受让方未定。',
                     'news-a披露股权转让尚未完成。']:
            with self.subTest(text=text):
                self.check('FAIL', text, value)
        for text in ['news-a材料未证明完成，当前状态和受让方未知。',
                     '拟议事项仍需核实进展，不能断言news-a披露转让尚未完成。']:
            with self.subTest(text=text):
                self.check('NOT_APPLICABLE', text, value)
        value['facts']['candidate_news'][0]['summary'] += '该公告明确转让尚未完成，受让方未定。'
        self.check('PASS', 'news-a披露转让尚未完成、受让方未定。', value)

    def peer_context(self, text=None, role='Bear Researcher'):
        text = text or '若完整财报解释利润下降，这才可能加强论据；现有材料尚未做到。'
        output = {'content': text, 'tool_calls': []}
        return {'case_id': 'sample-case', 'arm': 'ta-luna', 'role': role,
                'required_peer_roles': list(CONTRACT.PEER_REQUIRED[role]),
                'accepted_peers': [{'peer_id': 'p9', 'role': 'Bull Researcher', 'call_id': 9,
                                    'output_sha256': hashlib.sha256(json.dumps(output, ensure_ascii=False,
                                        sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                                    'output': output, 'visible_fields': ['/content'],
                                    'case_id': 'sample-case', 'arm': 'ta-luna', 'accepted': True}]}

    def peer_report(self, quote=None, body='我的判断：现有证据不足以形成新的交易提案。', citations=None):
        if citations is None:
            citations = [{'peer_id': 'p9', 'field': '/content', 'quote': quote or
                          '若完整财报解释利润下降，这才可能加强论据；现有材料尚未做到。'}]
        return {'content': '<PEER_EVIDENCE>' + json.dumps({'citations': citations}, ensure_ascii=False)
                + '</PEER_EVIDENCE>\n' + body, 'tool_calls': []}

    def peer_check(self, expected, report, context=None):
        context = self.peer_context() if context is None else context
        before = deepcopy((report, context))
        result = CONTRACT.evaluate_peer_evidence(report, context)
        self.assertEqual(expected, result['status'], result)
        self.assertEqual(before, (report, context))
        json.dumps(result, allow_nan=False)
        return result

    def test_peer_index_contains_identity_and_visible_fields_not_duplicate_body(self):
        context = self.peer_context()
        index = CONTRACT.build_peer_contract(context)
        self.assertTrue(index.startswith('<PEER_CONTEXT>'))
        self.assertNotIn(context['accepted_peers'][0]['output']['content'], index)
        self.assertIn('allowed_string_fields', index)
        self.assertIn('PEER_EVIDENCE_V1', CONTRACT.build_contract(packet()))
        self.assertIn('TERMINAL_TEXT_V1', CONTRACT.build_contract(packet()))

    def test_full_peer_condition_and_negative_sentence_bind_without_hash_copy(self):
        result = self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report())
        self.assertTrue(result['review_required'])
        self.assertEqual([], result['format_findings'])
        self.assertEqual(1, len(result['observations']))
        source = self.peer_context()['accepted_peers'][0]['output']['content']
        obs = result['observations'][0]
        self.assertEqual(source, source[obs['source_start']:obs['source_end']])
        text = '**若完整财报解释利润下降，这才可能加强论据；现有材料尚未做到。**'
        self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report(), self.peer_context(text))
        self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report('账户未知，不提出新的交易方案'),
                        self.peer_context('账户未知，不提出新的交易方案'))

    def test_peer_false_quote_condition_truncation_and_outside_attribution_fail(self):
        self.peer_check('FAIL', self.peer_report('足以支持继续看多。'))
        for quote in ['这才可能加强论据；现有材料尚未做到。',
                      '若完整财报解释利润下降，这才可能加强论据；']:
            with self.subTest(quote=quote):
                self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report(quote))
        self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report(body='多头把观察推进成足以支持继续看多。'))
        self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report(body='对方主张已有交易依据。'))
        self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report(body='我选择Bullish作为自身研究评级；账户仍未知。'))
        self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report(citations=[]))

    def test_peer_foreign_identity_hash_unaccepted_proposal_and_required_context_reject(self):
        mutations = [('output_sha256', '0' * 64), ('case_id', 'other-case'), ('arm', 'other-arm'),
                     ('accepted', False), ('state', 'started'), ('role', 'Bear Researcher'),
                     ('visible_fields', ['/missing'])]
        for field, child in mutations:
            context = self.peer_context()
            context['accepted_peers'][0][field] = child
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    CONTRACT.build_peer_contract(context)
                self.assertTrue(self.peer_check('FAIL', self.peer_report(), context)['caller_context_error'])
        context = self.peer_context()
        context['accepted_peers'][0]['output'] = {'content': '', 'tool_calls': [{'name': 'get_news'}]}
        with self.assertRaises(ValueError):
            CONTRACT.build_peer_contract(context)
        context = self.peer_context()
        context['required_peer_roles'] = []
        with self.assertRaises(ValueError):
            CONTRACT.build_peer_contract(context)

    def test_peer_wrong_id_field_and_original_news_cannot_masquerade_as_peer(self):
        for peer_id, field in [('p10', '/content'), ('news-a', '/summary'), ('p9', '/invisible')]:
            with self.subTest(peer_id=peer_id, field=field):
                self.peer_check('FAIL', self.peer_report(citations=[{
                    'peer_id': peer_id, 'field': field, 'quote': '样本新闻来源称拟议转让股份。'}]))
        value = self.peer_report()
        value['content'] = value['content'].replace('"citations":', '"citations": [], "citations":')
        self.peer_check('FAIL', value)
        value = self.peer_report()
        value['content'] = value['content'].replace('"quote":', '"paraphrase":"已经看多", "quote":')
        self.peer_check('FAIL', value)
        for stray in ['<PEER_EVIDENCE>', '</PEER_EVIDENCE>']:
            with self.subTest(stray=stray):
                value = self.peer_report()
                value['content'] += stray
                self.peer_check('FAIL', value)

    def test_peer_first_bull_empty_evidence_and_analyst_single_nonapplicability(self):
        context = {'case_id': 'sample-case', 'arm': 'ta-luna', 'role': 'Bull Researcher',
                   'required_peer_roles': [], 'accepted_peers': []}
        self.peer_check('PASS', self.peer_report(citations=[], body='多头观点：我只论证有源观察依据。'), context)
        self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report(citations=[], body='空方若说已经反转就错了。'), context)
        for role, arm in [('News Analyst', 'ta-luna'), ('single-baseline', 'sol')]:
            other = {'role': role, 'arm': arm, 'required_peer_roles': [], 'accepted_peers': []}
            self.peer_check('NOT_APPLICABLE', {'content': '自己的分析与明确假说。'}, other)
            self.assertEqual('', CONTRACT.build_peer_contract(other))

    def test_no_required_peer_can_omit_empty_block_without_licensing_attribution(self):
        context = {'case_id': 'sample-case', 'arm': 'ta-luna', 'role': 'Bull Researcher',
                   'required_peer_roles': [], 'accepted_peers': []}
        result = self.peer_check('PASS', {'content': '多头观点：只支持继续研究观察，不提出账户交易。',
                                           'tool_calls': []}, context)
        self.assertEqual('IMPLICIT_EMPTY_NO_REQUIRED_PEERS', result['observations'][0]['status'])
        self.peer_check('REQUIRES_PEER_REVIEW', {'content': '空方主张已有交易依据。', 'tool_calls': []}, context)
        self.peer_check('REQUIRES_PEER_REVIEW', {'content': '我认为资料不足，不提出交易。', 'tool_calls': []})
        for tag in ['<PEER_EVIDENCE>', '</PEER_EVIDENCE>']:
            with self.subTest(tag=tag):
                self.peer_check('FAIL', {'content': '只支持研究观察。' + tag, 'tool_calls': []}, context)

    def test_peer_cannot_relabel_nested_prior_citation_or_trim_year(self):
        context = self.peer_context('2026年资料尚未补齐，不构成新的交易依据。')
        self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report('年资料尚未补齐，不构成新的交易依据。'), context)
        nested = '<PEER_EVIDENCE>{"citations":[{"quote":"前句。没有补齐资料，不形成新的交易提案。"}]}</PEER_EVIDENCE>'
        self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report('没有补齐资料，不形成新的交易提案。'), self.peer_context(nested))

    def test_peer_format_findings_never_supply_a_semantic_review(self):
        for report in [self.peer_report(), {'content': '多头把条件说成已确定。', 'tool_calls': []}]:
            with self.subTest(report=report):
                result = self.peer_check('REQUIRES_PEER_REVIEW', report)
                self.assertTrue(result['review_required'])
                self.assertNotEqual('PASS', result['status'])
                self.assertNotIn('source_review', result)
        result = self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report(
            '这才可能加强论据；现有材料尚未做到。', body='多头预设论据成立。'))
        self.assertEqual({'PEER_QUOTE_NOT_FULL_SENTENCE', 'OUTSIDE_BLOCK_EXPLICIT_PEER_ATTRIBUTION'},
                         {item['reason'] for item in result['format_findings']})

    def test_optional_actual_peer_use_also_requires_full_source_review(self):
        context = self.peer_context()
        context.update(role='Bull Researcher', required_peer_roles=[])
        context['accepted_peers'][0]['role'] = 'News Analyst'
        result = self.peer_check('REQUIRES_PEER_REVIEW', self.peer_report(), context)
        self.assertTrue(result['review_required'])
        self.assertEqual([], result['format_findings'])


    def clock_packet(self):
        value = packet()
        value['as_of'] = '2026-10-19T17:02:45.821326+08:00'
        value['facts']['candidate_news'][0].update(
            record_id='observation-9973', published_at='2026-10-16T22:42:22.000000+08:00',
            available_at='2026-10-16T23:12:31.699293+08:00')
        value['facts']['market_snapshot']['record_id'] = 'new-6-quote'
        value['facts']['market_snapshot']['data']['received_at'] = '2026-10-19T09:02:45.820545Z'
        return value

    def test_real_explicit_source_clock_copy_errors_are_not_hidden_by_before_asof(self):
        value = self.clock_packet()
        for text in (
            'observation-9973 available_at为2026-10-16T22:12:31.699293+08:00。',
            'new-6-quote received_at为2026-10-19T16:02:45.820545Z。',
            'new-6-quote 来源报价时间为2026-10-19 08:14:56 UTC（北京时间16:14:56），接收时间16:02:45 UTC。',
        ):
            with self.subTest(text=text):
                result = self.check('FAIL', text, value)
                self.assertIn('SOURCE_CLOCK_VALUE_CONTRADICTED',
                              [row['reason'] for row in result['violations']])

    def test_clock_restatement_original_utc_conversion_and_declared_precision(self):
        value = self.clock_packet()
        for text in (
            'observation-9973 `/available_at`：2026-10-16T23:12:31.699293+08:00。',
            'observation-9973 可用时间为2026-10-16T15:12:31.699293Z。',
            'new-6-quote received_at=2026-10-19T09:02:45.820545Z。',
            'new-6-quote 接收时间：2026-10-19T17:02:45.820545+08:00。',
            'new-6-quote received_at=2026-10-19T09:02:45Z。',
            'new-6-quote 来源报价时间为2026-10-19 08:14:56 UTC，接收时间09:02:45 UTC。',
        ):
            with self.subTest(text=text):
                result = self.check('PASS', text, value)
                self.assertTrue(any(row['kind'] == 'EXPLICIT_SOURCE_CLOCK_VALUE'
                                    for row in result['observations']))
        value['facts']['candidate_news'][0]['available_at'] = '2026-10-18T23:12:31.699293Z'
        self.check('PASS', 'observation-9973 available_at为2026-10-19T07:12:31.699293+08:00。', value)
        self.check('FAIL', 'observation-9973 available_at为2026-10-18T07:12:31.699293+08:00。', value)

    def test_explicit_clock_negation_hypothesis_unknown_and_ambiguous_bindings_are_not_invented_facts(self):
        value = self.clock_packet()
        for text in (
            'new-6-quote received_at不是2026-10-19T16:02:45.820545Z。',
            '假设new-6-quote received_at为2026-10-19T16:02:45.820545Z，才需要重核时间。',
            '旧稿误称new-6-quote received_at为2026-10-19T16:02:45.820545Z，这一时刻有误，应更正。',
        ):
            with self.subTest(text=text):
                result = self.check('NOT_APPLICABLE', text, value)
                self.assertTrue(result['observations'])
        for text in (
            '接收时间为2026-10-19T16:02:45.820545Z。',
            'new-6-quote received_at未知，完整时刻仍待核验。',
            'new-6-quote received_at为16:02:45 UTC。',
            'observation-9973与new-6-quote的received_at为2026-10-19T16:02:45.820545Z。',
            'new-6-quote received_at为2026-10-19T16:02:45。',
        ):
            with self.subTest(text=text):
                self.check('NOT_APPLICABLE', text, value)
        for text in (
            'new-6-quote received_at不是2026-10-19T16:02:45.820545Z，但new-6-quote received_at为2026-10-19T16:02:45.820545Z。',
            '并非不能确认new-6-quote received_at为2026-10-19T16:02:45.820545Z。',
            '假设资料仍待补充，实际new-6-quote received_at为2026-10-19T16:02:45.820545Z。',
        ):
            with self.subTest(text=text):
                self.check('FAIL', text, value)
        value['facts']['market_snapshot']['data'].pop('received_at')
        result = self.check('FAIL', 'new-6-quote received_at为2026-10-19T09:02:45.820545Z。', value)
        self.assertEqual('SOURCE_CLOCK_FIELD_UNPROVEN', result['violations'][0]['reason'])


if __name__ == '__main__':
    unittest.main()
