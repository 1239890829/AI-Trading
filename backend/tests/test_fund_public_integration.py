"""Fund snapshot consumers retain separate public descriptions, references and NAV facts."""
import asyncio
from datetime import datetime
import json

import httpx
import pytest

from app.assets.service import AssetReadService, HOLDINGS, NAV, PROFILE, SEARCH
from app.assets.fund_public import EASTMONEY, HUAAN, SINA
from app.core.errors import AppError
from app.core.inflight_reads import LocalReadCapacityExceeded


NOW = datetime.fromisoformat("2026-10-30T14:00:00+08:00")
F10 = '''<table><tr><th>基金代码</th><td>040046</td></tr>
<tr><th>基金简称</th><td>华安纳斯达克100ETF联接(QDII)A</td></tr>
<tr><th>基金全称</th><td>华安纳斯达克100交易型开放式指数证券投资基金联接基金(QDII)</td></tr>
<tr><th>基金管理人</th><td>华安基金</td></tr>
<tr><th>业绩比较基准</th><td>纳斯达克100指数收益率(经汇率调整)*95%+人民币活期存款税后利率*5%</td></tr></table>
<div><h4>投资范围</h4><p>目标ETF基金份额及标的指数成份股。</p></div>'''.encode()
ISSUER = '''<table><tr><td class="hd">基金代码</td><td>040046</td></tr>
<tr><td class="hd">法定名称</td><td>华安纳斯达克100ETF联接基金(QDII)</td></tr>
<tr><td class="hd">基金简称</td><td>华安纳斯达克100ETF联接(QDII)A</td></tr>
<tr><td class="hd">币种</td><td>CNY</td></tr>
<tr><td class="hd">交易状态</td><td><li class="trade_status">暂停申购</li>
<li class="trade_status">开放赎回</li></td></tr></table>'''.encode()
REFERENCE = {"result": {"status": {"code": 0}, "data": {
    "desc": {"text_base": "前十大重仓均涨幅基于上一季度持仓，不能作为基金单位净值走势参考。"},
    "worth": "8.1932", "worth_date": "20261028", "heavy_stock_us_flag": 1,
    "networth": [{"symbol": "040046", "pre_date": "2026-10-30", "min_time": "13:50:00",
                  "pre_nav": "8.17", "nav_pct": "-0.28", "growthrate": "-0.0028",
                  "pre_nav2": "8.02", "nav2_pct": "-2.11", "growthrate2": "-0.0211"}],
}}}


def source_data(path):
    if path == SEARCH:
        item = [{"thscode": "040046.OF", "ticker": "040046", "name": "华安纳斯达克100ETF联接(QDII)A",
                 "asset_type": "fund-otc", "exchange": None, "currency": "CNY"}]
    elif path == NAV:
        item = [{"nav_date": int(datetime.fromisoformat("2026-10-28T00:00:00+08:00").timestamp() * 1000),
                 "unit_nav": 8.1932, "adj_nav": 8.1932}]
    elif path == PROFILE:
        item = [{"thscode": "040046.OF", "mgmt_name": "华安基金", "rate_info": [
            {"rate_type": "申购费", "standard_rate": "1.2%"}],
                 "trade_rule": [{"title": "开放日", "display_time": "按公告"}]}]
    elif path == HOLDINGS:
        item = []
    else:
        raise AssertionError(path)
    return {"code": 0, "data": {"item": item}}


def make_service(monkeypatch, *, public=True, issuer=ISSUER, ths=None, public_source=None, evidence_dir=None):
    from app.assets import service as module
    from app.assets import fund_public as public_module
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    monkeypatch.setattr(public_module, "_now", lambda: NOW)
    calls = []

    async def official(request):
        calls.append(str(request.url))
        return await ths(request) if ths else httpx.Response(200, json=source_data(request.url.path))

    async def supplementary(request):
        calls.append(str(request.url))
        assert not {"authorization", "x-api-key", "cookie"} & set(request.headers)
        if public_source:
            return await public_source(request)
        if str(request.url).startswith(EASTMONEY):
            return httpx.Response(200, content=F10)
        if str(request.url).startswith(HUAAN):
            return httpx.Response(200, content=issuer)
        assert str(request.url).startswith(SINA)
        return httpx.Response(200, json=REFERENCE)

    service = AssetReadService("fixture-private-key", public_enabled=public, evidence_dir=evidence_dir,
        http_client=httpx.AsyncClient(base_url="https://fuyao.aicubes.cn", transport=httpx.MockTransport(official)))
    service.public._client = httpx.AsyncClient(transport=httpx.MockTransport(supplementary))
    return service, calls


def test_snapshot_actually_consumes_public_profile_terms_research_and_distinct_reference(monkeypatch, tmp_path):
    async def run():
        service, calls = make_service(monkeypatch, evidence_dir=tmp_path)
        try:
            snapshot = await service.snapshot("040046.OF")
            assert snapshot.instrument.currency == snapshot.nav.currency == "CNY"
            assert snapshot.terms.investment_scope == "目标ETF基金份额及标的指数成份股。"
            assert "95%" in snapshot.terms.benchmark and snapshot.terms.subscription_status == "暂停申购"
            assert snapshot.terms.fees[0].value == "1.2%" and snapshot.terms.source == "mixed_public_sources"
            assert not {"investment_scope", "benchmark", "subscription_status"} & set(snapshot.terms.missing)
            checks = {check.label: check.value for check in snapshot.research.checks}
            assert checks["投资范围"] == checks["业绩基准"] == "公开资料具备"
            assert checks["申购状态"] == "暂停申购" and not snapshot.research.eligible
            assert "not_verified_regulatory_document" in snapshot.public_profile.document_scope
            assert snapshot.reference.curves[0].latest_pct == -.28
            assert snapshot.reference.curves[1].latest_pct == -2.11
            assert snapshot.estimate.value is None and snapshot.reference.estimated_fund_nav is None
            assert snapshot.nav.latest.unit_nav == 8.1932
            assert len(calls) == 7
            await service.snapshot("040046.OF")
            assert len(calls) == 7
            saved = [json.loads(path.read_text()) for path in tmp_path.glob("*.receipt.json")]
            assert len(saved) == 7 and all(record["raw_available"] for record in saved)
        finally:
            await service.aclose()
        assert service.us._client is None
        assert service.public._client.is_closed and service._client.is_closed
    asyncio.run(run())


def test_public_currency_conflict_is_quarantined_without_erasing_verified_ths_sections(monkeypatch):
    async def run():
        service, _ = make_service(monkeypatch, issuer=ISSUER.replace(b"CNY", b"USD"))
        try:
            snapshot = await service.snapshot("040046.OF")
            assert snapshot.instrument.currency == "CNY" and snapshot.nav.latest.unit_nav == 8.1932
            assert snapshot.terms.fees[0].value == "1.2%"
            assert snapshot.terms.source == "ths_official" and snapshot.terms.investment_scope is None
            assert snapshot.public_profile.fields == []
            assert "fund_public_currency_mismatch" in snapshot.public_profile.missing
            assert snapshot.reference.curves == [] and snapshot.reference.missing == ["fund_public_currency_mismatch"]
            assert {"investment_scope", "benchmark", "subscription_status"} <= set(snapshot.research.missing)
        finally:
            await service.aclose()
    asyncio.run(run())


def test_identity_resolves_before_any_optional_public_read_and_capacity_is_not_a_fake_snapshot(monkeypatch):
    async def run():
        async def missing(request):
            return httpx.Response(200, json={"code": 0, "data": {"item": []}})
        service, calls = make_service(monkeypatch, ths=missing)
        try:
            with pytest.raises(AppError) as caught:
                await service.snapshot("040046.OF")
            assert caught.value.code == "fund_identity_unconfirmed"
            assert len(calls) == 1 and calls[0].startswith("https://fuyao.aicubes.cn")
            service._snapshots.maxsize = 0
            with pytest.raises(LocalReadCapacityExceeded):
                await service.snapshot("040046.OF")
            assert len(calls) == 1
        finally:
            await service.aclose()
    asyncio.run(run())


def test_optional_source_slow_read_uses_remaining_deadline_keeps_nav_and_drains_owner(monkeypatch):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        async def slow(request):
            started.set()
            await release.wait()
            return httpx.Response(200, content=F10 if request.url.host == "fundf10.eastmoney.com" else ISSUER)
        service, calls = make_service(monkeypatch, public_source=slow)
        try:
            snapshot = await service._snapshot("040046.OF", "year", asyncio.get_running_loop().time() + .1)
            assert started.is_set() and snapshot.nav.latest.unit_nav == 8.1932
            assert snapshot.terms.fees[0].value == "1.2%"
            assert snapshot.holdings.state == "unavailable" and snapshot.holdings.missing == ["undisclosed_positions"]
            assert snapshot.public_profile.missing == ["fund_public_timeout"]
            assert snapshot.reference.missing == ["fund_public_timeout"]
            assert service.public._requests.tasks  # A timeout canceled waiters, not source owners.
            closing = asyncio.create_task(service.aclose())
            await asyncio.sleep(0)
            assert not closing.done()
            release.set()
            await closing
            assert not service.public._requests.tasks and not service.public._operations.tasks
            assert all(not url.startswith("https://fuyao.aicubes.cn/api/") or "X-api-key" not in url for url in calls)
        finally:
            release.set()
            await service.aclose()
    asyncio.run(run())


def test_slow_lookthrough_does_not_discard_root_holdings_or_restart_the_total_budget(monkeypatch):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        report = int(datetime.fromisoformat("2026-09-30T00:00:00+08:00").timestamp() * 1000)
        published = int(datetime.fromisoformat("2026-10-27T00:00:00+08:00").timestamp() * 1000)
        async def source(request):
            if request.url.path == HOLDINGS:
                if request.url.params["thscode"] == "159632.SZ":
                    started.set()
                    await release.wait()
                    return httpx.Response(200, json={"code": 0, "data": {"item": []}})
                return httpx.Response(200, json={"code": 0, "data": {"item": [{
                    "thscode": "159632.SZ", "stock_name": "目标ETF", "asset_type": "fund", "hold_ratio": 90.01,
                    "end_date_ms": report, "publish_date_ms": published,
                }]}})
            return httpx.Response(200, json=source_data(request.url.path))
        service, _ = make_service(monkeypatch, public=False, ths=source)
        try:
            snapshot = await service._snapshot("040046.OF", "year", asyncio.get_running_loop().time() + .1)
            assert started.is_set() and snapshot.nav.latest.unit_nav == 8.1932
            assert snapshot.holdings.items[0].source_symbol == "159632.SZ"
            assert snapshot.holdings.disclosed_weight_pct == 90.01
            assert snapshot.lookthrough.missing == ["fund_source_timeout"]
            assert service._requests.tasks
            closing = asyncio.create_task(service.aclose())
            await asyncio.sleep(0)
            assert not closing.done()
            release.set()
            await closing
            assert not service._requests.tasks
        finally:
            release.set()
            await service.aclose()
    asyncio.run(run())


def test_disabled_public_sources_remain_lazy_and_old_sections_keep_their_contract(monkeypatch):
    async def run():
        service, calls = make_service(monkeypatch, public=False)
        try:
            snapshot = await service.snapshot("040046.OF")
            assert snapshot.public_profile.missing == snapshot.reference.missing == ["fund_public_disabled"]
            assert snapshot.terms.source == "ths_official" and snapshot.nav.latest.unit_nav == 8.1932
            assert len(calls) == 4 and all(url.startswith("https://fuyao.aicubes.cn/") for url in calls)
        finally:
            await service.aclose()
    asyncio.run(run())
