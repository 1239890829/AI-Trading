"""东财忽略 pagesize 时，Provider 仍须兑现财务报告期上限。"""
import asyncio

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
