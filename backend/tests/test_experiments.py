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


@pytest.fixture(autouse=True)
def _clean_style_override():
    """本文件**每个**用例后都还原 `style_router` 的全局 provider（顺序依赖，2026-09-14）。

    为什么单靠下面的 `sf` fixture 不够：两条 shadow 流程用例
    （`test_shadow_flow_promotes_mild_change` / `..._rejects_dramatic_change`）
    **自建 sf**、不使用该 fixture，因此它们经 `promote_shadow → apply_change →
    refresh_runtime_overrides` 注入的 provider（闭包持有 tmp 库的 sessionmaker）
    会**留在全局**。后果不是"多一份缓存"，而是把 `style_router._load_override`
    短路成 provider 优先 ⇒ 后续 `tests/test_style_router.py` 里所有读
    `settings.picks_style_offsets_json` 的用例全部看不到自己的 monkeypatch。

    实测：`pytest tests/test_experiments.py tests/test_style_router.py` 必红 2 例
    （`test_config_override_merges` / `test_config_invalid_raises`），
    而单独跑 test_style_router 全绿 —— 典型顺序依赖。基线（`dc2cc35`）复现一致，
    与 R12 改动无关，是既有的隔离缺口。
    """
    yield
    from app.picks.style_router import set_override_provider

    set_override_provider(None)


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
    assert out["result"]["rollback"]["runtime_value_restored"] is True
    # 变更单确实被回滚（覆盖层还原）
    from app.services.agent_params import current_value

    assert current_value("picks_style_offsets_json", session_factory=sf) == ""


def test_auto_rollback_of_superseded_change_keeps_newer_value(sf, monkeypatch):
    """R12：实验自动回滚一条**已被取代**的变更单时，只归档、不得覆盖新版本。

    场景：A 生效并挂上 30 日实验 → 之后 B 生效取代 A → A 的实验到期判劣化。
    旧实现会把 A 的 before 写回覆盖层，等于**一次数据裁决顺手撤销了 B**。
    """
    from app.services.agent_params import apply_change, current_value, propose

    a = propose("picks_replace_threshold", "52", session_factory=sf)
    apply_change(a["id"], session_factory=sf)
    _seed_experiment(sf, baseline_win_rate=0.50, change_id=a["id"])
    b = propose("picks_replace_threshold", "58", session_factory=sf)
    apply_change(b["id"], session_factory=sf)

    _mock_health(monkeypatch, win_rate=0.45)          # A 的实验判劣化 → 自动回滚 A
    out = ex.conclude_due(sf)[0]

    assert out["status"] == "rolled_back"
    rb = out["result"]["rollback"]
    assert rb["runtime_value_restored"] is False
    assert rb["skipped_reason"] == "superseded"
    assert rb["active_change_id"] == b["id"], "当前值的拥有者是 B"
    assert current_value("picks_replace_threshold", session_factory=sf) == "58.0", (
        "自动回滚陈旧变更不得把 B 的 58 退回 A 的 before"
    )


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
    """conclude_due 是同步函数（scheduler 经 to_thread 调度），返回裁决列表。"""
    exp_id = _seed_experiment(sf, baseline_win_rate=0.48)
    _mock_health(monkeypatch, win_rate=0.55)
    out = ex.conclude_due(sf)  # 不用 asyncio.run——同步调用
    assert len(out) == 1 and out[0]["id"] == exp_id and out[0]["status"] == "concluded"


# ---------------------------------------------------------------- 影子队列（P1-4）

def _param_change(sf, after, before=None):
    from app.models.agent import AgentParamChange

    with sf() as db:
        row = AgentParamChange(key="picks_style_offsets_json", before=before, after=after,
                               source_type="ai_suggestion", source_id="test",
                               status="draft")
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row.id


def test_shadow_flow_promotes_mild_change(tmp_path, monkeypatch):
    """温和影子偏移：评估转正 → 真正生效 + 30 日实验挂账。"""
    from app.services import agent_params, experiments
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'sh.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(agent_params, "get_session_factory", lambda: sf)
    monkeypatch.setattr(experiments, "get_session_factory", lambda: sf)

    cid = _param_change(sf, after='{"启动": {"sentiment": 0.03}}', before="{}")
    agent_params.shadow_change(cid, sf)
    assert agent_params.list_shadow_changes(sf)[0]["id"] == cid

    results = experiments.evaluate_and_promote_shadow(sf)
    assert results[0]["verdict"] == "promoted", results
    from app.models.agent import AgentParamChange as APC

    with sf() as db:
        assert db.get(APC, cid).status == "applied"
    # 转正自动挂 30 日实验
    assert any(e["change_id"] == cid for e in experiments.list_experiments(session_factory=sf))


def test_shadow_flow_rejects_dramatic_change(tmp_path, monkeypatch):
    """剧变影子偏移（全维度 +0.06）：拒绝归档，不生效。"""
    from app.services import agent_params, experiments
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'sh2.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    monkeypatch.setattr(agent_params, "get_session_factory", lambda: sf)
    monkeypatch.setattr(experiments, "get_session_factory", lambda: sf)

    big = '{"启动": {"sentiment": 0.06, "tech": 0.06, "fundamental": 0.06, "capital": -0.06, "news": 0.06, "echelon": -0.06}, ' \
          '"发酵": {"sentiment": 0.06, "tech": 0.06, "fundamental": 0.06, "capital": -0.06, "news": 0.06, "echelon": -0.06}, ' \
          '"高潮": {"sentiment": 0.06, "tech": 0.06, "fundamental": 0.06, "capital": -0.06, "news": 0.06, "echelon": -0.06}, ' \
          '"分歧": {"sentiment": 0.06, "tech": 0.06, "fundamental": 0.06, "capital": -0.06, "news": 0.06, "echelon": -0.06}, ' \
          '"退潮": {"sentiment": 0.06, "tech": 0.06, "fundamental": 0.06, "capital": -0.06, "news": 0.06, "echelon": -0.06}, ' \
          '"冰点": {"sentiment": 0.06, "tech": 0.06, "fundamental": 0.06, "capital": -0.06, "news": 0.06, "echelon": -0.06}}'
    cid = _param_change(sf, after=big, before="{}")
    agent_params.shadow_change(cid, sf)
    results = experiments.evaluate_and_promote_shadow(sf)
    assert results[0]["verdict"] == "shadow_rejected", results
    from app.models.agent import AgentParamChange as APC

    with sf() as db:
        assert db.get(APC, cid).status == "shadow_rejected"

# 注：本文件原有两条「必须 to_thread」的源码级守卫
# （evaluate_and_promote_shadow / conclude_due）已于 2026-09-11 迁移至
# tests/test_event_loop_no_block.py —— 同类守卫集中一处，新增调用点只改那张表。
