"""P2-3 层1：pending 事件 LLM 辅助判定单测（零网络，mock chat_completion）。

覆盖：攒批下限 / 命中写 direction(llm_aux) / 中性不写但记 judged / 题材不匹配
不写 / LLM 失败整批不标记（可重试）/ 已 judged 不再入选 / 开关关闭跳过。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.event import EventCard, EventDirection
from app.models.watchlist import Base

import app.events.llm_aux as la
from app.core.bjtime import beijing_now_naive


def _factory(tmp_path, name="llm_aux.db"):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / name}")
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
    monkeypatch.setattr(settings, "jev_enabled", kw.get("jev_enabled", False))
    monkeypatch.setattr(settings, "jev_event_aux_mode", kw.get("jev_mode", "off"))
    monkeypatch.setattr(
        settings, "jev_event_aux_neutral_max_noul", kw.get("neutral_max_noul", 0.05)
    )
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

        return json.dumps({"items": items or []}, ensure_ascii=False)

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

    cands = la._pending_candidates(sf, theme_names=["半导体概念"], max_batch=12, age_max_h=5.0)
    ids = {c.id for c in cands}
    assert ev1.id in ids
    assert ev2.id not in ids
    assert ev3.id not in ids
    assert ev4.id not in ids


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
    assert out["directions_written"] == 0
    with sf() as db:
        marked = [r for r in db.query(EventCard).all() if r.llm_judged_at is not None]
        assert len(marked) == 2  # 题材不匹配仍标记——已试过，不重试


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



def test_jev_shadow_keeps_full_deepseek_batch_and_records_comparison(tmp_path, monkeypatch):
    from app.core import jev_client

    sf = _factory(tmp_path)
    _settings(monkeypatch, jev_enabled=True, jev_mode="shadow")
    a = _event(sf, "纯数据罗列", age_min=10)
    b = _event(sf, "芯片产业明确催化", age_min=11)
    jev_client._reset_metrics_for_tests()
    monkeypatch.setattr(la, "_jev_actionability", lambda rows, *args, **kwargs: {a.id: 0.03, b.id: 0.94})

    seen = {}

    def fake_deepseek(rows, *args, **kwargs):
        seen["ids"] = [r.id for r in rows]
        return [
            {"direction": 0, "theme": None, "chain": "", "reason": "中性"},
            {"direction": 1, "theme": "半导体概念", "chain": "产业催化", "reason": "明确"},
        ], None

    monkeypatch.setattr(la, "_deepseek_items", fake_deepseek)
    out = la.judge_pending_batch(sf, theme_names=["半导体概念"])

    assert seen["ids"] == [a.id, b.id]
    assert out["jev_mode"] == "shadow"
    assert out["jev_prefiltered_neutral"] == 0
    assert out["deepseek_candidates"] == 2
    cmp = jev_client.metrics_snapshot()["comparisons"]["event_llm_aux"]
    assert cmp["total"] == 2 and cmp["agree"] == 2
    assert cmp["avg_confidence"] is None



def test_jev_cascade_prefilters_only_high_confidence_neutral(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    _settings(monkeypatch, jev_enabled=True, jev_mode="cascade", neutral_max_noul=0.05)
    neutral = _event(sf, "例行数据", age_min=10)
    actionable = _event(sf, "半导体明确政策催化", age_min=11)
    monkeypatch.setattr(
        la, "_jev_actionability",
        lambda rows, *args, **kwargs: {neutral.id: 0.02, actionable.id: 0.91},
    )

    def fake_deepseek(rows, *args, **kwargs):
        assert [r.id for r in rows] == [actionable.id]
        return [
            {"direction": 1, "theme": "半导体概念", "chain": "政策支持", "reason": "明确"}
        ], None

    monkeypatch.setattr(la, "_deepseek_items", fake_deepseek)
    out = la.judge_pending_batch(sf, theme_names=["半导体概念"])

    assert out["jev_prefiltered_neutral"] == 1
    assert out["deepseek_candidates"] == 1
    assert out["directions_written"] == 1
    with sf() as db:
        assert db.query(EventDirection).filter_by(event_id=neutral.id).count() == 0
        assert db.query(EventDirection).filter_by(event_id=actionable.id).count() == 1
        assert all(r.llm_judged_at is not None for r in db.query(EventCard).all())



def test_jev_cascade_all_neutral_skips_deepseek_entirely(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    _settings(monkeypatch, jev_enabled=True, jev_mode="cascade", neutral_max_noul=0.05)
    a = _event(sf, "纯行情数据A", age_min=10)
    b = _event(sf, "纯行情数据B", age_min=11)
    monkeypatch.setattr(la, "_jev_actionability", lambda rows, *args, **kwargs: {a.id: 0.01, b.id: 0.03})
    monkeypatch.setattr(
        la, "_deepseek_items", lambda rows, *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("all-neutral cascade must not call DeepSeek")
        ),
    )

    out = la.judge_pending_batch(sf, theme_names=["半导体概念"])
    assert out["deepseek_candidates"] == 0
    assert out["jev_prefiltered_neutral"] == 2
    assert out["directions_written"] == 0
    with sf() as db:
        assert all(r.llm_judged_at is not None for r in db.query(EventCard).all())


def test_jev_prefilter_does_not_allow_partial_mark_when_deepseek_fails(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    _settings(monkeypatch, jev_enabled=True, jev_mode="cascade", neutral_max_noul=0.05)
    neutral = _event(sf, "例行数据", age_min=10)
    remaining = _event(sf, "待DeepSeek判题材", age_min=11)
    monkeypatch.setattr(
        la, "_jev_actionability", lambda rows, *args, **kwargs: {neutral.id: 0.01, remaining.id: 0.8}
    )
    monkeypatch.setattr(la, "_deepseek_items", lambda rows, *args, **kwargs: (None, "synthetic failure"))

    out = la.judge_pending_batch(sf, theme_names=["半导体概念"])
    assert out["skipped"] is True
    with sf() as db:
        assert all(r.llm_judged_at is None for r in db.query(EventCard).all())
        assert db.query(EventDirection).filter_by(matched_by="llm_aux").count() == 0



def test_jev_actionability_rejects_ambiguous_event_identity_before_network(monkeypatch):
    """未落库/重复 ID 无法安全把 answers 对回事件，必须在 Jev 调用前 fail-closed。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "jev_event_aux_mode", "shadow")
    monkeypatch.setattr(
        __import__("app.core.jev_client", fromlist=["evaluate"]),
        "evaluate",
        lambda *_a, **_k: pytest.fail("ambiguous ids must not call Jev"),
    )
    a = EventCard(fingerprint="a", title="A")
    b = EventCard(fingerprint="b", title="B")
    assert a.id is None and b.id is None
    assert la._jev_actionability([a, b]) is None
