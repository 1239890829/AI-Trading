"""Attribution is a current read view, never a causal verdict or new writer."""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

from app.api.routes import events as routes
from app.events.attribution import read_attribution
from app.events.store import EventStore
from app.models.event import EventCard, EventDirection, EventInterpretation
from app.models.watchlist import Base

NOW = datetime(2026, 10, 12, 10, 0)


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'attribution.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("app.events.store.beijing_now_naive", lambda: NOW)
    monkeypatch.setattr(routes, "beijing_now_naive", lambda: NOW)
    return EventStore(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))


def add(store, *, symbol="600001", direction=1, matched_by="manual", title="公司拟收购存储资产",
        summary="尚需审批，标的规模较小", published=None, source_published=None,
        source_item_id="source-1", target_type="symbol", target=None, **kwargs):
    return store.add_event({
        "fingerprint": source_item_id, "title": title, "summary": summary,
        "url": "https://example.com/disclosure", "source": "交易所公告",
        "source_tier": 5, "source_item_id": source_item_id, "source_symbol": symbol,
        "source_symbols": [symbol] if symbol else [],
        "published_at": published or NOW - timedelta(minutes=5),
        "source_published_at": source_published,
        "certainty": "proposed", "category": "corporate", "fact_kind": "fact",
        "directions": [{"target_type": target_type, "target": target or symbol,
                        "direction": direction, "matched_by": matched_by,
                        "chain": "收购预案 → 业务变化待核", "basis": "对应公司公告"}],
        **kwargs,
    })[0]


def item(store, symbol="600001", **kwargs):
    return read_attribution(store._sf, symbol=symbol, now=NOW, **kwargs)["data"]["items"][0]


def test_exact_context_distinguishes_source_relation_market_and_clocks(store):
    published = NOW - timedelta(hours=2)
    row = add(store, source_published=published)
    data = item(store)
    assert data["event_id"] == row.id
    assert data["stock_relation"]["status"] == "direct_symbol"
    assert data["stock_relation"]["direction"] == 1
    assert "denial" not in {q["kind"] for q in data["qualifiers"]}
    assert data["fact_support"]["status"] == "source_recorded"
    assert data["source"]["original_publisher"] is None
    assert data["source"]["source_tier_label"] == "官方披露"
    assert data["source_context"]["summary"] == "尚需审批，标的规模较小"
    assert data["timing"]["source_published_at"] == "2026-10-12T08:00:00+08:00"
    assert data["timing"]["available_at"] == "2026-10-12T10:00:00+08:00"
    assert data["timing"]["source_time_known"] is True
    assert {q["kind"] for q in data["qualifiers"]} >= {"proposal", "scope"}
    assert data["market_response"]["fact_verified"] is False
    assert data["market_response"]["causality_verified"] is False
    assert data["market_response"]["status"] == "unknown"
    result = read_attribution(store._sf, symbol="600001", now=NOW)
    assert result["meta"]["historical_selection_basis"] is False
    assert result["meta"]["opportunity_eligibility_changed"] is False


def test_theme_direction_does_not_become_stock_benefit_or_llm_validation(store):
    add(store, symbol=None, target_type="theme", target="算力", title="海外AI需求与原材料涨价",
        source_item_id="theme-1", summary="供给及成本影响需逐股核查")
    data = item(store, theme_names={"算力"})
    assert data["stock_relation"]["status"] == "theme_only"
    assert data["stock_relation"]["direction"] is None
    assert data["stock_relation"]["matched_themes"] == ["算力"]
    assert {d["key"] for d in data["dimensions"]} >= {"demand", "price", "industry"}
    add(store, matched_by="llm_aux", source_item_id="model-1", title="模型推断公司受益")
    data = item(store)
    assert data["stock_relation"]["status"] == "unverified_model"
    assert data["stock_relation"]["direction"] is None
    assert data["stock_relation"]["recorded_direction"] == 1


def test_association_and_denial_preserve_unknown_instead_of_forcing_positive(store):
    add(store, direction=0)
    data = item(store)
    assert data["stock_relation"]["status"] == "source_association"
    assert data["stock_relation"]["direction"] is None
    add(store, source_item_id="denial-1", title="公司澄清暂无相关业务", summary="未参与项目；尚未供货")
    data = item(store)
    assert data["source_context"]["summary"] == "未参与项目；尚未供货"
    assert data["stock_relation"]["recorded_direction"] == 1
    assert data["stock_relation"]["direction"] is None
    assert {q["kind"] for q in data["qualifiers"]} >= {"denial", "incomplete"}


@pytest.mark.parametrize("summary", [
    "公司已经签署订单，没有重大财务变化。",
    "订单已生效，公司不存在重大风险。",
    "公司澄清：订单已经生效，产品正常交付。",
])
def test_generic_disclaimer_or_clarification_does_not_deny_reported_order(store, summary):
    add(store, title="公司签署产品订单", summary=summary, certainty="done")
    data = item(store)
    assert data["source_context"]["summary"] == summary
    assert data["stock_relation"]["status"] == "direct_symbol"
    assert data["stock_relation"]["direction"] == 1
    assert "denial" not in {q["kind"] for q in data["qualifiers"]}
    assert "scope" in {q["kind"] for q in data["qualifiers"]}


@pytest.mark.parametrize("summary", [
    "公司未签署所传订单。",
    "公司尚未供货。",
    "公司不涉及相关产品业务。",
    "公司不存在合作关系。",
])
def test_explicit_business_denial_does_not_present_older_positive_as_supported(store, summary):
    add(store, title="公司回应合作及订单消息", summary=summary, certainty="unknown")
    data = item(store)
    assert data["source_context"]["summary"] == summary
    assert data["stock_relation"]["recorded_direction"] == 1
    assert data["stock_relation"]["direction"] is None
    assert "denial" in {q["kind"] for q in data["qualifiers"]}


def test_late_capture_future_versions_and_unknown_clock_are_distinct(store):
    old = add(store, source_published=NOW - timedelta(days=1), source_item_id="late")
    future = add(store, published=NOW + timedelta(minutes=1), source_item_id="future")
    version_future = add(store, source_item_id="version-future")
    with store._sf() as db:
        version = db.query(EventInterpretation).filter_by(event_id=version_future.id).one()
        version.effective_at = NOW + timedelta(seconds=1)
        db.commit()
    result = read_attribution(store._sf, symbol="600001", now=NOW, limit=1)
    assert [data["event_id"] for data in result["data"]["items"]] == [old.id]
    assert future.id not in [data["event_id"] for data in result["data"]["items"]]
    assert result["data"]["items"][0]["timing"]["received_at"] != result["data"]["items"][0]["timing"]["source_published_at"]
    unknown = add(store, source_item_id="unknown")
    data = item(store)
    assert data["event_id"] == unknown.id
    assert data["timing"]["source_published_at"] is None
    assert data["timing"]["source_time_known"] is False


def test_revision_suspends_direction_and_retains_full_changed_conditions(store):
    original = add(store)
    add(store, title="公司拟收购存储资产", summary="交易条件变化，标的规模较小且未完成审批")
    data = item(store)
    assert data["event_id"] == original.id
    assert data["state"] == "pending"
    assert data["stock_relation"]["direction"] is None
    assert data["source_context"]["summary"] == "交易条件变化，标的规模较小且未完成审批"
    assert data["source_context"]["change_kind"] == "revision"
    assert data["interpretation_ref"]["observation_id"] == data["source_context"]["observation_id"]


def test_withdrawal_keeps_new_source_notice_and_old_event_identity(store):
    row = add(store)
    notice = add(store, title="撤回此前收购公告", summary="正式公告取消交易", source_item_id="notice")
    target_obs = store.observations_of(row.id)[0]
    notice_obs = store.observations_of(notice.id)[0]
    prior = store.interpretations_of(row.id)[-1]
    store.link_withdrawal(row.id, target_observation_id=target_obs.id,
                          notice_observation_id=notice_obs.id,
                          expected_interpretation_id=prior.id, note="公告编号对应此前交易")
    data = read_attribution(store._sf, event_id=row.id, symbol="600001", now=NOW)["data"]["items"][0]
    assert data["state"] == "withdrawn"
    assert data["stock_relation"]["direction"] is None
    assert data["withdrawal_context"]["event_id"] == notice.id
    assert data["withdrawal_context"]["source_item_id"] == "notice"
    assert data["withdrawal_context"]["review_note"] == "公告编号对应此前交易"
    assert data["source_context"]["observation_id"] == target_obs.id


def test_legacy_event_does_not_invent_observation_or_interpretation_clock(store):
    with store._sf() as db:
        db.add(EventCard(fingerprint="legacy", title="历史关联", source="旧来源",
                         source_symbol="600001", published_at=NOW - timedelta(minutes=1)))
        db.commit()
    data = item(store)
    assert data["state"] == "unknown"
    assert data["source_context"]["observation_id"] is None
    assert data["timing"]["source_published_at"] is None
    assert data["timing"]["received_at"] is None
    assert data["interpretation_ref"]["version_id"] is None
    assert data["fact_support"]["status"] == "unknown"


def test_legacy_recorded_direction_is_not_presented_as_bound_stock_evidence(store):
    with store._sf() as db:
        row = EventCard(fingerprint="legacy-direction", title="历史订单关系", source="旧来源",
                        source_symbol="600001", published_at=NOW - timedelta(minutes=1))
        db.add(row)
        db.flush()
        db.add(EventDirection(event_id=row.id, target_type="symbol", target="600001", direction=1,
                              chain="旧关系", basis="旧字段", matched_by="manual"))
        db.commit()
    data = item(store)
    assert data["state"] == "unknown"
    assert data["stock_relation"]["status"] == "unknown"
    assert data["stock_relation"]["label"] == "历史关联未核"
    assert data["stock_relation"]["recorded_direction"] == 1
    assert data["stock_relation"]["direction"] is None
    assert data["source_context"]["observation_id"] is None


def test_unknown_event_stage_does_not_present_current_direction_as_effective(store):
    add(store, title="公司订单消息", summary="事件阶段待核", certainty="unknown")
    data = item(store)
    assert data["certainty"] == "unknown"
    assert data["stock_relation"]["recorded_direction"] == 1
    assert data["stock_relation"]["direction"] is None
    assert any(q["label"] == "事件阶段未核定" for q in data["qualifiers"])


def test_get_has_bounded_reads_no_writes_syncs_or_external_calls(store):
    for index in range(20):
        add(store, source_item_id=f"event-{index}")
    statements = []
    engine = store._sf.kw["bind"]
    sa_event.listen(engine, "before_cursor_execute", lambda _c, _cursor, statement, *_: statements.append(statement))

    class Catalog:
        def get_official_for_symbol(self, symbol):
            assert symbol == "600001"
            return [{"theme_name": "算力"}]

        def sync_members(self, *args):
            raise AssertionError("GET must not sync")

    request = NS(app=NS(state=NS(theme_catalog=Catalog())))
    result = asyncio.run(routes.stock_attribution("600001.SH", request, limit=12, store=store))
    assert result["data"]["count"] == 12
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 3
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
    result = asyncio.run(routes.event_attribution(result["data"]["items"][0]["event_id"], request, symbol=None, store=store))
    assert result["data"]["symbol"] is None
    assert result["data"]["items"][0]["stock_relation"]["status"] == "unknown"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.event_attribution(99999, request, symbol=None, store=store))
    assert exc.value.status_code == 404


def test_future_source_clock_never_becomes_effective_stock_direction(store):
    add(store, source_published=NOW + timedelta(minutes=5))
    data = item(store)
    assert data["state"] == "unavailable"
    assert data["stock_relation"]["direction"] is None
    assert data["is_active"] is False


def test_source_excerpt_categories_cover_weather_and_social_without_benefit(store):
    add(store, source_item_id="weather", title="厄尔尼诺引发干旱减产预期", summary="粮价和加工成本影响待核", direction=0)
    data = item(store)
    assert {d["key"] for d in data["dimensions"]} >= {"weather", "price"}
    assert data["stock_relation"]["direction"] is None
    add(store, source_item_id="social", title="社交媒体热搜传闻", summary="网传内容未核实", direction=0, fact_kind="rumor")
    data = item(store)
    assert data["fact_support"]["status"] == "rumor"
    assert "social" in {d["key"] for d in data["dimensions"]}
    assert data["market_response"]["causality_verified"] is False


def test_incomplete_interpretation_never_promotes_original_direction(store):
    row = add(store)
    with store._sf() as db:
        version = db.query(EventInterpretation).filter_by(event_id=row.id).one()
        version.payload_json = '{"directions": null}'
        db.commit()
    data = item(store)
    assert data["state"] == "unknown"
    assert data["stock_relation"]["direction"] is None
    assert data["source_context"]["observation_id"] is not None


def test_unbound_direction_does_not_claim_evidence_for_this_stock(store):
    row = add(store)
    with store._sf() as db:
        version = db.query(EventInterpretation).filter_by(event_id=row.id).one()
        payload = json.loads(version.payload_json)
        payload["directions"][0]["observation_id"] = None
        version.payload_json = json.dumps(payload)
        db.commit()
    data = item(store)
    assert data["stock_relation"]["status"] == "unknown"
    assert data["stock_relation"]["recorded_direction"] == 1
    assert data["stock_relation"]["direction"] is None


@pytest.mark.parametrize("malformed", [True, False, "1", "-1", 1.0, -1.0, 2, -2, None])
def test_malformed_direction_is_not_displayed_as_a_valid_stock_judgment(store, malformed):
    row = add(store)
    with store._sf() as db:
        version = db.query(EventInterpretation).filter_by(event_id=row.id).one()
        payload = json.loads(version.payload_json)
        payload["directions"][0]["direction"] = malformed
        version.payload_json = json.dumps(payload)
        db.commit()
    data = item(store)
    assert data["stock_relation"]["status"] == "unknown"
    assert data["stock_relation"]["direction"] is None
    assert data["stock_relation"]["recorded_direction"] is None


@pytest.mark.parametrize("field,malformed", [
    ("source_tier", []), ("source_tier", True), ("source_tier", "5"),
    ("category", []), ("fact_kind", []), ("certainty", []),
    ("title", []), ("summary", []), ("url", []),
    ("directions", ["bad direction"]),
    ("directions", [{"target_type": "theme", "target": ["芯片"], "direction": 1}]),
    ("directions", [{"target_type": [], "target": "芯片", "direction": 1}]),
])
def test_one_malformed_interpretation_does_not_break_other_stock_evidence(store, field, malformed):
    good = add(store, source_item_id="good")
    bad = add(store, source_item_id="bad")
    with store._sf() as db:
        version = db.query(EventInterpretation).filter_by(event_id=bad.id).one()
        payload = json.loads(version.payload_json)
        payload[field] = malformed
        raw = json.dumps(payload)
        version.payload_json = raw
        db.commit()
    results = read_attribution(store._sf, symbol="600001", now=NOW)["data"]["items"]
    views = {data["event_id"]: data for data in results}
    assert views[good.id]["state"] == "active"
    assert views[good.id]["stock_relation"]["direction"] == 1
    assert views[bad.id]["state"] == "unknown"
    assert views[bad.id]["stock_relation"]["status"] == "unknown"
    assert views[bad.id]["stock_relation"]["direction"] is None
    assert views[bad.id]["source_context"]["title"] == bad.title
    if field == "source_tier":
        assert views[bad.id]["source"]["source_tier"] is None
        assert views[bad.id]["source"]["source_tier_label"] == "来源等级未知"
    if field in {"fact_kind", "certainty"}:
        assert views[bad.id][field] == "unknown"
    with store._sf() as db:
        assert db.query(EventInterpretation).filter_by(event_id=bad.id).one().payload_json == raw


@pytest.mark.parametrize("field", ["source_tier", "category", "fact_kind", "certainty", "directions"])
def test_missing_interpretation_field_is_unknown_instead_of_defaulting_to_valid(store, field):
    row = add(store)
    with store._sf() as db:
        version = db.query(EventInterpretation).filter_by(event_id=row.id).one()
        payload = json.loads(version.payload_json)
        payload.pop(field)
        version.payload_json = json.dumps(payload)
        db.commit()
    data = item(store)
    assert data["state"] == "unknown"
    assert data["stock_relation"]["status"] == "unknown"
    assert data["stock_relation"]["direction"] is None


@pytest.mark.parametrize("field,malformed", [
    ("chain", ["错误链路"]), ("basis", {"错误依据": 1}),
    ("matched_by", ["manual"]), ("observation_id", [1]), ("observation_id", True),
])
def test_bad_direction_detail_does_not_leak_container_values_into_public_contract(store, field, malformed):
    row = add(store)
    with store._sf() as db:
        version = db.query(EventInterpretation).filter_by(event_id=row.id).one()
        payload = json.loads(version.payload_json)
        payload["directions"][0][field] = malformed
        version.payload_json = json.dumps(payload)
        db.commit()
    data = item(store)
    relation = data["stock_relation"]
    assert data["state"] == "unknown"
    assert relation["direction"] is None
    assert isinstance(relation["chain"], str)
    assert isinstance(relation["basis"], str)
    assert relation["matched_by"] is None or isinstance(relation["matched_by"], str)
    assert relation["observation_id"] is None or type(relation["observation_id"]) is int


def test_new_get_routes_are_authenticated_and_validate_bounds(store, monkeypatch):
    from fastapi.testclient import TestClient
    from app.core.config import settings
    from app.main import app

    row = add(store)
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "api_token", "attribution-test-token")
    monkeypatch.setattr(app.state, "theme_catalog", None, raising=False)
    monkeypatch.setitem(app.dependency_overrides, routes.get_store, lambda: store)
    client = TestClient(app)
    paths = ["/api/events/symbol/600001/attribution", f"/api/events/{row.id}/attribution"]
    for path in paths:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-API-Token": "wrong"}).status_code == 401
        response = client.get(path, headers={"X-API-Token": "attribution-test-token"})
        assert response.status_code == 200
        assert response.json()["data"]["count"] == 1
    headers = {"X-API-Token": "attribution-test-token"}
    assert client.get(paths[0] + "?limit=31", headers=headers).status_code == 422
    assert client.get("/api/events/symbol/not-a-symbol/attribution", headers=headers).status_code == 400
