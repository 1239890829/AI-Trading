"""Eastmoney qfq/raw daily lanes must use explicit fqt identities."""
from __future__ import annotations

import asyncio

from app.data_providers.eastmoney import EastmoneyProvider


def test_eastmoney_daily_basis_uses_explicit_fqt(monkeypatch):
    seen: list[str] = []
    p = EastmoneyProvider()

    async def fake_get_json(_url, params):
        seen.append(str(params["fqt"]))
        return {"data": {"klines": []}}

    monkeypatch.setattr(p, "_get_json", fake_get_json)

    async def run():
        try:
            assert await p.get_kline("600519", "1d") == []
            assert await p.get_raw_daily_kline("600519") == []
        finally:
            await p.aclose()

    asyncio.run(run())
    assert seen == ["1", "0"]
