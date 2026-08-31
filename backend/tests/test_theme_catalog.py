"""题材字典/官方成分测试（linkage-design §3 T1）。

网络全部 mock：测试绝不真打 fuyao 外网（CI 无外网会红）。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_engine, get_session_factory
from app.main import app
from app.services.theme_catalog_service import (
    ThemeCatalogService,
    parse_catalog_items,
    parse_member_items,
    reconcile,
)

GRAIN = "885995.TI"  # 粮食概念（官方实测代码）


@pytest.fixture(autouse=True)
def _create_tables():
    """服务单测不走 lifespan，需自建表（create_all 幂等）。"""
    from app.models.watchlist import Base

    Base.metadata.create_all(get_engine())
    yield


# ---------------------------------------------------------------- 纯函数


def test_parse_catalog_items_skips_incomplete():
    payload = {"data": {"item": [
        {"thscode": GRAIN, "name": "粮食概念"},
        {"thscode": "", "name": "缺代码"},
        {"name": "缺代码2"},
        {"thscode": "886042.TI", "name": "存储芯片"},
    ]}}
    items = parse_catalog_items(payload)
    assert {i["code"] for i in items} == {GRAIN, "886042.TI"}


def test_parse_member_items_filters_bad_ticker():
    payload = {"data": {"item": [
        {"thscode": "000019.SZ", "ticker": "000019", "name": "深粮控股"},
        {"thscode": "x.SZ", "ticker": "ABC123", "name": "非数字"},
        {"thscode": "1.SZ", "ticker": "1", "name": "过短"},
        {"ticker": "000505", "name": "京粮控股"},
    ]}}
    items = parse_member_items(payload)
    assert [i["symbol"] for i in items] == ["000019", "000505"]


def test_reconcile_detects_conflicts_and_unknown_themes():
    attributions = [
        ("600103", "青山纸业", ["粮食概念", "造纸"]),
        ("000019", "深粮控股", ["粮食概念"]),
    ]
    members = {"粮食概念": {"000019"}}
    names = {"粮食概念": GRAIN}
    r = reconcile(attributions, members, names)

    assert {"symbol": "600103", "name": "青山纸业", "theme": "粮食概念"} in r["attribution_conflicts"]
    assert r["unknown_themes"] == ["造纸"], "官方目录外的归因题材必须点名"
    assert r["checked_pairs"] == 2
    assert r["theme_names"]["粮食概念"] == GRAIN, "报告附 题材名→代码 便于前端跳转"


# ---------------------------------------------------------------- 同步（IO mock）


def _svc() -> ThemeCatalogService:
    return ThemeCatalogService(get_session_factory(), api_key="test-key")


def test_sync_catalog_upserts_idempotently(monkeypatch: pytest.MonkeyPatch):
    svc = _svc()

    async def fake_catalog():
        return [{"code": GRAIN, "name": "粮食概念"}, {"code": "886042.TI", "name": "存储芯片"}]

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    n1 = asyncio.run(svc.sync_catalog())
    n2 = asyncio.run(svc.sync_catalog())  # 幂等：不产生重复行
    assert n1 == n2 == 2
    assert svc.catalog_size() == 2


def test_sync_members_replaces_removed(monkeypatch: pytest.MonkeyPatch):
    svc = _svc()

    async def fake_members_v1(code):
        return [
            {"symbol": "000019", "name": "深粮控股"},
            {"symbol": "000505", "name": "京粮控股"},
        ]

    async def fake_members_v2(code):
        return [{"symbol": "000019", "name": "深粮控股"}]  # 官方移除了 000505

    monkeypatch.setattr(svc, "fetch_members", fake_members_v1)
    assert asyncio.run(svc.sync_members(GRAIN)) == 2

    monkeypatch.setattr(svc, "fetch_members", fake_members_v2)
    asyncio.run(svc.sync_members(GRAIN))
    symbols = {m.symbol for m in svc.get_members(GRAIN)}
    assert symbols == {"000019"}, "官方口径是当前成分：消失成员必须删除，不能残留"


def test_stale_codes_prioritizes_empty(monkeypatch: pytest.MonkeyPatch):
    svc = _svc()

    async def fake_catalog():
        return [{"code": GRAIN, "name": "粮食概念"}, {"code": "886042.TI", "name": "存储芯片"}]

    async def fake_members(code):
        return [{"symbol": "000019", "name": "深粮控股"}] if code == GRAIN else []

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    monkeypatch.setattr(svc, "fetch_members", fake_members)
    asyncio.run(svc.sync_catalog())
    asyncio.run(svc.sync_members(GRAIN))

    stale = svc.stale_codes(max_themes=10)
    assert "886042.TI" in stale, "从未同步成分的题材应排最前"
    assert stale[0] == "886042.TI"


# ---------------------------------------------------------------- API


def test_theme_catalog_api(monkeypatch: pytest.MonkeyPatch):
    with TestClient(app) as client:
        svc = _svc()

        async def fake_catalog():
            return [{"code": GRAIN, "name": "粮食概念"}, {"code": "886042.TI", "name": "存储芯片"}]

        async def fake_members(code):
            if code != GRAIN:
                return []
            return [{"symbol": "000019", "name": "深粮控股"}, {"symbol": "000505", "name": "京粮控股"}]

        monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
        monkeypatch.setattr(svc, "fetch_members", fake_members)
        client.app.state.theme_catalog = svc  # 替换掉 lifespan 建的真服务，杜绝外网

        asyncio.run(svc.sync_catalog())
        asyncio.run(svc.sync_members(GRAIN))

        r = client.get("/api/themes/catalog", params={"search": "粮食"})
        assert r.status_code == 200
        assert [i["code"] for i in r.json()["data"]["items"]] == [GRAIN]

        r = client.get(f"/api/themes/catalog/{GRAIN}/members")
        assert r.status_code == 200
        assert any(i["symbol"] == "000019" for i in r.json()["data"]["items"])

        # refresh=1 强制重拉（仍走 mock）
        r = client.get(f"/api/themes/catalog/{GRAIN}/members", params={"refresh": "1"})
        assert r.status_code == 200

        # 非法代码被拒
        assert client.get("/api/themes/catalog/../secret/members").status_code in (400, 404)


# ---------------------------------------------------------------- T2-a：个股反查


def test_apply_overrides_excludes_and_includes():
    from app.models.theme_catalog import ThemeOverride
    from app.services.theme_catalog_service import apply_overrides

    members = [
        {"theme_code": GRAIN, "theme_name": "粮食概念", "source": "ths_official"},
        {"theme_code": "886042.TI", "theme_name": "存储芯片", "source": "ths_official"},
    ]
    overrides = [
        ThemeOverride(theme_code="886042.TI", symbol="000019", action="exclude", reason="口径争议"),
        ThemeOverride(theme_code="881001.TI", symbol="000019", action="include", reason="人工确认"),
    ]
    out = apply_overrides(members, overrides, {"881001.TI": "测试题材"})
    codes = {m["theme_code"] for m in out}
    assert "886042.TI" not in codes, "exclude 必须剔除官方归属"
    added = next(m for m in out if m["theme_code"] == "881001.TI")
    assert added["theme_name"] == "测试题材" and added["source"] == "manual"

    # 目录里查不到 include 的题材：以代码兜底命名，不静默丢弃人工修正
    out2 = apply_overrides(members, [overrides[1]], {})
    assert out2[-1]["theme_name"] == "881001.TI"


def test_get_official_for_symbol_applies_overrides(monkeypatch: pytest.MonkeyPatch):
    svc = _svc()

    async def fake_catalog():
        return [{"code": GRAIN, "name": "粮食概念"}]

    async def fake_members(code):
        return [{"symbol": "000019", "name": "深粮控股"}] if code == GRAIN else []

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    monkeypatch.setattr(svc, "fetch_members", fake_members)
    asyncio.run(svc.sync_catalog())
    asyncio.run(svc.sync_members(GRAIN))

    base = svc.get_official_for_symbol("000019", apply_manual=False)
    assert base == [{"theme_code": GRAIN, "theme_name": "粮食概念", "source": "ths_official"}]

    with svc._sf() as db:
        from app.models.theme_catalog import ThemeOverride

        db.add(ThemeOverride(theme_code=GRAIN, symbol="000019", action="exclude", reason="测试"))
        db.commit()

    assert svc.get_official_for_symbol("000019") == [], "活跃 exclude 必须生效"


def test_stock_themes_api(monkeypatch: pytest.MonkeyPatch):
    with TestClient(app) as client:
        svc = _svc()

        async def fake_catalog():
            return [{"code": GRAIN, "name": "粮食概念"}]

        async def fake_members(code):
            if code != GRAIN:
                return []
            return [{"symbol": "000019", "name": "深粮控股"}, {"symbol": "000505", "name": "京粮控股"}]

        monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
        monkeypatch.setattr(svc, "fetch_members", fake_members)
        client.app.state.theme_catalog = svc

        asyncio.run(svc.sync_catalog())
        asyncio.run(svc.sync_members(GRAIN))

        # 用 000505：上一条测试写入的 000019 override 会泄漏到共享内存库
        r = client.get("/api/themes/stock/000505")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["official"] == [{"theme_code": GRAIN, "theme_name": "粮食概念", "source": "ths_official"}]
        # 测试环境 provider 链是 mock（无 ThsFuyaoProvider）→ 归因为空但不报错
        assert data["attribution"] == []

        # 非法代码
        assert client.get("/api/themes/stock/abc").status_code == 400
