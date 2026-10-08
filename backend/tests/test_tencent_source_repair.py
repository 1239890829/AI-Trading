"""现货量额、历史区间、域名失败与北京时间边界回归。"""
import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.core.bjtime import BJ_TZ
from app.data_providers.tencent import TencentProvider, build_minute_points, parse_quote, parse_kline_payload, parse_order_book


def test_star_quote_minute_and_kline_keep_shares_book_keeps_lots():
    fields = [''] * 62
    for i, v in {1:'中芯国际',2:'688981',3:'112',4:'111',9:'112',10:'2',30:'20261008094758',33:'113.13',34:'111.75',36:'5332340',37:'59931.2103'}.items():
        fields[i] = v
    q = parse_quote('sh', fields)
    assert q.volume == 5332340
    assert q.low <= q.amount / q.volume <= q.high
    assert parse_order_book('688981', fields).bids[0].volume == 200
    points = build_minute_points(['0930 112.00 76644 8584128.00','0931 112.05 834833 93610978.00'], '20261008', datetime(2026,10,8).date(), 'sh688981')
    assert points[0]['avg'] == 112
    assert points[1]['volume'] == 758189
    bars = parse_kline_payload('688981','1d',{'data':{'sh688981':{'day':[['2026-09-30','117.77','111.99','118.18','111.29','42874573']]}}})
    assert bars[0].volume == 42874573


def _run_kline(handler, start=None, end=None):
    async def run():
        p = TencentProvider()
        await p._client.aclose()
        p._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await p.get_kline('600519','1d',start,end)
        finally:
            await p.aclose()
    return asyncio.run(run())


def test_historical_segments_and_missing_identity_host_failover():
    seen=[]
    def handler(req):
        fields=req.url.params['param'].split(',');seen.append((req.url.host,fields))
        if req.url.host=='web.ifzq.gtimg.cn':
            return httpx.Response(200,json={'data':{'sh688981':{}}})
        first,last=fields[2:4]
        return httpx.Response(200,json={'data':{'sh600519':{'qfqday':[[first,'10','10','11','9','3'],[last,'10','10','11','9','4']]}}})
    bars=_run_kline(handler,datetime(2022,1,3,tzinfo=BJ_TZ),datetime(2026,9,30,23,59,tzinfo=BJ_TZ))
    assert bars[0].ts.date().isoformat()=='2022-01-03'
    assert bars[-1].ts.date().isoformat()=='2026-09-30'
    assert len({b.ts for b in bars})==len(bars)==6
    assert len([host for host,_ in seen if host=='web.ifzq.gtimg.cn'])==1
    assert all(int(f[4])==640 for _,f in seen)
    assert seen[2][1][2] > seen[1][1][3]


def test_kline_http_error_not_returned_as_empty_history():
    with pytest.raises(Exception,match='hosts unavailable'):
        _run_kline(lambda req: httpx.Response(503))


def test_kline_negative_qfq_is_rejected():
    payload={'data':{'sh600519':{'qfqday':[['2024-01-02','-2','-1','1','-3','1']]}}}
    with pytest.raises(Exception,match='nonpositive'):
        _run_kline(lambda req:httpx.Response(200,json=payload))


def test_api_date_bounds_use_beijing_midnight(monkeypatch):
    from app.api.routes import market_quotes
    captured={}
    async def payload(hub,symbol,timeframe,limit,start,end):
        captured.update(start=start,end=end)
        return {}
    monkeypatch.setattr(market_quotes,'_kline_payload',payload)
    day=datetime(2024,1,2).date()
    asyncio.run(market_quotes.kline('600519','1d',10,day,day,object()))
    assert captured['start'].astimezone(timezone.utc).isoformat()=='2024-01-01T16:00:00+00:00'
    assert captured['end'].astimezone(timezone.utc).isoformat()=='2024-01-02T15:59:59+00:00'


def test_empty_price_series_falls_back_to_other_host():
    hosts=[]
    def handler(req):
        hosts.append(req.url.host)
        data={'qfqday':[['2024-01-02','10','10','11','9','3']]} if len(hosts)>1 else {}
        return httpx.Response(200,json={'data':{'sh600519':data}})
    assert len(_run_kline(handler))==1
    assert len(hosts)==2
