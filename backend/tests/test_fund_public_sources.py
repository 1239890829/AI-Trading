"""Public source excerpts and offline behavior; no live source, credentials or production writes."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import httpx
import pytest

from app.assets.fund_public import (
    EASTMONEY, HUAAN, SINA, MAX_BODY, FundPublicService, FundPublicSourceError,
    parse_profile_html, parse_reference,
)
from app.core.inflight_reads import LocalReadCapacityExceeded
from app.schemas.asset_evidence import AssetEvidence


F10 = '''<html><table class="info w790">
<tr><th>基金全称</th><td>华安纳斯达克100交易型开放式指数证券投资基金联接基金(QDII)</td>
<th>基金简称</th><td>华安纳斯达克100ETF联接(QDII)A</td></tr>
<tr><th>基金代码</th><td>040046（前端）<th>基金类型</th><td>指数型-海外股票</td></tr>
<tr><th>成立日期/规模</th><td>2013年08月02日 / 2.730亿份</td></tr>
<tr><th>基金管理人</th><td>华安基金</td><th>基金托管人</th><td>建设银行</td></tr>
<tr><th>基金经理人</th><td>倪斌</td></tr>
<tr><th>业绩比较基准</th><td>纳斯达克100指数收益率(经汇率调整)*95%+人民币活期存款税后利率*5%</td>
<th>跟踪标的</th><td>纳斯达克100指数</td></tr></table>
<div><h4>投资目标</h4><p>投资于目标ETF并跟踪标的指数。</p></div>
<div><h4>投资范围</h4><p>目标ETF基金份额及标的指数成份股。</p></div>
<div><h4>投资策略</h4><p>以申购和赎回方式投资目标ETF。</p></div></html>'''.encode()

MANAGER = '''<html><table><tr><td class="hd">法定名称</td>
<td>华安纳斯达克100交易型开放式指数证券投资基金联接基金(QDII)</td>
<td class="hd">基金代码</td><td>040046</td></tr>
<tr><td class="hd">基金简称</td><td>华安纳斯达克100ETF联接（QDII）A</td>
<td class="hd">成立日期</td><td>2022-12-09</td></tr>
<tr><td class="hd">基金类型</td><td>QDII</td><td class="hd">交易状态</td><td>
<li class="trade_status">暂停申购</li><li class="trade_status">开放赎回</li>
<li class="trade_status">暂停定投</li></td></tr>
<tr><td class="hd">币种</td><td>CNY</td><td class="hd">产品风险等级</td><td>R4</td></tr>
<tr><td class="hd">业绩比较基准</td><td id="yjbx"></td></tr></table></html>'''.encode()

WARNING = "前十大重仓均涨幅展示截至上一季度的前十大重仓加权涨跌幅，不能作为基金单位净值走势参考。"


def payload():
    return {"result": {"status": {"code": 0}, "data": {
        "worth": "8.1932", "worth_date": "20261028", "heavy_stock_us_flag": 1,
        "desc": {"text_base": WARNING}, "networth": [
            {"symbol": "040046", "pre_date": "2026-10-30", "min_time": "13:49:00",
             "pre_nav": "8.1684", "nav_pct": "-0.3031", "growthrate": -0.003031,
             "pre_nav2": "8.0196", "nav2_pct": "-2.1183", "growthrate2": "-0.021183"},
            {"symbol": "040046", "pre_date": "2026-10-30", "min_time": "13:50:00",
             "pre_nav": "8.17", "nav_pct": "-0.28", "growthrate": -0.0028,
             "pre_nav2": "8.02", "nav2_pct": "-2.11", "growthrate2": "-0.0211"},
        ]}}}


def evidence():
    return AssetEvidence(endpoint=SINA, params={"symbol": "040046"},
        received_at=datetime(2026, 10, 30, 5, 51, tzinfo=timezone.utc),
        payload_sha256="a" * 64, receipt_id="b" * 64)


def reference(value=None):
    return parse_reference(json.dumps(payload() if value is None else value).encode(), "040046.OF", evidence())


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_real_malformed_f10_cells_do_not_swallow_neighbor_fields():
    fields = {f.key: f for f in parse_profile_html(F10, "040046.OF", "eastmoney_fund")}
    assert fields["name"].value == "华安纳斯达克100ETF联接(QDII)A"
    assert fields["fund_type"].value == "指数型-海外股票"
    assert "95%" in fields["benchmark"].value
    assert fields["investment_scope"].value == "目标ETF基金份额及标的指数成份股。"
    assert "currency" not in fields  # Do not guess from name, OF or local convention.
    assert all(f.source_as_of is None for f in fields.values())


@pytest.mark.parametrize("body", [b"<html>page not found</html>", F10.replace(b"040046", b"040047"),
                                      F10 + F10])
def test_missing_wrong_or_ambiguous_profile_identity_fails(body):
    with pytest.raises(FundPublicSourceError):
        parse_profile_html(body, "040046.OF", "eastmoney_fund")


def test_manager_html_provides_explicit_currency_and_separate_transaction_states():
    fields = {f.key: f for f in parse_profile_html(MANAGER, "040046.OF", "huaan_fund")}
    assert fields["currency"].value == "CNY"
    assert fields["subscription_status"].value == "暂停申购"
    assert fields["redemption_status"].value == "开放赎回"
    assert fields["automatic_investment_status"].value == "暂停定投"
    assert "benchmark" not in fields


def test_sina_is_two_distinct_holdings_references_never_a_fund_nav_forecast():
    result = reference()
    assert result.state == "ready" and result.warning == WARNING and result.heavy_stock_us is True
    assert result.scope == "reported_top_holdings_reference"
    assert result.estimated_fund_nav is None and result.estimated_fund_percent is None
    assert [c.latest_pct for c in result.curves] == [-.28, -2.11]
    assert [c.key for c in result.curves] == ["nav_pct", "nav2_pct"]
    assert result.disclosed_nav == 8.1932 and result.disclosed_nav_date.isoformat() == "2026-10-28"
    assert result.source_as_of.isoformat() == "2026-10-30T13:50:00+08:00"
    assert result.age_seconds == 60 and result.is_current_day
    assert "不是整只基金" in result.scope_note


def test_prior_day_reference_stays_stale_and_does_not_get_receive_clock_as_source_time():
    value = payload()
    for row in value["result"]["data"]["networth"]:
        row["pre_date"] = "2026-10-29"
    result = reference(value)
    assert result.state == "stale" and not result.is_current_day
    assert result.source_as_of.isoformat() == "2026-10-29T13:50:00+08:00"
    assert result.age_seconds == 86460


def test_cached_reference_keeps_original_receipt_but_recomputes_age_and_day():
    raw = json.dumps(payload()).encode()
    result = parse_reference(raw, "040046.OF", evidence(),
        observed_at=datetime(2026, 10, 30, 16, 1, tzinfo=timezone.utc))
    assert result.state == "stale" and not result.is_current_day
    assert result.evidence[0].received_at == evidence().received_at
    assert result.source_as_of.isoformat() == "2026-10-30T13:50:00+08:00"
    assert result.age_seconds == 36660


def test_out_of_order_points_are_sorted_and_identical_duplicates_are_not_double_counted():
    value = payload()
    rows = value["result"]["data"]["networth"]
    rows.reverse()
    rows.append(deepcopy(rows[0]))
    result = reference(value)
    assert len(result.curves[0].points) == 2
    assert result.curves[0].points[0].as_of.minute == 49 and result.curves[0].latest_pct == -.28


@pytest.mark.parametrize("change,expected", [
    (lambda d: d["networth"][0].update(symbol="270042"), "fund_reference_identity_mismatch"),
    (lambda d: d["networth"][0].update(pre_date="2026-10-31"), "fund_reference_time_invalid"),
    (lambda d: d["networth"][0].update(pre_date="2026-10-29"), "fund_reference_time_invalid"),
    (lambda d: d["networth"][0].update(nav_pct="NaN"), "fund_reference_shape_invalid"),
    (lambda d: d["networth"][0].update(pre_nav="-2"), "fund_reference_shape_invalid"),
    (lambda d: d.update(heavy_stock_us_flag=True), "fund_reference_shape_invalid"),
    (lambda d: d.update(desc={}), "fund_reference_scope_missing"),
    (lambda d: d.update(desc={"text_base": "基金实时净值"}), "fund_reference_scope_changed"),
    (lambda d: d.update(worth_date="20261031"), "fund_reference_time_invalid"),
])
def test_reference_rejects_identity_clock_nonfinite_and_semantic_scope_changes(change, expected):
    value = payload()
    change(value["result"]["data"])
    with pytest.raises(FundPublicSourceError) as caught:
        reference(value)
    assert caught.value.code == expected


def test_conflicting_same_clock_is_not_chosen_by_first_or_last():
    value = payload()
    duplicate = deepcopy(value["result"]["data"]["networth"][-1])
    duplicate.update(pre_nav="9")
    value["result"]["data"]["networth"].append(duplicate)
    with pytest.raises(FundPublicSourceError) as caught:
        reference(value)
    assert caught.value.code == "fund_reference_duplicate_conflict"


def test_mismatched_fraction_and_percent_does_not_become_a_different_scaled_answer():
    value = payload()
    value["result"]["data"]["networth"][-1]["growthrate"] = -.28
    result = reference(value)
    assert result.state == "partial" and result.curves[0].latest_pct is None
    assert result.curves[1].latest_pct == -2.11
    assert "nav_pct_ratio_conflict" in result.missing


def test_empty_reference_is_unavailable_not_zero_percent():
    value = payload()
    value["result"]["data"]["networth"] = []
    result = reference(value)
    assert result.state == "unavailable" and result.source_as_of is None
    assert all(c.latest_pct is None for c in result.curves)


def test_profile_prefers_nonempty_manager_facts_preserves_supplier_benchmark_and_shares_reads():
    async def run():
        calls, saved = [], []
        async def reply(request):
            calls.append(str(request.url))
            assert not {"authorization", "x-api-key", "cookie"} & set(request.headers)
            return httpx.Response(200, content=MANAGER if request.url.host == "www.huaan.com.cn" else F10)
        service = FundPublicService(http_client=client_for(reply), evidence_writer=lambda e, b: saved.append((e, b)))
        try:
            a, b = await asyncio.gather(service.profile("040046.OF"), service.profile("040046.OF"))
            assert len(calls) == 2 and len(saved) == 2
            fields = {f.key: f for f in a.fields}
            assert fields["currency"].source == "huaan_fund"
            assert fields["benchmark"].source == "eastmoney_fund" and "95%" in fields["benchmark"].value
            assert fields["subscription_status"].value == "暂停申购"
            assert fields["name"].value == "华安纳斯达克100ETF联接（QDII）A"
            assert "name" not in a.conflicts and set(a.conflicts) == {"fund_type", "established_date"}
            assert not a.recommendation and "not_verified_regulatory_document" in a.document_scope
            assert all(e.raw_available for e in a.evidence)
            assert all(e.payload_sha256 == hashlib.sha256(body).hexdigest() for e, body in saved)
            a.fields[0].value = "caller mutation"
            assert b.fields[0].value != a.fields[0].value
            await service.profile("040046.OF")
            assert len(calls) == 2 and len(saved) == 2
        finally:
            await service.aclose()
    asyncio.run(run())


def test_bad_manager_identity_does_not_replace_good_supplier_fields():
    async def run():
        async def reply(request):
            return httpx.Response(200, content=MANAGER.replace(b"040046", b"040047")
                                  if request.url.host == "www.huaan.com.cn" else F10)
        service = FundPublicService(http_client=client_for(reply))
        try:
            result = await service.profile("040046.OF")
            fields = {f.key: f for f in result.fields}
            assert result.state == "partial" and fields["benchmark"].source == "eastmoney_fund"
            assert "currency" not in fields and "fund_public_identity_mismatch" in result.missing
        finally:
            await service.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("status", [301, 403, 429])
def test_unavailable_source_is_not_retried_redirected_or_invented(status):
    async def run():
        calls = []
        async def reply(request):
            calls.append(str(request.url))
            return httpx.Response(status, headers={"Location": "https://other.invalid/"})
        service = FundPublicService(http_client=client_for(reply))
        try:
            result = await service.reference("040046.OF")
            assert result.state == "unavailable" and result.curves == [] and len(calls) == 1
            assert result.estimated_fund_percent is None and result.source_as_of is None
        finally:
            await service.aclose()
    asyncio.run(run())


def test_same_reference_waiters_share_source_and_cancel_does_not_abandon_owned_work(monkeypatch):
    from app.assets import fund_public as module
    monkeypatch.setattr(module, "_now", lambda: evidence().received_at)
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def reply(request):
            calls.append(str(request.url)); started.set(); await release.wait()
            return httpx.Response(200, json=payload())
        service = FundPublicService(http_client=client_for(reply))
        first = asyncio.create_task(service.reference("040046.OF"))
        second = asyncio.create_task(service.reference("040046.OF"))
        await started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        close = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)
        assert not close.done() and len(calls) == 1
        release.set()
        assert (await second).state == "ready"
        await close
        assert not service._requests.tasks and not service._operations.tasks
        assert service._client.is_closed
    asyncio.run(run())


def test_disabled_or_closed_service_does_not_allocate_a_source_client():
    async def run():
        service = FundPublicService(enabled=False)
        assert (await service.profile("040046.OF")).missing == ["fund_public_disabled"]
        assert (await service.reference("040046.OF")).state == "unavailable"
        assert service._client is None
        await service.aclose()
        assert service._client is None
    asyncio.run(run())


def test_public_reader_cannot_borrow_a_keyed_client_or_expand_origins():
    async def run():
        client = httpx.AsyncClient(headers={"X-api-key": "fixture-only"})
        try:
            with pytest.raises(ValueError):
                FundPublicService(http_client=client)
        finally:
            await client.aclose()
        service = FundPublicService()
        try:
            for url, params in [("https://other.invalid/", {}), (SINA, {"symbol": "040046", "url": "x"}),
                                (f"{EASTMONEY}/jbgk_040046.html", {"secret": "x"})]:
                with pytest.raises(ValueError):
                    await service._raw(url, params)
            assert service._client is None
        finally:
            await service.aclose()
    asyncio.run(run())


def test_source_body_cap_and_evidence_failure_do_not_publish_a_reference(monkeypatch):
    from app.assets import fund_public as module
    monkeypatch.setattr(module, "_now", lambda: evidence().received_at)
    async def run():
        service = FundPublicService(http_client=client_for(lambda r: httpx.Response(200, content=b"x" * (MAX_BODY + 1))))
        try:
            result = await service.reference("040046.OF")
            assert result.missing == ["fund_public_too_large"] and result.curves == []
        finally:
            await service.aclose()
        def fail(evidence, raw):
            raise OSError("fixture write failure")
        service = FundPublicService(http_client=client_for(lambda r: httpx.Response(200, json=payload())), evidence_writer=fail)
        try:
            result = await service.reference("040046.OF")
            assert result.state == "unavailable" and not result.evidence
        finally:
            await service.aclose()
    asyncio.run(run())


def test_local_capacity_rejects_without_starting_a_different_public_read():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def reply(request):
            calls.append(str(request.url));entered.set();await release.wait()
            return httpx.Response(200, content=F10)
        service = FundPublicService(http_client=client_for(reply));service._requests = type(service._requests)(maxsize=1)
        running = asyncio.create_task(service._raw(f"{EASTMONEY}/jbgk_040046.html"))
        await entered.wait()
        with pytest.raises(LocalReadCapacityExceeded):
            await service._raw(f"{HUAAN}/funds/040046/index.shtml")
        assert len(calls) == 1
        release.set();await running;await service.aclose()
    asyncio.run(run())


def test_saturated_public_consumer_returns_typed_missing_without_dropping_other_fund_facts():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def reply(request):
            calls.append(str(request.url)); entered.set(); await release.wait()
            return httpx.Response(200, content=F10)
        service = FundPublicService(http_client=client_for(reply))
        service._operations = type(service._operations)(maxsize=1)
        running = asyncio.create_task(service.profile("040046.OF"))
        await entered.wait()
        profile, reference_result = await asyncio.gather(service.profile("270042.OF"), service.reference("000834.OF"))
        assert profile.state == reference_result.state == "unavailable"
        assert profile.missing == reference_result.missing == ["fund_public_local_busy"]
        assert len(calls) == 1
        release.set()
        result = await running
        assert any(field.key == "benchmark" for field in result.fields)
        await service.aclose()
    asyncio.run(run())
