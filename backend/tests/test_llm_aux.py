"""P2-3 层1：pending 事件 LLM 辅助判定单测（零网络，mock chat_completion）。

覆盖：攒批下限 / 命中写 direction(llm_aux) / 中性不写但记 judged / 题材不匹配
不写 / LLM 失败整批不标记（可重试）/ 已 judged 不再入选 / 开关关闭跳过。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.event import EventCard, EventDirection, EventObservation, EventInterpretation
from app.models.agent import AgentResourceUsage  # register budget table before create_all
from app.models.watchlist import Base

import app.events.llm_aux as la
from app.core.bjtime import beijing_now_naive


def _factory(tmp_path, name="llm_aux.db"):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / name}")
    assert AgentResourceUsage.__table__.name in Base.metadata.tables
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _event(sf, title: str, *, sym: str | None = None, age_min: int = 10,
           with_dir0: bool = False) -> EventCard:
    """造事件：默认近 age_min 分钟发布、无方向行、未判过。"""
    with sf() as db:
        ev = EventCard(
            fingerprint=f"fp-{title[:20]}-{age_min}",
            title=title,
            source="东财快讯",
            published_at=beijing_now_naive() - timedelta(minutes=age_min),
            half_life_hours=48,
            source_symbol=sym,
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)
        if with_dir0:
            db.add(EventDirection(event_id=ev.id, target_type="theme", target="无",
                                  direction=0, chain="来源关联待判", matched_by="source"))
            db.commit()
        db.expunge(ev)
        return ev


def _settings(monkeypatch, **kw):
    """把 config 相关字段改到测试值。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "event_llm_aux_enabled", kw.get("enabled", True))
    monkeypatch.setattr(settings, "event_llm_aux_min_batch", kw.get("min_batch", 2))
    monkeypatch.setattr(settings, "event_llm_aux_max_batch", kw.get("max_batch", 12))
    monkeypatch.setattr(settings, "event_llm_aux_age_max_h", kw.get("age_max_h", 5.0))
    # 复用 review LLM 字段指向（测试不走真调用，值随意）
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "review_llm_base_url", "http://x")
    monkeypatch.setattr(settings, "review_llm_api_key", "k")
    monkeypatch.setattr(settings, "review_llm_model", "m")


def _mock_llm(monkeypatch, items=None, *, fail: bool = False):
    """mock chat_completion 与 extract_json_object。"""
    import app.core.llm_client as lc

    if fail:
        from app.core.llm_client import LLMError, LLMFailure

        def _boom(*a, **kw):
            raise LLMError("simulated gateway fail", LLMFailure.GATEWAY_ERROR)

        monkeypatch.setattr(lc, "chat_completion", _boom)
        return

    def _fake(*a, **kw):
        import json

        inputs = json.loads(kw["messages"][-1]["content"])
        rows = [{"chain": "", **item, "event_id": source["event_id"],
                 "evidence": {"field": "title", "quote": source["title"]}}
                for item, source in zip(items or [], inputs)]
        return json.dumps({"items": rows}, ensure_ascii=False)

    monkeypatch.setattr(lc, "chat_completion", _fake)
    monkeypatch.setattr(lc, "extract_json_object",
                        lambda raw: __import__("json").loads(raw))


def test_pending_candidates_filters(tmp_path):
    sf = _factory(tmp_path)
    ev1 = _event(sf, "某公司发布新品", age_min=10)
    ev2 = _event(sf, "行业数据快讯", age_min=300)  # 超龄（5h=300min 边界外）
    # 有 direction!=0 → 排除
    ev3 = _event(sf, "已判定事件", age_min=10)
    with sf() as db:
        db.add(EventDirection(event_id=ev3.id, target_type="theme", target="x",
                              direction=1, matched_by="name"))
        db.commit()
    # llm_judged_at 已设 → 排除
    ev4 = _event(sf, "已判过事件", age_min=10)
    with sf() as db:
        db.get(EventCard, ev4.id).llm_judged_at = beijing_now_naive()
        db.commit()
    ev5 = _event(sf, "来源修订待复核", age_min=10)
    with sf() as db:
        db.get(EventCard, ev5.id).revision_pending_at = beijing_now_naive()
        db.commit()

    cands = la._pending_candidates(sf, theme_names=["半导体概念"], max_batch=12, age_max_h=5.0)
    ids = {c.id for c in cands}
    assert ev1.id in ids
    assert ev2.id not in ids
    assert ev3.id not in ids
    assert ev4.id not in ids
    assert ev5.id not in ids


def test_future_interpretation_is_not_sent_to_llm(tmp_path, monkeypatch):
    """尚未可见的当前解释不能提前进入付费模型批次。"""
    sf = _factory(tmp_path)
    _settings(monkeypatch, min_batch=1)
    ev = _event(sf, "半导体扩产消息待判", age_min=10)
    now = beijing_now_naive()
    with sf() as db:
        obs = EventObservation(
            event_id=ev.id, observation_key="future-version",
            content_hash="future-content", source="东财快讯", title=ev.title,
            received_at=now, available_at=now, change_kind="initial",
        )
        db.add(obs)
        db.flush()
        db.add(EventInterpretation(
            event_id=ev.id, observation_id=obs.id,
            effective_at=now + timedelta(minutes=10), state="active", payload_json="{}",
        ))
        db.commit()

    assert la._pending_candidates(
        sf, theme_names=["半导体概念"], max_batch=12, age_max_h=5.0,
    ) == []
    monkeypatch.setattr(la, "_deepseek_items", lambda *_a, **_k: pytest.fail(
        "future interpretation must not trigger an LLM call"
    ))
    assert la.judge_pending_batch(sf, theme_names=["半导体概念"])["skipped"] is True
    monkeypatch.setattr(la, "beijing_now_naive", lambda: now + timedelta(minutes=11))
    assert [row.id for row in la._pending_candidates(
        sf, theme_names=["半导体概念"], max_batch=12, age_max_h=5.0,
    )] == [ev.id]


def test_theme_match(monkeypatch):
    names = ["半导体概念", "AI应用", "存储芯片", "商业航天"]
    assert la._match_theme("半导体概念", names) == "半导体概念"   # 整名
    assert la._match_theme("半导体", names) == "半导体概念"       # 词干
    assert la._match_theme("存储", names) == "存储芯片"           # 包含兜底
    assert la._match_theme("不存在的题材", names) is None
    assert la._match_theme(None, names) is None


def test_hit_writes_direction_and_marks(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    _settings(monkeypatch)
    _event(sf, "SK海力士美股创新高 存储景气", age_min=10)
    _event(sf, "某芯片公司产能满载", age_min=12)
    _mock_llm(monkeypatch, items=[
        {"direction": 1, "theme": "存储芯片", "chain": "海外映射→存储景气", "reason": "创新高反映景气上行"},
        {"direction": 1, "theme": "半导体", "chain": "", "reason": "产能满载利好"},
    ])
    out = la.judge_pending_batch(sf, theme_names=["半导体概念", "存储芯片"])
    assert out["skipped"] is False
    assert out["candidates"] == 2
    assert out["directions_written"] == 2
    with sf() as db:
        rows = db.query(EventDirection).filter_by(matched_by="llm_aux").all()
        assert len(rows) == 2
        assert all(r.direction == 1 for r in rows)
        assert {r.target for r in rows} == {"存储芯片", "半导体概念"}
        marked = [r for r in db.query(EventCard).all() if r.llm_judged_at is not None]
        assert len(marked) == 2  # 整批都记（含命中的）


def test_neutral_no_write_but_marks(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    _settings(monkeypatch)
    _event(sf, "深股通现身龙虎榜数据", age_min=10)
    _event(sf, "某公司常规澄清公告", age_min=15)
    _mock_llm(monkeypatch, items=[
        {"direction": 0, "theme": None, "chain": "", "reason": "数据罗列中性"},
        {"direction": 0, "theme": None, "chain": "", "reason": "澄清信息量低"},
    ])
    out = la.judge_pending_batch(sf, theme_names=["半导体概念"])
    assert out["skipped"] is False
    assert out["directions_written"] == 0
    with sf() as db:
        assert db.query(EventDirection).filter_by(matched_by="llm_aux").count() == 0
        marked = [r for r in db.query(EventCard).all() if r.llm_judged_at is not None]
        assert len(marked) == 2  # 中性也标记，防下轮重试烧钱


def test_theme_mismatch_no_write(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    _settings(monkeypatch)
    _event(sf, "某事件标题无题材归属", age_min=10)
    _event(sf, "另一条同样模糊", age_min=11)
    _mock_llm(monkeypatch, items=[
        {"direction": 1, "theme": "完全不在目录的题材", "chain": "", "reason": "x"},
        {"direction": 1, "theme": None, "chain": "", "reason": "x"},
    ])
    out = la.judge_pending_batch(sf, theme_names=["半导体概念"])
    assert out["skipped"] is True
    with sf() as db:
        marked = [r for r in db.query(EventCard).all() if r.llm_judged_at is not None]
        assert len(marked) == 0  # Invalid object is runtime unknown, never a neutral success.


def test_llm_failure_no_mark_retryable(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    _settings(monkeypatch)
    _event(sf, "事件A待判", age_min=10)
    _event(sf, "事件B待判", age_min=12)
    _mock_llm(monkeypatch, fail=True)
    out = la.judge_pending_batch(sf, theme_names=["半导体概念"])
    assert out["skipped"] is True
    with sf() as db:
        # 失败 → 不标记 → 下轮可重试
        assert all(r.llm_judged_at is None for r in db.query(EventCard).all())


@pytest.mark.parametrize("changed_state,direction", [
    ("pending", 1), ("active", 1), ("active", 0),
])
def test_stale_model_result_cannot_write_after_revision(
    tmp_path, monkeypatch, changed_state, direction,
):
    """Model response for an older interpretation must not change a corrected event."""
    sf = _factory(tmp_path)
    _settings(monkeypatch, min_batch=1)
    ev = _event(sf, "原报道：半导体产能扩张", age_min=10)

    def fake_deepseek(rows, *args, **kwargs):
        assert [row.id for row in rows] == [ev.id]
        with sf() as db:
            current = db.get(EventCard, ev.id)
            obs = EventObservation(
                event_id=ev.id, observation_key=f"revision-{changed_state}",
                content_hash="new-content", source=current.source,
                title="更正：产能扩张消息不实", received_at=beijing_now_naive(),
                available_at=beijing_now_naive(), change_kind="revision",
            )
            db.add(obs)
            db.flush()
            if changed_state == "pending":
                current.revision_pending_at = beijing_now_naive()
            else:
                # Retaining the old title still creates a new interpretation.
                # Only the version CAS can distinguish this from the model input.
                obs.title = current.title
            db.add(EventInterpretation(
                event_id=ev.id, observation_id=obs.id,
                effective_at=beijing_now_naive(), state=changed_state,
                payload_json="{}",
            ))
            db.commit()
        return [{"event_id":ev.id,"direction": direction, "theme": "半导体概念", "chain":"", "reason": "旧判断", "evidence":{"field":"title","quote":ev.title}}], None

    monkeypatch.setattr(la, "_deepseek_items", fake_deepseek)
    out = la.judge_pending_batch(sf, theme_names=["半导体概念"])
    assert out["directions_written"] == 0
    with sf() as db:
        assert db.query(EventDirection).filter_by(event_id=ev.id).count() == 0
        assert db.get(EventCard, ev.id).llm_judged_at is None


def test_current_version_result_uses_its_observation(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    _settings(monkeypatch, min_batch=1)
    ev = _event(sf, "半导体产业政策发布", age_min=10)
    with sf() as db:
        obs = EventObservation(
            event_id=ev.id, observation_key="current-observation",
            content_hash="current-content", source="东财快讯",
            title=ev.title, received_at=beijing_now_naive(),
            available_at=beijing_now_naive(), change_kind="initial",
        )
        db.add(obs)
        db.flush()
        db.add(EventInterpretation(
            event_id=ev.id, observation_id=obs.id,
            effective_at=beijing_now_naive(), state="active", payload_json="{}",
        ))
        db.commit()
        observation_id = obs.id
    monkeypatch.setattr(la, "_deepseek_items", lambda *_a, **_k: ([
        {"event_id":ev.id,"direction": 1, "theme": "半导体概念", "chain":"", "reason": "政策传导", "evidence":{"field":"title","quote":ev.title}}
    ], None))

    out = la.judge_pending_batch(sf, theme_names=["半导体概念"])
    assert out["directions_written"] == 1
    with sf() as db:
        direction = db.query(EventDirection).filter_by(event_id=ev.id).one()
        assert direction.observation_id == observation_id
        assert db.get(EventCard, ev.id).llm_judged_at is not None
        versions = db.query(EventInterpretation).filter_by(event_id=ev.id).all()
        assert len(versions) == 2
        assert versions[-1].observation_id == observation_id


def test_same_theme_model_hypothesis_preserves_rule_zero_and_records_non_application(
    tmp_path, monkeypatch,
):
    import json

    from app.events.store import EventStore

    sf = _factory(tmp_path)
    _settings(monkeypatch, min_batch=1)
    store = EventStore(sf)
    event, _ = store.add_event({
        "fingerprint": "same-theme-zero", "title": "芯片供给变化信息",
        "source": "测试", "source_item_id": "same-theme-zero",
        "published_at": beijing_now_naive() - timedelta(minutes=10),
        "directions": [{"target_type": "theme", "target": "半导体概念",
                        "direction": 0, "strength": 1, "matched_by": "name",
                        "basis": "原文仅确认半导体关联"}],
    })
    original = store.interpretations_of(event.id)[0]
    original_payload = original.payload_json
    _mock_llm(monkeypatch, [{
        "direction": 1, "theme": "半导体概念", "chain": "测试供给传导",
        "reason": "待验证的供给变化假设",
    }])
    result = la.judge_pending_batch(sf, theme_names=["半导体概念"])
    assert result["directions_written"] == 0 and result["hit_events"] == 0
    current = store.get_event(event.id)
    assert len(current.directions) == 1
    association = current.directions[0]
    assert association.direction == 0 and association.strength == 1
    assert association.matched_by == "name"
    assert association.basis == "原文仅确认半导体关联"
    versions = store.interpretations_of(event.id)
    assert len(versions) == 2 and versions[0].payload_json == original_payload
    assert json.loads(versions[0].payload_json)["directions"][0]["direction"] == 0
    latest_direction = json.loads(versions[-1].payload_json)["directions"][0]
    assert latest_direction["matched_by"] == "name" and latest_direction["direction"] == 0
    assert versions[-1].payload_json == original_payload
    assert association.observation_id == versions[-1].observation_id
    from app.api.routes.events import _serialize
    presented = _serialize(current)
    assert presented["judge_status_label"] == "方向未明"
    assert "LLM 辅助假设未应用" in presented["llm_aux_note"]
    assert "利好假设" in presented["llm_aux_note"]
    assert "未经人工验证" in presented["llm_aux_note"]
    assert presented["interpretation_ref"]["version_id"] == versions[-1].id
    assert presented["llm_judged_at"] is not None
    monkeypatch.setattr(la, "_deepseek_items", lambda *_a, **_k: pytest.fail(
        "a completed auxiliary attempt must not trigger another call"
    ))
    assert la.judge_pending_batch(sf, theme_names=["半导体概念"])["skipped"] is True


def test_model_does_not_rewrite_unversioned_zero_evidence(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    _settings(monkeypatch, min_batch=1)
    _event(sf, "旧事件仅有来源关联", with_dir0=True)
    _mock_llm(monkeypatch, [{"direction": 1, "theme": "无", "reason": "测试假设"}])
    result = la.judge_pending_batch(sf, theme_names=["无"])
    assert result["directions_written"] == 0
    with sf() as db:
        direction = db.query(EventDirection).one()
        assert direction.direction == 0 and direction.matched_by == "source"
        assert db.query(EventInterpretation).count() == 0


@pytest.mark.parametrize("judged_before_review", [False, True])
@pytest.mark.parametrize("review_action", ["adopt", "retain"])
def test_human_neutral_revision_is_not_rejudged_by_llm(
    tmp_path, monkeypatch, judged_before_review, review_action,
):
    """A reviewed neutral interpretation must remain human-owned and cost no new call."""
    from app.events.store import EventStore

    sf = _factory(tmp_path)
    _settings(monkeypatch, min_batch=1)
    store = EventStore(sf)
    first = {
        "fingerprint": "manual-neutral-1", "title": "某产业政策信息待确认",
        "summary": "政策口径尚不明确", "source": "东财快讯",
        "source_item_id": "manual-neutral-1",
        "published_at": beijing_now_naive() - timedelta(minutes=10),
        "directions": [],
    }
    event, _ = store.add_event(first)
    prior_judged_at = None
    if judged_before_review:
        monkeypatch.setattr(la, "_deepseek_items", lambda *_a, **_k: ([
            {"event_id":event.id,"direction": 0, "theme": None, "chain":"", "reason": "证据不足", "evidence":{"field":"title","quote":first["title"]}}
        ], None))
        assert la.judge_pending_batch(sf, theme_names=["半导体概念"])["skipped"] is False
        with sf() as db:
            prior_judged_at = db.get(EventCard, event.id).llm_judged_at
        assert prior_judged_at is not None

    store.add_event({**first, "summary": "更正：原政策传言未经证实"})
    pending_observation = store.observations_of(event.id)[-1]
    store.review_revision(
        event.id, expected_observation_id=pending_observation.id,
        action=review_action, note="核对来源后确认无可采信方向",
        interpretation={
            "source_tier": 3, "category": "policy", "fact_kind": "fact",
            "certainty": "done", "half_life_hours": 48, "directions": [],
        } if review_action == "adopt" else None,
    )
    with sf() as db:
        reviewed = db.query(EventInterpretation).filter_by(event_id=event.id).order_by(
            EventInterpretation.id.desc()
        ).first()
        assert reviewed.state == "active" and reviewed.review_note
        assert db.get(EventCard, event.id).llm_judged_at == prior_judged_at
    monkeypatch.setattr(la, "_deepseek_items", lambda *_a, **_k: pytest.fail(
        "human-reviewed neutral must not trigger an LLM call"
    ))
    assert la._pending_candidates(
        sf, theme_names=["半导体概念"], max_batch=12, age_max_h=5.0,
    ) == []
    assert la.judge_pending_batch(sf, theme_names=["半导体概念"])["skipped"] is True


def test_reviewed_events_do_not_fill_candidate_window(tmp_path):
    sf = _factory(tmp_path)
    for minute in range(1, 5):
        reviewed = _event(sf, f"已人工复核事件{minute}", age_min=minute)
        with sf() as db:
            obs = EventObservation(
                event_id=reviewed.id, observation_key=f"reviewed-{minute}",
                content_hash=f"content-{minute}", source="东财快讯",
                title=reviewed.title, received_at=beijing_now_naive(),
                available_at=beijing_now_naive(), change_kind="initial",
            )
            db.add(obs)
            db.flush()
            db.add(EventInterpretation(
                event_id=reviewed.id, observation_id=obs.id,
                effective_at=beijing_now_naive(), state="active",
                payload_json="{}", review_note="人工确认中性",
            ))
            db.commit()
    eligible = _event(sf, "较早但仍有效的待判事件", age_min=10)

    candidates = la._pending_candidates(
        sf, theme_names=["半导体概念"], max_batch=1, age_max_h=5.0,
    )
    assert [row.id for row in candidates] == [eligible.id]


def test_disabled_skips(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    _settings(monkeypatch, enabled=False)
    _event(sf, "事件A", age_min=10)
    _event(sf, "事件B", age_min=11)
    out = la.judge_pending_batch(sf, theme_names=["半导体概念"])
    assert out["skipped"] is True
    assert "默认关" in out["reason"]


def test_below_min_batch_skips(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    _settings(monkeypatch, min_batch=3)
    _event(sf, "事件A", age_min=10)
    _event(sf, "事件B", age_min=11)
    _mock_llm(monkeypatch, items=[])
    out = la.judge_pending_batch(sf, theme_names=["半导体概念"])
    assert out["skipped"] is True
    assert "攒批下限" in out["reason"]


def test_invalid_direction_and_unbound_evidence_do_not_mark_success(tmp_path, monkeypatch):
    import json
    import app.core.llm_client as client
    sf = _factory(tmp_path)
    _settings(monkeypatch)
    a = _event(sf, '公司签约大单', age_min=1)
    b = _event(sf, '公司发布另一消息', age_min=2)
    monkeypatch.setattr(client, 'chat_completion', lambda *args, **kw: json.dumps({'items':[
        {'event_id':a.id,'direction':2,'theme':'半导体概念','chain':'','reason':'签约','evidence':{'field':'title','quote':'公司签约大单'}},
        {'event_id':b.id,'direction':0,'theme':None,'chain':'','reason':'未知','evidence':{'field':'title','quote':'外部伪引用'}},
    ]},ensure_ascii=False))
    result = la.judge_pending_batch(sf, theme_names=['半导体概念'])
    assert result['skipped'] is True
    with sf() as db:
        assert db.query(EventDirection).filter_by(matched_by='llm_aux').count() == 0
        assert all(r.llm_judged_at is None for r in db.query(EventCard))


@pytest.mark.parametrize('reason,accepted', [('本材料未提供放量依据',True),('不是没有放量',False),('本材料未提供放量依据，但放量上涨已确认',False)])
def test_absent_fact_disclaimer_is_narrow_and_invalid_receipt_is_not_success(tmp_path,monkeypatch,reason,accepted):
    import json
    import app.core.llm_client as client
    sf=_factory(tmp_path)
    _settings(monkeypatch,min_batch=1)
    event=_event(sf,'芯片产能扩张',age_min=1)
    monkeypatch.setattr(client,'chat_completion',lambda *a,**k:json.dumps({'items':[{
        'event_id':event.id,'direction':0,'theme':None,'chain':'','reason':reason,
        'evidence':{'field':'title','quote':'芯片产能扩张'}}]},ensure_ascii=False))
    result=la.judge_pending_batch(sf,theme_names=['半导体概念'])
    assert result['skipped'] is not accepted
    with sf() as db:
        receipt=db.query(AgentResourceUsage).one()
        assert receipt.state == ('succeeded' if accepted else 'failed')
        assert bool(db.get(EventCard,event.id).llm_judged_at) is accepted


def test_price_action_candidates_do_not_invoke_model_but_material_shortage_remains(tmp_path,monkeypatch):
    sf=_factory(tmp_path)
    _settings(monkeypatch)
    _event(sf,'CPO概念震荡反弹 华盛昌涨停',age_min=1)
    _event(sf,'算力链低开高走 创业板算力ETF易方达涨2.30%',age_min=2)
    material=_event(sf,'DDR4现货三个月涨24% 供应短缺',age_min=3)
    monkeypatch.setattr(la,'_deepseek_items',lambda *a,**k:pytest.fail('pure price action cannot create model demand'))
    assert la.judge_pending_batch(sf,theme_names=['半导体概念'])['skipped'] is True
    assert [row.id for row in la._pending_candidates(sf,theme_names=['半导体概念'],max_batch=12,age_max_h=5)] == [material.id]
