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

from sqlalchemy import select, update

from app.core.bjtime import beijing_now_naive
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
        baseline["taken_at"] = beijing_now_naive().isoformat(timespec="seconds")
        with sf() as db:
            row = AgentExperiment(
                change_id=change_id, param_key=param_key, hypothesis=hypothesis[:300],
                baseline=json.dumps(baseline, ensure_ascii=False),
                verification_date=beijing_now_naive() + timedelta(days=VERIFY_WINDOW_DAYS),
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
    now = today or beijing_now_naive()
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
                row.verification_date = beijing_now_naive() + timedelta(days=VERIFY_WINDOW_DAYS)
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
            row.concluded_at = beijing_now_naive()
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
            row.concluded_at = beijing_now_naive()
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
        row.concluded_at = beijing_now_naive()
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
# **剧变检测**（L1 距离 + 维度灭声），不能代替效果或批准。
# 结构通过只保存待审证据，剧变/灭声可拒绝；不调用promote或创建事后实验。

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


def _store_shadow_assessment(candidate: dict, assessment: dict, sf) -> bool:
    """Conditional evidence update; never overwrite a changed/withdrawn candidate."""
    from app.models.agent import AgentParamChange as C

    evidence = json.loads(candidate["evidence"]) if candidate["evidence"] else {}
    if not isinstance(evidence, dict):
        raise ValueError("影子依据不是对象")
    previous = evidence.get("shadow_verdict")
    if isinstance(previous, dict) and {k: v for k, v in previous.items() if k != "at"} == assessment:
        # Still check the identity even when there is no new evidence to write.
        with sf() as db:
            row = db.get(C, candidate["id"])
            return bool(row and row.status == "shadow" and all(
                getattr(row, key) == value for key, value in candidate.items()))
    evidence["shadow_verdict"] = {**assessment, "at": beijing_now_naive().isoformat(timespec="seconds")}
    target_status = "shadow_rejected" if assessment["verdict"] == "shadow_rejected" else "shadow"
    with sf() as db:
        stmt = update(C.__table__).where(C.__table__.c.status == "shadow")
        for key, value in candidate.items():
            stmt = stmt.where(getattr(C.__table__.c, key) == value)
        result = db.execute(stmt.values(status=target_status,
                                       evidence=json.dumps(evidence, ensure_ascii=False, allow_nan=False)))
        if result.rowcount != 1:
            db.rollback()
            return False
        db.commit()
        return True


def evaluate_and_promote_shadow(session_factory=None) -> list[dict]:
    """Historical name retained: assess only, never promote or attach an experiment.

    Structural drift is not effect evidence. All positive/neutral assessments
    remain shadow; unsafe structure may be rejected. A separate verified review
    and activation contract is required, not a flag in model-supplied evidence.
    """
    from app.models.agent import AgentParamChange as C
    from app.picks.style_router import apply_style_offsets, parse_overrides, DEFAULT_ROUTES, DIMS
    from app.services.agent_params import current_value

    sf = session_factory or get_session_factory()
    with sf() as db:
        candidates = [dict(id=row.id, key=row.key, before=row.before, after=row.after,
                           evidence=row.evidence, source_type=row.source_type, source_id=row.source_id)
                      for row in db.execute(select(C).where(C.status == "shadow")).scalars().all()]
    out: list[dict] = []
    for candidate in candidates:
        cid, key = candidate["id"], candidate["key"]
        before, after = candidate["before"] or "", candidate["after"] or ""
        item = {"change_id": cid, "key": key, "review_required": True,
                "runtime_changed": False, "effect_verified": False, "experiment_id": None}
        try:
            current = current_value(key, sf)
            assessment = {"version": 2, "key": key, "before": before, "after": after,
                          "review_required": True, "runtime_changed": False, "effect_verified": False}
            matches = (parse_overrides(before) == parse_overrides(current)
                       if key == "picks_style_offsets_json" else before == current)
            if not matches:
                assessment.update(verdict="shadow_stale", note="当前参数与候选基线不同；保留影子，需重新评估")
            elif key != "picks_style_offsets_json":
                from app.services.shadow_eval import evaluate_shadow
                ev = evaluate_shadow(key, before=before, after=after, session_factory=sf)
                if ev.get("verdict") not in {"supports", "opposes", "neutral", "insufficient", "not_applicable", "unsupported"}:
                    raise ValueError("未知评估状态，不作为晋级批准")
                assessment.update(verdict=ev.get("verdict", "unsupported"),
                                  note=ev.get("note", ""), metrics=ev.get("metrics", {}),
                                  sample_days=ev.get("sample_days", 0), caveat=ev.get("caveat"))
            else:
                shadow_ov, current_ov = parse_overrides(after), parse_overrides(before)
                worst_l1, dim_floor_hit, dim = 0.0, False, ""
                for phase in _SHADOW_PHASES:
                    base = dict(DEFAULT_ROUTES.get(phase, ("均衡", {}))[1])
                    cur_w = apply_style_offsets(base, current_ov.get(phase, {}))
                    shd_w = apply_style_offsets(base, shadow_ov.get(phase, {}))
                    worst_l1 = max(worst_l1, sum(abs(shd_w[d] - cur_w[d]) for d in DIMS))
                    for d, v in shd_w.items():
                        if v < SHADOW_FLOOR:
                            dim_floor_hit, dim = True, d
                if dim_floor_hit:
                    verdict, note = "shadow_rejected", f"影子权重出现灭声维度（{dim} < {SHADOW_FLOOR}）"
                elif worst_l1 > SHADOW_L1_MAX:
                    verdict, note = "shadow_rejected", f"权重剧变（最大 L1 距离 {worst_l1:.3f} > {SHADOW_L1_MAX}）"
                else:
                    verdict, note = "shadow_review_required", (
                        f"权重漂移温和（最大 L1 距离 {worst_l1:.3f}）只表示结构未剧变；"
                        "尚缺完整效果证据与独立批准，保留影子，未生效")
                assessment.update(verdict=verdict, note=note, metrics={"worst_l1": worst_l1,
                                  "dimension_floor_hit": dim_floor_hit}, scope="structure_only")
            recorded = _store_shadow_assessment(candidate, assessment, sf)
            if not recorded:
                out.append({**item, "verdict": "shadow_stale", "recorded": False,
                            "note": "评估期间候选已变化或被撤回；不覆盖新状态，不应用参数"})
                continue
            out.append({**assessment, **item, "recorded": True})
        except Exception as exc:  # Single-candidate failure must not stop the queue.
            log.warning("shadow assessment failed (change=%s): %s", cid, type(exc).__name__)
            out.append({**item, "verdict": "error", "recorded": False,
                        "note": f"影子评估或证据保存失败（{type(exc).__name__}）；未应用参数"})
    return out
