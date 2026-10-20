"""Portable offline logical/source fixtures. These are not human accuracy gold."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve()
SCRIPT = HERE.parent / 'evidence_gate.py'
if not SCRIPT.is_file():
    SCRIPT = HERE.parents[1] / 'scripts' / 'tradingagents_source_gate.py'
SPEC = importlib.util.spec_from_file_location('tradingagents_source_gate_test', SCRIPT)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)
evaluate_report, source_semantics = GATE.evaluate_report, GATE.source_semantics

PACKET = {'as_of': '2026-10-14T09:43:08+08:00', 'facts': {
    'security': {'market': 'SH', 'name': '样本股份', 'symbol': '600001'},
    'candidate_news': [{'record_id': 'news-08', 'source_symbols': ['600001'],
        'title': '样本股份股票交易异常波动公告',
        'summary': '样本股份（600001）在10月9日、12日和13日连续3个交易日，收盘价格涨幅偏离值累计超过20%。',
        'published_at': '2026-10-13T18:11:00+08:00',
        'available_at': '2026-10-13T19:06:43+08:00'}],
    'market_snapshot': {'open': 9.0, 'low': 8.92, 'volume': 10000, 'semantics': 'ONE_OBSERVED_SNAPSHOT'},
    'historical_ohlcv': None}}


def history(closes, first_previous=10):
    packet = deepcopy(PACKET)
    days = ['2026-10-08', '2026-10-09', '2026-10-12', '2026-10-13'][-len(closes):]
    packet['facts']['trading_calendar'] = {'market': 'SH', 'version': 'OFFLINE_CALENDAR-v1', 'complete': True,
        'window_start': '2026-10-08', 'window_end': '2026-10-13',
        'sessions': ['2026-10-08', '2026-10-09', '2026-10-12', '2026-10-13'],
        'published_at': '2026-10-01T00:00:00+08:00', 'available_at': '2026-10-01T00:01:00+08:00'}
    previous = [first_previous] + closes[:-1]
    packet['facts']['price_history'] = {'symbol': '600001.SH', 'market': 'SH', 'version': 'OFFLINE_FIXTURE-v1',
        'price_basis': 'same_unadjusted', 'frequency': '1d', 'sessions': days,
        'observations': [{'session': day, 'prev_close': prior, 'close': value, 'final': True,
                          'session_close_at': day + 'T15:00:00+08:00',
                          'published_at': day + 'T15:01:00+08:00', 'available_at': day + 'T15:02:00+08:00'}
                         for day, prior, value in zip(days, previous, closes)]}
    return packet


class EvidenceGateTests(unittest.TestCase):
    def test_account_evidence_requirements_do_not_assert_execution(self):
        for text in (
            '若要形成账户方案，还需账户持仓、现金、风险预算、交易成本及实际可成交证据。',
            '也不能用换手率0.71证明该账户可成交。',
        ):
            with self.subTest(text=text):
                self.assertEqual('PASS', evaluate_report(text, PACKET)['status'])

    def test_account_assertions_stay_blocked_after_prerequisite_or_double_negation(self):
        for text in (
            '还需账户持仓，但是当前账户可以成交。',
            '还需可成交证据，现在已经齐全。',
            '不是不能用换手率证明该账户可成交。',
            '实际可成交证据已经具备。',
        ):
            with self.subTest(text=text):
                self.assertEqual('FAIL', evaluate_report(text, PACKET)['status'])

    def assert_status(self, expected, value, packet=PACKET):
        before = json.dumps(packet, ensure_ascii=False, sort_keys=True)
        result = evaluate_report(value, packet)
        self.assertEqual(expected, result['status'], (value, result))
        self.assertEqual(before, json.dumps(packet, ensure_ascii=False, sort_keys=True))
        self.assertTrue(result['checked_scope']['does_not_rewrite_report'])
        return result

    def test_observed_failure_shape_and_correct_denial(self):
        self.assert_status('FAIL', '该累计偏离值不能当作累计收益率。连续上涨后的风险约束仍须注意。')
        self.assert_status('PASS', '累计偏离不是累计收益率。没有逐日价格就不能证明连续上涨。不保证成交。')
        self.assert_status('PASS', '不能生成技术指标、实测情绪、胜率或可成交结论。换手率不证明特定账户能够成交。')
        self.assert_status('PASS', '累计偏离不能当作累计收益率；参与评估不能理解为账户能够成交。')
        self.assert_status('PASS', '不能由当时上涨推导持续买盘、后续封板或账户可成交。')
        self.assert_status('PASS', '不能把该时点上涨推演为后续封板或账户可成交。')
        self.assert_status('PASS', '观察资格不能直接变成可成交结论。不是账户可成交证明。')
        self.assert_status('PASS', '不能把偏离值说成股票累计涨幅。')

    def test_source_quote_and_useful_facts_not_all_abstention(self):
        self.assert_status('PASS', 'news-08原文为“连续3个交易日，收盘价格涨幅偏离值累计超过20%”。'
                           '这不能证明连续上涨，也不是累计收益率。拟转让10%仍是拟议事项；快照open9.0、low8.92。')

    def test_synonyms_and_presupposition_not_keyword_only(self):
        for sentence in ['三连涨后的风险', '连续上涨后的风险约束', '不能忽略连续上涨后的风险',
                         '不应忽视持续上行的风险', '每日都收涨带来风险', '价格一日高过一日',
                         '持续上行后的风险', 'consecutive daily gains imply risk']:
            with self.subTest(sentence=sentence):
                self.assert_status('FAIL', sentence)

    def test_local_negation_and_condition_not_global_pardon(self):
        for sentence in ['不能证明连续上涨。', '证据不足以确认持续上行。', '若逐日行情证实连续上涨，再评估。',
                         '是否连续上涨仍待核验。', '连续上涨尚未证实。', '不能把累计偏离视为连续上涨。',
                         '不能排除连续上涨。', '若连续上涨后回撤，再评估。']:
            with self.subTest(sentence=sentence):
                self.assert_status('PASS', sentence)
        for sentence in ['不能证明连续上涨，但连续上涨后的风险仍须注意。',
                         '不能证明连续上涨但连续上涨后的风险仍须注意。',
                         '不能把偏离当累计收益率。不能忽略连涨后的风险。',
                         '若逐日数据证明连续上涨但连续上涨已是事实。',
                         '连续上涨后的抛压尚未证实。', '是否买入要看连续上涨后的风险。',
                         '不能排除连续上涨后的回撤风险。', '不能证明连续上涨后的抛压。',
                         '并不是不能证明连续上涨。']:
            with self.subTest(sentence=sentence):
                self.assert_status('FAIL', sentence)

    def test_aggregate_does_not_determine_daily_path(self):
        packet = history([10, 12, 11, 12.5])
        self.assertEqual('25.00', source_semantics(packet)['daily_close_capability']['cumulative_return_percent'])
        self.assert_status('PASS', '该窗口累计收益率25%。', packet)
        self.assert_status('FAIL', '连续3个交易日上涨。', packet)
        self.assert_status('FAIL', '累计收益率25%。')

    def test_daily_path_needs_each_session_previous_close(self):
        packet = history([11, 12, 12.5])
        result = self.assert_status('PASS', '所给窗口连续3个交易日收涨。', packet)
        self.assertEqual([True, True, True], source_semantics(packet)['daily_close_capability']['positive_close_steps'])
        self.assertTrue(result['observations'])
        self.assert_status('FAIL', '盘中连续上涨全程没有回撤。', packet)
        self.assert_status('FAIL', '连续4个交易日上涨。', packet)
        self.assert_status('FAIL', '连续上涨10天。', packet)
        self.assert_status('FAIL', '连续上涨十一天。', packet)
        self.assert_status('FAIL', '连续2个交易日上涨10天。', packet)
        missing = deepcopy(packet)
        del missing['facts']['price_history']['observations'][0]['prev_close']
        self.assert_status('FAIL', '连续3个交易日上涨。', missing)
        only_two = history([10, 11, 12], first_previous=10)
        self.assert_status('FAIL', '连续3个交易日上涨。', only_two)
        self.assert_status('PASS', '连续2个交易日上涨。', only_two)

    def test_missing_identity_time_version_calendar_chain_reject(self):
        for mutation in ['gap', 'foreign', 'market', 'future', 'basis', 'duplicate', 'unfinal', 'version',
                         'calendar_missing', 'calendar_gap', 'calendar_future', 'calendar_incomplete', 'chain']:
            packet = history([11, 12, 12.5]); block = packet['facts']['price_history']
            if mutation == 'gap':
                block['observations'].pop(1)
            elif mutation == 'foreign':
                block['observations'][1]['symbol'] = '000811.SZ'
            elif mutation == 'market':
                block['observations'][1]['market'] = 'SZ'
            elif mutation == 'future':
                block['observations'][1]['available_at'] = '2026-10-15T15:02:00+08:00'
            elif mutation == 'basis':
                block['observations'][1]['price_basis'] = 'other_adjustment'
            elif mutation == 'duplicate':
                block['sessions'][1] = block['sessions'][0]
            elif mutation == 'unfinal':
                block['observations'][1]['final'] = False
            elif mutation == 'version':
                block['observations'][1]['version'] = 'other-v2'
            elif mutation == 'calendar_missing':
                del packet['facts']['trading_calendar']
            elif mutation == 'calendar_gap':
                block['sessions'].pop(1); block['observations'].pop(1)
            elif mutation == 'calendar_future':
                packet['facts']['trading_calendar']['available_at'] = '2026-10-15T00:01:00+08:00'
            elif mutation == 'calendar_incomplete':
                packet['facts']['trading_calendar']['complete'] = False
            else:
                block['observations'][1]['prev_close'] = 10.5
            with self.subTest(mutation=mutation):
                self.assert_status('FAIL', '连续3个交易日上涨。', packet)

    def test_direct_attribution_not_independent_daily_verification(self):
        packet = deepcopy(PACKET)
        packet['facts']['candidate_news'].append({'record_id': 'fixture-rise', 'source_symbols': ['600001'],
            'title': '样本股份连续3个交易日上涨', 'summary': '样本股份连续3个交易日上涨。',
            'published_at': '2026-10-13T16:00:00+08:00', 'available_at': '2026-10-13T16:01:00+08:00'})
        result = self.assert_status('PASS', 'fixture-rise原文称“样本股份连续3个交易日上涨”。这只是报道，不代表已核验逐日行情。', packet)
        self.assertEqual('ATTRIBUTED_NOT_DAILY_VERIFIED', result['observations'][0]['status'])
        self.assert_status('FAIL', '样本股份连续3个交易日上涨。', packet)
        self.assert_status('FAIL', 'news-08原文称“连续上涨”。')
        self.assert_status('FAIL', 'fixture-rise原文称“样本股份连续3个交易日上涨”证明连续3个交易日上涨。', packet)
        self.assert_status('FAIL', 'fixture-rise原文称“样本股份连续3个交易日上涨”，因此样本股份连续3个交易日上涨。', packet)
        packet['facts']['candidate_news'][-1]['available_at'] = '2026-10-15T16:01:00+08:00'
        self.assert_status('FAIL', 'fixture-rise称“样本股份连续3个交易日上涨”。', packet)

    def test_negative_news_cannot_license_affirmative_quote(self):
        packet = deepcopy(PACKET)
        row = packet['facts']['candidate_news'][0]
        row['summary'] = '样本股份（600001）不能证明连续上涨。'
        self.assertEqual([], source_semantics(packet)['reported_daily_path_statements'])
        self.assert_status('FAIL', 'news-08原文称“连续上涨”。', packet)

    def test_other_company_path_is_not_target_source_capability(self):
        packet = deepcopy(PACKET)
        row = packet['facts']['candidate_news'][0]
        row['summary'] = '样本股份（600001）公告提到另一公司连续3个交易日上涨。'
        self.assertEqual([], source_semantics(packet)['reported_daily_path_statements'])
        self.assert_status('FAIL', 'news-08原文称“样本股份连续3个交易日上涨”。', packet)
        row['summary'] = '样本股份（600001）连续3个交易日上涨。'
        self.assertTrue(source_semantics(packet)['reported_daily_path_statements'])
        self.assert_status('PASS', 'news-08原文称“样本股份连续3个交易日上涨”。', packet)

    def test_snapshot_volume_cannot_prove_continuous_or_executable_future(self):
        for sentence in ['换手字段说明当时连续成交。', '累计成交量保证成交。', '该账户可以买到。',
                         'open等于low是必然现象，是废信息。', '随后一定封板。']:
            with self.subTest(sentence=sentence):
                self.assert_status('FAIL', sentence)
        self.assert_status('PASS', '快照open9.0、low8.92。单份快照不能证明连续成交，也不能保证成交；若未来资料齐备再评估。')
        self.assert_status('PASS', 'open等于low并非必然。明天不一定上涨。')
        self.assert_status('PASS', '只有后续出现（当前包内均无）以下证据，解释才可能改变：本股自身披露、盘口深度与可成交证据、完整日K。这些均属缺失项。')
        self.assert_status('FAIL', '当前包内均无执行数据，账户可成交。')

    def test_daily_price_scope_does_not_confuse_revenue_year_or_calendar(self):
        self.assert_status('PASS', '文章称实现5年连续上涨。这只是收入历史概括。已报道连续交易日价格异动。')
        self.assert_status('PASS', '营业收入连续上涨，仍待逐年财务报表核实。')
        self.assert_status('FAIL', '股价连续上涨，营收仍待核验。')

    def test_intraday_cumulative_field_is_not_historical_return(self):
        packet = deepcopy(PACKET)
        packet['facts']['market_snapshot'] = {'status': 'observed', 'received_before_as_of': True,
            'semantics': 'one intraday cumulative quote; price is not a final daily close',
            'data': {'symbol': '600001', 'market': 'SH', 'change_pct': 5.263,
                     'received_at': '2026-10-14T09:32:46+08:00', 'ticktime': '09:32:36'}}
        self.assert_status('PASS', '该时点累计涨幅5.263%；这只是盘中快照。', packet)
        self.assert_status('FAIL', '该时点累计收益率5.263%。', packet)
        self.assert_status('FAIL', '该时点累计涨幅6.263%。', packet)
        self.assert_status('FAIL', '该时点连续上涨。', packet)
        packet['facts']['market_snapshot']['data']['received_at'] = '2026-10-15T09:32:46+08:00'
        self.assert_status('FAIL', '该时点累计涨幅5.263%。', packet)

    def test_daily_positive_cannot_be_borrowed_for_explicit_other_code_or_window(self):
        packet = history([11, 12, 12.5])
        self.assert_status('FAIL', '000811连续3个交易日上涨。', packet)
        self.assert_status('FAIL', '2026-09-01至2026-09-03连续3个交易日上涨。', packet)
        self.assert_status('PASS', '600001连续3个交易日上涨。', packet)
        self.assert_status('PASS', '2026-10-09至2026-10-13连续3个交易日上涨。', packet)

    def test_actual_local_denials_with_subject_list_and_postposed_proof(self):
        for sentence in ['不是个股累计收益率。', '不是本股累计收益率。',
                         '不能改写为个股累计收益超过20%、三日每天上涨或连续涨停。',
                         '不能改写为个股累计收益超过20%，三日每天上涨或连续涨停。',
                         '不能改写为累计收益率、连续上涨、持续上行。',
                         '| 累计相对偏离 | 不是逐日上涨证明 |']:
            with self.subTest(sentence=sentence):
                self.assert_status('PASS', sentence)
        for sentence in ['这是个股累计收益率。', '不是个股累计收益率。连续上涨后的风险仍需注意。',
                         '不能改写为累计收益率，但连续上涨是事实。',
                         '不能改写为累计收益率，连续上涨是事实。',
                         '不能改写为累计收益率，另一观点是连续上涨。',
                         '不能改写为累计收益率，连续上涨本来就存在。',
                         '不能改写为累计收益率，不能忽略连续上涨后的风险。',
                         '不是逐日上涨证明。逐日上涨后的抛压尚未证实。',
                         '不是不能改写为累计收益率。']:
            with self.subTest(sentence=sentence):
                self.assert_status('FAIL', sentence)

    def test_actual_coordinated_external_time_extrapolation_denial(self):
        self.assert_status('PASS', '也不能外推到10月15日或解释为之后持续上涨。')
        self.assert_status('PASS', '不能外推到次日或解释为之后连续上涨。')
        self.assert_status('FAIL', '也不能外推到10月15日，但之后持续上涨是事实。')
        self.assert_status('FAIL', '也不能外推到10月15日，之后持续上涨。')
        self.assert_status('FAIL', '也不能外推到10月15日。不能忽略持续上涨后的风险。')

    def test_actual_v3_report_denial_grammar_without_affirmative_pardon(self):
        accepted = [
            '不能据此说当天收涨、连续上涨或账户可成交。',
            '不表示从开盘后连续上涨，也不能据此判断未来。',
            '不能表示连续上涨。',
            '这不是连续上涨路径。',
            '不足以验证趋势与账户可成交性。',
            '材料不足以把当时快照解释成基本面转强、持续上涨。',
            '材料不足以把当时快照解释成基本面转强，持续上涨。',
            '而不是把一次快照外推成持续上涨。',
            '不说明全天路径、收盘结果、持续性或账户可成交性。',
            '若后续取得完整财报，同时有合格历史行情和执行资料支持趋势与可成交性，多头论据才可加强。',
        ]
        rejected = [
            '不能据此说当天收涨，但连续上涨是事实。',
            '连续上涨，不能据此说当天收涨。',
            '不能据此说连续上涨后的抛压。',
            '并不是不能据此说连续上涨。',
            '不是不表示连续上涨。',
            '不是不能表示连续上涨。',
            '不是不是连续上涨路径。',
            '不是不足以验证账户可成交性。',
            '不是连续上涨路径，但该股连续上涨。',
            '材料不足以把快照解释成基本面转强，但持续上涨是事实。',
            '材料不足以把快照解释成基本面转强，持续上涨本来存在。',
            '持续上涨，材料不足以把快照解释成基本面转强。',
            '并不是不足以把快照解释成持续上涨。',
            '而不是把快照外推成持续上涨，但持续上涨已成立。',
            '持续上涨，而不是把快照外推成基本面转强。',
            '不是不是把快照外推成持续上涨。',
            '不说明全天路径，但账户可成交。',
            '账户可成交，不说明全天路径。',
            '并不是不说明账户可成交。',
            '若后续取得完整财报，同时有合格历史行情和执行资料支持趋势，但当前账户可成交。',
            '若后续取得完整财报，同时有合格历史行情和执行资料支持趋势与可成交性，但账户可成交是事实。',
            '若后续取得完整财报，同时有合格历史行情和执行资料支持趋势与可成交性。账户可成交。',
            '不是若后续取得完整财报，同时有合格历史行情和执行资料支持趋势与可成交性。',
        ]
        for sentence in accepted:
            with self.subTest(sentence=sentence):
                self.assert_status('PASS', sentence)
        for sentence in rejected:
            with self.subTest(sentence=sentence):
                self.assert_status('FAIL', sentence)

    def test_aggregate_value_cannot_bind_foreign_security_or_other_window(self):
        packet = deepcopy(PACKET)
        packet['facts']['market_snapshot'] = {'status': 'observed', 'received_before_as_of': True,
            'data': {'symbol': '600001', 'market': 'SH', 'change_pct': 5.263,
                     'received_at': '2026-10-14T09:32:46+08:00'}}
        self.assert_status('PASS', '600001当前累计涨幅5.263%。', packet)
        self.assert_status('PASS', '2026-10-14当前累计涨幅5.263%。', packet)
        self.assert_status('FAIL', '000811当前累计涨幅5.263%。', packet)
        self.assert_status('FAIL', '600001.SZ当前累计涨幅5.263%。', packet)
        self.assert_status('FAIL', '2026-09-01当前累计涨幅5.263%。', packet)
        self.assert_status('PASS', '无法证明000811当前累计涨幅5.263%。', packet)
        daily = history([11, 12, 12.5])
        self.assert_status('PASS', '600001在2026-10-09至2026-10-13累计收益率25%。', daily)
        self.assert_status('FAIL', '000811累计收益率25%。', daily)
        self.assert_status('FAIL', '2026-10-12至2026-10-13累计收益率25%。', daily)
        self.assert_status('FAIL', '2026-10-12累计收益率25%。', daily)


    def test_real_local_execution_denial_with_ping_and_duanyan(self):
        # Public raw call47 wording, not a human accuracy label.
        for text in ('不能仅凭单次换手率或报价断言可成交。',
                     '也无法仅凭报价断言该账户可成交。'):
            with self.subTest(text=text):
                self.assert_status('PASS', text)
        for text in ('不是不能仅凭单次换手率或报价断言可成交。',
                     '并非无法仅凭报价断言该账户可成交。',
                     '不能仅凭报价断言可成交，但当前账户可成交。',
                     '不能仅凭报价断言可成交后的风险。'):
            with self.subTest(text=text):
                self.assert_status('FAIL', text)

    def test_real_pending_table_execution_noun_is_not_a_current_account_claim(self):
        table = ('| 主题 | 支持度 | 待核数据/指标 |\n'
                 '| --- | --- | --- |\n'
                 '| 行情快照 | 仅说明单时点报价字段 | 连续日线、盘中路径、账户可成交性 |')
        result = self.assert_status('PASS', table)
        self.assertIn('EXPLICIT_PENDING_TABLE_EXECUTION_NOUN',
                      [row['status'] for row in result['observations']])
        self.assert_status('PASS', table.replace('待核数据/指标', '主要未知'))
        for cell in ('当前账户可成交', '账户可成交性已经具备', '账户可成交性良好',
                     '实际可成交证据齐全', '账户可成交性，所以可以成交'):
            with self.subTest(cell=cell):
                self.assert_status('FAIL', table.replace('连续日线、盘中路径、账户可成交性', cell))
        self.assert_status('FAIL', table + '\n当前账户可成交。')
        self.assert_status('FAIL', table.replace('待核数据/指标', '已知结论'))
        self.assert_status('FAIL', table.replace('| --- | --- | --- |\n', ''))
        self.assert_status('FAIL', table + '\n| 另一来源 | 账户可成交 | 无 |')

    def test_session_count_after_only_binds_the_observed_trailing_window(self):
        packet = history([11, 10, 11])
        self.assert_status('PASS', '最新连续上涨仅1个会话。', packet)
        self.assert_status('FAIL', '最新连续上涨仅2个会话。', packet)
        self.assert_status('FAIL', '最新连续上涨仅1个会话。当前账户可成交。', packet)
        self.assert_status('FAIL', '最新连续上涨仅1个会话。')

    def test_not_presented_path_is_a_local_denial_not_a_positive_path(self):
        self.assert_status('PASS', '市场价格并未呈现与预告幅度一致的持续上行路径。')
        for text in ('市场价格并未呈现疲弱，但持续上行已经发生。',
                     '市场价格并非没有呈现持续上行路径。',
                     '市场价格并未呈现疲弱，而是持续上行。'):
            with self.subTest(text=text):
                self.assert_status('FAIL', text)


    def test_cumulative_display_precision_uses_round_half_up_not_tolerance(self):
        # Real regression values, carried in an isolated synthetic two-session
        # fixture. They are not a claim about the fixture dates' actual prices.
        packet = history([32.96, 35.22], first_previous=33.01)
        for text in ('该窗口累计上涨约6.69%。', '该窗口累计收益率约6.7%。',
                     '该窗口累计收益率约7%。', '该窗口累计收益率约 **+6.69%**。'):
            with self.subTest(text=text):
                self.assert_status('PASS', text, packet)
        for value in ('6.79', '6.68', '6.70', '6'):
            with self.subTest(value=value):
                self.assert_status('FAIL', '该窗口累计上涨约' + value + '%。', packet)
        negative = history([4.68, 4.21], first_previous=4.60)
        self.assert_status('PASS', '该窗口累计变化约-8.48%。', negative)
        self.assert_status('PASS', '该窗口累计变化约 **−8.48%**。', negative)
        self.assert_status('PASS', '该窗口累计约跌8.48%。', negative)
        self.assert_status('FAIL', '该窗口累计约涨8.48%。', negative)

    def test_displayed_return_rounds_half_ties_away_from_zero(self):
        positive = history([10, 10.0005], first_previous=10)
        self.assert_status('PASS', '该窗口累计收益率0.01%。', positive)
        self.assert_status('FAIL', '该窗口累计收益率0.00%。', positive)
        negative = history([10, 9.9995], first_previous=10)
        self.assert_status('PASS', '该窗口累计收益率-0.01%。', negative)
        self.assert_status('FAIL', '该窗口累计收益率-0.00%。', negative)

    def test_return_sessions_and_calendar_previous_base_are_distinct(self):
        packet = history([32.96, 35.22], first_previous=33.01)
        daily = source_semantics(packet)['daily_close_capability']
        self.assertEqual('2026-10-09', daily['base_session'])
        self.assertEqual('2026-10-12', daily['return_session_start'])
        self.assertEqual('2026-10-13', daily['return_session_end'])
        self.assertEqual('33.01', daily['first_previous_close'])
        self.assert_status('PASS', '600001从2026-10-09至2026-10-13累计上涨约6.69%。', packet)
        self.assert_status('PASS', '600001在2026-10-12至2026-10-13累计上涨约6.69%。', packet)
        self.assert_status('FAIL', '600001从2026-10-08至2026-10-13累计上涨约6.69%。', packet)
        packet['facts']['price_history']['base_session'] = '2026-10-08'
        self.assert_status('FAIL', '该窗口累计上涨约6.69%。', packet)

    def test_base_date_is_not_guessed_without_previous_calendar_member(self):
        packet = history([11, 12, 12.5, 13], first_previous=10)
        daily = source_semantics(packet)['daily_close_capability']
        self.assertIsNone(daily['base_session'])
        self.assert_status('PASS', '2026-10-08至2026-10-13累计收益率30%。', packet)
        self.assert_status('FAIL', '2026-10-07至2026-10-13累计收益率30%。', packet)

    def test_displayed_return_keeps_identity_chronology_and_calendar_gates(self):
        for mutation in ('no_source', 'foreign', 'market', 'future', 'no_calendar', 'incomplete', 'prev_basis', 'chain'):
            packet = history([32.96, 35.22], first_previous=33.01)
            if mutation == 'no_source':
                del packet['facts']['price_history']
            elif mutation == 'foreign':
                packet['facts']['price_history']['symbol'] = '000811.SZ'
            elif mutation == 'market':
                packet['facts']['price_history']['market'] = 'SZ'
            elif mutation == 'future':
                packet['facts']['price_history']['observations'][0]['available_at'] = '2026-10-15T15:02:00+08:00'
            elif mutation == 'no_calendar':
                del packet['facts']['trading_calendar']
            elif mutation == 'incomplete':
                packet['facts']['trading_calendar']['complete'] = False
            elif mutation == 'prev_basis':
                packet['facts']['price_history']['observations'][0]['prev_close_price_basis'] = 'other_adjustment'
            else:
                packet['facts']['price_history']['observations'][1]['prev_close'] = 33
            with self.subTest(mutation=mutation):
                self.assert_status('FAIL', '该窗口累计上涨约6.69%。', packet)
        packet = history([32.96, 35.22], first_previous=33.01)
        self.assert_status('FAIL', '000811累计上涨约6.69%。', packet)
        self.assert_status('FAIL', '600001.SZ累计上涨约6.69%。', packet)

    def test_non_numeric_cumulative_caution_is_scoped_not_verified_return(self):
        for text in ('反过来，窗口累计上涨也不能保证反弹。',
                     '区间累计涨幅不能抵消近期回撤。',
                     '保守方指出区间累计涨幅不能抵消近期回撤（p146-f0-s10），这个比较成立。'):
            with self.subTest(text=text):
                result = self.assert_status('PASS', text)
                self.assertIn('NON_NUMERIC_CUMULATIVE_CAUTION_NOT_RETURN_VERIFIED',
                              [row['status'] for row in result['observations']])
        for text in ('窗口已经累计上涨也不能保证反弹。',
                     '不是窗口累计上涨也不能保证反弹。',
                     '窗口累计上涨也不是不能保证反弹。',
                     '窗口累计上涨6.79%也不能保证反弹。',
                     '窗口累计上涨不能抵消回撤，但已经累计上涨6.79%。',
                     '窗口累计上涨不能抵消回撤，并非不能证明累计上涨6.79%。'):
            with self.subTest(text=text):
                self.assert_status('FAIL', text)

    def test_actual_pm_endpoint_percentage_contradiction_is_rejected(self):
        packet = history([4.68, 4.21], first_previous=4.60)
        # Actual PM138 sentence shape; -8.48% belongs to the previous base4.60,
        # not its stated4.68 endpoint. Do not correct the frozen model answer.
        wrong = '单次腾讯前复权61条日K首末收盘为4.68和4.21，累计约跌8.48%。'
        result = self.assert_status('FAIL', wrong, packet)
        self.assertIn('EXPLICIT_ENDPOINT_RETURN_VALUE_MISMATCH',
                      [row['reason'] for row in result['violations']])
        self.assert_status('PASS', wrong.replace('4.68和', '4.60和'), packet)
        endpoint = self.assert_status('PASS', wrong.replace('8.48%', '10.04%'), packet)
        self.assertEqual('2026-10-12', endpoint['observations'][0]['base_session'])
        self.assertEqual('2026-10-13', endpoint['observations'][0]['return_session_start'])
        self.assert_status('PASS', '2026-10-12至2026-10-13' + wrong.replace('8.48%', '10.04%'), packet)
        self.assert_status('FAIL', wrong.replace('4.68和', '4.69和').replace('8.48%', '10.23%'), packet)
        self.assert_status('FAIL', wrong.replace('4.21，', '4.22，').replace('8.48%', '9.83%'), packet)

    def test_added_numeric_families_do_not_promote_nouns_or_unparsed_dates(self):
        packet = history([32.96, 35.22], first_previous=33.01)
        # Abbreviated/natural dates are explicitly outside this date parser,
        # rather than a failed single-ISO-day binding or a date truth claim.
        self.assert_status('PASS', '2026-10-09至10-13，累计变动约 **+6.69%**。', packet)
        self.assert_status('PASS', '以2026-10-09为基准至10月13日，累计约+6.69%。', packet)
        self.assert_status('PASS', '从2026-10-09收盘33.01至2026-10-13收盘35.22，累计收益率6.69%。', packet)
        scoped = self.assert_status('PASS', '7-20收55.57至9-11收42.67，累计-23.21%。', packet)
        self.assertIn('BARE_CUMULATIVE_NUMBER_OUTSIDE_ENDPOINT_GRAMMAR_NOT_VERIFIED',
                      [v['status'] for v in scoped['observations']])
        self.assert_status('FAIL', '2026-10-08至2026-10-13，累计约+6.69%。', packet)
        self.assert_status('FAIL', '2026-10-08 至 2026-10-13，累计约+6.69%。', packet)
        self.assert_status('FAIL', '2026-10-08至2026-10-13及2026-10-09至10-13，累计约+6.69%。', packet)
        for text in ('| 窗口累计变化和有限指标 |', '窗口累计下跌是这里的研究描述，仍需源审。'):
            with self.subTest(text=text):
                result = self.assert_status('PASS', text)
                self.assertFalse(any(v['kind'] == 'ABSOLUTE_CUMULATIVE_RETURN' for v in result['observations']))

    def test_endpoint_arithmetic_cannot_borrow_other_identity_or_missing_source(self):
        packet = history([4.68, 4.21], first_previous=4.60)
        text = '600001首末收盘为4.60和4.21，累计约跌8.48%。'
        self.assert_status('PASS', text, packet)
        self.assert_status('FAIL', text.replace('600001', '000811'), packet)
        self.assert_status('FAIL', text.replace('600001', '600001.SZ'), packet)
        self.assert_status('FAIL', text)
        self.assert_status('FAIL', text.replace('8.48%', '8.49%'), packet)


if __name__ == '__main__':
    unittest.main(verbosity=2)
