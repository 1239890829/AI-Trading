"""东财忽略 pagesize 时，Provider 仍须兑现财务报告期上限。"""
import asyncio

import httpx
import pytest

from app.data_providers.eastmoney import EastmoneyProvider


def test_financial_period_limit_applies_after_report_dedup(monkeypatch):
    provider = EastmoneyProvider()
    rows = [
        {"SECURITY_CODE": "600519", "REPORTDATE": "2026-06-30", "NOTICE_DATE": "2026-08-15", "TOTAL_OPERATE_INCOME": 3},
        {"SECURITY_CODE": "600519", "REPORTDATE": "2026-06-30", "TOTAL_OPERATE_INCOME": 99},
        {"SECURITY_CODE": "600519", "REPORTDATE": "2026-03-31", "TOTAL_OPERATE_INCOME": 2},
        {"SECURITY_CODE": "600519", "REPORTDATE": "2025-12-31", "TOTAL_OPERATE_INCOME": 1},
    ]

    async def fetch(_url, params):
        assert params["pagesize"] == "2"
        return {"result": {"data": rows}}

    monkeypatch.setattr(provider, "_get_json", fetch)

    async def run():
        try:
            return await provider.get_financials("600519", 2)
        finally:
            await provider.aclose()

    result = asyncio.run(run())
    assert [row["report_date"] for row in result] == ["2026-06-30", "2026-03-31"]
    assert result[0]["revenue"] == 3.0
    assert result[0]["notice_date"] == "2026-08-15"


@pytest.mark.parametrize("symbol,secucode", [
    ("920819", "920819.BJ"), ("430047", "430047.BJ"), ("833171", "833171.BJ"),
    ("900901", "900901.SH"), ("200002", "200002.SZ"), ("510300", "510300.SH"),
    ("600519", "600519.SH"), ("000001", "000001.SZ"),
])
def test_company_profile_keeps_market_in_both_f10_requests(symbol, secucode):
    # 合成传输只核构造身份，不证明源端提供BJ/B股/ETF公司资料或财报。
    seen = []

    def respond(request):
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.host == "datacenter-web.eastmoney.com":
            assert request.url.params["filter"] == f'(SECUCODE="{secucode}")'
            return httpx.Response(200, json={"result": {"data": [
                {"SECURITY_CODE": symbol, "SECURITY_NAME_ABBR": "身份夹具"},
            ]}})
        assert request.url.host == "emweb.securities.eastmoney.com"
        assert request.url.params["code"] == secucode
        return httpx.Response(200, json={"ssbk": [], "hxtc": []})

    async def run():
        provider = EastmoneyProvider()
        await provider._client.aclose()
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            return await provider.get_company_profile(symbol)
        finally:
            await provider.aclose()

    result = asyncio.run(run())
    assert result["symbol"] == symbol
    assert result["source"] == "eastmoney"
    assert [path for path, _params in seen] == ["/api/data/v1/get", "/PC_HSF10/CoreConception/PageAjax"]
    assert seen[0][1]["filter"] == f'(SECUCODE="{secucode}")'
    assert seen[1][1]["code"] == secucode
