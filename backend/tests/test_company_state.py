"""Public company state is a pure projection, never an implicit task writer."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json

import pytest

from app.events import company_impact as ci
from app.events import company_state as state

NOW = datetime.fromisoformat("2026-10-20T10:00:00+08:00")


@pytest.fixture
def profile():
    return {"symbol": "600001", "company_name": "示例科技股份有限公司", "status": "ready",
            "checked_at": "2026-10-20T08:00:00+08:00", "missing": [], "documents": [
                {"id": "h1", "symbol": "600001", "company_name": "示例科技股份有限公司",
                 "title": "2026年半年度报告", "period": "2026H1",
                 "url": "https://static.cninfo.com.cn/finalpage/2026-08-15/1234567890.PDF",
                 "published_at": "2026-08-15T23:59:59+08:00", "received_at": "2026-10-20T07:55:00+08:00",
                 "sha256": "a" * 64, "identity_quote": "示例科技股份有限公司证券代码600001",
                 "excerpts": [{"kind": "product", "page": 18, "quote": "本公司生产精密连接器，向设备制造商销售。"}]}]}


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()} if root.exists() else {}


def test_missing_store_read_does_not_create_folder_or_state(tmp_path, monkeypatch):
    root = tmp_path / "never-created"
    monkeypatch.setattr(state, "atomic_json", lambda *_: pytest.fail("reader attempted a state write"))
    assert state.read_status("600001", now=NOW, enabled=True, root=root)["state"] == "idle"
    assert state.read_status("600001", now=NOW, enabled=False, root=root)["state"] == "disabled"
    assert state.read_profile("600001", root=root) is None
    assert not root.exists()


@pytest.mark.parametrize("task,expected", [(None, "interrupted"), ({"status": "succeeded"}, "interrupted"),
                                          ({"status": "failed"}, "interrupted"), ({"status": "canceled"}, "canceled"),
                                          (42, "interrupted"), ({}, "interrupted"),
                                          ({"status": "running"}, "generating")])
def test_lost_or_terminal_task_is_only_projected_not_written(profile, tmp_path, monkeypatch, task, expected):
    state.put_profile(profile, root=tmp_path)
    state.write_state("600001", "generating", now=NOW, root=tmp_path, task_id="task-1")
    before = snapshot(tmp_path)
    monkeypatch.setattr(state, "atomic_json", lambda *_: pytest.fail("projection attempted repair"))
    result = state.read_status("600001", now=NOW, enabled=True, root=tmp_path, task_resolver=lambda _: task)
    assert result["state"] == expected
    assert result["facts"][0]["quote"] == profile["documents"][0]["excerpts"][0]["quote"]
    assert "analysis" not in result
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("mode", ["missing_task_id", "resolver_error"])
def test_unresolvable_work_does_not_break_ordinary_read_or_stay_running(tmp_path, mode):
    fields = {} if mode == "missing_task_id" else {"task_id": "task-1"}
    state.write_state("600001", "collecting", now=NOW, root=tmp_path, **fields)
    before = snapshot(tmp_path)
    def unavailable(_):
        raise RuntimeError("task storage unavailable")
    result = state.read_status("600001", now=NOW, enabled=True, root=tmp_path, task_resolver=unavailable)
    assert result["state"] == "interrupted"
    assert snapshot(tmp_path) == before


def test_disabled_projection_keeps_facts_without_touching_task(profile, tmp_path):
    state.put_profile(profile, root=tmp_path)
    state.write_state("600001", "generating", now=NOW, root=tmp_path, task_id="task-1")
    before = snapshot(tmp_path)
    result = state.read_status("600001", now=NOW, enabled=False, root=tmp_path,
                               task_resolver=lambda _: pytest.fail("disabled read resolved task"))
    assert result["state"] == "disabled" and len(result["facts"]) == 1
    assert "旧版独立" in result["reason"]
    assert "完整TradingAgents" in result["reason"]
    assert "后台公司采集与判读已停用" not in result["reason"]
    assert snapshot(tmp_path) == before


def test_history_before_profile_check_cannot_claim_collected_facts(profile, tmp_path):
    state.put_profile(profile, root=tmp_path)
    state.write_state("600001", "idle", now=datetime.fromisoformat("2026-10-20T07:00:00+08:00"), root=tmp_path)
    before = snapshot(tmp_path)
    historical = datetime.fromisoformat("2026-10-20T07:30:00+08:00")
    result = state.read_status("600001", now=NOW, enabled=True, root=tmp_path, response_as_of=historical)
    assert "facts" not in result
    assert result["documents_count"] == 0
    assert snapshot(tmp_path) == before


def test_history_before_state_update_is_explicitly_excluded(profile, tmp_path):
    state.put_profile(profile, root=tmp_path)
    state.write_state("600001", "ready", now=NOW, root=tmp_path)
    result = state.read_status("600001", now=NOW, enabled=True, root=tmp_path,
                               response_as_of=datetime.fromisoformat("2026-10-20T09:00:00+08:00"))
    assert result["state"] == "historical_excluded" and result["documents_count"] == 0
    assert "facts" not in result


def test_model_failure_retains_only_public_facts_and_bounded_missing(profile, tmp_path):
    state.put_profile(profile, root=tmp_path)
    state.write_state("600001", "model_failed", now=NOW, root=tmp_path,
                      missing=["本次片段没有客户与具体产品的对应证据。", None], reason="raw internal failure")
    result = state.read_status("600001", now=NOW, enabled=True, root=tmp_path)
    assert result["state"] == "model_failed" and result["documents_count"] == 1
    assert result["missing"] == ["本次片段没有客户与具体产品的对应证据。"]
    assert result["reason"] != "raw internal failure"
    assert result["facts"] == ci.facts(profile)


@pytest.mark.parametrize("current_state", ["ready", "insufficient", "model_failed", "invalid_response"])
def test_current_profile_coverage_is_visible_without_changing_lifecycle(profile, tmp_path, monkeypatch, current_state):
    coverage = ["reports检索尚有后续页，本次只取前30条。", "财务附注中的真实客户及合同未覆盖。"]
    profile["missing"] = coverage
    state.put_profile(profile, root=tmp_path)
    state.write_state("600001", current_state, now=NOW, root=tmp_path,
                      missing=["模型事件关系证据不够。"], reason="internal detail")
    before = snapshot(tmp_path)
    monkeypatch.setattr(state, "atomic_json", lambda *_: pytest.fail("coverage projection wrote state"))
    result = state.read_status("600001", now=NOW, enabled=True, root=tmp_path)
    assert result["state"] == current_state and result["reason"] == state.REASONS[current_state]
    assert result["missing"] == ["模型事件关系证据不够。", *coverage]
    assert result["updated_at"] == NOW.isoformat()
    assert snapshot(tmp_path) == before


def test_coverage_merge_deduplicates_after_truncation_and_keeps_state_priority(profile, tmp_path):
    long_reason = "状态错误" + "甲" * 600
    duplicate = "本次尚未覆盖季度后续更新。"
    profile["missing"] = ["", duplicate, "  " + duplicate + "  ", long_reason + "不同尾部", None, {"unsafe": "not text"},
                          *[f"来源覆盖限制{index}。" for index in range(20)]]
    state.put_profile(profile, root=tmp_path)
    state.write_state("600001", "model_failed", now=NOW, root=tmp_path,
                      missing=[" \n ", long_reason, duplicate, duplicate, None, 42])
    before = snapshot(tmp_path)
    result = state.read_status("600001", now=NOW, enabled=True, root=tmp_path)
    assert result["missing"] == [long_reason[:500], duplicate,
                                 *[f"来源覆盖限制{index}。" for index in range(10)]]
    assert len(result["missing"]) == 12 and all(len(x) <= 500 for x in result["missing"])
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("malformed", [None, "not a list", {"missing": "not a list"}])
def test_coverage_merge_ignores_malformed_containers(profile, tmp_path, malformed):
    profile["missing"] = ["已见来源分页，但未读取后续页。"]
    state.put_profile(profile, root=tmp_path)
    state.write_state("600001", "ready", now=NOW, root=tmp_path, missing=malformed)
    assert state.read_status("600001", now=NOW, enabled=True, root=tmp_path)["missing"] == profile["missing"]
    profile["missing"] = malformed
    state.put_profile(profile, root=tmp_path)
    state.write_state("600001", "model_failed", now=NOW, root=tmp_path, missing=["状态失败原因。"])
    assert state.read_status("600001", now=NOW, enabled=True, root=tmp_path)["missing"] == ["状态失败原因。"]


@pytest.mark.parametrize("mode", ["future-source", "historical-response"])
def test_profile_coverage_cannot_leak_before_its_checked_time(profile, tmp_path, mode):
    profile["missing"] = ["不得回填的来源覆盖限制。"]
    profile["checked_at"] = "2026-10-20T11:00:00+08:00" if mode == "future-source" else "2026-10-20T08:00:00+08:00"
    state.put_profile(profile, root=tmp_path)
    state.write_state("600001", "model_failed", now=datetime.fromisoformat("2026-10-20T07:00:00+08:00"),
                      root=tmp_path, missing=["当时的状态失败原因。"])
    before = snapshot(tmp_path)
    cutoff = datetime.fromisoformat("2026-10-20T07:30:00+08:00") if mode == "historical-response" else None
    result = state.read_status("600001", now=NOW, enabled=True, root=tmp_path, response_as_of=cutoff)
    assert result["state"] == "model_failed" and result["missing"] == ["当时的状态失败原因。"]
    assert result["documents_count"] == 0 and "facts" not in result
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("change", ["content", "foreign", "pointer", "symlink", "future"])
def test_bad_or_future_profile_falls_back_without_hiding_state(profile, tmp_path, change):
    if change == "future":
        profile["checked_at"] = "2026-10-20T11:00:00+08:00"
    key = state.put_profile(profile, root=tmp_path)
    state.write_state("600001", "model_failed", now=NOW, root=tmp_path)
    folder = tmp_path / "600001"
    if change == "content":
        value = deepcopy(profile); value["documents"][0]["excerpts"][0]["quote"] = "改写的经营事实"
        (folder / f"profile-{key}.json").write_text(json.dumps(value))
    if change == "foreign":
        value = deepcopy(profile); value["symbol"] = "600002"
        foreign_key = ci.digest(value)
        (folder / f"profile-{foreign_key}.json").write_text(json.dumps(value))
        (folder / "profile.json").write_text(json.dumps({"key": foreign_key}))
    if change == "pointer":
        (folder / "profile.json").write_text(json.dumps({"key": "../foreign"}))
    if change == "symlink":
        obj = folder / f"profile-{key}.json"; replacement = folder / "recovery.json"
        obj.rename(replacement); obj.symlink_to(replacement)
    result = state.read_status("600001", now=NOW, enabled=True, root=tmp_path)
    assert result["state"] == "model_failed" and result["documents_count"] == 0
    assert "facts" not in result


def test_document_identity_quote_must_bind_exact_issuer_not_just_nonempty_text(profile, tmp_path):
    profile["documents"][0]["identity_quote"] = "其他科技股份有限公司证券代码600002"
    with pytest.raises(ValueError):
        state.put_profile(profile, root=tmp_path)


def test_profile_writer_respects_the_same_publication_lease(profile, tmp_path):
    state.put_profile(profile, root=tmp_path)
    original = deepcopy(profile)
    profile["documents"][0]["excerpts"][0]["quote"] = "本公司主要产品更新为另一类精密部件。"
    with ci.publication_lock(tmp_path / "600001"):
        with pytest.raises(FileExistsError):
            state.put_profile(profile, root=tmp_path)
    assert state.read_profile("600001", root=tmp_path) == original


def test_write_state_rejects_naive_clock_before_persisting(tmp_path):
    with pytest.raises(ValueError):
        state.write_state("600001", "queued", now=NOW.replace(tzinfo=None), root=tmp_path)
    assert snapshot(tmp_path) == {}
