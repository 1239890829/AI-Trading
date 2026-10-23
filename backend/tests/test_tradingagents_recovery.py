"""Hermetic native continuation contracts, never model quality evidence."""
import asyncio
from copy import deepcopy
from datetime import datetime,timedelta
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.events.company_state import atomic_json
from app.models.agent import AgentResourceUsage,AgentTask
from app.models.watchlist import Base
from app.services import agent_budget,agent_tasks
from app.services import tradingagents_driver as driver,tradingagents_research as research,tradingagents_recovery as recovery

NOW=datetime.fromisoformat('2026-10-21T16:00:00+08:00')
OLD,NEW,RUN,GRANT=('a'*32,'b'*32,'c'*32,'d'*32)
SYMBOL='002264'

def put(path,value):
    path.parent.mkdir(parents=True,exist_ok=True); atomic_json(path,value)

def lineage():
    return dict(version=1,source_task_id=OLD,source_run_id=RUN,recovery_task_id=NEW,source_method_sha256='a'*64,
        recovery_method_sha256='b'*64,reused_original_outputs=1,new_call_from=3,new_call_to=13,inherited_unknown_usage_count=1)

def test_public_lineage_only_bounded_truth(): assert recovery.public_lineage(lineage())==lineage()

@pytest.mark.parametrize('key,value',[('version',True),('new_call_from',0),('new_call_to',2),('reused_original_outputs',-1),
    ('inherited_unknown_usage_count',10001),('source_task_id','../bad'),('source_run_id','C'*32),('recovery_method_sha256','x'*64)])
def test_invalid_lineage(key,value):
    value={**lineage(),key:value}
    with pytest.raises(research.ResearchBlocked): recovery.public_lineage(value)

class Recorder:
    task_id=NEW
    async def checkpoint_async(self): pass

@pytest.fixture
def env(tmp_path,monkeypatch):
    engine=create_engine(f"sqlite:///{tmp_path/'isolated.db'}",connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine); sf=sessionmaker(bind=engine)
    monkeypatch.setattr(research,'ROOT',tmp_path/'research'); monkeypatch.setattr(research,'_now',lambda:NOW)
    for module in (research,agent_tasks,agent_budget): monkeypatch.setattr(module,'get_session_factory',lambda:sf)
    monkeypatch.setattr(agent_budget,'beijing_now',lambda:NOW); monkeypatch.setattr(agent_budget,'beijing_now_naive',lambda:NOW.replace(tzinfo=None))
    monkeypatch.setattr(settings,'tradingagents_research_enabled',True)
    monkeypatch.setattr(settings,'tradingagents_research_authorization_digest','')
    old=dict(upstream_sha=research.UPSTREAM_SHA,native_full_graph=True,transport=dict(provider='codex_cli',model='gpt-6.1-sol',effort='medium'),
        source_files={'driver.py':'1'*64,'codex_transport.py':'2'*64,'worker.py':'3'*64},dependencies={'frozen':'4'*64},packet_builder={'frozen':'5'*64})
    current=deepcopy(old); current['source_files'].update({'driver.py':'6'*64,'codex_transport.py':'7'*64,'tradingagents_recovery.py':'8'*64})
    class Native:
        calls=0; fail=None; first_role='Bear Researcher'; revoke=False; resume_counter=2
        def method_identity(self): return deepcopy(current)
        async def run_accounted(self,**kwargs): raise AssertionError('fresh run forbidden')
        def _review_summary(self,rows): return dict(finite_failures=0,peer_review_required=len(rows),source_verified=False,independent_human_gold=False)
        async def _execute(self,*,rec,packet,run,recovery_guard,usage_authorizer,**kwargs):
            self.calls+=1
            if self.fail: raise self.fail
            if self.revoke: monkeypatch.setattr(settings,'tradingagents_research_authorization_digest','f'*64)
            recovery_guard({'role':self.first_role},self.resume_counter); usage_authorizer(); n=self.resume_counter
            completed={json.loads(row)['peer']['role'] for row in (run/'role-outputs.jsonl').read_text().splitlines() if row.strip()}
            for role in [self.first_role]+[r for r in research.ROLES if r not in completed|{self.first_role}]:
                n+=1; output={'content':role+' engineering continuation','tool_calls':[]}
                put(run/'model-outputs'/f'{n}.json',dict(structured_output=output,call_id=n,role=role,output_sha256=research._digest(output)))
                put(run/'usage-receipts'/f'{n}.json',dict(task_id=rec.task_id,method_sha256=research._digest(current),call_id=n,
                    usage_id=f'{n:032x}',role=role,output_sha256=research._digest(output),usage_known=True,usage={'input_tokens':1,'output_tokens':1}))
                with (run/'role-outputs.jsonl').open('a') as h: h.write(json.dumps({'peer':dict(role=role,call_id=n,output_sha256=research._digest(output))})+'\n')
            with (run/'graph/node-trace.jsonl').open('a') as h: h.write(json.dumps({'node':'Memory Log','event':'end'})+'\n')
            put(run/'call-counter.json',{'reserved':n})
            return {key:'complete fixture continuation' for key in research.REPORTS}
    native=Native(); app=SimpleNamespace(state=SimpleNamespace(tradingagents_research_driver=native))
    run=research.ROOT/SYMBOL/RUN; run.mkdir(parents=True)
    packet=dict(ticker=SYMBOL,case_id=SYMBOL,trade_date=NOW.date().isoformat(),as_of=(NOW-timedelta(minutes=20)).isoformat())
    put(run/'packet.json',packet); put(run/'config.json',{'approved_model_identity':current['transport']})
    put(run/'staged-code.json',driver._stage_worker(run/'code'))
    signature='frozen-settings'; thread=hashlib.sha256((SYMBOL+':'+packet['trade_date']+':'+signature).encode()).hexdigest()[:16]
    put(run/'graph/run-manifest.json',dict(config_signature=signature,interrupted_by_request=False))
    cp=run/'graph/cache/checkpoints'/f'{SYMBOL}.db'; cp.parent.mkdir(parents=True)
    with sqlite3.connect(cp) as db:
        db.execute('CREATE TABLE checkpoints(thread_id TEXT,checkpoint_ns TEXT,checkpoint_id TEXT,parent_checkpoint_id TEXT,type TEXT,checkpoint BLOB,metadata BLOB)')
        db.execute('CREATE TABLE writes(thread_id TEXT,checkpoint_ns TEXT,checkpoint_id TEXT,task_id TEXT,task_path TEXT,idx INTEGER,channel TEXT,type TEXT,value BLOB)')
        db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)',(thread,'','checkpoint-1',None,'msgpack',b'opaque-checkpoint',json.dumps({'step':2})))
    put(run/'worker-terminal.json',{'exit_code':1}); put(run/'execution.json',{'state':'needs_native_recovery'}); put(run/'call-counter.json',{'reserved':2})
    output={'content':'original fixture answer','tool_calls':[]}; put(run/'model-outputs/1.json',{'structured_output':output})
    method=research._digest(old)
    for n in (1,2): put(run/'usage-receipts'/f'{n}.json',dict(task_id=OLD,method_sha256=method,call_id=n,usage_id=f'{n:032x}',usage_known=n==1,usage={'input_tokens':1,'output_tokens':1} if n==1 else None))
    (run/'role-outputs.jsonl').write_text(json.dumps({'peer':dict(role='Bull Researcher',call_id=1,output_sha256=research._digest(output))})+'\n')
    (run/'source-audit.jsonl').write_text('{}\n'); (run/'graph/node-trace.jsonl').write_text('{}\n')
    selection=dict(symbol=SYMBOL,trade_date=NOW.date().isoformat(),selected_at=(NOW-timedelta(minutes=30)).isoformat(),source='daily_generation',source_version='original-entry')
    selection['selection_key']=research._digest(selection); claim=research._selection_claim(selection,method)
    put(claim,dict(version=1,selection=selection,method_sha256=method,state='needs_recovery',task_id=OLD,updated_at=(NOW-timedelta(minutes=10)).isoformat()))
    with sf() as db:
        db.add(AgentTask(id=OLD,type='tradingagents_research',status='failed',params=json.dumps({'symbol':SYMBOL}),finished_at=NOW.replace(tzinfo=None)))
        db.add(AgentResourceUsage(id=f'{2:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,kind='model',purpose='original',task_id=OLD,state='failed',usage_known=0,started_at=(NOW-timedelta(minutes=15)).replace(tzinfo=None))); db.commit()
    binding=dict(selection=selection,source_claim_sha256=recovery._sha(claim),packet_sha256=recovery._sha(run/'packet.json'),config_sha256=recovery._sha(run/'config.json'),
        staged_code_sha256=recovery._sha(run/'staged-code.json'),manifest_sha256=recovery._sha(run/'graph/run-manifest.json'),worker_terminal_sha256=recovery._sha(run/'worker-terminal.json'),
        checkpoint=recovery.checkpoint_snapshot(run,SYMBOL),call_counter=2,outputs={'1':recovery._sha(run/'model-outputs/1.json')},
        usage_receipts={str(n):recovery._sha(run/'usage-receipts'/f'{n}.json') for n in (1,2)},prefixes={name:dict(bytes=len((run/name).read_bytes()),sha256=recovery._sha(run/name)) for name in ('role-outputs.jsonl','source-audit.jsonl','graph/node-trace.jsonl')},
        completed_roles=[dict(role='Bull Researcher',call_id=1,output_sha256=research._digest(output))],expected_pending_role='Bear Researcher')
    pairs={k:dict(old=old['source_files'].get(k),new=current['source_files'].get(k)) for k in old['source_files'].keys()|current['source_files'].keys() if old['source_files'].get(k)!=current['source_files'].get(k)}
    grant=dict(version=1,grant_id=GRANT,user_authorization='EXPLICIT',authorized_at=(NOW-timedelta(minutes=1)).isoformat(),valid_until=(NOW+timedelta(hours=1)).isoformat(),source_task_id=OLD,symbol=SYMBOL,run_id=RUN,
        source_method_identity=old,continuation_method_identity=current,compatibility=dict(kind='reviewed_host_diagnostics_and_recovery_v1',source_hash_pairs=pairs),binding=binding)
    auth=dict(version=1,mode='RESEARCH_ONLY_AUTHORIZED',consumer='selected_stock_explanation',user_authorization='EXPLICIT',net_gain_verified=False,production_eligible=False,method_identity=current,created_at=(NOW-timedelta(minutes=2)).isoformat(),valid_until=(NOW+timedelta(hours=2)).isoformat(),
        unknown_usage_exception=dict(version=1,user_authorization='EXPLICIT',reason='named fixture continuation',authorized_at=(NOW-timedelta(minutes=1)).isoformat(),method_sha256=research._digest(current),allowed_unknown_usage=[dict(usage_id=f'{2:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM)]),native_recovery_grants=[grant])
    def pin():
        put(research.ROOT/'authorization.json',auth); monkeypatch.setattr(settings,'tradingagents_research_authorization_digest',recovery._sha(research.ROOT/'authorization.json'))
    pin(); yield SimpleNamespace(sf=sf,app=app,native=native,run=run,selection=selection,source_claim=claim,grant=grant,current=current,auth=auth,pin=pin,rec=Recorder())
    engine.dispose()

def context(e): return recovery._context(OLD,RUN,e.app,e.sf)
def consumed(e):
    c=context(e); claim,fresh=recovery._dispatch(c); assert fresh
    owner=claim['native_recovery']['dispatch_owner']; recovery._advance(c,owner,'recovery_queued',task_id=NEW)
    return c,owner

def params(c,owner): return dict(symbol=SYMBOL,native_recovery=dict(source_task_id=OLD,run_id=RUN,grant_sha256=c['grant_sha256'],dispatch_owner=owner))

PARALLEL_ROLES=('Market Analyst','Sentiment Analyst','News Analyst','Fundamentals Analyst')

def parallel_env(e):
    """Four failed native subgraphs; two paid tool proposals already checkpointed."""
    b=e.grant['binding']; b.pop('expected_pending_role'); b['completed_roles']=[]
    b['expected_pending_roles']=list(PARALLEL_ROLES)
    e.messages={role:[dict(role='user',content='frozen initial message')] for role in PARALLEL_ROLES}
    for n,role in enumerate(('Fundamentals Analyst','Market Analyst'),1):
        output=dict(content=f'original {role} tool proposal',tool_calls=[dict(id=f'original{n}',name='get_news',arguments={'symbol':SYMBOL})])
        put(e.run/'model-outputs'/f'{n}.json',dict(call_id=n,role=role,structured_output=output,output_sha256=research._digest(output)))
        put(e.run/'usage-receipts'/f'{n}.json',dict(task_id=OLD,method_sha256=research._digest(e.grant['source_method_identity']),call_id=n,
            role=role,usage_id=f'{n:032x}',usage_known=True,usage={'input_tokens':1,'output_tokens':1}))
        e.messages[role]+=[dict(role='assistant',content=output['content'],tool_calls=[dict(name='get_news',args={'symbol':SYMBOL},id=f'mapped{n}',type='tool_call')]),
            dict(role='tool',content=f'frozen result {n}',name='get_news',status='success',tool_call_id=f'mapped{n}')]
    put(e.run/'usage-receipts/3.json',dict(task_id=OLD,method_sha256=research._digest(e.grant['source_method_identity']),call_id=3,usage_id=f'{3:032x}',usage_known=False,usage=None))
    with e.sf() as db:
        db.get(AgentResourceUsage,f'{2:032x}').usage_known=1
        db.add(AgentResourceUsage(id=f'{3:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
            kind='model',purpose='fixture.unknown',task_id=OLD,state='failed',usage_known=0,started_at=(NOW-timedelta(minutes=15)).replace(tzinfo=None)));db.commit()
    e.auth['unknown_usage_exception']['allowed_unknown_usage'][0]['usage_id']=f'{3:032x}'
    (e.run/'role-outputs.jsonl').unlink();put(e.run/'call-counter.json',{'reserved':3})
    b['call_counter']=3;b['outputs']={str(n):recovery._sha(e.run/'model-outputs'/f'{n}.json') for n in (1,2)}
    b['usage_receipts']={str(n):recovery._sha(e.run/'usage-receipts'/f'{n}.json') for n in (1,2,3)}
    b['prefixes']['role-outputs.jsonl']=dict(bytes=0,sha256=hashlib.sha256(b'').hexdigest())
    b['pending_message_fingerprints']={role:[research._digest(row) for row in rows] for role,rows in e.messages.items()}
    cp=e.run/'graph/cache/checkpoints'/f'{SYMBOL}.db'
    with sqlite3.connect(cp) as db:
        thread=db.execute('SELECT thread_id FROM checkpoints').fetchone()[0]
        db.execute('UPDATE checkpoints SET metadata=?',(json.dumps({'step':0}),))
        for i,role in enumerate(PARALLEL_ROLES):
            ns=role+':'+str(i)*8+'-1111-1111-1111-111111111111'; cid=f'child-{i}'
            db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)',(thread,ns,cid,None,'msgpack',b'opaque native messages',json.dumps({'step':2 if len(e.messages[role])>1 else 0,'parents':{'':'checkpoint-1'}})))
            db.execute('INSERT INTO writes VALUES(?,?,?,?,?,?,?,?,?)',(thread,ns,cid,f'task-{i}','~__pregel_pull, agent',-1,'__error__','msgpack',b'failed'))
            db.execute('INSERT INTO writes VALUES(?,?,?,?,?,?,?,?,?)',(thread,'','checkpoint-1',f'outer-{i}','~__pregel_pull, '+role,-1,'__error__','msgpack',b'failed'))
    b['checkpoint']=recovery.checkpoint_snapshot(e.run,SYMBOL);e.pin();return e

def test_parallel_zero_final_answers_are_valid_without_creating_role_stream(env):
    e=parallel_env(env);assert context(e)['binding']['expected_pending_roles']==list(PARALLEL_ROLES)
    assert not (e.run/'role-outputs.jsonl').exists()


def first_failed_parallel_env(e):
    """Initial native fan-out failed on call one before saving any answer."""
    e=parallel_env(e);b=e.grant['binding']
    for folder in ('model-outputs','usage-receipts'):
        for path in (e.run/folder).iterdir():path.unlink()
    (e.run/'model-outputs').rmdir();(e.run/'source-audit.jsonl').unlink()
    receipt=dict(task_id=OLD,method_sha256=research._digest(e.grant['source_method_identity']),call_id=1,
        role='Fundamentals Analyst',usage_id=f'{1:032x}',usage_known=False,usage=None,output_sha256=None,
        terminal_kind='unknown_transport_failure',transport_diagnostic=dict(kind='codex_typed_terminal_failure',
            failure_stage='terminal',exit_code=1,turn_count=0,turn_started_count=1,final_file_present=False,
            refusal_present=False,event_order_complete=True,explicit_error_codes=[],unknown_error_codes=[],
            event_order=[dict(event_type='thread.started',item_type='none'),
                dict(event_type='turn.started',item_type='none'),dict(event_type='error',item_type='none'),
                dict(event_type='turn.failed',item_type='none')]))
    put(e.run/'usage-receipts/1.json',receipt)
    with e.sf() as db:
        db.query(AgentResourceUsage).delete()
        db.add(AgentResourceUsage(id=f'{1:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
            kind='model',purpose='tradingagents.fundamentals_analyst',task_id=OLD,state='failed',usage_known=0,
            started_at=(NOW-timedelta(minutes=15)).replace(tzinfo=None)));db.commit()
    e.auth['unknown_usage_exception']['allowed_unknown_usage'][0]['usage_id']=f'{1:032x}'
    e.messages={role:[dict(role='user',content='frozen initial message')] for role in PARALLEL_ROLES}
    b.update(call_counter=1,outputs={},usage_receipts={'1':recovery._sha(e.run/'usage-receipts/1.json')},
        pending_message_fingerprints={role:[research._digest(row) for row in rows] for role,rows in e.messages.items()})
    b['prefixes']['source-audit.jsonl']=dict(bytes=0,sha256=hashlib.sha256(b'').hexdigest())
    put(e.run/'call-counter.json',{'reserved':1})
    cp=e.run/'graph/cache/checkpoints'/f'{SYMBOL}.db'
    with sqlite3.connect(cp) as db:
        for ns,metadata in db.execute('SELECT checkpoint_ns,metadata FROM checkpoints').fetchall():
            meta=json.loads(metadata);meta['step']=0
            db.execute('UPDATE checkpoints SET metadata=? WHERE checkpoint_ns=?',(json.dumps(meta),ns))
    b['checkpoint']=recovery.checkpoint_snapshot(e.run,SYMBOL);e.pin();return e


def test_first_parallel_unknown_checkpoint_has_no_original_answer_or_placeholder(env):
    e=first_failed_parallel_env(env);receipt=(e.run/'usage-receipts/1.json').read_bytes();c=context(e)
    assert c['binding']['outputs']=={} and c['binding']['call_counter']==1
    assert not (e.run/'model-outputs').exists() and not (e.run/'role-outputs.jsonl').exists()
    assert not (e.run/'source-audit.jsonl').exists() and (e.run/'usage-receipts/1.json').read_bytes()==receipt
    guard=recovery._pending_guard(c['binding'])
    guard(dict(role='Fundamentals Analyst',messages=e.messages['Fundamentals Analyst']),1)
    assert e.native.calls==0 and json.loads((e.run/'call-counter.json').read_text())=={'reserved':1}


@pytest.mark.parametrize('mutation',['single_role','step','counter','missing_receipt','not_named_previous_day',
    'known_usage','wrong_task','extra_original_model','wrong_method','typed_success','not_failed_turn','unverified_order','explicit_stop',
    'saved_answer_omitted','undeclared_future_answer','symlink_directory','pending_error_missing','message_drift'])
def test_first_parallel_unknown_checkpoint_does_not_waive_original_guards(env,mutation):
    e=first_failed_parallel_env(env);b=e.grant['binding'];path=e.run/'usage-receipts/1.json';receipt=json.loads(path.read_text())
    if mutation=='single_role':
        b.pop('expected_pending_roles');b.pop('pending_message_fingerprints');b['expected_pending_role']='Fundamentals Analyst'
    elif mutation=='step':b['checkpoint']['step']=2
    elif mutation=='counter':b['call_counter']=2;put(e.run/'call-counter.json',{'reserved':2})
    elif mutation=='missing_receipt':path.unlink()
    elif mutation=='not_named_previous_day':
        e.auth.pop('unknown_usage_exception')
        with e.sf() as db:
            row=db.get(AgentResourceUsage,f'{1:032x}');row.budget_date=(NOW-timedelta(days=1)).date().isoformat()
            row.started_at=(NOW-timedelta(days=1)).replace(tzinfo=None);db.commit()
    elif mutation=='known_usage':
        with e.sf() as db:db.get(AgentResourceUsage,f'{1:032x}').usage_known=1;db.commit()
    elif mutation=='wrong_task':
        with e.sf() as db:db.get(AgentResourceUsage,f'{1:032x}').task_id='f'*32;db.commit()
    elif mutation=='extra_original_model':
        with e.sf() as db:
            db.add(AgentResourceUsage(id='f'*32,budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
                kind='model',purpose='omitted original',task_id=OLD,state='succeeded',usage_known=1,
                input_tokens=1,output_tokens=1,started_at=(NOW-timedelta(minutes=15)).replace(tzinfo=None)));db.commit()
    elif mutation=='wrong_method':receipt['method_sha256']='f'*64
    elif mutation=='typed_success':receipt.update(terminal_kind='typed_response',output_sha256='a'*64)
    elif mutation=='not_failed_turn':receipt['transport_diagnostic']['exit_code']=0
    elif mutation=='unverified_order':receipt['transport_diagnostic']['event_order_complete']=False
    elif mutation=='explicit_stop':receipt['transport_diagnostic']['explicit_error_codes']=['authentication_failed']
    elif mutation in ('saved_answer_omitted','undeclared_future_answer'):
        put(e.run/'model-outputs'/('1.json' if mutation=='saved_answer_omitted' else '2.json'),{'structured_output':{'content':'unbound'}})
    elif mutation=='symlink_directory':(e.run/'model-outputs').symlink_to(e.run/'usage-receipts',target_is_directory=True)
    elif mutation=='pending_error_missing':
        cp=e.run/'graph/cache/checkpoints'/f'{SYMBOL}.db'
        with sqlite3.connect(cp) as db:db.execute("DELETE FROM writes WHERE task_path='~__pregel_pull, News Analyst'")
        b['checkpoint']=recovery.checkpoint_snapshot(e.run,SYMBOL)
    if path.exists() and mutation in ('wrong_method','typed_success','not_failed_turn','unverified_order','explicit_stop'):
        put(path,receipt);b['usage_receipts']['1']=recovery._sha(path)
    e.pin()
    if mutation=='message_drift':
        c=context(e);guard=recovery._pending_guard(c['binding'])
        with pytest.raises(research.ResearchBlocked):guard(dict(role='News Analyst',messages=[dict(role='user',content='changed')]),1)
    else:
        with pytest.raises(research.ResearchBlocked):context(e)
    assert e.native.calls==0


def test_first_parallel_unknown_allows_only_post_counter_outputs_after_dispatch(env):
    e=first_failed_parallel_env(env);context(e)
    put(e.run/'model-outputs/2.json',{'structured_output':{'content':'new continuation'}})
    with pytest.raises(research.ResearchBlocked):context(e)
    assert recovery._context(OLD,RUN,e.app,e.sf,initial=False)['binding']['outputs']=={}
    put(e.run/'model-outputs/1.json',{'structured_output':{'content':'omitted original'}})
    with pytest.raises(research.ResearchBlocked):recovery._context(OLD,RUN,e.app,e.sf,initial=False)


def offline_wal_first_failure(e):
    e=first_failed_parallel_env(e);cp=e.run/'graph/cache/checkpoints'/f'{SYMBOL}.db'
    with sqlite3.connect(cp) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    assert not any(cp.with_name(cp.name+suffix).exists() for suffix in ('-wal','-shm'))
    # Changing the journal header does not change logical checkpoint rows.
    e.grant['binding']['checkpoint']['files']={'db':recovery._sha(cp),'-wal':None,'-shm':None}
    e.pin();return e


def test_offline_first_failure_wal_reads_leave_all_original_files_absent_or_unchanged(env):
    e=offline_wal_first_failure(env)
    def files():return {p.relative_to(e.run).as_posix():p.read_bytes() for p in e.run.rglob('*') if p.is_file()}
    before=files();c=context(e)
    assert c['binding']['checkpoint']['step']==0 and files()==before
    assert not (e.run/'model-outputs').exists() and e.native.calls==0


@pytest.mark.parametrize('mutation',['active_source','canceled_source','worker_drift','worker_not_terminal','db_drift',
    'unexpected_sidecar','missing_files','files_none','files_list'])
def test_offline_first_failure_wal_snapshot_requires_exact_inactive_binding(env,monkeypatch,mutation):
    e=offline_wal_first_failure(env);cp=e.run/'graph/cache/checkpoints'/f'{SYMBOL}.db'
    if mutation=='active_source':
        with e.sf() as db:db.get(AgentTask,OLD).status='running';db.commit()
    elif mutation=='canceled_source':
        with e.sf() as db:db.get(AgentTask,OLD).status='canceled';db.commit()
        put(e.run/'execution.json',{'state':'canceled'})
    elif mutation=='worker_drift':put(e.run/'worker-terminal.json',{'exit_code':2})
    elif mutation=='worker_not_terminal':
        put(e.run/'worker-terminal.json',{'exit_code':0});e.grant['binding']['worker_terminal_sha256']=recovery._sha(e.run/'worker-terminal.json');e.pin()
    elif mutation=='db_drift':e.grant['binding']['checkpoint']['files']['db']='f'*64;e.pin()
    elif mutation=='missing_files':e.grant['binding']['checkpoint'].pop('files');e.pin()
    elif mutation in ('files_none','files_list'):
        e.grant['binding']['checkpoint']['files']=None if mutation=='files_none' else [];e.pin()
    else:cp.with_name(cp.name+'-wal').write_bytes(b'not a bound WAL')
    connect=sqlite3.connect;checkpoint_uris=[]
    def observe(database,*args,**kwargs):
        if str(database).startswith('file:') and '/checkpoints/' in str(database):checkpoint_uris.append(str(database))
        return connect(database,*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',observe)
    before={p.relative_to(e.run).as_posix():p.read_bytes() for p in e.run.rglob('*') if p.is_file()}
    with pytest.raises(research.ResearchBlocked):context(e)
    assert before=={p.relative_to(e.run).as_posix():p.read_bytes() for p in e.run.rglob('*') if p.is_file()} and e.native.calls==0
    assert checkpoint_uris==[]


def test_offline_first_failure_rechecks_bound_worker_after_query(env,monkeypatch):
    e=offline_wal_first_failure(env);connect=sqlite3.connect;changed=[]
    class ChangeAfterQuery:
        def __init__(self,connection):self.connection=connection
        def execute(self,statement,*args,**kwargs):
            result=self.connection.execute(statement,*args,**kwargs)
            if statement.startswith('SELECT') and not changed:
                put(e.run/'worker-terminal.json',{'exit_code':2});changed.append(True)
            return result
        def close(self):self.connection.close()
    def observe(database,*args,**kwargs):
        connection=connect(database,*args,**kwargs)
        return ChangeAfterQuery(connection) if str(database).startswith('file:') and '/checkpoints/' in str(database) else connection
    monkeypatch.setattr(sqlite3,'connect',observe)
    with pytest.raises(research.ResearchBlocked) as error:context(e)
    assert error.value.code=='recovery_offline_checkpoint_drift' and changed==[True] and e.native.calls==0


def test_unbound_checkpoint_snapshot_reads_current_wal_without_immutable(env,monkeypatch):
    cp=env.run/'graph/cache/checkpoints'/f'{SYMBOL}.db';connect=sqlite3.connect;uris=[]
    with connect(cp) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        thread=writer.execute('SELECT thread_id FROM checkpoints').fetchone()[0]
        writer.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)',(thread,'','checkpoint-2',None,'msgpack',b'current WAL',json.dumps({'step':3})));writer.commit()
        def observe(database,*args,**kwargs):
            uris.append(str(database));return connect(database,*args,**kwargs)
        monkeypatch.setattr(sqlite3,'connect',observe)
        snapshot=recovery.checkpoint_snapshot(env.run,SYMBOL)
        assert snapshot['checkpoint_id']=='checkpoint-2' and snapshot['step']==3
    assert uris and all('immutable=1' not in uri.partition('?')[2] for uri in uris)


@pytest.mark.parametrize('roles',[['News Analyst'],list(PARALLEL_ROLES)+['News Analyst'],['Bull Researcher',*PARALLEL_ROLES[1:]]])
def test_parallel_pending_set_must_match_native_four_subgraphs(env,roles):
    e=parallel_env(env);e.grant['binding']['expected_pending_roles']=roles;e.pin()
    with pytest.raises(research.ResearchBlocked):context(e)

def test_parallel_guard_resumes_two_paid_tool_proposals_in_any_role_order(env):
    e=parallel_env(env);originals={p:p.read_bytes() for p in (e.run/'model-outputs').glob('*.json')};seen=[]
    async def continuation(*,recovery_guard,**kwargs):
        for i,role in enumerate(('Sentiment Analyst','Market Analyst','Fundamentals Analyst','News Analyst')):
            recovery_guard(dict(role=role,messages=[dict(role='system',content='same pinned prompt')]+e.messages[role]),3+i);seen.append(role)
        raise RuntimeError('stop fixture before any paid generation')
    e.native._execute=continuation;c,owner=consumed(e)
    with pytest.raises(RuntimeError,match='stop fixture'):asyncio.run(recovery.run_recovery(e.rec,params(c,owner),e.app))
    assert seen==['Sentiment Analyst','Market Analyst','Fundamentals Analyst','News Analyst']
    assert all(p.read_bytes()==body for p,body in originals.items()) and json.loads((e.run/'call-counter.json').read_text())=={'reserved':3}

@pytest.mark.parametrize('mutation',['missing_proposal','alter_tool_result','duplicate_old_proposal'])
def test_parallel_first_request_requires_full_frozen_message_continuation(env,mutation):
    e=parallel_env(env);called=[]
    async def continuation(*,recovery_guard,**kwargs):
        messages=deepcopy(e.messages['Market Analyst'])
        if mutation=='missing_proposal':messages=messages[:1]
        elif mutation=='alter_tool_result':messages[-1]['content']='changed tool result'
        else:messages=messages+[messages[1]]
        recovery_guard(dict(role='Market Analyst',messages=messages),3);called.append(True)
    e.native._execute=continuation;c,owner=consumed(e)
    with pytest.raises(research.ResearchBlocked):asyncio.run(recovery.run_recovery(e.rec,params(c,owner),e.app))
    assert called==[] and json.loads((e.run/'call-counter.json').read_text())=={'reserved':3}

def test_parallel_empty_stream_allows_only_later_append(env):
    e=parallel_env(env);context(e)
    (e.run/'role-outputs.jsonl').write_text(json.dumps({'peer':dict(role='Market Analyst',call_id=4,output_sha256='a'*64)})+'\n')
    with pytest.raises(research.ResearchBlocked):context(e)
    assert recovery._context(OLD,RUN,e.app,e.sf,initial=False)['binding']['completed_roles']==[]
    (e.run/'role-outputs.jsonl').write_text(json.dumps({'peer':dict(role='Market Analyst',call_id=2,output_sha256='a'*64)})+'\n')
    with pytest.raises(research.ResearchBlocked):recovery._context(OLD,RUN,e.app,e.sf,initial=False)

@pytest.mark.parametrize('mode',['missing_nonempty_audit','empty_fingerprints','changed_subgraph','non_tool_original'])
def test_parallel_freeze_rejects_non_equivalent_source(env,mode):
    e=parallel_env(env)
    if mode=='missing_nonempty_audit':(e.run/'source-audit.jsonl').unlink()
    elif mode=='empty_fingerprints':e.grant['binding']['pending_message_fingerprints']['News Analyst']=[]
    elif mode=='changed_subgraph':
        cp=e.run/'graph/cache/checkpoints'/f'{SYMBOL}.db'
        with sqlite3.connect(cp) as db:db.execute("DELETE FROM writes WHERE task_path='~__pregel_pull, News Analyst'")
        e.grant['binding']['checkpoint']=recovery.checkpoint_snapshot(e.run,SYMBOL)
    else:
        value=json.loads((e.run/'model-outputs/1.json').read_text());value['structured_output']['tool_calls']=[]
        value['output_sha256']=research._digest(value['structured_output']);put(e.run/'model-outputs/1.json',value)
        e.grant['binding']['outputs']['1']=recovery._sha(e.run/'model-outputs/1.json')
    e.pin()
    with pytest.raises(research.ResearchBlocked):context(e)

def test_parallel_zero_final_continuation_publishes_complete_without_replaying_proposals(env):
    e=parallel_env(env);originals={p:p.read_bytes() for p in (e.run/'model-outputs').glob('*.json')}
    async def continuation(*,rec,recovery_guard,**kwargs):
        for n,role in enumerate([*PARALLEL_ROLES,*[r for r in research.ROLES if r not in PARALLEL_ROLES]],4):
            recovery_guard(dict(role=role,messages=e.messages.get(role,[])),n-1)
            output=dict(content='new fixture final '+role,tool_calls=[]);outsha=research._digest(output)
            put(e.run/'model-outputs'/f'{n}.json',dict(call_id=n,role=role,structured_output=output,output_sha256=outsha))
            put(e.run/'usage-receipts'/f'{n}.json',dict(task_id=rec.task_id,method_sha256=research._digest(e.current),call_id=n,
                role=role,output_sha256=outsha,usage_known=True,usage={'input_tokens':1,'output_tokens':1}))
            with (e.run/'role-outputs.jsonl').open('a') as h:h.write(json.dumps({'peer':dict(role=role,call_id=n,output_sha256=outsha)})+'\n')
            put(e.run/'call-counter.json',dict(reserved=n))
        with (e.run/'graph/node-trace.jsonl').open('a') as h:h.write(json.dumps({'node':'Memory Log','event':'end'})+'\n')
        return {key:'complete seven-report fixture' for key in research.REPORTS}
    e.native._execute=continuation;c,owner=consumed(e)
    result=asyncio.run(recovery.run_recovery(e.rec,params(c,owner),e.app))
    assert result['state']=='completed' and all(p.read_bytes()==body for p,body in originals.items())
    view=research._latest(SYMBOL)
    assert view['recovery_lineage']['reused_original_outputs']==2 and view['recovery_lineage']['new_call_from']==4
    assert len(view['reports'])==7 and view['recovery_lineage']['inherited_unknown_usage_count']==1

@pytest.mark.parametrize('mode',['permitted','not_named','terminal_failure','receipt_hash_drift'])
def test_typed_proposal_with_named_unknown_is_reused_without_reconciling_usage(env,mode):
    e=parallel_env(env);path=e.run/'usage-receipts/2.json';usage=json.loads(path.read_text());output=json.loads((e.run/'model-outputs/2.json').read_text())
    usage.update(usage_known=False,usage=None,terminal_kind='typed_response',output_sha256=output['output_sha256'])
    if mode=='terminal_failure':usage['terminal_kind']='unknown_transport_failure'
    if mode=='receipt_hash_drift':usage['output_sha256']='f'*64
    put(path,usage);e.grant['binding']['usage_receipts']['2']=recovery._sha(path)
    with e.sf() as db:db.get(AgentResourceUsage,f'{2:032x}').usage_known=0;db.commit()
    if mode!='not_named':e.auth['unknown_usage_exception']['allowed_unknown_usage'].append(dict(usage_id=f'{2:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM))
    e.pin();before=path.read_bytes()
    if mode=='permitted':assert context(e)['binding']['outputs']['2']==recovery._sha(e.run/'model-outputs/2.json')
    else:
        with pytest.raises(research.ResearchBlocked):context(e)
    with e.sf() as db:assert db.get(AgentResourceUsage,f'{2:032x}').usage_known==0
    assert path.read_bytes()==before

def test_cross_day_named_typed_reuse_does_not_exempt_new_day_unknown(env,monkeypatch):
    e=parallel_env(env);path=e.run/'usage-receipts/2.json';usage=json.loads(path.read_text())
    usage.update(usage_known=False,usage=None,terminal_kind='typed_response',
        output_sha256=json.loads((e.run/'model-outputs/2.json').read_text())['output_sha256'])
    put(path,usage);e.grant['binding']['usage_receipts']['2']=recovery._sha(path)
    with e.sf() as db:db.get(AgentResourceUsage,f'{2:032x}').usage_known=0;db.commit()
    e.auth['unknown_usage_exception']['allowed_unknown_usage'].append(dict(usage_id=f'{2:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM))
    tomorrow=NOW+timedelta(days=1)
    e.auth['valid_until']=(tomorrow+timedelta(hours=2)).isoformat();e.grant['valid_until']=(tomorrow+timedelta(hours=1)).isoformat();e.pin()
    monkeypatch.setattr(research,'_now',lambda:tomorrow);monkeypatch.setattr(agent_budget,'beijing_now',lambda:tomorrow)
    monkeypatch.setattr(agent_budget,'beijing_now_naive',lambda:tomorrow.replace(tzinfo=None))
    c=context(e);assert c['binding']['outputs']['2']==recovery._sha(e.run/'model-outputs/2.json')
    assert research._allowed_unknown_usage(c['admission'])==()
    with e.sf() as db:
        assert db.get(AgentResourceUsage,f'{2:032x}').usage_known==0
        db.add(AgentResourceUsage(id='9'*32,budget_date=tomorrow.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
            kind='model',purpose='tradingagents.new_day',task_id=NEW,state='failed',usage_known=0,started_at=tomorrow.replace(tzinfo=None)));db.commit()
    with pytest.raises(agent_budget.BudgetError,match='token 用量未知'):
        agent_budget.ensure_model_usage_known(e.sf,consumer_scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
            allowed_unknown_usage=research._allowed_unknown_usage(c['admission']))
    with pytest.raises(research.ResearchBlocked):context(e)

def test_mutable_checkpoint_and_counter_only_frozen_before_start(env):
    assert context(env)['binding']['call_counter']==2
    put(env.run/'call-counter.json',{'reserved':3})
    with pytest.raises(research.ResearchBlocked): context(env)
    assert recovery._context(OLD,RUN,env.app,env.sf,initial=False)['binding']['call_counter']==2

@pytest.mark.parametrize('name',['packet.json','config.json','model-outputs/1.json','usage-receipts/2.json','staged-code.json'])
def test_original_drift_refused(env,name):
    (env.run/name).write_text('{}')
    with pytest.raises(research.ResearchBlocked): context(env)
    assert env.native.calls==0

def test_symlink_refused(env,tmp_path):
    p=env.run/'packet.json'; p.unlink(); outside=tmp_path/'outside'; outside.write_text('{}'); p.symlink_to(outside)
    with pytest.raises(research.ResearchBlocked): context(env)

def test_full_continuation_preserves_old_and_reuses_normal_scheduler(env,monkeypatch):
    c,owner=consumed(env); old=env.source_claim.read_bytes(); originals={p:p.read_bytes() for p in (env.run/'model-outputs/1.json',env.run/'usage-receipts/1.json',env.run/'usage-receipts/2.json')}
    result=asyncio.run(agent_tasks._h_tradingagents_research(env.rec,params(c,owner),env.app))
    assert result['state']=='completed' and env.native.calls==1
    assert env.source_claim.read_bytes()==old and all(p.read_bytes()==b for p,b in originals.items())
    with env.sf() as db: assert db.get(AgentTask,OLD).status=='failed'
    view=research._latest(SYMBOL); assert view['recovery_lineage']['reused_original_outputs']==1
    assert view['recovery_lineage']['inherited_unknown_usage_count']==1
    prior=research._execution_prior(env.selection,research._digest(env.current)); assert research._completed_selection(prior,env.selection)['reused_identical_selection']
    from app.models.daily_pick import DailyPickSet
    entry={key:env.selection[key] for key in ('selected_at','source','source_version')}
    with env.sf() as db:
        DailyPickSet.__table__.create(db.get_bind(),checkfirst=True)
        db.add(DailyPickSet(date=env.selection['trade_date'],items=json.dumps([{'symbol':SYMBOL}]),
            meta=json.dumps({'selection_entries':{SYMBOL:{'state':'recorded',**entry}}})))
        db.commit()
    monkeypatch.setattr(research,'selected_bindings',lambda *args:{SYMBOL:env.selection})
    async def no_create(*args,**kwargs): raise AssertionError('completed selection must not regenerate')
    monkeypatch.setattr(agent_tasks,'create_task_async',no_create); asyncio.run(research.scheduler_tick(env.app)); assert env.native.calls==1

@pytest.mark.parametrize('failure',[RuntimeError('fixture failure'),asyncio.CancelledError()])
def test_failure_and_cancel_consumes_grant_preserves_old(env,failure):
    c,owner=consumed(env); env.native.fail=failure; before=env.source_claim.read_bytes()
    with pytest.raises(type(failure)): asyncio.run(recovery.run_recovery(env.rec,params(c,owner),env.app))
    assert env.source_claim.read_bytes()==before and recovery._json(c['claim'])['state']=='needs_recovery'
    again,fresh=recovery._dispatch(c); assert not fresh and again['task_id']==NEW

@pytest.mark.parametrize('revoke',[False,True])
def test_pending_guard_and_revocation_prevent_new_call(env,revoke):
    c,owner=consumed(env); env.native.revoke=revoke
    if not revoke: env.native.first_role='Bull Researcher'
    with pytest.raises(research.ResearchBlocked): asyncio.run(recovery.run_recovery(env.rec,params(c,owner),env.app))
    assert recovery._json(env.run/'call-counter.json')=={'reserved':2} and not (env.run/'model-outputs/3.json').exists()

def test_late_queue_and_unknown_never_downgrade_real_execution(env):
    c,owner=consumed(env); recovery._advance(c,owner,'recovering',task_id=NEW)
    assert recovery._advance(c,owner,'recovery_queued',task_id=NEW) is False
    recovery._advance(c,owner,'completed',task_id=NEW,result_sha256='f'*64)
    assert recovery._advance(c,owner,'recovery_dispatch_unknown',task_id=NEW) is False
    assert recovery._json(c['claim'])['state']=='completed'

def test_other_failed_method_cannot_hide_behind_recovery(env):
    put(research._selection_claim(env.selection,'e'*64),dict(version=1,selection=env.selection,method_sha256='e'*64,state='failed',task_id='e'*32,updated_at=NOW.isoformat()))
    with pytest.raises(research.ResearchBlocked): recovery._dispatch(context(env))

def test_repeated_post_after_success_returns_same_task_without_new_dispatch(env,monkeypatch):
    queued=[]
    async def create(type_,supplied,**kwargs):
        queued.append(supplied)
        return {'id':NEW,'type':type_,'params':supplied,'status':'queued'}
    monkeypatch.setattr(agent_tasks,'create_task_async',create)
    monkeypatch.setattr(agent_tasks,'get_task',lambda task_id:{'id':task_id,'status':'succeeded'})
    first=asyncio.run(recovery.create_recovery(OLD,RUN,env.app))
    asyncio.run(recovery.run_recovery(env.rec,first['params'],env.app))
    repeated=asyncio.run(recovery.create_recovery(OLD,RUN,env.app))
    assert repeated['id']==NEW and len(queued)==1 and env.native.calls==1


def test_dispatch_failure_stays_unknown_without_reclaim(env,monkeypatch):
    calls=[]
    async def create(*args,**kwargs): calls.append(1); raise agent_tasks.TaskQueueUnavailable(NEW,'unknown')
    monkeypatch.setattr(agent_tasks,'create_task_async',create)
    with pytest.raises(agent_tasks.TaskQueueUnavailable): asyncio.run(recovery.create_recovery(OLD,RUN,env.app))
    with pytest.raises(research.ResearchBlocked): asyncio.run(recovery.create_recovery(OLD,RUN,env.app))
    assert len(calls)==1 and env.native.calls==0


def test_history_failure_leaves_consumed_no_replay_claim(env):
    c,owner=consumed(env)
    (env.run/'recovery-history').write_text('file blocks archive creation')
    with pytest.raises(OSError): asyncio.run(recovery.run_recovery(env.rec,params(c,owner),env.app))
    assert recovery._json(c['claim'])['state']=='needs_recovery' and env.native.calls==0


def test_future_unknown_is_not_covered_by_existing_allowance(env):
    c,owner=consumed(env)
    with env.sf() as db:
        db.add(AgentResourceUsage(id='e'*32,budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
            kind='model',purpose='new unknown',state='failed',usage_known=0,started_at=NOW.replace(tzinfo=None))); db.commit()
    with pytest.raises(research.ResearchBlocked) as error: asyncio.run(recovery.run_recovery(env.rec,params(c,owner),env.app))
    assert error.value.code=='usage_unknown' and env.native.calls==0


@pytest.mark.parametrize('target',['transport','dependencies','packet_builder','worker.py'])
def test_component_compatibility_never_waives_native_model_or_data_drift(env,target):
    if target=='worker.py':
        env.current['source_files'][target]='f'*64
        env.grant['compatibility']['source_hash_pairs'][target]={'old':'3'*64,'new':'f'*64}
    else: env.current[target]={'changed':True}
    env.grant['continuation_method_identity']=env.current
    env.auth['method_identity']=env.current
    env.auth['unknown_usage_exception']['method_sha256']=research._digest(env.current); env.pin()
    with pytest.raises(research.ResearchBlocked): context(env)
    assert env.native.calls==0

@pytest.mark.parametrize('mutation',[None,'future_hash','reverse','missing_old','wrong_grant_pair','worker_drift'])
def test_reviewed_transport_usage_transition_is_exact_and_directed(env,mutation):
    grant=deepcopy(env.grant);old=grant['source_method_identity'];new=grant['continuation_method_identity']
    old['source_files']['transport_process.py']='9281aeaf1fc31d8b668392d99388f755043bbb191c5e27e39d4e3c1c053da3d2'
    new['source_files']['transport_process.py']='d88bdea9ecc8f9a854f02d92ed448d08fe3995074d4f130d805ab4d88aca7881'
    if mutation=='future_hash':new['source_files']['transport_process.py']='f'*64
    elif mutation=='reverse':old['source_files']['transport_process.py'],new['source_files']['transport_process.py']=new['source_files']['transport_process.py'],old['source_files']['transport_process.py']
    elif mutation=='missing_old':old['source_files'].pop('transport_process.py')
    elif mutation=='worker_drift':new['source_files']['worker.py']='f'*64
    grant['compatibility']['source_hash_pairs']={k:dict(old=old['source_files'].get(k),new=new['source_files'].get(k))
        for k in old['source_files'].keys()|new['source_files'].keys() if old['source_files'].get(k)!=new['source_files'].get(k)}
    if mutation=='wrong_grant_pair':grant['compatibility']['source_hash_pairs']['transport_process.py']['new']='e'*64
    if mutation is None:assert recovery._compatibility(grant,new)==(research._digest(old),research._digest(new))
    else:
        with pytest.raises(research.ResearchBlocked):recovery._compatibility(grant,new)


@pytest.mark.parametrize('components',[
    ('transport_process.py',), ('tradingagents_source_gate.py',),
    ('transport_process.py','tradingagents_source_gate.py'),
])
@pytest.mark.parametrize('mutation',[None,'future_hash','reverse','missing_old','wrong_grant_pair','codec_drift'])
def test_reviewed_current_source_and_timeout_transitions_are_exact(env,components,mutation):
    grant=deepcopy(env.grant);old=grant['source_method_identity'];new=grant['continuation_method_identity']
    pairs={
        'transport_process.py':(
            'd88bdea9ecc8f9a854f02d92ed448d08fe3995074d4f130d805ab4d88aca7881',
            'ff2812c801be847b6c6e83b0edf109f82fb7fe65ea9077e4473597437af4e592'),
        'tradingagents_source_gate.py':(
            '862dd58539f17698a8feabf04218acb41681f7381350007e6b630692d0fbbafc',
            '0655bee3b5138cb354e69ee9c0c8cf9f7d627c58898952a2140a7237e14d7f83'),
    }
    for name in components:
        old['source_files'][name],new['source_files'][name]=pairs[name]
    target=components[0]
    if mutation=='future_hash':new['source_files'][target]='f'*64
    elif mutation=='reverse':old['source_files'][target],new['source_files'][target]=new['source_files'][target],old['source_files'][target]
    elif mutation=='missing_old':old['source_files'].pop(target)
    elif mutation=='codec_drift':
        old['source_files']['cli_codec.py']='a'*64;new['source_files']['cli_codec.py']='b'*64
    grant['compatibility']['source_hash_pairs']={k:dict(old=old['source_files'].get(k),new=new['source_files'].get(k))
        for k in old['source_files'].keys()|new['source_files'].keys() if old['source_files'].get(k)!=new['source_files'].get(k)}
    if mutation=='wrong_grant_pair':grant['compatibility']['source_hash_pairs'][target]['new']='e'*64
    if mutation is None:assert recovery._compatibility(grant,new)==(research._digest(old),research._digest(new))
    else:
        with pytest.raises(research.ResearchBlocked):recovery._compatibility(grant,new)
    assert env.native.calls==0


@pytest.mark.parametrize('mutation',[None,'future_hash','reverse','missing_old','wrong_grant_pair','codec_drift'])
def test_reviewed_coordinated_disclaimer_source_transition_is_exact(env,mutation):
    grant=deepcopy(env.grant);old=grant['source_method_identity'];new=grant['continuation_method_identity'];name='tradingagents_source_gate.py'
    old['source_files'][name]='0655bee3b5138cb354e69ee9c0c8cf9f7d627c58898952a2140a7237e14d7f83'
    new['source_files'][name]='5554522f2f9b601f3e91443459eb6874c65466b88ca57bad9728029ebace6604'
    if mutation=='future_hash':new['source_files'][name]='f'*64
    elif mutation=='reverse':old['source_files'][name],new['source_files'][name]=new['source_files'][name],old['source_files'][name]
    elif mutation=='missing_old':old['source_files'].pop(name)
    elif mutation=='codec_drift':old['source_files']['cli_codec.py']='a'*64;new['source_files']['cli_codec.py']='b'*64
    grant['compatibility']['source_hash_pairs']={key:dict(old=old['source_files'].get(key),new=new['source_files'].get(key))
        for key in old['source_files'].keys()|new['source_files'].keys() if old['source_files'].get(key)!=new['source_files'].get(key)}
    if mutation=='wrong_grant_pair':grant['compatibility']['source_hash_pairs'][name]['new']='e'*64
    if mutation is None:assert recovery._compatibility(grant,new)==(research._digest(old),research._digest(new))
    else:
        with pytest.raises(research.ResearchBlocked):recovery._compatibility(grant,new)
    assert env.native.calls==0


def test_grant_cannot_omit_existing_original_paid_output(env):
    put(env.run/'model-outputs/2.json',{'structured_output':{'content':'another saved original'}})
    with pytest.raises(research.ResearchBlocked): context(env)
    assert env.native.calls==0

@pytest.mark.parametrize('bad',[None,[],True,'damaged'])
def test_corrupt_recovery_claim_does_not_crash_ordinary_reader(env,bad):
    c,owner=consumed(env)
    asyncio.run(recovery.run_recovery(env.rec,params(c,owner),env.app))
    claim=recovery._json(c['claim']); claim['native_recovery']=bad; put(c['claim'],claim)
    assert research._latest(SYMBOL) is None
    with pytest.raises(research.ResearchBlocked): research._execution_prior(env.selection,research._digest(env.current))

def test_lost_original_claim_cannot_be_silently_reused(env):
    c,owner=consumed(env); asyncio.run(recovery.run_recovery(env.rec,params(c,owner),env.app))
    completed=recovery._json(c['claim']); env.source_claim.unlink()
    assert research._latest(SYMBOL) is None
    with pytest.raises(research.ResearchBlocked): research._completed_selection(completed,env.selection)


@pytest.mark.parametrize('field',['method_sha256','selection','version','run_id','source_method_sha256','source_claim_sha256'])
def test_runtime_owner_identity_drift_is_rejected_before_new_call(env,field):
    c,owner=consumed(env); recovery._advance(c,owner,'recovering',task_id=NEW)
    claim=recovery._json(c['claim'])
    if field=='selection': claim[field]={**claim[field],'source_version':'wrong'}
    elif field=='version': claim[field]=True
    elif field=='method_sha256': claim[field]='f'*64
    else: claim['native_recovery'][field]='f'*(32 if field=='run_id' else 64)
    put(c['claim'],claim)
    with pytest.raises(research.ResearchBlocked): recovery._authorizer(c,params(c,owner)['native_recovery'],NEW,env.app,env.sf)
    assert env.native.calls==0 and recovery._json(env.run/'call-counter.json')=={'reserved':2}


@pytest.mark.parametrize('corrupt',['output','receipt'])
def test_new_output_and_usage_checksum_must_agree_before_completion(env,monkeypatch,corrupt):
    c,owner=consumed(env); native=env.native._execute
    async def execute(**kwargs):
        result=await native(**kwargs)
        path=env.run/('model-outputs' if corrupt=='output' else 'usage-receipts')/'3.json'
        saved=recovery._json(path)
        if corrupt=='output': saved['structured_output']['content']='silently different answer'
        else: saved['output_sha256']='f'*64
        put(path,saved); return result
    monkeypatch.setattr(env.native,'_execute',execute)
    with pytest.raises(research.ResearchBlocked): asyncio.run(recovery.run_recovery(env.rec,params(c,owner),env.app))
    assert not (env.run/'result.json').exists() and recovery._json(c['claim'])['state']=='needs_recovery'


def failed_recovery_chain(e, monkeypatch, *, progress=False):
    """A real handler failure appends one unknown receipt, never another answer."""
    c, owner = consumed(e)
    original_execute = e.native._execute
    async def failed(**kwargs):
        kwargs['recovery_guard']({'role':'Bear Researcher'},2)
        if progress:
            output={'content':'saved recovery answer','tool_calls':[]}; output_hash=research._digest(output)
            put(e.run/'model-outputs/3.json',dict(call_id=3,role='Bear Researcher',structured_output=output,output_sha256=output_hash))
            put(e.run/'usage-receipts/3.json',dict(task_id=NEW,method_sha256=research._digest(e.current),call_id=3,
                usage_id=f'{3:032x}',role='Bear Researcher',output_sha256=output_hash,usage_known=True,usage={'input_tokens':1,'output_tokens':1}))
            with (e.run/'role-outputs.jsonl').open('a') as handle:
                handle.write(json.dumps({'peer':dict(role='Bear Researcher',call_id=3,output_sha256=output_hash)})+'\n')
        last=4 if progress else 3
        put(e.run/'usage-receipts'/f'{last}.json',dict(task_id=NEW,method_sha256=research._digest(e.current),
            call_id=last,usage_id=f'{last:032x}',usage_known=False,usage=None))
        put(e.run/'call-counter.json',{'reserved':last})
        raise RuntimeError('isolated terminal failure')
    monkeypatch.setattr(e.native,'_execute',failed)
    with pytest.raises(RuntimeError): asyncio.run(recovery.run_recovery(e.rec,params(c,owner),e.app))
    monkeypatch.setattr(e.native,'_execute',original_execute)
    with e.sf() as db:
        db.add(AgentTask(id=NEW,type='tradingagents_research',status='failed',params=json.dumps(params(c,owner)),finished_at=NOW.replace(tzinfo=None)))
        db.add(AgentResourceUsage(id=f'{4 if progress else 3:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
            kind='model',purpose='failed recovery',task_id=NEW,state='failed',usage_known=0,started_at=(NOW-timedelta(seconds=1)).replace(tzinfo=None)))
        db.commit()
    previous_identity=deepcopy(e.current)
    history=dict(grant_id=GRANT,grant_sha256=c['grant_sha256'],task_id=NEW,method_identity=previous_identity,
        call_from=3,call_to=4 if progress else 3,claim_sha256=recovery._sha(c['claim']))
    e.current['source_files']['codex_transport.py']='9'*64
    e.current['source_files']['tradingagents_recovery.py']='a'*64
    e.grant['grant_id']='e'*32
    e.grant['continuation_method_identity']=e.current
    e.grant['compatibility']['source_hash_pairs']={k:dict(old=e.grant['source_method_identity']['source_files'].get(k),new=e.current['source_files'].get(k))
        for k in e.grant['source_method_identity']['source_files'].keys()|e.current['source_files'].keys()
        if e.grant['source_method_identity']['source_files'].get(k)!=e.current['source_files'].get(k)}
    last=4 if progress else 3
    e.grant['binding'].update(call_counter=last,recovery_history=[history],
        usage_receipts={str(n):recovery._sha(e.run/'usage-receipts'/f'{n}.json') for n in range(1,last+1)},
        outputs={path.stem:recovery._sha(path) for path in (e.run/'model-outputs').glob('*.json')},
        prefixes={name:dict(bytes=len((e.run/name).read_bytes()),sha256=recovery._sha(e.run/name)) for name in ('role-outputs.jsonl','source-audit.jsonl','graph/node-trace.jsonl')},
        completed_roles=[{key:json.loads(line)['peer'][key] for key in ('role','call_id','output_sha256')} for line in (e.run/'role-outputs.jsonl').read_text().splitlines()])
    if progress: e.grant['binding']['expected_pending_role']='Market Analyst'; e.native.first_role='Market Analyst'
    e.auth['unknown_usage_exception']['method_sha256']=research._digest(e.current)
    e.auth['unknown_usage_exception']['authorized_at']=NOW.isoformat()
    e.auth['unknown_usage_exception']['allowed_unknown_usage'].append(dict(usage_id=f'{last:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM))
    e.native.resume_counter=last
    e.pin()
    return c, history


def test_second_authorized_recovery_retains_failed_chain_and_true_original_source(env,monkeypatch):
    first, history = failed_recovery_chain(env,monkeypatch)
    originals={p:p.read_bytes() for p in [env.source_claim,first['claim'],*(env.run/'usage-receipts'/f'{n}.json' for n in (1,2,3)),env.run/'model-outputs/1.json']}
    c=context(env); claim,fresh=recovery._dispatch(c); assert fresh
    task='f'*32; owner=claim['native_recovery']['dispatch_owner']
    recovery._advance(c,owner,'recovery_queued',task_id=task)
    rec=Recorder(); rec.task_id=task
    result=asyncio.run(recovery.run_recovery(rec,params(c,owner),env.app))
    assert result['recovery_lineage']['source_task_id']==OLD
    assert result['recovery_lineage']['source_method_sha256']==research._digest(env.grant['source_method_identity'])
    assert result['recovery_lineage']['new_call_from']==4
    assert result['recovery_lineage']['inherited_unknown_usage_count']==2
    assert all(p.read_bytes()==body for p,body in originals.items())
    assert research._latest(SYMBOL)['recovery_lineage']==result['recovery_lineage']
    prior=research._execution_prior(env.selection,research._digest(env.current))
    assert research._completed_selection(prior,env.selection)['reused_identical_selection']
    repeated,fresh=recovery._dispatch(recovery._context(OLD,RUN,env.app,env.sf,initial=False))
    assert not fresh and repeated['task_id']==task


@pytest.mark.parametrize('field',['task_id','method_sha256'])
def test_chain_receipt_requires_exact_historical_task_and_method(env,monkeypatch,field):
    failed_recovery_chain(env,monkeypatch)
    path=env.run/'usage-receipts/3.json'; value=recovery._json(path); value[field]='f'*(32 if field=='task_id' else 64); put(path,value)
    env.grant['binding']['usage_receipts']['3']=recovery._sha(path); env.pin()
    with pytest.raises(research.ResearchBlocked): context(env)


def test_historical_claim_drift_prevents_completed_reader_and_reuse(env,monkeypatch):
    first,_=failed_recovery_chain(env,monkeypatch)
    c=context(env); claim,fresh=recovery._dispatch(c); assert fresh
    task='f'*32; owner=claim['native_recovery']['dispatch_owner']; recovery._advance(c,owner,'recovery_queued',task_id=task)
    rec=Recorder(); rec.task_id=task
    asyncio.run(recovery.run_recovery(rec,params(c,owner),env.app))
    changed=recovery._json(first['claim']); changed['native_recovery']['source_claim_sha256']='f'*64; put(first['claim'],changed)
    assert research._latest(SYMBOL) is None
    with pytest.raises(research.ResearchBlocked): research._execution_prior(env.selection,research._digest(env.current))


@pytest.mark.parametrize('change',['gap','overlap','source_task','task_running','history_bound'])
def test_chain_cannot_hide_gap_other_source_active_task_or_unbounded_history(env,monkeypatch,change):
    _,history=failed_recovery_chain(env,monkeypatch)
    if change=='gap': history['call_to']=2
    elif change=='overlap': history['call_from']=2
    elif change=='source_task': history['task_id']='f'*32
    elif change=='history_bound': env.grant['binding']['recovery_history']*=9
    else:
        with env.sf() as db: db.get(AgentTask,NEW).status='running'; db.commit()
    env.pin()
    with pytest.raises(research.ResearchBlocked): context(env)


def test_historical_expiry_is_not_retroactive_but_current_grant_must_be_live(env,monkeypatch):
    failed_recovery_chain(env,monkeypatch)
    later=NOW+timedelta(hours=2); monkeypatch.setattr(research,'_now',lambda:later)
    env.grant.update(authorized_at=(later-timedelta(minutes=1)).isoformat(),valid_until=(later+timedelta(hours=1)).isoformat())
    env.auth['valid_until']=(later+timedelta(hours=2)).isoformat(); env.auth['unknown_usage_exception']['authorized_at']=later.isoformat(); env.pin()
    assert context(env)['binding']['call_counter']==3
    env.grant['valid_until']=later.isoformat(); env.pin()
    with pytest.raises(research.ResearchBlocked) as error: context(env)
    assert error.value.code=='recovery_grant_expired'


def test_chain_allowance_does_not_implicitly_cover_new_unknown(env,monkeypatch):
    failed_recovery_chain(env,monkeypatch)
    env.auth['unknown_usage_exception']['allowed_unknown_usage'].pop(); env.pin()
    with pytest.raises(research.ResearchBlocked) as error: context(env)
    assert error.value.code=='usage_unknown'


def test_next_recovery_reuses_roles_saved_by_failed_recovery_without_replay(env,monkeypatch):
    first,_=failed_recovery_chain(env,monkeypatch,progress=True)
    old=(env.run/'model-outputs/3.json').read_bytes(); c=context(env); claim,fresh=recovery._dispatch(c); assert fresh
    task='f'*32; owner=claim['native_recovery']['dispatch_owner']; recovery._advance(c,owner,'recovery_queued',task_id=task)
    rec=Recorder(); rec.task_id=task
    result=asyncio.run(recovery.run_recovery(rec,params(c,owner),env.app))
    assert result['recovery_lineage']['new_call_from']==5 and result['recovery_lineage']['reused_original_outputs']==2
    assert (env.run/'model-outputs/3.json').read_bytes()==old and recovery._json(first['claim'])['state']=='needs_recovery'
    new_roles=[recovery._json(p)['role'] for p in (env.run/'model-outputs').glob('*.json') if int(p.stem)>4]
    assert 'Bull Researcher' not in new_roles and 'Bear Researcher' not in new_roles


def test_bounded_chain_can_recover_another_consumed_prelaunch_failure(env,monkeypatch):
    failed_recovery_chain(env,monkeypatch)
    c=context(env); claim,fresh=recovery._dispatch(c); assert fresh
    task='f'*32; owner=claim['native_recovery']['dispatch_owner']; recovery._advance(c,owner,'recovery_queued',task_id=task)
    rec=Recorder(); rec.task_id=task; env.native.fail=RuntimeError('failed before model launch')
    with pytest.raises(RuntimeError): asyncio.run(recovery.run_recovery(rec,params(c,owner),env.app))
    with env.sf() as db:
        db.add(AgentTask(id=task,type='tradingagents_research',status='failed',params=json.dumps(params(c,owner)),finished_at=NOW.replace(tzinfo=None))); db.commit()
    entry=dict(grant_id=env.grant['grant_id'],grant_sha256=c['grant_sha256'],task_id=task,
        method_identity=deepcopy(env.current),call_from=4,call_to=3,claim_sha256=recovery._sha(c['claim']))
    env.grant['binding']['recovery_history'].append(entry)
    env.grant['grant_id']='1'*32; env.current['source_files']['tradingagents_recovery.py']='b'*64
    env.grant['compatibility']['source_hash_pairs']={k:dict(old=env.grant['source_method_identity']['source_files'].get(k),new=env.current['source_files'].get(k))
        for k in env.grant['source_method_identity']['source_files'].keys()|env.current['source_files'].keys()
        if env.grant['source_method_identity']['source_files'].get(k)!=env.current['source_files'].get(k)}
    env.auth['unknown_usage_exception']['method_sha256']=research._digest(env.current); env.native.fail=None; env.pin()
    current=context(env); claim,fresh=recovery._dispatch(current); assert fresh
    rec.task_id='2'*32; owner=claim['native_recovery']['dispatch_owner']; recovery._advance(current,owner,'recovery_queued',task_id=rec.task_id)
    result=asyncio.run(recovery.run_recovery(rec,params(current,owner),env.app))
    assert result['recovery_lineage']['source_task_id']==OLD and result['recovery_lineage']['inherited_unknown_usage_count']==2
    assert research._latest(SYMBOL)['recovery_lineage']==result['recovery_lineage']


def test_new_grant_cannot_reseal_a_changed_historical_prefix(env,monkeypatch):
    failed_recovery_chain(env,monkeypatch)
    path=env.run/'source-audit.jsonl'; original=path.read_bytes()
    path.write_text('{"changed":true}\n')
    env.grant['binding']['prefixes']['source-audit.jsonl']=dict(bytes=len(path.read_bytes()),sha256=recovery._sha(path)); env.pin()
    with pytest.raises(research.ResearchBlocked): context(env)
    # Restore the actual old bytes, not a relaxed old SHA or rewritten grant.
    path.write_bytes(original)
    env.grant['binding']['prefixes']['source-audit.jsonl']=dict(bytes=len(original),sha256=recovery._sha(path)); env.pin()
    assert context(env)['binding']['call_counter']==3


def prelaunch_tail_env(e):
    """Historical accounted gate failure after counter2, before any second row."""
    parallel_env(e); b=e.grant['binding']; old=e.grant['source_method_identity']
    old['source_files'].update({'driver.py':'939e0de30999ac2b6e93b947e581e82f5e430a1134a2b51c5af8748cafb09b9d',
        'codex_transport.py':'1876b45f0636e4305fb1df97ea2306134aacdc338a357b20e88e3cb0ad205101',
        'agent_budget.py':'5b292c8d675f71e97a86116b6e3250c7a15af8e2bde34ec6492875105c519923'})
    e.current['source_files']['agent_budget.py']=old['source_files']['agent_budget.py']
    source=recovery._json(e.source_claim); e.source_claim.unlink()
    source['method_sha256']=research._digest(old); e.source_claim=research._selection_claim(e.selection,source['method_sha256'])
    put(e.source_claim,source); b['source_claim_sha256']=recovery._sha(e.source_claim)
    e.grant['compatibility']['source_hash_pairs']={k:dict(old=old['source_files'].get(k),new=e.current['source_files'].get(k))
        for k in old['source_files'].keys()|e.current['source_files'].keys() if old['source_files'].get(k)!=e.current['source_files'].get(k)}
    (e.run/'model-outputs/2.json').unlink()
    for n in (2,3): (e.run/'usage-receipts'/f'{n}.json').unlink()
    receipt=recovery._json(e.run/'usage-receipts/1.json'); output=recovery._json(e.run/'model-outputs/1.json')
    receipt.update(method_sha256=research._digest(old),usage_known=False,usage=None,terminal_kind='typed_response',output_sha256=output['output_sha256'])
    put(e.run/'usage-receipts/1.json',receipt)
    error=dict(code='BudgetError',message='今日已有模型调用的 token 用量未知；拒绝继续消耗，须先核实/跨日恢复',retryable=False)
    with e.sf() as db:
        for row in db.query(AgentResourceUsage).all(): db.delete(row)
        db.add(AgentResourceUsage(id=f'{1:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
            kind='model',purpose='tradingagents.fundamentals_analyst',provider='codex_cli',model='gpt-6.1-sol',task_id=OLD,
            state='succeeded',usage_known=0,attempts=1,started_at=(NOW-timedelta(minutes=16)).replace(tzinfo=None),
            finished_at=(NOW-timedelta(minutes=15)).replace(tzinfo=None)))
        db.get(AgentTask,OLD).error=json.dumps(error); db.commit()
        task_error=db.get(AgentTask,OLD).error
        rows=db.query(AgentResourceUsage).order_by(AgentResourceUsage.id).all()
        facts=[{c.name:(v.isoformat() if isinstance(v,datetime) else v) for c in AgentResourceUsage.__table__.columns
                for v in (getattr(row,c.name),)} for row in rows]
    put(e.run/'execution.json',dict(state='needs_native_recovery',error_kind='BudgetError',automatic_retry=False))
    returned=dict(case_id=SYMBOL,arm='ta-luna',status='returned',call_id=1,role='Fundamentals Analyst',response_sha256=output['output_sha256'])
    failed=dict(case_id=SYMBOL,arm='ta-luna',status='broker_error',call_id=None,role='Market Analyst',error_type="{'kind': 'BudgetError'}")
    records=[returned,failed,dict(failed,role='News Analyst',error_type="{'kind': 'DriverUnavailable'}")]
    (e.run/'graph/model-receipts.jsonl').write_text(''.join(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n' for row in records))
    e.messages['Market Analyst']=e.messages['Market Analyst'][:1]
    cp=e.run/'graph/cache/checkpoints'/f'{SYMBOL}.db'
    with sqlite3.connect(cp) as db:db.execute("UPDATE checkpoints SET metadata=? WHERE checkpoint_ns LIKE 'Market Analyst:%'",(json.dumps({'step':0,'parents':{'':'checkpoint-1'}}),))
    b.update(call_counter=2,checkpoint=recovery.checkpoint_snapshot(e.run,SYMBOL),outputs={'1':recovery._sha(e.run/'model-outputs/1.json')},
        usage_receipts={'1':recovery._sha(e.run/'usage-receipts/1.json')},
        pending_message_fingerprints={role:[research._digest(row) for row in rows] for role,rows in e.messages.items()})
    put(e.run/'call-counter.json',{'reserved':2})
    b['prelaunch_tail']=dict(version=1,call_id=2,source_task_error_sha256=hashlib.sha256(task_error.encode()).hexdigest(),
        source_execution_sha256=recovery._sha(e.run/'execution.json'),source_model_usage_facts_sha256=research._digest(facts),
        model_receipts_prefix=dict(bytes=(e.run/'graph/model-receipts.jsonl').stat().st_size,sha256=recovery._sha(e.run/'graph/model-receipts.jsonl')),
        first_broker_error_record_sha256=research._digest(failed))
    e.auth['unknown_usage_exception'].update(method_sha256=research._digest(e.current),
        allowed_unknown_usage=[dict(usage_id=f'{1:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM)])
    e.pin(); return e


def complete_tail_continuation(e):
    async def continuation(*,rec,recovery_guard,usage_authorizer,**kwargs):
        for n,role in enumerate([*PARALLEL_ROLES,*[r for r in research.ROLES if r not in PARALLEL_ROLES]],e.grant['binding']['call_counter']+1):
            recovery_guard(dict(role=role,messages=e.messages.get(role,[])),n-1); usage_authorizer()
            output=dict(content='new fixture final '+role,tool_calls=[]); outsha=research._digest(output)
            put(e.run/'model-outputs'/f'{n}.json',dict(call_id=n,role=role,structured_output=output,output_sha256=outsha))
            put(e.run/'usage-receipts'/f'{n}.json',dict(task_id=rec.task_id,method_sha256=research._digest(e.current),call_id=n,
                usage_id=f'{n:032x}',role=role,output_sha256=outsha,usage_known=True,usage={'input_tokens':1,'output_tokens':1}))
            with (e.run/'role-outputs.jsonl').open('a') as handle:handle.write(json.dumps({'peer':dict(role=role,call_id=n,output_sha256=outsha)})+'\n')
        with (e.run/'graph/node-trace.jsonl').open('a') as handle:handle.write(json.dumps({'node':'Memory Log','event':'end'})+'\n')
        put(e.run/'call-counter.json',{'reserved':n}); return {key:'complete resumed report' for key in research.REPORTS}
    e.native._execute=continuation


def test_declared_prelaunch_tail_preserves_gap_and_completed_read_reuses_result(env,monkeypatch):
    e=prelaunch_tail_env(env); complete_tail_continuation(e)
    originals={p:p.read_bytes() for p in (e.run/'model-outputs/1.json',e.run/'usage-receipts/1.json',e.source_claim)}
    c,owner=consumed(e); result=asyncio.run(recovery.run_recovery(e.rec,params(c,owner),e.app))
    assert result['recovery_lineage']['new_call_from']==3 and result['recovery_lineage']['inherited_unknown_usage_count']==1
    assert not (e.run/'usage-receipts/2.json').exists() and not (e.run/'model-outputs/2.json').exists()
    assert all(p.read_bytes()==body for p,body in originals.items())
    assert research._latest(SYMBOL)['recovery_lineage']==result['recovery_lineage']
    prior=research._execution_prior(e.selection,research._digest(e.current)); assert research._completed_selection(prior,e.selection)['reused_identical_selection']
    monkeypatch.setattr(research,'selected_bindings',lambda *args:{SYMBOL:e.selection})
    async def no_create(*args,**kwargs):raise AssertionError('saved completion must not regenerate')
    monkeypatch.setattr(agent_tasks,'create_task_async',no_create); asyncio.run(research.scheduler_tick(e.app))


@pytest.mark.parametrize('mutation',['undeclared','interior','new_gap','source_driver','task_error','execution','native_error','receipt2'])
def test_prelaunch_tail_requires_exact_original_proof_before_dispatch(env,mutation):
    e=prelaunch_tail_env(env); b=e.grant['binding']; tail=b['prelaunch_tail']
    if mutation=='undeclared':b.pop('prelaunch_tail')
    elif mutation=='interior':tail['call_id']=1
    elif mutation=='new_gap':b['call_counter']=3;put(e.run/'call-counter.json',{'reserved':3})
    elif mutation=='source_driver':
        e.grant['source_method_identity']['source_files']['driver.py']='f'*64
        e.grant['compatibility']['source_hash_pairs']['driver.py']['old']='f'*64
        row=recovery._json(e.source_claim);e.source_claim.unlink();row['method_sha256']=research._digest(e.grant['source_method_identity'])
        e.source_claim=research._selection_claim(e.selection,row['method_sha256']);put(e.source_claim,row)
        b['source_claim_sha256']=recovery._sha(e.source_claim)
        receipt=recovery._json(e.run/'usage-receipts/1.json');receipt['method_sha256']=row['method_sha256'];put(e.run/'usage-receipts/1.json',receipt)
        b['usage_receipts']['1']=recovery._sha(e.run/'usage-receipts/1.json')
    elif mutation=='task_error':
        with e.sf() as db:
            task=db.get(AgentTask,OLD);task.error=json.dumps(dict(code='BudgetError',message='another failure'))
            db.commit();tail['source_task_error_sha256']=hashlib.sha256(task.error.encode()).hexdigest()
    elif mutation=='execution':
        put(e.run/'execution.json',dict(state='needs_native_recovery',error_kind='TransportError'))
        tail['source_execution_sha256']=recovery._sha(e.run/'execution.json')
    elif mutation=='native_error':
        p=e.run/'graph/model-receipts.jsonl';rows=[json.loads(line) for line in p.read_text().splitlines()]
        rows[1]['error_type']="{'kind': 'TransportError'}";p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        tail['model_receipts_prefix']=dict(bytes=p.stat().st_size,sha256=recovery._sha(p));tail['first_broker_error_record_sha256']=research._digest(rows[1])
    else:put(e.run/'usage-receipts/2.json',dict(call_id=2,usage_known=False))
    e.pin()
    with pytest.raises(research.ResearchBlocked):context(e)
    assert e.native.calls==0


@pytest.mark.parametrize('mutation',['extra_reserved','extra_started_other_scope','tokens','purpose','started_at'])
def test_prelaunch_source_query_includes_all_model_rows_and_exact_facts(env,mutation):
    e=prelaunch_tail_env(env)
    with e.sf() as db:
        if mutation.startswith('extra_'):
            db.add(AgentResourceUsage(id='f'*32,budget_date='2026-10-20',scope=agent_budget.SCOPE_AUTONOMY_LLM,
                kind='model',purpose='not in original receipts',task_id=OLD,state='reserved' if mutation=='extra_reserved' else 'succeeded',
                usage_known=1,started_at=None if mutation=='extra_reserved' else NOW.replace(tzinfo=None)))
        else:
            row=db.get(AgentResourceUsage,f'{1:032x}')
            if mutation=='tokens':row.input_tokens=123
            elif mutation=='purpose':row.purpose='silently another role'
            else:row.started_at=NOW.replace(tzinfo=None)
        db.commit()
    with pytest.raises(research.ResearchBlocked):context(e)


@pytest.mark.parametrize('mutation',['missing','unknown'])
def test_old_prelaunch_tail_never_exempts_new_call_receipts(env,mutation):
    e=prelaunch_tail_env(env);complete_tail_continuation(e);execute=e.native._execute
    async def corrupt(**kwargs):
        result=await execute(**kwargs);p=e.run/'usage-receipts/3.json'
        if mutation=='missing':p.unlink()
        else:
            row=recovery._json(p);row.update(usage_known=False,usage=None);put(p,row)
        return result
    e.native._execute=corrupt;c,owner=consumed(e)
    failure=FileNotFoundError if mutation=='missing' else research.ResearchBlocked
    with pytest.raises(failure):asyncio.run(recovery.run_recovery(e.rec,params(c,owner),e.app))
    assert not (e.run/'result.json').exists() and not (e.run/'usage-receipts/2.json').exists()


def failed_tail_chain(e):
    c,owner=consumed(e)
    async def failed(*,rec,recovery_guard,usage_authorizer,**kwargs):
        recovery_guard(dict(role='Market Analyst',messages=e.messages['Market Analyst']),2);usage_authorizer()
        put(e.run/'usage-receipts/3.json',dict(task_id=rec.task_id,method_sha256=research._digest(e.current),
            call_id=3,usage_id=f'{3:032x}',usage_known=False,usage=None))
        put(e.run/'call-counter.json',{'reserved':3})
        raise RuntimeError('new terminal usage unknown retained')
    e.native._execute=failed
    with pytest.raises(RuntimeError):asyncio.run(recovery.run_recovery(e.rec,params(c,owner),e.app))
    with e.sf() as db:
        db.add(AgentTask(id=NEW,type='tradingagents_research',status='failed',params=json.dumps(params(c,owner)),finished_at=NOW.replace(tzinfo=None)))
        db.add(AgentResourceUsage(id=f'{3:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
            kind='model',purpose='new failed recovery',task_id=NEW,state='failed',usage_known=0,started_at=(NOW-timedelta(seconds=1)).replace(tzinfo=None)));db.commit()
    prior=deepcopy(e.current)
    e.grant['binding']['recovery_history']=[dict(grant_id=GRANT,grant_sha256=c['grant_sha256'],task_id=NEW,method_identity=prior,
        call_from=3,call_to=3,claim_sha256=recovery._sha(c['claim']))]
    e.grant['grant_id']='e'*32;e.current['source_files']['codex_transport.py']='9'*64;e.current['source_files']['tradingagents_recovery.py']='a'*64
    e.grant['compatibility']['source_hash_pairs']={k:dict(old=e.grant['source_method_identity']['source_files'].get(k),new=e.current['source_files'].get(k))
        for k in e.grant['source_method_identity']['source_files'].keys()|e.current['source_files'].keys()
        if e.grant['source_method_identity']['source_files'].get(k)!=e.current['source_files'].get(k)}
    e.grant['binding'].update(call_counter=3,usage_receipts={str(n):recovery._sha(e.run/'usage-receipts'/f'{n}.json') for n in (1,3)})
    e.auth['unknown_usage_exception'].update(method_sha256=research._digest(e.current),authorized_at=NOW.isoformat())
    e.auth['unknown_usage_exception']['allowed_unknown_usage'].append(dict(usage_id=f'{3:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM))
    e.pin();return c


def test_original_prelaunch_tail_survives_growing_history_and_completed_reader(env):
    e=prelaunch_tail_env(env);first=failed_tail_chain(e);complete_tail_continuation(e)
    c=context(e);claim,fresh=recovery._dispatch(c);assert fresh
    rec=Recorder();rec.task_id='f'*32;owner=claim['native_recovery']['dispatch_owner'];recovery._advance(c,owner,'recovery_queued',task_id=rec.task_id)
    result=asyncio.run(recovery.run_recovery(rec,params(c,owner),e.app))
    assert result['recovery_lineage']['new_call_from']==4 and result['recovery_lineage']['inherited_unknown_usage_count']==2
    assert research._latest(SYMBOL)['recovery_lineage']==result['recovery_lineage'] and recovery._json(first['claim'])['state']=='needs_recovery'
    assert not (e.run/'usage-receipts/2.json').exists() and not (e.run/'model-outputs/2.json').exists()


@pytest.mark.parametrize('mutation',['drop','reseal'])
def test_history_must_keep_exact_original_prelaunch_tail(env,mutation):
    e=prelaunch_tail_env(env);failed_tail_chain(e)
    if mutation=='drop':e.grant['binding'].pop('prelaunch_tail')
    else:e.grant['binding']['prelaunch_tail']['source_model_usage_facts_sha256']='f'*64
    e.pin()
    with pytest.raises(research.ResearchBlocked):context(e)


def test_completed_tail_reader_rechecks_old_db_and_archived_execution(env):
    e=prelaunch_tail_env(env);complete_tail_continuation(e);c,owner=consumed(e)
    asyncio.run(recovery.run_recovery(e.rec,params(c,owner),e.app))
    assert research._latest(SYMBOL) is not None
    with e.sf() as db:
        row=db.get(AgentResourceUsage,f'{1:032x}');purpose=row.purpose;row.purpose='changed after completion';db.commit()
    assert research._latest(SYMBOL) is None
    with e.sf() as db:db.get(AgentResourceUsage,f'{1:032x}').purpose=purpose;db.commit()
    assert research._latest(SYMBOL) is not None
    put(e.run/'recovery-history'/GRANT/'execution.json',dict(state='needs_native_recovery',error_kind='TransportError'))
    assert research._latest(SYMBOL) is None


def input_prelaunch_env(e):
    """Reviewed input check failed before reserve; six known answers already exist."""
    b=e.grant['binding'];old=e.grant['source_method_identity']
    old['source_files'].update({
        'driver.py':'af48f1ce70b37e474e2359daa25eb789ea092693e32147298d6f7d6f6e84c6ee',
        'codex_transport.py':'033af2af08a2c0aca013fb9be301e65bb74a7965a0a4ad6a59a23632bfd48970',
        'agent_budget.py':'5b292c8d675f71e97a86116b6e3250c7a15af8e2bde34ec6492875105c519923',
        'transport_process.py':'ff2812c801be847b6c6e83b0edf109f82fb7fe65ea9077e4473597437af4e592'})
    e.current['source_files'].update({key:old['source_files'][key] for key in ('agent_budget.py','transport_process.py')})
    method=research._digest(old);source=recovery._json(e.source_claim);e.source_claim.unlink()
    source['method_sha256']=method;e.source_claim=research._selection_claim(e.selection,method);put(e.source_claim,source)
    b['source_claim_sha256']=recovery._sha(e.source_claim)
    e.grant['compatibility']['source_hash_pairs']={key:dict(old=old['source_files'].get(key),new=e.current['source_files'].get(key))
        for key in old['source_files'].keys()|e.current['source_files'].keys() if old['source_files'].get(key)!=e.current['source_files'].get(key)}
    roles=('Fundamentals Analyst','News Analyst','Market Analyst','Sentiment Analyst','Fundamentals Analyst','Market Analyst')
    completed=[];records=[]
    for number,role in enumerate(roles,1):
        final=number in (2,4,5);value=dict(content='saved independent output '+str(number),tool_calls=[] if final else [dict(id=f't{number}',name='get_news',arguments={'symbol':SYMBOL})])
        outsha=research._digest(value)
        put(e.run/'model-outputs'/f'{number}.json',dict(call_id=number,role=role,structured_output=value,output_sha256=outsha))
        put(e.run/'usage-receipts'/f'{number}.json',dict(task_id=OLD,method_sha256=method,call_id=number,role=role,
            usage_id=f'{number:032x}',usage_known=True,usage={'input_tokens':number+10,'output_tokens':number+1},terminal_kind='typed_response',output_sha256=outsha))
        records.append(dict(case_id=SYMBOL,arm='ta-luna',status='returned',call_id=number,role=role,response_sha256=outsha))
        if final:completed.append(dict(role=role,call_id=number,output_sha256=outsha))
    (e.run/'role-outputs.jsonl').write_text(''.join(json.dumps({'peer':peer})+'\n' for peer in completed))
    failed=dict(case_id=SYMBOL,arm='ta-luna',status='broker_error',call_id=None,role='Market Analyst',error_type="{'kind': 'BudgetError'}")
    records.append(failed);path=e.run/'graph/model-receipts.jsonl';path.write_text(''.join(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n' for row in records))
    task_error=json.dumps(dict(code='BudgetError',message='模型输入 250349 字符超过 Agent 上限 250000',retryable=False))
    with e.sf() as db:
        for row in db.query(AgentResourceUsage).all():db.delete(row)
        for number,role in enumerate(roles,1):
            db.add(AgentResourceUsage(id=f'{number:032x}',budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,kind='model',
                purpose='tradingagents.'+role.replace(' ','_').lower(),task_id=OLD,provider='codex_cli',model=old['transport']['model'],state='succeeded',attempts=1,
                usage_known=1,input_tokens=number+10,output_tokens=number+1,started_at=(NOW-timedelta(minutes=16)).replace(tzinfo=None),finished_at=(NOW-timedelta(minutes=15)).replace(tzinfo=None)))
        db.add(AgentResourceUsage(id='9'*32,budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,kind='model',purpose='retained_other_task_unknown',
            task_id='8'*32,state='failed',usage_known=0,started_at=(NOW-timedelta(minutes=30)).replace(tzinfo=None),finished_at=(NOW-timedelta(minutes=25)).replace(tzinfo=None)))
        db.get(AgentTask,OLD).error=task_error;db.commit()
        facts=[{column.name:(value.isoformat() if isinstance(value,datetime) else value) for column in AgentResourceUsage.__table__.columns for value in (getattr(row,column.name),)}
            for row in db.query(AgentResourceUsage).filter(AgentResourceUsage.task_id==OLD).order_by(AgentResourceUsage.id).all()]
    put(e.run/'execution.json',dict(state='needs_native_recovery',error_kind='BudgetError',automatic_retry=False))
    put(e.run/'call-counter.json',{'reserved':7})
    cp=e.run/'graph/cache/checkpoints'/f'{SYMBOL}.db'
    native_error=b'\xd97'+b"ProtocolError('Approved broker reported model failure')"
    with sqlite3.connect(cp) as db:
        thread=db.execute('SELECT thread_id FROM checkpoints').fetchone()[0]
        db.execute('UPDATE checkpoints SET metadata=?',(json.dumps({'step':0}),))
        for index,role in enumerate(PARALLEL_ROLES):
            ns=role+':'+str(index)*8+'-1111-1111-1111-111111111111';cid=f'child-{index}'
            step={'Market Analyst':4,'Fundamentals Analyst':3,'Sentiment Analyst':1,'News Analyst':1}[role]
            db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)',(thread,ns,cid,None,'msgpack',b'frozen native messages',json.dumps({'step':step,'parents':{'':'checkpoint-1'}})))
            if role=='Market Analyst':
                db.execute('INSERT INTO writes VALUES(?,?,?,?,?,?,?,?,?)',(thread,ns,cid,'pending-market','~__pregel_pull, wrap_up',-1,'__error__','msgpack',native_error))
                db.execute('INSERT INTO writes VALUES(?,?,?,?,?,?,?,?,?)',(thread,'','checkpoint-1','root-market','~__pregel_pull, Market Analyst',-1,'__error__','msgpack',native_error))
            else:
                channel={'News Analyst':'news_report','Sentiment Analyst':'sentiment_report','Fundamentals Analyst':'fundamentals_report'}[role]
                db.execute('INSERT INTO writes VALUES(?,?,?,?,?,?,?,?,?)',(thread,'','checkpoint-1','root-'+str(index),'~__pregel_pull, '+role,index,channel,'msgpack',b'frozen completed report'))
    e.input_messages=[dict(role='user',content='frozen stock evidence'),dict(role='assistant',content='saved tool proposal',tool_calls=[dict(id='t6',name='get_news',arguments={'symbol':SYMBOL})]),
        dict(role='tool',tool_call_id='t6',name='get_news',status='success',content='frozen available public source')]
    wrap_up=dict(role='user',content='You have used every tool round this report allows. Write your final report now from the tool results above, and say which data you could not retrieve.')
    assert research._digest(wrap_up)=='a6a32b933c43bd17a43fa277e12d9ba9eb480d87008d2289b35c67efe14c0790'
    e.input_request_messages=[*e.input_messages,wrap_up]
    b.update(call_counter=7,checkpoint=recovery.checkpoint_snapshot(e.run,SYMBOL),completed_roles=completed,expected_pending_role='Market Analyst',
        outputs={str(n):recovery._sha(e.run/'model-outputs'/f'{n}.json') for n in range(1,7)},
        usage_receipts={str(n):recovery._sha(e.run/'usage-receipts'/f'{n}.json') for n in range(1,7)},
        prefixes={name:dict(bytes=(e.run/name).stat().st_size,sha256=recovery._sha(e.run/name)) for name in b['prefixes']})
    b['input_prelaunch_tail']=dict(version=1,call_id=7,source_task_error_sha256=hashlib.sha256(task_error.encode()).hexdigest(),
        source_execution_sha256=recovery._sha(e.run/'execution.json'),source_model_usage_facts_sha256=research._digest(facts),
        model_receipts_prefix=dict(bytes=path.stat().st_size,sha256=recovery._sha(path)),first_broker_error_record_sha256=research._digest(failed),
        input_chars=250349,input_limit_chars=250000,pending_message_fingerprints=[research._digest(row) for row in e.input_messages])
    e.auth['unknown_usage_exception'].update(method_sha256=research._digest(e.current),allowed_unknown_usage=[dict(usage_id='9'*32,budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM)])
    e.pin();return e


def test_known_input_prelaunch_tail_preserves_six_answers_without_inventing_seventh(env):
    e=input_prelaunch_env(env);before={p:p.read_bytes() for folder in ('model-outputs','usage-receipts') for p in (e.run/folder).iterdir()}
    c=context(e);assert c['binding']['call_counter']==7 and len(c['binding']['outputs'])==6 and len(c['binding']['completed_roles'])==3
    guard=recovery._pending_guard(c['binding']);guard(dict(role='Market Analyst',messages=e.input_request_messages),7)
    assert all(p.read_bytes()==body for p,body in before.items())
    assert not (e.run/'model-outputs/7.json').exists() and not (e.run/'usage-receipts/7.json').exists()


def input_wrapup_wire(e):
    """Pinned turn.py _as_text output, then the unchanged native codec.

    Upstream turn.py SHA 94fa9514… turns tool calls/results into text only
    for wrap_up. Raw checkpoint fingerprints remain separately authoritative.
    """
    proposal=e.input_messages[1]
    call=proposal['tool_calls'][0]
    tool=e.input_messages[2]
    wire=[deepcopy(e.input_messages[0]),
        dict(role='assistant',content=proposal['content']+'\n[Called '+call['name']+'('+json.dumps(call['arguments'],default=str)+')]'),
        dict(role='user',content='['+tool['name']+' returned]\n'+tool['content']),
        deepcopy(e.input_request_messages[-1])]
    e.grant['binding']['input_wrapup_message_fingerprints']=[research._digest(row) for row in wire]
    return wire


def test_input_wrapup_accepts_reviewed_actual_upstream_text_without_rewriting_checkpoint(env):
    e=input_prelaunch_env(env);raw=deepcopy(e.grant['binding']['input_prelaunch_tail']);wire=input_wrapup_wire(e)
    assert wire[1]!=e.input_messages[1] and wire[2]!=e.input_messages[2]
    recovery._pending_guard(e.grant['binding'])(dict(role='Market Analyst',messages=wire),7)
    assert e.grant['binding']['input_prelaunch_tail']==raw
    e.pin();assert context(e)['binding']['call_counter']==7


def input_wrapup_failed_history(e,monkeypatch):
    """Original grant rejected real _as_text before reserve; preserve that failure."""
    e=input_prelaunch_env(e);wire=input_wrapup_wire(e)
    e.grant['binding'].pop('input_wrapup_message_fingerprints');e.pin()
    original_execute=e.native._execute;c,owner=consumed(e)
    async def failed(*,recovery_guard,**kwargs):
        recovery_guard(dict(role='Market Analyst',messages=wire),7)
        raise AssertionError('old raw-message guard must reject actual wrap_up')
    monkeypatch.setattr(e.native,'_execute',failed)
    with pytest.raises(research.ResearchBlocked) as error:
        asyncio.run(recovery.run_recovery(e.rec,params(c,owner),e.app))
    assert error.value.code=='recovery_checkpoint_messages_drift'
    diagnostic=recovery._json(e.run/'execution.json')
    assert diagnostic['error_code']=='recovery_checkpoint_messages_drift'
    assert recovery._json(e.run/'call-counter.json')=={'reserved':7}
    with e.sf() as db:
        assert db.query(AgentResourceUsage).filter(AgentResourceUsage.task_id==NEW).count()==0
        db.add(AgentTask(id=NEW,type='tradingagents_research',status='failed',params=json.dumps(params(c,owner)),
            error=json.dumps(dict(code='ResearchBlocked',message=str(error.value),retryable=False)),finished_at=NOW.replace(tzinfo=None)));db.commit()
    previous=deepcopy(e.current)
    history=dict(grant_id=GRANT,grant_sha256=c['grant_sha256'],task_id=NEW,method_identity=previous,
        call_from=8,call_to=7,claim_sha256=recovery._sha(c['claim']))
    e.current['source_files']['tradingagents_recovery.py']='f'*64
    e.grant['grant_id']='e'*32
    e.grant['compatibility']['source_hash_pairs']={key:dict(old=e.grant['source_method_identity']['source_files'].get(key),new=e.current['source_files'].get(key))
        for key in e.grant['source_method_identity']['source_files'].keys()|e.current['source_files'].keys()
        if e.grant['source_method_identity']['source_files'].get(key)!=e.current['source_files'].get(key)}
    e.grant['binding'].update(recovery_history=[history],checkpoint=recovery.checkpoint_snapshot(e.run,SYMBOL),
        worker_terminal_sha256=recovery._sha(e.run/'worker-terminal.json'))
    input_wrapup_wire(e)
    e.auth['unknown_usage_exception']['method_sha256']=research._digest(e.current)
    e.native.resume_counter=7;e.native.first_role='Market Analyst'
    async def continuation(*,recovery_guard,**kwargs):
        def checked(request,counter):recovery_guard(dict(request,messages=wire),counter)
        return await original_execute(recovery_guard=checked,**kwargs)
    monkeypatch.setattr(e.native,'_execute',continuation);e.pin()
    return e,c,history,wire


def test_input_wrapup_zero_call_failure_history_continues_at_eight_preserving_six_answers(env,monkeypatch):
    e,first,history,_=input_wrapup_failed_history(env,monkeypatch)
    originals={p:p.read_bytes() for p in [e.source_claim,first['claim'],
        *(e.run/folder/f'{n}.json' for folder in ('model-outputs','usage-receipts') for n in range(1,7)),
        *(e.run/'recovery-history'/GRANT/name for name in ('grant.json','execution.json','worker-terminal.json','start.json'))]}
    c=context(e);assert len(c['segments'])==2 and c['segments'][1]['call_from']==8 and c['segments'][1]['call_to']==7
    claim,fresh=recovery._dispatch(c);assert fresh
    owner=claim['native_recovery']['dispatch_owner'];rec=Recorder();rec.task_id='1'*32
    recovery._advance(c,owner,'recovery_queued',task_id=rec.task_id)
    result=asyncio.run(recovery.run_recovery(rec,params(c,owner),e.app))
    assert result['recovery_lineage']['new_call_from']==8 and result['recovery_lineage']['reused_original_outputs']==6
    assert result['recovery_lineage']['inherited_unknown_usage_count']==0
    assert all(p.read_bytes()==raw for p,raw in originals.items())
    assert not (e.run/'model-outputs/7.json').exists() and not (e.run/'usage-receipts/7.json').exists()
    assert research._latest(SYMBOL)['recovery_lineage']==result['recovery_lineage']
    assert e.grant['binding']['recovery_history']==[history]


@pytest.mark.parametrize('mutation',['role','message','order','wrap_up','counter','raw_checkpoint','missing_wire','length','bad_hash','wrong_wrap_hash'])
def test_input_wrapup_reviewed_wire_never_accepts_changed_role_messages_or_binding(env,mutation):
    e=input_prelaunch_env(env);wire=input_wrapup_wire(e);b=e.grant['binding'];request=dict(role='Market Analyst',messages=deepcopy(wire));counter=7
    if mutation=='role':request['role']='Fundamentals Analyst'
    elif mutation=='message':request['messages'][2]['content']='different source'
    elif mutation=='order':request['messages'][0],request['messages'][1]=request['messages'][1],request['messages'][0]
    elif mutation=='wrap_up':request['messages'][-1]['content']='write fresh research'
    elif mutation=='counter':counter=6
    elif mutation=='raw_checkpoint':request['messages']=e.input_request_messages
    elif mutation=='missing_wire':b.pop('input_wrapup_message_fingerprints')
    elif mutation=='length':b['input_wrapup_message_fingerprints'].pop(0)
    elif mutation=='bad_hash':b['input_wrapup_message_fingerprints'][0]=False
    else:b['input_wrapup_message_fingerprints'][-1]='f'*64
    with pytest.raises(research.ResearchBlocked):
        recovery._binding(b);recovery._pending_guard(b)(request,counter)
    assert e.native.calls==0


@pytest.mark.parametrize('mutation',['paid_range','source_range','extra_reserved','extra_started','extra_known','extra_unknown','running','canceled',
    'wrong_error','wrong_execution','wrong_recovery_task','changed_raw_tail','changed_old_answer','changed_native_error','unindexed_answer','unindexed_receipt','missing_grant','missing_old_wire'])
def test_input_wrapup_zero_call_history_cannot_hide_paid_or_unbound_attempt(env,monkeypatch,mutation):
    e,_,entry,_=input_wrapup_failed_history(env,monkeypatch);b=e.grant['binding']
    if mutation=='paid_range':entry['call_to']=8;b['call_counter']=8
    elif mutation=='source_range':entry['call_from']=7
    elif mutation.startswith('extra_'):
        state={'extra_reserved':'reserved','extra_started':'started','extra_known':'succeeded','extra_unknown':'failed'}[mutation]
        with e.sf() as db:
            db.add(AgentResourceUsage(id='7'*32,budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_SELECTED_RESEARCH_LLM,
                kind='model',purpose='must not be hidden',task_id=NEW,state=state,usage_known=int(mutation=='extra_known'),
                started_at=None if state=='reserved' else NOW.replace(tzinfo=None)));db.commit()
    elif mutation in {'running','canceled','wrong_error'}:
        with e.sf() as db:
            task=db.get(AgentTask,NEW)
            if mutation=='wrong_error':task.error=json.dumps(dict(code='TransportError'))
            else:task.status=mutation
            db.commit()
    elif mutation in {'wrong_execution','wrong_recovery_task'}:
        value=recovery._json(e.run/'execution.json');value['error_kind' if mutation=='wrong_execution' else 'recovery_task_id']='BudgetError' if mutation=='wrong_execution' else '2'*32
        put(e.run/'execution.json',value)
    elif mutation=='changed_raw_tail':b['input_prelaunch_tail']['pending_message_fingerprints'][0]='e'*64
    elif mutation=='changed_old_answer':
        put(e.run/'model-outputs/1.json',dict(call_id=1,role='Fundamentals Analyst',structured_output={'content':'changed'}));b['outputs']['1']=recovery._sha(e.run/'model-outputs/1.json')
    elif mutation=='changed_native_error':
        with sqlite3.connect(e.run/'graph/cache/checkpoints'/f'{SYMBOL}.db') as db:db.execute("UPDATE writes SET value=? WHERE channel='__error__'",(b'changed native failure',))
        b['checkpoint']=recovery.checkpoint_snapshot(e.run,SYMBOL)
    elif mutation in {'unindexed_answer','unindexed_receipt'}:
        put(e.run/('model-outputs' if mutation=='unindexed_answer' else 'usage-receipts')/'8.json',dict(unbound=True))
    elif mutation=='missing_grant':(e.run/'recovery-history'/GRANT/'grant.json').unlink()
    else:b.pop('input_wrapup_message_fingerprints')
    e.pin()
    with pytest.raises(research.ResearchBlocked):context(e)
    assert e.native.calls==0 and recovery._json(e.run/'call-counter.json')=={'reserved':7}


def test_input_wrapup_authorizer_rechecks_expiry_before_each_new_call(env,monkeypatch):
    e=input_prelaunch_env(env);input_wrapup_wire(e);e.pin();c,owner=consumed(e)
    recovery._advance(c,owner,'recovering',task_id=NEW)
    assert recovery._authorizer(c,params(c,owner)['native_recovery'],NEW,e.app,e.sf)
    monkeypatch.setattr(research,'_now',lambda:NOW+timedelta(hours=1))
    with pytest.raises(research.ResearchBlocked) as error:
        recovery._authorizer(c,params(c,owner)['native_recovery'],NEW,e.app,e.sf)
    assert error.value.code=='recovery_grant_expired' and e.native.calls==0


def test_known_input_prelaunch_continuation_starts_at_eight_without_replaying_completed_roles(env):
    e=input_prelaunch_env(env);before={p:p.read_bytes() for folder in ('model-outputs','usage-receipts') for p in (e.run/folder).iterdir()}
    e.native.resume_counter=7;e.native.first_role='Market Analyst';original_execute=e.native._execute
    async def continuation(*,recovery_guard,**kwargs):
        def checked(request,counter):recovery_guard(dict(request,messages=e.input_request_messages),counter)
        return await original_execute(recovery_guard=checked,**kwargs)
    e.native._execute=continuation;c,owner=consumed(e)
    result=asyncio.run(recovery.run_recovery(e.rec,params(c,owner),e.app))
    assert result['recovery_lineage']['new_call_from']==8 and result['recovery_lineage']['reused_original_outputs']==6 and result['recovery_lineage']['inherited_unknown_usage_count']==0
    assert all(p.read_bytes()==body for p,body in before.items())
    assert not (e.run/'model-outputs/7.json').exists() and not (e.run/'usage-receipts/7.json').exists()
    assert research._latest(SYMBOL)['recovery_lineage']==result['recovery_lineage']


def reseal_input_source_facts(e):
    tail=e.grant['binding']['input_prelaunch_tail']
    with e.sf() as db:
        facts=[{column.name:(value.isoformat() if isinstance(value,datetime) else value) for column in AgentResourceUsage.__table__.columns for value in (getattr(row,column.name),)}
            for row in db.query(AgentResourceUsage).filter(AgentResourceUsage.task_id==OLD,AgentResourceUsage.kind=='model').order_by(AgentResourceUsage.id).all()]
        tail['source_model_usage_facts_sha256']=research._digest(facts)
        tail['source_task_error_sha256']=hashlib.sha256(db.get(AgentTask,OLD).error.encode()).hexdigest()


@pytest.mark.parametrize('key',['driver.py','codex_transport.py','agent_budget.py','transport_process.py'])
def test_input_prelaunch_accepts_only_reviewed_check_before_reserve_sources(env,key):
    e=input_prelaunch_env(env);e.grant['source_method_identity']['source_files'][key]='f'*64
    with pytest.raises(research.ResearchBlocked):
        recovery._input_prelaunch_tail(e.grant,e.run,recovery._history_segments(e.grant,e.run,e.sf),e.sf)


@pytest.mark.parametrize('mutation',['undeclared','old_tail','history','bool_counter','tail_count','input_bool','input_zero','input_not_over_limit','input_too_large',
    'unknown_error','loose_input_text','error_code','execution','worker_success','pending_role','completed_role','empty_fingerprints','invalid_fingerprint','receipt7','output7','missing_old_output'])
def test_input_prelaunch_requires_exact_declared_terminal_and_input_rejection(env,mutation):
    e=input_prelaunch_env(env);b=e.grant['binding'];tail=b['input_prelaunch_tail']
    if mutation=='undeclared':b.pop('input_prelaunch_tail')
    elif mutation=='old_tail':b['prelaunch_tail']={key:value for key,value in tail.items() if key in recovery.PRELAUNCH_TAIL_KEYS}
    elif mutation=='history':b['recovery_history']=[{}]
    elif mutation=='bool_counter':b['call_counter']=True
    elif mutation=='tail_count':tail['call_id']=6
    elif mutation=='input_bool':tail['input_chars']=True
    elif mutation=='input_zero':tail['input_limit_chars']=0
    elif mutation=='input_not_over_limit':tail['input_chars']=tail['input_limit_chars']
    elif mutation=='input_too_large':tail['input_chars']=recovery.MAX_BYTES+1
    elif mutation in {'unknown_error','loose_input_text','error_code'}:
        with e.sf() as db:
            task=db.get(AgentTask,OLD);error=json.loads(task.error)
            if mutation=='unknown_error':error['message']='今日已有模型调用的 token 用量未知；拒绝继续消耗，须先核实/跨日恢复'
            elif mutation=='loose_input_text':error['message']='prefix '+error['message']+' input'
            else:error['code']='TransportError'
            task.error=json.dumps(error);db.commit()
        reseal_input_source_facts(e)
    elif mutation=='execution':
        put(e.run/'execution.json',dict(state='needs_native_recovery',error_kind='TransportError',automatic_retry=False));tail['source_execution_sha256']=recovery._sha(e.run/'execution.json')
    elif mutation=='worker_success':
        put(e.run/'worker-terminal.json',{'exit_code':0});b['worker_terminal_sha256']=recovery._sha(e.run/'worker-terminal.json')
    elif mutation=='pending_role':b['expected_pending_role']='Bull Researcher'
    elif mutation=='completed_role':b['completed_roles'][0]['role']='Market Analyst'
    elif mutation=='empty_fingerprints':tail['pending_message_fingerprints']=[]
    elif mutation=='invalid_fingerprint':tail['pending_message_fingerprints'][0]='not-a-sha'
    elif mutation=='receipt7':put(e.run/'usage-receipts/7.json',{'usage_known':False})
    elif mutation=='output7':put(e.run/'model-outputs/7.json',{'structured_output':{}})
    else:(e.run/'model-outputs/3.json').unlink()
    e.pin()
    with pytest.raises(research.ResearchBlocked):context(e)
    assert e.native.calls==0


@pytest.mark.parametrize('mutation',['extra_reserved','extra_started','usage_unknown','tokens','purpose','provider','model','attempts','retries','scope','date','state','started','finished'])
def test_input_prelaunch_full_db_closure_rejects_even_resealed_reservations_and_usage_drift(env,mutation):
    e=input_prelaunch_env(env)
    with e.sf() as db:
        row=db.get(AgentResourceUsage,f'{1:032x}')
        if mutation.startswith('extra_'):
            db.add(AgentResourceUsage(id='7'*32,budget_date=NOW.date().isoformat(),scope=agent_budget.SCOPE_AUTONOMY_LLM,kind='model',purpose='extra unaccounted source call',
                task_id=OLD,state='reserved' if mutation=='extra_reserved' else 'started',usage_known=0,started_at=None if mutation=='extra_reserved' else NOW.replace(tzinfo=None)))
        elif mutation=='usage_unknown':row.usage_known=0
        elif mutation=='tokens':row.input_tokens+=1
        elif mutation=='purpose':row.purpose='other'
        elif mutation=='provider':row.provider='other'
        elif mutation=='model':row.model='other'
        elif mutation=='attempts':row.attempts=2
        elif mutation=='retries':row.max_retries=1
        elif mutation=='scope':row.scope=agent_budget.SCOPE_AUTONOMY_LLM
        elif mutation=='date':row.budget_date='2026-10-20'
        elif mutation=='state':row.state='failed'
        elif mutation=='started':row.started_at=None
        else:row.finished_at=None
        db.commit()
    reseal_input_source_facts(e)
    with pytest.raises(research.ResearchBlocked):recovery._input_prelaunch_tail(e.grant,e.run,recovery._history_segments(e.grant,e.run,e.sf),e.sf)


@pytest.mark.parametrize('mutation',['known_false','usage_none','usage_bool','usage_string','negative_usage','duplicate_id','receipt_call_bool','output_role','output_call_bool','saved_final_market'])
def test_input_prelaunch_receipts_and_original_outputs_must_be_strict_and_closed(env,mutation):
    e=input_prelaunch_env(env);b=e.grant['binding'];path=e.run/'usage-receipts/6.json';usage=recovery._json(path);outpath=e.run/'model-outputs/6.json';output=recovery._json(outpath)
    if mutation=='known_false':usage['usage_known']=False
    elif mutation=='usage_none':usage['usage']=None
    elif mutation=='usage_bool':usage['usage']['input_tokens']=True
    elif mutation=='usage_string':usage['usage']['input_tokens']='16'
    elif mutation=='negative_usage':usage['usage']['input_tokens']=-1
    elif mutation=='duplicate_id':usage['usage_id']=f'{5:032x}'
    elif mutation=='receipt_call_bool':usage['call_id']=True
    elif mutation=='output_role':output['role']='Trader'
    elif mutation=='output_call_bool':output['call_id']=True
    else:
        output['structured_output']['tool_calls']=[];output['output_sha256']=research._digest(output['structured_output']);usage['output_sha256']=output['output_sha256']
    put(path,usage);put(outpath,output);b['usage_receipts']['6']=recovery._sha(path);b['outputs']['6']=recovery._sha(outpath)
    e.pin()
    with pytest.raises(research.ResearchBlocked):context(e)


@pytest.mark.parametrize('mutation',['error_type','error_role','error_call','missing_return','duplicate_return','returned_role','returned_sha','trailing_error'])
def test_input_prelaunch_broker_stream_cannot_infer_nonlaunch_from_a_generic_error(env,mutation):
    e=input_prelaunch_env(env);tail=e.grant['binding']['input_prelaunch_tail'];path=e.run/'graph/model-receipts.jsonl';rows=[json.loads(line) for line in path.read_text().splitlines()]
    if mutation=='error_type':rows[-1]['error_type']="{'kind': 'TransportError'}"
    elif mutation=='error_role':rows[-1]['role']='News Analyst'
    elif mutation=='error_call':rows[-1]['call_id']=7
    elif mutation=='missing_return':rows.pop(0)
    elif mutation=='duplicate_return':rows[0]=deepcopy(rows[1])
    elif mutation=='returned_role':rows[0]['role']='Market Analyst'
    elif mutation=='returned_sha':rows[0]['response_sha256']='f'*64
    else:rows.append(dict(rows[-1],error_type="{'kind': 'DriverUnavailable'}"))
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows));tail['model_receipts_prefix']=dict(bytes=path.stat().st_size,sha256=recovery._sha(path));tail['first_broker_error_record_sha256']=research._digest(rows[-1]);e.pin()
    with pytest.raises(research.ResearchBlocked):context(e)


@pytest.mark.parametrize('mutation',['root_step','pending_node','pending_error','missing_report','completed_error','native_parent','native_step','missing_branch'])
def test_input_prelaunch_requires_native_three_results_and_market_wrapup_checkpoint(env,mutation):
    e=input_prelaunch_env(env);b=e.grant['binding'];cp=e.run/'graph/cache/checkpoints'/f'{SYMBOL}.db'
    with sqlite3.connect(cp) as db:
        if mutation=='root_step':db.execute("UPDATE checkpoints SET metadata=? WHERE checkpoint_ns=''",(json.dumps({'step':1}),))
        elif mutation=='pending_node':db.execute("UPDATE writes SET task_path='~__pregel_pull, agent' WHERE task_path='~__pregel_pull, wrap_up'")
        elif mutation=='pending_error':db.execute("UPDATE writes SET value=? WHERE task_path='~__pregel_pull, wrap_up'",(b'another failure',))
        elif mutation=='missing_report':db.execute("DELETE FROM writes WHERE channel='news_report'")
        elif mutation=='completed_error':
            thread,ns,cid=db.execute("SELECT thread_id,checkpoint_ns,checkpoint_id FROM checkpoints WHERE checkpoint_ns LIKE 'News Analyst:%'").fetchone()
            db.execute('INSERT INTO writes VALUES(?,?,?,?,?,?,?,?,?)',(thread,ns,cid,'bad','~__pregel_pull, agent',-1,'__error__','msgpack',b'failed'))
        elif mutation in {'native_parent','native_step'}:
            meta=json.loads(db.execute("SELECT metadata FROM checkpoints WHERE checkpoint_ns LIKE 'Market Analyst:%'").fetchone()[0]);meta['parents']={'':'other'} if mutation=='native_parent' else meta['parents'];meta['step']=5 if mutation=='native_step' else meta['step']
            db.execute("UPDATE checkpoints SET metadata=? WHERE checkpoint_ns LIKE 'Market Analyst:%'",(json.dumps(meta),))
        else:db.execute("DELETE FROM checkpoints WHERE checkpoint_ns LIKE 'News Analyst:%'")
    b['checkpoint']=recovery.checkpoint_snapshot(e.run,SYMBOL);e.pin()
    with pytest.raises(research.ResearchBlocked):context(e)


@pytest.mark.parametrize('mutation',['role','counter','messages','missing_messages','missing_wrap_up','changed_wrap_up','completed_role_after_first'])
def test_input_prelaunch_first_request_uses_frozen_market_messages_and_never_replays_three_finals(env,mutation):
    e=input_prelaunch_env(env);guard=recovery._pending_guard(context(e)['binding']);request=dict(role='Market Analyst',messages=deepcopy(e.input_request_messages));counter=7
    if mutation=='role':request['role']='News Analyst'
    elif mutation=='counter':counter=6
    elif mutation=='messages':request['messages'][-2]['content']='different source'
    elif mutation=='missing_messages':request.pop('messages')
    elif mutation=='missing_wrap_up':request['messages'].pop()
    elif mutation=='changed_wrap_up':request['messages'][-1]['content']='write a fresh report'
    else:
        guard(request,7);request['role']='Fundamentals Analyst';counter=8
    with pytest.raises(research.ResearchBlocked):guard(request,counter)
    assert not (e.run/'usage-receipts/7.json').exists()


def test_input_prelaunch_offline_wal_reads_create_no_sidecars_or_context_files(env):
    e=input_prelaunch_env(env);b=e.grant['binding'];cp=e.run/'graph/cache/checkpoints'/f'{SYMBOL}.db'
    with sqlite3.connect(cp) as db:assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    b['checkpoint']['files']={'db':recovery._sha(cp),'-wal':None,'-shm':None}
    b['checkpoint']=recovery.checkpoint_snapshot(e.run,SYMBOL,offline={'grant':e.grant,'session_factory':e.sf});e.pin()
    original={p.relative_to(e.run):p.read_bytes() for p in e.run.rglob('*') if p.is_file()}
    context(e);context(e)
    assert {p.relative_to(e.run):p.read_bytes() for p in e.run.rglob('*') if p.is_file()}==original
    assert not (cp.with_name(cp.name+'-wal')).exists() and not (cp.with_name(cp.name+'-shm')).exists()


def frozen_packet_owner_env(e):
    e=input_prelaunch_env(e);old=e.grant['source_method_identity'];b=e.grant['binding']
    before='8ef2086885ae17cf52a5c720705251a2b956b91f401ca8604b655182053308bf'
    after='5d970afc948ea70e8d5ef3cc6a1efe68d2a3df09d3044fc1c92c405c864db0b0'
    old['packet_builder']=dict(kind='frozen-free-source-fixture',implicit_collection=False,history_calendar_days=400,
        owner_sources={'services/quote_hub.py':before,'schemas/market.py':'c'*64})
    e.current['packet_builder']=deepcopy(old['packet_builder']);e.current['packet_builder']['owner_sources']['services/quote_hub.py']=after
    source=recovery._json(e.source_claim);e.source_claim.unlink();source['method_sha256']=research._digest(old)
    e.source_claim=research._selection_claim(e.selection,source['method_sha256']);put(e.source_claim,source);b['source_claim_sha256']=recovery._sha(e.source_claim)
    for number in b['usage_receipts']:
        path=e.run/'usage-receipts'/(number+'.json');usage=recovery._json(path);usage['method_sha256']=source['method_sha256'];put(path,usage);b['usage_receipts'][number]=recovery._sha(path)
    e.grant['compatibility'].update(kind='reviewed_frozen_packet_owner_v1',packet_owner_hash_pairs={'services/quote_hub.py':dict(old=before,new=after)})
    e.auth['unknown_usage_exception']['method_sha256']=research._digest(e.current)
    e.pin();return e


@pytest.mark.parametrize('two_owners',[False,True])
def test_frozen_packet_owner_continuation_uses_exact_saved_packet_and_never_calls_builder(env,monkeypatch,two_owners):
    e=(frozen_packet_two_owner_env if two_owners else frozen_packet_owner_env)(env);packet=(e.run/'packet.json').read_bytes();originals={p:p.read_bytes() for folder in ('model-outputs','usage-receipts') for p in (e.run/folder).iterdir()}
    async def no_builder(*args,**kwargs):raise AssertionError('frozen continuation must never recollect or build new input')
    monkeypatch.setattr(driver.StoredCompanyPacketBuilder,'__call__',no_builder);e.native.packet_builder=no_builder
    e.native.resume_counter=7;e.native.first_role='Market Analyst';execute=e.native._execute
    async def continuation(*,recovery_guard,**kwargs):
        def checked(request,counter):recovery_guard(dict(request,messages=e.input_request_messages),counter)
        return await execute(recovery_guard=checked,**kwargs)
    e.native._execute=continuation;c,owner=consumed(e);result=asyncio.run(recovery.run_recovery(e.rec,params(c,owner),e.app))
    assert result['recovery_lineage']['new_call_from']==8 and result['recovery_lineage']['reused_original_outputs']==6
    assert (e.run/'packet.json').read_bytes()==packet and all(p.read_bytes()==body for p,body in originals.items())
    assert research._latest(SYMBOL)['recovery_lineage']==result['recovery_lineage']


@pytest.mark.parametrize('mutation',['default_kind','missing_pair','extra_pair','future_pair','reverse_pair','missing_old_owner','missing_new_owner','other_owner','new_owner',
    'packet_field','packet_kind','model','graph','dependencies','missing_binding','missing_tail','bad_packet_sha','bad_config_sha','bad_staged_sha','bad_manifest_sha','source_mapping'])
def test_frozen_packet_owner_transition_is_one_forward_pair_only_for_a_bound_old_packet(env,mutation):
    e=frozen_packet_owner_env(env);grant=e.grant;compat=grant['compatibility'];old=grant['source_method_identity'];new=e.current
    if mutation=='default_kind':compat.update(kind='reviewed_host_diagnostics_and_recovery_v1');compat.pop('packet_owner_hash_pairs')
    elif mutation=='missing_pair':compat.pop('packet_owner_hash_pairs')
    elif mutation=='extra_pair':compat['packet_owner_hash_pairs']['schemas/market.py']=dict(old='c'*64,new='c'*64)
    elif mutation=='future_pair':new['packet_builder']['owner_sources']['services/quote_hub.py']='f'*64;compat['packet_owner_hash_pairs']['services/quote_hub.py']['new']='f'*64
    elif mutation=='reverse_pair':
        owners=(old['packet_builder']['owner_sources'],new['packet_builder']['owner_sources']);owners[0]['services/quote_hub.py'],owners[1]['services/quote_hub.py']=owners[1]['services/quote_hub.py'],owners[0]['services/quote_hub.py']
        pair=compat['packet_owner_hash_pairs']['services/quote_hub.py'];pair['old'],pair['new']=pair['new'],pair['old']
    elif mutation=='missing_old_owner':old['packet_builder']['owner_sources'].pop('services/quote_hub.py')
    elif mutation=='missing_new_owner':new['packet_builder']['owner_sources'].pop('services/quote_hub.py')
    elif mutation=='other_owner':new['packet_builder']['owner_sources']['schemas/market.py']='f'*64
    elif mutation=='new_owner':new['packet_builder']['owner_sources']['services/unreviewed.py']='f'*64
    elif mutation=='packet_field':new['packet_builder']['history_calendar_days']=401
    elif mutation=='packet_kind':new['packet_builder']['kind']='new input builder'
    elif mutation=='model':new['transport']['model']='another-model'
    elif mutation=='graph':new['upstream_sha']='f'*40
    elif mutation=='dependencies':new['dependencies']['frozen']='f'*64
    elif mutation=='missing_binding':grant.pop('binding')
    elif mutation=='missing_tail':grant['binding'].pop('input_prelaunch_tail')
    elif mutation.startswith('bad_'):
        key={'bad_packet_sha':'packet_sha256','bad_config_sha':'config_sha256','bad_staged_sha':'staged_code_sha256','bad_manifest_sha':'manifest_sha256'}[mutation];grant['binding'][key]='unbound'
    else:compat['source_hash_pairs'].pop('driver.py')
    with pytest.raises(research.ResearchBlocked):recovery._compatibility(grant,new)


def test_frozen_packet_owner_compatibility_is_readonly_and_old_kind_stays_strict(env):
    e=frozen_packet_owner_env(env);before=deepcopy(e.grant)
    assert recovery._compatibility(e.grant,e.current)==(research._digest(e.grant['source_method_identity']),research._digest(e.current))
    assert e.grant==before
    e.grant['compatibility'].pop('packet_owner_hash_pairs');e.grant['compatibility']['kind']='reviewed_host_diagnostics_and_recovery_v1'
    with pytest.raises(research.ResearchBlocked):recovery._compatibility(e.grant,e.current)


def test_frozen_packet_owner_does_not_accept_replaced_or_recaptured_packet(env):
    e=frozen_packet_owner_env(env);put(e.run/'packet.json',dict(recovery._json(e.run/'packet.json'),as_of=NOW.isoformat()));e.pin()
    with pytest.raises(research.ResearchBlocked):context(e)
    assert e.native.calls==0


def frozen_packet_two_owner_env(e):
    e=frozen_packet_owner_env(e);old=e.grant['source_method_identity'];b=e.grant['binding'];name='data_providers/tencent.py'
    pair=dict(old='69d6380eab0e4ffc228d08391a8a815bfb37e304ee9cac70271d04ed58bc3a43',new='32699b46cc03f0038e8e9d4969461bd336d9465c0363f984f8dca56b0b828aa9')
    old['packet_builder']['owner_sources'][name]=pair['old'];e.current['packet_builder']['owner_sources'][name]=pair['new']
    e.grant['compatibility']['packet_owner_hash_pairs'][name]=pair
    source=recovery._json(e.source_claim);e.source_claim.unlink();source['method_sha256']=research._digest(old)
    e.source_claim=research._selection_claim(e.selection,source['method_sha256']);put(e.source_claim,source);b['source_claim_sha256']=recovery._sha(e.source_claim)
    for number in b['usage_receipts']:
        path=e.run/'usage-receipts'/(number+'.json');usage=recovery._json(path);usage['method_sha256']=source['method_sha256'];put(path,usage);b['usage_receipts'][number]=recovery._sha(path)
    e.auth['unknown_usage_exception']['method_sha256']=research._digest(e.current);e.pin();return e


@pytest.mark.parametrize('mutation',['missing_tencent_pair','missing_quote_pair','unchanged_tencent_pair','empty_changes','future_tencent','reverse_tencent','no_old_tencent','other_owner'])
def test_frozen_packet_owner_pairs_exactly_match_nonempty_reviewed_changes(env,mutation):
    e=frozen_packet_two_owner_env(env);compat=e.grant['compatibility'];old=e.grant['source_method_identity']['packet_builder']['owner_sources'];new=e.current['packet_builder']['owner_sources'];name='data_providers/tencent.py'
    if mutation=='missing_tencent_pair':compat['packet_owner_hash_pairs'].pop(name)
    elif mutation=='missing_quote_pair':compat['packet_owner_hash_pairs'].pop('services/quote_hub.py')
    elif mutation=='unchanged_tencent_pair':new[name]=old[name]
    elif mutation=='empty_changes':
        e.current['packet_builder']['owner_sources']=deepcopy(old);compat['packet_owner_hash_pairs']={}
    elif mutation=='future_tencent':new[name]='f'*64;compat['packet_owner_hash_pairs'][name]['new']='f'*64
    elif mutation=='reverse_tencent':
        old[name],new[name]=new[name],old[name];pair=compat['packet_owner_hash_pairs'][name];pair['old'],pair['new']=pair['new'],pair['old']
    elif mutation=='no_old_tencent':old.pop(name);compat['packet_owner_hash_pairs'][name]['old']=None
    else:new['events/company_state.py']='f'*64;compat['packet_owner_hash_pairs']['events/company_state.py']=dict(old=None,new='f'*64)
    with pytest.raises(research.ResearchBlocked):recovery._compatibility(e.grant,e.current)


def test_frozen_search_owner_pair_refuses_an_unreviewed_quote_function_change(env,tmp_path):
    import ast
    from pathlib import Path
    from app.data_providers import tencent
    e=frozen_packet_two_owner_env(env);path=Path(tencent.__file__);body=path.read_text();tree=ast.parse(body)
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='TencentProvider')
    function=next(node for node in cls.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='get_quotes')
    lines=body.splitlines(keepends=True);lines.insert(function.body[0].lineno-1,"        raise RuntimeError('unreviewed quote change')\n")
    candidate=tmp_path/'tampered_tencent.py';candidate.write_text(''.join(lines));ast.parse(candidate.read_text())
    changed=hashlib.sha256(candidate.read_bytes()).hexdigest();assert changed!=hashlib.sha256(path.read_bytes()).hexdigest()
    e.current['packet_builder']['owner_sources']['data_providers/tencent.py']=changed
    e.grant['compatibility']['packet_owner_hash_pairs']['data_providers/tencent.py']['new']=changed
    with pytest.raises(research.ResearchBlocked):recovery._compatibility(e.grant,e.current)
