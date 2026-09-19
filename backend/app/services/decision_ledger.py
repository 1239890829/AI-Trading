"""决策台账（S2-11 第 5 步「决策级记忆」）——**只读聚合视图**，零迁移。

## 解决什么

一个决策被做出后，它的三要素散在四处，想知道「当初为什么这么定、后来兑现了吗」只能靠翻：
- **决策本身**：改进项的处置（`review_action_items.status`）
- **依据**：核验结论（`data/research/verify/<key>.json`）、复盘证据（`evidence`）
- **事后**：参数是否真的落地（`writeback.audit_applied_landed`）

它们彼此没有关联，也没有统一入口 ⇒ 决策做完了就"蒸发"，下次遇到同类问题还得重新想一遍。

## 为什么是只读聚合

候选落点有三个：①复用 `review_meta_insights`（有 `effectiveness` 但无决策链）；
②新建决策表（需数据库迁移）；③**只读聚合视图（零迁移）**。
选 ③ 的理由：不写库、不改现有结构、随时可换成 ①/② 而不影响消费方；
且本项目的红线里"口径变更/写库"都需确认，只读聚合是**零风险**做法。

## 边界（必须声明，别把它当权威数据源）

- 它是**视图**，不是新事实：每条都从既有数据现场聚合，不缓存、不落库；
- **不做因果判定**：只把决策与事后的证据并排放好，"兑现与否"由人（或 LLM 议程）判断；
- 缺哪项就标缺（`available=False` + 原因），**不用空列表冒充"没有决策"**。
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#: 单条决策的字段（消费方按此取用，不要自行加字段后不更新本注释）
ENTRY_KEYS = ("kind", "subject", "decision", "basis", "outcome", "at")


def _entry(*, kind: str, subject: str, decision: str, basis: str = "",
           outcome: str = "", at: str = "") -> dict[str, Any]:
    return {"kind": kind, "subject": subject, "decision": decision,
            "basis": basis, "outcome": outcome, "at": at}


def collect_decision_ledger(
    session_factory,
    *,
    limit_per_kind: int = 20,
) -> dict:
    """聚合四类决策痕迹，返回统一台账。

    :returns: `{"available", "entries", "counts", "kinds", "note", "caveat"}`
    """
    entries: list[dict] = []
    problems: list[str] = []

    # 1) 改进项处置：做了什么处置、依据是什么
    try:
        entries += _from_action_items(session_factory, limit=limit_per_kind)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"改进项：{exc}")

    # 2) 策略核验结论：准入/否决的依据与结论
    try:
        entries += _from_verification()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"核验结论：{exc}")

    # 3) 参数变更：改了什么、归因、存活与否
    try:
        entries += _from_param_changes(session_factory, limit=limit_per_kind)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"参数变更：{exc}")

    # 4) 采纳落地：标了 applied 但参数没真的改（**决策与事实不符**——最该被记住的一类）
    try:
        entries += _from_applied_landed(session_factory)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"落地核对：{exc}")

    counts: dict[str, int] = {}
    for e in entries:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1

    return {
        "available": bool(entries),
        "entries": entries,
        "counts": counts,
        "kinds": sorted(counts),
        "note": None if entries else "暂无可聚合的决策痕迹",
        "problems": problems,
        "caveat": (
            "只读聚合视图：现场从既有数据汇总，不落库、不缓存；"
            "只并陈决策与事后证据，**不做因果判定**；缺失项如实标注，不用空列表冒充"
        ),
    }


def _from_action_items(session_factory, *, limit: int) -> list[dict]:
    """改进项处置（含处置说明作为依据）。"""
    from sqlalchemy import select

    from app.review.models import ReviewActionItemRow

    with session_factory() as db:
        rows = db.execute(
            select(ReviewActionItemRow)
            .order_by(ReviewActionItemRow.id.desc())
            .limit(limit)
        ).scalars().all()
        out = []
        for r in rows:
            if not r.status or r.status == "pending":
                continue  # 未处置 = 还没形成决策，不进台账
            # 状态全集见 `app.review.storage.ALLOWED_STATUSES`（pending 已在前面过滤）。
            # 未登记的状态**原样透出**而不是猜一个中文名——猜错比显示英文更有误导性。
            outcome = {
                "confirmed": "已确认", "applied": "已采纳", "rejected": "已否决",
                "reverted": "已回退",
            }.get(r.status, r.status)
            out.append(_entry(
                kind="改进项处置",
                subject=(r.title or "")[:60] or f"#{r.id}",
                decision=outcome,
                basis=(r.resolution_note or r.evidence or "")[:160],
                outcome=_landed_note(r),
                at=(r.resolved_at.isoformat() if r.resolved_at else "") or r.trade_date,
            ))
        return out


def _landed_note(r) -> str:
    """applied 的落地情况：**采纳 ≠ 落地**（参数是否真的改了由运行时值判定）。"""
    if r.status != "applied":
        return ""
    from app.review.writeback import build_param_diff

    text = f"{r.title or ''} {r.resolution_note or ''}"
    try:
        diffs = build_param_diff(text)
    except Exception:  # noqa: BLE001
        return ""
    if not diffs:
        return "无参数意图（流程/文档类，不适用落地核对）"
    pending = [d["param"] for d in diffs if not d["landed"]]
    if pending:
        return f"⚠️ 未落地：{', '.join(pending)}（运行时值仍不等于建议值）"
    return "已落地（运行时值等于建议值）"


def _from_verification() -> list[dict]:
    """策略核验结论（准入/否决的依据）。"""
    from app.research.verify_registry import gate_evidence, list_records

    out = []
    for rec in list_records():
        context = gate_evidence(rec)
        gate = context["gate"]
        passed_label = ("机器条款通过（待终审）" if gate is not None
                        else "历史通过（待复核）")
        verdict = {
            "pass": passed_label, "observe": "观察", "reject": "否决",
        }.get(rec.get("verdict") or "", rec.get("verdict") or "?")
        if gate is not None:
            outcome = (f"判据命中 {len(gate['failed'])} 项；未验 {len(gate['unchecked'])} 项；"
                       "完整准入仍需终审")
        else:
            outcome = "历史或无效判据记录，完整性未核验；不得据此自动准入"
        out.append(_entry(
            kind="策略核验",
            subject=rec.get("key") or "?",
            decision=verdict,
            basis=(rec.get("headline") or "")[:200],
            outcome=outcome,
            at=(rec.get("recorded_at") or "")[:19],
        ))
    return out


def _from_param_changes(session_factory, *, limit: int) -> list[dict]:
    """参数变更单（改了什么 + 归因）。"""
    from sqlalchemy import select

    from app.models.agent import AgentParamChange

    with session_factory() as db:
        rows = db.execute(
            select(AgentParamChange).order_by(AgentParamChange.id.desc()).limit(limit)
        ).scalars().all()
        out = []
        for c in rows:
            if c.status == "draft":
                continue  # 草稿尚未形成决策
            out.append(_entry(
                kind="参数变更",
                subject=c.key or "?",
                decision=f"{c.before or '—'} → {c.after or '—'}",
                basis=((c.evidence or {}) if isinstance(c.evidence, dict) else {}) and str(c.evidence)[:160],
                outcome={"applied": "已应用", "rolled_back": "已回滚"}.get(c.status, c.status),
                at=(c.created_at.isoformat() if c.created_at else "") or "",
            ))
        return out


def _from_applied_landed(session_factory) -> list[dict]:
    """标了 applied 却未落地——**决策与事实不符**，这恰恰最该被记住。"""
    from sqlalchemy import select

    from app.review.models import ReviewActionItemRow

    with session_factory() as db:
        rows = db.execute(
            select(ReviewActionItemRow).where(ReviewActionItemRow.status == "applied")
        ).scalars().all()
        out = []
        for r in rows:
            note = _landed_note(r)
            if note.startswith("⚠️"):
                out.append(_entry(
                    kind="决策未兑现",
                    subject=(r.title or "")[:60] or f"#{r.id}",
                    decision="标记 applied 但参数未变",
                    basis=(r.resolution_note or "")[:160],
                    outcome=note,
                    at=(r.resolved_at.isoformat() if r.resolved_at else "") or r.trade_date,
                ))
        return out
