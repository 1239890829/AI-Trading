"""告警 AI 判读层单测（零网络）：确定性去重 / LLM 判读 / 降级不伪装。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

import app.services.alert_triage as tri
from app.models.agent import AgentTriage
from app.models.alert import AlertEvent, AlertRule
from app.models.watchlist import Base


def _factory(tmp_path, name="triage.db"):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _event(sf, rule_id: int = 1, symbol: str = "600000", **kw) -> AlertEvent:
    with sf() as db:
        ev = AlertEvent(
            rule_id=rule_id, symbol=symbol, trigger_value=kw.get("trigger_value", 12.5),
            threshold=kw.get("threshold", 12.0),
            snapshot='{"kind": "price_above", "text": "股价 12.50 突破阈值 12.00", "name": "贵州茅台"}',
            triggered_at=kw.get("triggered_at") or datetime.utcnow(),
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)
        db.expunge(ev)
        return ev


@pytest.fixture()
def sf(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        db.add(AlertRule(id=1, name="茅台突破", condition_type="price_above",
                         scope="symbols", threshold=12.0, channels='["in_app"]'))
        db.commit()
    return factory


def test_llm_verdict_saved(sf, monkeypatch):
    """LLM 判读结果落库（verdict/reason/model=llm）。"""

    async def fake(ctx):
        assert ctx["symbol"] == "600000" and ctx["condition"] == "price_above"
        return ("notify", "持仓相关，需关注")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    ev = _event(sf)
    out = asyncio.run(tri.triage_event(ev, sf))
    assert out["verdict"] == "notify" and out["model"] == "llm"
    assert "持仓" in out["reason"]


def test_cooldown_dedup_ignores(sf, monkeypatch):
    """同规则冷却窗口内已有判读 → ignore（不消耗 LLM）。"""
    called = {"n": 0}

    async def fake(ctx):
        called["n"] += 1
        return ("notify", "x")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    ev1 = _event(sf)
    asyncio.run(tri.triage_event(ev1, sf))
    assert called["n"] == 1
    ev2 = _event(sf)  # 同一规则
    out = asyncio.run(tri.triage_event(ev2, sf))
    assert out["verdict"] == "ignore" and out["model"] == "rules"
    assert called["n"] == 1  # 未再调用 LLM


def test_llm_unavailable_falls_back_and_marks(sf, monkeypatch):
    """LLM 不可用 → 按规则提醒，但 model 标 llm_fallback（不伪装成 AI 判断）。"""

    async def fake(ctx):
        return None

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    out = asyncio.run(tri.triage_event(_event(sf), sf))
    assert out["verdict"] == "notify"
    assert out["model"] == "llm_fallback"
    assert "不可用" in out["reason"]


def test_triage_is_idempotent(sf, monkeypatch):
    """同一事件只判读一次。"""
    calls = {"n": 0}

    async def fake(ctx):
        calls["n"] += 1
        return ("ignore", "噪音")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    ev = _event(sf)
    asyncio.run(tri.triage_event(ev, sf))
    again = asyncio.run(tri.triage_event(ev, sf))
    assert again["verdict"] == "ignore" and calls["n"] == 1


def test_pending_bubbles_only_notify_unacked(sf, monkeypatch):
    """悬浮球只弹 notify 且未确认的。"""

    async def fake(ctx):
        return ("ignore", "噪音")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    asyncio.run(tri.triage_event(_event(sf), sf))
    assert tri.pending_bubbles(session_factory=sf) == []  # ignore 不进气泡

    # 造一条 notify
    with sf() as db:
        db.add(AgentTriage(event_id=_event(sf).id, verdict="notify", reason="风险", model="llm"))
        db.commit()
    bubbles = tri.pending_bubbles(limit=5, session_factory=sf)
    assert len(bubbles) == 1 and bubbles[0]["verdict"] == "notify"
    assert tri.ack_triage(bubbles[0]["id"], sf) is True
    assert tri.pending_bubbles(session_factory=sf) == []


def test_triage_pending_scans_recent(sf, monkeypatch):
    """两条**不同规则**同时刻事件都应判读（同规则的重复会被冷却去重，另有用例）。"""
    async def fake(ctx):
        return ("escalate", "系统性异常")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    with sf() as db:  # 第二条规则
        db.add(AlertRule(id=2, name="炸板率", condition_type="break_rate",
                         scope="all", threshold=0.4, channels='["in_app"]'))
        db.commit()
    _event(sf, rule_id=1)
    _event(sf, rule_id=2, symbol="600001")
    out = asyncio.run(tri.triage_pending(limit=10, session_factory=sf))
    assert len(out) == 2 and {o["verdict"] for o in out} == {"escalate"}


def test_old_event_outside_cooldown_still_judged(sf, monkeypatch):
    """超出冷却窗口的事件不再被去重（避免永久静默）。"""
    calls = {"n": 0}

    async def fake(ctx):
        calls["n"] += 1
        return ("notify", "x")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    old = _event(sf, triggered_at=datetime.utcnow() - timedelta(hours=3))
    asyncio.run(tri.triage_event(old, sf))
    new = _event(sf)
    asyncio.run(tri.triage_event(new, sf))
    assert calls["n"] == 2


def test_triage_auto_acknowledges_event(sf, monkeypatch):
    """2026-09-08 用户指令「触发记录状态不再需要确认」：判读落库时事件自动
    置 acknowledged=1——终态即判读态，无人工确认环节。"""

    async def fake(ctx):
        return ("notify", "测试判读")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    ev = _event(sf)
    assert ev.acknowledged == 0  # 初始未确认
    out = asyncio.run(tri.triage_event(ev, sf))
    assert out["verdict"] == "notify"
    with sf() as db:
        row = db.get(AlertEvent, ev.id)
        assert row.acknowledged == 1, "判读完成后事件必须自动 acknowledged"


def test_push_policy_matrix():
    """推送矩阵契约：CRITICAL/ANOMALY/REPORT 允许进飞书，SILENT 一律不允许。"""
    from app.services.push_policy import PolicyKind, feishu_allowed

    assert feishu_allowed(PolicyKind.CRITICAL) is True
    assert feishu_allowed(PolicyKind.ANOMALY) is True
    assert feishu_allowed(PolicyKind.REPORT) is True
    assert feishu_allowed(PolicyKind.SILENT) is False


def test_anomaly_guard_only_pushes_new():
    """ANOMALY 守卫：同一异常不重复推；恢复后再出现视为新异常。"""
    from app.services.push_policy import AnomalyPushGuard

    g = AnomalyPushGuard()
    assert g.filter_new(["marketdb 停更"]) == ["marketdb 停更"]  # 首推
    assert g.filter_new(["marketdb 停更"]) == []                 # 重复不推
    assert g.filter_new(["marketdb 停更", "盘中零告警"]) == ["盘中零告警"]  # 只推新增
    assert g.filter_new([]) == []                                # 恢复清空
    assert g.filter_new(["marketdb 停更"]) == ["marketdb 停更"]  # 再现=新异常
