"""IMP-052: independent approval binding and atomic shadow promotion."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import pytest

import app.services.agent_params as ap
from app.core.bjtime import beijing_now_naive
from app.models.watchlist import Base


def _factory(tmp_path, name="imp052-promotion.db"):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    import app.services.agent_tasks as at
    import app.services.experiments as ex

    monkeypatch.setattr(ap, "get_session_factory", lambda: factory)
    monkeypatch.setattr(at, "get_session_factory", lambda: factory)
    monkeypatch.setattr(ap, "REPO_ROOT", tmp_path)
    (tmp_path / "docs" / "review").mkdir(parents=True)
    monkeypatch.setattr(ex, "capture_experiment_baseline", lambda _sf=None: {
        "status": "ok", "win_rate": 0.50, "mean_excess": 0.01,
        "taken_at": "2026-09-22T17:00:00",
    })
    yield factory
    from app.core import runtime_params
    import app.picks.style_router as sr

    runtime_params.clear()
    sr.set_override_provider(None)


def _shadow_candidate(sf, monkeypatch, *, after=58, source_id="agenda-1"):
    from app.services import experiments, shadow_eval

    change = ap.propose(
        "picks_min_pick_score", after,
        source_type="ai_suggestion", source_id=source_id,
        evidence={"finding": "fixture"}, session_factory=sf,
    )
    ap.shadow_change(change["id"], sf)
    monkeypatch.setattr(shadow_eval, "evaluate_shadow", lambda *a, **k: {
        "verdict": "supports",
        "note": "fixture exploratory support",
        "metrics": {"mean_excess": 0.01},
        "sample_days": 30,
        "caveat": "fixture only",
    })
    result = experiments.evaluate_and_promote_shadow(sf)
    assert result and result[0]["recorded"] is True
    review = ap.promotion_review(change["id"], sf)
    assert review["approvable"] is True and review["blockers"] == []
    return change, review


def _write_effect_evidence(suffix: str) -> tuple[str, str]:
    path = ap.REPO_ROOT / "docs" / "review" / f"imp052-effect-{suffix}.json"
    payload = json.dumps({"fixture": suffix, "effect_verified": True}, sort_keys=True).encode()
    path.write_bytes(payload)
    return f"repo://docs/review/{path.name}", hashlib.sha256(payload).hexdigest()


def _approve(sf, review, *, suffix="a", expires=None):
    ref, digest = _write_effect_evidence(suffix)
    return ap.approve_shadow_promotion(
        review["change_id"],
        expected_candidate_digest=review["candidate_digest"],
        expected_baseline_digest=review["baseline_digest"],
        expected_shadow_evidence_digest=review["shadow_evidence_digest"],
        effect_evidence_ref=ref,
        effect_evidence_sha256=digest,
        expires_at=expires or (beijing_now_naive() + timedelta(hours=1)),
        note=f"review {suffix}",
        session_factory=sf,
    )


def test_review_package_binds_candidate_baseline_and_shadow_evidence(sf, monkeypatch):
    change, review = _shadow_candidate(sf, monkeypatch)
    assert review["change_id"] == change["id"]
    assert review["current_baseline"] == ""
    assert all(len(review[key]) == 64 for key in (
        "candidate_digest", "baseline_digest", "shadow_evidence_digest",
    ))
    assert review["shadow_verdict"]["effect_verified"] is False
    assert review["shadow_verdict"]["review_required"] is True


def test_approval_requires_exact_review_package_digests(sf, monkeypatch):
    _change, review = _shadow_candidate(sf, monkeypatch)
    ref, digest = _write_effect_evidence("digest-mismatch")
    with pytest.raises(ValueError, match="candidate_digest 已变化"):
        ap.approve_shadow_promotion(
            review["change_id"],
            expected_candidate_digest="0" * 64,
            expected_baseline_digest=review["baseline_digest"],
            expected_shadow_evidence_digest=review["shadow_evidence_digest"],
            effect_evidence_ref=ref,
            effect_evidence_sha256=digest,
            expires_at=beijing_now_naive() + timedelta(hours=1),
            session_factory=sf,
        )
    assert ap.list_promotion_approvals(review["change_id"], sf) == []


def test_approval_requires_real_effect_artifact_identity_not_boolean(sf, monkeypatch):
    change, review = _shadow_candidate(sf, monkeypatch)
    from app.models.agent import AgentParamChange

    # Candidate-produced flags remain candidate evidence; they cannot create an approval row.
    with sf() as db:
        row = db.get(AgentParamChange, change["id"])
        evidence = json.loads(row.evidence)
        evidence["approved"] = True
        evidence["reviewer"] = "user"
        row.evidence = json.dumps(evidence, ensure_ascii=False)
        db.commit()
    with pytest.raises(ValueError, match="approval_id"):
        ap.promote_shadow(change["id"], sf)
    assert ap.current_value("picks_min_pick_score", sf) == ""

    refreshed = ap.promotion_review(change["id"], sf)
    with pytest.raises(ValueError, match="64 位 SHA-256"):
        ap.approve_shadow_promotion(
            change["id"],
            expected_candidate_digest=refreshed["candidate_digest"],
            expected_baseline_digest=refreshed["baseline_digest"],
            expected_shadow_evidence_digest=refreshed["shadow_evidence_digest"],
            effect_evidence_ref="repo://docs/review/no-digest.json",
            effect_evidence_sha256="approved",
            expires_at=beijing_now_naive() + timedelta(hours=1),
            session_factory=sf,
        )


def test_exact_human_approval_is_one_shot_and_promotion_changes_runtime(sf, monkeypatch):
    change, review = _shadow_candidate(sf, monkeypatch)
    approval = _approve(sf, review)
    assert approval["state"] == "approved"
    assert approval["reviewer"] == "operator" and approval["approval_source"] == "promotion_token"

    out = ap.promote_shadow(change["id"], sf, approval_id=approval["id"], mutation_source="user")
    assert out["status"] == "applied"
    assert out["promotion_approval"]["state"] == "consumed"
    assert out["post_guard_experiment_id"]
    assert ap.current_value("picks_min_pick_score", sf) == "58.0"
    from app.models.agent import AgentExperiment
    with sf() as db:
        exp = db.get(AgentExperiment, out["post_guard_experiment_id"])
        baseline = json.loads(exp.baseline)
        assert exp.change_id == change["id"] and exp.status == "running"
        assert baseline["promotion_approval_id"] == approval["id"]
        assert baseline["effect_evidence_sha256"] == approval["effect_evidence_sha256"]

    # Retrying the exact consumed approval is idempotent: no second write/consume.
    again = ap.promote_shadow(change["id"], sf, approval_id=approval["id"], mutation_source="user")
    assert again["promotion_already_applied"] is True
    assert again["promotion_approval"]["id"] == approval["id"]
    assert ap.current_value("picks_min_pick_score", sf) == "58.0"
    with sf() as db:
        assert db.query(AgentExperiment).filter(AgentExperiment.change_id == change["id"]).count() == 1


def test_candidate_change_after_approval_fails_without_consuming_approval(sf, monkeypatch):
    change, review = _shadow_candidate(sf, monkeypatch)
    approval = _approve(sf, review)
    from app.models.agent import AgentParamChange

    with sf() as db:
        db.get(AgentParamChange, change["id"]).after = "60.0"
        db.commit()
    with pytest.raises(ValueError, match="候选身份"):
        ap.promote_shadow(change["id"], sf, approval_id=approval["id"])
    assert ap.current_value("picks_min_pick_score", sf) == ""
    assert ap.list_promotion_approvals(change["id"], sf)[0]["state"] == "approved"


def test_shadow_evidence_change_after_approval_fails_without_partial_apply(sf, monkeypatch):
    change, review = _shadow_candidate(sf, monkeypatch)
    approval = _approve(sf, review)
    from app.models.agent import AgentParamChange

    with sf() as db:
        row = db.get(AgentParamChange, change["id"])
        evidence = json.loads(row.evidence)
        evidence["shadow_verdict"]["metrics"]["mean_excess"] = 999
        row.evidence = json.dumps(evidence, ensure_ascii=False)
        db.commit()
    with pytest.raises(ValueError, match="影子证据"):
        ap.promote_shadow(change["id"], sf, approval_id=approval["id"])
    assert ap.current_value("picks_min_pick_score", sf) == ""
    assert ap.list_promotion_approvals(change["id"], sf)[0]["consumed_at"] is None


def test_live_baseline_change_after_approval_invalidates_it(sf, monkeypatch):
    change, review = _shadow_candidate(sf, monkeypatch)
    approval = _approve(sf, review)
    manual = ap.propose("picks_min_pick_score", 61, source_type="manual", session_factory=sf)
    ap.apply_change(manual["id"], sf)
    assert ap.current_value("picks_min_pick_score", sf) == "61.0"

    with pytest.raises(ValueError, match="基线"):
        ap.promote_shadow(change["id"], sf, approval_id=approval["id"])
    assert ap.current_value("picks_min_pick_score", sf) == "61.0"
    assert ap.list_promotion_approvals(change["id"], sf)[0]["state"] == "approved"


def test_expired_and_revoked_approvals_fail_closed(sf, monkeypatch):
    change, review = _shadow_candidate(sf, monkeypatch)
    expired = _approve(sf, review, suffix="b")
    from app.models.agent import AgentParamPromotionApproval

    with sf() as db:
        db.get(AgentParamPromotionApproval, expired["id"]).expires_at = beijing_now_naive() - timedelta(seconds=1)
        db.commit()
    with pytest.raises(ValueError, match="过期"):
        ap.promote_shadow(change["id"], sf, approval_id=expired["id"])

    fresh = _approve(sf, review, suffix="c")
    revoked = ap.revoke_promotion_approval(fresh["id"], note="review withdrawn", session_factory=sf)
    assert revoked["state"] == "revoked"
    with pytest.raises(ValueError, match="撤销"):
        ap.promote_shadow(change["id"], sf, approval_id=fresh["id"])
    assert ap.current_value("picks_min_pick_score", sf) == ""


def test_two_valid_approvals_cannot_apply_candidate_twice(sf, monkeypatch):
    change, review = _shadow_candidate(sf, monkeypatch)
    a = _approve(sf, review, suffix="d")
    b = _approve(sf, review, suffix="e")
    ap.promote_shadow(change["id"], sf, approval_id=a["id"])
    with pytest.raises(ValueError, match="其它路径"):
        ap.promote_shadow(change["id"], sf, approval_id=b["id"])
    approvals = {row["id"]: row for row in ap.list_promotion_approvals(change["id"], sf)}
    assert approvals[a["id"]]["state"] == "consumed"
    assert approvals[b["id"]]["state"] == "approved"
    assert ap.current_value("picks_min_pick_score", sf) == "58.0"


def test_tampered_approval_digest_fails_closed(sf, monkeypatch):
    change, review = _shadow_candidate(sf, monkeypatch)
    approval = _approve(sf, review, suffix="f")
    from app.models.agent import AgentParamPromotionApproval

    with sf() as db:
        db.get(AgentParamPromotionApproval, approval["id"]).note = "tampered after approval"
        db.commit()
    with pytest.raises(ValueError, match="摘要不一致"):
        ap.promote_shadow(change["id"], sf, approval_id=approval["id"])
    assert ap.current_value("picks_min_pick_score", sf) == ""


def test_real_api_review_approve_promote_roundtrip(sf, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import agent as routes

    change, _review = _shadow_candidate(sf, monkeypatch, after=59)
    from app.core.config import settings
    monkeypatch.setattr(settings, "agent_promotion_token", "p" * 32)
    monkeypatch.setattr(settings, "api_token", "")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_write_token] = lambda: None
    with TestClient(app) as client:
        review_resp = client.get(f"/agent/params/changes/{change['id']}/promotion-review")
        assert review_resp.status_code == 200
        review = review_resp.json()["data"]
        missing = client.post(
            f"/agent/params/changes/{change['id']}/promotion-approval",
            json={
                "candidate_digest": review["candidate_digest"],
                "baseline_digest": review["baseline_digest"],
                "shadow_evidence_digest": review["shadow_evidence_digest"],
                "effect_evidence_ref": _write_effect_evidence("api-missing")[0],
                "effect_evidence_sha256": _write_effect_evidence("api-missing")[1],
                "expires_at": (beijing_now_naive() + timedelta(hours=1)).isoformat(),
                "note": "independent human review fixture",
            },
        )
        assert missing.status_code == 403
        api_ref, api_digest = _write_effect_evidence("api")
        approval_resp = client.post(
            f"/agent/params/changes/{change['id']}/promotion-approval",
            headers={"X-Agent-Promotion-Token": "p" * 32},
            json={
                "candidate_digest": review["candidate_digest"],
                "baseline_digest": review["baseline_digest"],
                "shadow_evidence_digest": review["shadow_evidence_digest"],
                "effect_evidence_ref": api_ref,
                "effect_evidence_sha256": api_digest,
                "expires_at": (beijing_now_naive() + timedelta(hours=1)).isoformat(),
                "note": "independent human review fixture",
            },
        )
        assert approval_resp.status_code == 200, approval_resp.text
        approval = approval_resp.json()["data"]
        promote_resp = client.post(
            f"/agent/params/changes/{change['id']}/promote",
            json={"approval_id": approval["id"]},
        )
        assert promote_resp.status_code == 200, promote_resp.text
        assert promote_resp.json()["data"]["promotion_approval"]["state"] == "consumed"
    assert ap.current_value("picks_min_pick_score", sf) == "59.0"

@pytest.mark.parametrize("mutation", ["candidate", "evidence", "approval"])
def test_promotion_rechecks_identity_after_mutation_task_registration(sf, monkeypatch, mutation):
    """TOCTOU: approval preflight success cannot authorize a later changed object."""
    from app.models.agent import AgentParamChange, AgentParamPromotionApproval, AgentTask
    from app.services import agent_tasks

    change, review = _shadow_candidate(sf, monkeypatch)
    approval = _approve(sf, review, suffix={"candidate": "1", "evidence": "2", "approval": "3"}[mutation])
    real_record = agent_tasks.record_mutation

    def mutate_after_record(**kwargs):
        task_id = real_record(**kwargs)
        with sf() as db:
            if mutation == "candidate":
                db.get(AgentParamChange, change["id"]).after = "60.0"
            elif mutation == "evidence":
                row = db.get(AgentParamChange, change["id"])
                evidence = json.loads(row.evidence)
                evidence["shadow_verdict"]["note"] = "changed after review"
                row.evidence = json.dumps(evidence, ensure_ascii=False)
            else:
                db.get(AgentParamPromotionApproval, approval["id"]).consumed_at = beijing_now_naive()
            db.commit()
        return task_id

    monkeypatch.setattr(agent_tasks, "record_mutation", mutate_after_record)
    with pytest.raises(ValueError):
        ap.promote_shadow(
            change["id"], sf, approval_id=approval["id"], mutation_source="user",
        )
    assert ap.current_value("picks_min_pick_score", sf) == ""
    with sf() as db:
        task = db.query(AgentTask).filter(AgentTask.type == "mutation").one()
        assert task.status == "failed"
        # Candidate/evidence races leave approval unconsumed; an externally consumed approval stays consumed.
        stored = db.get(AgentParamPromotionApproval, approval["id"])
        if mutation != "approval":
            assert stored.consumed_at is None


def test_promotion_rechecks_live_baseline_after_mutation_task_registration(sf, monkeypatch):
    """A concurrent write after human review must not be overwritten by promotion."""
    from app.models.agent import AgentParam, AgentParamPromotionApproval, AgentTask
    from app.services import agent_tasks

    change, review = _shadow_candidate(sf, monkeypatch)
    approval = _approve(sf, review, suffix="4")
    real_record = agent_tasks.record_mutation

    def change_baseline_after_record(**kwargs):
        task_id = real_record(**kwargs)
        with sf() as db:
            db.merge(AgentParam(key="picks_min_pick_score", value="63.0"))
            db.commit()
        return task_id

    monkeypatch.setattr(agent_tasks, "record_mutation", change_baseline_after_record)
    with pytest.raises(ValueError, match="基线"):
        ap.promote_shadow(
            change["id"], sf, approval_id=approval["id"], mutation_source="user",
        )
    assert ap.current_value("picks_min_pick_score", sf) == "63.0"
    with sf() as db:
        assert db.get(AgentParamPromotionApproval, approval["id"]).consumed_at is None
        task = db.query(AgentTask).filter(AgentTask.type == "mutation").one()
        assert task.status == "failed"


@pytest.mark.parametrize("verdict", ["opposes", "neutral", "insufficient", "not_applicable", "unsupported"])
def test_non_supportive_shadow_evidence_cannot_reach_human_approval(sf, monkeypatch, verdict):
    from app.services import experiments, shadow_eval

    change = ap.propose(
        "picks_min_pick_score", 58, source_type="ai_suggestion", source_id=f"agenda-{verdict}",
        evidence={"finding": "fixture"}, session_factory=sf,
    )
    ap.shadow_change(change["id"], sf)
    monkeypatch.setattr(shadow_eval, "evaluate_shadow", lambda *a, **k: {
        "verdict": verdict, "note": "fixture", "metrics": {}, "sample_days": 1, "caveat": None,
    })
    experiments.evaluate_and_promote_shadow(sf)
    review = ap.promotion_review(change["id"], sf)
    assert review["approvable"] is False
    assert any(str(item).startswith("shadow_verdict_not_supportive:") for item in review["blockers"])
    with pytest.raises(ValueError, match="不可批准"):
        _approve(sf, review, suffix="9")
    assert ap.current_value("picks_min_pick_score", sf) == ""


def test_promotion_approval_ttl_is_bounded(sf, monkeypatch):
    _change, review = _shadow_candidate(sf, monkeypatch)
    with pytest.raises(ValueError, match="24 小时"):
        _approve(sf, review, suffix="8", expires=beijing_now_naive() + timedelta(days=2))
    assert ap.list_promotion_approvals(review["change_id"], sf) == []


def test_post_guard_baseline_failure_blocks_promotion_without_consuming_approval(sf, monkeypatch):
    from app.models.agent import AgentExperiment, AgentParamPromotionApproval, AgentTask
    from app.services import experiments

    change, review = _shadow_candidate(sf, monkeypatch)
    approval = _approve(sf, review, suffix="7")
    monkeypatch.setattr(
        experiments, "capture_experiment_baseline",
        lambda _sf=None: (_ for _ in ()).throw(RuntimeError("health unavailable")),
    )
    with pytest.raises(RuntimeError, match="health unavailable"):
        ap.promote_shadow(change["id"], sf, approval_id=approval["id"], mutation_source="user")
    assert ap.current_value("picks_min_pick_score", sf) == ""
    with sf() as db:
        assert db.get(AgentParamPromotionApproval, approval["id"]).consumed_at is None
        assert db.query(AgentExperiment).count() == 0
        task = db.query(AgentTask).filter(AgentTask.type == "mutation").one()
        assert task.status == "failed"


def test_promotion_approval_endpoint_disabled_without_dedicated_token(sf, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import agent as routes
    from app.core.config import settings

    change, review = _shadow_candidate(sf, monkeypatch)
    monkeypatch.setattr(settings, "agent_promotion_token", "")
    monkeypatch.setattr(settings, "api_token", "")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_write_token] = lambda: None
    ref, digest = _write_effect_evidence("disabled")
    payload = {
        "candidate_digest": review["candidate_digest"],
        "baseline_digest": review["baseline_digest"],
        "shadow_evidence_digest": review["shadow_evidence_digest"],
        "effect_evidence_ref": ref,
        "effect_evidence_sha256": digest,
        "expires_at": (beijing_now_naive() + timedelta(hours=1)).isoformat(),
    }
    with TestClient(app) as client:
        resp = client.post(
            f"/agent/params/changes/{change['id']}/promotion-approval", json=payload,
        )
    assert resp.status_code == 503
    assert ap.list_promotion_approvals(change["id"], sf) == []


def test_promotion_approval_token_must_not_reuse_general_api_token(sf, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import agent as routes
    from app.core.config import settings

    change, review = _shadow_candidate(sf, monkeypatch)
    monkeypatch.setattr(settings, "api_token", "s" * 32)
    monkeypatch.setattr(settings, "agent_promotion_token", "s" * 32)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_write_token] = lambda: None
    with TestClient(app) as client:
        resp = client.post(
            f"/agent/params/changes/{change['id']}/promotion-approval",
            headers={"X-Agent-Promotion-Token": "s" * 32},
            json={
                "candidate_digest": review["candidate_digest"],
                "baseline_digest": review["baseline_digest"],
                "shadow_evidence_digest": review["shadow_evidence_digest"],
                "effect_evidence_ref": _write_effect_evidence("same-token")[0],
                "effect_evidence_sha256": _write_effect_evidence("same-token")[1],
                "expires_at": (beijing_now_naive() + timedelta(hours=1)).isoformat(),
            },
        )
    assert resp.status_code == 503
    assert ap.list_promotion_approvals(change["id"], sf) == []


def test_post_commit_runtime_refresh_failure_is_reported_as_real_partial_effect(sf, monkeypatch):
    """Durable apply must not be reported as 'no side effect' if in-process refresh fails."""
    from app.models.agent import AgentExperiment, AgentParamPromotionApproval, AgentTask

    change, review = _shadow_candidate(sf, monkeypatch)
    approval = _approve(sf, review, suffix="6")
    real_refresh = ap.refresh_runtime_overrides
    calls = {"n": 0}

    def fail_once(_sf=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("runtime injection unavailable")
        return real_refresh(_sf)

    monkeypatch.setattr(ap, "refresh_runtime_overrides", fail_once)
    out = ap.promote_shadow(change["id"], sf, approval_id=approval["id"], mutation_source="user")
    assert out["status"] == "applied"
    assert out["runtime_refreshed"] is False and out["restart_required"] is True
    assert out["runtime_refresh_error"] == "RuntimeError"
    assert ap.current_value("picks_min_pick_score", sf) == "58.0"
    with sf() as db:
        assert db.get(AgentParamPromotionApproval, approval["id"]).consumed_at is not None
        assert db.query(AgentExperiment).filter(AgentExperiment.change_id == change["id"]).count() == 1
        task = db.query(AgentTask).filter(AgentTask.type == "mutation").one()
        assert task.status == "failed"
        assert "运行时刷新失败" in json.loads(task.params)["result"]

    # Exact retry is idempotent but retries runtime reconciliation instead of doing nothing.
    retry = ap.promote_shadow(change["id"], sf, approval_id=approval["id"])
    assert retry["promotion_already_applied"] is True
    assert retry["runtime_refreshed"] is True and retry["restart_required"] is False


def test_effect_evidence_file_drift_after_approval_invalidates_promotion(sf, monkeypatch):
    change, review = _shadow_candidate(sf, monkeypatch)
    approval = _approve(sf, review, suffix="drift")
    rel = approval["effect_evidence_ref"].removeprefix("repo://")
    (ap.REPO_ROOT / rel).write_text("changed after approval", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        ap.promote_shadow(change["id"], sf, approval_id=approval["id"])
    assert ap.current_value("picks_min_pick_score", sf) == ""
    assert ap.list_promotion_approvals(change["id"], sf)[0]["state"] == "approved"


def test_effect_evidence_path_traversal_and_missing_file_fail_closed(sf, monkeypatch):
    _change, review = _shadow_candidate(sf, monkeypatch)
    for ref in ("repo://../outside.txt", "repo://docs/review/missing.json", "https://example.com/evidence"):
        with pytest.raises(ValueError):
            ap.approve_shadow_promotion(
                review["change_id"],
                expected_candidate_digest=review["candidate_digest"],
                expected_baseline_digest=review["baseline_digest"],
                expected_shadow_evidence_digest=review["shadow_evidence_digest"],
                effect_evidence_ref=ref, effect_evidence_sha256="a" * 64,
                expires_at=beijing_now_naive() + timedelta(hours=1),
                session_factory=sf,
            )


def test_promotion_approval_token_rejects_weak_config(sf, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import agent as routes
    from app.core.config import settings

    change, review = _shadow_candidate(sf, monkeypatch)
    monkeypatch.setattr(settings, "api_token", "")
    monkeypatch.setattr(settings, "agent_promotion_token", "short")
    ref, digest = _write_effect_evidence("weak-token")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_write_token] = lambda: None
    with TestClient(app) as client:
        resp = client.post(
            f"/agent/params/changes/{change['id']}/promotion-approval",
            headers={"X-Agent-Promotion-Token": "short"},
            json={
                "candidate_digest": review["candidate_digest"],
                "baseline_digest": review["baseline_digest"],
                "shadow_evidence_digest": review["shadow_evidence_digest"],
                "effect_evidence_ref": ref, "effect_evidence_sha256": digest,
                "expires_at": (beijing_now_naive() + timedelta(hours=1)).isoformat(),
            },
        )
    assert resp.status_code == 503
    assert ap.list_promotion_approvals(change["id"], sf) == []


def test_effect_evidence_symlink_cannot_escape_allowed_directory(sf, monkeypatch):
    _change, review = _shadow_candidate(sf, monkeypatch)
    secret = ap.REPO_ROOT / "backend.env"
    secret.write_text("sensitive", encoding="utf-8")
    link = ap.REPO_ROOT / "docs" / "review" / "escape-link"
    link.symlink_to(secret)
    digest = hashlib.sha256(secret.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="真实路径越出"):
        ap.approve_shadow_promotion(
            review["change_id"],
            expected_candidate_digest=review["candidate_digest"],
            expected_baseline_digest=review["baseline_digest"],
            expected_shadow_evidence_digest=review["shadow_evidence_digest"],
            effect_evidence_ref="repo://docs/review/escape-link",
            effect_evidence_sha256=digest,
            expires_at=beijing_now_naive() + timedelta(hours=1),
            session_factory=sf,
        )
