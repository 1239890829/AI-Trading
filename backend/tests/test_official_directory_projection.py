"""One source identity projection is shared without changing trading eligibility."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from app.services import official_match as official


def directory(kind, rows):
    return {"kind": kind, "source": "ths_official", "catalog_state": "ready",
            "synced_at": "2026-10-27T08:00:00+00:00", "total": len(rows), "items": [
                {"code": code, "name": name, "kind": kind, "source": "ths_official"}
                for code, name in rows]}


def service(concepts=(("885001.TI", "无关正式概念"),), industries=(("881126.TI", "汽车零部件"),)):
    values = {"concept": directory("concept", concepts), "industry": directory("industry", industries)}
    calls = []

    async def read(kind):
        calls.append(kind)
        value = values[kind]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    svc = NS(read_board_catalog=read, revision=0)
    return svc, values, calls


def test_exact_industry_maps_without_guessing_raw_label_or_granting_candidate():
    svc, _, calls = service(concepts=(("886069.TI", "人形机器人"),))
    cards = [{"theme": name, "stocks": [{"symbol": "001317"}]} for name in ("汽车零部件", "具身智能")]
    original = deepcopy(cards)
    asyncio.run(official.attach_official_async(NS(theme_catalog=svc), cards))
    identity = cards[0]["exact_catalog"]
    assert identity["code"] == "881126.TI" and identity["kind"] == "industry"
    assert identity["basis"] == "exact_name" and identity["source"] == "ths_official"
    assert len(identity["catalog_version"]) == 64 and identity["observed_at"].endswith("+00:00")
    assert cards[0]["catalog_mapping_state"] == "exact" and cards[0]["candidate_catalog_code"] is None
    assert cards[1]["catalog_mapping_state"] == "unmapped" and cards[1]["exact_catalog"] is None
    assert [card["theme"] for card in cards] == [card["theme"] for card in original]
    assert set(calls) == {"concept", "industry"}


def test_cross_kind_same_name_is_ambiguous_without_arbitrary_choice():
    svc, _, _ = service(concepts=(("886001.TI", "同名"),), industries=(("881126.TI", "同名"),))
    cards = [{"theme": "同名"}]
    asyncio.run(official.attach_official_async(NS(theme_catalog=svc), cards))
    assert cards[0]["catalog_mapping_state"] == "ambiguous"
    assert cards[0]["exact_catalog"] is cards[0]["catalog_code"] is None


@pytest.mark.parametrize("broken", [OSError("offline"), {"kind": "industry", "source": "ths_official",
    "catalog_state": "ready", "items": [], "total": 320},
    {"kind": "industry", "source": "ths_official", "catalog_state": "ready", "items": [], "total": 0},
    directory("industry", [("881126.TI", "  ")])])
def test_partial_directory_cannot_claim_unmapped_or_unique_identity(broken):
    svc, values, _ = service(concepts=(("886001.TI", "同名"),))
    values["industry"] = broken
    cards = [{"theme": "同名"}, {"theme": "未见标签"}]
    asyncio.run(official.attach_official_async(NS(theme_catalog=svc), cards))
    assert all(card["catalog_mapping_state"] == "unavailable" for card in cards)
    assert all(card["exact_catalog"] is None for card in cards)


def test_member_or_navigation_failure_does_not_erase_exact_identity():
    svc, _, _ = service(concepts=(("885001.TI", "已核概念"),))

    def fail(*args, **kwargs):
        raise OSError("optional member read failed")

    svc.get_catalog = svc.member_symbols_bulk = svc.get_members = fail
    cards = [{"theme": "已核概念", "stocks": [{"symbol": "600001", "official": True}]}]
    asyncio.run(official.attach_official_async(NS(theme_catalog=svc), cards))
    assert cards[0]["exact_catalog"]["code"] == "885001.TI"
    assert cards[0]["catalog_mapping_state"] == "exact" and cards[0]["candidate_catalog_code"] is None
    assert "official" not in cards[0]["stocks"][0], "a failed membership read is unknown, not false"


def test_resolved_projection_is_frozen_even_if_directory_changes_before_attachment():
    svc, values, _ = service()

    async def run():
        holder = NS(theme_catalog=svc)
        resolved = await official.resolve_official_catalog(holder)
        values["industry"] = directory("industry", [("881999.TI", "新名称")])
        svc.revision = 1
        cards = [{"theme": "汽车零部件"}, {"theme": "无关正式概念", "stocks": [{"symbol": "600001"}]}]
        await official.attach_official_async(holder, cards, resolution=resolved)
        assert cards[0]["exact_catalog"]["code"] == "881126.TI"
        assert cards[0]["exact_catalog"]["catalog_version"] == resolved["catalog_version"]
        assert cards[1]["exact_catalog"]["code"] == "885001.TI"
        assert cards[1]["candidate_catalog_code"] is None and "official" not in cards[1]["stocks"][0]

    asyncio.run(run())


def test_current_reference_is_separate_ordered_and_does_not_mutate_saved_facts():
    svc, _, calls = service()
    saved = {"themes": [{"theme": "汽车零部件", "old_metric": 8.0}, {"theme": "具身智能"}]}
    original = deepcopy(saved)

    async def run():
        holder = NS(theme_catalog=svc)
        empty = await official.current_catalog_reference(holder, [None, "", " "])
        assert empty["items"] == [] and calls == []
        ref = await official.current_catalog_reference(holder, ["汽车零部件", "具身智能", "汽车零部件"])
        assert ref["scope"] == "current_directory_reference"
        assert [item["theme"] for item in ref["items"]] == ["汽车零部件", "具身智能"]
        assert ref["items"][0]["exact_catalog"]["kind"] == "industry"
        assert ref["items"][1]["catalog_mapping_state"] == "unmapped"
        assert saved == original and "exact_catalog" not in saved["themes"][0]

    asyncio.run(run())


def test_industry_identity_uses_only_its_exact_kline_code_and_keeps_metrics_separate():
    from datetime import date
    from app.api.routes import market_themes

    svc, _, _ = service()
    calls = []

    async def bars(code):
        calls.append(code)
        return [{"date": f"2026-10-{day:02d}", "close": 100 + day} for day in range(1, 13)]

    svc.fetch_board_bars = bars
    holder = NS(app=NS(state=NS(theme_catalog=svc)))
    card = {"theme": "汽车零部件", "candidate_board": {"name": "其他汽车零部件", "chg_5d": 88.0}}

    async def run():
        resolution = await official.resolve_official_catalog(holder)
        await market_themes._verify_board_multi_day(holder, {"themes": [card]}, date(2026, 10, 12),
                                                  resolution=resolution)
        await official.attach_official_async(holder, [card], resolution=resolution)
        assert card["exact_catalog"]["catalog_version"] == card["official_interval"]["catalog_version"]

    asyncio.run(run())
    assert calls == ["881126.TI"] and card["official_interval"]["kind"] == "industry"
    assert card["official_interval"]["verified"] is True and card["official_interval"]["source_as_of"] == "2026-10-12"
    assert card["candidate_board"]["chg_5d"] == 88.0 and card["candidate_catalog_code"] is None


def test_pending_foreground_reference_reuses_the_original_owner_after_waiter_cancel():
    svc, values, calls = service()

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def held(kind):
            calls.append(kind)
            entered.set()
            await release.wait()
            return deepcopy(values[kind])

        svc.read_board_catalog = held
        holder = NS(theme_catalog=svc)
        pending = await official.resolve_official_catalog_view(holder, wait_seconds=0.001)
        assert entered.is_set() and pending["reason"] == "catalog_read_pending"
        assert official.mapping_for_name("汽车零部件", pending)["catalog_mapping_state"] == "unavailable"
        canceled = asyncio.create_task(official.current_catalog_reference(holder, ["汽车零部件"]))
        await asyncio.sleep(0)
        canceled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await canceled
        release.set()
        ref = await official.current_catalog_reference(holder, ["汽车零部件"])
        assert ref["items"][0]["exact_catalog"]["code"] == "881126.TI"
        assert sorted(calls) == ["concept", "industry"], "pending or cancellation never starts a second source read"

    asyncio.run(run())


def test_historical_market_read_keeps_original_position_and_exposes_only_current_reference(monkeypatch):
    from datetime import datetime
    import app.core.bjtime as clock
    from app.api.routes import market_themes
    from app.schemas.envelope import ThemeBoardPayload

    monkeypatch.setattr(clock, "beijing_now", lambda: datetime.fromisoformat("2026-10-27T16:00:00+08:00"))
    svc, _, calls = service()
    kline_calls = []

    async def bars(code):
        kline_calls.append(code)
        return [{"date": f"2026-10-{day:02d}", "close": 100 + day} for day in range(1, 28)]

    svc.fetch_board_bars = bars
    original = {"theme": "汽车零部件", "strength_score": 73,
                "persistence": {"position_risk": False, "grade": "原判据", "checks": []},
                "candidate_board": {"name": "其他汽车零部件", "chg_5d": 88.0}}

    async def cached(*args):
        return {"data": {"trade_date": "2026-10-26", "themes": [deepcopy(original)]}, "meta": {}}

    monkeypatch.setattr(market_themes, "_theme_board_cached", cached)
    holder = NS(app=NS(state=NS(theme_catalog=svc)))

    async def read(day):
        return await market_themes.themes(date_str=day, min_boards=0, min_count=1, sort="strength",
                                          limit=30, request=holder, hub=NS())

    async def run():
        historical = await read("2026-10-26")
        card = historical["data"]["themes"][0]
        assert card["theme"] == original["theme"] and card["strength_score"] == original["strength_score"]
        assert card["persistence"] == original["persistence"] and card["candidate_board"] == original["candidate_board"]
        assert card["catalog_mapping_state"] == "unavailable" and card["exact_catalog"] is None
        assert card["catalog_mapping_context"]["reason"] == "historical_identity_not_frozen"
        ref = historical["data"]["current_theme_catalog"]
        assert ref["scope"] == "current_directory_reference" and ref["items"][0]["exact_catalog"]["kind"] == "industry"
        assert kline_calls == [] and sorted(calls) == ["concept", "industry"]
        current = await read("2026-10-27")
        projected = ThemeBoardPayload.model_validate(current["data"]).model_dump()
        current_card = projected["themes"][0]
        assert current_card["exact_catalog"]["kind"] == current_card["official_interval"]["kind"] == "industry"
        assert current_card["exact_catalog"]["catalog_version"] == current_card["official_interval"]["catalog_version"]
        assert kline_calls == ["881126.TI"]

    asyncio.run(run())


def test_market_directory_and_bars_share_one_original_view_wait(monkeypatch):
    from datetime import datetime
    import app.core.bjtime as clock
    from app.api.routes import market_themes

    monkeypatch.setattr(clock, "beijing_now", lambda: datetime.fromisoformat("2026-10-27T16:00:00+08:00"))
    svc, values, calls = service()

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        bar_calls = []

        async def directory_read(kind):
            calls.append(kind)
            await asyncio.sleep(0.12)  # real event-loop time; do not freeze clocks
            return deepcopy(values[kind])

        async def bars(code):
            bar_calls.append(code)
            entered.set()
            await release.wait()
            return [{"date": f"2026-10-{day:02d}", "close": 100 + day} for day in range(1, 28)]

        async def cached(*args):
            return {"data": {"themes": [{"theme": "汽车零部件"}]}, "meta": {}}

        svc.read_board_catalog, svc.fetch_board_bars = directory_read, bars
        monkeypatch.setattr(market_themes, "_theme_board_cached", cached)
        holder = NS(app=NS(state=NS(theme_catalog=svc)))

        async def read():
            return await market_themes.themes(date_str="2026-10-27", min_boards=0, min_count=1,
                                              sort="strength", limit=30, request=holder, hub=NS())

        loop = asyncio.get_running_loop()
        began = loop.time()
        task = asyncio.create_task(read())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            # The 120ms directory read has spent part of the original 250ms.
            # This observer margin is not another production wait budget.
            done, _pending = await asyncio.wait({task}, timeout=0.18)
            elapsed = loop.time() - began
            assert done, f"directory + bars must share 250ms, observed {elapsed:.3f}s and still waiting"
            payload = task.result()
            assert payload["data"]["themes"][0]["official_interval"]["state"] == "pending"
            assert bar_calls == ["881126.TI"] and sorted(calls) == ["concept", "industry"]
        finally:
            release.set()
            await task
        # The original source owner completes once and is reused on a later view.
        again = await read()
        assert again["data"]["themes"][0]["official_interval"]["verified"] is True
        assert bar_calls == ["881126.TI"] and sorted(calls) == ["concept", "industry"]

    asyncio.run(run())
