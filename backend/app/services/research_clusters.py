"""Evidence-bound research grouping above unchanged source identities.

This is a reading projection, never a ranking, selection or trading input.  An
industry-structure relation is deliberately separate from today's market-driver
claim.  Original THS reason labels remain literal, including secondary labels.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re

from app.core.bjtime import BJ_TZ


METHODOLOGY_VERSION = "evidence-research-clusters-v1"
_EVIDENCE_FILE = Path(__file__).resolve().parents[1] / "research" / "theme_cluster_evidence.json"
_SHA = re.compile(r"[0-9a-f]{64}")


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def content_digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _clock(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("aware observation clock required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("aware observation clock required") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("aware observation clock required")
    return result


def validate_day(value: str) -> str:
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("ISO trade date required")
    return value


def _finite(value):
    return float(value) if (type(value) in (int, float) and math.isfinite(value)) else None


def _version_content(value):
    """Receive clocks and read mode never manufacture another content version."""
    if isinstance(value, dict):
        return {key: _version_content(item) for key, item in value.items()
                if key not in {"version", "observed_at", "received_at", "reference_mode", "catalog_version"}}
    if isinstance(value, list):
        return [_version_content(item) for item in value]
    return value


def snapshot_version(payload: dict) -> str:
    return content_digest(_version_content(payload))


def empty_research_clusters(trade_date: str, *, state: str = "not_recorded",
                            reference_mode: str = "not_recorded",
                            reason: str = "historical_cluster_not_recorded") -> dict:
    validate_day(trade_date)
    if state not in {"not_recorded", "unavailable"} or reference_mode not in {"saved", "not_recorded"}:
        raise ValueError("invalid empty snapshot state")
    return {"scope": "research_theme_clusters", "methodology_version": METHODOLOGY_VERSION,
            "version": None, "observed_at": None, "trade_date": trade_date,
            "reference_mode": reference_mode, "state": state, "clusters": [],
            "coverage": {"source_scope": "limit_up_sample", "source_count": 0,
                         "tagged_count": 0, "unique_stock_count": 0, "complete_universe": False},
            "missing": [reason]}


def _valid_official_identity(name, row, observed_at: str) -> bool:
    if (not isinstance(name, str) or not isinstance(row, dict)
            or row.get("name") != name or row.get("source") != "ths_official"
            or row.get("kind") not in {"concept", "industry"} or row.get("basis") != "exact_name"
            or not isinstance(row.get("code"), str) or not re.fullmatch(r"[0-9]{6}\.TI", row["code"])
            or not isinstance(row.get("catalog_version"), str) or not _SHA.fullmatch(row["catalog_version"])):
        return False
    try:
        return _clock(row.get("observed_at")) <= _clock(observed_at)
    except (ValueError, TypeError):
        return False


def _catalog(resolution: dict, observed_at: str) -> tuple[dict[str, dict], str, list[str]]:
    if not isinstance(resolution, dict) or resolution.get("state") != "available":
        return {}, content_digest({"state": "unavailable"}), ["formal_catalog_unavailable"]
    source = resolution.get("identities")
    if not isinstance(source, dict) or not source:
        return {}, content_digest({"state": "unavailable"}), ["formal_catalog_unavailable"]
    raw_ambiguous = resolution.get("ambiguous") or []
    if not isinstance(raw_ambiguous, list) or any(not isinstance(name, str) for name in raw_ambiguous):
        return {}, content_digest({"state": "unavailable"}), ["formal_catalog_unavailable"]
    ambiguous = set(raw_ambiguous)
    identities, invalid = {}, []
    for name, row in source.items():
        if name in ambiguous:
            continue
        if not _valid_official_identity(name, row, observed_at):
            invalid.append(name if isinstance(name, str) else "invalid_name")
            continue
        identities[name] = {key: deepcopy(row[key]) for key in
                            ("code", "name", "source", "kind", "basis", "catalog_version", "observed_at")
                            if key in row}
    content = [{key: row[key] for key in ("code", "name", "source", "kind")}
               for _name, row in sorted(identities.items())]
    missing = (["formal_catalog_partially_invalid"] if invalid else []) + (["formal_catalog_ambiguous"] if ambiguous else [])
    if not identities:
        missing.append("formal_catalog_unavailable")
    return identities, content_digest({"identities": content, "ambiguous": sorted(ambiguous),
                                      "invalid_names": sorted(invalid)}), missing


def _source_rows(records: list[dict], trade_date: str, observed_at: str) -> tuple[list[dict], list[str], str]:
    if not isinstance(records, list) or len(records) > 10000:
        raise ValueError("bounded source observation list required")
    by_symbol, conflicted, missing = {}, set(), set()
    content = []
    for record in records:
        if not isinstance(record, dict):
            missing.add("invalid_source_observation")
            content.append({"invalid_record": True})
            continue
        sym, reason, source = record.get("symbol"), record.get("reason"), record.get("source")
        row_day = record.get("trade_date")
        if isinstance(row_day, date):
            row_day = row_day.isoformat()
        quality = getattr(record.get("quality"), "value", record.get("quality"))
        clean = {"symbol": sym if isinstance(sym, str) else None,
                 "name": record.get("name") if isinstance(record.get("name"), str) else None,
                 "reason": reason if isinstance(reason, str) else None,
                 "source": source if isinstance(source, str) else None,
                 "trade_date": row_day if isinstance(row_day, str) else None,
                 "quality": quality if isinstance(quality, str) else None,
                 "change_pct": _finite(record.get("change_pct"))}
        content.append(clean)
        if source != "ths":
            missing.add("non_ths_reason_observation_excluded")
            continue
        if not isinstance(sym, str) or not re.fullmatch(r"[0-9]{6}", sym):
            missing.add("invalid_source_symbol")
            continue
        if row_day is None:
            missing.add("source_trade_date_unknown")
            continue
        if row_day != trade_date:
            missing.add("source_trade_date_mismatch")
            continue
        if quality is None or quality not in {"high", "medium", "low", "stale", "invalid"}:
            missing.add("source_quality_unknown")
            continue
        if quality in {"stale", "invalid", "low"}:
            missing.add("unusable_source_quality")
            continue
        if reason is not None and (not isinstance(reason, str) or len(reason) > 12000):
            missing.add("invalid_source_reason")
            continue
        tags = sorted({part.strip() for part in (reason or "").split("+") if part.strip()})
        if not tags:
            missing.add("source_reason_missing")
        if any(len(tag) > 128 for tag in tags):
            missing.add("invalid_source_reason")
            continue
        received = record.get("received_at")
        if isinstance(received, datetime):
            received = received.isoformat()
        if received is not None:
            try:
                if _clock(received) > _clock(observed_at):
                    raise ValueError("source receive clock is after capture")
            except (ValueError, TypeError):
                missing.add("source_receive_clock_invalid")
                continue
        row = {"symbol": sym, "name": clean["name"], "raw_tags": tags,
               "reason": reason or "", "source": source, "change_pct": clean["change_pct"],
               "received_at": received}
        if sym in by_symbol and _version_content(by_symbol[sym]) != _version_content(row):
            conflicted.add(sym)
            missing.add("conflicting_duplicate_symbol_excluded")
        elif sym in by_symbol:
            prior_received = by_symbol[sym].get("received_at")
            if received is not None and (prior_received is None or _clock(received) < _clock(prior_received)):
                by_symbol[sym] = row
        else:
            by_symbol[sym] = row
    rows = [row for sym, row in sorted(by_symbol.items()) if sym not in conflicted]
    ordered = sorted(content, key=lambda item: canonical(item))
    return rows, sorted(missing), content_digest(ordered)


def _reason_evidence(rows: list[dict], observed_at: str) -> list[dict]:
    return [{"id": "reason-" + content_digest({"symbol": row["symbol"], "reason": row["reason"],
                                               "source": row["source"]})[:24],
             "source": "ths", "url": None, "published_at": None,
             "observed_at": observed_at, "excerpt": row["reason"],
             "evidence_type": "source_reason_observation"} for row in rows]


def _branch(name: str, rows: list[dict], identities: dict, relation: str, refs: list[str]) -> dict:
    return {"raw_name": name, "official_identity": deepcopy(identities.get(name)),
            "relation": relation, "symbols": sorted(row["symbol"] for row in rows),
            "evidence_ids": sorted(refs)}


def build_research_clusters(records: list[dict], *, trade_date: str, observed_at: str,
                            resolution: dict) -> dict:
    """Build a deterministic, additive projection; no IO except reviewed evidence.

    A source-reason sample is not a full thematic universe.  Neither price rises
    nor labels confirm company revenue/orders or a shared contemporary catalyst.
    """
    validate_day(trade_date)
    observed = _clock(observed_at)
    if date.fromisoformat(trade_date) > observed.astimezone(BJ_TZ).date():
        raise ValueError("future trade date cannot be observed")
    identities, catalog_hash, missing = _catalog(resolution, observed_at)
    rows, source_missing, source_hash = _source_rows(records, trade_date, observed_at)
    missing.extend(source_missing)
    tagged = [row for row in rows if row["raw_tags"]]
    by_tag: dict[str, list[dict]] = {}
    for row in tagged:
        for tag in row["raw_tags"]:
            by_tag.setdefault(tag, []).append(row)

    specification = json.loads(_EVIDENCE_FILE.read_text(encoding="utf-8"))
    publication = specification["evidence"]
    can_observe_publication = date.fromisoformat(publication["published_at"]) <= observed.astimezone(BJ_TZ).date()
    anchors = set(specification["anchor_tags"])
    anchor_rows = [row for row in tagged if anchors.intersection(row["raw_tags"])]
    anchor_symbols = {row["symbol"] for row in anchor_rows}
    cluster_branches = []
    used_tags = set()
    if can_observe_publication and anchor_rows:
        refs = _reason_evidence(anchor_rows, observed_at)
        for tag in sorted(anchors.intersection(by_tag)):
            chosen = by_tag[tag]
            cluster_branches.append(_branch(tag, chosen, identities, "whole", [publication["id"],
                                           *[ref["id"] for ref in _reason_evidence(chosen, observed_at)]]))
        for tag in sorted(set(specification["partial_tags"]).intersection(by_tag)):
            chosen = [row for row in by_tag[tag] if row["symbol"] in anchor_symbols]
            if chosen:
                cluster_branches.append(_branch(tag, chosen, identities, "partial", [publication["id"],
                                               *[ref["id"] for ref in _reason_evidence(chosen, observed_at)]]))
        # A lone official label stays available as itself; no synthetic duplicate.
        if len(cluster_branches) < 2:
            cluster_branches = []

    clusters = []
    if cluster_branches:
        used_tags = {branch["raw_name"] for branch in cluster_branches}
        clusters.append({"id": "research-industry-" + content_digest({"name": specification["cluster_name"],
                          "evidence": publication["id"]})[:24],
                         "name": specification["cluster_name"], "type": "industry_chain",
                         "state": "supported", "support_scope": "industry_structure",
                         "driver_state": "candidate", "driver": "已核产业结构；当日共同驱动待核",
                         "branches": cluster_branches, "stocks": deepcopy(anchor_rows),
                         "evidence": [{**deepcopy(publication), "observed_at": observed_at}, *refs],
                         "missing": ["event_to_company_driver_not_verified", "full_theme_universe_not_observed",
                                     "common_constituent_and_market_effect_not_removed"],
                         "counter_evidence": list(specification["limits"]),
                         "unique_stock_count": len(anchor_rows), "change_pct": None})

    for tag, members in sorted(by_tag.items()):
        # Independently named official boards remain in the original official
        # directory, not another synthetic research board for each formal label.
        if tag in identities:
            continue
        # Even a partial structural link must not swallow other observed stocks.
        # Keep the complete literal source theme as a separate candidate.
        evidence = _reason_evidence(members, observed_at)
        related = tag in used_tags
        clusters.append({"id": "research-narrative-" + content_digest({"source": "ths", "raw_name": tag})[:24],
                         "name": tag, "type": "market_narrative", "state": "candidate",
                         "support_scope": "market_narrative", "driver_state": "candidate",
                         "driver": "来源交易标签；共同催化和完整成员待核",
                         "branches": [_branch(tag, members, identities, "candidate", [e["id"] for e in evidence])],
                         "stocks": deepcopy(members), "evidence": evidence,
                         "missing": ["formal_catalog_status_unknown" if any(reason.startswith("formal_catalog_") for reason in missing)
                                     else "no_exact_formal_catalog_identity", "shared_market_driver_not_verified",
                                     "full_theme_universe_not_observed", "source_quote_clock_unavailable"],
                         "counter_evidence": ["涨停原因是源方归因，不是独立因果证明", "只观察涨停样本，未观察其他成员及未上涨样本"]
                                             + (["产业关联只覆盖部分观察股，未合并该原始标签"] if related else []),
                         "unique_stock_count": len(members), "change_pct": None})

    clusters.sort(key=lambda row: (row["type"] != "industry_chain", row["name"], row["id"]))
    ambiguity = (resolution.get("ambiguous") or []) if isinstance(resolution, dict) else []
    ambiguous = set(ambiguity) if isinstance(ambiguity, list) and all(isinstance(name, str) for name in ambiguity) else set()
    for cluster in clusters:
        for branch in cluster["branches"]:
            if branch["official_identity"] is not None:
                branch.update(catalog_mapping_state="exact", missing=[])
            elif branch["raw_name"] in ambiguous:
                branch.update(catalog_mapping_state="ambiguous",
                              missing=["formal_catalog_status_unknown", "formal_catalog_name_ambiguous"])
            elif any(reason in missing for reason in ("formal_catalog_unavailable", "formal_catalog_partially_invalid")):
                branch.update(catalog_mapping_state="unavailable", missing=["formal_catalog_status_unknown"])
            else:
                branch.update(catalog_mapping_state="unmapped", missing=["no_exact_formal_catalog_identity"])
        if cluster["type"] == "market_narrative":
            cluster["missing"] = [*cluster["branches"][0]["missing"], *cluster["missing"][1:]]
    if tagged:
        missing.extend(["full_theme_universe_not_observed", "current_common_driver_not_verified",
                        "source_quote_clock_unavailable"])
    payload = {"scope": "research_theme_clusters", "methodology_version": METHODOLOGY_VERSION,
               "version": None, "observed_at": observed_at, "trade_date": trade_date,
               "reference_mode": "current", "state": "unavailable" if records and not rows else
               "partial" if missing else "ready", "clusters": clusters,
               "coverage": {"source_scope": "limit_up_sample", "source_count": len(records),
                            "tagged_count": len(tagged), "unique_stock_count": len(rows), "complete_universe": False},
               "catalog_content_sha256": catalog_hash, "source_content_sha256": source_hash,
               "source_hash_basis": "normalized_reason_observations_v1",
               "evidence_version": specification["version"], "missing": sorted(set(missing))}
    payload["version"] = snapshot_version(payload)
    return payload


def validate_snapshot(payload: dict) -> None:
    """Reject corrupted/incompatible saved snapshots rather than replacing them."""
    if (not isinstance(payload, dict) or payload.get("scope") != "research_theme_clusters"
            or payload.get("methodology_version") != METHODOLOGY_VERSION
            or payload.get("reference_mode") not in {"current", "saved"}
            or payload.get("state") not in {"ready", "partial", "unavailable"}
            or not isinstance(payload.get("version"), str) or not _SHA.fullmatch(payload["version"])
            or not isinstance(payload.get("clusters"), list)
            or snapshot_version(payload) != payload["version"]):
        raise ValueError("research cluster snapshot integrity mismatch")
    validate_day(payload.get("trade_date"))
    _clock(payload.get("observed_at"))
    coverage = payload.get("coverage")
    if (not isinstance(coverage, dict) or coverage.get("source_scope") != "limit_up_sample"
            or coverage.get("complete_universe") is not False
            or any(type(coverage.get(key)) is not int or coverage[key] < 0 for key in
                   ("source_count", "tagged_count", "unique_stock_count"))
            or not coverage["tagged_count"] <= coverage["unique_stock_count"] <= coverage["source_count"]):
        raise ValueError("invalid research source coverage")
    cluster_ids, observed_stocks = set(), {}
    specification = json.loads(_EVIDENCE_FILE.read_text(encoding="utf-8"))
    publication = specification["evidence"]
    for cluster in payload["clusters"]:
        if (not isinstance(cluster, dict) or not isinstance(cluster.get("id"), str)
                or cluster["id"] in cluster_ids or cluster.get("change_pct") is not None
                or cluster.get("driver_state") != "candidate"
                or cluster.get("state") not in {"supported", "candidate"}
                or cluster.get("type") not in {"industry_chain", "market_narrative"}
                or not isinstance(cluster.get("stocks"), list) or not cluster["stocks"]
                or not isinstance(cluster.get("branches"), list) or not cluster["branches"]
                or not isinstance(cluster.get("evidence"), list)
                or cluster.get("unique_stock_count") != len(cluster.get("stocks", []))):
            raise ValueError("invalid research cluster semantics")
        cluster_ids.add(cluster["id"])
        symbols = set()
        for row in cluster["stocks"]:
            if (not isinstance(row, dict) or row.get("source") != "ths"
                    or not isinstance(row.get("symbol"), str) or not re.fullmatch(r"[0-9]{6}", row["symbol"])
                    or row["symbol"] in symbols or not isinstance(row.get("reason"), str)
                    or row.get("raw_tags") != sorted({part.strip() for part in row["reason"].split("+") if part.strip()})
                    or (row.get("change_pct") is not None and _finite(row["change_pct"]) is None)):
                raise ValueError("invalid research source stock")
            symbols.add(row["symbol"])
            if row.get("received_at") is not None and _clock(row["received_at"]) > _clock(payload["observed_at"]):
                raise ValueError("research source receipt is after capture")
            if row["symbol"] in observed_stocks and observed_stocks[row["symbol"]] != row:
                raise ValueError("research source stock conflicts between groups")
            observed_stocks[row["symbol"]] = row
        if any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in cluster["evidence"]):
            raise ValueError("invalid research evidence identity")
        evidence = {row["id"]: row for row in cluster["evidence"]}
        if len(evidence) != len(cluster["evidence"]):
            raise ValueError("duplicate research evidence identity")
        expected = {row["id"]: row for row in _reason_evidence(cluster["stocks"], payload["observed_at"])}
        structural = cluster["type"] == "industry_chain"
        if structural:
            expected[publication["id"]] = {**publication, "observed_at": payload["observed_at"]}
            expected_id = "research-industry-" + content_digest({"name": specification["cluster_name"],
                                                                 "evidence": publication["id"]})[:24]
            if (cluster["id"] != expected_id or cluster.get("name") != specification["cluster_name"]
                    or cluster.get("support_scope") != "industry_structure" or cluster["state"] != "supported"
                    or len(cluster["branches"]) < 2
                    or date.fromisoformat(publication["published_at"]) > _clock(payload["observed_at"]).astimezone(BJ_TZ).date()
                    or any(not set(specification["anchor_tags"]).intersection(row["raw_tags"])
                           for row in cluster["stocks"])):
                raise ValueError("invalid reviewed industry scope")
        if evidence != expected:
            raise ValueError("research evidence does not bind the observed source stocks")
        branch_names, branch_union = set(), set()
        for branch in cluster["branches"]:
            if (not isinstance(branch, dict) or branch.get("relation") not in {"whole", "partial", "candidate"}
                    or not isinstance(branch.get("raw_name"), str) or not isinstance(branch.get("symbols"), list)
                    or branch["raw_name"] in branch_names or not branch["symbols"]
                    or branch["symbols"] != sorted(set(branch["symbols"]))
                    or not set(branch["symbols"]) <= symbols
                    or not isinstance(branch.get("evidence_ids"), list)
                    or branch["evidence_ids"] != sorted(set(branch["evidence_ids"]))):
                raise ValueError("invalid research branch scope")
            branch_names.add(branch["raw_name"])
            branch_union.update(branch["symbols"])
            chosen = [row for row in cluster["stocks"] if branch["raw_name"] in row["raw_tags"]]
            expected_refs = [row["id"] for row in _reason_evidence(chosen, payload["observed_at"])]
            if structural:
                expected_refs.append(publication["id"])
                relation = "whole" if branch["raw_name"] in specification["anchor_tags"] else "partial"
                allowed = set(specification["anchor_tags"]) | set(specification["partial_tags"])
                if branch["raw_name"] not in allowed or branch["relation"] != relation:
                    raise ValueError("unreviewed industry branch")
            if (branch["symbols"] != sorted(row["symbol"] for row in chosen)
                    or branch["evidence_ids"] != sorted(expected_refs)):
                raise ValueError("branch evidence does not bind every scoped stock")
            identity = branch.get("official_identity")
            if identity is not None and not _valid_official_identity(branch["raw_name"], identity, payload["observed_at"]):
                raise ValueError("invalid exact source identity")
            mapping_state = branch.get("catalog_mapping_state")
            if (mapping_state not in {"exact", "ambiguous", "unmapped", "unavailable"}
                    or (identity is not None) != (mapping_state == "exact")
                    or not isinstance(branch.get("missing"), list)
                    or (mapping_state != "exact" and not branch["missing"])
                    or (mapping_state == "ambiguous" and "formal_catalog_name_ambiguous" not in branch["missing"])):
                raise ValueError("invalid research branch directory uncertainty")
        if branch_union != symbols:
            raise ValueError("unscoped research source stock")
        if cluster["type"] == "market_narrative" and (cluster["state"] != "candidate"
                or cluster.get("support_scope") != "market_narrative"
                or len(cluster["branches"]) != 1 or cluster["branches"][0]["raw_name"] != cluster.get("name")
                or cluster["branches"][0]["relation"] != "candidate"
                or cluster["id"] != "research-narrative-" + content_digest({"source": "ths", "raw_name": cluster["name"]})[:24]):
            raise ValueError("market source labels cannot silently merge")
    if len(observed_stocks) > coverage["unique_stock_count"]:
        raise ValueError("research groups exceed source coverage")
