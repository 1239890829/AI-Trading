"""预判报告的持久化与检索（与复盘 storage 同模式）。

同一 target_date 重复生成时**覆盖**——针对同一目标日，
只有最新预判有效（周六预判 + 周日补充证据后重跑，旧的作废）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select

from app.core.db import utcnow
from app.predict.models import PredictionReportRow, PredictionThemeRow
from app.predict.schemas import PredictionReport

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = REPO_ROOT / "data" / "review" / "predictions"


def save_report(session_factory, report: PredictionReport) -> PredictionReport:
    """落库 + 落盘（同一 target_date 覆盖）。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{report.target_date}.json"
    path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    db = session_factory()
    try:
        existing = db.execute(
            select(PredictionReportRow).where(PredictionReportRow.target_date == report.target_date)
        ).scalars().first()

        payload = json.dumps(report.model_dump(), ensure_ascii=False, default=str)
        verdict_summary = "；".join(f"{p.theme}:{p.verdict}" for p in report.predictions) or "无候选题材"
        if existing:
            existing.prediction_id = report.prediction_id
            existing.context = report.context
            existing.engine_version = report.engine_version
            existing.trigger = report.trigger
            existing.theme_count = len(report.predictions)
            existing.verdict_summary = verdict_summary[:200]
            existing.verify_status = "verified" if report.verify else "pending"
            existing.payload = payload
            existing.report_path = str(path)
            existing.created_at = utcnow()
            row = existing
            db.query(PredictionThemeRow).filter(
                PredictionThemeRow.prediction_id == report.prediction_id
            ).delete()
        else:
            row = PredictionReportRow(
                prediction_id=report.prediction_id,
                target_date=report.target_date,
                context=report.context,
                engine_version=report.engine_version,
                trigger=report.trigger,
                theme_count=len(report.predictions),
                verdict_summary=verdict_summary[:200],
                verify_status="verified" if report.verify else "pending",
                payload=payload,
                report_path=str(path),
            )
            db.add(row)

        for p in report.predictions:
            db.add(PredictionThemeRow(
                prediction_id=report.prediction_id,
                target_date=report.target_date,
                theme=p.theme,
                verdict=p.verdict,
                score=p.score,
                engine_version=report.engine_version,
            ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    log.info("prediction saved: %s target=%s (%s)", report.prediction_id, report.target_date, path)
    return report


def get_report(session_factory, target_date: str) -> PredictionReport | None:
    db = session_factory()
    try:
        row = db.execute(
            select(PredictionReportRow).where(PredictionReportRow.target_date == target_date)
        ).scalars().first()
        if not row:
            return None
        return PredictionReport.model_validate_json(row.payload)
    finally:
        db.close()


def list_reports(session_factory, limit: int = 30) -> list[dict]:
    db = session_factory()
    try:
        rows = db.execute(
            select(PredictionReportRow).order_by(PredictionReportRow.target_date.desc()).limit(limit)
        ).scalars().all()
        return [{
            "prediction_id": r.prediction_id,
            "target_date": r.target_date,
            "context": r.context,
            "engine_version": r.engine_version,
            "theme_count": r.theme_count,
            "verdict_summary": r.verdict_summary,
            "verify_status": r.verify_status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]
    finally:
        db.close()


def apply_verify(session_factory, target_date: str, verify: dict) -> None:
    """验证结果回填：报告 payload 与题材明细行同步更新。"""
    db = session_factory()
    try:
        row = db.execute(
            select(PredictionReportRow).where(PredictionReportRow.target_date == target_date)
        ).scalars().first()
        if not row:
            return
        report = PredictionReport.model_validate_json(row.payload)
        report.verify = verify
        row.payload = json.dumps(report.model_dump(), ensure_ascii=False, default=str)
        row.verify_status = "verified"
        row.verified_at = utcnow()

        outcomes = {t["theme"]: t for t in verify.get("themes", [])}
        themes = db.execute(
            select(PredictionThemeRow).where(PredictionThemeRow.target_date == target_date)
        ).scalars().all()
        for tr in themes:
            t = outcomes.get(tr.theme)
            if not t:
                continue
            tr.verify_outcome = t.get("outcome")
            tr.formed = 1 if t.get("formed") else 0
            tr.limit_up_count_d1 = t.get("limit_up_count")
            tr.leader_actual = t.get("leader_actual")
            tr.leader_hit = 1 if t.get("leader_hit") else 0
            note = t.get("note", "")
            if t.get("auction_note"):
                note = f"{note}；{t['auction_note']}" if note else t["auction_note"]
            tr.verify_note = note
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def hit_stats(session_factory) -> dict:
    """预判命中率分层统计（方法论迭代输入）：按 verdict 与 context 分组。"""
    db = session_factory()
    try:
        rows = db.execute(
            select(PredictionThemeRow).where(PredictionThemeRow.verify_outcome.isnot(None))
        ).scalars().all()
        by_verdict: dict[str, dict] = {}
        by_context: dict[str, dict] = {}
        reports = db.execute(select(PredictionReportRow)).scalars().all()
        ctx_by_pid = {r.prediction_id: r.context for r in reports}
        for r in rows:
            for bucket, key in ((by_verdict, r.verdict), (by_context, ctx_by_pid.get(r.prediction_id, "?"))):
                b = bucket.setdefault(key, {"total": 0, "hit": 0, "partial": 0, "miss": 0})
                b["total"] += 1
                b[r.verify_outcome] = b.get(r.verify_outcome, 0) + 1
        return {"by_verdict": by_verdict, "by_context": by_context, "sample_note": "样本外跟踪 ≥20 条前，统计仅供参考"}
    finally:
        db.close()
