"""Date-bound review fallback reuses the board owner and official identities."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta
import json

import httpx
import pytest

from app.core.bjtime import BJ_TZ
from app.market import board_flow as bf
from app.services.theme_catalog_service import ThemeCatalogService


DAY = date(2026, 10, 26)
PREVIOUS = date(2026, 10, 23)
NOW = datetime(2026, 10, 26, 18, 30, tzinfo=BJ_TZ)


@pytest.mark.parametrize("metadata", [
    {"thscode": "885999.TI", "interval": "1d", "adjust": None},
    {"thscode": "885001.TI", "interval": "1m", "adjust": None},
    {"thscode": "885001.TI", "interval": "1d", "adjust": "forward"},
    {"interval": "1d", "adjust": None},
])
def test_official_bars_reject_another_identity_or_contract(monkeypatch, metadata):
    """A valid-looking close from another board must never become this board's fact."""
    svc = ThemeCatalogService(None, api_key="test-key")

    async def response(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", "https://example.invalid"),
                              json={"code": 0, "data": {**metadata, "item": [
                                  {"date_ms": int(NOW.timestamp() * 1000), "close_price": 100},
                              ]}})

    monkeypatch.setattr(svc._client, "get", response)

    async def check():
        try:
            with pytest.raises(RuntimeError, match="identity|contract"):
                await svc.fetch_board_bars("885001.TI")
        finally:
            await svc._client.aclose()

    asyncio.run(check())


@pytest.fixture
def owner(monkeypatch, tmp_path):
    monkeypatch.setattr(bf, "_DAILY_STORE", tmp_path / "daily.json")
    monkeypatch.setattr(bf, "_DAYK_STORE", tmp_path / "daykline.json")
    monkeypatch.setattr(bf, "_mem_memo", {"daily": (None, {}), "dayk": (None, {})})
    monkeypatch.delattr(bf, "_inflight_source_reads", raising=False)
    monkeypatch.setattr(bf, "beijing_now", lambda: NOW)

    async def calendar(_provider):
        return [PREVIOUS, DAY]

    monkeypatch.setattr(bf.trade_calendar, "trading_days", calendar)
    return tmp_path


class Catalog:
    def __init__(self):
        self._board_fetch_sem = asyncio.Semaphore(6)
        self.calls = []
        self.bar_calls = []
        self.missing = set()
        self.wrong_day = set()
        self.bad_catalog = False
        self.bad_snapshot = False

    async def read_board_catalog(self, kind):
        self.calls.append(("catalog", kind))
        base = 881001 if kind == "industry" else 885001
        items = [{"code": f"{base+i}.TI", "name": f"正式{kind}{i}", "source": "ths_official"}
                 for i in range(4)]
        if self.bad_catalog:
            items.append(deepcopy(items[0]))
        return {"source": "ths_official", "catalog_state": "ready", "items": items, "total": len(items)}

    async def read_board_snapshots(self, codes):
        self.calls.append(("snapshot", tuple(codes)))
        rows = [{"code": code, "price": 100, "change_pct": i-2}
                for i, code in enumerate(codes)]
        if self.bad_snapshot:
            rows[0]["code"] = "889999.TI"
        return {"available": True, "rows": rows, "source_as_of": None,
                "source_ready_at": NOW.isoformat(), "received_at": NOW.isoformat()}

    async def fetch_board_bars(self, code, calendar_days=8):
        self.bar_calls.append(code)
        await asyncio.sleep(0)
        if code in self.missing:
            raise RuntimeError("source temporarily unavailable")
        day = "2026-10-22" if code in self.wrong_day else DAY.isoformat()
        return [{"date": PREVIOUS.isoformat(), "close": 100, "source_code": code,
                 "source": "ths_official_kline", "source_date_ms": 1792684800000},
                {"date": day, "close": 110 if code.endswith("001.TI") else 90,
                 "source_code": code, "source": "ths_official_kline",
                 "source_date_ms": int(datetime.fromisoformat(day).replace(tzinfo=BJ_TZ).timestamp() * 1000)}]


def collect(catalog, day=DAY, *, retry_missing=False):
    return asyncio.run(bf.collect_review_boards(object(), day, catalog_service=catalog, retry_missing=retry_missing))


def test_fallback_daily_facts_do_not_promote_snapshot_clock_or_fabricate_funds(owner):
    cat = Catalog()
    out = collect(cat)
    assert out["state"] == "partial"
    assert out["source_as_of"] is None
    assert out["source_trade_date"] == "2026-10-26"
    assert len(cat.bar_calls) == 8
    rows = out["boards"]["industry"]
    assert {row["board_code"] for row in rows} == {f"88100{i}.TI" for i in range(1, 5)}
    assert rows[0]["change_pct"] == 10
    assert all(row["main_net_yi"] is None and row["source_as_of"] is None for row in rows)
    assert all(row["source_trade_date"] == "2026-10-26" for row in rows)
    assert all(row["source_date_ms"] == 1792944000000 for row in rows)
    assert out["summary"]["industry"]["ranking_basis"] == "official_daily_candidate_sample"
    assert out["summary"]["industry"]["verified_count"] == 4
    assert "样本" in out["reason"] and "全市场" in out["reason"]


def test_saved_owner_version_is_reused_for_same_day_and_history_without_source_reads(owner, monkeypatch):
    cat = Catalog()
    first = collect(cat)
    count = len(cat.calls), len(cat.bar_calls)
    assert collect(cat) == first
    assert (len(cat.calls), len(cat.bar_calls)) == count
    monkeypatch.setattr(bf, "beijing_now", lambda: datetime(2026, 10, 27, 18, tzinfo=BJ_TZ))
    assert collect(cat) == first
    assert (len(cat.calls), len(cat.bar_calls)) == count


def test_missing_history_never_queries_current_source(owner):
    cat = Catalog()
    out = collect(cat, PREVIOUS)
    assert out["state"] == "unavailable"
    assert not cat.calls and not cat.bar_calls


def test_unknown_calendar_does_not_fetch_or_save(owner, monkeypatch):
    async def unknown(_provider):
        return [PREVIOUS]

    monkeypatch.setattr(bf.trade_calendar, "trading_days", unknown)
    cat = Catalog()
    assert collect(cat)["state"] == "unavailable"
    assert not cat.calls and not (owner / "daily.json").exists()


@pytest.mark.parametrize("issue", ["bad_catalog", "bad_snapshot"])
def test_wrong_or_duplicate_source_identities_never_produce_a_rank(owner, issue):
    cat = Catalog()
    setattr(cat, issue, True)
    out = collect(cat)
    assert not out["boards"]["industry"] and not out["boards"]["concept"]
    assert not cat.bar_calls


def test_failed_and_wrong_day_bars_are_missing_not_zero(owner):
    cat = Catalog()
    cat.missing.add("881001.TI")
    cat.wrong_day.add("881002.TI")
    out = collect(cat)
    assert {x["board_code"] for x in out["boards"]["industry"]} == {"881003.TI", "881004.TI"}
    assert out["summary"]["industry"]["missing_count"] == 2
    assert out["summary"]["industry"]["verified_count"] == 2


def test_original_bar_date_label_must_match_its_declared_day(owner):
    cat = Catalog()
    original = cat.fetch_board_bars

    async def wrong_label(code, calendar_days=8):
        bars = await original(code, calendar_days)
        bars[-1]["source_date_ms"] = 1792684800000
        return bars

    cat.fetch_board_bars = wrong_label
    out = collect(cat)
    assert out["state"] == "unavailable"
    assert not out["boards"]["industry"] and not out["boards"]["concept"]
    assert out["summary"]["industry"]["missing_count"] == 4


def test_actual_service_keeps_the_original_date_label_and_source_identity(monkeypatch):
    svc = ThemeCatalogService(None, api_key="test-key")

    async def response(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", "https://example.invalid"),
                              json={"code": 0, "data": {"thscode": "885001.TI",
                                  "interval": "1d", "adjust": None, "item": [
                                      {"date_ms": 1792944000000, "close_price": 100},
                                  ]}})

    monkeypatch.setattr(svc._client, "get", response)

    async def check():
        try:
            bars = await svc.fetch_board_bars("885001.TI")
            assert bars[0]["source_code"] == "885001.TI"
            assert bars[0]["source_date_ms"] == 1792944000000
            assert bars[0]["date"] == "2026-10-26"
        finally:
            await svc._client.aclose()

    asyncio.run(check())


def test_owner_original_funds_and_review_payload_preserve_each_other(owner, monkeypatch):
    original = {"boards": {"industry": [["BK0001", "同名概念", 3.5, 10, 2]]},
                "ranks": {"industry": {"BK0001": 1}}, "members": {}}
    (owner / "daily.json").write_text(json.dumps({"days": {DAY.isoformat(): original}}))
    out = collect(Catalog())
    saved = json.loads((owner / "daily.json").read_text())
    assert saved["days"][DAY.isoformat()]["boards"] == original["boards"]
    assert saved["days"][DAY.isoformat()]["review_source"]["payload"] == out
    assert all(row["board_code"] != "BK0001" for row in out["boards"]["industry"])
    stale_funds_writer = {"days": {DAY.isoformat(): deepcopy(original)}}
    assert bf._write_json_atomic(owner / "daily.json", stale_funds_writer)
    reread = json.loads((owner / "daily.json").read_text())["days"][DAY.isoformat()]
    assert reread["review_source"]["payload"] == out


def test_concurrent_reviews_share_the_existing_board_read_owner(owner):
    cat = Catalog()

    async def run():
        return await asyncio.gather(*[bf.collect_review_boards(object(), DAY, catalog_service=cat)
                                      for _ in range(3)])

    results = asyncio.run(run())
    assert results[0] == results[1] == results[2]
    assert len(cat.bar_calls) == 8


def test_candidate_bound_selects_strong_and_weak_without_full_history_sweep(owner):
    cat = Catalog()

    async def directory(kind):
        base = 881000 if kind == "industry" else 885000
        return {"source": "ths_official", "catalog_state": "ready", "total": 80,
                "items": [{"code": f"{base+i}.TI", "name": f"正式{kind}{i}"} for i in range(80)]}

    cat.read_board_catalog = directory
    out = collect(cat)
    assert len(cat.bar_calls) == 40
    assert len(set(cat.bar_calls)) == 40
    assert out["summary"]["industry"]["universe_count"] == 80
    assert out["summary"]["industry"]["candidate_count"] == 20


def test_explicit_recovery_only_fetches_the_missing_frozen_bar(owner, monkeypatch):
    cat = Catalog()
    cat.missing.add("881001.TI")
    first = collect(cat)
    before = (owner / "daily.json").read_text()
    first_version = json.loads(before)["days"][DAY.isoformat()]["review_source"]["frozen_version"]
    calls = len(cat.calls), len(cat.bar_calls)
    cat.missing.clear()
    monkeypatch.setattr(bf, "beijing_now", lambda: NOW + timedelta(seconds=61))
    assert collect(cat) == first, "normal reads cannot trigger recovery"
    recovered = collect(cat, retry_missing=True)
    assert recovered["summary"]["industry"]["verified_count"] == 4
    assert recovered["boards"]["concept"] == first["boards"]["concept"]
    assert len(cat.calls) == calls[0], "frozen directory and candidates are not read again"
    assert len(cat.bar_calls) == calls[1] + 1
    assert cat.bar_calls[-1] == "881001.TI"
    saved = json.loads((owner / "daily.json").read_text())["days"][DAY.isoformat()]
    assert saved["review_history"][0]["payload"] == first
    assert saved["review_history"][0]["frozen_version"] == first_version
    assert saved["review_source"]["frozen_version"] != first_version
    assert collect(cat, retry_missing=True) == recovered
    assert len(cat.bar_calls) == calls[1] + 1, "successful recovery must remain reusable"


def test_recovery_cooldown_and_historical_reads_never_call_sources(owner, monkeypatch):
    cat = Catalog()
    cat.missing.add("881001.TI")
    first = collect(cat)
    calls = len(cat.calls), len(cat.bar_calls)
    cat.missing.clear()
    monkeypatch.setattr(bf, "beijing_now", lambda: NOW + timedelta(seconds=59))
    assert collect(cat, retry_missing=True) == first
    monkeypatch.setattr(bf, "beijing_now", lambda: NOW + timedelta(days=1))
    assert collect(cat, retry_missing=True) == first
    assert (len(cat.calls), len(cat.bar_calls)) == calls


def test_recovery_retries_only_a_failed_kind_and_keeps_the_successful_kind(owner, monkeypatch):
    cat = Catalog()
    original = cat.read_board_catalog

    async def unavailable(kind):
        if kind == "concept":
            raise RuntimeError("concept directory unavailable")
        return await original(kind)

    cat.read_board_catalog = unavailable
    first = collect(cat)
    assert first["boards"]["industry"] and not first["boards"]["concept"]
    call_count = len(cat.calls)
    bar_count = len(cat.bar_calls)
    cat.read_board_catalog = original
    monkeypatch.setattr(bf, "beijing_now", lambda: NOW + timedelta(seconds=61))
    recovered = collect(cat, retry_missing=True)
    assert recovered["boards"]["industry"] == first["boards"]["industry"]
    assert len(recovered["boards"]["concept"]) == 4
    assert cat.calls[call_count:] == [("catalog", "concept"), ("snapshot", tuple(f"88500{i}.TI" for i in range(1, 5)))]
    assert len(cat.bar_calls) == bar_count + 4


def test_repeated_failure_retains_original_reason_and_only_attempts_once(owner, monkeypatch):
    cat = Catalog()
    cat.missing.add("881001.TI")
    first = collect(cat)
    calls = len(cat.bar_calls)
    monkeypatch.setattr(bf, "beijing_now", lambda: NOW + timedelta(seconds=61))
    again = collect(cat, retry_missing=True)
    missing = again["summary"]["industry"]["missing"][0]
    assert missing["previous_reasons"] == [first["summary"]["industry"]["missing"][0]["reason"]]
    assert len(cat.bar_calls) == calls + 1
    assert collect(cat, retry_missing=True) == again
    assert len(cat.bar_calls) == calls + 1, "a failed recovery still starts the cooldown"


def test_old_funds_snapshot_and_stale_cas_cannot_overwrite_a_recovered_version(owner, monkeypatch):
    cat = Catalog()
    cat.missing.add("881001.TI")
    collect(cat)
    stale = json.loads((owner / "daily.json").read_text())
    version = stale["days"][DAY.isoformat()]["review_source"]["frozen_version"]
    cat.missing.clear()
    monkeypatch.setattr(bf, "beijing_now", lambda: NOW + timedelta(seconds=61))
    recovered = collect(cat, retry_missing=True)
    assert not bf._write_json_atomic(owner / "daily.json", stale, review_versions={DAY.isoformat(): version})
    assert bf._write_json_atomic(owner / "daily.json", stale), "ordinary funds writes may preserve the newer review"
    saved = json.loads((owner / "daily.json").read_text())["days"][DAY.isoformat()]
    assert saved["review_source"]["payload"] == recovered
    assert len(saved["review_history"]) == 1


def test_concurrent_explicit_recovery_shares_one_missing_read(owner, monkeypatch):
    cat = Catalog()
    cat.missing.add("881001.TI")
    collect(cat)
    calls = len(cat.bar_calls)
    cat.missing.clear()
    monkeypatch.setattr(bf, "beijing_now", lambda: NOW + timedelta(seconds=61))

    async def run():
        return await asyncio.gather(*[bf.collect_review_boards(object(), DAY, catalog_service=cat,
                                                              retry_missing=True) for _ in range(3)])

    values = asyncio.run(run())
    assert values[0] == values[1] == values[2]
    assert len(cat.bar_calls) == calls + 1
    assert len(json.loads((owner / "daily.json").read_text())["days"][DAY.isoformat()]["review_history"]) == 1
