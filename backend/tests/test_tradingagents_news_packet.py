"""Existing public source observations are inputs, never old AI conclusions."""
import hashlib
import json
from datetime import datetime, timedelta
from urllib.parse import urlunsplit

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.events.store import EventStore
from app.models.event import EventCard, EventInterpretation, EventObservation
from app.models.watchlist import Base
from app.services import tradingagents_news_packet as news


NOW = datetime(2026, 10, 20, 10, tzinfo=BJ_TZ)


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'news.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("app.events.store.beijing_now_naive", lambda: NOW.replace(tzinfo=None))
    monkeypatch.setattr(news, "beijing_now", lambda: NOW + timedelta(seconds=1))
    return EventStore(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))


def add(store, *, symbols=None, title="600001：示例科技签订设备合同", source="东财快讯",
        url="https://finance.eastmoney.com/a/public-1.html", key="one", summary="设备合同尚需按约履行。"):
    symbols = ["600001"] if symbols is None else symbols
    return store.add_event({
        "fingerprint": key, "title": title, "summary": summary, "source": source,
        "source_item_id": key, "url": url, "source_tier": 3,
        "source_symbol": symbols[0] if symbols else None, "source_symbols": symbols,
        "published_at": NOW.replace(tzinfo=None) - timedelta(minutes=5),
        "source_published_at": NOW.replace(tzinfo=None) - timedelta(minutes=5),
        "fact_kind": "fact", "certainty": "proposed", "category": "corporate", "half_life_hours": 48,
        "directions": [{"target_type": "symbol", "target": "600001", "direction": 0,
                        "strength": 1, "chain": "旧模型解释不能成为原文", "basis": "旧模型依据",
                        "matched_by": "llm_aux"}],
    })[0]


def get(store, **kwargs):
    return news.read_news_packet("600001", session_factory=store._sf, as_of=NOW, **kwargs)


def mutate(store, row, **values):
    with store._sf() as db:
        observation = db.execute(select(EventObservation).where(
            EventObservation.event_id == row.id)).scalar_one()
        for name, value in values.items():
            setattr(observation, name, value)
        db.commit()


def test_public_raw_text_identity_clock_and_normalized_hash_survive(store):
    row = add(store)
    before = []
    engine = store._sf.kw["bind"]
    def check(_conn, _cursor, statement, *_args):
        before.append(statement.lstrip().split()[0].upper())
    event.listen(engine, "before_cursor_execute", check)
    try:
        value = get(store)
    finally:
        event.remove(engine, "before_cursor_execute", check)
    assert value["source_status"] == "ready"
    item, = value["candidate_news"]
    assert item["title"] == "600001：示例科技签订设备合同"
    assert item["summary"] == "设备合同尚需按约履行。"
    assert item["source_symbols"] == ["600001"]
    assert item["published_at"] == "2026-10-20T09:55:00+08:00"
    assert item["received_at"] == item["available_at"] == "2026-10-20T10:00:00+08:00"
    assert item["source_ref"]["event_id"] == row.id
    assert len(item["normalized_content_sha256"]) == 64
    assert "source_sha256" not in item and "symbol" not in item
    assert "旧模型" not in json.dumps(item, ensure_ascii=False)
    assert set(before) <= {"SELECT", "BEGIN"}
    assert value["captured_at"] == "2026-10-20T10:00:01+08:00"


@pytest.mark.parametrize("symbols", [[], ["600002"], ["600001", "600002"]])
def test_unknown_global_and_multiple_subjects_are_not_fabricated_as_target(store, symbols):
    add(store, symbols=symbols, title="其他公司与行业公开消息")
    item, = get(store)["candidate_news"]
    assert item["source_symbols"] == symbols
    assert item["relation"]["source_declares_requested_symbol"] is ("600001" in symbols)
    assert item["company_eligible"] is not True
    assert "symbol" not in item


@pytest.mark.parametrize("field,value", [
    ("content_hash", "f" * 64),
    ("source_published_at", None),
    ("source_published_at", NOW.replace(tzinfo=None) + timedelta(seconds=1)),
    ("received_at", NOW.replace(tzinfo=None) + timedelta(seconds=1)),
    ("available_at", NOW.replace(tzinfo=None) + timedelta(seconds=1)),
    ("source_symbols_json", '["600001", 1]'),
    ("source_symbols_json", '{"600001":true}'),
    ("source_symbols_json", 'bad json'),
])
def test_invalid_source_hash_dates_and_symbol_metadata_fail_closed(store, field, value):
    row = add(store)
    mutate(store, row, **{field: value})
    assert get(store)["candidate_news"] == []


@pytest.mark.parametrize("source,url,title", [
    ("AI摘要", "https://finance.eastmoney.com/a/one.html", "600001公开消息"),
    ("人工私有", "https://finance.eastmoney.com/a/one.html", "600001公开消息"),
    ("内部交易", "https://finance.eastmoney.com/a/one.html", "600001公开消息"),
    ("东财快讯", "https://evil.example/a/one.html", "600001公开消息"),
    ("东财快讯", "http://127.0.0.1/data", "600001公开消息"),
    ("东财快讯", urlunsplit(("https", "{}:{}@{}".format("user", "pass", "finance.eastmoney.com"),
                           "/a/one.html", "", "")), "600001公开消息"),
    ("东财快讯", "https://finance.eastmoney.com/a/one.html?token=private", "600001公开消息"),
    ("东财快讯", "https://finance.eastmoney.com/a/one.html#access_token=private", "600001公开消息"),
    ("东财快讯", "https://finance.eastmoney.com/a/one.html", "我的持仓与账户消息"),
])
def test_untrusted_or_private_material_never_enters_model_packet(store, source, url, title):
    add(store, source=source, url=url, title=title)
    result = get(store)
    assert result["candidate_news"] == []
    assert source not in " ".join(result["missing"])
    assert "private" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("kind", ["pending", "withdrawn", "wrong_observation", "future_version"])
def test_version_eligibility_and_exact_observation_binding(store, kind):
    row = add(store)
    other = add(store, key="other") if kind == "wrong_observation" else None
    with store._sf() as db:
        version = db.execute(select(EventInterpretation).where(
            EventInterpretation.event_id == row.id)).scalar_one()
        card = db.get(EventCard, row.id)
        if kind == "pending":
            card.revision_pending_at = NOW.replace(tzinfo=None)
        elif kind == "withdrawn":
            version.state = "withdrawn"
        elif kind == "wrong_observation":
            observation = db.execute(select(EventObservation).where(
                EventObservation.event_id == other.id)).scalar_one()
            version.observation_id = observation.id
        else:
            version.effective_at = NOW.replace(tzinfo=None) + timedelta(seconds=1)
        db.commit()
    result = get(store)["candidate_news"]
    if kind == "wrong_observation":
        assert all(item["source_ref"]["event_id"] != row.id for item in result)
    else:
        assert result == []


@pytest.mark.parametrize("change", ["summary", "bool_id", "version_id"])
def test_projection_cannot_smuggle_old_ai_summary_or_wrong_version(store, monkeypatch, change):
    add(store)
    original = news.read_attribution
    def modified(*args, **kwargs):
        result = original(*args, **kwargs)
        item = result["data"]["items"][0]
        if change == "summary":
            item["source_context"]["summary"] = "模型编出的业务关系"
        elif change == "bool_id":
            item["interpretation_ref"]["observation_id"] = True
        else:
            item["interpretation_ref"]["version_id"] += 1
        return result
    monkeypatch.setattr(news, "read_attribution", modified)
    assert get(store)["candidate_news"] == []


def test_limit_is_applied_before_exact_lineage_read_and_empty_is_unknown(store, monkeypatch):
    assert get(store)["source_status"] == "missing"
    for index in range(3):
        add(store, key=str(index))
    assert len(get(store, limit=2)["candidate_news"]) == 2
    original, limits = news.read_attribution, []
    def observe(*args, **kwargs):
        limits.append(kwargs["limit"])
        return original(*args, **kwargs)
    monkeypatch.setattr(news, "read_attribution", observe)
    get(store, limit=999)
    assert limits == [30]


@pytest.mark.parametrize("days,kept", [(6, True), (8, False)])
def test_recent_expired_source_is_context_but_old_window_is_missing(store, days, kept):
    row = add(store)
    with store._sf() as db:
        card = db.get(EventCard, row.id)
        observation = db.execute(select(EventObservation).where(
            EventObservation.event_id == row.id)).scalar_one()
        card.published_at = observation.source_published_at = NOW.replace(tzinfo=None) - timedelta(days=days)
        observation.content_hash = hashlib.sha256(json.dumps({
            "title": observation.title, "summary": observation.summary, "url": observation.url,
            "source_published_at": observation.source_published_at.isoformat(),
            "source_symbols": json.loads(observation.source_symbols_json),
            "board_codes": json.loads(observation.board_codes_json),
        }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        db.commit()
    assert bool(get(store)["candidate_news"]) is kept


@pytest.mark.parametrize("symbol,as_of", [("../private", NOW), ("600001", NOW.replace(tzinfo=None)),
                                         ("600001", "2026-10-20")])
def test_invalid_call_never_opens_database(symbol, as_of):
    def forbidden():
        pytest.fail("invalid arguments opened a session")
    with pytest.raises(ValueError):
        news.read_news_packet(symbol, session_factory=forbidden, as_of=as_of)


def test_future_cutoff_never_opens_database(monkeypatch):
    monkeypatch.setattr(news, "beijing_now", lambda: NOW)
    def forbidden():
        pytest.fail("future cutoff opened a session")
    with pytest.raises(ValueError, match="future_news_packet_cutoff"):
        news.read_news_packet("600001", session_factory=forbidden, as_of=NOW + timedelta(seconds=1))


def test_reader_failure_reports_safe_unknown_without_exception_payload():
    def unavailable():
        raise RuntimeError("private database secret")
    result = news.read_news_packet("600001", session_factory=unavailable, as_of=NOW)
    assert result["source_status"] == "failed"
    assert "private" not in json.dumps(result)


def test_source_content_hash_is_the_existing_store_encoding(store):
    row = add(store, summary="同一来源原句")
    with store._sf() as db:
        observation = db.execute(select(EventObservation).where(
            EventObservation.event_id == row.id)).scalar_one()
        expected = hashlib.sha256(json.dumps({
            "title": observation.title, "summary": observation.summary, "url": observation.url,
            "source_published_at": observation.source_published_at.isoformat(),
            "source_symbols": json.loads(observation.source_symbols_json),
            "board_codes": json.loads(observation.board_codes_json),
        }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert get(store)["candidate_news"][0]["normalized_content_sha256"] == expected
