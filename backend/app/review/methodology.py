"""方法论自我迭代：记录「哪种复盘方式有效、哪种无效」。

需求 5 记录复盘产出、证据限制与改进项的管理处置，供后续复核。
没有判断或未被采纳，本身不足以证明框架无效；资料或覆盖不足时先核数据。
管理采纳率不等于判断准确率、策略胜率或交易效果。

三层元结论：
1. **当场观察**（每次复盘）：各维度保留的判断、合法无动作与证据缺口
2. **历史回顾**（跨复盘统计）：各方法论版本的改进项采纳率、回退率
3. **演进建议**：基于上面两层，给出"下一版方法论该怎么改"

刻意不做的事：**不自动改方法论**。自动改会让"方法论演进"变成无法归因的黑箱——
你分不清是市场变了还是框架变了。改进项需人工确认后才应用。
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.review.config import MethodologyConfig
from app.review.models import ReviewActionItemRow, ReviewMetaInsightRow
from app.review.schemas import DimensionResult, MetaInsight, ReviewData

log = logging.getLogger(__name__)


def build_meta_insights(
    data: ReviewData, dimensions: list[DimensionResult], method: MethodologyConfig
) -> list[MetaInsight]:
    """记录产出及证据限制，不以缺少判断反推策略或阈值有误。"""
    out: list[MetaInsight] = []

    for d in dimensions:
        if d.status == "blocked":
            out.append(MetaInsight(
                dimension=d.key,
                observation=f"维度「{d.key}」因数据缺失被阻断，本次未产出任何结论",
                effectiveness="ineffective",
                evidence=str(d.evidence.get("blocked_reason", ""))[:200],
                suggestion="先核实数据来源、日期与覆盖；本次阻断不能证明维度无用，不据此关闭维度或调整策略阈值",
            ))
        elif d.status != "ok" or d.gaps:
            out.append(MetaInsight(
                dimension=d.key,
                observation=f"维度「{d.key}」资料或覆盖不足，保留 {len(d.findings)} 条观察、{len(d.judgements)} 条判断；本次方法效果未核",
                effectiveness="unknown",
                evidence=("; ".join(f"{g.field}:{g.reason}" for g in d.gaps[:3])
                          or "; ".join(d.findings[:3]))[:200],
                suggestion="先核实数据来源、日期与覆盖；资料补齐前不据此调整策略或阈值，也不把判断条数当作效果证明",
            ))
        elif not d.judgements:
            out.append(MetaInsight(
                dimension=d.key,
                observation=f"维度「{d.key}」已记录 {len(d.findings)} 条观察，本次无判断产出",
                effectiveness="unknown",
                evidence="; ".join(d.findings[:3])[:200],
                suggestion="无判断可能是合法空集合或无需行动；先核对原触发条件，不能仅凭无判断认定阈值或指标有问题",
            ))
        else:
            out.append(MetaInsight(
                dimension=d.key,
                observation=f"维度「{d.key}」产出 {len(d.judgements)} 条判断",
                effectiveness="effective",
                evidence="; ".join(d.judgements[:2])[:200],
                suggestion="",
            ))

    # 数据完整度本身就是一条元结论：缺数据的方法论无法被评估
    total_gaps = len(data.all_gaps)
    if total_gaps:
        out.append(MetaInsight(
            dimension="__data__",
            observation=f"本次复盘存在 {total_gaps} 处数据缺失",
            effectiveness="ineffective" if total_gaps >= 3 else "unknown",
            evidence="; ".join(f"{g.field}:{g.reason}" for g in data.all_gaps[:3])[:200],
            suggestion="数据缺失过多时，本次方法论效果评估不可信，不应据此调整框架",
        ))

    return out


def evaluate_historical_effectiveness(
    session_factory, version: str | None = None
) -> dict:
    """跨复盘统计：各方法论版本的改进项采纳率与回退率。

    这是"方法论自我迭代"的证据来源——不是拍脑袋说 v2 更好，
    而是看 v2 产生的改进项有多少被真采纳、多少被回退。
    """
    db = session_factory()
    try:
        q = select(
            ReviewActionItemRow.category,
            ReviewActionItemRow.status,
            func.count(ReviewActionItemRow.id),
        )
        if version:
            q = q.join(
                ReviewMetaInsightRow,
                ReviewMetaInsightRow.review_id == ReviewActionItemRow.review_id,
            ).where(ReviewMetaInsightRow.methodology_version == version)
        rows = db.execute(q.group_by(
            ReviewActionItemRow.category, ReviewActionItemRow.status
        )).all()
    finally:
        db.close()

    stats: dict[str, dict[str, int]] = {}
    for category, status, cnt in rows:
        stats.setdefault(category or "unknown", {})[status or "pending"] = cnt

    summary: dict[str, dict] = {}
    for cat, s in stats.items():
        total = sum(s.values())
        confirmed = s.get("confirmed", 0) + s.get("applied", 0)
        reverted = s.get("reverted", 0)
        rejected = s.get("rejected", 0)
        summary[cat] = {
            "total": total,
            "confirmed": confirmed,
            "reverted": reverted,
            "rejected": rejected,
            "pending": s.get("pending", 0),
            "recorded": s.get("recorded", 0),
            "implemented": s.get("applied", 0),
            "adoption_rate": round(confirmed / total, 3) if total else None,
            "revert_rate": round(reverted / confirmed, 3) if confirmed else None,
        }
    return {"version": version or "all", "by_category": summary}


def suggest_methodology_changes(session_factory, version: str) -> list[str]:
    """基于历史效果给出方法论演进建议（只建议，不自动改）。"""
    eff = evaluate_historical_effectiveness(session_factory, version)
    suggestions: list[str] = []

    for cat, s in eff.get("by_category", {}).items():
        total = s["total"]
        if total < 5:
            suggestions.append(
                f"[{cat}] 样本仅 {total} 条，不足以判断该维度效果，继续积累"
            )
            continue
        adoption = s["adoption_rate"] or 0.0
        revert = s["revert_rate"] or 0.0
        if adoption < 0.2:
            suggestions.append(
                f"[{cat}] 共 {total} 条改进项但采纳率仅 {adoption:.0%}，"
                "疑似产出噪音——建议提高该维度的触发门槛或降低其权重"
            )
        if revert > 0.3:
            suggestions.append(
                f"[{cat}] 已采纳 {s['confirmed']} 条中回退 {s['reverted']} 条"
                f"（{revert:.0%}），说明该类判据不可靠，建议复核其证据链"
            )

    if not suggestions:
        suggestions.append("暂无足够样本给出方法论调整建议")
    return suggestions
