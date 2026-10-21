"""Company evidence cache/result state; task lifecycle remains in AgentTask.

Ordinary readers only follow bounded content-addressed profile pointers. They
never create folders, refresh sources, enqueue work or invoke a model.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from app.events import company_impact as ci

STATES = {"idle", "queued", "collecting", "generating", "ready", "insufficient", "source_failed",
          "model_failed", "invalid_response", "budget_blocked", "canceled", "interrupted", "disabled"}
REASONS = {
    "idle": "尚未轮到后台采集；普通刷新只读取状态。",
    "queued": "已进入既有后台任务，等待公司依据采集。",
    "collecting": "正在采集本公司正式披露与原文依据。",
    "generating": "已保存公司原文，正在生成有界经营解释。",
    "ready": "已保存本股研究解释；验证身份以实际回执为准，经营效果未独立验证。",
    "insufficient": "已保存可核对的公司资料；事件关系或经营传导证据不够。",
    "source_failed": "本轮公司披露来源或解析失败，未用空资料冒充无业务。",
    "model_failed": "模型本轮未完成；已采集事实保留，未把失败改成中性结论。",
    "invalid_response": "模型选择未通过身份或结构校验，未发布该回答。",
    "budget_blocked": "当前模型预算或用量核实条件未满足；未绕过预算继续调用。",
    "canceled": "后台任务已取消；已完成采集保留，未将取消写成判读完成。",
    "interrupted": "前次任务中断；已开始的未知模型调用不会自动重发。",
    "disabled": "旧版独立公司采集与判读的自动调度已停用，已有资料仍按其时间与范围阅读。完整TradingAgents研究使用独立任务状态，此开关不表示其已完成或停用。",
}


def _folder(symbol: str, root: Path | None = None) -> Path:
    root = root if root is not None else ci.ROOT
    if not re.fullmatch(r"\d{6}", symbol) or root.is_symlink() or (root / symbol).is_symlink():
        raise ValueError("invalid company store identity")
    return root / symbol


def atomic_json(path: Path, value: dict) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("company state cannot be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".company-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_state(symbol: str, *, root: Path | None = None) -> dict:
    try:
        value = ci._read(_folder(symbol, root) / "service.json")
        return value if value.get("symbol") == symbol and value.get("state") in STATES else {}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def write_state(symbol: str, state: str, *, now: datetime, root: Path | None = None, **fields) -> dict:
    if state not in STATES:
        raise ValueError("unknown company state")
    clock = now.isoformat()
    ci.clock(clock)  # Refuse a naive state before making any persistent change.
    prior = load_state(symbol, root=root)
    value = {**prior, **fields, "symbol": symbol, "state": state, "updated_at": clock}
    atomic_json(_folder(symbol, root) / "service.json", value)
    return value


def put_profile(profile: dict, *, root: Path | None = None) -> str:
    symbol = profile["symbol"]
    ci.validate_company_profile(profile)
    folder = _folder(symbol, root)
    folder.mkdir(parents=True, exist_ok=True)
    key = ci.digest(profile)
    target = folder / ("profile-" + key + ".json")
    with ci.publication_lock(folder):
        if target.exists():
            if ci._read(target) != profile:
                raise ValueError("profile content identity drift")
        else:
            atomic_json(target, profile)
        atomic_json(folder / "profile.json", {"key": key})
    return key


def read_profile(symbol: str, *, root: Path | None = None) -> dict | None:
    return ci.read_company_profile(symbol, root=root)


def read_status(symbol: str, *, now: datetime, enabled: bool,
                response_as_of: datetime | None = None, root: Path | None = None,
                task_resolver=None) -> dict:
    current = now.replace(tzinfo=ci.BJ_TZ) if now.tzinfo is None else now
    value = load_state(symbol, root=root)
    state = value.get("state", "idle") if enabled else "disabled"
    updated = value.get("updated_at", current.isoformat())
    try:
        at = ci.clock(updated)
        if at > current:
            raise ValueError("future company state")
    except (ValueError, TypeError):
        state, updated, value = "interrupted", current.isoformat(), {}
    if response_as_of is not None:
        cutoff = response_as_of.replace(tzinfo=ci.BJ_TZ) if response_as_of.tzinfo is None else response_as_of
        if ci.clock(updated) > cutoff:
            return {"symbol": symbol, "state": "historical_excluded", "updated_at": cutoff.isoformat(),
                    "reason": "新采集的公司资料不回填历史时点。", "missing": [], "documents_count": 0}
    profile = read_profile(symbol, root=root)
    if profile and (ci.clock(profile["checked_at"]) > current
                    or (response_as_of is not None and ci.clock(profile["checked_at"]) > cutoff)):
        profile = None
    # Startup reconciliation owns task terminal status; this projection only
    # exposes interrupted/canceled work without repairing files or repeating it.
    if state in {"queued", "collecting", "generating"} and task_resolver:
        try:
            task_id = value.get("task_id")
            task = task_resolver(task_id) if isinstance(task_id, str) and task_id else None
        except Exception:
            task = None  # Projection cannot repair task storage or break ordinary reads.
        if not isinstance(task, dict) or task.get("status") not in {"queued", "running"}:
            state = "canceled" if isinstance(task, dict) and task.get("status") == "canceled" else "interrupted"
    missing = []
    for items in (value.get("missing"), profile.get("missing") if profile else None):
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str):
                text = item.strip()[:500]
                if text and text not in missing and len(missing) < 12:
                    missing.append(text)
    result = {"symbol": symbol, "state": state, "updated_at": updated,
              "reason": REASONS[state], "missing": missing,
              "task_id": value.get("task_id"), "next_retry_at": value.get("next_retry_at"),
              "source_status": value.get("source_status", "unknown"),
              "documents_count": len(profile["documents"]) if profile else 0}
    if profile:
        # Facts have their own profile capture time. A later state never makes
        # older facts available at an earlier requested history cutoff.
        if response_as_of is None or ci.clock(profile["checked_at"]) <= cutoff:
            result["facts"] = ci.facts(profile)
    return result
