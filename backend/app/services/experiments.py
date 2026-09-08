"""实验记录本（AI 大脑 v2 P1）：A 类变更的后置验证与自动回滚。

安全模型的最后一环：参数自动生效是"前置放行"，本模块是"后置纠错"——
生效时自动建实验（基线=当时 signal_health 快照），30 日后自动对比：
- 胜率劣化 ≥ ROLLBACK_DROPPCT → **自动回滚变更单**并记录结论
- 样本不足（insufficient/None）→ 延长窗口（≤ max_extensions 次），绝不误杀
- 改善或持平 → concluded，结论归档

纪律：对比口径取 collect_signal_health 的同源字段；insufficient 绝不当作 0。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.agent import AgentExperiment
from app.picks.signal_health import collect_signal_health

log = logging.getLogger(__name__)

#: 胜率劣化回滚阈值（绝对百分点）：基线 0.50 → 现在 0.475 即触发
ROLLBACK_DROPPCT = 0.02
#: 实验窗口（天）
VERIFY_WINDOW_DAYS = 30


def attach_experiment(change_id: int, param_key: str, hypothesis: str,
                      session_factory=None) -> dict | None:
    """A 类变更生效时自动建实验（基线=当时 signal_health 快照）。失败只记日志。"""
    sf = session_factory or get_session_factory()
    try:
        from app.picks.signal_health import collect_signal_health

        health = collect_signal_health(sf)
        baseline = {k: health.get(k) for k in ("status", "win_rate", "mean_excess")}
        baseline["taken_at"] = datetime.utcnow().isoformat(timespec="seconds")
        with sf() as db:
            row = AgentExperiment(
                change_id=change_id, param_key=param_key, hypothesis=hypothesis[:300],
                baseline=json.dumps(baseline, ensure_ascii=False),
                verification_date=datetime.utcnow() + timedelta(days=VERIFY_WINDOW_DAYS),
                status="running",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return _dump(row)
    except Exception as exc:  # noqa: BLE001  实验是守护层，失败不阻断业务
        log.warning("experiment attach failed (change=%s): %s", change_id, exc)
        return None


def _dump(row: AgentExperiment) -> dict:
    return {
        "id": row.id, "change_id": row.change_id, "param_key": row.param_key,
        "hypothesis": row.hypothesis,
        "baseline": _j(row.baseline, {}),
        "verification_date": row.verification_date.isoformat() if row.verification_date else None,
        "status": row.status,
        "result": _j(row.result, None),
        "extensions": row.extensions,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "concluded_at": row.concluded_at.isoformat() if row.concluded_at else None,
    }


def _j(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return default


def conclude_due(session_factory=None, today: datetime | None = None) -> list[dict]:
    """扫描到期实验并裁决（scheduler 每日调用一次）。返回裁决结果列表。"""
    sf = session_factory or get_session_factory()
    now = today or datetime.utcnow()
    out: list[dict] = []
    with sf() as db:
        rows = db.execute(
            select(AgentExperiment).where(AgentExperiment.status == "running")
        ).scalars().all()
        due = [r for r in rows
               if r.verification_date is not None and r.verification_date <= now]
    for row in due:
        try:
            out.append(_conclude_one(row.id, sf))
        except Exception as exc:  # noqa: BLE001  单条失败不影响其他
            log.warning("experiment conclude failed (id=%s): %s", row.id, exc)
    return out


def _conclude_one(exp_id: int, sf) -> dict:
    with sf() as db:
        row = db.get(AgentExperiment, exp_id)
        if row is None or row.status != "running":
            return _dump(row) if row else {}
        baseline = _j(row.baseline, {})
        health = collect_signal_health(sf)
        current = {k: health.get(k) for k in ("status", "win_rate", "mean_excess")}

        # ① 样本不足：延长窗口（绝不拿 insufficient 冒充对比结果）
        if current.get("status") != "ok" or current.get("win_rate") is None:
            if row.extensions < row.max_extensions:
                row.extensions += 1
                row.verification_date = datetime.utcnow() + timedelta(days=VERIFY_WINDOW_DAYS)
                row.result = json.dumps({
                    "conclusion": "样本不足，验证窗口延长",
                    "current": current, "extensions": row.extensions,
                }, ensure_ascii=False)
                db.commit()
                return _dump(row)
            row.status = "concluded_insufficient"
            row.result = json.dumps({
                "conclusion": "窗口多次延长后仍样本不足，无法裁决（保守不回滚）",
                "current": current,
            }, ensure_ascii=False)
            row.concluded_at = datetime.utcnow()
            db.commit()
            return _dump(row)

        # ② 基线不可比（生效时样本就不足）：同样保守不回滚
        base_rate = baseline.get("win_rate")
        if base_rate is None:
            row.status = "concluded_insufficient"
            row.result = json.dumps({
                "conclusion": "基线样本不足，无法对比（保守不回滚）",
                "baseline": baseline, "current": current,
            }, ensure_ascii=False)
            row.concluded_at = datetime.utcnow()
            db.commit()
            return _dump(row)

        # ③ 正常对比：劣化 ≥ 阈值 → 自动回滚
        delta = float(current["win_rate"]) - float(base_rate)
        verdict = {
            "baseline": baseline, "current": current,
            "win_rate_delta": round(delta, 4), "threshold": -ROLLBACK_DROPPCT,
        }
        if delta <= -ROLLBACK_DROPPCT:
            rollback_info = _rollback_change(row.change_id, sf)
            row.status = "rolled_back"
            verdict["rollback"] = rollback_info
            row.result = json.dumps({
                "conclusion": f"胜率劣化 {delta:+.3f}，超阈值，已自动回滚",
                **verdict,
            }, ensure_ascii=False)
        else:
            row.status = "concluded"
            row.result = json.dumps({
                "conclusion": f"胜率变化 {delta:+.3f}，未触发回滚阈值",
                **verdict,
            }, ensure_ascii=False)
        row.concluded_at = datetime.utcnow()
        db.commit()
        return _dump(row)


def _rollback_change(change_id: int, sf) -> dict:
    """自动回滚变更单（找到该实验关联的 change，恢复 before）。"""
    from app.services.agent_params import rollback_change

    return rollback_change(change_id, session_factory=sf)


def list_experiments(limit: int = 30, session_factory=None) -> list[dict]:
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(AgentExperiment).order_by(AgentExperiment.id.desc()).limit(limit)
        ).scalars().all()
        return [_dump(r) for r in rows]
