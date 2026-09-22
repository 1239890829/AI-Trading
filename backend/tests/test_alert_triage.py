"""告警 AI 判读层单测（零网络）：确定性去重 / LLM 判读 / 降级不伪装。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta


import pytest

import app.services.alert_triage as tri
from app.models.agent import AgentTriage
from app.models.alert import AlertEvent, AlertRule
from app.models.watchlist import Base
from app.core.bjtime import beijing_now_naive


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
            # 2026-09-09 时区统一：triggered_at 一律北京 naive（此前 datetime.utcnow 造 UTC
            # → 气泡 6h 时效按北京 cutoff 判定时被误过滤，测试与生产契约同步）
            triggered_at=kw.get("triggered_at") or beijing_now_naive(),
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

    async def fake(ctx, *args, **kwargs):
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

    async def fake(ctx, *args, **kwargs):
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

    async def fake(ctx, *args, **kwargs):
        return None

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    out = asyncio.run(tri.triage_event(_event(sf), sf))
    assert out["verdict"] == "notify"
    assert out["model"] == "llm_fallback"
    assert "不可用" in out["reason"]


def test_triage_is_idempotent(sf, monkeypatch):
    """同一事件只判读一次。"""
    calls = {"n": 0}

    async def fake(ctx, *args, **kwargs):
        calls["n"] += 1
        return ("ignore", "噪音")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    ev = _event(sf)
    asyncio.run(tri.triage_event(ev, sf))
    again = asyncio.run(tri.triage_event(ev, sf))
    assert again["verdict"] == "ignore" and calls["n"] == 1


def test_pending_bubbles_only_notify_unacked(sf, monkeypatch):
    """悬浮球只弹 notify 且未确认的。"""

    async def fake(ctx, *args, **kwargs):
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
    async def fake(ctx, *args, **kwargs):
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

    async def fake(ctx, *args, **kwargs):
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

    async def fake(ctx, *args, **kwargs):
        return ("notify", "测试判读")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    ev = _event(sf)
    assert ev.acknowledged == 0  # 初始未确认
    out = asyncio.run(tri.triage_event(ev, sf))
    assert out["verdict"] == "notify"
    with sf() as db:
        row = db.get(AlertEvent, ev.id)
        assert row.acknowledged == 1, "判读完成后事件必须自动 acknowledged"


# ---------------------------------------------------------------- escalate → 任务中心待办（P1-36）
# 事故/问题：判读层 docstring 承诺「escalate 进任务中心待办」，代码却只落 agent_triage
# 一行——控制台任务中心看不到，"升级"实际等于丢弃（与 P1-38「检查到≠有人知道」同类）。


def test_escalate_registers_task_center_todo(sf, monkeypatch):
    """escalate 必须落任务中心待办（needs_confirm，待人工处置）。"""
    import app.services.agent_tasks as at

    # 审计也落 tmp 库（不污染进程内全局工厂）
    monkeypatch.setattr(at, "get_session_factory", lambda: sf)

    async def fake(ctx, *args, **kwargs):
        return ("escalate", "全市场级风险")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    ev = _event(sf)
    out = asyncio.run(tri.triage_event(ev, sf))
    assert out["verdict"] == "escalate"

    todo = at.get_task(f"{at.ESCALATION_ID_PREFIX}{ev.id}")
    assert todo is not None, "escalate 必须在任务中心生成待办，否则升级即丢弃"
    assert todo["type"] == "escalation" and todo["status"] == "needs_confirm"
    assert "全市场级风险" in todo["params"]["summary"]
    assert todo["params"]["symbol"] == "600000"
    assert "茅台突破" in todo["params"]["rule"]

    # 幂等：重复判读（模拟 worker 重入）不刷出第二条待办
    at.record_escalation(event_id=ev.id, summary="重放", session_factory=sf)
    with sf() as db:
        from app.models.agent import AgentTask

        assert db.query(AgentTask).filter(AgentTask.type == "escalation").count() == 1


def test_non_escalate_registers_no_todo(sf, monkeypatch):
    """notify / ignore 不产生待办——否则任务中心被噪音淹没（与降噪初衷相悖）。"""
    import app.services.agent_tasks as at
    from app.models.agent import AgentTask

    monkeypatch.setattr(at, "get_session_factory", lambda: sf)
    with sf() as db:  # 独立规则，避免被冷却去重抢先判为 ignore
        db.add(AlertRule(id=2, name="炸板率", condition_type="break_rate",
                         scope="all", threshold=0.4, channels='["in_app"]'))
        db.commit()

    async def fake(ctx, *args, **kwargs):
        return ("ignore", "噪音")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    out = asyncio.run(tri.triage_event(_event(sf, rule_id=2), sf))
    assert out["verdict"] == "ignore"
    with sf() as db:
        assert db.query(AgentTask).filter(AgentTask.type == "escalation").count() == 0


def test_escalation_register_failure_does_not_break_triage(sf, monkeypatch):
    """待办登记失败不能拖垮判读落库（增强层失败必须降级，不留半途状态）。"""
    import app.services.agent_tasks as at

    def boom(**_kw):
        raise RuntimeError("db locked")

    monkeypatch.setattr(at, "record_escalation", boom)

    async def fake(ctx, *args, **kwargs):
        return ("escalate", "系统性异常")

    monkeypatch.setattr(tri, "_llm_verdict", fake)
    ev = _event(sf)
    out = asyncio.run(tri.triage_event(ev, sf))
    assert out["verdict"] == "escalate"  # 判读照常落库
    with sf() as db:
        assert db.query(AgentTriage).count() == 1


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


# ---------------------------------------------------------------- 判读队列（漏判回归）

def test_triage_pending_reaches_old_unjudged(sf, monkeypatch):
    """未判读优先：事件量超过窗口上限后，**较早的未判读事件仍会被判读**。

    回归背景（2026-09-10）：原实现取「最近 limit 条」再过滤未判读 → 新事件把
    旧事件永久挤出窗口，实测库内 75 条从未判读（含 falsify 18 条方向证伪）。
    本测试造 30 条未判读 + 1 条已判读，断言**最早那条**（30 条之外）最终被判读。
    """
    judged_ids: list[int] = []

    async def fake_verdict(ctx, *args, **kwargs):
        return ("notify", "测试")

    monkeypatch.setattr(tri, "_llm_verdict", fake_verdict)
    monkeypatch.setattr(tri, "RECENT_LIMIT", 40)  # 放开单轮上限以便一次覆盖

    base = beijing_now_naive() - timedelta(hours=12)
    old = _event(sf, symbol="600001", triggered_at=base)
    for i in range(30):
        _event(sf, symbol=f"6001{i:02d}", triggered_at=base + timedelta(minutes=i + 1))

    out = asyncio.run(tri.triage_pending(limit=50, session_factory=sf))
    judged_ids = [r["event_id"] for r in out]
    assert old.id in judged_ids, "最早的未判读事件必须被判读（不能被新事件挤出窗口）"
    assert len(judged_ids) == 31
    # 幂等：再跑一轮无新增
    assert asyncio.run(tri.triage_pending(limit=50, session_factory=sf)) == []


def test_triage_pending_skips_judged(sf, monkeypatch):
    """已判读事件不重复判读（幂等），且不占用单轮预算。"""

    async def fake_verdict(ctx, *args, **kwargs):
        return ("ignore", "测试")

    monkeypatch.setattr(tri, "_llm_verdict", fake_verdict)
    ev = _event(sf)
    first = asyncio.run(tri.triage_pending(session_factory=sf))
    assert [r["event_id"] for r in first] == [ev.id]
    second = asyncio.run(tri.triage_pending(session_factory=sf))
    assert second == []
    with sf() as db:
        assert db.query(AgentTriage).count() == 1



def _enable_jev(monkeypatch, mode: str, confidence: float = 0.90):
    from app.core.config import settings
    monkeypatch.setattr(settings, "jev_enabled", True)
    monkeypatch.setattr(settings, "jev_alert_triage_mode", mode)
    monkeypatch.setattr(settings, "jev_alert_triage_accept_confidence", confidence)


def test_jev_shadow_never_changes_deepseek_verdict(sf, monkeypatch):
    """shadow 只记分歧：Jev=ignore、DeepSeek=notify 时，用户可见仍是 DeepSeek。"""
    from app.core import jev_client

    _enable_jev(monkeypatch, "shadow")
    jev_client._reset_metrics_for_tests()

    async def fake_jev(_ctx, *args, **kwargs):
        return {"verdict": "ignore", "confidence": 0.98, "model": "jev-test", "latency_ms": 5}

    async def fake_llm(_ctx, *args, **kwargs):
        return ("notify", "DeepSeek 保留提醒")

    monkeypatch.setattr(tri, "_jev_verdict", fake_jev)
    monkeypatch.setattr(tri, "_llm_verdict", fake_llm)
    out = asyncio.run(tri.triage_event(_event(sf), sf))
    assert out["verdict"] == "notify" and out["model"] == "llm"
    cmp = jev_client.metrics_snapshot()["comparisons"]["alert_triage"]
    assert cmp["total"] == 1 and cmp["disagree"] == 1
    assert cmp["avg_confidence"] == 0.98


def test_jev_cascade_high_confidence_skips_deepseek(sf, monkeypatch):
    """cascade 高置信直接消费 Jev，DeepSeek 不应再调用。"""
    _enable_jev(monkeypatch, "cascade", confidence=0.90)

    async def fake_jev(_ctx, *args, **kwargs):
        return {"verdict": "ignore", "confidence": 0.96, "model": "jev-test", "latency_ms": 5}

    async def no_llm(_ctx, *args, **kwargs):
        pytest.fail("high-confidence Jev must skip DeepSeek")

    monkeypatch.setattr(tri, "_jev_verdict", fake_jev)
    monkeypatch.setattr(tri, "_llm_verdict", no_llm)
    out = asyncio.run(tri.triage_event(_event(sf), sf))
    assert out["verdict"] == "ignore" and out["model"] == "jev"
    assert "0.96" in out["reason"]



def test_jev_cascade_low_confidence_escalates_to_deepseek(sf, monkeypatch):
    """阈值未达到时不得为了省额度硬吃 Jev 结论。"""
    from app.core import jev_client

    _enable_jev(monkeypatch, "cascade", confidence=0.90)
    jev_client._reset_metrics_for_tests()

    async def fake_jev(_ctx, *args, **kwargs):
        return {"verdict": "notify", "confidence": 0.61, "model": "jev-test", "latency_ms": 5}

    async def fake_llm(_ctx, *args, **kwargs):
        return ("escalate", "DeepSeek 判断为系统性异常")

    monkeypatch.setattr(tri, "_jev_verdict", fake_jev)
    monkeypatch.setattr(tri, "_llm_verdict", fake_llm)
    out = asyncio.run(tri.triage_event(_event(sf), sf))
    assert out["verdict"] == "escalate" and out["model"] == "llm"
    cmp = jev_client.metrics_snapshot()["comparisons"]["alert_triage"]
    assert cmp["total"] == 1 and cmp["disagree"] == 1


def test_jev_off_does_not_call_jev(sf, monkeypatch):
    _enable_jev(monkeypatch, "off")

    async def no_jev(_ctx, *args, **kwargs):
        pytest.fail("off mode must not call Jev")

    async def fake_llm(_ctx, *args, **kwargs):
        return ("ignore", "DeepSeek")

    monkeypatch.setattr(tri, "_jev_verdict", no_jev)
    monkeypatch.setattr(tri, "_llm_verdict", fake_llm)
    out = asyncio.run(tri.triage_event(_event(sf), sf))
    assert out["model"] == "llm" and out["verdict"] == "ignore"


def test_jev_cascade_unavailable_falls_through_to_deepseek(sf, monkeypatch):
    _enable_jev(monkeypatch, "cascade")

    async def fake_jev(_ctx, *args, **kwargs):
        return None

    async def fake_llm(_ctx, *args, **kwargs):
        return ("notify", "DeepSeek fallback")

    monkeypatch.setattr(tri, "_jev_verdict", fake_jev)
    monkeypatch.setattr(tri, "_llm_verdict", fake_llm)
    out = asyncio.run(tri.triage_event(_event(sf), sf))
    assert out["model"] == "llm" and out["verdict"] == "notify"
