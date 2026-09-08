"""实验记录本与复盘闭环单测（零网络）：后置裁决/自动回滚/样本保守/闭环回写。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import app.services.experiments as ex
import app.services.evolution as evo
from app.models.agent import AgentExperiment
from app.models.watchlist import Base


def _factory(tmp_path, name="exp.db"):
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

    monkeypatch.setattr(ex, "get_session_factory", lambda: factory)
    monkeypatch.setattr(evo, "get_session_factory", lambda: factory)
    monkeypatch.setattr(ap, "get_session_factory", lambda: factory)
    monkeypatch.setattr(at2, "get_session_factory", lambda: factory)
    yield factory
    from app.picks.style_router import set_override_provider

    set_override_provider(None)


def _seed_experiment(sf, *, baseline_win_rate, due=True, change_id=1) -> int:
    with sf() as db:
        row = AgentExperiment(
            change_id=change_id, param_key="picks_style_offsets_json",
            hypothesis="胜率回升",
            baseline=json.dumps({"status": "ok", "win_rate": baseline_win_rate,
                                 "mean_excess": 1.2, "taken_at": "…"}),
            verification_date=datetime.utcnow() - timedelta(days=1) if due
            else datetime.utcnow() + timedelta(days=10),
            status="running",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _mock_health(monkeypatch, *, status="ok", win_rate=None):
    monkeypatch.setattr(ex, "collect_signal_health",
                        lambda sf=None: {"status": status, "win_rate": win_rate, "mean_excess": 0.5})


def test_due_experiment_improvement_concludes(sf, monkeypatch):
    """改善（胜率上升）→ concluded，不回滚。"""
    _seed_experiment(sf, baseline_win_rate=0.48)
    _mock_health(monkeypatch, win_rate=0.55)
    out = ex.conclude_due(sf)[0]
    assert out["status"] == "concluded"
    assert out["result"]["win_rate_delta"] == pytest.approx(0.07)


def test_due_experiment_degradation_auto_rolls_back(sf, monkeypatch):
    """劣化 ≥2pct → **自动回滚**变更单（后置守护核心场景）。"""
    from app.services.agent_params import propose, apply_change

    change = propose("picks_style_offsets_json", '{"发酵": {"echelon": 0.04}}',
                     session_factory=sf)
    apply_change(change["id"], session_factory=sf)
    _seed_experiment(sf, baseline_win_rate=0.50, change_id=change["id"])
    _mock_health(monkeypatch, win_rate=0.45)  # 劣化 5pct

    out = ex.conclude_due(sf)[0]
    assert out["status"] == "rolled_back"
    assert "自动回滚" in out["result"]["conclusion"]
    # 变更单确实被回滚（覆盖层还原）
    from app.services.agent_params import current_value

    assert current_value("picks_style_offsets_json", session_factory=sf) == ""


def test_insufficient_extends_window_never_fake_verdict(sf, monkeypatch):
    """样本不足 → 延长窗口（不误杀）；超次数上限 → concluded_insufficient（保守不回滚）。"""
    exp_id = _seed_experiment(sf, baseline_win_rate=0.48)
    _mock_health(monkeypatch, status="insufficient")
    out = ex.conclude_due(sf)[0]
    assert out["status"] == "running" and out["extensions"] == 1  # 延长，不裁决

    # 模拟"30 天流逝后再裁决"：第一次 → 延长到上限（extensions=2，仍 running）；
    # 再次到期 → 上限已用尽 → 保守结论 concluded_insufficient（绝不回滚）
    with sf() as db:
        row = db.get(AgentExperiment, exp_id)
        row.verification_date = datetime.utcnow() - timedelta(days=1)
        db.commit()
    results = ex.conclude_due(sf)
    assert results[0]["extensions"] == 2 and results[0]["status"] == "running"
    with sf() as db:
        row = db.get(AgentExperiment, exp_id)
        row.verification_date = datetime.utcnow() - timedelta(days=1)
        db.commit()
    results = ex.conclude_due(sf)
    assert results[0]["status"] == "concluded_insufficient"
    assert "保守" in results[0]["result"]["conclusion"]


def test_not_due_stays_running(sf, monkeypatch):
    exp_id = _seed_experiment(sf, baseline_win_rate=0.48, due=False)
    _mock_health(monkeypatch, win_rate=0.10)
    assert ex.conclude_due(sf) == []  # 未到期不裁决
    assert sf() and sf().__class__  # no-op
    with sf() as db:
        from sqlalchemy import select

        row = db.execute(select(AgentExperiment).where(AgentExperiment.id == exp_id)).scalars().one()
        assert row.status == "running"


def test_baseline_insufficient_never_rolls_back(sf, monkeypatch):
    """生效时基线就不足 → 无论现在如何都保守不回滚。"""
    _seed_experiment(sf, baseline_win_rate=None)
    _mock_health(monkeypatch, win_rate=0.60)
    out = ex.conclude_due(sf)[0]
    assert out["status"] == "concluded_insufficient"


# ---------------------------------------------------------------- 复盘闭环

def test_sync_review_items_marks_applied(sf):
    """执行成功的议程项（带 review_item）→ 复盘改进项状态回写 applied。"""
    from sqlalchemy import select

    from app.review.models import ReviewActionItemRow

    with sf() as db:
        db.add(ReviewActionItemRow(
            id=7, review_id="r1", trade_date="20260908",
            title="补齐梯队断层校验", category="strategy", priority="P1",
            expected_impact="减少追高", evidence="30 日数据",
            target="theme_service.echelon_complete", status="pending",
        ))
        db.commit()

    agenda = {"inputs": {"review": {"available": True, "trade_date": "20260908",
                                    "action_items": [{"id": "7", "title": "补齐梯队断层校验",
                                                      "category": "strategy"}]}}}
    items = [{"class": "A", "status": "executed",
              "result": "变更单 #1 已自动生效",
              "review_item": {"id": "7", "title": "补齐梯队断层校验", "category": "strategy"}}]
    evo._sync_review_items(agenda, items, sf)

    with sf() as db:
        row = db.execute(
            select(ReviewActionItemRow).where(ReviewActionItemRow.id == 7)
        ).scalars().one()
        assert row.status == "applied"
        assert "自动执行" in row.resolution_note


def test_conclude_due_is_sync_callable(sf, monkeypatch):
    """conclude_due 是同步函数（scheduler 每日 tick 直接调用），返回裁决列表。"""
    exp_id = _seed_experiment(sf, baseline_win_rate=0.48)
    _mock_health(monkeypatch, win_rate=0.55)
    out = ex.conclude_due(sf)  # 不用 asyncio.run——同步调用
    assert len(out) == 1 and out[0]["id"] == exp_id and out[0]["status"] == "concluded"
