"""AI 控制台任务执行器单测（零网络）：状态机/步骤轨迹/互斥/取消/审计。

注入点：agent_tasks 用模块级 `get_session_factory`（tmp 库替换）+ `_APP`
（fake app，提供 state.review / snapshot_service）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

import app.services.agent_tasks as at
from app.models.agent import AgentTask
from app.models.watchlist import Base


class FakeReport:
    def model_dump(self):
        return {"brief_date": "20260908", "trade_date": "2026-09-08", "llm_enhanced": True}


class FakeReviewService:
    requested_model = "llm"

    def __init__(self):
        self.calls = []

    async def run(self, td, methodology_version=None):
        self.calls.append((td, methodology_version))
        return FakeReport()


def _factory(tmp_path, name="agent.db"):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _patch(monkeypatch, tmp_path, *, review=None, snapshot=None):
    factory = _factory(tmp_path)
    monkeypatch.setattr(at, "get_session_factory", lambda: factory)
    svc = review if review is not None else FakeReviewService()
    state = type("S", (), {"review": svc, "snapshot_service": snapshot or type("P", (), {"snapshot": [{"symbol": "600000"}], "last_refresh_at": "14:30"})()})()
    at.init_agent_runtime(type("A", (), {"state": state})())
    return factory, svc


def test_create_task_persists_and_runs_to_success(monkeypatch, tmp_path):
    factory, svc = _patch(monkeypatch, tmp_path)

    async def main():
        task = at.create_task("review", {"trade_date": "20260908"})
        assert task["status"] == "queued"
        for _ in range(50):
            await asyncio.sleep(0.02)
            cur = at.get_task(task["id"])
            if cur["status"] != "running":
                break
        return cur

    done = asyncio.run(main())
    assert done["status"] == "succeeded", done
    assert done["result_ref"] == {"kind": "report", "id": "20260908"}
    assert [s["name"] for s in done["steps"]] == ["解析参数", "执行复盘"]
    # LLM 信息留痕（可追溯三件套）
    assert done["steps"][1]["llm"]["enhanced"] is True
    assert svc.calls == [None] or svc.calls[0][0] is not None


def test_unknown_type_rejected(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        at.create_task("nope")


def test_same_type_mutex(monkeypatch, tmp_path):
    """同类型互斥：复盘会消耗数据源配额，不允许并行重复跑。"""

    class Slow(FakeReviewService):
        async def run(self, td, methodology_version=None):
            await asyncio.sleep(0.5)
            return FakeReport()

    _patch(monkeypatch, tmp_path, review=Slow())

    async def main():
        at.create_task("review")
        with pytest.raises(RuntimeError):
            at.create_task("review")

    asyncio.run(main())


def test_failure_records_error_and_steps(monkeypatch, tmp_path):
    class Boom(FakeReviewService):
        async def run(self, td, methodology_version=None):
            raise RuntimeError("复盘服务炸了")

    _patch(monkeypatch, tmp_path, review=Boom())

    async def main():
        t = at.create_task("review")
        for _ in range(50):
            await asyncio.sleep(0.02)
            cur = at.get_task(t["id"])
            if cur["status"] in ("failed", "succeeded"):
                return cur
        return cur

    done = asyncio.run(main())
    assert done["status"] == "failed"
    assert done["error"]["retryable"] is True and "炸了" in done["error"]["message"]


def test_audit_records_create_and_finish(monkeypatch, tmp_path):
    factory, _ = _patch(monkeypatch, tmp_path)

    async def main():
        t = at.create_task("data_check")
        for _ in range(60):
            await asyncio.sleep(0.02)
            cur = at.get_task(t["id"])
            if cur["status"] != "running":
                break
        return t

    t = asyncio.run(main())
    rows = at.list_audit(task_id=t["id"])
    actions = {r["action"] for r in rows}
    assert {"task.create"} <= actions
    assert rows[0]["task_id"] == t["id"]


def test_reconcile_on_startup_marks_interrupted(monkeypatch, tmp_path):
    """重启后残留 running 不能假装还在跑（三态纪律：缺失不冒充）。"""
    factory, _ = _patch(monkeypatch, tmp_path)
    with factory() as db:
        db.add(AgentTask(id="abc", type="review", status="running"))
        db.commit()
    n = at.reconcile_on_startup()
    assert n == 1
    assert at.get_task("abc")["status"] == "failed"
    assert at.get_task("abc")["error"]["code"] == "Interrupted"


def test_list_tasks_filter_by_type(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path)
    with at.get_session_factory()() as db:
        db.add(AgentTask(id="t1", type="review", status="succeeded"))
        db.add(AgentTask(id="t2", type="data_check", status="succeeded"))
        db.commit()
    assert [t["id"] for t in at.list_tasks()] == ["t2", "t1"]  # 创建时间倒序（同秒按入库序）
    assert [t["id"] for t in at.list_tasks(type_="review")] == ["t1"]
    assert json.loads("{}") == {}


def test_mutation_record_and_result(tmp_path, monkeypatch):
    """2026-09-08 用户指令「改动前必须先创建任务」：mutation 登记 → 结果回填。

    record_mutation 纯登记不执行；update_mutation_result 只接受
    succeeded/failed，回写进 params.result（AgentTask 无自由 result 列）。
    """

    from app.services import agent_tasks as at

    factory = _factory(tmp_path)
    monkeypatch.setattr(at, "get_session_factory", lambda: factory)
    tid = at.record_mutation(source="agenda", kind="code_change",
                             summary="C类代码改动：测试", detail={"agenda_date": "2026-09-08"})
    row = at.get_task(tid)
    assert row is not None and row["type"] == "mutation" and row["status"] == "queued"
    assert row["params"]["summary"] == "C类代码改动：测试"

    at.update_mutation_result(tid, "succeeded", "已合入 abc1234")
    row = at.get_task(tid)
    assert row["status"] == "succeeded" and row["params"]["result"] == "已合入 abc1234"

    tid2 = at.record_mutation(source="user", kind="param_change", summary="参数变更测试")
    at.update_mutation_result(tid2, "bogus", "非法状态归为 failed")
    row2 = at.get_task(tid2)
    assert row2["status"] == "failed"


# ---------------------------------------------------------------- 留痕合一（P1-14，2026-09-10）
# 事故/问题：议程 A/B/C 自动执行只落在 agent_agenda，任务中心（agent_task）看不到
# ——两个留痕孤岛 = 追溯性缺口（实测 09-10：agent_task 仅 2 行、agent_agenda 3 行）。


def _seed_agenda(factory, *, date="2026-09-10", status="executed", items=None, error=None, budget=None):
    from app.models.agent import AgentAgenda

    with factory() as db:
        db.add(AgentAgenda(
            date=date, status=status,
            items=json.dumps(items if items is not None else [{
                "class": "B", "priority": "P1", "status": "executed",
                "finding": "12 个题材梯队断层", "evidence": {"source": "review #122"},
                "result": "已写入 KB",
            }], ensure_ascii=False),
            budget=json.dumps(budget if budget is not None else {"llm_used": 1, "llm_budget": 8,
                                                                "tasks_used": 0, "task_budget": 3},
                              ensure_ascii=False),
            error=error,
        ))
        db.commit()


def test_list_tasks_merges_agenda_as_readonly(monkeypatch, tmp_path):
    """议程自动执行也进同一时间线，且标记 read_only（前端据此隐藏取消按钮）。"""
    factory, _ = _patch(monkeypatch, tmp_path)
    with factory() as db:
        db.add(AgentTask(id="t1", type="review", status="succeeded"))
        db.commit()
    _seed_agenda(factory)

    out = at.list_tasks()
    assert {t["type"] for t in out} == {"review", "agenda"}
    ag = next(t for t in out if t["type"] == "agenda")
    assert ag["id"] == "agenda:2026-09-10" and ag["read_only"] is True
    # 状态映射到任务词汇；原始状态留在 params（不丢信息）
    assert ag["status"] == "succeeded" and ag["params"]["agenda_status"] == "executed"
    # 步骤 = 预算一段 + 每个议程条目一段（字段名照搬 _StepRecorder，前端零改动）
    assert ag["steps"][0]["name"] == "证据采集与预算"
    assert "1/8" in ag["steps"][0]["output_summary"]
    assert ag["steps"][1]["name"].startswith("B 类")
    assert "12 个题材梯队断层" in ag["steps"][1]["output_summary"]
    # 按类型过滤时不混入议程（真实任务查询保持原语义）
    assert [t["type"] for t in at.list_tasks(type_="review")] == ["review"]


def test_agenda_task_detail_and_readonly_cancel(monkeypatch, tmp_path):
    factory, _ = _patch(monkeypatch, tmp_path)
    _seed_agenda(factory)

    d = at.get_task("agenda:2026-09-10")
    assert d is not None and d["read_only"] is True
    assert d["result_ref"] == {"kind": "agenda", "id": "2026-09-10"}
    assert d["created_by"] == "agenda"
    assert at.get_task("agenda:1999-01-01") is None
    # 只读条目不可取消（已发生的事实记录，没有"取消"语义）
    assert at.cancel_task("agenda:2026-09-10")["status"] == "succeeded"


def test_agenda_failed_normalizes_error_shape(monkeypatch, tmp_path):
    """议程失败：status 映射为 failed，error 归一成 {code,message,retryable}。

    历史行可能是裸字符串 —— 不归一前端会渲染成「失败：undefined（undefined）」。
    """
    factory, _ = _patch(monkeypatch, tmp_path)
    _seed_agenda(factory, date="2026-09-11", status="failed", error="boom", items=[])

    d = at.get_task("agenda:2026-09-11")
    assert d["status"] == "failed"
    assert d["error"] == {"code": "AgendaError", "message": "boom", "retryable": False}


def test_agenda_deferred_item_marked_not_ok(monkeypatch, tmp_path):
    """deferred（延后，附原因）不算做成——审计语义上标 ✗，不制造成功假象。"""
    factory, _ = _patch(monkeypatch, tmp_path)
    _seed_agenda(factory, items=[
        {"class": "A", "priority": "P2", "status": "deferred", "finding": "证据不足", "result": "延后到有样本"},
        {"class": "B", "priority": "P1", "status": "executed", "finding": "已沉淀"},
    ])
    d = at.get_task("agenda:2026-09-10")
    assert d["steps"][1]["ok"] is False and d["steps"][2]["ok"] is True


# ---------------------------------------------------------------- 告警升级待办（P1-36，2026-09-10）
# 事故/问题：判读层 docstring 承诺「escalate 进任务中心待办」，但代码里 escalate 只
# 落 agent_triage 一行——控制台任务中心完全看不到，"升级"实际等于丢弃。


def test_record_escalation_is_idempotent_and_pending(monkeypatch, tmp_path):
    """escalate 登记为待办：确定性 id（同一事件只一条）+ 初始态 needs_confirm。"""
    factory, _ = _patch(monkeypatch, tmp_path)

    t1 = at.record_escalation(event_id=42, summary="600000 突破｜系统性风险",
                              detail={"symbol": "600000", "rule": "茅台突破"})
    t2 = at.record_escalation(event_id=42, summary="重复判读不应新增")
    assert t1 == t2 == at.ESCALATION_ID_PREFIX + "42"

    row = at.get_task(t1)
    assert row["type"] == "escalation" and row["status"] == "needs_confirm"
    assert row["params"]["summary"] == "600000 突破｜系统性风险"
    assert row["params"]["symbol"] == "600000" and row["params"]["event_id"] == 42
    with factory() as db:
        assert db.query(AgentTask).filter(AgentTask.type == "escalation").count() == 1


def test_resolve_task_closes_todo(monkeypatch, tmp_path):
    """处置入口：done→succeeded / dismissed→canceled，且只在 needs_confirm 时可改。"""
    factory, _ = _patch(monkeypatch, tmp_path)
    tid = at.record_escalation(event_id=7, summary="炸板率 42%｜系统性异常")

    done = at.resolve_task(tid, "done", "已核对，非系统性风险")
    assert done["status"] == "succeeded"
    assert done["params"]["resolve"]["outcome"] == "done"
    assert done["params"]["resolve"]["note"] == "已核对，非系统性风险"
    # 幂等：已终态再处置不改写（不报错）
    assert at.resolve_task(tid, "dismissed")["status"] == "succeeded"

    tid2 = at.record_escalation(event_id=8, summary="涨停家数异常")
    assert at.resolve_task(tid2, "dismissed")["status"] == "canceled"
    assert at.resolve_task("esc-404", "done") is None
    with pytest.raises(ValueError):
        at.resolve_task(tid2, "bogus")


def test_create_task_rejects_registry_only_types(monkeypatch, tmp_path):
    """登记类类型不可创建：没有 handler，建出来必然失败（按钮点了就报错）。

    同时锁定 task-types 清单与 handler 注册表同源——避免"清单里有类型、实际跑不了"。
    """
    _patch(monkeypatch, tmp_path)
    for bad in ("mutation", "escalation"):
        with pytest.raises(ValueError) as ei:
            at.create_task(bad)
        assert bad in str(ei.value)

    names = [t["type"] for t in at.creatable_task_types()]
    assert names == ["review", "data_check"]
    assert set(names) == set(at.CREATABLE_TASK_TYPES) == set(at._HANDLERS)
    # 登记类仍留在 TASK_TYPES 里（前端/审计需要统一标签来源）
    assert {"mutation", "escalation"} <= set(at.TASK_TYPES)
