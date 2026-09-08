"""每日进化议程单测（零网络）：预算/红线/频率闸/A 类自动生效/停机开关/解析校验。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

import app.services.evolution as evo
from app.models.agent import AgentAgenda
from app.models.watchlist import Base


def _factory(tmp_path, name="evo.db"):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    import app.services.agent_params as ap
    import app.services.agent_tasks as at2

    monkeypatch.setattr(evo, "get_session_factory", lambda: factory)
    monkeypatch.setattr(ap, "get_session_factory", lambda: factory)
    monkeypatch.setattr(at2, "get_session_factory", lambda: factory)
    monkeypatch.setattr(evo, "_collect_review_improvements",
                        lambda sf2: {"available": False, "note": "测试跳过"})
    monkeypatch.setattr(evo, "_collect_signal_health",
                        lambda sf2: {"available": False, "note": "测试跳过"})
    monkeypatch.setattr(evo, "_collect_triage_stats",
                        lambda sf2: {"available": False, "note": "测试跳过"})
    yield factory
    from app.picks.style_router import set_override_provider

    set_override_provider(None)


VALID = '{"发酵": {"echelon": 0.04}}'

LLM_OK = json.dumps({"items": [
    {"class": "A", "finding": "发酵期 fundamental 偏移拖累胜率",
     "evidence": {"sample_days": 30, "win_rate": 0.41},
     "action": "将发酵期 fundamental 偏移调回 -0.03",
     "param": {"key": "picks_style_offsets_json",
               "after": {"发酵": {"echelon": 0.04, "fundamental": -0.03}}},
     "expected_effect": "胜率回升", "verification": "30 日对比", "priority": 1},
    {"class": "B", "finding": "watcher 阈值偏松",
     "summary": "本周 confirm 事件中 60% 在 10 分钟内证伪", "priority": 2},
]})


def _fake_llm(monkeypatch, payload):
    async def fake(fn):
        return payload

    monkeypatch.setattr(evo, "_llm_call", fake)
    monkeypatch.setattr("app.core.llm_client.chat_completion", lambda *a, **k: payload)


def test_full_cycle_a_class_auto_applied(sf, monkeypatch):
    """A 类议程免人工直接生效：变更单 applied + 覆盖层生效 + 免重启。"""
    _fake_llm(monkeypatch, LLM_OK)

    async def main():
        return await evo.run_evolution_now(sf)

    agenda = asyncio.run(main())
    assert agenda["status"] == "executed"
    statuses = {i["class"]: i["status"] for i in agenda["items"]}
    assert statuses["A"] == "executed"
    assert statuses["B"] == "executed"  # B 类写进化日报
    # 覆盖层真实生效（style_router 免重启读到新偏移）
    from app.picks.style_router import route_style

    assert route_style("发酵")["offsets"]["echelon"] == pytest.approx(0.04)
    # 变更单带证据落库
    rows = ap_rows(sf)
    assert rows[0]["evidence"]["sample_days"] == 30


def ap_rows(sf):
    from sqlalchemy import select

    from app.models.agent import AgentParamChange as C

    with sf() as db:
        return [{
            "key": r.key, "status": r.status,
            "evidence": json.loads(r.evidence) if r.evidence else {},
        } for r in db.execute(select(C)).scalars().all()]


def test_autonomy_off_generates_but_never_executes(sf, monkeypatch):
    """停机开关：议程照常生成，但一项都不执行（降级为建议清单）。"""
    _fake_llm(monkeypatch, LLM_OK)
    monkeypatch.setattr(evo, "autonomy_enabled", lambda: False)

    async def main():
        return await evo.run_evolution_now(sf)

    agenda = asyncio.run(main())
    # autonomy 关闭：议程照常生成（ready），但执行层一项不动（后置由 execute_agenda 的开关拦截）
    assert agenda["status"] == "ready"
    assert all(i["status"] == "pending" for i in agenda["items"])
    assert ap_rows(sf) == []  # 没有任何参数变更
    # 直接调 execute_agenda：开关拦截 → skipped（证明不是靠"没调执行"碰巧安全）
    executed = evo.execute_agenda(agenda, sf)
    assert executed["status"] == "skipped"
    assert executed["error"]["code"] == "AutonomyOff"
    assert ap_rows(sf) == []


def test_redline_param_rejected(sf, monkeypatch):
    """红线参数（风控阈值）被提议 → rejected 并留痕，即使 LLM 输出了。"""
    payload = json.dumps({"items": [
        {"class": "A", "finding": "gap 阈值太严",
         "evidence": {}, "action": "放宽禁买线",
         "param": {"key": "picks_gate_block_gap", "after": 12.0},
         "priority": 1},
    ]})
    _fake_llm(monkeypatch, payload)

    async def main():
        return await evo.run_evolution_now(sf)

    agenda = asyncio.run(main())
    assert agenda["items"][0]["status"] == "rejected"
    assert "红线" in agenda["items"][0]["result"]


def test_frequency_gate_defers_second_change(sf, monkeypatch):
    """同参数 24h 内只允许一次自动变更（第二单 deferred）。"""
    _fake_llm(monkeypatch, LLM_OK)

    async def main():
        first = await evo.run_evolution_now(sf)
        # 手动清掉今日议程，模拟次日同参数再次被提出
        with sf() as db:
            row = db.query(AgentAgenda).filter(AgentAgenda.date == datetime.utcnow().date().isoformat()).one()
            db.delete(row)
            db.commit()
        second = await evo.run_evolution_now(sf)
        return first, second

    first, second = asyncio.run(main())
    assert first["items"][0]["status"] == "executed"
    a_items = [i for i in second["items"] if i["class"] == "A"]
    assert a_items and a_items[0]["status"] == "deferred"
    assert "频率闸" in a_items[0]["result"]


def test_budget_exhausted_blocks_llm(sf, monkeypatch):
    """LLM 预算耗尽 → 议程 skipped（不烧钱），显式原因。"""
    monkeypatch.setattr(evo.settings, "agent_daily_llm_budget", 0)

    async def main():
        return await evo.run_evolution_now(sf)

    agenda = asyncio.run(main())
    assert agenda["status"] == "skipped"
    assert "预算" in agenda["error"]["message"]


def test_parse_items_drops_garbage_and_c_class_deferred(sf, monkeypatch):
    """非法项丢弃；C 类不静默忽略而是 deferred 留痕。"""
    payload = json.dumps({"items": [
        {"class": "A", "finding": "缺 param 字段", "action": "x", "priority": 1},
        {"class": "C", "finding": "补一个边界校验", "action": "改 engine.py", "priority": 2},
        {"class": "B", "finding": "沉淀今日结论", "summary": "……", "priority": 3},
        "not-a-dict",
    ]})
    _fake_llm(monkeypatch, payload)

    async def main():
        return await evo.run_evolution_now(sf)

    agenda = asyncio.run(main())
    by_class = {i["class"]: i for i in agenda["items"]}
    assert by_class["A"]["status"] == "rejected"  # 缺 param/非法值被校验拒绝
    # C 类已接入执行器（P1-⑤）：缺 files → 预检 rejected（不再 deferred）
    assert by_class["C"]["status"] == "rejected" and "目标文件" in by_class["C"]["result"]
    assert by_class["B"]["status"] == "executed"


def test_get_and_list_agendas(sf, monkeypatch):
    _fake_llm(monkeypatch, json.dumps({"items": []}))

    async def main():
        await evo.run_evolution_now(sf)
        return evo.get_agenda(session_factory=sf), evo.list_agendas(session_factory=sf)

    today, rows = asyncio.run(main())
    assert today is not None and rows[0]["date"] == today["date"]


# ---------------------------------------------------------------- 数据健康哨兵（P2-②）

def test_data_health_structure(sf):
    """哨兵输出结构稳定：checks 全带 name/ok/detail，issues 与 not-ok 集合一致。

    不断言具体 ok 值（依赖宿主机文件状态），只锚定契约——语义异常的判断在议程 LLM。
    """
    out = evo._collect_data_health(sf)
    assert out["available"] is True
    names = {c["name"] for c in out["checks"]}
    assert {"trade_calendar", "snapshot_parquet", "marketdb", "alert_pipeline"} <= names
    for c in out["checks"]:
        assert isinstance(c["ok"], bool) and c["detail"]
    not_ok = [c["name"] for c in out["checks"] if not c["ok"]]
    assert out["n_issues"] == len(not_ok)
    # issues 每条 = "<name>：<detail>"，且与 not_ok 集合一一对应
    assert sorted(i.split("：", 1)[0] for i in out["issues"]) == sorted(not_ok)
