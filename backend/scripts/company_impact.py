"""Explicit finite company-evidence collection, comparison and research publication.

collect needs pypdf (the desktop bundled PDF runtime is sufficient). Other commands
use the existing backend environment. No HTTP route or scheduler invokes this CLI.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.request import build_opener, HTTPRedirectHandler, Request

from app.events.company_impact import digest, messages, publish, source_url, validate_analysis


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def collect(plan: list[dict], root: Path) -> list[dict]:
    """Only designated official disclosures; exact page quotes and issuer required.

    Local original bytes are reused after checking the pinned URL-derived path.
    A changed or failed source never becomes an empty business fact.
    """
    from pypdf import PdfReader

    if not 1 <= len(plan) <= 6:
        raise ValueError("finite collection requires 1-6 documents")
    root.mkdir(parents=True, exist_ok=True)
    documents = []
    for doc in plan:
        if not source_url(doc["url"]):
            raise ValueError("unsupported source; no redirects/credentials/arbitrary URL")
        filename = doc["url"].rsplit("/", 1)[-1].lower()
        path = root / filename
        received = datetime.now(timezone.utc).isoformat()
        stamp = path.with_suffix(".received.json")
        if path.exists():
            if path.is_symlink() or path.stat().st_size > 24 * 1024 * 1024:
                raise ValueError("unsafe original file")
            received = read(stamp)["received_at"]
        else:
            request = Request(doc["url"], headers={"User-Agent": "AShare-public-research/1.0"})
            with build_opener(NoRedirect).open(request, timeout=30) as response:
                raw = response.read(24 * 1024 * 1024 + 1)
            if len(raw) > 24 * 1024 * 1024 or not raw.startswith(b"%PDF-"):
                raise ValueError("source is not a bounded PDF")
            with path.open("xb") as out:
                out.write(raw)
            write_new(stamp, {"url": doc["url"], "received_at": received,
                              "sha256": hashlib.sha256(raw).hexdigest()})
        raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if raw_hash != read(stamp)["sha256"] or read(stamp)["url"] != doc["url"]:
            raise ValueError("original bytes or URL changed")
        pdf = PdfReader(path)
        if len(pdf.pages) > 350:
            raise ValueError("oversized document page count")
        compact = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
        identity = compact(pdf.pages[doc["identity_page"] - 1].extract_text())
        if doc["symbol"] not in identity or compact(doc["company_name"]) not in identity:
            raise ValueError("official issuer identity not found on specified page")
        for excerpt in doc["excerpts"]:
            text = compact(pdf.pages[excerpt["page"] - 1].extract_text())
            if excerpt["quote"] != compact(excerpt["quote"]) or excerpt["quote"] not in text:
                raise ValueError(f"exact source quote not found: {doc['id']} page {excerpt['page']}")
        documents.append({**doc, "sha256": raw_hash, "received_at": received,
                          "identity_quote": identity[:250], "published_precision": "day"})
    return documents


def prepare(packets: list[dict]) -> list[dict]:
    if not 1 <= len(packets) <= 6 or len({p["id"] for p in packets}) != len(packets):
        raise ValueError("finite unique cases required")
    requests = []
    # Pair identical public inputs, use up to four cases per call to amortize startup.
    # Protocol hashes stay per-case; prompt_sha256 names the actual batch prompt.
    for i in range(0, len(packets), 4):
        batch = packets[i:i + 4]
        for arm in ("baseline", "company"):
            systems = [messages(p, baseline=arm == "baseline")[0]["content"] for p in batch]
            inputs = [{"id": p["id"], "input": json.loads(messages(p)[1]["content"])} for p in batch]
            msgs = [{"role": "system", "content": systems[0] +
                     '本次为独立案例批次，只返回JSON数组[{"id":输入id,"analysis":上述对象}]；每案分开，不得跨公司借用事实。'},
                    {"role": "user", "content": json.dumps(inputs, ensure_ascii=False, sort_keys=True)}]
            requests.append({"request_id": f"{arm}-{i // 4}", "arm": arm,
                             "case_ids": [p["id"] for p in batch],
                             "input_sha256": digest(inputs), "prompt_sha256": digest(msgs), "messages": msgs})
    return requests


def decode(result: str) -> list[dict]:
    text = result.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text[text.find("\n") + 1:-3].strip()
    rows = json.loads(text)
    if not isinstance(rows, list):
        raise ValueError("model output must be an array")
    return rows


def review(packets: list[dict], requests: list[dict], receipts: list[dict]) -> dict:
    if prepare(packets) != requests:
        raise ValueError("prepared requests drifted")
    expected = {r["request_id"]: r for r in requests}
    seen = set()
    cases = []
    for receipt in receipts:
        rid = receipt["request_id"]
        if rid not in expected or rid in seen:
            raise ValueError("foreign or duplicate call receipt")
        seen.add(rid)
        request = expected[rid]
        if any(receipt[k] != request[k] for k in ("input_sha256", "prompt_sha256")):
            raise ValueError("call identity drift")
        row = {"request_id": rid, "arm": request["arm"], "state": receipt["state"],
               "elapsed_ms": receipt.get("elapsed_ms"), "usage": receipt.get("usage"),
               "model": receipt.get("model"), "provider": receipt.get("provider"),
               "actual_cost_usd": None, "cases": []}
        if receipt["state"] == "succeeded":
            try:
                results = decode(receipt["result"])
                if sorted(v["id"] for v in results) != sorted(request["case_ids"]):
                    raise ValueError("model cases changed")
                for result in results:
                    packet = next(p for p in packets if p["id"] == result["id"])
                    validate_analysis(result["analysis"], packet)
                    row["cases"].append(result)
            except (ValueError, KeyError, TypeError) as error:
                row.update(state="invalid", error=str(error), cases=[])
        cases.append(row)
    return {"schema": "company-impact-comparison-v1", "input_sha256": digest(packets),
            "requests_sha256": digest(requests), "calls": cases,
            "unattempted": sorted(set(expected) - seen), "independent_human_gold": 0,
            "semantic_accuracy": None, "selection_effect": None, "actual_cost_usd": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect", "prepare", "run", "review", "publish"])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--requests", type=Path)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--comparison", type=Path)
    parser.add_argument("--source-review", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--allow-existing-model-cost", action="store_true")
    args = parser.parse_args()
    data = read(args.input)
    if args.command == "collect":
        write_new(args.out / "documents.json", collect(data, args.out))
    elif args.command == "prepare":
        write_new(args.out, prepare(data))
    elif args.command == "run":
        from scripts.news_classification_lab import run_cli
        if not args.allow_existing_model_cost or args.config is None:
            parser.error("explicit existing-model cost authorization and config required")
        run_cli(prepare(data), args.config, args.out)
    elif args.command == "review":
        receipts = [json.loads(v) for v in args.receipts.read_text().splitlines() if v.strip()]
        write_new(args.out, review(data, read(args.requests), receipts))
    else:
        # This is an explicit operator action, after the full same-input comparison
        # and source review; an LLM cannot publish itself through a tool/API.
        comparison, source_review = read(args.comparison), read(args.source_review)
        if comparison["input_sha256"] != digest(data):
            raise ValueError("comparison input changed")
        calls = [json.loads(v) for v in args.receipts.read_text().splitlines() if v.strip()]
        if review(data, prepare(data), calls) != comparison:
            raise ValueError("comparison differs from actual calls")
        if comparison["unattempted"] or any(c["state"] != "succeeded" for c in comparison["calls"]):
            raise ValueError("complete valid same-input comparison required before publication")
        if args.source_root is None:
            raise ValueError("publication requires collected official originals")
        documents = {}
        for packet in data:
            for document in packet["documents"]:
                url = document["url"]
                if url in documents and documents[url] != document:
                    raise ValueError("same original cannot carry conflicting collected evidence")
                documents[url] = document
        for doc in documents.values():
            path = args.source_root / doc["url"].rsplit("/", 1)[-1].lower()
            if not path.is_file() or not path.with_suffix(".received.json").is_file():
                raise ValueError("original missing; publication does not fetch")
        verified = collect(list(documents.values()), args.source_root)
        if verified != list(documents.values()):
            raise ValueError("packet facts differ from verified original evidence")
        for packet in data:
            sr = source_review[packet["id"]]
            if sr.get("net_improvement") is not True:
                continue
            call = next(c for c in calls if c["request_id"].startswith("company-") and
                        packet["id"] in next(r for r in prepare(data) if r["request_id"] == c["request_id"])["case_ids"])
            raw_analysis = next(v["analysis"] for v in decode(call["result"]) if v["id"] == packet["id"])
            # Human/operator source review may remove unsupported prose or qualify
            # it. Preserve the exact model output hash and an explicit change log;
            # never silently replace the failed raw response or grade it as passed.
            analysis = sr.get("approved_analysis", raw_analysis)
            if analysis != raw_analysis:
                corrections = sr.get("corrections")
                if (not isinstance(corrections, list) or not 1 <= len(corrections) <= 12
                        or any(not isinstance(v, str) or not v.strip() for v in corrections)):
                    raise ValueError("author corrections require explicit source-review notes")
            sr = {k: v for k, v in sr.items() if k != "approved_analysis"}
            sr = {**sr, "comparison_sha256": digest(comparison), "input_sha256": digest(packet),
                  "output_sha256": digest(analysis), "protocol_sha256": digest(messages(packet)),
                  "prompt_sha256": call["prompt_sha256"], "raw_output_sha256": digest(call["result"]),
                  "raw_analysis_sha256": digest(raw_analysis)}
            publish(packet, analysis, sr, model={"provider": call["provider"], "model": call["model"]},
                    generated_at=sr["generated_at"], root=args.out)


if __name__ == "__main__":
    main()
