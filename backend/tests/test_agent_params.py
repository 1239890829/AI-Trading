"""参数配置服务单测（零网络）：白名单/值域钳制/变更单全链/覆盖层注入免重启。"""
from __future__ import annotations

import json

import pytest

import app.services.agent_params as ap
import app.picks.style_router as sr
from app.models.watchlist import Base


def _factory(tmp_path, name="params.db"):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


VALID = '{"发酵": {"echelon": 0.04, "sentiment": 0.03}}'


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    # 审计经 agent_tasks.record_audit 落库——两处 session_factory 都注入 tmp 库，
    # 避免测试写运行中服务的真实 DB（锁冲突 + 污染）
    import app.services.agent_tasks as at2

    monkeypatch.setattr(ap, "get_session_factory", lambda: factory)
    monkeypatch.setattr(at2, "get_session_factory", lambda: factory)
    yield factory
    sr.set_override_provider(None)  # 清理运行时注入，避免污染其他测试


def test_unknown_key_rejected(sf):
    with pytest.raises(ValueError):
        ap.propose("not_a_key", "{}", session_factory=sf)


def test_invalid_value_rejected_never_silent(sf):
    """维度未知/幅度越界/非法 JSON → 422 语义（ValueError），绝不静默收敛。"""
    for bad in ('{"发酵": {"nope": 0.01}}', '{"发酵": {"tech": 0.5}}', "not-json"):
        with pytest.raises(ValueError):
            ap.propose("picks_style_offsets_json", bad, session_factory=sf)
    assert ap.list_changes(session_factory=sf) == []  # 没有落任何变更单


def test_same_value_rejected(sf):
    ap.propose("picks_style_offsets_json", VALID, session_factory=sf)
    change = ap.list_changes(session_factory=sf)[0]
    ap.apply_change(change["id"], session_factory=sf)
    with pytest.raises(ValueError):
        ap.propose("picks_style_offsets_json", VALID, session_factory=sf)


def test_propose_apply_rollback_full_cycle(sf):
    """draft → apply（覆盖层生效+免重启）→ rollback（恢复默认）全链。"""
    change = ap.propose("picks_style_offsets_json", VALID, session_factory=sf,
                        source_type="review_action_item", source_id="ai-42",
                        evidence={"sample_days": 30, "win_rate": 0.55})
    assert change["status"] == "draft"
    assert change["before"] in (None, "")  # 空串=无覆盖用静态默认，_j 规范化为 None

    applied = ap.apply_change(change["id"], session_factory=sf)
    assert applied["status"] == "applied" and applied["applied_at"]

    # 覆盖层已写入 + style_router 运行时生效（免重启）：
    # 同名维度是「替换默认偏移值」语义（route_style offsets.update）
    assert ap.current_value("picks_style_offsets_json", session_factory=sf) == VALID
    offs = sr.route_style("发酵")["offsets"]
    assert offs["echelon"] == pytest.approx(0.04)   # 被覆盖
    assert offs["sentiment"] == pytest.approx(0.03)  # 被覆盖
    assert offs["tech"] == pytest.approx(sr.DEFAULT_ROUTES["发酵"][1]["tech"])  # 未覆盖保持默认

    # 回滚：恢复 before
    rolled = ap.rollback_change(change["id"], session_factory=sf)
    assert rolled["status"] == "rolled_back"
    # 回滚后回到默认（""=无覆盖；rolled["before"] 经 _j 规范化可能为 None，同义）
    assert ap.current_value("picks_style_offsets_json", session_factory=sf) in (rolled["before"], "")


def test_apply_twice_is_idempotent_and_rollback_then_apply_rejected(sf):
    change = ap.propose("picks_style_offsets_json", VALID, session_factory=sf)
    ap.apply_change(change["id"], session_factory=sf)
    again = ap.apply_change(change["id"], session_factory=sf)
    assert again["status"] == "applied"
    ap.rollback_change(change["id"], session_factory=sf)
    with pytest.raises(ValueError):
        ap.apply_change(change["id"], session_factory=sf)


def test_audit_trail_written(sf):
    change = ap.propose("picks_style_offsets_json", VALID, session_factory=sf)
    ap.apply_change(change["id"], session_factory=sf)
    rows = at_audits(sf)
    actions = [r["action"] for r in rows]
    assert "param.propose" in actions and "param.apply" in actions


def at_audits(sf):
    from app.models.agent import AgentAudit
    from sqlalchemy import select

    with sf() as db:
        return [{
            "action": r.action, "target": r.target,
            "before": json.loads(r.before) if r.before else None,
            "after": json.loads(r.after) if r.after else None,
        } for r in db.execute(select(AgentAudit).order_by(AgentAudit.id)).scalars().all()]


