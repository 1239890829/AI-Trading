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
