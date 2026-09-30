"""Cold page fan-out must not amplify upstream requests or share mutable results."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app.api.routes.market_board import boards
from app.services.quote_hub import QuoteHub


def context():
    class Provider:
        name = "fixture"
        realtime = False

        def __init__(self):
            self.calls = []
            self.fail = False

        async def get_board_rankings(self, kind):
            self.calls.append(kind)
            await asyncio.sleep(.01)
            if self.fail:
                raise OSError("offline fixture failure")
            return [{"name": kind, "change_pct": 1, "nested": {"value": 2}}]

    provider = Provider()
    app = FastAPI()
    return SimpleNamespace(provider=provider, hub=QuoteHub(provider, 1),
                           request=Request({"type": "http", "app": app}))


def test_cold_burst_coalesces_and_does_not_mutate_cached_payload():
    async def run():
        c = context()
        results = await asyncio.gather(*(boards(type="concept", request=c.request, hub=c.hub) for _ in range(20)))
        assert c.provider.calls == ["concept"]
        assert sum(not result["meta"]["cached"] for result in results) == 1
        results[0]["meta"]["cached"] = "corrupted"
        again = await boards(type="concept", request=c.request, hub=c.hub)
        assert again["data"]["boards"][0]["nested"]["value"] == 2
        assert again["meta"]["cached"] is True
        assert c.provider.calls == ["concept"]
    asyncio.run(run())


def test_keys_remain_independent_and_failure_does_not_poison_recovery():
    async def run():
        c = context()
        a, b = await asyncio.gather(boards(type="concept", request=c.request, hub=c.hub),
                                    boards(type="hangye", request=c.request, hub=c.hub))
        assert sorted(c.provider.calls) == ["concept", "hangye"]
        assert a["data"]["boards"][0]["name"] == "concept"
        assert b["data"]["boards"][0]["name"] == "hangye"
        c.provider.fail = True
        with pytest.raises(HTTPException) as failed:
            await boards(type="failure-key", request=c.request, hub=c.hub)
        assert failed.value.status_code == 502
        c.provider.fail = False
        recovered = await boards(type="failure-key", request=c.request, hub=c.hub)
        assert recovered["meta"]["cached"] is False
        assert c.provider.calls.count("failure-key") == 2
    asyncio.run(run())
