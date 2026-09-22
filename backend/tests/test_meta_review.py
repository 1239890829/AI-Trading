"""元评估周报单测：数据汇总 / 幂等 / LLM 降级不伪装 / 周报文件内容。"""
from __future__ import annotations

import json

import pytest

from app.core.bjtime import BJ_TZ, beijing_now_naive
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
    """本周数据：1 份议程（A executed + B executed + C rejected）、1 实验回滚、若干审计。

    🔴 2026-09-14 修正（[[KB-ENG-56]] 同族）：原用 `datetime.utcnow()` 作 seed 时间，
    而 `_collect_week` 按 `_week_start_bj()`（**北京 naive**）过滤、模型列默认值也是
    `beijing_now_naive`（`app/models/agent.py`）——两侧都 naive，直接比较不报错，
    但**口径差了 8 小时**：每周一 00:00~08:00（北京）之间，UTC naive 仍是上一周周日，
    于是 seed 数据落到本周窗口之外、`agenda_count` 变 0 ⇒ **每周一凌晨必红**。
    改用与被测过滤同源的 `beijing_now_naive()`，并加夹具前提断言让未来失真可自解释。
    """
    items = [
        {"class": "A", "status": "executed", "finding": "f1"},
        {"class": "B", "status": "executed", "finding": "f2"},
        {"class": "C", "status": "rejected", "finding": "f3"},
    ]
    now = beijing_now_naive()
    assert now >= mr._week_start_bj(), (
        "夹具前提：seed 时间必须落在本周窗口内，否则 _collect_week 过滤后为空"
        "（口径须与 _week_start_bj 同为北京 naive）"
    )
    with sf() as db:
        db.add(AgentAgenda(date="2026-09-08", status="executed",
                           items=json.dumps(items), created_at=now))
        db.add(AgentExperiment(change_id=1, param_key="picks_style_offsets_json",
                               hypothesis="h", baseline="{}", status="rolled_back",
                               verification_date=now, created_at=now))
        db.add(AgentAudit(actor="ai", action="code.apply", target="abc:backend/app/x.py"))
        db.add(AgentAudit(actor="ai", action="agenda.generate", target="evolution"))
        db.commit()


def test_week_start_bj_is_monday_midnight_naive(monkeypatch):
    """口径守卫（[[KB-ENG-56]] 定点版）：本周起点 = **北京**周一 0 点、且为 naive。

    为什么单独钉这一条：原 seed 用 `datetime.utcnow()`，在北京周一 00:00~08:00 之间
    UTC 仍是上一周周日 ⇒ 数据被本周窗口滤掉、`agenda_count` 变 0，**每周一凌晨必红**。
    这里用固定时刻（周一 00:30 北京 = 周日 16:30 UTC）定点复现该边界，
    不依赖真实运行日 ⇒ 任何一天跑都会红/都会绿，口径被改坏时立刻暴露。
    """
    from datetime import datetime

    monkeypatch.setattr(mr, "beijing_now", lambda: datetime(2026, 9, 14, 0, 30, tzinfo=BJ_TZ))
    out = mr._week_start_bj()
    assert out == datetime(2026, 9, 14), "周一 00:30 的周起点必须是当天 0 点（不得退回上一周）"
    assert out.tzinfo is None, (
        "必须 naive：agent 域时间列存 naive，若返 aware 则与列值相减抛 TypeError"
        "（aware/naive 混用正是当年「早 8 小时」事故的类型级防线）"
    )


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
        callback = kw.get("usage_callback")
        if callback:
            callback({"input_tokens": 33, "output_tokens": 7})
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
