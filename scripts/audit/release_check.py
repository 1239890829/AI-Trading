#!/usr/bin/env python3
"""Read-only pre-merge evidence for this repository; never changes GitHub settings.

Local gates, diff review and authorization remain mandatory (AGENTS.md §6.5).
Exit 1 means blocked, including incomplete API responses. Rerun immediately
before merging with gh pr merge --match-head-commit <expected-head>.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

WORKFLOW = ".github/workflows/ci.yml"
REQUIRED_JOBS = {"backend (pytest + pyflakes)", "frontend (tsc + lint)", "docs (doc-health)"}


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def gh_json(*args: str):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    # Do not echo arbitrary server/error output (or credentials) into evidence.
    require(result.returncode == 0, "GitHub API unavailable: " + args[0])
    return json.loads(result.stdout)


def paged(path: str, key: str | None = None) -> list:
    pages = gh_json("api", "--paginate", "--slurp", path)
    return [item for page in pages for item in (page[key] if key else page)]


def latest_runs(runs: list[dict]) -> list[dict]:
    latest = {}
    for run in runs:
        key = (run["workflow_id"], run["event"])
        if key not in latest or (run["run_number"], run["run_attempt"]) > (
            latest[key]["run_number"], latest[key]["run_attempt"]
        ):
            latest[key] = run
    return list(latest.values())


def validate(snapshot: dict, expected_head: str) -> dict:
    pr, base = snapshot["pr"], snapshot["base"]
    require(pr["state"] == "open" and pr["draft"] is False, "PR is not open and ready")
    require(pr["head"]["sha"] == expected_head, "PR HEAD changed")
    require(pr["head"]["repo"]["full_name"] == snapshot["repo"], "Unexpected head repository")
    require(pr["base"]["ref"] == "master", "Unexpected base branch")
    require(pr["mergeable"] is True, "Mergeability is unknown or conflicting")
    require(pr["mergeable_state"] == "clean", "GitHub reports a blocked or unknown merge state")
    require(snapshot["comparison"]["merge_base_commit"]["sha"] == base["commit"]["sha"],
            "Latest master is not integrated in PR HEAD; integrate and retest")
    # Base is an ancestor of HEAD: the current prospective merge tree is exactly HEAD.
    require(snapshot["review_decision"] in (None, "APPROVED", "REVIEW_REQUIRED"),
            "Changes requested or unknown review decision")
    require(not snapshot["unresolved_threads"], "Unresolved review threads")
    require(snapshot["reviews_complete"], "Review pagination incomplete")
    reviews = {}
    for review in snapshot["reviews"]:
        if review["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            reviews[review["user"]["login"]] = review["state"]
    require("CHANGES_REQUESTED" not in reviews.values(), "Unresolved Request changes")

    runs = latest_runs(snapshot["runs"])
    ci = [run for run in runs if run["path"] == WORKFLOW and run["event"] == "pull_request"]
    require(len(ci) == 1, "Exactly one current PR CI run is required; empty is not green")
    run = ci[0]
    require(pr["number"] in {item["number"] for item in run["pull_requests"]},
            "CI run does not belong to this PR")
    for item in runs:
        require(item["head_sha"] == expected_head, "CI is for a different HEAD")
        require(item["status"] == "completed" and item["conclusion"] == "success",
                "Latest Actions run is pending, skipped, cancelled or failing")
    require(snapshot["jobs_run_id"] == run["id"] and snapshot["jobs_attempt"] == run["run_attempt"],
            "Jobs do not belong to the latest run attempt")
    jobs = snapshot["jobs"]
    require(len(jobs) == len(REQUIRED_JOBS) and {job["name"] for job in jobs} == REQUIRED_JOBS,
            "Required job set is empty, incomplete, duplicated or changed")
    for job in jobs:
        require(job["run_id"] == run["id"] and job["head_sha"] == expected_head,
                "Job identity does not match PR CI")
        require(job["status"] == "completed" and job["conclusion"] == "success",
                "Required job is pending, skipped, cancelled or failing")
    require(snapshot["commit_status"]["sha"] == expected_head, "Commit statuses are for a different HEAD")
    require(snapshot["commit_status"]["total_count"] == len(snapshot["commit_status"]["statuses"]),
            "Commit status pagination incomplete")
    for status in snapshot["commit_status"]["statuses"]:
        require(status["state"] == "success", "A commit status is blocking")
    # Empty commit statuses are normal for Actions-only repositories, but are
    # never evidence of success: the nonempty exact job set above is mandatory.
    return {
        "repository": snapshot["repo"], "pr": pr["number"], "head": expected_head,
        "base": base["commit"]["sha"], "integration_tree": snapshot["head_tree"],
        "run_id": run["id"], "attempt": run["run_attempt"],
        "jobs": [{"id": job["id"], "name": job["name"], "conclusion": job["conclusion"]} for job in jobs],
        "branch_protected": base["protected"],
        "scope": "Actions and commit statuses; client check, not server branch protection",
    }


def collect(repo: str, number: int, head: str) -> dict:
    prefix = f"repos/{repo}"
    pr = gh_json("api", f"{prefix}/pulls/{number}")
    base = gh_json("api", f"{prefix}/branches/master")
    runs = paged(f"{prefix}/actions/runs?head_sha={head}&per_page=100", "workflow_runs")
    ci = [run for run in latest_runs(runs) if run["path"] == WORKFLOW and run["event"] == "pull_request"]
    require(len(ci) == 1, "Current PR CI run missing or ambiguous")
    run = ci[0]
    owner, name = repo.split("/")
    review = gh_json("api", "graphql", "-f", "query=" + """
      query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){
        pullRequest(number:$number){reviewDecision reviewThreads(first:100){
          nodes{isResolved} pageInfo{hasNextPage}}}}}
      """, "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={number}")
    require(not review.get("errors"), "Review query returned errors")
    review = review["data"]["repository"]["pullRequest"]
    return {
        "repo": repo, "pr": pr, "base": base, "runs": runs,
        "comparison": gh_json("api", f"{prefix}/compare/{base['commit']['sha']}...{head}"),
        "head_tree": gh_json("api", f"{prefix}/git/commits/{head}")["tree"]["sha"],
        "jobs_run_id": run["id"], "jobs_attempt": run["run_attempt"],
        "jobs": paged(f"{prefix}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100", "jobs"),
        "commit_status": gh_json("api", f"{prefix}/commits/{head}/status"),
        "reviews": paged(f"{prefix}/pulls/{number}/reviews?per_page=100"),
        "review_decision": review["reviewDecision"],
        "unresolved_threads": any(not t["isResolved"] for t in review["reviewThreads"]["nodes"]),
        "reviews_complete": not review["reviewThreads"]["pageInfo"]["hasNextPage"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr", type=int)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args()
    try:
        require(bool(re.fullmatch(r"[0-9a-f]{40}", args.expected_head)), "A full commit SHA is required")
        repo = gh_json("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]
        first = validate(collect(repo, args.pr, args.expected_head), args.expected_head)
        # Re-read all evidence, including reviews and attempts; a changing base
        # or a rerun invalidates the result instead of silently using old green.
        second = validate(collect(repo, args.pr, args.expected_head), args.expected_head)
        require(first == second, "Release evidence changed while checking; rerun")
        print(json.dumps(second, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
