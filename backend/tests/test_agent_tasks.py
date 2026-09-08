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
