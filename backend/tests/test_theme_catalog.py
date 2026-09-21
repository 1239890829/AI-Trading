"""题材字典/官方成分测试（architecture-design §1 T1）。

网络全部 mock：测试绝不真打 fuyao 外网（CI 无外网会红）。
"""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_engine, get_session_factory
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
    # 共享内存库里其他测试会写入别的目录条目，catalog_size 不能锁死；
    # 幂等的本质是「同一 code 只有一行」——按名称搜索验证无重复
    rows = svc.get_catalog(search="粮食概念")
    assert len(rows) == 1 and rows[0].code == GRAIN


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

    # ⚠️ max_themes 必须给足，**不能用小值**：stale_codes 按 synced_at 升序返回并截断，
    # 共享内存库里其它测试（含全端点冒烟 test_endpoint_smoke，它会打所有 GET 端点、
    # 连带写入题材目录缓存）的条目可能排在 886042.TI 之前 ⇒ 截断到 10 时它会被挤出，
    # 于是这条断言实际验的是"排名"而不是"成员关系"，与下面不锁顺序的意图自相矛盾。
    # 2026-09-11 实测：单跑本文件通过，接在冒烟测试之后跑就失败，根因即此。
    stale = svc.stale_codes(max_themes=10_000)
    assert "886042.TI" in stale, "从未同步成分的题材必须进入 stale 队列"
    assert GRAIN not in stale, "已同步成分且未过 TTL 的题材不得进入 stale 队列"
    # 注：不锁 stale[0]——共享内存库里其他测试的目录条目可能排在更前（按 synced_at 升序），
    # 跨测试只断言成员关系，避免顺序脆弱


# ---------------------------------------------------------------- API


def test_theme_catalog_api(client, monkeypatch: pytest.MonkeyPatch):
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

    # 用 TestClient 自身的事件循环执行（portal.call），而不是 asyncio.run——
    # sqlite 文件库是 SingletonThreadPool（同线程同一连接），asyncio.run 的
    # 第二个循环与 lifespan 后台轮询任务共用连接，同步 commit 时会撞上
    # 后台任务的未消费游标（"SQL statements in progress"，CI 慢机偶发）。
    # portal.call 让两者在同一循环内串行让出，连接不再交叠。
    client.portal.call(svc.sync_catalog)
    client.portal.call(svc.sync_members, GRAIN)

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


def test_official_for_symbols_bulk_matches_single_read(monkeypatch: pytest.MonkeyPatch):
    """**批次 3 / P-3② 的 A/B 等价守卫**：批量反查逐条 == 逐只反查。

    为什么必须直接对照、而不是各测各的（2026-09-12）：批量版把
    `N 只 ×（成员 + 人工纠错）` 压成 2 次 `in_` 查询，**优化本身就可能改口径**——
    漏排序、漏叠加 override、把「未命中」从空列表变成缺键，三种都会让调用方
    `themes_by_symbol.get(sym) or []` 悄悄取到不同结果，而单侧测试照样全绿。
    所以此处逐只跑一遍、与批量结果**整体比对**，并覆盖两种 override 状态：

    · 活跃 exclude：批量与逐只都必须剔除该官方归属；
    · 已过期 include：两边都不该出现（过期 = 不生效，判据共用
      `override_still_active`，而不是"两处各写一遍同样的比较"）；
    · 未命中的 symbol：返回**空列表而非缺键**（调用方不必再 `or []`）。

    注入验证（2026-09-12，逐条真实破坏后复原）：把批量版改成跳过 override 叠加 /
    把未命中改成缺键 —— 均由 `bulk == single` 精确变红；而把 `override_still_active`
    改成恒 True（**两路共用的判据本身失效**）时 `bulk == single` **照旧成立** ——
    这时候只有下面那条显式断言（`bulk["600303"] == []`）会红。⇒ A/B 对照只能钉
    「两路一致」，钉不住「判据正确」；两者必须同时存在。

    用独立题材码 / 个股码：本文件与其它用例共用内存库，复用既有码会被别的
    用例写入的 override 污染（同 `test_stock_themes_api` 的注释）。
    """
    from datetime import timedelta

    from app.core.db import utcnow
    from app.models.theme_catalog import ThemeOverride

    svc = _svc()
    code_a, code_b = "889900.TI", "889901.TI"

    async def fake_catalog():
        return [{"code": code_a, "name": "测试题材甲"}, {"code": code_b, "name": "测试题材乙"}]

    async def fake_members(code):
        if code == code_a:
            return [{"symbol": "600301", "name": "甲一"}, {"symbol": "600302", "name": "甲二"}]
        if code == code_b:
            return [{"symbol": "600301", "name": "甲一"}]
        return []

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    monkeypatch.setattr(svc, "fetch_members", fake_members)
    asyncio.run(svc.sync_catalog())
    asyncio.run(svc.sync_members(code_a))
    asyncio.run(svc.sync_members(code_b))

    with svc._sf() as db:
        db.add(ThemeOverride(theme_code=code_b, symbol="600301", action="exclude", reason="测试：活跃剔除"))
        db.add(ThemeOverride(theme_code=code_a, symbol="600303", action="include", reason="测试：已过期",
                             expires_at=utcnow() - timedelta(days=1)))
        db.commit()

    symbols = ["600301", "600302", "600303", "600304"]  # 600304 不在任何题材里
    bulk = svc.official_for_symbols_bulk(symbols)
    single = {s: svc.get_official_for_symbol(s) for s in symbols}
    assert bulk == single, "批量反查与逐只反查必须逐条等价（含排序与 override 叠加）"

    assert [m["theme_code"] for m in bulk["600301"]] == [code_a], "活跃 exclude 必须生效"
    assert bulk["600302"] == [{"theme_code": code_a, "theme_name": "测试题材甲",
                               "source": "ths_official"}]
    assert bulk["600303"] == [], "已过期 override 不生效（include 不该被保留）"
    assert "600304" in bulk and bulk["600304"] == [], "未命中返回空列表，不是缺键"

    # 空入参：不查库、返回空字典（调用方无需特判）
    assert svc.official_for_symbols_bulk([]) == {}


def test_stock_themes_api(client, monkeypatch: pytest.MonkeyPatch):
    svc = _svc()

    async def fake_catalog():
        return [{"code": GRAIN, "name": "粮食概念"}]

    async def fake_members(code):
        if code != GRAIN:
            return []
        return [{"symbol": "000019", "name": "深粮控股"}, {"symbol": "000505", "name": "京粮控股"}]

    async def unavailable_board_bars(_code, calendar_days=35):
        raise RuntimeError("test board kline unavailable")

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    monkeypatch.setattr(svc, "fetch_members", fake_members)
    monkeypatch.setattr(svc, "fetch_board_bars", unavailable_board_bars)
    client.app.state.theme_catalog = svc

    client.portal.call(svc.sync_catalog)
    client.portal.call(svc.sync_members, GRAIN)

    # 用 000505：上一条测试写入的 000019 override 会泄漏到共享内存库
    r = client.get("/api/themes/stock/000505")
    assert r.status_code == 200
    data = r.json()["data"]
    # theme_chg_1d / theme_align_1d：增强字段（测试 key 无法拉官方板块K线/无快照 → None）
    assert data["official"] == [
        {"theme_code": GRAIN, "theme_name": "粮食概念", "source": "ths_official",
         "theme_chg_1d": None, "theme_align_1d": None}
    ]
    # 测试环境 provider 链是 mock（无 ThsFuyaoProvider）→ 归因为空但不报错
    assert data["attribution"] == []

    # 非法代码
    assert client.get("/api/themes/stock/abc").status_code == 400


# ---------------------------------------------------------------- T3/B3：官方 K 线交叉验证


def test_parse_board_bars_sorts_and_skips_incomplete():
    from app.services.theme_catalog_service import parse_board_bars

    payload = {"data": {"item": [
        {"date_ms": 1787846400000, "close_price": 2632.18},
        {"date_ms": 1787673600000, "close_price": 2554.496},
        {"date_ms": 1787760000000, "close_price": None},   # 缺收盘 → 跳过
        {"close_price": 100.0},                              # 缺时间 → 跳过
    ]}}
    bars = parse_board_bars(payload)
    assert [b["close"] for b in bars] == [2554.496, 2632.18], "按日期升序"
    assert bars[0]["date"] < bars[1]["date"]


def test_official_multi_day_changes():
    from app.services.theme_catalog_service import official_multi_day_changes

    # 12 个交易日：close 100 → 111
    bars = [{"date": f"2026-08-{d:02d}", "close": 100 + i} for i, d in enumerate(range(10, 22))]
    r = official_multi_day_changes(bars)
    assert r["chg_3d"] == round((111 / 108 - 1) * 100, 2)
    assert r["chg_5d"] == round((111 / 106 - 1) * 100, 2)
    assert r["chg_10d"] == round((111 / 101 - 1) * 100, 2)
    # 10 根 K 线：10 日涨幅需 11 根以上，不足时 None（不硬凑）
    r10 = official_multi_day_changes(bars[:10])
    assert r10["chg_10d"] is None
    assert r10["chg_3d"] == round((109 / 106 - 1) * 100, 2)


def test_verify_board_multi_day_replaces_and_flags(monkeypatch: pytest.MonkeyPatch):
    """官方值覆盖推断值、保留 *_inferred、verified 标志与 caveats 同步更新。"""
    svc = _svc()

    async def fake_catalog():
        return [{"code": GRAIN, "name": "粮食概念"}]

    # 12 根：100..111 → chg_3d = 111/108-1
    async def fake_bars(code):
        return [{"date": f"2026-08-{d:02d}", "close": 100 + i} for i, d in enumerate(range(10, 22))]

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    monkeypatch.setattr(svc, "fetch_board_bars", fake_bars)

    from app.api.routes.market_themes import _verify_board_multi_day

    payload = {
        "themes": [
            {"theme": "粮食概念", "board": {"name": "粮食概念", "chg_3d": 99.0, "chg_5d": 88.0, "chg_10d": 77.0}},
            {"theme": "目录外题材", "board": {"name": "目录外题材", "chg_3d": 1.0}},
        ],
        "caveats": ["板块 3/5/10 日涨跌幅为东财字段序推断，未经 K 线交叉验证（board_multi_day_verified=false）"],
    }
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(theme_catalog=svc)))
    asyncio.run(_verify_board_multi_day(request, payload))

    board = payload["themes"][0]["board"]
    assert board["multi_day_verified"] is True
    assert board["chg_3d"] == round((111 / 108 - 1) * 100, 2), "官方值覆盖推断值"
    assert board["chg_3d_inferred"] == 99.0, "推断值保留供审计"
    assert board["multi_day_source"] == "ths_official_kline"

    other = payload["themes"][1]["board"]
    assert "multi_day_verified" not in other, "目录外题材保留推断值、不打验证标"
    assert any("已用同花顺官方板块 K 线交叉验证" in c for c in payload["caveats"])
    assert any("board_multi_day_verified=false" in c for c in payload["caveats"]) is False

    # 缓存命中短路：再次调用不再重复拉取（fetch 计数不变）
    calls = {"n": 0}
    orig = svc.fetch_board_bars

    async def counting(code):
        calls["n"] += 1
        return await orig(code)

    monkeypatch.setattr(svc, "fetch_board_bars", counting)
    asyncio.run(_verify_board_multi_day(request, payload))
    assert calls["n"] == 0, "已验证的 payload 直接跳过"


def test_verify_board_multi_day_upgrades_persistence_position(monkeypatch: pytest.MonkeyPatch):
    """P1-6：官方 5 日涨幅到位后，持续性评估的位置判定从 active_days 代理升级。

    板块 K 线 8 根 100→121（chg_5d≈+14.2% > 10% 阈值）：代理口径判低位
    （active_days=2），升级后 position_risk 必须转高——慢牛高位只有真实
    区间涨幅能抓住。chg_10d 因 K 线不足 11 根为 None → 不打 verified 标，
    但 chg_5d 可得就足够升级位置判定。
    """
    from app.services.dragon_service import news_persistence

    svc = _svc()

    async def fake_catalog():
        return [{"code": GRAIN, "name": "粮食概念"}]

    async def fake_bars(code):
        return [{"date": f"2026-08-{d:02d}", "close": 100 + 3 * i} for i, d in enumerate(range(10, 18))]

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    asyncio.run(svc.sync_catalog())  # 自同步目录：不依赖其他测试留下的库状态（内存库顺序耦合是隐患）
    monkeypatch.setattr(svc, "fetch_board_bars", fake_bars)

    from app.api.routes.market_themes import _verify_board_multi_day

    persistence = news_persistence(core_type="业绩兑现", limit_up_count=8,
                                   has_second_board=True, active_days=2, main_net_inflow=5e8)
    assert persistence["position_risk"] is False
    payload = {
        "themes": [{"theme": "粮食概念", "persistence": persistence,
                    "board": {"name": "粮食概念", "chg_3d": 1.0, "chg_5d": 1.0}}],
        "caveats": ["板块 3/5/10 日涨跌幅为东财字段序推断，未经 K 线交叉验证（board_multi_day_verified=false）"],
    }
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(theme_catalog=svc)))
    asyncio.run(_verify_board_multi_day(request, payload))

    board = payload["themes"][0]["board"]
    assert board["chg_5d"] == round((121 / 106 - 1) * 100, 2), "官方值覆盖"
    assert board.get("multi_day_verified") is not True, "K 线不足 11 根，10 日涨幅缺失不打验证标"
    card = payload["themes"][0]
    assert card["persistence"]["position_risk"] is True, "官方 5 日涨幅超阈值 → 位置转高"
    assert "官方 K 线" in next(c for c in card["persistence"]["checks"] if c["axis"] == "risk")["value"]
    assert card["persistence"]["grade"] == "主线·位置偏高"
    assert any("位置判定同步升级" in c for c in payload["caveats"])


# ------------------------------------------------- 回归：reconciliation 默认日期（retro §三 #8）


def test_reconciliation_without_date_resolves_trade_date(monkeypatch: pytest.MonkeyPatch):
    """不带 ?date= 时路由必须先解析最近交易日，不得把 None 直传 ths 池端点。

    真实契约：ths.get_limit_up_pool 要求具体 date（date_ms(None) 崩
    'NoneType' object has no attribute 'year'）。桩复刻该契约（None 即抛），
    路由不解析日期时本测试必红。
    """
    seen = {}

    class ThsFuyaoProvider:  # 类型名匹配 _pick_provider
        name = "ths"

        async def get_limit_up_pool(self, trade_date):
            if trade_date is None:
                raise TypeError("'NoneType' object has no attribute 'year'")
            seen["date"] = trade_date
            return [SimpleNamespace(symbol="600103", name="青山纸业", reason="热股测试题材+造纸")]

    class _FakeHub:
        name = "fake"
        provider = ThsFuyaoProvider()

    svc = _svc()

    async def fake_catalog():
        return [{"code": "889901.TI", "name": "热股测试题材"}]

    async def fake_members(code):
        return [{"symbol": "000019", "name": "深粮控股"}]

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    monkeypatch.setattr(svc, "fetch_members", fake_members)
    asyncio.run(svc.sync_catalog())
    asyncio.run(svc.sync_members("889901.TI"))

    from fastapi import FastAPI

    from app.api.routes import theme_catalog as route

    a = FastAPI()
    a.include_router(route.router, prefix="/api")
    a.state.hub = _FakeHub()
    a.state.theme_catalog = svc
    with TestClient(a) as client:
        r = client.get("/api/themes/reconciliation")

    assert r.status_code == 200
    assert isinstance(seen["date"], date), "池拉取收到的是解析后的交易日"
    assert r.json()["data"]["pool_size"] == 1


def test_reconciliation_missing_ths_fails_fast_without_side_effects(monkeypatch):
    """缺 ths 硬依赖 → **可控降级**（503 + 原因），且不得先做写明会带副作用的动作。

    `sync_catalog()` 会真打 fuyao 目录接口并写库；若把依赖检查放在它之后，
    「注定 503 的请求」仍会制造一次网络 + 库写入——而 `test_endpoint_smoke`
    按 openapi 遍历会打到本端点，那笔写就会跨用例污染共享内存库
    （该文件头部已登记过同类副作用：把题材挤出 max_themes=10 的截断）。
    回退即红：`synced["n"]` 会是 1（同步先于依赖检查发生）。
    """
    svc = _svc()
    synced = {"n": 0}

    async def fake_catalog():
        synced["n"] += 1
        return [{"code": GRAIN, "name": "粮食概念"}]

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    # 强制进入「目录为空 → 懒同步」分支（共享内存库里通常非空，否则本用例没有区分力）
    monkeypatch.setattr(ThemeCatalogService, "catalog_size", lambda self: 0)

    class _NoThsHub:
        name = "mock"
        provider = SimpleNamespace(name="mock")  # 类型名非 ThsFuyaoProvider ⇒ 取不到源

    from fastapi import FastAPI

    from app.api.routes import theme_catalog as route

    a = FastAPI()
    a.include_router(route.router, prefix="/api")
    a.state.hub = _NoThsHub()
    a.state.theme_catalog = svc
    with TestClient(a) as client:
        r = client.get("/api/themes/reconciliation")

    assert r.status_code == 503, "缺依赖是可控降级（503），不是 500"
    assert "同花顺源不可用" in r.json()["detail"], "降级必须给出可读原因"
    assert synced["n"] == 0, "注定 503 的请求不得先做目录同步（真打外网 + 写库）"


# ------------------------------------------------- B1 热股榜：题材人气聚合


def test_ths_hot_stock_list_parses_real_payload(monkeypatch: pytest.MonkeyPatch):
    """provider 归一化（fixture 取自 2026-08-31 实抓）：heat 是字符串数字，rank_change 整数。"""
    from app.data_providers.ths import ThsFuyaoProvider

    real = {
        "timestamp": 1788147857073,
        "item": [
            {"thscode": "000560.SZ", "ticker": "000560", "name": "我爱我家",
             "rank": 1, "heat": "6002184", "rank_change": 0, "rank_trend": "flat"},
            {"thscode": "600722.SH", "ticker": "600722", "name": "金牛化工",
             "rank": 2, "heat": "4487024", "rank_change": 1, "rank_trend": "up"},
            {"thscode": "x.SH", "ticker": "ABC", "name": "坏代码",
             "rank": 3, "heat": "1", "rank_change": 0, "rank_trend": "flat"},
        ],
    }

    async def fake_get(self, path, params=None):
        return real

    monkeypatch.setattr(ThsFuyaoProvider, "_get", fake_get)
    rows = asyncio.run(ThsFuyaoProvider(api_key="k").get_hot_stock_list("day"))

    assert [r["symbol"] for r in rows] == ["000560", "600722"], "坏代码剔除"
    assert rows[0]["heat"] == 6002184.0, "heat 字符串 → 数值（真实接口是字符串）"
    assert rows[1]["rank_change"] == 1
    assert rows[0]["ts"] is not None and rows[0]["source"] == "ths"


def test_ths_hot_stock_list_empty_raises(monkeypatch: pytest.MonkeyPatch):
    from app.data_providers.eastmoney import ProviderError
    from app.data_providers.ths import ThsFuyaoProvider

    async def fake_get(self, path, params=None):
        return {"timestamp": None, "item": []}

    monkeypatch.setattr(ThsFuyaoProvider, "_get", fake_get)
    with pytest.raises(ProviderError):
        asyncio.run(ThsFuyaoProvider(api_key="k").get_hot_stock_list("day"))


def test_aggregate_hot_themes_sums_and_picks_best():
    from app.services.theme_catalog_service import aggregate_hot_themes

    stocks = [
        {"rank": 1, "symbol": "000560", "name": "我爱我家", "heat": 100.0, "rank_change": 0, "ts": "t1"},
        {"rank": 5, "symbol": "000019", "name": "深粮控股", "heat": 50.0, "rank_change": 3, "ts": "t1"},
        {"rank": 7, "symbol": "300001", "name": "无归属股", "heat": 30.0, "rank_change": -2, "ts": "t1"},
    ]
    official = {
        "000560": [{"theme_code": "A", "theme_name": "物业管理", "source": "ths_official"}],
        "000019": [{"theme_code": "B", "theme_name": "粮食概念", "source": "ths_official"}],
        # 300001 无官方归属
    }
    r = aggregate_hot_themes(stocks, official)

    assert r["ts"] == "t1"
    assert r["stocks"][2]["themes"] == [], "无归属热股保留在 stocks，themes 为空"

    assert len(r["themes"]) == 2
    assert r["themes"][0]["theme"] == "物业管理" and r["themes"][0]["heat"] == 100.0, "按人气倒序"
    assert r["themes"][0]["hot_count"] == 1
    assert r["themes"][0]["best"]["symbol"] == "000560"
    assert "第 1 名" in r["themes"][0]["basis"]


def test_aggregate_hot_themes_best_is_highest_rank_member():
    from app.services.theme_catalog_service import aggregate_hot_themes

    stocks = [
        {"rank": 2, "symbol": "000560", "name": "我爱我家", "heat": 80.0, "rank_change": -1, "ts": None},
        {"rank": 9, "symbol": "000505", "name": "京粮控股", "heat": 60.0, "rank_change": 4, "ts": None},
    ]
    same = [{"theme_code": "B", "theme_name": "粮食概念", "source": "ths_official"}]
    r = aggregate_hot_themes(stocks, {"000560": same, "000505": same})

    t = r["themes"][0]
    assert t["heat"] == 140.0 and t["hot_count"] == 2, "同题材 heat 合计"
    assert t["best"]["rank"] == 2 and t["best"]["symbol"] == "000560", "best 取榜内排名最高成员"
    assert t["best"]["rank_change"] == -1, "rank_change 沿用 best 成员，不造题材级指标"
    assert "2 只官方成分热股人气合计" in t["basis"]


def test_themes_hot_route(monkeypatch: pytest.MonkeyPatch):
    """路由：ths 榜 × 官方成分 → 200；60s 缓存命中时 provider 只打一次。"""
    calls = {"n": 0}

    class ThsFuyaoProvider:
        name = "ths"

        async def get_hot_stock_list(self, period="day"):
            calls["n"] += 1
            return [
                {"rank": 1, "symbol": "000019", "name": "深粮控股", "heat": 100.0,
                 "rank_change": 2, "ts": "2026-08-31T03:00:00+00:00", "source": "ths"},
                {"rank": 4, "symbol": "000505", "name": "京粮控股", "heat": 40.0,
                 "rank_change": 0, "ts": "2026-08-31T03:00:00+00:00", "source": "ths"},
            ]

    class _FakeHub:
        name = "fake"
        provider = ThsFuyaoProvider()

    svc = _svc()

    async def fake_catalog():
        return [{"code": "889902.TI", "name": "热股路由题材"}]

    async def fake_members(code):
        return [{"symbol": "000019", "name": "深粮控股"}]

    monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
    monkeypatch.setattr(svc, "fetch_members", fake_members)
    asyncio.run(svc.sync_catalog())
    asyncio.run(svc.sync_members("889902.TI"))

    from fastapi import FastAPI

    from app.api.routes import theme_catalog as route

    a = FastAPI()
    a.include_router(route.router, prefix="/api")
    a.state.hub = _FakeHub()
    a.state.theme_catalog = svc
    with TestClient(a) as client:
        r1 = client.get("/api/themes/hot")
        client.get("/api/themes/hot")  # 命中缓存

    assert r1.status_code == 200
    data = r1.json()["data"]
    theme_entry = next(t for t in data["themes"] if t["theme"] == "热股路由题材")
    assert theme_entry["hot_count"] == 1 and theme_entry["heat"] == 100.0
    assert theme_entry["best"]["symbol"] == "000019"
    # 共享内存库：000019 可能被其他用例加进别的题材，断言锁成员关系不锁全量（账本教训）
    assert "热股路由题材" in data["stocks"][0]["themes"]
    assert "热股路由题材" not in data["stocks"][1]["themes"], "无官方归属的京粮控股不入题材聚合"
    assert calls["n"] == 1, "第二次请求命中 60s 缓存，provider 不重打"
    assert client.get("/api/themes/hot").status_code == 200


# ---- 方向联动度（2026-09-01 用户反馈 #2：排序依据只用方向不依赖涨跌幅数值）----


def test_direction_alignment_market_up():
    from app.services.theme_catalog_service import direction_alignment

    members = ["a", "b", "c", "d", "e", "f"]
    chg = {s: 1.0 for s in members[:4]}  # 4 只上涨
    chg.update({s: -1.0 for s in members[4:]})  # 2 只下跌
    assert direction_alignment(members, chg, market_chg=2.0) == round(4 / 6, 3)


def test_direction_alignment_market_down_uses_down_ratio():
    from app.services.theme_catalog_service import direction_alignment

    members = ["a", "b", "c", "d", "e", "f"]
    chg = {s: -1.0 for s in members}
    # 大盘下跌日：全成分下跌 → 方向一致占比 1.0（跟跌也是强联动）
    assert direction_alignment(members, chg, market_chg=-1.0) == 1.0


def test_direction_alignment_flat_market_or_thin_members_none():
    from app.services.theme_catalog_service import direction_alignment

    assert direction_alignment(["a"], {"a": 1.0}, market_chg=2.0) is None  # 样本不足
    assert direction_alignment(["a", "b"], {"a": 1.0}, market_chg=0.0) is None  # 大盘平盘
    assert direction_alignment(["a", "b"], {"a": None}, market_chg=2.0) is None  # 无有效行情
