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
    VERDICT_OBSERVE,
    VERDICT_PASS,
    VERDICT_REJECT,
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
    "verification_of",
]


def _path_for(key: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (key or "").strip())
    if not safe:
        raise ValueError("strategy key 不能为空")
    return VERIFY_DIR / f"{safe}.json"


def save_record(
    key: str,
    *,
    verdict: str,
    headline: str,
    metrics: dict[str, Any] | None = None,
    sample: dict[str, Any] | None = None,
    source: str = "",
    cost_bps: float | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """写入一份核验结论（覆盖式：一个策略键只保留最新一次结论）。

    :param verdict: `pass` / `observe` / `reject`
    :param headline: 一句话结论（给人和 LLM 读的，必须自带数字与口径）
    :param metrics: 关键统计量，建议用 `strategy_verify.summarize_row()` 产出
    :param sample: 样本边界（起止日期 / 交易日数 / 样本量），**没有边界的结论不可信**
    :param source: 产出该结论的脚本或命令（可重跑性）
    """
    if verdict not in (VERDICT_PASS, VERDICT_OBSERVE, VERDICT_REJECT):
        raise ValueError(f"verdict 必须是 pass/observe/reject，收到 {verdict!r}")
    path = _path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "key": key,
        "verdict": verdict,
        "headline": headline,
        "metrics": metrics or {},
        "sample": sample or {},
        "source": source,
        # 未显式传时回退到 metrics 里的值，避免顶层与 metrics 两个 cost_bps 打架
        "cost_bps": cost_bps if cost_bps is not None else (metrics or {}).get("cost_bps"),
        "recorded_at": beijing_now_naive().isoformat(timespec="seconds"),
    }
    if extra:
        payload.update(extra)
    # 原子写：先写临时文件再 rename，避免读到写了一半的 JSON
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
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


def _age_days(recorded_at: str | None) -> int | None:
    if not recorded_at:
        return None
    try:
        then = datetime.fromisoformat(recorded_at)
    except ValueError:
        return None
    return max((beijing_now_naive() - then).days, 0)


def gate_evidence(record: dict) -> dict:
    """Expose recorded machine checks, never certify strategy adoption."""
    gate = record.get("gate")
    if gate is None:
        state = "legacy_unverified"
    elif (isinstance(gate, dict) and gate.get("gate_version") == 2
          and gate.get("scope") == "machine_checks_only"
          and gate.get("review_required") is True
          and gate.get("verdict") == record.get("verdict")
          and gate.get("verdict") in {"pass", "observe", "reject"}
          and isinstance(gate.get("failed"), list) and isinstance(gate.get("unchecked"), list)
          and all(isinstance(v, str) for v in gate["failed"] + gate["unchecked"])
          and gate.get("machine_checks_complete") is (not gate["unchecked"])
          and (gate.get("verdict") != "pass" or not (gate["failed"] or gate["unchecked"]))):
        state = "recorded"
    else:
        state = "invalid"
    return {"gate": gate if state == "recorded" else None,
            "gate_evidence_state": state, "review_required": True}


def verification_of(key: str, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """单个策略键的核验结论（三态）。

    返回 `{"available", "stale", "age_days", "recorded_at", "verdict",
    "headline", "metrics", "sample", "source", "reason"}`。
    """
    rec = load_record(key)
    if rec is None:
        return {
            "available": False,
            "gate": None, "gate_evidence_state": "absent", "review_required": True,
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
