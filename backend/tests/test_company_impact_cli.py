"""Only a complete comparison and unchanged collected sources can be published.

The PDF reader double supplies extracted fixture text. These tests exercise the
collector's issuer, quote and cached-byte guards, not PDF extraction quality.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.events.company_impact import SCHEMA, digest, messages
from scripts import company_impact as cli


@pytest.fixture
def public_case():
    document = {
        "id": "interim", "symbol": "003021", "company_name": "夹具驱动公司",
        "title": "2026年半年度报告", "period": "2026H1", "identity_page": 1,
        "url": "https://static.cninfo.com.cn/finalpage/2026-08-15/1234567890.PDF",
        "published_at": "2026-08-15T23:59:59+08:00",
        "received_at": "2026-10-20T08:00:00+08:00", "sha256": "a" * 64,
        "identity_quote": "夹具驱动公司003021", "published_precision": "day",
        "excerpts": [{"kind": "product", "page": 2, "quote": "公司提供医疗驱动部件。"}],
    }
    packet = {
        "id": "medical-components", "schema": SCHEMA, "symbol": "003021",
        "company_name": "夹具驱动公司", "captured_at": "2026-10-20T08:05:00+08:00",
        "event": {"event_id": 10, "version_id": 20, "observation_id": 30,
                  "title": "设备采购价格规则调整", "summary": "新规则尚无具体采购量。",
                  "available_at": "2026-10-20T07:30:00+08:00"},
        "documents": [document],
    }
    analysis = {
        "status": "conditional", "summary": "驱动部件需求可能变化，须新增设备订单证实。",
        "chain": [{"text": "若采购规则影响设备采购，医疗驱动部件需求可能随之变化。",
                   "evidence_ids": ["event:title", "interim:0"]}],
        "counterarguments": ["价格压力也可能传导到零部件供应者。"],
        "missing": ["本材料没有对应客户或具体订单金额。"],
    }
    return packet, analysis


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _cli_inputs(tmp_path, public_case, monkeypatch, *, receipt_change=None):
    packet, analysis = public_case
    packets = [packet]
    requests = cli.prepare(packets)
    receipts = [{
        **{key: request[key] for key in ("request_id", "input_sha256", "prompt_sha256")},
        "state": "succeeded", "provider": "fixture", "model": "fixture-model",
        "elapsed_ms": 10, "usage": {"input_tokens": 10, "output_tokens": 20},
        "result": json.dumps([{"id": packet["id"], "analysis": analysis}], ensure_ascii=False),
    } for request in requests]
    if receipt_change == "missing_baseline":
        receipts = receipts[1:]
    elif receipt_change == "failed_baseline":
        receipts[0]["state"] = "failed"
        receipts[0].pop("result")
    elif receipt_change == "invalid_baseline":
        receipts[0]["result"] = "[]"
    comparison = cli.review(packets, requests, receipts)
    review = {packet["id"]: {
        "role": "author", "verdict": "PASS_EXPLANATION_ONLY", "independent_gold": False,
        "net_improvement": True, "rationale": "合成夹具，仅测试CLI准入与数据传递。",
        "reviewed_at": "2026-10-20T08:30:00+08:00",
        "generated_at": "2026-10-20T08:15:00+08:00",
    }}
    paths = {key: tmp_path / f"{key}.json" for key in ("input", "comparison", "source-review")}
    _write_json(paths["input"], packets)
    _write_json(paths["comparison"], comparison)
    _write_json(paths["source-review"], review)
    receipt_path = tmp_path / "receipts.jsonl"
    receipt_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in receipts), encoding="utf-8")
    source_root = tmp_path / "sources"
    source_root.mkdir()
    original = source_root / "1234567890.pdf"
    original.write_bytes(b"%PDF-fixture; not a parsed PDF")
    _write_json(original.with_suffix(".received.json"), {"fixture": True})
    out = tmp_path / "published"
    argv = ["company_impact.py", "publish", "--out", str(out),
            "--receipts", str(receipt_path), "--source-root", str(source_root)]
    for option, path in paths.items():
        argv.extend([f"--{option}", str(path)])
    monkeypatch.setattr(sys, "argv", argv)
    published, collected = [], []

    def collect(plan, root):
        collected.append((deepcopy(plan), root))
        return deepcopy(packet["documents"])

    def publish(*args, **kwargs):
        published.append((args, kwargs))

    monkeypatch.setattr(cli, "collect", collect)
    monkeypatch.setattr(cli, "publish", publish)
    return SimpleNamespace(packet=packet, analysis=analysis, receipts=receipts, paths=paths,
                           comparison=comparison, published=published, collected=collected,
                           receipt_path=receipt_path, source_root=source_root, out=out)


@pytest.mark.parametrize("change", ["missing_baseline", "failed_baseline", "invalid_baseline"])
def test_publish_rejects_incomplete_or_invalid_paired_comparison(tmp_path, public_case, monkeypatch, change):
    context = _cli_inputs(tmp_path, public_case, monkeypatch, receipt_change=change)
    with pytest.raises(ValueError, match="complete valid same-input comparison"):
        cli.main()
    assert context.collected == [] and context.published == []
    assert not context.out.exists()


def test_publish_rejects_changed_input_before_source_or_publication(tmp_path, public_case, monkeypatch):
    context = _cli_inputs(tmp_path, public_case, monkeypatch)
    changed = deepcopy(context.packet)
    changed["event"]["summary"] = "公司已签新订单"
    _write_json(context.paths["input"], [changed])
    with pytest.raises(ValueError, match="comparison input changed"):
        cli.main()
    assert context.collected == [] and context.published == []


def test_publish_rejects_packet_quote_changed_from_collected_source(tmp_path, public_case, monkeypatch):
    context = _cli_inputs(tmp_path, public_case, monkeypatch)
    verified = deepcopy(context.packet["documents"])
    verified[0]["excerpts"][0]["quote"] = "本次合同未成立。"
    monkeypatch.setattr(cli, "collect", lambda *_: verified)
    with pytest.raises(ValueError, match="packet facts differ from verified original evidence"):
        cli.main()
    assert context.published == [] and not context.out.exists()


def test_publish_missing_original_never_fetches_or_publishes(tmp_path, public_case, monkeypatch):
    context = _cli_inputs(tmp_path, public_case, monkeypatch)
    (context.source_root / "1234567890.pdf").unlink()
    with pytest.raises(ValueError, match="original missing; publication does not fetch"):
        cli.main()
    assert context.collected == [] and context.published == []


def test_same_pdf_url_cannot_hide_one_cases_changed_quote(tmp_path, public_case, monkeypatch):
    context = _cli_inputs(tmp_path, public_case, monkeypatch)
    first = deepcopy(context.packet)
    first["documents"][0]["excerpts"][0]["quote"] = "公司已获得百亿元医疗订单。"
    second = deepcopy(context.packet)
    second["id"] = "second-scope"
    second["event"].update(event_id=11, version_id=21, observation_id=31)
    packets = [first, second]
    requests = cli.prepare(packets)
    receipts = [{
        **{key: request[key] for key in ("request_id", "input_sha256", "prompt_sha256")},
        "state": "succeeded", "provider": "fixture", "model": "fixture-model",
        "result": json.dumps([{"id": p["id"], "analysis": context.analysis} for p in packets], ensure_ascii=False),
    } for request in requests]
    _write_json(context.paths["input"], packets)
    _write_json(context.paths["comparison"], cli.review(packets, requests, receipts))
    source_review = cli.read(context.paths["source-review"])
    source_review[second["id"]] = deepcopy(source_review[first["id"]])
    _write_json(context.paths["source-review"], source_review)
    context.receipt_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in receipts), encoding="utf-8")

    def verify_all_scopes(plan, _root):
        verified = deepcopy(plan)
        # The first packet's alleged order is absent from the original. The
        # second packet's product quote is present in that same official PDF.
        for document in verified:
            if document["excerpts"][0]["quote"] == first["documents"][0]["excerpts"][0]["quote"]:
                document["excerpts"][0]["quote"] = second["documents"][0]["excerpts"][0]["quote"]
        return verified

    monkeypatch.setattr(cli, "collect", verify_all_scopes)
    with pytest.raises(ValueError):
        cli.main()
    assert context.published == []


def test_complete_comparison_passes_exact_public_evidence_and_model_identity(tmp_path, public_case, monkeypatch):
    context = _cli_inputs(tmp_path, public_case, monkeypatch)
    cli.main()
    assert context.collected == [(context.packet["documents"], context.source_root)]
    assert len(context.published) == 1
    args, kwargs = context.published[0]
    packet, analysis, review = args
    assert packet == context.packet and analysis == context.analysis
    assert review["input_sha256"] == digest(packet)
    assert review["output_sha256"] == digest(analysis)
    assert review["protocol_sha256"] == digest(messages(packet))
    assert review["comparison_sha256"] == digest(context.comparison)
    assert review["prompt_sha256"] == context.receipts[1]["prompt_sha256"]
    assert review["raw_output_sha256"] == digest(context.receipts[1]["result"])
    assert review["role"] == "author" and review["independent_gold"] is False
    assert kwargs == {"model": {"provider": "fixture", "model": "fixture-model"},
                      "generated_at": "2026-10-20T08:15:00+08:00", "root": context.out}


def test_changed_model_analysis_requires_explicit_author_corrections(tmp_path, public_case, monkeypatch):
    context = _cli_inputs(tmp_path, public_case, monkeypatch)
    source_review = cli.read(context.paths["source-review"])
    approved = deepcopy(context.analysis)
    approved["summary"] = "公司披露医疗驱动部件业务；消息没有本股订单，经营方向仍未核实。"
    source_review[context.packet["id"]]["approved_analysis"] = approved
    _write_json(context.paths["source-review"], source_review)
    before = context.receipt_path.read_bytes()
    with pytest.raises(ValueError, match="author corrections require explicit source-review notes"):
        cli.main()
    assert context.published == [] and not context.out.exists()
    assert context.receipt_path.read_bytes() == before


def test_author_corrected_analysis_preserves_exact_raw_identity_and_change_notes(tmp_path, public_case, monkeypatch):
    context = _cli_inputs(tmp_path, public_case, monkeypatch)
    source_review = cli.read(context.paths["source-review"])
    approved = deepcopy(context.analysis)
    approved["summary"] = "公司披露医疗驱动部件业务；消息没有本股订单，经营方向仍未核实。"
    notes = ["原答的需求传导为假设；明确保留客户、订单及影响方向缺口。"]
    source_review[context.packet["id"]].update(
        approved_analysis=approved, corrections=notes, raw_analysis_sha256="0" * 64,
    )
    _write_json(context.paths["source-review"], source_review)
    before = context.receipt_path.read_bytes()
    cli.main()
    assert len(context.published) == 1
    args, _ = context.published[0]
    _, analysis, review = args
    assert analysis == approved and analysis != context.analysis
    assert "approved_analysis" not in review
    assert review["corrections"] == notes
    assert review["raw_analysis_sha256"] == digest(context.analysis)
    assert review["output_sha256"] == digest(approved)
    assert review["output_sha256"] != review["raw_analysis_sha256"]
    assert review["raw_output_sha256"] == digest(context.receipts[1]["result"])
    assert review["role"] == "author" and review["independent_gold"] is False
    assert context.receipt_path.read_bytes() == before


def _cached_source(tmp_path, public_case, monkeypatch, *, identity="夹具驱动公司003021",
                   excerpt="公司提供医疗驱动部件。"):
    document = deepcopy(public_case[0]["documents"][0])
    raw = b"%PDF-fixture with stable bytes"
    document["sha256"] = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "1234567890.pdf"
    path.write_bytes(raw)
    stamp = {"url": document["url"], "received_at": document["received_at"],
             "sha256": document["sha256"]}
    _write_json(path.with_suffix(".received.json"), stamp)
    parsed = []

    def reader(source):
        parsed.append(Path(source))
        return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: identity),
                                      SimpleNamespace(extract_text=lambda: excerpt)])

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=reader))

    def no_network(*_args, **_kwargs):
        pytest.fail("a cached original must not make a network request")

    monkeypatch.setattr(cli, "build_opener", no_network)
    return document, path, parsed


@pytest.mark.parametrize("change", ["issuer", "quote"])
def test_collector_rejects_issuer_or_quote_mismatch_in_extracted_original(tmp_path, public_case, monkeypatch, change):
    kwargs = {"identity": "其他公司600001"} if change == "issuer" else {"excerpt": "公司只提供工业部件。"}
    document, _, parsed = _cached_source(tmp_path, public_case, monkeypatch, **kwargs)
    message = "official issuer identity not found" if change == "issuer" else "exact source quote not found"
    with pytest.raises(ValueError, match=message):
        cli.collect([document], tmp_path)
    assert len(parsed) == 1


def test_collector_rejects_changed_cached_bytes_before_parsing(tmp_path, public_case, monkeypatch):
    document, path, parsed = _cached_source(tmp_path, public_case, monkeypatch)
    path.write_bytes(b"%PDF-replaced bytes")
    with pytest.raises(ValueError, match="original bytes or URL changed"):
        cli.collect([document], tmp_path)
    assert parsed == []


def test_collector_reuses_matching_cached_original_without_fetch(tmp_path, public_case, monkeypatch):
    document, path, parsed = _cached_source(tmp_path, public_case, monkeypatch)
    result = cli.collect([document], tmp_path)
    assert result == [document]
    assert parsed == [path]
