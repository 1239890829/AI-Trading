"""Automatic research cannot manufacture an author review or bypass pure reads."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.events import company_impact as ci
from app.events import company_state as state
from app.events.company_reasoning import compile_analysis, METHOD, VERIFICATION

NOW = datetime(2026, 10, 20, 2, tzinfo=timezone.utc)
MODEL = {"provider": "fixture", "model": "source-selector"}
USAGE = "a" * 32


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(ci, "datetime", Clock)
    packet = {"id": "automatic-fixture", "schema": ci.SCHEMA, "symbol": "600001",
              "company_name": "测试科技股份有限公司", "short_name": "测试科技",
              "captured_at": "2026-10-20T08:00:00+08:00",
              "event": {"event_id": 10, "version_id": 20, "observation_id": 30,
                        "title": "精密连接器采购需求增长", "summary": "相关行业精密连接器采购需求增加。",
                        "available_at": "2026-10-20T07:30:00+08:00"},
              "documents": [{"id": "h1", "symbol": "600001", "company_name": "测试科技股份有限公司",
                             "title": "半年度报告", "period": "2026H1",
                             "url": "https://static.cninfo.com.cn/finalpage/2026-08-15/1234567890.PDF",
                             "published_at": "2026-08-15T23:59:59+08:00",
                             "received_at": "2026-10-20T07:50:00+08:00", "sha256": "b" * 64,
                             "identity_quote": "测试科技股份有限公司证券代码600001",
                             "excerpts": [{"kind": "product", "page": 18, "quote": "本公司主要产品为精密连接器，向设备制造商销售。"}]}]}
    profile = {"symbol": packet["symbol"], "company_name": packet["company_name"],
               "checked_at": "2026-10-20T07:55:00+08:00", "documents": deepcopy(packet["documents"])}
    state.put_profile(profile, root=tmp_path)
    selected = {"id": packet["id"], "status": "linked", "links": [
        {"fact_id": "h1:0", "phrase": "精密连接器", "anchor": "精密连接器", "channel": "demand"}],
        "missing_codes": ["customer", "order", "exposure"]}
    item = {"event_id": 10, "state": "active", "is_active": True,
            "interpretation_ref": {"event_id": 10, "version_id": 20, "observation_id": 30, "state": "active"},
            "source_context": {"observation_id": 30, "title": packet["event"]["title"], "summary": packet["event"]["summary"]}}
    return packet, selected, profile, item


def publish(prepared, root):
    packet, selected, _, _ = prepared
    return ci.publish_automatic(packet, selected, model=MODEL, usage_id=USAGE,
                                generated_at="2026-10-20T08:30:00+08:00", root=root)


def rewrite(root, key, change):
    folder = root / "600001"
    receipt = json.loads((folder / f"{key}.json").read_text())
    change(receipt)
    replacement = ci.digest(receipt)
    (folder / f"{replacement}.json").write_text(json.dumps(receipt))
    (folder / "10-20-30.current").write_text(replacement)


def test_automatic_result_is_canonical_and_explicitly_not_author_review(prepared, tmp_path, monkeypatch):
    packet, selected, _, item = prepared
    key = publish(prepared, tmp_path)
    receipt = json.loads((tmp_path / "600001" / f"{key}.json").read_text())
    assert receipt["schema"] == ci.AUTO_SCHEMA and "review" not in receipt
    expected, _ = compile_analysis(packet, selected)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(state, "atomic_json", lambda *_: pytest.fail("reader attempted write"))
    monkeypatch.setattr(ci, "publish_automatic", lambda *_a, **_kw: pytest.fail("reader attempted publish"))
    got = ci.read_saved(item, "600001", now=NOW, root=tmp_path)
    assert got["analysis"] == expected and got["analysis"]["status"] == "conditional"
    assert got["verification"] == VERIFICATION and got["generation_method"] == METHOD
    assert got["business_effect_verified"] is False and got["eligibility_changed"] is False
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_auto_publication_preserves_original_author_receipt(prepared, tmp_path):
    packet, selected, _, item = prepared
    analysis, _ = compile_analysis(packet, selected)
    review = {"input_sha256": ci.digest(packet), "output_sha256": ci.digest(analysis),
              "protocol_sha256": ci.digest(ci.messages(packet)), "prompt_sha256": "c" * 64,
              "raw_output_sha256": "d" * 64, "comparison_sha256": "e" * 64,
              "role": "author", "verdict": "PASS_EXPLANATION_ONLY", "independent_gold": False,
              "net_improvement": True, "rationale": "合成测试，只核不同发布身份与原文保全。",
              "reviewed_at": "2026-10-20T08:25:00+08:00"}
    old = ci.publish(packet, analysis, review, model=MODEL, generated_at="2026-10-20T08:20:00+08:00", root=tmp_path)
    path = tmp_path / "600001" / f"{old}.json"
    original = path.read_bytes()
    pointer = tmp_path / "600001" / "10-20-30.current"
    original_pointer = pointer.read_bytes()
    assert ci.read_saved(item, "600001", now=NOW, root=tmp_path)["verification"] == "author_source_reviewed_not_gold"
    with pytest.raises(ValueError, match="author-reviewed"):
        publish(prepared, tmp_path)
    assert path.read_bytes() == original
    assert pointer.read_bytes() == original_pointer
    assert ci.read_saved(item, "600001", now=NOW, root=tmp_path)["verification"] == "author_source_reviewed_not_gold"


@pytest.mark.parametrize("change", ["missing_profile", "different_documents", "future_profile", "foreign_identity"])
def test_auto_publication_requires_the_actual_current_profile(prepared, tmp_path, change):
    packet, _, profile, _ = prepared
    folder = tmp_path / "600001"
    if change == "missing_profile":
        (folder / "profile.json").unlink()
    elif change == "different_documents":
        profile["documents"][0]["excerpts"][0]["quote"] = "本公司主要产品为其他部件。"
        state.put_profile(profile, root=tmp_path)
    elif change == "future_profile":
        profile["checked_at"] = "2026-10-20T11:00:00+08:00"
        state.put_profile(profile, root=tmp_path)
    else:
        packet["documents"][0]["symbol"] = "600002"
    with pytest.raises(ValueError):
        publish(prepared, tmp_path)
    assert not (folder / "10-20-30.current").exists()


@pytest.mark.parametrize("change", ["analysis", "protocol", "usage", "evidence_hash", "rejections", "expiry", "packet_identity"])
def test_rehashed_receipt_tampering_still_fails_canonical_read(prepared, tmp_path, change):
    key = publish(prepared, tmp_path)
    def mutate(value):
        if change == "analysis": value["analysis"]["summary"] = "公司确定增加20亿元收入。"
        if change == "protocol": value["protocol"] = "unknown-protocol"
        if change == "usage": value["usage_id"] = "unknown"
        if change == "evidence_hash": value["company_evidence_sha256"] = "f" * 64
        if change == "rejections": value["rejected_links"] = ["原答中不存在的拒绝理由"]
        if change == "expiry": value["valid_until"] = "2026-12-20T08:30:00+08:00"
        if change == "packet_identity": value["packet"]["symbol"] = "600002"
    rewrite(tmp_path, key, mutate)
    assert ci.read_saved(prepared[3], "600001", now=NOW, root=tmp_path) is None


@pytest.mark.parametrize("change", ["version", "source", "inactive", "expired", "before_generation", "historical", "profile_drift"])
def test_auto_current_reader_rechecks_version_time_source_and_profile(prepared, tmp_path, change):
    publish(prepared, tmp_path)
    item = deepcopy(prepared[3])
    now, history = NOW, None
    if change == "version": item["interpretation_ref"]["version_id"] = 21
    if change == "source": item["source_context"]["summary"] += "修订"
    if change == "inactive": item["state"] = "withdrawn"
    if change == "expired": now += timedelta(days=8)
    if change == "before_generation": now = datetime.fromisoformat("2026-10-20T08:10:00+08:00")
    if change == "historical": history = datetime.fromisoformat("2026-10-20T09:59:59+08:00")
    if change == "profile_drift":
        profile = deepcopy(prepared[2]); profile["documents"][0]["excerpts"][0]["quote"] = "后来公司资料已修订。"
        state.put_profile(profile, root=tmp_path)
    assert ci.read_saved(item, "600001", now=now, response_as_of=history, root=tmp_path) is None


def test_legacy_owner_marker_is_preserved_for_explicit_recovery(tmp_path):
    marker = tmp_path / ".publish.lock"
    marker.write_text("legacy-owner-marker")
    with pytest.raises(FileExistsError):
        with ci.publication_lock(tmp_path):
            pytest.fail("legacy owner was overridden")
    assert marker.read_text() == "legacy-owner-marker"


@pytest.mark.parametrize("crash", [False, True])
def test_os_lease_blocks_other_process_and_recovers_without_unlinking(tmp_path, crash):
    code = ("from pathlib import Path\nimport sys\n"
            "from app.events.company_impact import publication_lock\n"
            "with publication_lock(Path(sys.argv[1])):\n"
            " print('owned', flush=True)\n sys.stdin.readline()\n")
    process = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, cwd=Path(__file__).resolve().parents[1])
    try:
        assert process.stdout.readline().strip() == "owned"
        marker = tmp_path / ".publish.lock"
        inode = marker.stat().st_ino
        with pytest.raises(FileExistsError):
            with ci.publication_lock(tmp_path):
                pytest.fail("second process acquired an owned lease")
        if crash:
            process.kill()
        else:
            process.stdin.write("release\n"); process.stdin.flush()
        process.wait(timeout=5)
        with ci.publication_lock(tmp_path):
            assert marker.stat().st_ino == inode and marker.stat().st_size == 0
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)
        for handle in (process.stdin, process.stdout, process.stderr):
            handle.close()


def test_identical_documents_rechecked_later_do_not_invent_new_generation_or_block_recovery(prepared, tmp_path):
    packet, _, profile, item = prepared
    profile["checked_at"] = "2026-10-20T09:30:00+08:00"
    state.put_profile(profile, root=tmp_path)
    key = publish(prepared, tmp_path)
    saved = json.loads((tmp_path/"600001"/f"{key}.json").read_text())
    assert saved["generated_at"] == "2026-10-20T08:30:00+08:00"
    assert saved["packet"]["captured_at"] == "2026-10-20T08:00:00+08:00"
    assert saved["valid_until"] == "2026-10-27T08:30:00+08:00"
    assert ci.read_saved(item,"600001",now=NOW,root=tmp_path) is not None
