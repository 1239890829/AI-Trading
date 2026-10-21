"""Behavioral regressions with small public-style sources; no provider calls."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/tradingagents_adapter_contract.py'
SPEC=importlib.util.spec_from_file_location('adapter_contract_test',SCRIPT)
C=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)


def packet():
    return {'ticker':'600001','trade_date':'2026-10-19','as_of':'2026-10-19T17:00:00+08:00'}


def article(record='a',symbol='600001',published='2026-10-19T09:00:00+08:00',available='2026-10-19T09:01:00+08:00'):
    return {'record_id':record,'source_symbols':[symbol],'title':'样本公司拟签约，尚待核实履约。',
            'published_at':published,'available_at':available}


def context(required=None):
    output={'content':'若合同顺利履行，才可能增加收入；现有材料尚未证明兑现。\n目前仅支持继续核查，不能据此形成交易。','tool_calls':[]}
    peer={'peer_id':'p9','role':'Bull Researcher','call_id':9,'case_id':'sample','arm':'ta-luna',
          'accepted':True,'state':'accepted','output':output,'output_sha256':C.digest(output),'visible_fields':['/content']}
    return {'case_id':'sample','arm':'ta-luna','role':'Bear Researcher',
            'required_peer_roles':['Bull Researcher'] if required is None else required,'accepted_peers':[peer]}


def refs(sid):
    return {'content':'自己的证据分析不替同伴补结论。\n<PEER_REFS>'+json.dumps({'citations':[{'segment_id':sid}]},ensure_ascii=False)+'</PEER_REFS>','tool_calls':[]}


class AdapterContractTests(unittest.TestCase):
    def test_generic_company_title_needs_bound_original_issuer_excerpt(self):
        row = {'document_id': 'notice-a', 'source_sha256': 'a' * 64,
               'source_symbols': ['600001'], 'title': '关于公司诉讼事项的公告'}
        security = {'symbol': '600001', 'name': '样本股份', 'market': 'SH'}
        self.assertFalse(C.has_bound_issuer_excerpt(row, security))
        quote = '证券代码：600001 样本股份有限公司'
        row['document_issuer'] = {'document_id': 'notice-a', 'source_sha256': 'a' * 64,
                                  'symbol': '600001', 'legal_name': '样本股份有限公司',
                                  'physical_page': 1, 'quote': quote,
                                  'quote_sha256': C.sha(quote.encode())}
        self.assertTrue(C.has_bound_issuer_excerpt(row, security))
        for change in [{'document_id': 'other'}, {'source_sha256': 'b' * 64},
                       {'symbol': '000002'}, {'physical_page': True},
                       {'quote': quote + '被改写'}, {'legal_name': '另一公司'}]:
            wrong = deepcopy(row)
            wrong['document_issuer'].update(change)
            with self.subTest(change=change):
                self.assertFalse(C.has_bound_issuer_excerpt(wrong, security))

    def test_review_body_must_bind_same_output_and_verdict(self):
        good={'binding':{'output':'a'},'verdict':'ALLOW_FALSE_POSITIVE','reason':'实际完整正文已审。',
              'reviewed_complete_original_own_output':True,
              'reviewed_actual_visible_peer_bodies_and_frozen_facts':True}
        self.assertTrue(C.verify_review_content(good,{'output':'a'},'ALLOW_FALSE_POSITIVE'))
        for patch in [{'binding':{'output':'other'}},{'verdict':'DENY_SEMANTIC_ERROR'},
                      {'reviewed_complete_original_own_output':False},
                      {'reviewed_actual_visible_peer_bodies_and_frozen_facts':False},{'reason':''}]:
            with self.subTest(patch=patch),self.assertRaises(ValueError):
                C.verify_review_content({**good,**patch},{'output':'a'},'ALLOW_FALSE_POSITIVE')

    def test_foreign_instrument_and_old_snapshot_are_not_current_facts(self):
        for args in [('999999','2026-10-19'),('600001','1999-01-01'),('600001','2026-10-20')]:
            with self.subTest(args=args):
                self.assertEqual(C.validate_tool_scope(packet(),'get_verified_market_snapshot',args)['status'],'FAIL')
        self.assertEqual(C.validate_tool_scope(packet(),'get_verified_market_snapshot',('600001','2026-10-19'))['status'],'PASS')

    def test_news_range_validation_and_unknown_arguments(self):
        for args in [('999999','2026-10-01','2026-10-19'),('600001','2026-10-20','2026-10-19'),
                     ('600001','2026-10-01','2026-10-20'),('600001','bad','2026-10-19')]:
            with self.subTest(args=args):self.assertEqual(C.validate_tool_scope(packet(),'get_news',args)['status'],'FAIL')
        self.assertEqual(C.validate_tool_scope(packet(),'get_news',('600001','2026-10-01','2026-10-19'),{'ticker':'600001'})['status'],'FAIL')
        self.assertEqual(C.validate_tool_scope(packet(),'unknown',())['status'],'FAIL')

    def test_current_financial_date_and_range_rows(self):
        for method,args in [('get_fundamentals',('600001','2026-10-19')),
                            ('get_balance_sheet',('600001','quarterly',None)),
                            ('get_insider_transactions',('600001','2026-10-19'))]:
            with self.subTest(method=method):self.assertEqual(C.validate_tool_scope(packet(),method,args)['status'],'PASS')
        self.assertEqual(C.validate_tool_scope(packet(),'get_fundamentals',('600001','2025-01-01'))['status'],'FAIL')
        scope=C.validate_tool_scope(packet(),'get_stock_data',('600001','2026-10-18','2026-10-19'))['scope']
        rows=[{'Date':'2026-10-17','Close':1},{'Date':'2026-10-19','Close':2}]
        out=C.project_tool_value(packet(),'get_stock_data',rows,scope)
        self.assertEqual(out['rows'],[rows[1]])
        self.assertFalse(out['complete_history'])
        with self.assertRaises(ValueError):C.project_tool_value(packet(),'get_stock_data','opaque table',scope)

    def test_news_dual_clock_subject_and_requested_range(self):
        p=packet();scope=C.validate_tool_scope(p,'get_news',('600001','2026-10-19','2026-10-19'))['scope']
        rows=[article(),article('suffix','600001.SH'),article('foreign','000002'),
              article('late',available='2026-10-19T18:00:00+08:00'),
              article('older',published='2026-10-18T09:00:00+08:00'),article('naive',available='2026-10-19T10:00:00')]
        before=deepcopy(rows);out=C.project_tool_value(p,'get_news',rows,scope)
        self.assertEqual([r['record_id'] for r in out['articles']],['a','suffix'])
        self.assertEqual(rows,before)
        self.assertTrue(out['empty_subset_does_not_prove_no_news'])
        empty=C.project_tool_value(p,'get_news',[article('foreign','000002')],scope)
        self.assertEqual(empty['articles'],[])

    def test_only_required_predecessors_are_indexed(self):
        c=context();extra=deepcopy(c['accepted_peers'][0]);extra.update(peer_id='p2',call_id=2,role='News Analyst')
        c['accepted_peers'].append(extra);idx=C.build_predecessor_index(c)
        self.assertEqual({e['role'] for e in idx['entries']},{'Bull Researcher'})
        first=context([]);first['role']='Bull Researcher'
        self.assertEqual(C.build_predecessor_index(first)['entries'],[])
        self.assertEqual(C.evaluate_predecessor_refs({'content':'从原始事实独立论证。'},first)['status'],'PASS')

    def test_exact_sentence_reference_resolves_without_model_hash_or_quote(self):
        c=context();idx=C.build_predecessor_index(c);entry=idx['entries'][0]
        self.assertIn('若合同顺利履行',entry['quote']);self.assertIn('尚未证明兑现',entry['quote'])
        out=C.evaluate_predecessor_refs(refs(entry['segment_id']),c)
        self.assertEqual(out['status'],'REQUIRES_PEER_REVIEW');self.assertTrue(out['review_required'])
        resolved=out['resolved_citations'][0];text=c['accepted_peers'][0]['output']['content']
        self.assertEqual(text[resolved['start']:resolved['end']],resolved['quote'])
        self.assertEqual(C.sha(resolved['quote'].encode()),resolved['quote_sha256'])
        self.assertNotIn('PEER_EVIDENCE_V1',C.PEER_POLICY)

    def test_actual_exact_ids_and_multiple_valid_blocks_keep_same_identity_gate(self):
        c = context()
        sid = C.build_predecessor_index(c)['entries'][0]['segment_id']
        for text in ['据同伴原句 `'+sid+'`，该条件尚未兑现。',
                     refs(sid)['content'] + '\n' + refs(sid)['content']]:
            with self.subTest(text=text):
                out = C.evaluate_predecessor_refs({'content': text}, c)
                self.assertEqual(out['status'], 'REQUIRES_PEER_REVIEW')
                self.assertEqual(len(out['resolved_citations']), 1)

    def test_bad_block_or_unknown_id_cannot_fall_back_to_other_valid_id(self):
        c = context()
        good = refs('p9-f0-s0')['content']
        for text in [good+'\n<PEER_REFS>{}</PEER_REFS>', good+'\np999-f0-s0',
                     'xp9-f0-s0evil', 'https://example.test/p9-f0-s0',
                     good+'\n<PEER_REFS>']:
            with self.subTest(text=text):
                self.assertEqual(C.evaluate_predecessor_refs({'content': text}, c)['status'], 'FAIL')
        second = deepcopy(c['accepted_peers'][0])
        second.update(peer_id='p10', call_id=10, role='Bear Researcher')
        c['accepted_peers'].append(second)
        c['required_peer_roles'].append('Bear Researcher')
        self.assertEqual(C.evaluate_predecessor_refs({'content': good+'\n`p9-f0-s1`'}, c)['status'], 'FAIL')

    def test_bare_reference_carrier_is_not_new_peer_evidence(self):
        c = context()
        c['accepted_peers'][0]['output']['content'] += '\n`p1-f0-s2`'
        c['accepted_peers'][0]['output_sha256'] = C.digest(c['accepted_peers'][0]['output'])
        self.assertNotIn('`p1-f0-s2`', [row['quote'] for row in C.build_predecessor_index(c)['entries']])

    def test_unknown_id_not_repaired_and_missing_required_not_pass(self):
        c=context()
        for value in [refs('invented'),{'content':'没有引用块但有自己的分析。'},
                      {'content':'<PEER_REFS>{"citations":[]}</PEER_REFS>'},
                      {'content':'<PEER_REFS>{"citations":[{"segment_id":"p9-f0-s0","quote":"删掉条件"}]}</PEER_REFS>'}]:
            with self.subTest(value=value):self.assertEqual(C.evaluate_predecessor_refs(value,c)['status'],'FAIL')
        for tag in ['<PEER_REFS>','</PEER_REFS>']:
            value=refs('p9-f0-s0');value['content']+=tag
            self.assertEqual(C.evaluate_predecessor_refs(value,c)['status'],'FAIL')

    def test_wrong_case_arm_hash_visibility_or_proposal_is_hard_context_error(self):
        patches=[{'case_id':'foreign'},{'arm':'other'},{'output_sha256':'wrong'},
                 {'visible_fields':['/missing']},{'accepted':False},{'state':'proposed'}]
        for patch in patches:
            c=context();c['accepted_peers'][0].update(patch)
            with self.subTest(patch=patch):
                self.assertEqual(C.evaluate_predecessor_refs(refs('p9-f0-s0'),c)['status'],'FAIL')
        c=context();c['accepted_peers'][0]['output']['tool_calls']=[{'name':'get_news'}]
        c['accepted_peers'][0]['output_sha256']=C.digest(c['accepted_peers'][0]['output'])
        self.assertEqual(C.evaluate_predecessor_refs(refs('p9-f0-s0'),c)['status'],'FAIL')
        c=context();c['accepted_peers']=[]
        with self.assertRaises(C.PeerContextError):C.build_predecessor_contract(c)

    def test_valid_quote_does_not_automatically_make_criticism_fair(self):
        c=context();value=refs('p9-f0-s0');value['content']+='\n多头已预设合同必然兑现。'
        self.assertEqual(C.evaluate_predecessor_refs(value,c)['status'],'REQUIRES_PEER_REVIEW')
        first=context([]);first['role']='Bull Researcher'
        self.assertEqual(C.evaluate_predecessor_refs({'content':'对方主张必然兑现。'},first)['status'],'REQUIRES_PEER_REVIEW')


if __name__=='__main__':unittest.main()
