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
from app.events.extract import build_event
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


def test_direct_source_survives_newer_theme_rows_before_limit(store):
    direct = add(store, published=NOW - timedelta(minutes=30), source_item_id="direct-older",
                 source_published=NOW - timedelta(minutes=30))
    for i in range(13):
        add(store, symbol=None, target_type="theme", target="算力", direction=0,
            source_item_id=f"theme-new-{i}", published=NOW - timedelta(minutes=i + 1))
    result = read_attribution(store._sf, symbol="600001", theme_names={"算力"}, limit=12, now=NOW)
    data = result["data"]
    assert data["items"][0]["event_id"] == direct.id
    assert data["count"] == 12
    assert data["evidence_counts"]["theme_only"] == 11
    primary = data["primary_reason"]
    assert primary["event_id"] == direct.id
    assert primary["timing_relation"] == "response_time_unknown"
    assert primary["causality_verified"] is False


def test_stock_events_filter_before_global_window_and_prefer_direct_company(store):
    direct = add(store, published=NOW - timedelta(hours=1), source_item_id="company-older")
    for index in range(305):
        add(store, symbol=None, target_type="theme", target="算力", direction=0,
            published=NOW - timedelta(seconds=index + 1), source_item_id=f"background-{index}")
    request = NS(app=NS(state=NS(theme_catalog=NS(
        get_official_for_symbol=lambda _: [{"theme_name": "算力"}]))))
    result = asyncio.run(routes.events_for_symbol("600001", request, limit=5, store=store))
    assert result["data"]["items"][0]["id"] == direct.id
    assert result["data"]["items"][0]["match_reason"] == "source"
    assert len(result["data"]["items"]) == 5


def test_material_primary_survives_newer_direct_trade_recaps_before_limit(store):
    direct = add(store, published=NOW - timedelta(hours=1), source_item_id="contract-older",
                 source_published=NOW - timedelta(hours=1), title="公司签署设备供货合同",
                 summary="公司已签署合同，收入确认以交付为准")
    for index in range(13):
        add(store, title="公司股价上涨10%", summary="今日成交额8亿元，换手率9%",
            published=NOW - timedelta(minutes=index + 1), source_published=NOW - timedelta(minutes=index + 1),
            direction=0, source_item_id=f"direct-recap-{index}")
    data = read_attribution(store._sf, symbol="600001", limit=12, now=NOW)["data"]
    assert data["primary_reason"]["event_id"] == direct.id
    assert data["count"] == 12


@pytest.mark.parametrize("title,summary,expected", [
    ("公司股价上涨10%", "投资者关注后市表现", "unconfirmed"),
    ("公司股票交易异常波动公告", "公司股价上涨10%，没有新增重大事项", "unconfirmed"),
    ("公司股价上涨10%", "公司签署设备采购合同，具体收入影响待核", "source_clue"),
    ("公司股价上涨10%", "原材料供应短缺导致涨价", "source_clue"),
    ("公司股价上涨10%", "公司拟投资2亿元建设生产基地，尚待审批", "source_clue"),
])
def test_primary_recap_material_exception_requires_real_action(store, title, summary, expected):
    add(store, title=title, summary=summary, direction=0, source_item_id="bounded-material",
        source_published=NOW - timedelta(minutes=5))
    result = read_attribution(store._sf, symbol="600001", now=NOW)["data"]
    assert result["primary_reason"]["status"] == expected
    assert result["primary_reason"]["causality_verified"] is False


def test_primary_clue_binds_exact_summary_and_preserves_unfulfilled_conditions(store):
    summary = "公司AIGC平台尚在建设完善中，相关项目尚未形成收入；影响存在不确定性。"
    direct = add(store, title="公司回应AIGC平台进展", summary=summary, direction=0,
                 certainty="done", source_item_id="conditional-clue",
                 source_published=NOW - timedelta(minutes=10))
    add(store, symbol=None, target_type="theme", target="AIGC", title="腾讯概念上涨",
        summary="仅板块行情", direction=0, source_item_id="theme-price")
    data = read_attribution(store._sf, symbol="600001", theme_names={"AIGC"}, now=NOW,
                            pool_date="2026-10-12", response_as_of=NOW)["data"]
    primary = data["primary_reason"]
    bound = next(row for row in data["items"] if row["event_id"] == direct.id)
    assert primary["status"] == "source_clue"
    assert primary["version_id"] == bound["interpretation_ref"]["version_id"]
    assert primary["observation_id"] == bound["source_context"]["observation_id"]
    assert primary["excerpt"]["field"] == "summary"
    assert primary["excerpt"]["quote"] in summary
    assert "尚未形成收入" in primary["excerpt"]["quote"]
    assert {q["kind"] for q in primary["qualifiers"]} >= {"incomplete", "scope"}
    assert primary["timing_relation"] == "known_before_response"
    assert primary["causality_verified"] is False


def test_primary_title_denial_is_not_replaced_by_price_only_summary(store):
    title = "公司澄清尚未签署算力订单"
    summary = "公司昨日股价上涨10%，今日换手率较高。"
    add(store, title=title, summary=summary, direction=0, certainty="unknown",
        source_item_id="title-denial", source_published=NOW - timedelta(minutes=5))
    primary = read_attribution(store._sf, symbol="600001", now=NOW)["data"]["primary_reason"]
    assert primary["excerpt"]["field"] == "title"
    assert primary["excerpt"]["quote"] in title and "尚未签署" in primary["excerpt"]["quote"]
    assert {q["kind"] for q in primary["qualifiers"]} >= {"denial", "incomplete"}
    assert primary["fact_verified"] is False and primary["causality_verified"] is False


def test_primary_business_denial_precedes_unfulfilled_price_target_in_other_field(store):
    title = "公司澄清不存在合作订单"
    summary = "公司股价尚未突破前期高点，换手率较高。"
    add(store, title=title, summary=summary, direction=0, certainty="unknown",
        source_item_id="denial-versus-price-condition", source_published=NOW - timedelta(minutes=5))
    primary = read_attribution(store._sf, symbol="600001", now=NOW)["data"]["primary_reason"]
    assert primary["excerpt"]["field"] == "title"
    assert primary["excerpt"]["quote"] in title and "不存在合作订单" in primary["excerpt"]["quote"]
    assert primary["fact_verified"] is False and primary["causality_verified"] is False


@pytest.mark.parametrize("summary", [
    "公司股价上涨10%，回购概念受到投资者关注。",
    "公司股价上涨10%，股份回购概念受到投资者关注。",
    "公司股价上涨10%，回购受到投资者关注。",
])
def test_repurchase_noun_does_not_turn_trade_recap_into_material_event(store, summary):
    add(store, title="公司股价上涨10%", summary=summary, direction=0,
        source_item_id="repurchase-noun", source_published=NOW - timedelta(minutes=5))
    data = read_attribution(store._sf, symbol="600001", now=NOW)["data"]
    assert data["primary_reason"]["status"] == "unconfirmed"
    assert data["primary_reason"]["excerpt"] is None
    assert data["count"] == 1


@pytest.mark.parametrize("title", ["公司拟回购股份", "公司已完成股份回购", "公司披露回购进展"])
def test_reported_repurchase_action_is_kept_when_summary_only_reports_price(store, title):
    add(store, title=title, summary="公司股价上涨10%，换手率较高。", direction=0,
        source_item_id="repurchase-action", source_published=NOW - timedelta(minutes=5))
    primary = read_attribution(store._sf, symbol="600001", now=NOW)["data"]["primary_reason"]
    assert primary["status"] == "source_clue"
    assert primary["excerpt"]["field"] == "title" and primary["excerpt"]["quote"] in title


def test_primary_later_direct_source_never_becomes_verified_cause(store):
    add(store, source_item_id="later-direct", source_published=NOW - timedelta(minutes=10))
    data = read_attribution(store._sf, symbol="600001", now=NOW,
                            response_as_of=NOW - timedelta(minutes=20))["data"]
    assert data["items"] == [] and len(data["later_items"]) == 1
    primary = data["primary_reason"]
    assert primary["status"] == "later_clue"
    assert primary["timing_relation"] == "late_source_observation"
    assert primary["causality_verified"] is False and primary["fact_verified"] is False


def test_primary_missing_source_clock_stays_unconfirmed_without_response_context(store):
    add(store, source_item_id="no-source-clock")
    primary = read_attribution(store._sf, symbol="600001", now=NOW)["data"]["primary_reason"]
    assert primary["status"] == "unconfirmed" and primary["timing_relation"] == "time_unknown"
    assert primary["excerpt"]["quote"] in "尚需审批，标的规模较小"


def test_auxiliary_foreign_input_version_is_not_a_bound_receipt(store):
    own = add(store, source_item_id="own-auxiliary")
    foreign = add(store, symbol="600002", source_item_id="foreign-input")
    with store._sf() as db:
        own_version = db.query(EventInterpretation).filter_by(event_id=own.id).one()
        foreign_version = db.query(EventInterpretation).filter_by(event_id=foreign.id).one()
        payload = json.loads(own_version.payload_json)
        payload["llm_aux"] = {"event_id": own.id, "observation_id": own_version.observation_id,
            "input_version_id": foreign_version.id, "direction": 0, "theme": None,
            "reason": "无明确方向", "chain": "审批待核",
            "evidence": {"field": "summary", "quote": "尚需审批，标的规模较小"},
            "state": "no_direction", "verification": "unverified", "eligibility_changed": False}
        db.add(EventInterpretation(event_id=own.id, observation_id=own_version.observation_id,
            state="active", effective_at=NOW, payload_json=json.dumps(payload), review_note="LLM 辅助判读完成"))
        db.commit()
    projected = read_attribution(store._sf, event_id=own.id, symbol="600001", now=NOW)["data"]["items"][0]
    assert projected["llm_aux"] is None
    assert projected["source_context"]["summary"] == "尚需审批，标的规模较小"


@pytest.mark.parametrize("title,summary,expected", [
    ("新华传媒股价上涨10%", "今日成交额8亿元，换手率9%", "unconfirmed"),
    ("宏和科技今日股价下跌5%", "成交量30万手，主力资金净流出1亿元", "unconfirmed"),
    ("公司股价上涨10%", "供应短缺使材料涨价，供需变化仍需核查", "source_clue"),
    ("公司股价上涨10%", "行业政策发布，具体支持条件见原文", "source_clue"),
    ("公司股价下跌5%", "公司拟收购设备资产，尚待审批", "source_clue"),
    ("厄尔尼诺加剧", "干旱使全球粮食减产，国际粮价上涨。", "source_clue"),
    ("厄尔尼诺概念上涨5%", "题材成交额增加", "unconfirmed"),
])
def test_primary_trade_recap_filter_preserves_mixed_material_clues(store, title, summary, expected):
    add(store, title=title, summary=summary, direction=0, source_item_id="trade-recap",
        source_published=NOW - timedelta(minutes=5))
    data = read_attribution(store._sf, symbol="600001", now=NOW, response_as_of=NOW)["data"]
    assert data["primary_reason"]["status"] == expected
    assert data["primary_reason"]["causality_verified"] is False
    assert data["count"] == 1  # Filtering a headline does not erase its source record.


@pytest.mark.parametrize("kind", ["rumor", "opinion"])
def test_recorded_fact_survives_newer_active_nonfact_sources_before_limit(store, kind):
    original = add(store, source_item_id="valid-recorded-fact", title="公司签署设备供货合同",
        summary="合同已签署，具体收入影响尚待核查", certainty="done", half_life_hours=72,
        published=NOW - timedelta(hours=10), source_published=NOW - timedelta(hours=10))
    for i in range(13):
        add(store, source_item_id=f"active-nonfact-{i}", title="公司业务观点或传闻待核",
            certainty="rumor", fact_kind=kind, half_life_hours=24,
            published=NOW - timedelta(hours=5, minutes=i),
            source_published=NOW - timedelta(hours=5, minutes=i))
    data=read_attribution(store._sf,symbol="600001",now=NOW,limit=12)["data"]
    assert data["primary_reason"]["event_id"] == original.id
    assert data["count"] == 12
    assert sum(row["fact_support"]["status"] == kind for row in data["items"]) == 11
    assert all(row["state"] == "active" for row in data["items"])


def test_active_direct_source_survives_newer_expired_direct_rows_before_limit(store):
    active = add(store, source_item_id="active-corporate", title="公司签署设备供货合同",
        summary="合同已签署，具体收入影响尚待核查", certainty="done", half_life_hours=72,
        published=NOW - timedelta(hours=10), source_published=NOW - timedelta(hours=10))
    for i in range(13):
        add(store, source_item_id=f"expired-rumor-{i}", title="公司业务传闻待核",
            certainty="rumor", fact_kind="rumor", half_life_hours=2,
            published=NOW - timedelta(hours=5, minutes=i),
            source_published=NOW - timedelta(hours=5, minutes=i))
    data = read_attribution(store._sf, symbol="600001", now=NOW, limit=12)["data"]
    assert data["items"][0]["event_id"] == active.id
    assert data["primary_reason"]["event_id"] == active.id
    assert data["count"] == 12
    assert sum(row["state"] == "expired" for row in data["items"]) == 11


@pytest.mark.parametrize("kind", ["theme", "price", "unknown_time", "foreign_identity"])
def test_primary_never_promotes_weak_or_unbound_evidence(store, kind):
    if kind == "theme":
        add(store, symbol=None, target_type="theme", target="算力", source_item_id="weak-theme")
    elif kind == "price":
        add(store, title="公司今日涨停", summary="全天上涨10%", direction=0, source_item_id="price-only")
    else:
        event = add(store, source_item_id=kind)
        if kind == "foreign_identity":
            with store._sf() as db:
                version = db.query(EventInterpretation).filter_by(event_id=event.id).one()
                payload = json.loads(version.payload_json)
                payload["directions"][0]["observation_id"] = 999999
                version.payload_json = json.dumps(payload)
                db.commit()
    data = read_attribution(store._sf, symbol="600001", theme_names={"算力"}, now=NOW,
                            response_as_of=NOW)["data"]
    primary = data["primary_reason"]
    assert primary["status"] == "unconfirmed"
    assert primary["causality_verified"] is False
    if kind != "unknown_time":
        assert primary["event_id"] is None and primary["excerpt"] is None


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


def test_real_macro_producer_survives_persistence_and_both_attribution_routes(store):
    event = build_event("美国非农就业大幅低于预期", source="财联社",
                        published_at=NOW - timedelta(minutes=5), source_published_at=NOW - timedelta(minutes=5))
    assert {d["target_type"] for d in event["directions"]} == {"market", "theme"}
    assert any(d["target_type"] == "market" and d["direction"] == 1 for d in event["directions"])
    row, _ = store.add_event(event)
    request = NS(app=NS(state=NS(theme_catalog=NS(
        get_official_for_symbol=lambda _symbol: [{"theme_name": "苹果概念"}],
    ))))
    single = asyncio.run(routes.event_attribution(row.id, request, symbol="600001", store=store))
    stocks = asyncio.run(routes.stock_attribution("600001", request, limit=12, store=store))
    for result in (single, stocks):
        assert result["data"]["count"] == 1
        data = result["data"]["items"][0]
        assert data["event_id"] == row.id
        assert data["state"] == "active"
        assert data["stock_relation"]["status"] == "theme_only"
        assert data["stock_relation"]["matched_themes"] == ["苹果概念"]
        assert data["stock_relation"]["direction"] is None
        assert data["stock_relation"]["recorded_direction"] is None


def test_market_only_producer_does_not_create_direct_stock_relationship(store):
    event = build_event("美联储宣布降息", source="财联社", published_at=NOW - timedelta(minutes=5))
    assert {d["target_type"] for d in event["directions"]} == {"market"}
    row, _ = store.add_event(event)
    data = read_attribution(store._sf, event_id=row.id, symbol="600001", now=NOW)["data"]["items"][0]
    assert data["state"] == "active"
    assert data["stock_relation"]["status"] == "unknown"
    assert data["stock_relation"]["direction"] is None
    assert read_attribution(store._sf, symbol="600001", now=NOW)["data"]["count"] == 0


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
    # This test checks the missing source clock, not latest-first ordering.
    # Usable, known-clock evidence now precedes unconfirmed rows before LIMIT.
    data = next(row for row in read_attribution(store._sf, symbol="600001", now=NOW)["data"]["items"]
                if row["event_id"] == unknown.id)
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
    ("directions", [{"target_type": "unsupported", "target": "芯片", "direction": 1,
                     "strength": 1, "chain": "", "basis": "", "matched_by": "name"}]),
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


def test_company_receipt_is_read_by_both_existing_routes_without_new_generation(store, monkeypatch):
    """The real router assembly must consume saved enhancement, with original auth."""
    from fastapi.testclient import TestClient
    from app.core.config import settings
    from app.main import app

    row = add(store)
    calls = []
    def saved(value, symbol, **context):
        calls.append((value["event_id"], value["interpretation_ref"]["observation_id"], symbol, context))
        return {"fixture": "published research projection"}

    monkeypatch.setattr("app.events.company_impact.read_saved", saved)
    monkeypatch.setattr(settings, "auth_mode", "local")
    monkeypatch.setattr(settings, "api_token", "company-reader-fixture")
    monkeypatch.setattr(app.state, "theme_catalog", None, raising=False)
    monkeypatch.setitem(app.dependency_overrides, routes.get_store, lambda: store)
    client = TestClient(app)
    paths = ["/api/events/symbol/600001/attribution", f"/api/events/{row.id}/attribution?symbol=600001"]
    for path in paths:
        assert client.get(path).status_code == 401
        assert not calls or len(calls) == 1
        response = client.get(path, headers={"X-API-Token": "company-reader-fixture"})
        assert response.status_code == 200
        assert response.json()["data"]["items"][0]["company_impact"]["fixture"] == "published research projection"
    assert len(calls) == 2 and all(call[0] == row.id and call[2] == "600001" for call in calls)


def test_reviewed_operating_context_outside_recent_window_remains_readable(store, monkeypatch):
    older = add(store, published=NOW - timedelta(hours=2), source_item_id="old-reviewed")
    for n in range(13):
        add(store, published=NOW - timedelta(minutes=n), source_item_id=f"fresh-{n}")
    baseline = read_attribution(store._sf, symbol="600001", now=NOW)["data"]
    assert older.id not in {i["event_id"] for i in baseline["items"]}
    monkeypatch.setattr("app.events.company_impact.published_event_ids", lambda _: [older.id])
    def saved(value, symbol, **_):
        return {"fixture": "source-reviewed current context"} if value["event_id"] == older.id else None
    monkeypatch.setattr("app.events.company_impact.read_saved", saved)
    data = routes._read_attribution(store, None, symbol="600001", now=NOW)["data"]
    assert [v["event_id"] for v in data["items"]] == [v["event_id"] for v in baseline["items"]]
    assert data["count"] == baseline["count"] and data["primary_reason"] == baseline["primary_reason"]
    assert [v["event_id"] for v in data["company_items"]] == [older.id]
    monkeypatch.setattr("app.events.company_impact.read_saved", lambda *a, **k: None)
    assert routes._read_attribution(store, None, symbol="600001", now=NOW)["data"]["company_items"] == []


def test_response_context_is_not_historical_replay_and_keeps_late_evidence(store):
    before = NOW - timedelta(hours=1)
    late = add(store, source_published=before, source_item_id='late-response')
    response = read_attribution(store._sf, symbol='600001', now=NOW,
                               pool_date='2026-10-12', response_as_of=before)
    assert response['data']['response_context']['historical_replay'] is False
    assert response['data']['items'] == []
    item = response['data']['later_items'][0]
    assert item['event_id'] == late.id
    assert item['timing_relation']['status'] == 'late_source_observation'
    assert item['stock_relation']['direction'] == 1


def test_current_display_retains_old_version_but_does_not_confirm_qualified_progress(store):
    row = add(store, title='AIGC平台尚在建设完善中', summary='相关业务进展及财务影响均存在不确定性，尚未形成收入。',
              matched_by='source', source_item_id='legacy-progress')
    with store._sf() as db:
        version = db.query(EventInterpretation).filter_by(event_id=row.id).one()
        payload = json.loads(version.payload_json)
        payload['directions'][0]['basis'] = '命中利好词「进展」→ 利好'
        version.payload_json = json.dumps(payload, ensure_ascii=False)
        db.commit()
        saved = version.payload_json
    data = item(store)
    assert data['stock_relation']['recorded_direction'] == 1
    assert data['stock_relation']['direction'] is None
    with store._sf() as db:
        assert db.get(EventInterpretation, version.id).payload_json == saved


def save_stock_reasoning_aux(store, row, *, direction=1, theme="商业航天"):
    with store._sf() as db:
        prior = db.query(EventInterpretation).filter_by(event_id=row.id).order_by(EventInterpretation.id.desc()).first()
        payload = json.loads(prior.payload_json)
        payload["llm_aux"] = {"event_id": row.id, "observation_id": prior.observation_id,
            "input_version_id": prior.id, "direction": direction, "theme": theme,
            "reason": "融资用途仍需兑现", "chain": "融资用于研发，后续交付待核",
            "evidence": {"field": "summary", "quote": payload["summary"]},
            "state": "hypothesis" if direction else "no_direction",
            "verification": "unverified", "eligibility_changed": False}
        db.add(EventInterpretation(event_id=row.id, observation_id=prior.observation_id,
            state="active", effective_at=NOW, payload_json=json.dumps(payload)))
        db.commit()


def test_stock_reasoning_exposes_real_company_basis_and_exact_receipt(store):
    row = add(store, matched_by="source", direction=0)
    save_stock_reasoning_aux(store, row)
    x = item(store)
    reason = x["stock_reasoning"]
    assert reason["symbol"] == "600001"
    assert reason["status"] == "source_related"
    assert reason["basis"] == ["对应公司公告"]
    assert reason["transmission"] == "收购预案 → 业务变化待核"
    assert reason["source_excerpt"]["quote"] == x["source_context"]["summary"]
    assert reason["ai_scope"] == "company_context"
    assert reason["version_id"] == x["interpretation_ref"]["version_id"]
    assert reason["observation_id"] == x["source_context"]["observation_id"]
    assert reason["business_effect_verified"] is False and reason["eligibility_changed"] is False
    assert x["stock_relation"]["direction"] is None


@pytest.mark.parametrize("theme", ["商业航天", "机器人"])
def test_stock_reasoning_does_not_turn_theme_membership_into_company_ai(store, theme):
    row = add(store, symbol=None, target_type="theme", target="机器人", matched_by="llm_aux")
    save_stock_reasoning_aux(store, row, theme=theme)
    x = item(store, theme_names={"机器人"})
    reason = x["stock_reasoning"]
    assert reason["status"] == "theme_only"
    assert reason["ai_scope"] == "not_applicable"
    assert "机器人" in reason["basis"][0]
    assert reason["transmission"] is None
    assert ("题材不同" if theme == "商业航天" else "没有公司经营关系") in reason["ai_scope_reason"]


def test_stock_reasoning_neutral_is_processed_but_not_a_new_catalyst(store):
    row = add(store, matched_by="source", direction=0)
    save_stock_reasoning_aux(store, row, direction=0, theme=None)
    reason = item(store)["stock_reasoning"]
    assert reason["ai_scope"] == "company_context"
    assert "无明确催化方向" in reason["ai_scope_reason"]


def test_stock_reasoning_never_consumes_expired_or_foreign_source(store):
    row = add(store)
    save_stock_reasoning_aux(store, row)
    expired = read_attribution(store._sf, event_id=row.id, symbol="600001", now=NOW + timedelta(days=20))["data"]["items"][0]
    assert expired["stock_reasoning"]["status"] == "inactive"
    assert expired["stock_reasoning"]["ai_scope"] == "not_applicable"
    other = add(store, symbol="600002", source_item_id="foreign-reasoning")
    with store._sf() as db:
        foreign = db.query(EventInterpretation).filter_by(event_id=other.id).one()
        own = db.query(EventInterpretation).filter_by(event_id=row.id).order_by(EventInterpretation.id.desc()).first()
        own.observation_id = foreign.observation_id
        db.commit()
    x = read_attribution(store._sf, event_id=row.id, symbol="600001", now=NOW)["data"]["items"][0]
    assert x["stock_reasoning"]["ai_scope"] == "not_applicable"
    assert x["stock_reasoning"]["source_excerpt"] is None


def test_stock_reasoning_preserves_opposing_source_directions_without_ai_adoption(store):
    row = add(store, direction=1, matched_by="source")
    save_stock_reasoning_aux(store, row)
    with store._sf() as db:
        version = db.query(EventInterpretation).filter_by(event_id=row.id).order_by(EventInterpretation.id.desc()).first()
        payload = json.loads(version.payload_json)
        opposite = dict(payload['directions'][0], direction=-1, basis='相反成本风险')
        payload['directions'].append(opposite)
        version.payload_json = json.dumps(payload)
        db.commit()
    reason = item(store)["stock_reasoning"]
    assert reason["basis"] == ["对应公司公告", "相反成本风险"]
    assert reason["ai_scope"] == "not_applicable"
    assert "冲突" in reason["ai_scope_reason"]
    assert reason["transmission"] is None


def test_stock_reasoning_missing_ai_does_not_erase_real_source_relationship(store):
    add(store, direction=0, matched_by="source")
    reason = item(store)["stock_reasoning"]
    assert reason["status"] == "source_related"
    assert reason["basis"] == ["对应公司公告"]
    assert "没有可用的已保存AI判读" in reason["ai_scope_reason"]
    assert "没有可核对的个股关系" not in reason["ai_scope_reason"]


def test_company_status_visible_without_any_event_and_get_never_starts_work(store, monkeypatch):
    from app.core.config import settings
    from app.events import company_state, company_sources

    monkeypatch.setattr(settings, "company_evidence_enabled", True, raising=False)
    calls = []
    def projection(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return {"symbol": symbol, "state": "source_failed", "missing": ["正式披露来源失败"],
                "updated_at": NOW.isoformat(), "documents_count": 0}
    monkeypatch.setattr(company_state, "read_status", projection)
    def forbidden(*args, **kwargs):
        pytest.fail("ordinary GET must not collect, generate or enqueue")
    monkeypatch.setattr(company_sources, "collect_company", forbidden)
    monkeypatch.setattr("app.services.agent_tasks.create_task", forbidden)
    monkeypatch.setattr("app.core.llm_client.bounded_chat_completion", forbidden)
    before = list(company_state.ci.ROOT.glob("**/*"))
    data = routes._read_attribution(store, None, symbol="600001", now=NOW)["data"]
    assert data["items"] == []
    assert data["company_status"]["state"] == "source_failed"
    assert calls[0][0] == "600001" and calls[0][1]["enabled"] is True
    assert callable(calls[0][1]["task_resolver"])
    assert list(company_state.ci.ROOT.glob("**/*")) == before


def test_company_status_uses_same_stock_clock_and_history_cutoff(store, monkeypatch):
    from app.events import company_state
    cutoff = NOW - timedelta(minutes=3)
    seen = []
    monkeypatch.setattr(company_state, "read_status", lambda symbol, **kw: seen.append((symbol, kw)) or
                        {"symbol": symbol, "state": "historical_excluded", "documents_count": 0})
    direct = add(store)
    result = routes._read_attribution(store, None, symbol="600001", event_id=direct.id,
                                      now=NOW, response_as_of=cutoff)
    assert result["data"]["company_status"]["symbol"] == "600001"
    assert seen[0][1]["now"] == NOW
    assert seen[0][1]["response_as_of"] == cutoff
    without_symbol = routes._read_attribution(store, None, event_id=direct.id, now=NOW)
    assert "company_status" not in without_symbol["data"]
    assert len(seen) == 1
