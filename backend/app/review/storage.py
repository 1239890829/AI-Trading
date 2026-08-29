"""复盘报告的持久化、检索与历史对比（需求 6）。

存储分两处，各有职责：
- **SQLite**：结构化列用于检索与统计（"某方法论版本产生了多少 P0"、
  "某维度的历史采纳率"）。改进项单独建表，因为它会被确认/应用/回退。
- **JSON 落盘**（`data/review/reports/YYYYMMDD.json`）：完整报告，
  用于历史 diff 与重放。只存 SQLite 会丢细节，只存 JSON 会失去检索能力。

同一交易日重复生成时**覆盖**而不是追加——复盘是对某一天的判断，
留两份只会让人分不清该看哪个。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.core.db import utcnow
from app.review.models import (
    ReviewActionItemRow,
    ReviewMetaInsightRow,
    ReviewReportRow,
)
from app.review.schemas import ReviewReport

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = REPO_ROOT / "data" / "review" / "reports"


def _report_path(trade_date: str) -> Path:
    return REPORT_DIR / f"{trade_date}.json"


def save_report(session_factory, report: ReviewReport) -> ReviewReport:
    """落库 + 落盘。同一交易日覆盖。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _report_path(report.trade_date)

    if not report.review_id:
        report.review_id = f"RV-{report.trade_date}-{datetime.now().strftime('%H%M%S')}"

    # JSON 先落盘：落盘失败就不该写库，避免"库里有报告但文件找不到"
    path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    db = session_factory()
    try:
        existing = db.execute(
            select(ReviewReportRow).where(ReviewReportRow.trade_date == report.trade_date)
        ).scalars().first()

        payload = json.dumps(report.model_dump(), ensure_ascii=False, default=str)
        if existing:
            existing.review_id = report.review_id
            existing.methodology_version = report.methodology_version
            existing.model_requested = report.model.requested
            existing.model_actual = report.model.actual
            existing.model_degraded = 1 if report.model.degraded else 0
            existing.model_cost = report.model.cost
            existing.summary = report.summary
            existing.gap_count = len(report.data.all_gaps)
            existing.action_item_count = len(report.action_items)
            existing.payload = payload
            existing.report_path = str(path)
            existing.generated_at = utcnow()
            row = existing
            # 旧的改进项与元结论先清掉，避免重复累积
            db.query(ReviewActionItemRow).filter(
                ReviewActionItemRow.review_id == report.review_id
            ).delete()
            db.query(ReviewMetaInsightRow).filter(
                ReviewMetaInsightRow.review_id == report.review_id
            ).delete()
        else:
            row = ReviewReportRow(
                review_id=report.review_id,
                trade_date=report.trade_date,
                methodology_version=report.methodology_version,
                model_requested=report.model.requested,
                model_actual=report.model.actual,
                model_degraded=1 if report.model.degraded else 0,
                model_cost=report.model.cost,
                summary=report.summary,
                gap_count=len(report.data.all_gaps),
                action_item_count=len(report.action_items),
                payload=payload,
                report_path=str(path),
            )
            db.add(row)

        for it in report.action_items:
            db.add(ReviewActionItemRow(
                review_id=report.review_id, trade_date=report.trade_date,
                title=it.title, category=it.category, priority=it.priority,
                expected_impact=it.expected_impact, evidence=it.evidence,
                target=it.target, proposed_change=it.proposed_change, status=it.status,
            ))
        for mi in report.meta_insights:
            db.add(ReviewMetaInsightRow(
                review_id=report.review_id, trade_date=report.trade_date,
                methodology_version=report.methodology_version, dimension=mi.dimension,
                observation=mi.observation, effectiveness=mi.effectiveness,
                evidence=mi.evidence, suggestion=mi.suggestion,
            ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    log.info("review saved: %s (%s)", report.review_id, path)
    return report


def get_report(session_factory, trade_date: str) -> ReviewReport | None:
    db = session_factory()
    try:
        row = db.execute(
            select(ReviewReportRow).where(ReviewReportRow.trade_date == trade_date)
        ).scalars().first()
        if not row:
            return None
        return ReviewReport.model_validate_json(row.payload)
    finally:
        db.close()


def list_reports(session_factory, limit: int = 30) -> list[dict]:
    db = session_factory()
    try:
        rows = db.execute(
            select(ReviewReportRow).order_by(ReviewReportRow.trade_date.desc()).limit(limit)
        ).scalars().all()
        return [{
            "review_id": r.review_id, "trade_date": r.trade_date,
            "methodology_version": r.methodology_version,
            "model_actual": r.model_actual, "model_degraded": bool(r.model_degraded),
            "summary": r.summary, "gap_count": r.gap_count,
            "action_item_count": r.action_item_count,
            "generated_at": r.generated_at.isoformat() if r.generated_at else None,
        } for r in rows]
    finally:
        db.close()


def compare_reports(a: ReviewReport, b: ReviewReport) -> dict:
    """两日报告对比：看变化而不是看绝对值。"""
    ga = {(g.field, g.source) for g in a.data.all_gaps}
    gb = {(g.field, g.source) for g in b.data.all_gaps}

    def _phase(r: ReviewReport) -> str | None:
        return (r.data.market.sentiment or {}).get("phase")

    return {
        "from": a.trade_date,
        "to": b.trade_date,
        "methodology_changed": a.methodology_version != b.methodology_version,
        "gaps_fixed": sorted(f"{f}" for f, s in ga - gb),
        "gaps_new": sorted(f"{f}" for f, s in gb - ga),
        "gaps_persistent": sorted(f"{f}" for f, s in ga & gb),
        "sentiment": {"from": _phase(a), "to": _phase(b)},
        "action_items": {
            "from": len(a.action_items), "to": len(b.action_items),
            "resolved": [i.title for i in a.action_items if i.status != "pending"],
        },
    }
