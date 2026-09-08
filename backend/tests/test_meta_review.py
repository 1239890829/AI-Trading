"""元评估周报单测：数据汇总 / 幂等 / LLM 降级不伪装 / 周报文件内容。"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.models.agent import AgentAgenda, AgentAudit, AgentExperiment
from app.services import meta_review as mr


@pytest.fixture()
def sf(tmp_path):
    (tmp_path / "meta.db").touch()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.watchlist import Base

    engine = create_engine(f"sqlite:///{tmp_path}/meta.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


def _seed_week_data(sf):
    """本周数据：1 份议程（A executed + B executed + C rejected）、1 实验回滚、若干审计。"""
    items = [
        {"class": "A", "status": "executed", "finding": "f1"},
        {"class": "B", "status": "executed", "finding": "f2"},
        {"class": "C", "status": "rejected", "finding": "f3"},
    ]
    with sf() as db:
        db.add(AgentAgenda(date="2026-09-08", status="executed",
                           items=json.dumps(items), created_at=datetime.utcnow()))
        db.add(AgentExperiment(change_id=1, param_key="picks_style_offsets_json",
                               hypothesis="h", baseline="{}", status="rolled_back",
                               verification_date=datetime.utcnow(), created_at=datetime.utcnow()))
        db.add(AgentAudit(actor="ai", action="code.apply", target="abc:backend/app/x.py"))
        db.add(AgentAudit(actor="ai", action="agenda.generate", target="evolution"))
        db.commit()


def test_collect_week_shape(sf):
    _seed_week_data(sf)
    data = mr._collect_week(sf)
    assert data["agenda_count"] == 1
    assert data["agenda_item_stats"] == {"A:executed": 1, "B:executed": 1, "C:rejected": 1}
    assert data["experiments"][0]["status"] == "rolled_back"
    assert data["audit_stats"].get("code.apply") == 1


def test_generate_meta_review_idempotent(sf, monkeypatch, tmp_path):
    _seed_week_data(sf)
    monkeypatch.setattr(mr, "_EVOLUTION_DIR", tmp_path / "evolution")

    def fake_chat(**kw):
        return json.dumps({"patterns": ["C 类被门禁拦是常态"], "bias": "均衡",
                           "focus": ["补 C 类样本"], "score": {"decision_quality": 3, "safety_discipline": 5}})

    import app.core.llm_client as llm

    monkeypatch.setattr(llm, "chat_completion", lambda **kw: fake_chat(**kw))
    out = mr.generate_meta_review(sf)
    assert out["status"] == "executed" and not out["degraded"]
    path = mr.meta_review_path()
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "元评估周报" in body and "C 类被门禁拦是常态" in body

    # 第二次：幂等跳过
    out2 = mr.generate_meta_review(sf)
    assert out2["status"] == "skipped"


def test_generate_meta_review_llm_down_falls_back_honestly(sf, monkeypatch, tmp_path):
    _seed_week_data(sf)
    monkeypatch.setattr(mr, "_EVOLUTION_DIR", tmp_path / "evolution")

    import app.core.llm_client as llm

    def boom(**kw):
        raise RuntimeError("LLM gateway down")

    monkeypatch.setattr(llm, "chat_completion", boom)
    out = mr.generate_meta_review(sf)
    assert out["status"] == "executed" and out["degraded"]  # 降级显式标注
    body = mr.meta_review_path().read_text(encoding="utf-8")
    assert "LLM 不可用" in body  # 不伪装成 AI 判断
    assert "本周数据" in body  # 数据照存


def test_garbage_llm_output_falls_back(sf, monkeypatch, tmp_path):
    _seed_week_data(sf)
    monkeypatch.setattr(mr, "_EVOLUTION_DIR", tmp_path / "evolution")

    import app.core.llm_client as llm

    monkeypatch.setattr(llm, "chat_completion", lambda **kw: "我觉得本周干得不错！")  # 非 JSON
    out = mr.generate_meta_review(sf)
    assert out["degraded"]
