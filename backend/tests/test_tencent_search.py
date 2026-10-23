"""Search source contract from public smartbox samples; never real network."""
import asyncio
import json

import httpx
import pytest

from app.data_providers.eastmoney import ProviderError
from app.data_providers.tencent import TencentProvider


def search(query,body,*,status=200):
    async def run():
        provider=TencentProvider()
        await provider._client.aclose()
        def response(request):
            assert request.url.host=='smartbox.gtimg.cn' and request.url.path=='/s3/'
            assert dict(request.url.params)=={'v':'2','q':query,'t':'all'}
            return httpx.Response(status,content=body)
        provider._client=httpx.AsyncClient(transport=httpx.MockTransport(response))
        try:return await provider.search(query)
        finally:await provider.aclose()
    return asyncio.run(run())


def reply(value):return ('v_hint='+json.dumps(value,ensure_ascii=True)+';').encode('ascii')


@pytest.mark.parametrize('query',['贵州茅台','gzmt','GZMT','guizhoumaotai'])
def test_search_five_columns_support_name_abbreviation_and_full_pinyin(query):
    rows=search(query,reply('sh~600519~贵州茅台~gzmt~GP-A'))
    assert len(rows)==1
    assert rows[0].symbol=='600519' and rows[0].name=='贵州茅台' and rows[0].market=='SH' and rows[0].source=='tencent'


def test_search_decodes_unicode_without_executing_javascript_or_mangling_gbk():
    rows=search('hstc','v_hint="sh~600410~华胜天成~hstc~GP-A";'.encode('gbk'))
    assert len(rows)==1 and rows[0].name=='华胜天成' and rows[0].symbol=='600410'


@pytest.mark.parametrize('value',['','hk~00700~腾讯控股~txkg~GP-H','us~AAPL~Apple~apple~GP-US'])
def test_search_real_empty_or_non_a_share_remains_empty(value):assert search('none',reply(value))==[]


def test_search_keeps_declared_combined_market_code_legacy_shape():
    rows=search('600519',reply('贵州茅台~sh600519'))
    assert len(rows)==1 and rows[0].symbol=='600519' and rows[0].name=='贵州茅台'


@pytest.mark.parametrize('market,code',[('hk','600519'),('us','600519'),('sh','60051'),('sh','6005190'),('sh','60051x'),('sh','６００５１９'),('../sh','600519')])
def test_search_filters_unknown_market_or_non_ascii_six_digit_code(market,code):
    assert search('bad',reply(f'{market}~{code}~bad row~bad~GP-A'))==[]


def test_search_preserves_multiple_stock_identities_and_filters_unrelated_rows():
    rows=search('stock',reply('sh~600519~贵州茅台~gzmt~GP-A^sz~000001~平安银行~payh~GP-A^bj~920000~北京样本~bjyb~GP-A^hk~00700~腾讯控股~txkg~GP-H'))
    assert [(row.symbol,row.market,row.name) for row in rows]==[('600519','SH','贵州茅台'),('000001','SZ','平安银行'),('920000','BJ','北京样本')]


@pytest.mark.parametrize('body',[b'<html>temporarily unavailable</html>',b'v_hint="unterminated;',b'v_hint=[];',b'v_hint=""; run_untrusted_code()'])
def test_search_invalid_or_nonstring_source_reply_is_failure(body):
    with pytest.raises(ProviderError):search('gzmt',body)


def test_search_http_error_remains_failure():
    with pytest.raises(ProviderError):search('gzmt',b'',status=503)
