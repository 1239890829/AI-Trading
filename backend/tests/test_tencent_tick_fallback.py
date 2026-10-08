import asyncio
from datetime import datetime

import httpx
import pytest

from app.core.bjtime import BJ_TZ
from app.market import tencent_tick, tdx_tick
from app.schemas.market import Trade


def page_text(page, size=70, *, code='sh600519', gap=False):
    rows=[]
    for n in range(size):
        seq=page*70+n+(1 if gap and n>2 else 0)
        rows.append(f'{seq}/09:30:00/10/0/2/2000/B')
    return f'v_detail_data_{code}=[{page},"'+ '|'.join(rows)+'"];'


@pytest.mark.parametrize('text', [page_text(0, gap=True), page_text(1), 'garbage',page_text(0).replace('/10/','/nan/'),page_text(0).replace('/B','/X')])
def test_tick_bad_format_and_gaps_rejected(text):
    with pytest.raises(Exception):
        tencent_tick.parse_tick_page(text,'sh600519',0)


def test_tail_binary_search_cache_and_cross_day(monkeypatch):
    real=httpx.AsyncClient
    hits=[]
    day=['20260930']
    def handle(req):
        hits.append(str(req.url))
        if req.url.host=='qt.gtimg.cn':
            fields=['']*37;fields[30]=day[0]+'100000';fields[36]='100'
            return httpx.Response(200,content=('v_sh600519="'+'~'.join(fields)+'";').encode())
        n=int(req.url.params['p'])
        return httpx.Response(200,text=page_text(n,13 if n==55 else 70) if n<=55 else '')
    monkeypatch.setattr(tencent_tick.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle)))
    async def run():
        source=tencent_tick.TencentTickFallback()
        rows=await source.fetch('600519',50)
        assert len(rows)==50 and rows[-1].volume==2 and rows[-1].side=='buy'
        assert rows[-1].ts.astimezone(BJ_TZ).date().strftime('%Y%m%d')==day[0]
        count=len(hits)
        again=await source.fetch('600519',50)
        assert len(hits)==count and rows[0] is not again[0]
        assert count<24
    asyncio.run(run())


def test_snapshot_date_change_rejected(monkeypatch):
    real=httpx.AsyncClient;calls=[0]
    def handle(req):
        if req.url.host=='qt.gtimg.cn':
            calls[0]+=1
            fields=['']*37;fields[30]=f'202609{28+calls[0]}100000';fields[36]='100'
            return httpx.Response(200,text='v_sh600519="'+'~'.join(fields)+'";')
        return httpx.Response(200,text=page_text(0,10) if req.url.params['p']=='0' else '')
    monkeypatch.setattr(tencent_tick.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle)))
    with pytest.raises(Exception,match='crossed source day'):
        asyncio.run(tencent_tick.TencentTickFallback().fetch('600519'))


def test_tdx_success_never_calls_http_fallback(monkeypatch):
    monkeypatch.setattr(tdx_tick.settings,'trades_tdx_fallback_enabled',True)
    monkeypatch.setattr(tdx_tick.settings,'trades_tencent_http_fallback_enabled',True)
    row=Trade(symbol='600519',price=10,volume=2,side='buy',source='tdx')
    monkeypatch.setattr(tdx_tick,'fetch_tdx_trades',lambda *a,**kw:[row])
    async def never(*a,**kw):
        raise AssertionError('healthy TDX must not hit HTTP')
    monkeypatch.setattr(tencent_tick,'fetch_tencent_trades',never)
    async def primary(symbol):return []
    result=asyncio.run(tdx_tick.fetch_trades_with_tdx_fallback(primary,'600519'))
    assert result[1]=='tdx'


@pytest.mark.parametrize('symbol',['430047.BJ','000001.SH','399001.SZ'])
def test_unsupported_http_does_not_turn_valid_empty_into_fault(monkeypatch,symbol):
    monkeypatch.setattr(tdx_tick.settings,'trades_tdx_fallback_enabled',True)
    monkeypatch.setattr(tdx_tick.settings,'trades_tencent_http_fallback_enabled',True)
    monkeypatch.setattr(tdx_tick,'fetch_tdx_trades',lambda *a,**kw:[])
    async def primary(symbol):return []
    rows,source,detail=asyncio.run(tdx_tick.fetch_trades_with_tdx_fallback(primary,symbol))
    assert rows==[] and source=='none'
    assert 'tencent_http: unsupported' in detail
    assert tdx_tick.trades_failure_detail(detail)==''


def test_http_fallback_after_tdx_error_and_error_remains_visible(monkeypatch):
    monkeypatch.setattr(tdx_tick.settings,'trades_tdx_fallback_enabled',True)
    monkeypatch.setattr(tdx_tick.settings,'trades_tencent_http_fallback_enabled',True)
    def broken(*a,**kw):raise RuntimeError('socket failed')
    monkeypatch.setattr(tdx_tick,'fetch_tdx_trades',broken)
    async def primary(symbol):return []
    async def http(symbol,limit):return [Trade(symbol=symbol,price=10,volume=2,side='buy',source='tencent')]
    monkeypatch.setattr(tencent_tick,'fetch_tencent_trades',http)
    assert asyncio.run(tdx_tick.fetch_trades_with_tdx_fallback(primary,'600519'))[1]=='tencent'
    async def failed(*a,**kw):raise RuntimeError('HTTP failed')
    monkeypatch.setattr(tencent_tick,'fetch_tencent_trades',failed)
    result=asyncio.run(tdx_tick.fetch_trades_with_tdx_fallback(primary,'600519'))
    assert 'HTTP failed' in tdx_tick.trades_failure_detail(result[2])


def test_positive_quote_volume_with_empty_first_page_is_failure(monkeypatch):
    real=httpx.AsyncClient
    def handle(req):
        if req.url.host=='qt.gtimg.cn':
            fields=['']*37;fields[30]='20261008100000';fields[36]='100'
            return httpx.Response(200,text='v_sh600519="'+'~'.join(fields)+'";')
        return httpx.Response(200,text='')
    monkeypatch.setattr(tencent_tick.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle)))
    with pytest.raises(Exception,match='positive source volume'):
        asyncio.run(tencent_tick.TencentTickFallback().fetch('600519'))


def test_http_request_obeys_total_timeout(monkeypatch):
    real=httpx.AsyncClient
    async def handle(req):
        await asyncio.sleep(10)
        return httpx.Response(200,text='')
    monkeypatch.setattr(tencent_tick.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle)))
    with pytest.raises(TimeoutError):
        asyncio.run(tencent_tick.TencentTickFallback().fetch('600519'))


def test_assistant_preserves_source_date_aggregation_quality(monkeypatch):
    from app.assistant.tools import market
    from app.assistant.tools.core import ToolContext
    from datetime import timezone
    row=Trade(symbol='600519',ts=datetime(2026,9,30,7,0,tzinfo=timezone.utc),price=10,volume=2,side='buy',source='tencent',quality='stale',quality_reasons=['旧交易日'])
    async def fallback(*a,**kw):return [row],'tencent',''
    monkeypatch.setattr(market,'fetch_trades_with_tdx_fallback',fallback)
    class Provider:
        async def get_trades(self,symbol):return []
    out=asyncio.run(market._t_trades(ToolContext(provider=Provider(),known_symbols={'600519'}),symbols='600519'))
    assert '2026-09-30' in out and 'stale' in out and '旧交易日' in out
    assert '非L2' in out and '量：手' in out


def test_future_snapshot_timestamp_rejected(monkeypatch):
    real=httpx.AsyncClient
    def handle(req):
        fields=['']*37;fields[30]='20990101100000';fields[36]='100'
        return httpx.Response(200,text='v_sh600519="'+'~'.join(fields)+'";')
    monkeypatch.setattr(tencent_tick.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle)))
    with pytest.raises(Exception,match='invalid source timestamp'):
        asyncio.run(tencent_tick.TencentTickFallback().fetch('600519'))
