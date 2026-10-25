"""Derived saved-report summaries. List reads never open original report bodies."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import stat

from app.core.bjtime import beijing_now
from app.core.ttl_cache import TTLCache
from app.events.company_state import atomic_json

_CACHE = TTLCache("tradingagents-summary-manifest", ttl=60, maxsize=128)
_LIMIT = 64_000
_HASH = re.compile(r"[0-9a-f]{64}")
_RUN = re.compile(r"[0-9a-f]{32}")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _signature(path: Path) -> list[int]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError("invalid saved-summary source")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError("linked saved-summary source")
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _small(path: Path, limit: int) -> tuple[dict, str]:
    before = _signature(path)
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit or _signature(path) != before:
        raise ValueError("saved-summary source changed or oversized")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("saved-summary object required")
    return value, hashlib.sha256(raw).hexdigest()


def _pointer(folder: Path) -> tuple[dict, str]:
    pointer, digest = _small(folder / "latest.json", 4096)
    if not _RUN.fullmatch(str(pointer.get("run_id", ""))) or not _HASH.fullmatch(str(pointer.get("result_sha256", ""))):
        raise ValueError("invalid saved-report pointer")
    return pointer, digest


def _sources(run: Path) -> list[dict]:
    result = [{"path": ".", "signature": _signature(run)}]
    for name in ("result.json", "packet.json", "role-outputs.jsonl"):
        path = run / name
        if path.exists() or path.is_symlink():
            result.append({"path": name, "signature": _signature(path)})
    models = run / "model-outputs"
    if models.exists() or models.is_symlink():
        result.append({"path": "model-outputs", "signature": _signature(models)})
        files = sorted(models.glob("*.json"))
        if len(files) > 256:
            raise ValueError("saved-summary source range exceeded")
        for path in files:
            if not re.fullmatch(r"[1-9][0-9]*\.json", path.name):
                raise ValueError("invalid original model identity")
            result.append({"path": f"model-outputs/{path.name}", "signature": _signature(path)})
    return result


def _checked_summary(value: dict, symbol: str, run_id: str) -> dict:
    required = {"symbol", "run_id", "generated_at", "as_of", "input_sha256", "method_sha256", "upstream_sha",
                "report_count", "roles_count", "final_rating", "review_state", "role_projection_state", "selection_key"}
    if set(value) != required or value["symbol"] != symbol or value["run_id"] != run_id:
        raise ValueError("saved-summary identity mismatch")
    for key in ("input_sha256", "method_sha256", "upstream_sha"):
        if not _HASH.fullmatch(str(value[key])) and not (key == "upstream_sha" and re.fullmatch(r"[0-9a-f]{40}", str(value[key]))):
            raise ValueError("saved-summary version missing")
    generated, as_of = (datetime.fromisoformat(value[key].replace("Z", "+00:00")) for key in ("generated_at", "as_of"))
    if generated.utcoffset() is None or as_of.utcoffset() is None or not as_of <= generated <= beijing_now():
        raise ValueError("saved-summary time mismatch")
    if (type(value["report_count"]) is not int or value["report_count"] != 7
            or value["role_projection_state"] not in {"available", "unavailable"}
            or (type(value["roles_count"]) is not int or value["roles_count"] != 12
                if value["role_projection_state"] == "available" else value["roles_count"] is not None)
            or value["review_state"] not in {"unreviewed", "finite_failed_requires_review"}
            or value["selection_key"] is not None and not _HASH.fullmatch(str(value["selection_key"]))
            or value["final_rating"] is not None and (not isinstance(value["final_rating"], str) or not 0 < len(value["final_rating"]) <= 80)):
        raise ValueError("invalid saved-summary state")
    return value


def materialize(symbol: str, *, write: bool = False, expected_run_id: str | None = None,
                expected_root: Path | None = None) -> dict:
    """Explicit derivation from existing verified artifacts; no model, source or DB write."""
    from app.services import tradingagents_research as research

    if expected_root is not None and Path(expected_root).resolve() != research.ROOT.resolve():
        raise ValueError("publisher research store mismatch")
    folder = research._folder(symbol)
    pointer, pointer_sha = _pointer(folder)
    run_id = pointer["run_id"]
    if expected_run_id is not None and expected_run_id != run_id:
        raise ValueError("published report superseded")
    run = folder / run_id
    before = _sources(run)
    result = research._latest(symbol)
    if result is None or result["run_id"] != run_id:
        raise ValueError("saved report verification unavailable")
    roles, projection = research._role_projection(run, result)
    final = next((row for row in roles if row["role"] == "Portfolio Manager"), None)
    rating = final["fields"].get("rating") if final else None
    if not isinstance(rating, str) or not 0 < len(rating) <= 80:
        rating = None
    summary = _checked_summary({
        **{key: result[key] for key in ("symbol", "run_id", "generated_at", "as_of", "input_sha256", "method_sha256", "upstream_sha", "review_state")},
        "report_count": len(result["reports"]), "roles_count": len(roles) if projection == "available" else None,
        "final_rating": rating, "role_projection_state": projection,
        "selection_key": (result.get("selection") or {}).get("selection_key"),
    }, symbol, run_id)
    if _sources(run) != before or _pointer(folder) != (pointer, pointer_sha):
        raise ValueError("saved report changed during summary derivation")
    body = {"version": 1, "symbol": symbol, "run_id": run_id, "result_sha256": pointer["result_sha256"],
            "pointer_sha256": pointer_sha, "source_files": before, "summary": summary}
    manifest = {"body": body, "sha256": _digest(body)}
    target = folder / "derived" / "latest-summary.json"
    if target.parent.is_symlink() or target.is_symlink():
        raise ValueError("invalid derived summary path")
    if write:
        target.parent.mkdir(exist_ok=True)
        atomic_json(target, manifest)
    return {"symbol": symbol, "run_id": run_id, "summary_sha256": manifest["sha256"],
            "state": "written" if write else "dry_run", "summary": summary}


def read(symbol: str) -> dict:
    """Read only the pointer and small sidecar; validate source identities with stat."""
    from app.services import tradingagents_research as research

    known_run = None
    record = False
    try:
        folder = research._folder(symbol)
        pointer_path, sidecar = folder / "latest.json", folder / "derived" / "latest-summary.json"
        if not pointer_path.exists() and not pointer_path.is_symlink():
            return {"state": "unavailable" if sidecar.exists() else "missing", "has_record": sidecar.exists(),
                    "run_id": None, "summary": None, "reason": "保存摘要未绑定原报告指针" if sidecar.exists() else "未找到保存报告指针"}
        record = True
        if sidecar.parent.is_symlink():
            raise ValueError("invalid derived summary directory")
        pointer, pointer_sha = _pointer(folder)
        known_run = pointer["run_id"]
        key = (str(folder), tuple(_signature(pointer_path)), tuple(_signature(sidecar)))
        hit, body = _CACHE.get(key)
        if not hit:
            manifest, _sha = _small(sidecar, _LIMIT)
            body = manifest["body"]
            if not isinstance(body, dict) or manifest.get("sha256") != _digest(body):
                raise ValueError("saved-summary digest mismatch")
            if (set(body) != {"version", "symbol", "run_id", "result_sha256", "pointer_sha256", "source_files", "summary"}
                    or body["version"] != 1 or body["symbol"] != symbol or body["run_id"] != known_run
                    or body["result_sha256"] != pointer["result_sha256"] or body["pointer_sha256"] != pointer_sha):
                raise ValueError("saved-summary source mismatch")
            _checked_summary(body["summary"], symbol, known_run)
            if not isinstance(body["source_files"], list) or len(body["source_files"]) > 261:
                raise ValueError("invalid saved-summary source range")
            _CACHE.set(key, body)
        # This detects changed/corrupt originals without opening any of their bodies.
        if body["source_files"] != _sources(folder / known_run):
            raise ValueError("saved-summary original source changed")
        _checked_summary(body["summary"], symbol, known_run)
        return {"state": "available", "has_record": True, "run_id": known_run,
                "summary": deepcopy(body["summary"]), "reason": "已存派生摘要；完整原件按需重新核验"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {"state": "unavailable", "has_record": record, "run_id": known_run,
                "summary": None, "reason": "保存摘要缺失、损坏或来源版本已变化；完整研究记录待核"}
