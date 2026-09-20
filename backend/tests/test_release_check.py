"""Publish evidence must reject stale, missing and failed CI, not infer green."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_check", ROOT / "scripts/audit/release_check.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
HEAD, BASE = "a" * 40, "b" * 40


def review_comment(*, head=HEAD, pr=23):
    return dict(
        id=91,
        created_at="2026-09-20T00:00:00Z",
        user=dict(login="web-review-account"),
        body=(
            "## Independent web review receipt\n\n"
            "- Stage: `Review` (web-owned; not author self-approval)\n"
            f"- PR: #{pr}\n"
            f"- Reviewed HEAD: `{head}`\n"
            f"**Web Review verdict: `{release.REVIEW_VERDICT}` for HEAD `{head}` only.**\n"
        ),
    )


def snapshot():
    run = dict(id=31, workflow_id=7, run_number=4, run_attempt=2,
               path=release.WORKFLOW, event="pull_request", head_sha=HEAD, pull_requests=[dict(number=23)],
               status="completed", conclusion="success")
    return dict(
        repo="owner/project", pr=dict(number=23, state="open", draft=False, mergeable=True, mergeable_state="clean",
                                      head=dict(sha=HEAD, repo=dict(full_name="owner/project")),
                                      base=dict(ref="master")),
        base=dict(commit=dict(sha=BASE), protected=False), head_tree="c" * 40,
        comparison=dict(merge_base_commit=dict(sha=BASE)), runs=[run],
        jobs_run_id=31, jobs_attempt=2,
        jobs=[dict(id=i, run_id=31, head_sha=HEAD, name=name, status="completed", conclusion="success")
              for i, name in enumerate(sorted(release.REQUIRED_JOBS))],
        review_decision=None, unresolved_threads=False, reviews_complete=True, reviews=[],
        review_comments=[review_comment()],
        commit_status=dict(sha=HEAD, state="pending", total_count=0, statuses=[]),
    )


def test_actions_jobs_are_evidence_even_with_empty_commit_statuses():
    proof = release.validate(snapshot(), HEAD)
    assert proof["head"] == HEAD and proof["base"] == BASE
    assert len(proof["jobs"]) == 3 and proof["branch_protected"] is False


def test_required_jobs_match_the_actual_workflow():
    workflow = yaml.safe_load((ROOT / release.WORKFLOW).read_text())
    assert {job.get("name", key) for key, job in workflow["jobs"].items()} == release.REQUIRED_JOBS


def test_web_review_receipt_is_bound_to_exact_pr_and_head():
    receipt = release.exact_web_review_receipt([review_comment()], 23, HEAD)
    assert receipt and receipt["id"] == 91
    assert release.exact_web_review_receipt([review_comment(head=BASE)], 23, HEAD) is None
    assert release.exact_web_review_receipt([review_comment(pr=24)], 23, HEAD) is None


@pytest.mark.parametrize("case", [
    "empty_runs", "empty_jobs", "missing_job", "duplicate_job", "extra_job", "old_head",
    "old_job_head", "old_job_run", "old_jobs_attempt", "newer_attempt_cancelled", "newer_run_pending",
    "job_skipped", "job_cancelled", "job_pending", "run_failure", "base_advanced", "head_changed",
    "mergeability_unknown", "conflict", "review_changes", "review_thread", "review_pagination",
    "review_rest_changes", "missing_web_review", "old_web_review_head", "wrong_web_review_pr",
    "status_failure", "status_old_head", "draft", "closed", "foreign_repo",
])
def test_bad_or_incomplete_evidence_cannot_release(case):
    s = snapshot()
    if case == "empty_runs": s["runs"] = []
    elif case == "empty_jobs": s["jobs"] = []
    elif case == "missing_job": s["jobs"].pop()
    elif case == "duplicate_job": s["jobs"][0] = deepcopy(s["jobs"][1])
    elif case == "extra_job": s["jobs"].append(dict(s["jobs"][0], name="unreviewed"))
    elif case == "old_head": s["runs"][0]["head_sha"] = BASE
    elif case == "old_job_head": s["jobs"][0]["head_sha"] = BASE
    elif case == "old_job_run": s["jobs"][0]["run_id"] = 30
    elif case == "old_jobs_attempt": s["jobs_attempt"] = 1
    elif case == "newer_attempt_cancelled": s["runs"].append(dict(s["runs"][0], run_attempt=3, conclusion="cancelled"))
    elif case == "newer_run_pending": s["runs"].append(dict(s["runs"][0], id=32, run_number=5, status="queued", conclusion=None))
    elif case == "job_skipped": s["jobs"][0]["conclusion"] = "skipped"
    elif case == "job_cancelled": s["jobs"][0]["conclusion"] = "cancelled"
    elif case == "job_pending": s["jobs"][0]["status"] = "in_progress"
    elif case == "run_failure": s["runs"][0]["conclusion"] = "failure"
    elif case == "base_advanced": s["base"]["commit"]["sha"] = "d" * 40
    elif case == "head_changed": s["pr"]["head"]["sha"] = BASE
    elif case == "mergeability_unknown": s["pr"]["mergeable"] = None
    elif case == "conflict": s["pr"]["mergeable"] = False
    elif case == "review_changes": s["review_decision"] = "CHANGES_REQUESTED"
    elif case == "review_thread": s["unresolved_threads"] = True
    elif case == "review_pagination": s["reviews_complete"] = False
    elif case == "review_rest_changes": s["reviews"] = [dict(user=dict(login="reviewer"), state="CHANGES_REQUESTED")]
    elif case == "missing_web_review": s["review_comments"] = []
    elif case == "old_web_review_head": s["review_comments"] = [review_comment(head=BASE)]
    elif case == "wrong_web_review_pr": s["review_comments"] = [review_comment(pr=24)]
    elif case == "status_failure":
        s["commit_status"]["statuses"] = [dict(state="failure")]
        s["commit_status"]["total_count"] = 1
    elif case == "status_old_head": s["commit_status"]["sha"] = BASE
    elif case == "draft": s["pr"]["draft"] = True
    elif case == "closed": s["pr"]["state"] = "closed"
    elif case == "foreign_repo": s["pr"]["head"]["repo"]["full_name"] = "foreign/project"
    else: pytest.fail(f"unhandled test case: {case}")
    with pytest.raises(ValueError):
        release.validate(s, HEAD)


def test_comment_does_not_dismiss_a_request_for_changes():
    s = snapshot()
    s["reviews"] = [dict(user=dict(login="reviewer"), state=state)
                    for state in ["CHANGES_REQUESTED", "COMMENTED"]]
    with pytest.raises(ValueError, match="Request changes"):
        release.validate(s, HEAD)
    s["reviews"].append(dict(user=dict(login="reviewer"), state="APPROVED"))
    assert release.validate(s, HEAD)["head"] == HEAD


def test_api_failure_is_blocked_without_echoing_credentials(monkeypatch):
    class Result:
        returncode, stdout, stderr = 1, "", "secret-token"
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **kw: Result())
    with pytest.raises(ValueError, match="API unavailable") as error:
        release.gh_json("api", "example")
    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize("case", ["wrong_pr", "unknown_merge_state", "truncated_statuses"])
def test_evidence_identity_and_completeness(case):
    s = snapshot()
    if case == "wrong_pr": s["runs"][0]["pull_requests"] = [dict(number=4)]
    elif case == "unknown_merge_state": s["pr"]["mergeable_state"] = "blocked"
    else: s["commit_status"]["total_count"] = 1
    with pytest.raises(ValueError):
        release.validate(s, HEAD)
