"""事件驱动最小闭环测试（linkage-design §4 E1）。网络全 mock。"""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from app.core.db import get_engine, get_session_factory
from app.events.extract import (
    build_event,
    classify_category,
    classify_certainty,
    classify_source_tier,
    extract_directions,
    title_fingerprint,
)
from app.events.store import EventStore
from app.main import app


@pytest.fixture(autouse=True)
def _create_tables():
    from app.models.watchlist import Base as _B

    _B.metadata.create_all(get_engine())
    yield


def _store() -> EventStore:
    return EventStore(get_session_factory())


# ---------------------------------------------------------------- 抽取规则


def test_classify_source_tier_announcement_and_fallback():
    assert classify_source_tier(None, is_announcement=True) == 5
    assert classify_source_tier("财联社快讯") == 4
    assert classify_source_tier("eastmoney") == 3
    assert classify_source_tier("某个未知渠道") == 2


def test_classify_certainty_and_category():
    # 传闻三档联动：rumor 词同时决定 fact_kind 与 certainty
    assert classify_certainty("据悉某大厂拟收购存储厂") == ("rumor", "rumor")
    # 拟议：草案未落地
    assert classify_certainty("美国拟禁止芯片经东南亚转口")[1] == "proposed"
    # 已落地事实
    assert classify_certainty("长鑫 LPDDR6 全球首发量产") == ("fact", "done")
    assert classify_category("美国拟禁止中国 AI 企业采购芯片") == "policy"
    assert classify_category("沃什鹰派发言") == "statement"
    assert classify_category("长鑫 LPDDR6 全球首发量产") == "corporate"


def test_title_fingerprint_dedupes_punctuation():
    a = title_fingerprint("长鑫 LPDDR6 全球首发量产！")
    b = title_fingerprint("长鑫LPDDR6全球首发量产")
    c = title_fingerprint("另一条完全不同的新闻")
    assert a == b != c


def test_extract_directions_alias_and_forbidden_flip():
    names = ["存储芯片", "算力", "信创"]
    # 利好词 + 别名表命中（LPDDR → 存储芯片）
    rows = extract_directions("长鑫 LPDDR6 全球首发量产", names)
    assert [(r["target"], r["direction"]) for r in rows] == [("存储芯片", 1)]
    assert rows[0]["matched_by"] == "alias"
    assert "利好" in rows[0]["basis"] and ("首发" in rows[0]["basis"] or "量产" in rows[0]["basis"])

    # 禁令 + 国产替代题材 → 对冲为利好；禁令 + 租赁属性题材 / 普通算力 → 利空
    rows = extract_directions("美国拟禁止算力租赁及海外算力转口，利好国产信创替代", ["算力", "信创", "算力租赁"])
    by = {r["target"]: r["direction"] for r in rows}
    assert by["算力租赁"] == -1
    assert by["算力"] == -1, "无对冲属性的题材命中利空词 → 利空"
    assert by["信创"] == 1, "禁令对国产替代题材对冲为利好"

    # 无动词命中 → 关联待判（不猜方向）
    rows = extract_directions("粮食概念盘中异动", ["粮食概念"])
    assert rows[0]["direction"] == 0


def test_build_event_completes_all_fields():
    e = build_event("沃什鹰派发言致美股黄金大跌", source="财联社")
    assert e["category"] == "statement" and e["half_life_hours"] == 48
    assert e["source_tier"] == 4 and e["fact_kind"] == "fact"
    assert e["fingerprint"]


# ---------------------------------------------------------------- 存储


def test_store_dedupes_by_fingerprint():
    store = _store()
    title = "某公司签订重大合同中标金额创新高"
    r1, c1 = store.register(title, source="东财")
    r2, c2 = store.register(title + "。", source="东财")
    assert c1 is True and c2 is False
    assert r1.id == r2.id


def test_store_list_active_respects_half_life():
    from datetime import timedelta
    from app.core.db import utcnow

    store = _store()
    # 半衰期 2h 的传闻事件，发布于 5h 前 → 过期
    old = store.register("据悉某公司酝酿重大资产重组", source="东财")[0]
    with store._sf() as db:
        from app.models.event import EventCard

        row = db.get(EventCard, old.id)
        row.published_at = utcnow() - timedelta(hours=5)
        row.half_life_hours = 2
        db.commit()
    assert store.is_active(store.get_event(old.id)) is False
    assert store.get_event(old.id) not in store.list_events(active_only=True)
    # 人工裁决 resolved 也算非活跃
    fresh = store.register("另一家公司发布业绩预增公告", source="东财")[0]
    store.set_status(fresh.id, "resolved")
    assert store.is_active(store.get_event(fresh.id)) is False


# ---------------------------------------------------------------- API


def test_events_api_lifecycle(monkeypatch: pytest.MonkeyPatch):
    with TestClient(app) as client:
        # 目录服务替换为可控实例（注册时用于实体匹配）
        from app.services.theme_catalog_service import ThemeCatalogService

        svc = ThemeCatalogService(get_session_factory(), api_key="t")

        async def fake_catalog():
            # 用独立代码 990001.TI：886042.TI 会被 test_theme_catalog 的 stale 用例使用，
            # 共享内存库下避免跨文件污染（本测试的 stocks 懒同步会给它写入成分）
            return [{"code": "990001.TI", "name": "存储芯片"}]

        monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
        client.app.state.theme_catalog = svc
        client.portal.call(svc.sync_catalog)

        # 手动注册：方向抽取依赖目录名
        r = client.post("/api/events", json={"title": "长鑫 LPDDR6 全球首发量产", "source": "财联社"})
        assert r.status_code == 201
        data = r.json()["data"]
        assert data["created"] is True
        assert data["category"] == "corporate"
        assert data["directions"][0]["target"] == "存储芯片"
        assert data["directions"][0]["direction"] == 1
        eid = data["id"]

        # 重复注册 → created=False
        r2 = client.post("/api/events", json={"title": "长鑫 LPDDR6 全球首发量产"})
        assert r2.json()["data"]["created"] is False

        # 列表
        r = client.get("/api/events")
        assert any(i["id"] == eid for i in r.json()["data"]["items"])

        # 标的池：成分未同步 → 懒同步（mock fetch_members）→ 池返回
        async def fake_members(code):
            return [{"symbol": "000019", "name": "深粮控股"}] if False else [
                {"symbol": "600171", "name": "上海贝岭"}
            ]

        monkeypatch.setattr(svc, "fetch_members", fake_members)
        r = client.get(f"/api/events/{eid}/stocks")
        pools = r.json()["data"]["pools"]
        assert pools[0]["target"] == "存储芯片"
        assert any(s["symbol"] == "600171" for s in pools[0]["stocks"])

        # 人工裁决（2026-09-08 P0-4 起 /events/{id}/review 端点已删——
        # store.set_status 是唯一裁决路径，store 层语义由上面的 storage 用例锚定）
        store = client.app.state.event_store
        store.set_status(eid, "rejected")
        assert store.get_event(eid).status == "rejected"
        r = client.get("/api/events")
        assert all(i["id"] != eid for i in r.json()["data"]["items"]), "rejected 不再出现在活跃列表"

        # 非法状态在 store 层拒绝
        import pytest as _pytest

        with _pytest.raises(ValueError):
            store.set_status(eid, "whatever")


def test_events_for_symbol_api(monkeypatch: pytest.MonkeyPatch):
    """E2：详情页事件标签——按官方归属题材 + source_symbol 两条路径匹配。"""
    with TestClient(app) as client:
        from app.services.theme_catalog_service import ThemeCatalogService

        svc = ThemeCatalogService(get_session_factory(), api_key="t")

        async def fake_catalog():
            return [{"code": "990001.TI", "name": "存储芯片"}]

        async def fake_members(code):
            if code == "990001.TI":
                return [{"symbol": "600171", "name": "上海贝岭"}]
            return []

        monkeypatch.setattr(svc, "fetch_catalog", fake_catalog)
        monkeypatch.setattr(svc, "fetch_members", fake_members)
        client.app.state.theme_catalog = svc
        client.portal.call(svc.sync_catalog)
        client.portal.call(svc.sync_members, "990001.TI")

        # 事件 A：方向命中 600171 的归属题材（标题用变体，避免与前面用例指纹去重）
        r = client.post("/api/events", json={"title": "长鑫 LPDDR6 量产全面爬坡"})
        assert r.json()["data"]["created"] is True
        # 事件 B：来源个股即 600171（source_symbol 路径）
        r = client.post("/api/events", json={"title": "上海贝岭发布业绩预增公告", "source_symbol": "600171"})
        assert r.json()["data"]["created"] is True
        # 事件 C：题材无关（不应命中）
        client.post("/api/events", json={"title": "某机场旅客吞吐量创新高"})

        r = client.get("/api/events/symbol/600171")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["themes"] == ["存储芯片"]
        ids = {i["id"] for i in data["items"]}
        assert len(ids) == 2, "题材命中 + source_symbol 两条路径各命中一个"
        reasons = {i["match_reason"] for i in data["items"]}
        assert reasons == {"theme", "source"}

        # 非法代码
        assert client.get("/api/events/symbol/xyz").status_code == 400


# ---------------------------------------------------------------- G3 方向词典 v2（hotspot-pipeline §3 固定测试集子集）

def test_g3_benefits_word_drives_direction():
    """「受益」进 _POSITIVE：厄尔尼诺→电力/化肥 实测 dir=0 的修复。"""
    names = ["电力", "磷肥及磷化工"]
    rows = extract_directions("厄尔尼诺超强推升粮价，化肥电力受益", names)
    assert rows, "题材必须命中"
    assert all(r["direction"] == 1 for r in rows), "「受益」应给出利好方向"


def test_g3_paper_level_proposed():
    """「论文/实验室」进 _PROPOSED：长存学术消息 certainty=proposed（研究阶段非落地）。"""
    fact_kind, certainty = classify_certainty("长存 3D NAND 论文披露绕过 EUV 的实验室路径")
    assert (fact_kind, certainty) == ("fact", "proposed")


def test_g3_progress_word():
    """「进展」进 _POSITIVE：取得进展类消息方向可判。"""
    rows = extract_directions("国产存储芯片技术取得进展", ["存储芯片"])
    assert rows and rows[0]["direction"] == 1
