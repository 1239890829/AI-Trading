"""Research publication cannot confer event authority or leak across versions."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from app.events import company_impact as ci
from scripts.company_impact import prepare, review

NOW = datetime(2026, 10, 20, 2, tzinfo=timezone.utc)


@pytest.fixture
def packet(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(ci, "datetime", Clock)
    return {"id": "example", "schema": ci.SCHEMA, "symbol": "003021", "company_name": "测试公司",
            "captured_at": "2026-10-20T08:00:00+08:00",
            "event": {"event_id": 10, "version_id": 20, "observation_id": 30, "title": "采购价格规则改变",
                      "summary": "具体订单未披露", "available_at": "2026-10-19T10:00:00+08:00"},
            "documents": [{"id": "h1", "symbol": "003021", "company_name": "测试公司", "title": "半年报",
                           "url": "https://static.cninfo.com.cn/finalpage/2026-08-15/1234567890.PDF",
                           "published_at": "2026-08-15T23:59:59+08:00", "period": "2026H1",
                           "received_at": "2026-10-20T07:55:00+08:00", "sha256": "a" * 64,
                           "identity_quote": "测试公司003021", "excerpts": [
                               {"kind": "product", "page": 13, "quote": "公司提供医疗驱动部件。"}]}]}


@pytest.fixture
def analysis():
    return {"status": "conditional", "summary": "部件需求或受影响，方向未证实。",
            "chain": [{"text": "采购规则可能改变设备需求，须新增订单证实。", "evidence_ids": ["event:title", "h1:0"]}],
            "counterarguments": ["采购价格变化也可能传导为压价。"], "missing": ["没有本次订单或客户映射。"]}


def publish(packet, analysis, root):
    sr = {"input_sha256": ci.digest(packet), "output_sha256": ci.digest(analysis),
          "protocol_sha256": ci.digest(ci.messages(packet)), "prompt_sha256": "b" * 64,
          "raw_output_sha256": "c" * 64, "comparison_sha256": "d" * 64,
          "role": "author", "verdict": "PASS_EXPLANATION_ONLY", "independent_gold": False,
          "net_improvement": True, "rationale": "合成夹具，只核发布边界。",
          "reviewed_at": "2026-10-20T09:00:00+08:00"}
    return ci.publish(packet, analysis, sr, model={"provider": "test", "model": "fixture"},
                      generated_at="2026-10-20T08:30:00+08:00", root=root)


def item(packet):
    return {"event_id": 10, "state": "active", "is_active": True,
            "interpretation_ref": {k: packet["event"][k] for k in ("event_id", "version_id", "observation_id")},
            "source_context": {"observation_id": 30, "title": packet["event"]["title"], "summary": packet["event"]["summary"]}}


def test_only_published_exact_current_receipt_can_be_read(packet, analysis, tmp_path):
    original = deepcopy(item(packet))
    assert ci.read_saved(original, "003021", now=NOW, root=tmp_path) is None
    publish(packet, analysis, tmp_path)
    assert ci.published_event_ids("003021", root=tmp_path) == [10]
    got = ci.read_saved(original, "003021", now=NOW, root=tmp_path)
    assert got["analysis"] == analysis
    assert got["eligibility_changed"] is False and got["business_effect_verified"] is False
    assert got["facts"][0]["quote"] == "公司提供医疗驱动部件。"
    assert item(packet) == original
    assert ci.read_saved(original, "600001", now=NOW, root=tmp_path) is None
    assert ci.read_saved(original, "../003021", now=NOW, root=tmp_path) is None
    historical = datetime.fromisoformat("2026-10-19T14:00:00+08:00")
    assert ci.read_saved(original, "003021", now=NOW, response_as_of=historical, root=tmp_path) is None


def test_discovery_is_bounded_and_never_confers_authority(packet, analysis, tmp_path):
    publish(packet, analysis, tmp_path)
    pointer = tmp_path / "003021" / "discovery.json"
    pointer.write_text(json.dumps({"event_ids": [999]}))
    assert ci.published_event_ids("003021", root=tmp_path) == [999]
    assert ci.read_saved(item(packet), "003021", now=NOW, root=tmp_path) is not None
    for value in ([1, 2, 3, 4, 5], [1, 1], ["../10"], [True], [[10]]):
        pointer.write_text(json.dumps({"event_ids": value}))
        assert ci.published_event_ids("003021", root=tmp_path) == []


def test_default_store_uses_runtime_injected_root(packet, analysis, tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "ROOT", tmp_path / "default")
    publish(packet, analysis, None)
    assert ci.published_event_ids("003021") == [10]
    assert ci.read_saved(item(packet), "003021", now=NOW) is not None


@pytest.mark.parametrize("change", ["revision", "observation", "source", "inactive", "expired", "pending", "clock"])
def test_source_or_state_change_cannot_reuse_analysis(packet, analysis, tmp_path, change):
    publish(packet, analysis, tmp_path)
    row = item(packet)
    if change == "revision": row["interpretation_ref"]["version_id"] += 1
    if change == "observation": row["source_context"]["observation_id"] += 1
    if change == "source": row["source_context"]["summary"] = "公司否认订单"
    if change in {"inactive", "pending"}: row["state"] = change
    now = datetime.fromisoformat("2026-10-28T10:00:00+08:00") if change == "expired" else NOW
    if change == "clock": now = datetime.fromisoformat("2026-10-20T08:10:00+08:00")
    assert ci.read_saved(row, "003021", now=now, root=tmp_path) is None


@pytest.mark.parametrize("change", ["corrupt", "tampered", "oversize", "symlink", "pointer", "orphan"])
def test_disk_failure_keeps_baseline(packet, analysis, tmp_path, change):
    key = publish(packet, analysis, tmp_path)
    folder = tmp_path / "003021"
    obj = folder / (key + ".json")
    if change == "corrupt": obj.write_text("{")
    if change == "tampered":
        value = json.loads(obj.read_text()); value["analysis"]["summary"] = "确定新增订单"; obj.write_text(json.dumps(value))
    if change == "oversize": obj.write_text("x" * 256001)
    if change == "symlink":
        original = folder / "copy.json"; obj.rename(original); obj.symlink_to(original)
    if change == "pointer": (folder / "10-20-30.current").write_text("../" + "a" * 61)
    if change == "orphan": (folder / "10-20-30.current").unlink()
    assert ci.read_saved(item(packet), "003021", now=NOW, root=tmp_path) is None


def test_interrupted_publish_and_single_writer_preserve_previous(packet, analysis, tmp_path, monkeypatch):
    publish(packet, analysis, tmp_path)
    folder = tmp_path / "003021"
    (folder / ".publish.lock").write_text("owner")
    with pytest.raises(FileExistsError): publish(packet, analysis, tmp_path)
    (folder / ".publish.lock").unlink()
    def failed_replace(*_): raise OSError("injected power-loss before manifest replacement")
    monkeypatch.setattr(ci.os, "replace", failed_replace)
    with pytest.raises(OSError): publish(packet, analysis, tmp_path)
    assert ci.read_saved(item(packet), "003021", now=NOW, root=tmp_path)["analysis"] == analysis
    assert not (folder / ".publish.lock").exists()


@pytest.mark.parametrize("change", ["foreign", "url", "date", "future", "quote", "missing", "citation", "fields"])
def test_bad_evidence_or_claim_contract_is_rejected(packet, analysis, tmp_path, change):
    if change == "foreign": packet["documents"][0]["symbol"] = "600001"
    if change == "url": packet["documents"][0]["url"] = "https://static.cninfo.com.cn.evil.test/a.PDF"
    if change == "date": packet["documents"][0]["published_at"] = "2026-08-14T00:00:00+08:00"
    if change == "future": packet["documents"][0]["received_at"] = "2027-01-01T00:00:00+08:00"
    if change == "quote": packet["documents"][0]["excerpts"][0]["quote"] = ""
    if change == "missing": analysis["missing"] = []
    if change == "citation": analysis["chain"][0]["evidence_ids"] = ["foreign:0"]
    if change == "fields": analysis["buy_price"] = 100
    with pytest.raises(ValueError): publish(packet, analysis, tmp_path)


def test_two_arms_have_identical_public_inputs_and_failures_remain_unknown(packet):
    requests = prepare([packet])
    assert requests[0]["messages"][1] == requests[1]["messages"][1]
    assert requests[0]["input_sha256"] == requests[1]["input_sha256"]
    receipt = {k: requests[0][k] for k in ("request_id", "input_sha256", "prompt_sha256")}
    receipt.update(state="failed", elapsed_ms=1)
    result = review([packet], requests, [receipt])
    assert result["unattempted"] == ["company-0"]
    assert result["calls"][0]["state"] == "failed"
    assert result["semantic_accuracy"] is None and result["independent_human_gold"] == 0
