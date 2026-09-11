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
from app.sentiment.engine import PHASE_ORDER  # S2-7：相位集合唯一权威

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
    """自动回滚变更单（找到该实验关联的 change，恢复 before）。

    归因固定为 `degraded`（30 日实验测到劣化自动回滚）——这是存活率统计里
    最有价值的一类归因：它区分"被数据否决"与"人工改主意"。
    """
    from app.services.agent_params import rollback_change

    return rollback_change(
        change_id, session_factory=sf,
        reason_code="degraded", note="30 日实验劣化，自动回滚（experiments 后置守护）",
    )


def list_experiments(limit: int = 30, session_factory=None) -> list[dict]:
    sf = session_factory or get_session_factory()
    with sf() as db:
        rows = db.execute(
            select(AgentExperiment).order_by(AgentExperiment.id.desc()).limit(limit)
        ).scalars().all()
        return [_dump(r) for r in rows]


# ---------------------------------------------------------------- 影子队列（P1-4）
#
# 2026-09-08 用户指令「新策略需经数据验证有效后方可启用」：A 类参数变更不再
# 直接生效，先入影子队列（不写运行时覆盖层）。评估 = 影子权重 vs 现行权重的
# **剧变检测**（L1 距离 + 维度灭声）——通过则转正（真正生效 + 挂 30 日胜率
# 劣化回滚实验）；剧变/灭声 → 拒绝归档。效果验证由转正后的 30 日实验承担
# （本评估回答的是「会不会一步改出极端权重」，不是「效果好不好」——不冒充）。

#: 影子权重剧变阈值：同相位下影子与现行 offsets 的 L1 距离上限
SHADOW_L1_MAX = 0.25
#: 维度灭声阈值：影子权重中任一维度低于此值即拒绝（防止某维度被关掉）
SHADOW_FLOOR = 0.05
#: 偏移生效的相位集合（影子对照逐相位检查）。
# S2-7 修正（2026-09-11）：原为字面量 `("启动", "发酵", "高潮", "分歧", "退潮", "冰点")`——
# 含**题材阶段**「启动」（非市场相位，该轮循环恒不命中），却**漏了市场相位「修复」**。
# 后果：修复期的影子偏移**从未被校验过**就进了转正流程。
# 现直接取 `PHASE_ORDER`：相位集合只有一处定义，新增相位自动纳入校验（漏检不可再发生）。
_SHADOW_PHASES = tuple(PHASE_ORDER)


def evaluate_and_promote_shadow(session_factory=None) -> list[dict]:
    """影子队列逐条评估 → 达标转正（apply + 30 日实验） / 剧变拒绝归档。

    挂在每日议程调度器里（议程生成前执行）。
    """
    from app.models.agent import AgentParamChange
    from app.picks.style_router import apply_style_offsets, parse_overrides
    from app.services.agent_params import promote_shadow

    sf = session_factory or get_session_factory()
    out: list[dict] = []
    with sf() as db:
        rows = db.execute(
            select(AgentParamChange).where(AgentParamChange.status == "shadow")
        ).scalars().all()
        shadow_ids = [r.id for r in rows]
    for cid in shadow_ids:
        try:
            with sf() as db:
                row = db.get(AgentParamChange, cid)
                if row is None:
                    continue
                key = row.key
                after_raw = row.after or ""
                before_raw = row.before or ""

            if key != "picks_style_offsets_json":
                # 非权重类参数暂无离线评估器：诚实跳过（累计在影子队列，不假装验证）
                out.append({"change_id": cid, "key": key,
                            "verdict": "shadow_eval_unsupported",
                            "note": "该参数暂无离线对照评估器，需人工拍板"})
                continue

            shadow_ov = parse_overrides(after_raw)      # 非法 → 拒绝
            current_ov = parse_overrides(before_raw or "")
            worst_l1, dim_floor_hit, dim = 0.0, False, ""
            for phase in _SHADOW_PHASES:
                from app.picks.style_router import DEFAULT_ROUTES, DIMS

                base = dict(DEFAULT_ROUTES.get(phase, ("均衡", {}))[1])
                cur_w = apply_style_offsets(base, current_ov.get(phase, {}))
                shd_w = apply_style_offsets(base, shadow_ov.get(phase, {}))
                l1 = sum(abs(shd_w[d] - cur_w[d]) for d in DIMS)
                worst_l1 = max(worst_l1, l1)
                for d, v in shd_w.items():
                    if v < SHADOW_FLOOR:
                        dim_floor_hit, dim = True, d

            if dim_floor_hit:
                verdict = "shadow_rejected"
                note = f"影子权重出现灭声维度（{dim} < {SHADOW_FLOOR}）——防单维被关掉"
            elif worst_l1 > SHADOW_L1_MAX:
                verdict = "shadow_rejected"
                note = f"权重剧变（最大 L1 距离 {worst_l1:.3f} > {SHADOW_L1_MAX}）——防一步改出极端风格"
            else:
                verdict = "promoted"
                note = f"权重漂移温和（最大 L1 距离 {worst_l1:.3f}）——转正生效，效果由 30 日实验守护"

            if verdict == "promoted":
                promoted = promote_shadow(cid, sf)
                exp = attach_experiment(promoted["id"], key,
                                        hypothesis=f"影子转正：{note}", session_factory=sf)
                out.append({"change_id": cid, "key": key, "verdict": verdict,
                            "note": note, "experiment_id": exp["id"] if exp else None})
            else:
                with sf() as db:
                    row = db.get(AgentParamChange, cid)
                    row.status = "shadow_rejected"
                    try:
                        ev = json.loads(row.evidence) if row.evidence else {}
                    except Exception:  # noqa: BLE001
                        ev = {}
                    ev["shadow_verdict"] = {"at": datetime.utcnow().isoformat(timespec="seconds"),
                                            "note": note}
                    row.evidence = json.dumps(ev, ensure_ascii=False)
                    db.commit()
                out.append({"change_id": cid, "key": key, "verdict": verdict, "note": note})
        except Exception as exc:  # noqa: BLE001  单条失败不影响队列其余
            log.exception("shadow evaluation failed (change=%s): %s", cid, exc)
            out.append({"change_id": cid, "verdict": "error", "note": str(exc)[:150]})
    return out
