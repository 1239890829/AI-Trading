"""战法核验结论的登记与回读（S2-11 闭环）。

**为什么需要这一层**（不是给 `strategy_verify.py` 硬造生产调用）：

`app/research/strategy_verify.py` 是**离线重计算**——`build()` 要在 duckdb 上跑全历史
特征表（数千万行级），它**不该、也不能进请求链**。此前它的结论只有两个去处：
① 脚本 stdout（跑完即散）；② 人工誊写进 `picks/strategy_registry.py` 的
`StrategySpec.note`（"五步全通过 −0.51%、胜率 39.3% ⇒ 已否决"）。

后果是 **状态无背书**：`status` 可以随意改，测试守卫只查"键集合与文档一致"，
查不出"这个 ⛔ 到底有没有跑过核验"。要回查只能翻 git 历史或文档。

本模块补的正是这一环：**落盘 → 读回**，让登记册的状态背后有一份带时间戳、
带关键统计量的可回查产物。核验器本身保持纯计算、离线触发，职责不变。

**三态纪律**（与 P1-3 / `factors/report.py` 同口径，缓存层不吞业务决策）：
- 产物缺失或 JSON 损坏 → `available=False` + `reason`（**不是**静默返回空）
- 产物存在但超期 → `available=True` + `stale=True` + `age_days`，由消费方决定降级
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.bjtime import beijing_now_naive
from app.research.strategy_verify import (
    GATE_VERSION,
    VERDICT_OBSERVE,
    VERDICT_PASS,
    VERDICT_REJECT,
    validation_protocol_issues,
)

#: 核验产物目录（`backend/data/research/verify/<strategy_key>.json`）
VERIFY_DIR = Path(__file__).resolve().parents[2] / "data" / "research" / "verify"

#: 超期阈值（天）。战法结论不随行情逐日失效，故比因子 IC 的 40 天宽松；
#: 超期**只标注不删除**——历史结论本身是有价值的证据链。
DEFAULT_MAX_AGE_DAYS = 180

__all__ = [
    "VERDICT_OBSERVE",
    "VERDICT_PASS",
    "VERDICT_REJECT",
    "VERIFY_DIR",
    "DEFAULT_MAX_AGE_DAYS",
    "list_records",
    "load_record",
    "save_record",
    "load_history",
    "verification_of",
]


def _path_for(key: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (key or "").strip())
    if not safe:
        raise ValueError("strategy key 不能为空")
    return VERIFY_DIR / f"{safe}.json"


def _history_of(record: dict) -> list[dict]:
    history = record.get("history", [])
    if not isinstance(history, list) or not all(isinstance(item, dict) for item in history):
        raise ValueError("verification history 损坏，拒绝覆盖证据")
    return history


def _current_without_history(record: dict) -> dict:
    return {key: value for key, value in record.items() if key != "history"}


def save_record(
    key: str, *, verdict: str, headline: str,
    metrics: dict[str, Any] | None = None, sample: dict[str, Any] | None = None,
    source: str = "", cost_bps: float | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Atomically write latest evidence while retaining every prior current record."""
    if verdict not in (VERDICT_PASS, VERDICT_OBSERVE, VERDICT_REJECT):
        raise ValueError(f"verdict 必须是 pass/observe/reject，收到 {verdict!r}")
    path = _path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    gate = (extra or {}).get("gate")
    if isinstance(gate, dict) and gate.get("gate_version") == GATE_VERSION:
        protocol = gate.get("validation_protocol")
        if not isinstance(protocol, dict):
            raise ValueError("current gate 缺 validation_protocol")
        recomputed = validation_protocol_issues(metrics or {}, protocol)
        if recomputed != gate.get("protocol_issues"):
            raise ValueError("current gate 与 metrics/protocol 不一致，拒绝落盘")
    history: list[dict] = []
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("existing verification record 损坏，拒绝覆盖") from exc
        if not isinstance(previous, dict) or previous.get("key") not in (None, key):
            raise ValueError("existing verification record 无效，拒绝覆盖")
        history = [*_history_of(previous), _current_without_history(previous)]
    payload = {
        "key": key, "verdict": verdict, "headline": headline,
        "metrics": metrics or {}, "sample": sample or {}, "source": source,
        "cost_bps": cost_bps if cost_bps is not None else (metrics or {}).get("cost_bps"),
        "recorded_at": beijing_now_naive().isoformat(timespec="seconds"),
    }
    if extra:
        payload.update(extra)
    if history:
        payload["history"] = history
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)
    return path


def load_record(key: str) -> dict | None:
    """读回一份核验结论。缺失/损坏一律返回 `None`——**不抛异常**，
    因为调用方多在聚合路径上，一处坏文件不该让整页 500。"""
    path = _path_for(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_history(key: str) -> list[dict]:
    record = load_record(key)
    if record is None:
        return []
    return list(_history_of(record))


def _age_days(recorded_at: str | None) -> int | None:
    if not recorded_at:
        return None
    try:
        then = datetime.fromisoformat(recorded_at)
    except ValueError:
        return None
    return max((beijing_now_naive() - then).days, 0)


def _valid_legacy_gate_v2(record: dict, gate: dict) -> bool:
    failed, unchecked = gate.get("failed"), gate.get("unchecked")
    return bool(
        gate.get("gate_version") == 2
        and gate.get("scope") == "machine_checks_only"
        and gate.get("review_required") is True
        and gate.get("verdict") == record.get("verdict")
        and gate.get("verdict") in {VERDICT_PASS, VERDICT_OBSERVE, VERDICT_REJECT}
        and isinstance(failed, list) and isinstance(unchecked, list)
        and all(isinstance(v, str) for v in failed + unchecked)
        and gate.get("machine_checks_complete") is (not unchecked)
        and (gate.get("verdict") != VERDICT_PASS or not (failed or unchecked))
    )


def gate_evidence(record: dict) -> dict:
    """Expose current research-admission evidence without rewriting historical verdicts."""
    gate = record.get("gate")
    if gate is None:
        state = "legacy_unverified"
    elif isinstance(gate, dict) and gate.get("gate_version") == 2:
        state = "legacy_protocol_unverified" if _valid_legacy_gate_v2(record, gate) else "invalid"
    elif isinstance(gate, dict):
        failed = gate.get("failed")
        machine_unchecked = gate.get("machine_unchecked")
        protocol_issues = gate.get("protocol_issues")
        unchecked = gate.get("unchecked")
        protocol = gate.get("validation_protocol")
        eligible = gate.get("research_admission_eligible_for_review")
        machine_verdict = gate.get("machine_verdict")
        expected_unchecked = None
        if isinstance(machine_unchecked, list) and isinstance(protocol_issues, list):
            expected_unchecked = [
                *machine_unchecked,
                *(f"协议：{item}" for item in protocol_issues),
            ]
        protocol_identity_ok = (
            protocol is None
            or (
                isinstance(protocol, dict)
                and protocol.get("return_identity") == gate.get("return_identity")
            )
        )
        recomputed_protocol_issues = (
            validation_protocol_issues(record.get("metrics") or {}, protocol)
            if isinstance(protocol, dict) else None
        )
        protocol_recheck_ok = (
            isinstance(recomputed_protocol_issues, list)
            and recomputed_protocol_issues == protocol_issues
        )
        structurally_valid = (
            gate.get("gate_version") == GATE_VERSION
            and gate.get("scope") == "research_admission_machine_checks"
            and gate.get("review_required") is True
            and gate.get("verdict") == record.get("verdict")
            and gate.get("verdict") in {VERDICT_PASS, VERDICT_OBSERVE, VERDICT_REJECT}
            and machine_verdict in {VERDICT_PASS, VERDICT_OBSERVE, VERDICT_REJECT}
            and isinstance(failed, list)
            and isinstance(machine_unchecked, list)
            and isinstance(protocol_issues, list)
            and isinstance(unchecked, list)
            and all(
                isinstance(v, str)
                for v in failed + machine_unchecked + protocol_issues + unchecked
            )
            and expected_unchecked == unchecked
            and gate.get("machine_checks_complete") is (not machine_unchecked)
            and gate.get("protocol_complete") is (not protocol_issues)
            and protocol_identity_ok
            and protocol_recheck_ok
            and isinstance(eligible, bool)
            and isinstance(gate.get("production_effect_evidence_complete"), bool)
            and gate.get("production_promotion_eligible") is False
            and eligible is (
                gate.get("verdict") == VERDICT_PASS
                and gate.get("protocol_complete") is True
                and gate.get("machine_checks_complete") is True
                and not failed
            )
            and (
                gate.get("verdict") != VERDICT_PASS
                or eligible
            )
        )
        state = "recorded" if structurally_valid else "invalid"
    else:
        state = "invalid"

    current_gate = gate if state == "recorded" else None
    research_eligible = bool(
        current_gate and current_gate.get("research_admission_eligible_for_review") is True
    )
    raw_verdict = record.get("verdict")
    # 协议缺失/旧协议只阻止“通过”升级为准入；明确负效应 reject 不能被抬成 observe。
    if raw_verdict == VERDICT_PASS and not research_eligible:
        effective_verdict = VERDICT_OBSERVE
    else:
        effective_verdict = raw_verdict
    return {
        "gate": current_gate,
        "gate_evidence_state": state,
        "admission_eligible_for_review": research_eligible,
        "production_effect_evidence_complete": bool(
            current_gate and current_gate.get("production_effect_evidence_complete") is True
        ),
        "production_promotion_eligible": False,
        "effective_verdict": effective_verdict,
        "review_required": True,
    }


def verification_of(key: str, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """单个策略键的核验结论（三态）。

    返回 `{"available", "stale", "age_days", "recorded_at", "verdict",
    "headline", "metrics", "sample", "source", "reason"}`。
    """
    rec = load_record(key)
    if rec is None:
        return {
            "available": False,
            "gate": None, "gate_evidence_state": "absent",
            "admission_eligible_for_review": False,
            "production_effect_evidence_complete": False,
            "production_promotion_eligible": False,
            "effective_verdict": None,
            "review_required": True,
            "stale": None,
            "age_days": None,
            "recorded_at": None,
            "verdict": None,
            "headline": None,
            "metrics": {},
            "sample": {},
            "source": None,
            "reason": "尚无核验产物（未跑过 strategy_verify 或未落盘）",
        }
    age = _age_days(rec.get("recorded_at"))
    stale = age is None or age > max_age_days
    return {
        "available": True,
        **gate_evidence(rec),
        "stale": stale,
        "age_days": age,
        "max_age_days": max_age_days,
        "recorded_at": rec.get("recorded_at"),
        "verdict": rec.get("verdict"),
        "headline": rec.get("headline"),
        "metrics": rec.get("metrics") or {},
        "sample": rec.get("sample") or {},
        "source": rec.get("source"),
        "cost_bps": rec.get("cost_bps"),
        "reason": None,
    }


def list_records() -> list[dict]:
    """全量核验产物（按记录时间倒序）。目录不存在时返回空列表。"""
    if not VERIFY_DIR.exists():
        return []
    out: list[dict] = []
    for p in sorted(VERIFY_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    # 同秒写入时按 key 兜底，保证顺序确定（否则依赖 glob 的文件系统顺序）
    out.sort(key=lambda d: (d.get("recorded_at") or "", d.get("key") or ""), reverse=True)
    return out
