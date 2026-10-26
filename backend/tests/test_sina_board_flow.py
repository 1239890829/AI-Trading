"""Source-isolated funds reads never translate BK identities or execution scope."""
import asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app.market import board_flow as bf
from app.core.ttl_cache import TTLCache


@pytest.mark.parametrize('kind', ['concept', 'industry'])
def test_sina_route_passes_exact_identity_and_manual_fresh(monkeypatch, kind):
    from app.api.routes.market_flow import router
    seen=[]
    async def read(category, *, kind, fresh):
        seen.append((category,kind,fresh))
        return {'source':'sina_board_funds','category':category,'kind':kind,'available':True,'rows':[]}
    monkeypatch.setattr(bf,'get_sina_board_constituents',read)
    app=FastAPI();app.include_router(router,prefix='/api')
    with TestClient(app) as c:
        r=c.get('/api/market/sina-board-fund-flow/gn_ldc/constituents',params={'kind':kind,'fresh':'true'})
    assert r.status_code==200 and r.json()['data']['source']=='sina_board_funds'
    assert seen==[('gn_ldc',kind,True)]


@pytest.mark.parametrize('category,kind',[('gn bad','concept'),('gn_ldc','region')])
def test_sina_route_rejects_malformed_input_without_source(monkeypatch,category,kind):
    from app.api.routes.market_flow import router
    async def forbidden(*args,**kwargs):raise AssertionError('invalid identity must not reach source')
    monkeypatch.setattr(bf,'get_sina_board_constituents',forbidden)
    app=FastAPI();app.include_router(router,prefix='/api')
    with TestClient(app) as c:
        assert c.get(f'/api/market/sina-board-fund-flow/{category}/constituents',params={'kind':kind}).status_code==422


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(bf,'_HTTP',object())
    monkeypatch.setattr(bf,'_SINA_BOARD_CACHE',TTLCache('test-sina-groups',60,maxsize=4))
    monkeypatch.setattr(bf,'_SINA_MEMBER_CACHE',TTLCache('test-sina-members',60,maxsize=4))
    monkeypatch.setattr(bf,'_inflight_source_reads',None,raising=False)


def payload(kind):
    return {'available':True,'source':'sina_board_funds','kind':kind,'rows':[{'category':'gn_ldc','name':'锂电池'}],
            'received_at':'2026-10-26T15:00:00+08:00','source_as_of':None,
            'coverage':{'total':1,'returned':1,'complete':True},'degraded':[]}


def test_sina_cold_reads_share_and_mutation_cannot_renew_or_corrupt(monkeypatch,owner):
    from app.market import _sina_board_source as source
    calls=[]
    async def fetch(http,kind,now):
        calls.append(kind);await asyncio.sleep(0);return payload(kind)
    monkeypatch.setattr(source,'fetch_boards',fetch)
    async def check():
        result=await asyncio.gather(*(bf.get_sina_board_fund_flow('concept') for _ in range(8)))
        result[0]['rows'].clear()
        again=await bf.get_sina_board_fund_flow('concept')
        assert len(again['rows'])==1 and calls==['concept']
        assert again['received_at']=='2026-10-26T15:00:00+08:00' and again['source_as_of'] is None
        await bf.get_sina_board_fund_flow('industry')
        assert calls==['concept','industry']
    asyncio.run(check())


def test_sina_failure_keeps_unknown_and_manual_retry_reaches_source(monkeypatch,owner):
    from app.market import _sina_board_source as source
    calls=[]
    async def fetch(http,kind,now):
        calls.append(kind)
        if len(calls)==1:raise ConnectionError('fixture client disconnect')
        return payload(kind)
    monkeypatch.setattr(source,'fetch_boards',fetch)
    async def check():
        first=await bf.get_sina_board_fund_flow('concept')
        assert not first['available'] and first['coverage']['total'] is None and first['rows']==[]
        assert first['source_as_of'] is None and first['received_at'] is None
        assert not (await bf.get_sina_board_fund_flow('concept'))['available']
        assert (await bf.get_sina_board_fund_flow('concept',fresh=True))['available'] and len(calls)==2
    asyncio.run(check())
