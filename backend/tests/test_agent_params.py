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




# ---------------------------------------------------------------- 白名单扩充 + 归因 + 存活率（P1-15）
# 起因（2026-09-10 实证）：白名单只有 1 个参数 → "没得调"；回滚不记原因 →
# 存活率只是个数字，回答不了"为什么活不下来"。


def test_scalar_params_validated_by_range_and_type(sf):
    """标量参数走通用值域校验：越界/非数/小数给整数 一律拒绝，不静默收敛。"""
    for bad in ("999", "-1"):
        with pytest.raises(ValueError):
            ap.propose("picks_max_swaps_per_day", bad, session_factory=sf)
    with pytest.raises(ValueError):
        ap.propose("picks_max_swaps_per_day", "1.5", session_factory=sf)
    with pytest.raises(ValueError):
        ap.propose("picks_max_swaps_per_day", "abc", session_factory=sf)
    with pytest.raises(ValueError):
        ap.propose("picks_min_pick_score", "101", session_factory=sf)
    # 合法值规范化后落库（`_dump` 会把 JSON 文本解析回来：整数 → int）
    c = ap.propose("picks_max_swaps_per_day", "1", session_factory=sf)
    assert c["after"] == 1


def test_apply_injects_scalar_and_rollback_removes_it(sf, monkeypatch):
    """生效 → 注入 runtime_params 覆盖层（免重启）；回滚 → 覆盖移除、回落代码常量。"""
    import app.core.runtime_params as rp

    rp.clear()
    monkeypatch.setattr(ap, "get_session_factory", lambda: sf)
    c = ap.propose("picks_max_swaps_per_day", "1", session_factory=sf)
    ap.apply_change(c["id"], sf)
    assert rp.get("picks_max_swaps_per_day", 2) == 1
    ap.rollback_change(c["id"], session_factory=sf, reason_code="manual")
    # 回滚后覆盖层不含该 key ⇒ 业务侧回落代码常量（不需要在消费点写回退逻辑）
    assert rp.get("picks_max_swaps_per_day", 2) == 2
    rp.clear()


def test_rollback_requires_valid_reason_code(sf):
    """归因是**封闭集合**：非法 code 直接拒绝，不静默落 other（垃圾桶式归因等于没归因）。"""
    c = ap.propose("picks_replace_threshold", "12", session_factory=sf)
    ap.apply_change(c["id"], sf)
    with pytest.raises(ValueError):
        ap.rollback_change(c["id"], session_factory=sf, reason_code="whatever")
    rolled = ap.rollback_change(
        c["id"], session_factory=sf, reason_code="superseded", note="被更激进的版本取代"
    )
    assert rolled["rollback_reason"] == {"code": "superseded", "note": "被更激进的版本取代"}


def test_survival_stats_counts_and_insufficient_flag(sf):
    """存活率分母只算**已裁决**（applied + rolled_back）；样本不足时明说不可用。"""
    a = ap.propose("picks_replace_threshold", "12", session_factory=sf)
    ap.apply_change(a["id"], sf)
    b = ap.propose("picks_min_pick_score", "55", session_factory=sf)
    ap.apply_change(b["id"], sf)
    ap.rollback_change(b["id"], session_factory=sf, reason_code="degraded")

    st = ap.survival_stats(sf)
    assert st["applied"] == 1 and st["rolled_back"] == 1 and st["decided"] == 2
    assert st["survival_rate"] == 0.5
    assert st["rollback_reasons"] == {"degraded": 1}
    assert st["insufficient"] is True  # 2 < MIN_SURVIVAL_SAMPLES
    assert "样本不足" in st["note"]
    assert st["by_key"]["picks_min_pick_score"]["rolled_back"] == 1


def test_survival_still_effective_excludes_superseded(sf):
    """**假存活**要排除：状态仍是 applied，但值已被后来者覆盖 ⇒ 算 superseded。

    只看 status 会把"被取代"的也算成活下来，存活率因此虚高。
    """
    a = ap.propose("picks_replace_threshold", "12", session_factory=sf)
    ap.apply_change(a["id"], sf)
    b = ap.propose("picks_replace_threshold", "18", session_factory=sf)
    ap.apply_change(b["id"], sf)

    st = ap.survival_stats(sf)
    assert st["applied"] == 2 and st["decided"] == 2
    assert st["still_effective"] == 1 and st["superseded"] == 1
    assert st["survival_rate"] == 1.0  # 都没被回滚——所以"存活率"必须配 superseded 一起看


def test_survival_empty_db_returns_none_rate(sf):
    """无已裁决变更 → survival_rate=None（不编造 0，也不编造 1）。"""
    st = ap.survival_stats(sf)
    assert st["decided"] == 0 and st["survival_rate"] is None and st["insufficient"] is True


def test_survival_labels_legacy_rows_as_unspecified(sf):
    """早于归因功能上线（2026-09-10）的回滚行 → 分桶 `unspecified` 且**有中文标签**。

    `unspecified` 不是可提交的 code（不在 ROLLBACK_REASONS 里），只是统计分桶——
    但缺标签会让界面直接显示英文 code（"unspecified×1" 没人看得懂）。
    """
    from app.models.agent import AgentParamChange

    with sf() as db:
        db.add(AgentParamChange(key="picks_replace_threshold", before=None, after="12",
                                status="rolled_back", rollback_reason=None))
        db.commit()
    st = ap.survival_stats(sf)
    assert st["rollback_reasons"] == {"unspecified": 1}
    assert "未记录归因" in st["reason_labels"]["unspecified"]
    # 并且它不可作为入参提交（封闭集合只收 ROLLBACK_REASONS）
    assert "unspecified" not in ap.ROLLBACK_REASONS
