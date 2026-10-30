"""New asset lifecycle cannot prevent existing quote/source shutdown."""
import asyncio
from pathlib import Path
import tempfile

import httpx
import pytest

from app.assets.service import AssetReadService
from app.bootstrap import services as assembly
from app.bootstrap.services import AppServices
from app.core import inflight_reads


def test_unused_asset_view_never_allocates_an_http_client(monkeypatch):
    def unexpected(**kwargs):
        raise AssertionError("unused asset view allocated HTTP/TLS resources")
    monkeypatch.setattr(httpx, "AsyncClient", unexpected)
    async def run():
        for enabled in (False, True):
            service = AssetReadService(api_key="fixture", enabled=enabled)
            assert service.capabilities().items[0].state == ("available" if enabled else "blocked")
            await service.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("failure", [asyncio.CancelledError, RuntimeError])
def test_asset_shutdown_failure_still_drains_existing_owners(monkeypatch, failure):
    calls = []
    class Provider:
        async def aclose(self):
            calls.append("provider_closed")
    class Catalog:
        async def aclose(self):
            calls.append("catalog_closed")
    class Assets:
        async def aclose(self):
            calls.append("asset_close_attempted")
            raise failure()
    provider = Provider()
    async def drain(owner):
        calls.append("provider_drained" if owner is provider else "board_drained")
    monkeypatch.setattr(inflight_reads, "close_inflight_reads", drain)
    services = AppServices(provider=provider, hub=None, watchlist_repo=None,
                           alert_repo=None, alert_engine=None, snapshot_service=None,
                           risk_engine=None, paper=None, theme_catalog=Catalog(),
                           event_store=None, review=None, asset_service=Assets())
    async def run():
        if failure is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):
                await services.aclose()
        else:
            await services.aclose()
    asyncio.run(run())
    assert calls == ["asset_close_attempted", "board_drained", "provider_drained",
                     "provider_closed", "catalog_closed"]


def test_enabled_source_evidence_uses_isolated_assembly_destination():
    directory = assembly.ASSET_EVIDENCE_DIR.resolve()
    assert not directory.is_relative_to(Path(__file__).resolve().parents[2])
    assert directory.is_relative_to(Path(tempfile.gettempdir()).resolve())
    def reply(request):
        return httpx.Response(200, json={"code": 0, "data": {"item": [{
            "thscode": "000001.OF", "ticker": "000001", "name": "夹具基金",
            "asset_type": "fund-otc", "exchange": "OF", "currency": "CNY",
        }]}})
    async def run():
        client = httpx.AsyncClient(base_url="https://fuyao.aicubes.cn", transport=httpx.MockTransport(reply))
        service = AssetReadService(api_key="fixture", enabled=True, evidence_dir=directory, http_client=client)
        try:
            result = await service.search("000001", "fund", 20)
            assert result.items[0].source_symbol == "000001.OF"
            assert result.evidence[0].raw_available
            assert list(directory.glob("*.body.json"))
        finally:
            await service.aclose()
    asyncio.run(run())
