"""Finite public-news LAB using the existing CLI transport, without production writes."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
from time import monotonic

try:
    from scripts.news_classification_contract import build_messages, sample_content_hash, validate_response
except ModuleNotFoundError:  # direct invocation from backend/scripts
    from news_classification_contract import build_messages, sample_content_hash, validate_response


def digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_new(path: Path, value, *, lines: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in value) if lines
            else json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def load_frozen(samples_path: Path, manifest_path: Path) -> tuple[list[dict], dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    file_hash = hashlib.sha256(samples_path.read_bytes()).hexdigest()
    if manifest.get("sample_file_sha256") != file_hash:
        raise ValueError("frozen sample hash mismatch")
    samples = read_jsonl(samples_path)
    if len(samples) != manifest.get("sample_count") or not 1 <= len(samples) <= 24:
        raise ValueError("pilot must contain the frozen 1-24 rows")
    ids = [sample.get("id") for sample in samples]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("sample IDs must be unique strings")
    freeze = datetime.fromisoformat(manifest["frozen_at"])
    if freeze.utcoffset() is None:
        raise ValueError("freeze requires an explicit timezone")
    for sample in samples:
        for field in ("published_at", "available_at"):
            at = datetime.fromisoformat(sample[field])
            if at.utcoffset() is None or at > freeze:
                raise ValueError(f"{field} is unzoned or later than freeze")
        if sample.get("change_kind") != "initial":
            raise ValueError("this pilot accepts initial observations only")
        expected = digest({"title": sample.get("title") or "", "summary": sample.get("summary") or ""})
        if sample.get("input_sha256") != expected:
            raise ValueError("public input identity mismatch")
    return samples, manifest


def variant_samples(samples: list[dict], variant: str) -> list[dict]:
    if variant not in {"title_only", "title_summary"}:
        raise ValueError("unknown input variant")
    rows = [{**sample, "summary": (sample.get("summary") or "") if variant == "title_summary" else "",
             "prior_version": None} for sample in samples]
    for row in rows:
        row["content_hash"] = sample_content_hash(row)
    return rows


def prepare_requests(samples: list[dict], *, batch_size: int = 8) -> list[dict]:
    if not 1 <= len(samples) <= 24:
        raise ValueError("finite pilot requires 1-24 samples")
    if not 1 <= batch_size <= 8:
        raise ValueError("batch size must be 1-8")
    requests = []
    for variant in ("title_only", "title_summary"):
        rows = variant_samples(samples, variant)
        for index in range(0, len(rows), batch_size):
            batch = rows[index:index + batch_size]
            messages = build_messages(batch)
            requests.append({
                "request_id": f"{variant}:{index // batch_size}", "variant": variant,
                "sample_ids": [row["id"] for row in batch],
                "input_sha256": digest([sample_content_hash(row) for row in batch]),
                "prompt_sha256": digest(messages), "messages": messages,
            })
    return requests


def blind_rows(samples: list[dict]) -> list[dict]:
    # Predictors and sampling coverage notes are deliberately absent.
    return [{key: sample.get(key) for key in
             ("id", "title", "summary", "source", "url", "published_at", "available_at", "input_sha256")}
            | {"human": None} for sample in samples]


def _human_labels(samples: list[dict], annotations: list[dict]) -> dict:
    expected = {row["id"]: row for row in blind_rows(samples)}
    labels = {}
    seen = set()
    for row in annotations:
        sid = row.get("id")
        if sid not in expected or sid in seen:
            raise ValueError("annotation has unknown or repeated ID")
        seen.add(sid)
        if {key: row.get(key) for key in expected[sid] if key != "human"} != {
                key: value for key, value in expected[sid].items() if key != "human"}:
            raise ValueError("annotation changed frozen public input")
        human = row.get("human")
        if human is None:
            continue
        if (not isinstance(human, dict) or human.get("category") not in
                {"material_event", "market_action", "routine", "insufficient"}
                or not isinstance(human.get("reviewer"), str) or not human["reviewer"].strip()
                or human.get("independent") is not True):
            raise ValueError("labels require declared independent human review")
        # This validates the declaration; it cannot prove the reviewer's independence.
        labels[sid] = human["category"]
    return labels


def review(samples: list[dict], requests: list[dict], receipts: list[dict],
           *, annotations: list[dict] | None = None, started: list[dict] | None = None) -> dict:
    by_request = {request["request_id"]: request for request in requests}
    if len(by_request) != len(requests):
        raise ValueError("request IDs repeated")
    # Derive the contract again instead of trusting an edited exported prompt/hash.
    if not requests or requests != prepare_requests(samples, batch_size=len(requests[0]["sample_ids"])):
        raise ValueError("requests differ from the frozen contract")
    receipt_ids = {row.get("request_id") for row in receipts}
    start_ids = [row.get("request_id") for row in started or []]
    if len(set(start_ids)) != len(start_ids):
        raise ValueError("started IDs repeated")
    receipts = list(receipts)
    for marker in started or []:
        rid = marker.get("request_id")
        if rid not in by_request or any(marker.get(key) != by_request[rid][key]
                                       for key in ("input_sha256", "prompt_sha256")):
            raise ValueError("started marker differs from frozen request")
        if rid not in receipt_ids:
            receipts.append({**marker, "state": "interrupted_unknown", "actual_cost_usd": None,
                             "cost_basis": "unknown_gateway_billing"})
    seen = set()
    predictions = {variant: {} for variant in ("title_only", "title_summary")}
    attempts = Counter()
    failures = Counter()
    latencies = {variant: [] for variant in predictions}
    cost_known = []
    unknown_cost = 0
    for receipt in receipts:
        rid = receipt.get("request_id")
        if rid not in by_request or rid in seen:
            raise ValueError("receipt has unknown or repeated request ID")
        seen.add(rid)
        request = by_request[rid]
        if any(receipt.get(key) != request[key] for key in ("input_sha256", "prompt_sha256")):
            raise ValueError("receipt is not bound to this input and prompt")
        variant = request["variant"]
        ids = request["sample_ids"]
        attempts[variant] += len(ids)
        elapsed = receipt.get("elapsed_ms")
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and 0 <= elapsed < float("inf"):
            latencies[variant].append(elapsed)
        actual = receipt.get("actual_cost_usd")
        if (receipt.get("cost_basis") == "provider_billing" and isinstance(actual, (int, float))
                and not isinstance(actual, bool) and 0 <= actual < float("inf")):
            cost_known.append(Decimal(str(actual)))
        else:
            unknown_cost += 1
        if receipt.get("state") != "succeeded":
            failures[variant] += len(ids)
            continue
        batch = [row for row in variant_samples(samples, variant) if row["id"] in ids]
        parsed = validate_response(receipt.get("result"), batch)
        if parsed["batch_errors"]:
            failures[variant] += len(ids)
            continue
        predictions[variant].update({row["id"]: row for row in parsed["valid"]})
        failures[variant] += parsed["invalid_count"]
    labels = _human_labels(samples, annotations or [])
    variants = {}
    for variant, preds in predictions.items():
        # Blind annotations contain title+summary. They cannot fairly grade a
        # title-only model that never received the additional information.
        variant_labels = labels if variant == "title_summary" else {}
        confusion = Counter((gold, preds[sid]["category"] if sid in preds else "runtime_unknown")
                            for sid, gold in variant_labels.items())
        correct = sum(preds.get(sid, {}).get("category") == gold for sid, gold in variant_labels.items())
        delays = sorted(latencies[variant])
        variants[variant] = {
            "total_samples": len(samples), "attempted_samples": attempts[variant],
            "unattempted_samples": len(samples) - attempts[variant],
            "valid_samples": len(preds), "failed_or_invalid_samples": failures[variant],
            "runtime_unknown_samples": len(samples) - len(preds),
            "valid_coverage": len(preds) / len(samples),
            "category_distribution": dict(Counter(row["category"] for row in preds.values())),
            "human_labeled_samples": len(variant_labels),
            "correct_over_all_labeled": correct / len(variant_labels) if variant_labels else None,
            "confusion_including_runtime_unknown": [
                {"gold": gold, "prediction": pred, "count": count} for (gold, pred), count in confusion.items()],
            "p50_batch_elapsed_ms": statistics.median(delays) if delays else None,
            "p95_batch_elapsed_ms": delays[min(len(delays) - 1, int((len(delays) - 1) * .95 + .5))] if delays else None,
            "batch_latency_observations": len(delays),
        }
    return {
        "variants": variants, "provider_billed_usd_known": str(sum(cost_known, Decimal(0))),
        "receipts_with_unknown_actual_cost": unknown_cost,
        "actual_total_cost_usd": str(sum(cost_known, Decimal(0))) if not unknown_cost else None,
        "quality_status": "independent_review_declared" if labels else "unknown_no_human_gold",
        "limits": ["Exact quotes prove anchoring, not semantic correctness.",
                   "Purposive pilot cannot establish production accuracy or selection benefit.",
                   "Human annotations grade title+summary only; title-only is descriptive, without matched-input gold.",
                   "No prior version: novelty remains unavailable.",
                   "Batch latency includes failed attempts when elapsed was supplied; missing latency remains unknown."],
        "predictions": predictions,
    }


def run_cli(requests: list[dict], config_path: Path, out: Path) -> None:
    """Explicit, finite CLI run. A started receipt prevents accidental re-sending.

    A temporary exec adapter adds isolation flags without spawning an extra child.
    The parser, credentials, model and transport remain the existing ones. Token
    usage is kept; the transport does not expose a trustworthy gateway bill.
    """
    from dotenv import dotenv_values
    from app.core.llm_client import LLMError, chat_completion_via_cli, resolve_cli_path

    config = dotenv_values(config_path)
    provider = config.get("ASHARE_LLM_PROVIDER")
    model = config.get("ASHARE_REVIEW_LLM_MODEL")
    if provider != "claude_cli" or not isinstance(model, str) or not model.strip():
        raise ValueError("LAB requires the already configured CLI provider and explicit model")
    cli = resolve_cli_path(config.get("ASHARE_LLM_CLI_PATH") or "")
    if not cli:
        raise ValueError("configured CLI unavailable")
    if not 1 <= len(requests) <= 6:
        raise ValueError("finite pilot allows at most six prepared calls")
    # Creating, rather than truncating, receipts is the retry/resume boundary.
    out.parent.mkdir(parents=True, exist_ok=True)
    original_cwd = Path.cwd()
    with out.open("x", encoding="utf-8") as receipts, tempfile.TemporaryDirectory(prefix="news-lab-") as tmp:
        root = Path(tmp)
        adapter = root / "bounded-cli"
        adapter.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            f"cmd = [{cli!r}] + sys.argv[1:] + ['--setting-sources','user',"
            "'--settings','{\"disableAllHooks\":true}', '--strict-mcp-config',"
            "'--mcp-config','{\"mcpServers\":{}}', '--disable-slash-commands','--max-turns','1']\n"
            f"os.execv({cli!r}, cmd)\n", encoding="utf-8")
        adapter.chmod(0o700)
        try:
            os.chdir(root)  # The CLI cannot discover project instructions or files.
            for request in requests:
                receipt = {key: request[key] for key in ("request_id", "input_sha256", "prompt_sha256")}
                receipt.update(provider=provider, model=model, state="started", top_level_calls=1,
                               internal_retries=None, actual_cost_usd=None, cost_basis="unknown_gateway_billing")
                # Durable started marker before invoking an external model. No auto-resume.
                write_new(out.parent / (request["request_id"].replace(":", "-") + ".started.json"), receipt)
                start = monotonic()
                usage = []
                try:
                    result = chat_completion_via_cli(
                        model, request["messages"], cli_path=str(adapter), timeout=180,
                        usage_callback=usage.append, observe_health=False)
                    receipt.update(state="succeeded", result=result)
                except LLMError as error:
                    receipt.update(state="failed", failure_kind=error.kind.value)
                receipt.update(elapsed_ms=round((monotonic() - start) * 1000, 3),
                               usage=usage[-1] if usage else None)
                receipts.write(json.dumps(receipt, ensure_ascii=False) + "\n")
                receipts.flush()
                os.fsync(receipts.fileno())
                if receipt["state"] != "succeeded":
                    break  # Failure stops the batch; no retries or fallback calls.
        finally:
            os.chdir(original_cwd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run-cli", "review"))
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--requests", type=Path)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--allow-existing-model-cost", action="store_true")
    args = parser.parse_args(argv)
    samples, manifest = load_frozen(args.samples, args.manifest)
    if args.command == "prepare":
        requests = prepare_requests(samples)
        write_new(args.out / "requests.jsonl", requests, lines=True)
        blind = blind_rows(samples)
        write_new(args.out / "blind.jsonl", blind, lines=True)
        write_new(args.out / "identity.json", {
            "sample_file_sha256": manifest["sample_file_sha256"], "requests_sha256": digest(requests),
            "blind_sha256": digest(blind), "max_rows": 24, "production_consumer": False,
            "model_calls": 0, "live_status": "not_started",
            "cost_boundary": "Explicit caller authorization; gateway actual costs remain unknown unless billed.",
        })
    elif args.command == "run-cli":
        if not args.requests or not args.config or not args.allow_existing_model_cost:
            parser.error("run-cli requires --requests, --config and explicit --allow-existing-model-cost")
        requests = read_jsonl(args.requests)
        if requests != prepare_requests(samples):
            raise ValueError("requests differ from frozen public pilot and prompt")
        run_cli(requests, args.config.resolve(), args.out.resolve())
    else:
        if not args.requests or not args.receipts:
            parser.error("review requires --requests and --receipts")
        report = review(samples, read_jsonl(args.requests), read_jsonl(args.receipts),
                        annotations=read_jsonl(args.annotations) if args.annotations else None,
                        started=[json.loads(path.read_text(encoding="utf-8")) for path in
                                 sorted(args.receipts.parent.glob("*.started.json"))])
        write_new(args.out, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
