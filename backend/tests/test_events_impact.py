"""事件影响力视图测试（§六.4 拍板）：四级分类 / L1-L3 分级 / 路由。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.events.impact import classify_four, impact_level


# ---------------------------------------------------------------- 四级分类


def test_classify_four_material_priority_over_policy():
    # 关税(国际/政策词) + 涨价 → 归原材料（材料优先）
    assert classify_four("出口关税上调 推动稀土涨价", "policy") == "material"
    assert classify_four("多晶硅涨价函落地", "corporate") == "material"


def test_classify_four_policy_international_hot():
    assert classify_four("证监会发布减持新规", "policy") == "policy"
    assert classify_four("美联储议息会议表态鹰派", "statement") == "international"
    assert classify_four("存储芯片供不应求传闻发酵", "rumor") == "hot"


# ---------------------------------------------------------------- 影响力分级


def _row(four="hot", certainty="done", fact_kind="fact",
         source_tier=3, n_directions=1):
    return dict(four=four, certainty=certainty,
                fact_kind=fact_kind, source_tier=source_tier, n_directions=n_directions)


def test_level_l1_paths():
    assert impact_level("多晶硅涨价函落地", **_row(four="material")) == "L1"
    assert impact_level("证监会发布减持新规", **_row(four="policy")) == "L1"
    assert impact_level("美联储宣布降息", **_row(four="international", source_tier=4)) == "L1"


def test_level_rumor_never_l1():
    assert impact_level("据悉多晶硅将涨价", **_row(four="material", certainty="rumor",
                                                   fact_kind="rumor", n_directions=0)) != "L1"
    assert impact_level("传闻证监会拟出新规", **_row(four="policy", certainty="rumor",
                                                     fact_kind="rumor", n_directions=0)) != "L1"


def test_level_international_needs_tier3():
    assert impact_level("海外某公司事件", **_row(four="international", source_tier=2)) != "L1"


def test_level_personnel_and_default_l3():
    assert impact_level("某公司董事辞职", **_row(four="hot")) == "L3"
    # 解读类无方向 → L3
    assert impact_level("机构点评行情", **_row(fact_kind="opinion", n_directions=0)) == "L3"


def test_level_l2_fact_with_directions():
    assert impact_level("某龙头签订重大合同", **_row(four="hot")) == "L2"


# ---------------------------------------------------------------- 路由


def test_impact_route_filters_l3(monkeypatch=None):
    """路由冒烟：L3 默认被滤掉，counts_all 仍统计全量。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import events as events_route

    rows = [
        SimpleNamespace(
            id=1, title="多晶硅涨价函落地", url=None, summary=None, source="财联社", source_tier=4,
            published_at=datetime(2026, 9, 4, 2, 0, tzinfo=timezone.utc),
            fact_kind="fact", certainty="done", category="corporate",
            half_life_hours=72, source_symbol=None, status="active",
            directions=[SimpleNamespace(target_type="theme", target="光伏",
                                        direction=1, strength=2, chain="", basis="")],
        ),
        SimpleNamespace(
            id=2, title="某公司董事辞职", url=None, summary=None, source="x", source_tier=2,
            published_at=datetime(2026, 9, 4, 2, 0, tzinfo=timezone.utc),
            fact_kind="fact", certainty="done", category="corporate",
            half_life_hours=72, source_symbol=None, status="active",
            directions=[],
        ),
    ]

    class _Store:
        def list_events(self, *, active_only=True, limit=30):
            return rows

    app = FastAPI()
    app.include_router(events_route.router, prefix="/api")
    app.dependency_overrides[events_route.get_store] = lambda: _Store()
    with TestClient(app) as client:
        body = client.get("/api/events/impact").json()
        assert body["data"]["counts_all"] == {"L1": 1, "L2": 0, "L3": 1}
        assert [it["id"] for it in body["data"]["items"]] == [1]
        assert body["data"]["items"][0]["four_label"] == "原材料涨价"
        body2 = client.get("/api/events/impact?include_l3=true").json()
        assert len(body2["data"]["items"]) == 2


# ---------------------------------------------------------------- 事件标签（2026-09-04 任务③）


def test_derive_tags_multi_and_none():
    from app.events.impact import derive_tags

    # 多标签：业绩 + 公告（定增获批复=公告，含业绩词暂无）
    assert "公告" in derive_tags("金富科技：向特定对象发行股票获证监会同意注册批复", "corporate")
    assert "业绩" in derive_tags("美诺华2026年中报净利润为6368.91万元", "corporate")
    assert "异动" in derive_tags("603538，涨停！网红牛散成第四大股东", "other")
    assert "资金" in derive_tags("杠杆资金大手笔加仓股名单", "other")
    assert "行业" in derive_tags("培育钻石概念上涨3.80%，主力资金净流入", "other")
    # 无命中不臆造；公司类兜底归公告
    assert derive_tags("某公司在海外设立办事处", "other") == []
    assert derive_tags("某公司签署日常经营合同", "corporate") == ["公告"]


def test_impact_route_returns_tags_and_tag_counts():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import events as events_route

    rows = [
        SimpleNamespace(
            id=1, title="多晶硅涨价函落地", url=None, summary=None, source="财联社", source_tier=4,
            published_at=datetime(2026, 9, 4, 2, 0, tzinfo=timezone.utc),
            fact_kind="fact", certainty="done", category="corporate",
            half_life_hours=72, source_symbol=None, status="active",
            directions=[SimpleNamespace(target_type="theme", target="光伏",
                                        direction=1, strength=2, chain="", basis="")],
        ),
        SimpleNamespace(
            id=2, title="某公司董事辞职", url=None, summary=None, source="x", source_tier=2,
            published_at=datetime(2026, 9, 4, 2, 0, tzinfo=timezone.utc),
            fact_kind="fact", certainty="done", category="corporate",
            half_life_hours=72, source_symbol=None, status="active",
            directions=[],
        ),
    ]

    class _Store:
        def list_events(self, *, active_only=True, limit=30):
            return rows

    app = FastAPI()
    app.include_router(events_route.router, prefix="/api")
    app.dependency_overrides[events_route.get_store] = lambda: _Store()
    with TestClient(app) as client:
        body = client.get("/api/events/impact?include_l3=true&sort=time").json()
        # id=1 corporate 无关键词命中 → 兜底公告；id=2 同理
        assert body["data"]["tag_counts"] == {"公告": 2}
        tags_by_id = {it["id"]: it["tags"] for it in body["data"]["items"]}
        assert tags_by_id[1] == ["公告"]
        assert tags_by_id[2] == ["公告"]
