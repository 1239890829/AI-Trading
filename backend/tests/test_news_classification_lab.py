"""Finite LAB provenance, failed-request denominators and production isolation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import news_classification_lab as lab


def samples():
    rows = []
    for sid, title in (("a", "某公司拟收购标的"), ("b", "板块震荡走强")):
        row = {"id": sid, "title": title, "summary": "尚未签署正式协议。", "source": "public",
               "url": "https://example.org/news", "published_at": "2026-10-10T10:00:00+08:00",
               "available_at": "2026-10-10T10:01:00+08:00", "change_kind": "initial"}
        row["input_sha256"] = lab.digest({"title": row["title"], "summary": row["summary"]})
        row["content_hash"] = row["input_sha256"]
        rows.append(row)
    return rows


def prediction(sid="a"):
    return {"id": sid, "category": "material_event", "direction": 0, "themes": [],
            "chain": "", "reason": "拟议事件，实际进展未知。", "evidence": [{"field": "title", "quote": "拟收购"}],
            "uncertainties": ["尚未完成"], "event_status": "proposed", "novelty": "unavailable"}


def receipt(request, result=None, **kwargs):
    row = {key: request[key] for key in ("request_id", "input_sha256", "prompt_sha256")}
    return row | {"state": "succeeded", "result": result, "elapsed_ms": 10, **kwargs}


def test_title_variant_hash_and_no_private_fields():
    rows = samples()
    rows[0]["holdings"] = "private account data"
    requests = lab.prepare_requests(rows)
    assert "private account data" not in json.dumps(requests)
    title_input = json.loads(requests[0]["messages"][1]["content"])
    assert title_input[0]["summary"] == ""
    assert title_input[0]["content_hash"] != rows[0]["content_hash"]
    assert rows[0]["summary"] == "尚未签署正式协议。"


def test_no_gold_no_accuracy_and_failed_requests_remain_in_denominator():
    rows = samples()
    requests = lab.prepare_requests(rows, batch_size=1)
    results = [receipt(requests[0], [prediction()]), receipt(requests[1], state="timeout")]
    report = lab.review(rows, requests, results)
    title = report["variants"]["title_only"]
    assert title["total_samples"] == 2
    assert title["attempted_samples"] == 2
    assert title["valid_samples"] == 1
    assert title["failed_or_invalid_samples"] == 1
    assert title["runtime_unknown_samples"] == 1
    assert title["correct_over_all_labeled"] is None
    assert report["actual_total_cost_usd"] is None
    assert report["receipts_with_unknown_actual_cost"] == 2
    assert report["variants"]["title_summary"]["unattempted_samples"] == 2


def test_quote_failure_is_runtime_unknown_not_semantic_insufficient():
    rows = samples()
    requests = lab.prepare_requests(rows, batch_size=1)
    bad = prediction()
    bad["evidence"][0]["quote"] = "已完成收购"
    report = lab.review(rows, requests, [receipt(requests[0], [bad])])
    assert report["variants"]["title_only"]["valid_samples"] == 0
    assert report["variants"]["title_only"]["category_distribution"] == {}


def test_edited_prompt_and_duplicate_receipt_rejected():
    rows = samples()
    requests = lab.prepare_requests(rows)
    item = receipt(requests[0], [])
    with pytest.raises(ValueError, match="repeated"):
        lab.review(rows, requests, [item, item])
    requests[0]["messages"][0]["content"] = "different rubric"
    with pytest.raises(ValueError, match="frozen contract"):
        lab.review(rows, requests, [])


def test_independent_label_and_input_binding():
    rows = samples()
    annotations = lab.blind_rows(rows)
    annotations[0]["human"] = {"category": "material_event", "reviewer": "human reviewer", "independent": True}
    report = lab.review(rows, lab.prepare_requests(rows), [], annotations=annotations)
    assert report["variants"]["title_summary"]["correct_over_all_labeled"] == 0
    assert report["variants"]["title_only"]["correct_over_all_labeled"] is None
    annotations[0]["title"] = "changed source"
    with pytest.raises(ValueError, match="changed frozen"):
        lab.review(rows, lab.prepare_requests(rows), [], annotations=annotations)
    annotations = lab.blind_rows(rows)
    with pytest.raises(ValueError, match="repeated"):
        lab.review(rows, lab.prepare_requests(rows), [], annotations=annotations + [annotations[0]])


def test_frozen_hash_and_future_available_time(tmp_path):
    path = tmp_path / "samples.jsonl"
    meta = tmp_path / "manifest.json"
    rows = samples()
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    manifest = {"sample_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "sample_count": 2, "frozen_at": "2026-10-11T09:00:00+08:00"}
    meta.write_text(json.dumps(manifest))
    assert lab.load_frozen(path, meta)[0] == rows
    rows[0]["available_at"] = "2026-10-12T10:00:00+08:00"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="hash mismatch"):
        lab.load_frozen(path, meta)
    manifest["sample_file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    meta.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="later than freeze"):
        lab.load_frozen(path, meta)


def test_write_cannot_silently_replace_evidence(tmp_path):
    out = tmp_path / "evidence.json"
    lab.write_new(out, {"value": 1})
    with pytest.raises(FileExistsError):
        lab.write_new(out, {"value": 2})
    assert json.loads(out.read_text()) == {"value": 1}


def test_transport_is_stateless_and_failure_stops_without_retry(tmp_path, monkeypatch):
    from app.core import llm_client as lc
    config = tmp_path / "config"
    config.write_text('ASHARE_LLM_PROVIDER=claude_cli\nASHARE_REVIEW_LLM_MODEL=research-model\n')
    monkeypatch.setattr(lc, "resolve_cli_path", lambda path="": "/configured/cli")
    calls = []
    cwd_before = Path.cwd()

    def fake(model, messages, **options):
        calls.append((model, messages, options))
        assert Path.cwd() != cwd_before
        adapter = Path(options["cli_path"]).read_text()
        assert "os.execv" in adapter and "--strict-mcp-config" in adapter
        assert "disableAllHooks" in adapter and "--disable-slash-commands" in adapter
        assert options["observe_health"] is False
        raise lc.LLMError("failed", lc.LLMFailure.TIMEOUT)

    monkeypatch.setattr(lc, "chat_completion_via_cli", fake)
    out = tmp_path / "receipts.jsonl"
    lab.run_cli(lab.prepare_requests(samples()), config, out)
    assert len(calls) == 1 and Path.cwd() == cwd_before
    assert lab.read_jsonl(out)[0]["state"] == "failed"
    assert len(list(tmp_path.glob("*.started.json"))) == 1
    with pytest.raises(FileExistsError):
        lab.run_cli(lab.prepare_requests(samples()), config, out)
    assert len(calls) == 1


def test_interrupted_started_call_is_attempted_and_cost_unknown(tmp_path, monkeypatch):
    from app.core import llm_client as lc
    config = tmp_path / "config"
    config.write_text('ASHARE_LLM_PROVIDER=claude_cli\nASHARE_REVIEW_LLM_MODEL=research-model\n')
    monkeypatch.setattr(lc, "resolve_cli_path", lambda path="": "/configured/cli")
    def interrupted(*args, **kwargs):
        raise RuntimeError("process interrupted after started marker")
    monkeypatch.setattr(lc, "chat_completion_via_cli", interrupted)
    requests = lab.prepare_requests(samples())
    out = tmp_path / "receipts.jsonl"
    with pytest.raises(RuntimeError):
        lab.run_cli(requests, config, out)
    markers = [json.loads(path.read_text()) for path in tmp_path.glob("*.started.json")]
    report = lab.review(samples(), requests, lab.read_jsonl(out), started=markers)
    title = report["variants"]["title_only"]
    assert title["attempted_samples"] == 2
    assert title["failed_or_invalid_samples"] == 2
    assert report["actual_total_cost_usd"] is None
    assert report["receipts_with_unknown_actual_cost"] == 1


def test_pilot_request_bound_is_consistent():
    with pytest.raises(ValueError, match="1-24"):
        lab.prepare_requests(samples() * 13)
