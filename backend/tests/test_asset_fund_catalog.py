"""Small public-response excerpts; no key, live source, account or database."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import httpx
import pytest

from app.assets.funds import AssetSourceError, parse_catalog
from app.assets.service import AssetReadService, NAV, QUOTA, SEARCH
from app.core.errors import AppError
from app.core.inflight_reads import LocalReadCapacityExceeded
from app.schemas.assets import AssetEvidence


def row(code="040046.OF", name="华安纳斯达克100ETF联接(QDII)A", quota="0", year="7.39", classify=None):
    return dict(thscode=code, fund_name=name, quota=quota, year=year, classify=classify)


def source_data():
    # Excerpts retain the real source's duplicate and source-scope counterexamples.
    a = row()
    b = row("019524.OF", "华泰柏瑞纳斯达克100ETF发起式联接(QDII)A", "5.00", "7.59", ["A", "C", "I"])
    c = row("501312.SH", "海外科技LOF", "20.00", "14.15", [])
    d = row("539002.OF", "建信新兴市场混合(QDII)A人民币", "100.00", "56.68", [])
    return [{"name": "meiguo", "sub_tab": [
        {"name": "all", "fund_list": deepcopy([a, b, c, d, a])},
        {"name": "nazhi100", "fund_list": deepcopy([a, b])},
        {"name": "meigukeji", "fund_list": deepcopy([c, d])},
        {"name": "nazhi", "fund_list": deepcopy([a])},
    ]}]


def evidence():
    return AssetEvidence(endpoint=QUOTA, params={"tab": '["meiguo"]'},
                         received_at=datetime(2026, 10, 30, 3, 51, 21, tzinfo=timezone.utc),
                         payload_sha256="a" * 64, receipt_id="b" * 64)


def parse(data=None):
    return parse_catalog(source_data() if data is None else data, "all", evidence())


def one_row(value):
    return [{"name": "meiguo", "sub_tab": [{"name": "all", "fund_list": [value]}]}]


def test_all_memberships_are_preserved_without_inventing_identities_or_mandates():
    result = parse()
    assert result.state == "ready" and result.coverage.complete_source_response
    assert result.coverage.model_dump() == dict(source_rows=10, unique_symbols=4, of_symbols=3,
        candidate_symbols=3, out_of_scope_symbols=1, conflict_symbols=0, placeholder_count=0,
        excluded_rows=0, complete_source_response=True)
    assert [group.source_key for group in result.groups] == ["all", "nazhi100", "meigukeji", "nazhi"]
    assert [item.source_symbol for item in result.items] == ["040046.OF", "019524.OF", "501312.SH", "539002.OF"]
    candidate = result.items[0]
    assert candidate.source_name == "华安纳斯达克100ETF联接(QDII)A"
    assert candidate.occurrence_count == 4
    assert [(m.group_key, m.row_position) for m in candidate.memberships] == [("all", 0), ("all", 4), ("nazhi100", 0), ("nazhi", 0)]
    assert candidate.quota_raw == "0" and candidate.quota_status == "reported"
    assert candidate.quota_unit is None and candidate.year_raw == "7.39"
    assert result.items[1].classify == ["A", "C", "I"]  # hints cannot create sibling codes
    assert result.items[2].state == "out_of_scope"
    assert "currency" not in candidate.model_dump() and "instrument_id" not in candidate.model_dump()
    assert result.source_as_of is None and not result.recommendation
    assert "新兴市场" in result.scope_note and "不等于" in result.scope_note


def test_conflicting_duplicate_is_isolated_not_resolved_by_first_or_maximum():
    data = source_data()
    data[0]["sub_tab"][3]["fund_list"][0].update(fund_name="不同来源名称", quota="100", year="99")
    result = parse(data)
    item = result.items[0]
    assert result.state == "partial" and result.coverage.conflict_symbols == 1
    assert item.state == "conflict" and item.conflicting_fields == ["fund_name", "quota", "year"]
    assert item.source_name is None and item.quota_raw is None and item.year_raw is None
    assert item.quota_status == "conflict" and item.occurrence_count == 4
    assert result.items[1].state == "candidate"


def test_absent_and_explicit_null_in_duplicate_rows_cannot_be_called_unlimited():
    data = one_row(row(quota=None))
    missing_quota = row()
    missing_quota.pop("quota")
    data[0]["sub_tab"][0]["fund_list"].append(missing_quota)
    item = parse(data).items[0]
    assert item.state == "conflict" and item.quota_status == "conflict"
    assert item.conflicting_fields == ["quota"]


@pytest.mark.parametrize("kind,status,missing", [
    ("null", "unlimited", False), ("missing", "unknown", True),
    ("zero", "reported", False), ("empty", "unknown", True),
])
def test_explicit_null_is_unlimited_but_absence_and_zero_are_different(kind, status, missing):
    value = row()
    if kind == "null":
        value["quota"] = None
    elif kind == "missing":
        value.pop("quota")
    elif kind == "empty":
        value["quota"] = ""
    result = parse(one_row(value))
    item = result.items[0]
    assert item.quota_status == status and bool(item.missing) == missing
    assert item.quota_unit is None
    assert "purchase_allowed" not in item.model_dump()


@pytest.mark.parametrize("data,reason", [
    (None, "source_catalog_null"), ([None], "source_tab_null"),
    ([{"name": "meiguo", "sub_tab": None}], "source_groups_missing"),
    ([{"name": "meiguo", "sub_tab": [None]}], "source_group_null"),
    ([{"name": "meiguo", "sub_tab": [{"name": "all", "fund_list": None}]}], "source_group_members_missing"),
    (one_row(None), "source_fund_null"),
    (one_row({"thscode": None}), "source_symbol_missing"),
])
def test_documented_null_placeholders_are_unknown_not_an_empty_complete_universe(data, reason):
    result = parse_catalog(data, "all", evidence())
    assert result.state == "unavailable" and reason in result.missing
    assert not result.coverage.complete_source_response and result.coverage.placeholder_count == 1
    assert result.items == [] and result.evidence[0].payload_sha256


@pytest.mark.parametrize("data", [
    {"item": []}, ["bad"], [{"name": "meiguo", "sub_tab": "bad"}],
    [{"name": "meiguo", "sub_tab": [{"name": "all", "fund_list": "bad"}]}],
    one_row("bad"), one_row(row()) * 9,
])
def test_malformed_quota_shapes_fail_instead_of_silently_dropping(data):
    with pytest.raises(AssetSourceError):
        parse(data)


def test_oversized_directory_fails_before_partial_first_page_is_returned():
    data = one_row(row())
    data[0]["sub_tab"][0]["fund_list"] *= 2001
    with pytest.raises(AssetSourceError) as caught:
        parse(data)
    assert caught.value.code == "fund_catalog_too_large"


def test_out_of_scope_invalid_identity_and_null_rows_have_separate_coverage():
    data = source_data()
    data[0]["sub_tab"][0]["fund_list"].extend([row("040046"), None])
    result = parse(data)
    assert result.coverage.source_rows == 12 and result.coverage.unique_symbols == 4
    assert result.coverage.out_of_scope_symbols == 1 and result.coverage.excluded_rows == 2
    assert result.coverage.placeholder_count == 1
    assert {item.reason for item in result.excluded} == {"invalid_source_symbol", "source_fund_null"}
    assert [item.source_symbol for item in result.items].count("040046.OF") == 1


def service_for(handler, evidence_dir=None):
    client = httpx.AsyncClient(base_url="https://fuyao.aicubes.cn", transport=httpx.MockTransport(handler))
    return AssetReadService("fixture-key", http_client=client, evidence_dir=evidence_dir)


def test_views_have_separate_requests_clocks_hashes_and_do_not_query_fund_metadata(monkeypatch, tmp_path):
    from app.assets import service as module
    clock = iter([datetime(2026, 10, 30, 3, 51, 21, tzinfo=timezone.utc),
                  datetime(2026, 10, 30, 3, 53, 21, tzinfo=timezone.utc)])
    monkeypatch.setattr(module, "_utcnow", lambda: next(clock))
    async def run():
        calls = []
        async def reply(request):
            calls.append((request.url.path, dict(request.url.params)))
            data = source_data() if "buy" not in request.url.params else one_row(row(quota="10.00"))
            return httpx.Response(200, json={"code": 0, "data": data})
        service = service_for(reply, tmp_path)
        try:
            all_items = await service.catalog()
            buyable = await service.catalog("source_buyable")
            assert calls == [(QUOTA, {"tab": '["meiguo"]'}), (QUOTA, {"tab": '["meiguo"]', "buy": "true"})]
            assert all_items.evidence[0].received_at < buyable.evidence[0].received_at
            assert all_items.evidence[0].payload_sha256 != buyable.evidence[0].payload_sha256
            assert all_items.items[0].quota_raw == "0" and buyable.items[0].quota_raw == "10.00"
            assert all_items.purchase == "all" and buyable.purchase == "source_buyable"
            all_items.items[0].source_name = "modified-return-copy"
            cached = await service.catalog()
            assert cached.items[0].source_name == row()["fund_name"] and len(calls) == 2
            assert len(list(tmp_path.glob("*.body.json"))) == len(list(tmp_path.glob("*.receipt.json"))) == 2
        finally:
            await service.aclose()
    asyncio.run(run())


def test_quota_nested_shape_does_not_relax_nav_contract():
    async def run():
        async def reply(request):
            return httpx.Response(200, json={"code": 0, "data": source_data()})
        service = service_for(reply)
        try:
            assert (await service.catalog()).coverage.unique_symbols == 4
            with pytest.raises(AssetSourceError):
                await service._raw(NAV, {"thscode": "040046.OF", "range": "year", "nav_type": "unit,adj"})
        finally:
            await service.aclose()
    asyncio.run(run())


def test_429_does_not_retry_or_turn_an_independent_good_view_into_not_buyable():
    async def run():
        calls = []
        async def reply(request):
            calls.append(dict(request.url.params))
            if "buy" in request.url.params:
                return httpx.Response(429)
            return httpx.Response(200, json={"code": 0, "data": source_data()})
        service = service_for(reply)
        try:
            before = await service.catalog()
            with pytest.raises(AssetSourceError):
                await service.catalog("source_buyable")
            after = await service.catalog()
            assert before == after and len(calls) == 2
        finally:
            await service.aclose()
    asyncio.run(run())


def test_same_view_waiters_share_owned_request_and_cancellation_does_not_replay():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def reply(request):
            calls.append(dict(request.url.params))
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"code": 0, "data": source_data()})
        service = service_for(reply)
        first = asyncio.create_task(service.catalog())
        try:
            await entered.wait()
            second = asyncio.create_task(service.catalog())
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            result = await second
            assert result.coverage.unique_symbols == 4 and len(calls) == 1
        finally:
            release.set()
            await service.aclose()
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


def test_catalog_respects_existing_source_capacity_without_fallback():
    async def run():
        release, started = asyncio.Event(), asyncio.Event()
        calls = []
        async def reply(request):
            calls.append(request.url.path)
            if len(calls) == 4:
                started.set()
            await release.wait()
            return httpx.Response(200, json={"code": 0, "data": {"item": []}})
        service = service_for(reply)
        pending = [asyncio.create_task(service._raw(SEARCH, {"q": str(i)})) for i in range(4)]
        try:
            await started.wait()
            with pytest.raises(LocalReadCapacityExceeded):
                await service.catalog()
            assert calls == [SEARCH] * 4
        finally:
            release.set()
            await asyncio.gather(*pending)
            await service.aclose()
    asyncio.run(run())


def test_bad_tab_is_not_cached_and_parameters_cannot_expand_source_scope():
    async def run():
        calls = []
        async def reply(request):
            calls.append(dict(request.url.params))
            data = source_data()
            if len(calls) == 1:
                data[0]["name"] = "other_region"
            return httpx.Response(200, json={"code": 0, "data": data})
        service = service_for(reply)
        try:
            with pytest.raises(AssetSourceError, match="栏目不一致"):
                await service.catalog()
            assert (await service.catalog()).coverage.unique_symbols == 4 and len(calls) == 2
            for purchase, tab in (("all", "global"), ("true", "meiguo")):
                with pytest.raises(AppError):
                    await service.catalog(purchase, tab)
            assert len(calls) == 2
        finally:
            await service.aclose()
    asyncio.run(run())


def test_expired_catalog_cache_refetches_without_changing_the_other_view(monkeypatch):
    from app.core import ttl_cache
    tick = {"now": 10.0}
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: tick["now"])
    async def run():
        calls = []
        async def reply(request):
            calls.append(dict(request.url.params))
            return httpx.Response(200, json={"code": 0, "data": one_row(row(quota=str(len(calls))))})
        service = service_for(reply)
        try:
            first = await service.catalog()
            tick["now"] += 100
            buy = await service.catalog("source_buyable")
            tick["now"] += 201
            next_all = await service.catalog()
            same_buy = await service.catalog("source_buyable")
            assert first.items[0].quota_raw == "1" and next_all.items[0].quota_raw == "3"
            assert buy == same_buy and len(calls) == 3
        finally:
            await service.aclose()
    asyncio.run(run())


def test_unchanged_body_after_ttl_has_distinct_receipts_with_matching_receive_clocks(monkeypatch, tmp_path):
    from app.assets import service as module
    from app.core import ttl_cache
    tick = {"now": 10.0}
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: tick["now"])
    clock = iter([datetime(2026, 10, 30, 3, 51, 21, tzinfo=timezone.utc),
                  datetime(2026, 10, 30, 3, 57, 21, tzinfo=timezone.utc)])
    monkeypatch.setattr(module, "_utcnow", lambda: next(clock))
    async def run():
        calls = []
        async def reply(request):
            calls.append(dict(request.url.params))
            return httpx.Response(200, json={"code": 0, "data": source_data()})
        service = service_for(reply, tmp_path)
        try:
            first = (await service.catalog()).evidence[0]
            old_path = tmp_path / f"{first.receipt_id}.receipt.json"
            old_content = old_path.read_bytes()
            old_mtime = old_path.stat().st_mtime_ns
            tick["now"] += 301
            second = (await service.catalog()).evidence[0]
            assert len(calls) == 2 and first.received_at != second.received_at
            assert first.payload_sha256 == second.payload_sha256 and first.receipt_id != second.receipt_id
            assert old_path.read_bytes() == old_content and old_path.stat().st_mtime_ns == old_mtime
            assert len(list(tmp_path.glob("*.body.json"))) == 1
            assert len(list(tmp_path.glob("*.receipt.json"))) == 2
            for item in (first, second):
                saved = json.loads((tmp_path / f"{item.receipt_id}.receipt.json").read_text())
                assert datetime.fromisoformat(saved["received_at"]) == item.received_at
                assert saved["params"] == item.params and saved["receipt_id"] == item.receipt_id
                assert hashlib.sha256((tmp_path / saved["raw_file"]).read_bytes()).hexdigest() == item.payload_sha256
                assert item.raw_available
        finally:
            await service.aclose()
    asyncio.run(run())


def test_slow_stream_is_bounded_by_total_deadline_not_each_chunk(monkeypatch):
    from app.assets import service as module
    real_timeout = asyncio.timeout
    # Only compress the existing 10-second transport deadline. The event loop's
    # own monotonic clock and real cooperative waits continue normally.
    monkeypatch.setattr(module.asyncio, "timeout", lambda seconds: real_timeout(0.05 if seconds == 10 else seconds))
    async def run():
        progress = []
        closed = []
        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                for i in range(30):
                    await asyncio.sleep(0.01)
                    progress.append(i)
                    yield b" "
                yield json.dumps({"code": 0, "data": source_data()}).encode()
            async def aclose(self):
                closed.append(True)
        async def reply(request):
            return httpx.Response(200, stream=SlowBody())
        service = service_for(reply)
        try:
            with pytest.raises(AssetSourceError) as caught:
                await service.catalog()
            assert caught.value.code == "fund_source_request_failed"
            assert len(progress) < 30 and closed == [True]
            assert not service._requests.tasks
        finally:
            await service.aclose()
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())
