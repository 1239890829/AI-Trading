"""TypeSafe Jev 的统一、最小权限适配层。

职责：
- 只接收窄而结构化的 Choice / Noul / Score 判断；
- 在发请求前拒绝明显凭据字段与密钥形态；
- 对外只暴露结构化答案、模型、usage、延迟与安全的错误类型；
- 只记录聚合 telemetry，绝不记录 state / questions 正文。

Jev 是语义判断层，不得替代价格计算、撮合、风控硬门或事实核验。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

_ALLOWED_TYPES = {"choice", "noul", "score"}
_SENSITIVE_KEYS = {
    "api_key", "apikey", "password", "secret", "authorization", "cookie",
    "access_token", "refresh_token", "private_key", "token",
}
_SECRET_VALUE = re.compile(
    r"(?:apikey_[A-Za-z0-9_\-]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_\-]{20,})"
)

_lock = threading.Lock()
_usage_log_lock = threading.Lock()
_metrics: dict[str, Any] = {
    "calls": 0, "ok": 0, "failed": 0, "skipped": 0,
    "input_tokens": 0, "output_tokens": 0, "latency_ms": 0.0,
    "purposes": defaultdict(lambda: {"calls": 0, "ok": 0, "failed": 0, "input_tokens": 0}),
    "comparisons": defaultdict(lambda: {
        "total": 0, "agree": 0, "disagree": 0, "confidence_sum": 0.0, "confidence_count": 0
    }),
    "routing": defaultdict(lambda: {
        "observations": 0, "baseline_items": 0, "selected_items": 0,
        "coverage_checked": 0, "coverage_missed": 0,
    }),
}
class JevError(RuntimeError):
    """Jev 请求/响应不满足项目契约。"""


def _settings():
    from app.core.config import settings
    return settings


def _api_key() -> str:
    # 全局 Codex/本机通过 Keychain 注入；部署环境也可显式提供。
    return (os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY") or "").strip()


def _walk_sensitive(value: Any, path: str = "state") -> None:
    """发现疑似凭据即拒绝整次外发；宁可少用 Jev，也不带 secret。"""
    if isinstance(value, dict):
        for key, child in value.items():
            k = str(key).strip().lower()
            normalized = k.replace("-", "_")
            if normalized in _SENSITIVE_KEYS or any(
                normalized.endswith("_" + s) for s in _SENSITIVE_KEYS
            ):
                raise JevError(f"sensitive_key:{path}.{key}")
            _walk_sensitive(child, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for idx, child in enumerate(value):
            _walk_sensitive(child, f"{path}[{idx}]")
        return
    if isinstance(value, str) and _SECRET_VALUE.search(value):
        raise JevError(f"secret_like_value:{path}")


def _validate_questions(questions: dict[str, dict]) -> None:
    if not isinstance(questions, dict) or not questions:
        raise JevError("questions_empty")
    for qid, question in questions.items():
        if not isinstance(qid, str) or not qid:
            raise JevError("question_id_invalid")
        if not isinstance(question, dict) or question.get("type") not in _ALLOWED_TYPES:
            raise JevError(f"question_type_invalid:{qid}")
        if not question.get("instructions"):
            raise JevError(f"question_instructions_missing:{qid}")
def _persist_usage(
    purpose: str,
    *,
    status: str,
    model: str | None = None,
    usage: dict | None = None,
    latency_ms: float = 0.0,
    reason: str | None = None,
) -> None:
    """Append metadata-only usage receipt; never write model input/output text."""
    cfg = _settings()
    if not bool(getattr(cfg, "jev_usage_log_enabled", False)):
        return
    path = Path(str(getattr(cfg, "jev_usage_log_path", "") or ""))
    if not str(path):
        return
    usage = usage or {}
    row = {
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": str(purpose or "unspecified")[:80],
        "status": str(status)[:24],
        "model": str(model or "")[:80] or None,
        "input_tokens": max(0, int(usage.get("input_tokens") or 0)),
        "output_tokens": max(0, int(usage.get("output_tokens") or 0)),
        "latency_ms": round(float(latency_ms or 0.0), 2),
        "reason": str(reason or "")[:120] or None,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _usage_log_lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except Exception as exc:  # telemetry must never break business behavior
        log.debug("Jev usage receipt skipped: %s", type(exc).__name__)


def _record(purpose: str, *, ok: bool = False, skipped: bool = False,
            usage: dict | None = None, latency_ms: float = 0.0,
            model: str | None = None, reason: str | None = None) -> None:
    usage = usage or {}
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    with _lock:
        _metrics["calls"] += 1
        _metrics["latency_ms"] += float(latency_ms)
        if skipped:
            _metrics["skipped"] += 1
        elif ok:
            _metrics["ok"] += 1
        else:
            _metrics["failed"] += 1
        _metrics["input_tokens"] += max(0, inp)
        _metrics["output_tokens"] += max(0, out)
        row = _metrics["purposes"][purpose or "unspecified"]
        row["calls"] += 1
        if ok:
            row["ok"] += 1
        elif not skipped:
            row["failed"] += 1
        row["input_tokens"] += max(0, inp)
    _persist_usage(
        purpose,
        status="skipped" if skipped else ("ok" if ok else "failed"),
        model=model,
        usage=usage,
        latency_ms=latency_ms,
        reason=reason,
    )


def metrics_snapshot() -> dict:
    """返回聚合统计；不含任何发给模型的正文。"""
    with _lock:
        calls = int(_metrics["calls"])
        return {
            "calls": calls,
            "ok": int(_metrics["ok"]),
            "failed": int(_metrics["failed"]),
            "skipped": int(_metrics["skipped"]),
            "input_tokens": int(_metrics["input_tokens"]),
            "output_tokens": int(_metrics["output_tokens"]),
            "avg_latency_ms": round(float(_metrics["latency_ms"]) / max(1, calls), 2),
            "purposes": {k: dict(v) for k, v in _metrics["purposes"].items()},
            "comparisons": {
                k: {
                    **dict(v),
                    "avg_confidence": (
                        round(float(v["confidence_sum"]) / int(v["confidence_count"]), 4)
                        if int(v["confidence_count"]) else None
                    ),
                }
                for k, v in _metrics["comparisons"].items()
            },
            "routing": {
                k: {
                    **dict(v),
                    "item_reduction_pct": round(
                        100.0 * (1.0 - float(v["selected_items"]) / max(1, int(v["baseline_items"]))),
                        2,
                    ),
                    "coverage_miss_rate": (
                        round(float(v["coverage_missed"]) / int(v["coverage_checked"]), 4)
                        if int(v["coverage_checked"]) else None
                    ),
                }
                for k, v in _metrics["routing"].items()
            },
        }


def historical_usage_summary(path: str | Path | None = None) -> dict:
    """Aggregate metadata receipts without network I/O; malformed lines are counted."""
    cfg = _settings()
    target = Path(path) if path is not None else Path(str(getattr(cfg, "jev_usage_log_path", "")))
    summary: dict[str, Any] = {
        "path": str(target), "calls": 0, "ok": 0, "failed": 0, "skipped": 0,
        "input_tokens": 0, "output_tokens": 0, "latency_ms_sum": 0.0,
        "malformed_lines": 0, "purposes": defaultdict(int), "models": defaultdict(int),
    }
    if not target.is_file():
        summary["avg_latency_ms"] = 0.0
        summary["purposes"] = {}
        summary["models"] = {}
        return summary
    for raw in target.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError("not_object")
            status = str(row.get("status") or "failed")
            summary["calls"] += 1
            if status in {"ok", "failed", "skipped"}:
                summary[status] += 1
            else:
                summary["failed"] += 1
            summary["input_tokens"] += max(0, int(row.get("input_tokens") or 0))
            summary["output_tokens"] += max(0, int(row.get("output_tokens") or 0))
            summary["latency_ms_sum"] += max(0.0, float(row.get("latency_ms") or 0.0))
            summary["purposes"][str(row.get("purpose") or "unspecified")] += 1
            if row.get("model"):
                summary["models"][str(row["model"])] += 1
        except Exception:
            summary["malformed_lines"] += 1
    summary["avg_latency_ms"] = round(
        float(summary.pop("latency_ms_sum")) / max(1, int(summary["calls"])), 2
    )
    summary["purposes"] = dict(sorted(summary["purposes"].items()))
    summary["models"] = dict(sorted(summary["models"].items()))
    return summary


def status_snapshot() -> dict:
    """零成本状态快照；只披露是否配置，绝不回传 key/base 响应正文。"""
    cfg = _settings()
    enabled = bool(getattr(cfg, "jev_enabled", True))
    configured = bool(_api_key())
    state = "ready" if enabled and configured else ("disabled" if not enabled else "not_configured")
    return {
        "state": state,
        "model": str(getattr(cfg, "jev_model", "jev-latest")),
        "modes": {
            "alert_triage": str(getattr(cfg, "jev_alert_triage_mode", "off")),
            "event_aux": str(getattr(cfg, "jev_event_aux_mode", "off")),
            "assistant_tools": str(getattr(cfg, "jev_assistant_tool_mode", "off")),
            "assistant_verify": str(getattr(cfg, "jev_assistant_verify_mode", "off")),
        },
        "usage_log_enabled": bool(getattr(cfg, "jev_usage_log_enabled", False)),
        "metrics": metrics_snapshot(),
    }


def _reset_metrics_for_tests() -> None:
    with _lock:
        _metrics.update(calls=0, ok=0, failed=0, skipped=0,
                        input_tokens=0, output_tokens=0, latency_ms=0.0)
        _metrics["purposes"] = defaultdict(
            lambda: {"calls": 0, "ok": 0, "failed": 0, "input_tokens": 0}
        )
        _metrics["comparisons"] = defaultdict(
            lambda: {
                "total": 0, "agree": 0, "disagree": 0,
                "confidence_sum": 0.0, "confidence_count": 0,
            }
        )
        _metrics["routing"] = defaultdict(
            lambda: {
                "observations": 0, "baseline_items": 0, "selected_items": 0,
                "coverage_checked": 0, "coverage_missed": 0,
            }
        )


def record_comparison(
    purpose: str,
    structured_choice: str,
    reference_choice: str,
    *,
    confidence: float | None = None,
) -> None:
    """只记两层输出是否一致；不保存输入正文或具体事件身份。

    Noul 没有独立 confidence，调用方应传 None；Choice/Score 有真实
    confidence 时才累计到 avg_confidence，避免把概率离 0.5 的距离冒充置信度。
    """
    same = str(structured_choice) == str(reference_choice)
    with _lock:
        row = _metrics["comparisons"][purpose or "unspecified"]
        row["total"] += 1
        row["agree" if same else "disagree"] += 1
        if confidence is not None:
            row["confidence_sum"] += max(0.0, min(1.0, float(confidence)))
            row["confidence_count"] += 1


def record_routing_observation(
    purpose: str,
    baseline_items: int,
    selected_items: int,
    *,
    coverage_ok: bool | None = None,
) -> None:
    """记录候选集合缩减与实际消费覆盖，不保存候选名或用户正文。"""
    baseline = max(0, int(baseline_items))
    selected = max(0, min(baseline, int(selected_items)))
    with _lock:
        row = _metrics["routing"][purpose or "unspecified"]
        row["observations"] += 1
        row["baseline_items"] += baseline
        row["selected_items"] += selected
        if coverage_ok is not None:
            row["coverage_checked"] += 1
            if not coverage_ok:
                row["coverage_missed"] += 1


def _post(url: str, key: str, payload: dict, timeout: float) -> httpx.Response:
    return httpx.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout,
    )


def evaluate(
    state: Any,
    questions: dict[str, dict],
    *,
    purpose: str,
    model: str | None = None,
) -> dict:
    """同步调用 Jev；失败返回显式状态，不抛出正文或凭据。

    调用方应根据 ok / reason 决定升级 DeepSeek、规则路径或人工复核。
    """
    cfg = _settings()
    requested_model = model or str(getattr(cfg, "jev_model", "jev-latest"))
    if not bool(getattr(cfg, "jev_enabled", True)):
        _record(purpose, skipped=True, model=requested_model, reason="disabled")
        return {"ok": False, "skipped": True, "reason": "disabled"}

    key = _api_key()
    if not key:
        _record(purpose, skipped=True, model=requested_model, reason="not_configured")
        return {"ok": False, "skipped": True, "reason": "not_configured"}

    try:
        _validate_questions(questions)
        _walk_sensitive(state)
        _walk_sensitive(questions, "questions")
        raw = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        max_chars = int(getattr(cfg, "jev_max_state_chars", 20_000))
        if len(raw) > max_chars:
            raise JevError(f"state_too_large:{len(raw)}>{max_chars}")
    except JevError as exc:
        _record(purpose, skipped=True, model=requested_model, reason=str(exc))
        return {"ok": False, "skipped": True, "reason": str(exc)}

    chosen_model = requested_model
    base = str(getattr(cfg, "jev_base_url", "https://api.typesafe.ai")).rstrip("/")
    timeout = float(getattr(cfg, "jev_timeout_seconds", 8.0))
    payload = {"model": chosen_model, "state": state, "questions": questions}
    started = time.perf_counter()
    response: httpx.Response | None = None
    try:
        for attempt in range(3):
            response = _post(f"{base}/v1/systemone", key, payload, timeout)
            if response.status_code not in {429, 503, 529} or attempt == 2:
                break
            time.sleep(0.25 * (2 ** attempt))
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if response is None or response.status_code >= 400:
            status = response.status_code if response is not None else "none"
            reason = f"http_{status}"
            _record(
                purpose, latency_ms=latency_ms, model=chosen_model, reason=reason
            )
            return {"ok": False, "reason": reason, "latency_ms": latency_ms}
        data = response.json()
        answers = data.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise JevError("answer_shape_mismatch")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        actual_model = str(data.get("model") or chosen_model)
        _record(
            purpose, ok=True, usage=usage, latency_ms=latency_ms, model=actual_model
        )
        return {
            "ok": True,
            "model": actual_model,
            "answers": answers,
            "usage": usage,
            "latency_ms": latency_ms,
        }
    except (httpx.HTTPError, ValueError, JevError) as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        reason = str(exc) if isinstance(exc, JevError) else type(exc).__name__
        _record(
            purpose, latency_ms=latency_ms, model=chosen_model, reason=reason
        )
        log.info("Jev unavailable purpose=%s reason=%s", purpose, reason)
        return {"ok": False, "reason": reason, "latency_ms": latency_ms}
