"""Distinct official-product funds: offline identity, paging, clocks and reuse."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from app.core.bjtime import BJ_TZ
from app.core.ttl_cache import TTLCache
from app.core.inflight_reads import InflightReads, close_inflight_reads
from app.market import _board_changes_source as source, board_flow as bf


NOW = datetime(2026, 10, 26, 20, 30, tzinfo=BJ_TZ)
DIRECTORY = {"industry": {"BK0001": "fixture行业"}, "concept": {"BK0002": "fixture概念"}, "region": {"BK0003": "fixture地域"}}
HTML = '<ul class="pop_slider"><li>行业</li><li>概念</li><li>地域</li></ul>' + ''.join(
    '<div class="pop-cont">' + ''.join(f'<a href="/bkzj/{code}.html">{name}</a>' for code, name in rows.items()) + '</div>'
    for rows in DIRECTORY.values())


def page():
    return {"dt": 20261026150000, "tc": 4, "allbk": [
        {"c": code, "n": name, "zjl": amount, "u": pct}
        for code, name, amount, pct in [
            ("BK0001", "fixture行业", -12500, "-2.1"),
            ("BK0002", "fixture概念", 25000, "0"),
            ("BK0003", "fixture地域", 500, "1"),
            ("BK9999", "fixture未核", 40000, "8"),
        ]
    ]}


def snapshot():
    return source.build_snapshot([page()], DIRECTORY, NOW, [])


def test_official_labels_and_exact_code_name_define_kind_not_word_stems():
    assert source.decode_directory(HTML) == DIRECTORY
    x = snapshot()
    assert x["source_total"] == x["source_returned"] == 4
    assert x["unknown_codes"] == ["BK9999"] and x["excluded_region_codes"] == ["BK0003"]
    assert x["rows"]["industry"][0]["main_net_yi"] == -1.25
    assert x["rows"]["concept"][0]["main_net_yi"] == 2.5
    assert x["rows"]["concept"][0]["change_pct"] == 0
    assert x["rows"]["concept"][0]["main_net_5d_yi"] is None
    changed = page()
    changed["allbk"][1]["n"] = "fixture概念的自造别名"
    x = source.build_snapshot([changed], DIRECTORY, NOW, [])
    assert x["rows"]["concept"] == [] and x["name_mismatch_codes"] == ["BK0002"]


@pytest.mark.parametrize("bad", [HTML.replace("行业", "主题", 1), HTML.replace('BK0002', 'BK0001'), HTML.replace("概念</li>", "概念目录</li>")])
def test_directory_unknown_or_conflicting_group_is_not_guessed(bad):
    with pytest.raises(ValueError):
        source.decode_directory(bad)


@pytest.mark.parametrize("patch", [
    {"dt": 20261027150000}, {"dt": 20260230150000}, {"dt": None},
    {"tc": True}, {"tc": 9999}, {"tc": 5},
    {"allbk": [{"c": "BK0001", "n": "fixture行业", "zjl": True}] * 4},
])
def test_bad_clocks_totals_rows_and_nonfinite_do_not_become_empty_success(patch):
    p = page(); p.update(patch)
    with pytest.raises(ValueError):
        source.build_snapshot([p], DIRECTORY, NOW, [])


@pytest.mark.parametrize("field,value", [("zjl", float("nan")), ("zjl", float("inf")), ("u", True), ("zjl", "not-a-number")])
def test_invalid_numeric_evidence_is_not_zero(field, value):
    p = page(); p["allbk"][0][field] = value
    with pytest.raises(ValueError):
        source.build_snapshot([p], DIRECTORY, NOW, [])


def test_pages_require_same_batch_complete_counts_and_unique_identity():
    p = {"dt": 20261026150000, "tc": 51, "allbk": [
        {"c": f"BK{i:04d}", "n": "fixture", "zjl": i, "u": 0} for i in range(50)]}
    tail = {"dt": 20261026150000, "tc": 51, "allbk": [{"c": "BK0050", "n": "fixture", "zjl": 50, "u": 0}]}
    assert source.build_snapshot([p, tail], DIRECTORY, NOW, [])["source_returned"] == 51
    for patch in ({"dt": 20261026145900}, {"tc": 52}, {"allbk": []}, {"allbk": p["allbk"][:1]}):
        broken = {**tail, **patch}
        with pytest.raises(ValueError):
            source.build_snapshot([p, broken], DIRECTORY, NOW, [])


def test_fetch_reuses_seed_and_reads_only_missing_pages_and_formal_directory():
    calls = []
    class HTTP:
        @asynccontextmanager
        async def stream(self, method, url, params, headers):
            calls.append(url)
            class Response:
                def raise_for_status(self):
                    return None
                async def aiter_bytes(self):
                    yield HTML.encode()
            yield Response()
    x = asyncio.run(source.fetch_snapshot(HTTP(), lambda: NOW, seed=(page(), {"sha256": "a" * 64, "page": "0"})))
    assert calls == [source.DIRECTORY_URL]
    assert x["source_returned"] == 4 and len(x["source_receipts"]) == 2


def test_public_body_limit_stops_stream_without_additional_pages_or_directory():
    reads = []
    class HTTP:
        @asynccontextmanager
        async def stream(self, method, url, params, headers):
            class Response:
                def raise_for_status(self):
                    return None
                async def aiter_bytes(self):
                    for _ in range(3):
                        reads.append(1)
                        yield b"x" * 300000
            yield Response()
    with pytest.raises(ValueError, match="有界大小"):
        asyncio.run(source.fetch_snapshot(HTTP(), lambda: NOW))
    assert len(reads) == 2


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(bf, "_STORE_DIR", tmp_path)
    monkeypatch.setattr(bf, "beijing_now", lambda: NOW)
    monkeypatch.setattr(bf, "_CHANGES_CACHE", TTLCache("fixture-changes", 300, maxsize=2))
    monkeypatch.setattr(bf, "_CHANGES_FAILURES", TTLCache("fixture-changes-failure", 86400, maxsize=2))
    monkeypatch.setattr(bf, "_inflight_source_reads", InflightReads(), raising=False)
    return tmp_path


def test_immutable_archive_reuses_closing_batch_without_touching_f62_or_old_dates(isolated):
    prior = isolated / "daily.json"; prior.write_text('{"days":{"2026-10-23":{"boards":{}}}}')
    original = prior.read_bytes()
    x = snapshot()
    assert bf._persist_changes_snapshot(x)
    assert bf._persist_changes_snapshot(x)
    reread = {**x, "received_at": (NOW + timedelta(seconds=10)).isoformat(), "source_receipts": [{"sha256": "b" * 64}]}
    assert bf._persist_changes_snapshot(reread)
    assert bf._read_closed_changes_snapshot() == x
    assert len(list(isolated.rglob("*.json"))) == 2
    assert prior.read_bytes() == original
    changed = deepcopy(x); changed["rows"]["industry"][0]["main_net_yi"] = -4
    assert bf._persist_changes_snapshot(changed)
    assert len(list((isolated / "changes").rglob("*.json"))) == 2
    for path in (isolated / "changes").rglob("*.json"):
        payload = json.loads(path.read_text()); payload["received_at"] = (NOW + timedelta(days=1)).isoformat()
        path.write_text(json.dumps(payload))
    assert bf._read_closed_changes_snapshot() is None
    assert prior.read_bytes() == original


def test_actual_source_failure_stays_unavailable_fresh_bypasses_failure_and_success_cache(monkeypatch, isolated):
    from app.core import ttl_cache
    clock = [0.0]
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])
    calls = []

    async def fetch(http, now):
        calls.append(1)
        await asyncio.sleep(0)
        if len(calls) == 1:
            raise ValueError("fixture source outage")
        return snapshot()

    monkeypatch.setattr(source, "fetch_snapshot", fetch)
    monkeypatch.setattr(bf, "_http", lambda: None)

    async def go():
        failed = await asyncio.gather(*(bf.get_board_fund_flow("industry", "changes") for _ in range(8)))
        assert len(calls) == 1 and all(not x["available"] and not x["rows"] for x in failed)
        clock[0] = 29
        assert not (await bf.get_board_fund_flow("concept", "changes"))["available"]
        assert len(calls) == 1
        recovered = await asyncio.gather(*(bf.get_board_fund_flow("industry", "changes", fresh=True) for _ in range(8)))
        assert len(calls) == 2 and all(x["rows"][0]["main_net_yi"] == -1.25 for x in recovered)
        assert recovered[0]["source_as_of"] == "2026-10-26T15:00:00+08:00"
        assert recovered[0]["coverage"] == recovered[0]["directory_total"] == 1
        await bf.get_board_fund_flow("concept", "changes", fresh=True)
        assert len(calls) == 3

    asyncio.run(go())


def test_default_reads_saved_close_but_explicit_fresh_makes_a_real_source_attempt(monkeypatch, isolated):
    from app.core import ttl_cache
    clock = [0.0]
    monkeypatch.setattr(ttl_cache, "monotonic", lambda: clock[0])
    assert bf._persist_changes_snapshot(snapshot())
    calls = []
    async def fetch(http, now):
        calls.append(1)
        raise ValueError("fixture current failure")
    monkeypatch.setattr(source, "fetch_snapshot", fetch)
    monkeypatch.setattr(bf, "_http", lambda: None)
    async def go():
        x = await bf.get_board_fund_flow("concept", "changes")
        assert x["available"] and x["rows"][0]["main_net_yi"] == 2.5
        assert not calls
        failed = await bf.get_board_fund_flow("concept", "changes", fresh=True)
        assert not failed["available"] and failed["rows"] == [] and len(calls) == 1
        clock[0] = 30
        failed_again = await bf.get_board_fund_flow("concept", "changes")
        assert not failed_again["available"] and failed_again["rows"] == [] and len(calls) == 2
    asyncio.run(go())


def test_disconnected_reader_does_not_orphan_new_source_owner_or_archive_write(monkeypatch, isolated):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def fetch(http, now):
            entered.set()
            await release.wait()
            return snapshot()
        monkeypatch.setattr(source, "fetch_snapshot", fetch)
        monkeypatch.setattr(bf, "_http", lambda: None)
        reader = asyncio.create_task(bf.get_board_fund_flow("industry", "changes", fresh=True))
        await entered.wait()
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reader
        closing = asyncio.create_task(close_inflight_reads(bf))
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await closing
        assert len(list((isolated / "changes").rglob("*.json"))) == 1
        with pytest.raises(RuntimeError, match="closing"):
            await bf.get_board_fund_flow("industry", "changes")
    asyncio.run(go())


@pytest.mark.parametrize("age,expected", [(299,"current_batch"),(300,"stale_batch"),(3600,"stale_batch")])
def test_same_day_batch_uses_original_300_second_source_window_not_receipt_clock(monkeypatch, isolated, age, expected):
    now = datetime(2026, 10, 26, 10, 0, tzinfo=BJ_TZ)
    x = snapshot()
    x["source_as_of"] = (now - timedelta(seconds=age)).isoformat()
    x["received_at"] = now.isoformat()  # Late delivery does not refresh its source clock.
    monkeypatch.setattr(bf, "beijing_now", lambda: now)
    async def fetch(http, clock):
        return x
    monkeypatch.setattr(source, "fetch_snapshot", fetch)
    monkeypatch.setattr(bf, "_http", lambda: None)
    y = asyncio.run(bf.get_board_fund_flow("industry","changes"))
    assert y["available"] and y["rows"][0]["main_net_yi"] == -1.25
    assert y["observation_state"] == expected and y["quality"] == ("medium" if age < 300 else "stale")
    assert y["source_age_seconds"] == age and y["source_age_limit_seconds"] == 300
    assert y["source_as_of"] == x["source_as_of"] and y["received_at"] == now.isoformat()


def test_aging_is_recomputed_on_warm_payload_and_close_is_only_an_observation(monkeypatch, isolated):
    clock = [datetime(2026,10,26,10,0,tzinfo=BJ_TZ)]
    x = snapshot(); x["source_as_of"] = clock[0].isoformat(); x["received_at"] = clock[0].isoformat()
    monkeypatch.setattr(bf,"beijing_now",lambda:clock[0])
    calls=[]
    async def fetch(http,now):
        calls.append(1); return x
    monkeypatch.setattr(source,"fetch_snapshot",fetch)
    monkeypatch.setattr(bf,"_http",lambda:None)
    async def go():
        first=await bf.get_board_fund_flow("concept","changes")
        assert first["quality"]=="medium"
        clock[0]+=timedelta(seconds=300)
        aged=await bf.get_board_fund_flow("concept","changes")
        assert len(calls)==1 and aged["quality"]=="stale" and aged["source_age_seconds"]==300
        assert aged["received_at"]==first["received_at"] and aged["rows"]==first["rows"]
        bf._CHANGES_CACHE.invalidate()
        x["source_as_of"]="2026-10-26T15:00:00+08:00";x["received_at"]=NOW.isoformat();clock[0]=NOW
        closed=await bf.get_board_fund_flow("concept","changes",fresh=True)
        assert closed["quality"]=="stale" and closed["historical"] is True
        assert closed["observation_state"]=="closed_observation"
        assert closed["source_as_of"]=="2026-10-26T15:00:00+08:00"
    asyncio.run(go())
