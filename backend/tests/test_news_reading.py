from datetime import datetime
from types import SimpleNamespace as NS
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.events.reading import saved_selection_context, selection_links
from app.events.store import EventStore
from app.models.daily_pick import DailyPickSet
from app.models.opportunity_learning import OpportunityDecisionRun, OpportunityDecisionSnapshot
from app.models.watchlist import Base
from app.picks.strategy_registry import saved_method_runs

NOW = datetime(2026, 10, 25, 12, tzinfo=BJ_TZ)

@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reading.db'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def test_saved_dates_exact_event_identity_and_no_model_relation(sf):
    with sf() as db:
        db.add(DailyPickSet(date='2026-10-23', items=json.dumps([{'symbol':'600000','name':'示例'}]),
            meta=json.dumps({'selection_version':'v1','selection_entries':{'600000':{'state':'recorded',
                'event_refs':[{'event_id':7,'version_id':3,'observation_id':2}]}}})))
        db.add(DailyPickSet(date='2099-01-01',items='[{"symbol":"600001"}]'))
        db.commit()
    context=saved_selection_context(sf,now=NOW)
    assert [(x['symbol'],x['trade_date']) for x in context['items']]==[('600000','2026-10-23')]
    assert selection_links({'id':7,'directions':[]},context)[0]['relation']=='original_evidence'
    assert selection_links({'id':8,'directions':[{'target_type':'symbol','target':'600000','matched_by':'llm_aux'}]},context)==[]
    link=selection_links({'id':8,'source_symbol':'600000'},context)[0]
    assert link['relation']=='same_symbol' and link['original_event_refs']==[]
    assert context['state']=='partial'


def test_runtime_projects_real_zero_score_and_unknown_counts(sf,monkeypatch):
    monkeypatch.setattr('app.core.bjtime.beijing_now',lambda:NOW)
    with sf() as db:
        db.add(DailyPickSet(date='2026-10-23',items='[{"symbol":"600000","score":0}]',
            rejected='[{"symbol":"600001","score":false,"reason":"缺项"}]',
            meta=json.dumps({'selection_version':'v1','selection_funnel':{'scored':2}})))
        db.commit()
    run=saved_method_runs(sf)['daily_picks']
    assert run['trade_date']=='2026-10-23' and run['source_version']=='v1'
    assert run['counts']=={'candidates':None,'scored':2,'selected':1,'rejected_saved':1}
    assert run['items'][0]['score']==0 and run['items'][1]['score'] is None
    assert run['run_at'] is None and run['elapsed_ms'] is None


def test_only_committed_within_capacity_intraday_rows_are_related(sf):
    with sf() as db:
        db.add(OpportunityDecisionRun(run_id='r1',trade_date='2026-10-23',as_of=datetime(2026,10,23,10),
            scenario='intraday_opportunity',strategy_version='s1',feature_version='f1',evidence_digest='fixture'))
        for i,within in enumerate([True,False]):
            db.add(OpportunityDecisionSnapshot(snapshot_id=f's{i}',run_id='r1',trade_date='2026-10-23',
                as_of=datetime(2026,10,23,10),scenario='intraday_opportunity',stage='rank',symbol=f'60000{i}',
                decision='ranked',rank=i+1,strategy_version='s1',feature_version='f1',
                evidence=json.dumps({'effective_limit':8,'within_display_capacity':within})))
        db.commit()
    context=saved_selection_context(sf,now=NOW)
    assert [x['symbol'] for x in context['items']]==['600000']
    assert context['items'][0]['source_version']=='r1'


def test_reading_contract_does_not_read_live_market_or_extend_opportunity_scope(sf,monkeypatch):
    from app.api.routes import events as route
    from app.events import ranking
    from app.services import leader_archive
    from unittest.mock import AsyncMock
    store=EventStore(sf)
    row,_=store.add_event({'title':'历史公司董事辞职','fingerprint':'old-personnel','source':'官方',
        'source_item_id':'old1','published_at':datetime(2026,1,2,10), 'source_published_at':datetime(2026,1,2,10),
        'half_life_hours':1,'directions':[]})
    rank=AsyncMock(side_effect=AssertionError('must not request market'))
    archive=AsyncMock(side_effect=AssertionError('must not request archive'))
    monkeypatch.setattr(ranking,'collect_rank_context',rank)
    monkeypatch.setattr(leader_archive,'get_archive',archive)
    request=NS(app=NS(state=NS(hub=NS(provider=object()))))
    import asyncio
    value=asyncio.run(route.impact_events(include_l3=True,limit=200,sort='time',request=request,store=store,reading=True))
    assert value['data']['items'][0]['id']==row.id
    assert value['data']['items'][0]['is_active'] is False
    assert value['data']['coverage']['scope']=='latest_saved_visible_events'
    rank.assert_not_called();archive.assert_not_called()
    assert store.get_event(row.id).status=='active'


@pytest.mark.parametrize("items", ["not-json", "{}", '[null]'])
def test_broken_saved_selection_is_unknown_not_zero(sf, items):
    with sf() as db:
        db.add(DailyPickSet(date='2026-10-23',items=items))
        db.commit()
    context = saved_selection_context(sf, now=NOW)
    assert context['state'] == 'partial'
    assert any('每日名单损坏' in reason for reason in context['missing'])
    assert context['items'] == []


def test_current_revision_cannot_become_the_original_cited_version():
    context = {'items': [{'symbol':'600000', 'scope':'daily', 'trade_date':'2026-10-23',
        'source_version':'v1', 'event_refs':[{'event_id':7,'version_id':2,'observation_id':3}]}]}
    event = {'id':7,'interpretation_ref':{'version_id':9,'observation_id':10}}
    link = selection_links(event, context)[0]
    assert link['reference_state'] == 'different_version'
    assert link['original_event_refs'] == context['items'][0]['event_refs']
    event['interpretation_ref'] = {'version_id':2,'observation_id':3}
    assert selection_links(event,context)[0]['reference_state'] == 'current_version'
    event.pop('interpretation_ref')
    assert selection_links(event,context)[0]['reference_state'] == 'unknown'
