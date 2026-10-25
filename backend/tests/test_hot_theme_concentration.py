"""Heat samples are not an official theme ranking or a strategy qualification."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import threading
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.api.routes import theme_catalog as route
from app.core.config import settings
from app.core.db import utcnow
from app.services.theme_catalog_service import aggregate_hot_themes


def stock(symbol, rank=1, heat=10):
    return {"symbol": symbol, "rank": rank, "heat": heat, "name": symbol, "rank_change": 0,
            "ts": "2026-10-23T07:00:00+00:00"}


def link(code, name, source="ths_official"):
    return {"theme_code": code, "theme_name": name, "source": source}


def catalog(name, current=True):
    return {"name": name, "source": "ths_official", "members_current": current,
            "member_as_of": "2026-10-23T06:00:00+00:00"}


def test_concentration_does_not_reward_broad_tags_or_merge_same_names():
    stocks = [stock("600001", 1, 900), stock("600002", 2, 800), stock("600003", 3, 1)]
    official = {"600001": [link("A", "宽标签"), link("C", "同名")],
                "600002": [link("A", "宽标签")], "600003": [link("B", "同名")]}
    members = {"A": [f"{i:06d}" for i in range(600001, 600101)],
               "B": ["600003", "600004"], "C": ["600001"]}
    before = deepcopy((stocks, official, members))
    data = aggregate_hot_themes(stocks, official, catalog_by_code={
        "A": catalog("宽标签"), "B": catalog("同名"), "C": catalog("同名")},
        members_by_code=members, catalog_state="ready", catalog_revision=3)
    assert [(row["theme_code"], row["concentration_pct"]) for row in data["themes"]] == [
        ("C", 100.0), ("B", 50.0), ("A", 2.0)]
    assert data["themes"][-1]["hot_count"] == 2
    assert data["themes"][0]["hot_symbols"] == ["600001"]
    assert data["themes"][1]["hot_symbols"] == ["600003"]
    assert data["ranking_basis"] == "hot_stock_concentration" and data["catalog_revision"] == 3
    assert "并非官方题材榜" in data["scope"]
    assert (stocks, official, members) == before


def test_formal_identity_and_membership_source_are_both_required():
    data = aggregate_hot_themes([stock("600001")], {"600001": [
        link("A", "正式"), link("A", "正式"), link("B", "手工", "manual"),
        link("C", "错名"), link("D", "非官方目录"), link("E", "目录不存在"),
    ]}, catalog_by_code={"A": catalog("正式"), "B": catalog("手工"), "C": catalog("真名"),
                         "D": {**catalog("非官方目录"), "source": "manual"}},
        members_by_code={"A": ["600001"]}, catalog_state="ready")
    assert [row["theme_code"] for row in data["themes"]] == ["A"]
    assert data["themes"][0]["hot_count"] == 1
    assert data["stocks"][0]["themes"] == ["正式"]


def test_missing_heat_and_member_coverage_never_become_zero():
    data = aggregate_hot_themes([stock("600001", 1, None), stock("600002", 2, 0), stock("600003", 3, 0)],
        {"600001": [link("A", "缺热度")], "600002": [link("A", "缺热度")], "600003": [link("B", "真零")]},
        catalog_by_code={"A": catalog("缺热度"), "B": catalog("真零")},
        members_by_code={"B": ["600003"]}, catalog_state="ready")
    rows = {row["theme_code"]: row for row in data["themes"]}
    assert rows["A"]["heat"] is None and rows["A"]["heat_complete"] is False
    assert rows["A"]["member_count"] is None and rows["A"]["concentration_pct"] is None
    assert rows["A"]["member_as_of"] == "2026-10-23T06:00:00+00:00"
    assert rows["B"]["heat"] == 0 and rows["B"]["heat_complete"] is True
    assert data["themes"][0]["theme_code"] == "B"


@pytest.mark.parametrize("state,current,members", [
    ("stale", True, ["600001"]), ("unknown", True, ["600001"]),
    ("ready", False, ["600001"]), ("ready", True, []), ("ready", True, ["600002"]),
])
def test_stale_missing_or_conflicting_denominator_remains_unknown(state, current, members):
    data = aggregate_hot_themes([stock("600001")], {"600001": [link("A", "已存题材")]},
        catalog_by_code={"A": catalog("已存题材", current)}, members_by_code={"A": members}, catalog_state=state)
    row = data["themes"][0]
    assert row["member_count"] is None and row["concentration_pct"] is None
    assert row["hot_symbols"] == ["600001"] and row["source"] == "ths_official"
    assert "未核" in row["basis"]


class ThsFuyaoProvider:
    def __init__(self):
        self.calls = 0
        self.gate = None

    async def get_hot_stock_list(self, period):
        assert period == "day"
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return [stock("600001"), stock("600002", 2, None)]


class LocalCatalog:
    def __init__(self):
        now = utcnow()
        self.revision = 7
        self.calls = []
        self.row = SimpleNamespace(code="885001.TI", name="已存题材", source="ths_official",
                                   catalog_synced_at=now, synced_at=now)
        self.bulk_error = False
        self.count_error = False
        self.drift = False

    def get_catalog(self, limit):
        assert limit == 1000
        self.calls.append(("catalog", threading.get_ident()))
        return [self.row]

    def official_for_symbols_bulk(self, symbols, *, apply_manual):
        assert apply_manual is False
        assert symbols == ["600001", "600002"]
        self.calls.append(("membership", threading.get_ident()))
        if self.bulk_error:
            raise RuntimeError("isolated membership failure")
        if self.drift:
            self.revision += 1
        return {"600001": [link("885001.TI", "已存题材")], "600002": []}

    def member_symbols_bulk(self, codes):
        assert codes in (["885001.TI"], [])
        self.calls.append(("denominator", threading.get_ident()))
        if self.count_error:
            raise RuntimeError("isolated denominator failure")
        return {"885001.TI": ["600001", "600002"]}


def context():
    provider, svc = ThsFuyaoProvider(), LocalCatalog()
    state = SimpleNamespace(hub=SimpleNamespace(provider=provider), theme_catalog=svc)
    return SimpleNamespace(app=SimpleNamespace(state=state)), provider, svc


def test_route_cold_singleflight_worker_projection_and_revision_cache():
    async def run():
        request, provider, svc = context()
        provider.gate = asyncio.Event()
        event_loop_thread = threading.get_ident()
        first = asyncio.create_task(route.themes_hot(request, 30, svc))
        second = asyncio.create_task(route.themes_hot(request, 30, svc))
        await asyncio.sleep(0)
        provider.gate.set()
        a, b = await asyncio.gather(first, second)
        assert a == b and provider.calls == 1
        assert [name for name, _thread in svc.calls] == ["catalog", "membership", "denominator"]
        assert all(thread != event_loop_thread for _name, thread in svc.calls)
        a["data"]["themes"].clear()
        cached = await route.themes_hot(request, 30, svc)
        assert len(cached["data"]["themes"]) == 1 and provider.calls == 1
        assert cached["data"]["catalog_revision"] == 7
        svc.revision = 8
        current = await route.themes_hot(request, 30, svc)
        assert current["data"]["catalog_revision"] == 8 and provider.calls == 2
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["bulk_error", "count_error"])
def test_local_read_failures_keep_original_stocks_with_explicit_unknown(failure):
    request, _provider, svc = context()
    setattr(svc, failure, True)
    data = asyncio.run(route.themes_hot(request, 30, svc))["data"]
    assert len(data["stocks"]) == 2
    if failure == "bulk_error":
        assert data["themes"] == [] and data["membership_state"] == "unavailable"
        assert "不能据空分类" in data["scope"]
    else:
        assert data["themes"][0]["member_count"] is None
        assert data["themes"][0]["concentration_pct"] is None


@pytest.mark.parametrize("future", [False, True])
def test_member_snapshot_ttl_and_future_time_are_not_current(future):
    request, _provider, svc = context()
    svc.row.synced_at = utcnow() + timedelta(hours=1) if future else (
        utcnow() - timedelta(hours=settings.theme_members_ttl_hours + 1))
    row = asyncio.run(route.themes_hot(request, 30, svc))["data"]["themes"][0]
    assert row["member_count"] is None and row["concentration_pct"] is None
    assert (row["member_as_of"] is None) is future


def test_catalog_age_is_disclosed_without_relabeling_membership_as_today():
    request, _provider, svc = context()
    svc.row.catalog_synced_at = utcnow() - timedelta(hours=25)
    data = asyncio.run(route.themes_hot(request, 30, svc))["data"]
    assert data["catalog_state"] == "stale" and "目录未在有效期内" in data["scope"]
    assert data["themes"][0]["member_count"] is None
    assert data["themes"][0]["theme_code"] == "885001.TI"


def test_mid_read_revision_drift_fails_instead_of_caching_mixed_directory():
    request, provider, svc = context()
    svc.drift = True
    with pytest.raises(HTTPException) as error:
        asyncio.run(route.themes_hot(request, 30, svc))
    assert error.value.status_code == 503
    svc.drift = False
    result = asyncio.run(route.themes_hot(request, 30, svc))
    assert result["data"]["catalog_revision"] == 8 and provider.calls == 2
