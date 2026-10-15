"""Recover narrowly identified archive-only dispositions, preserving an atomic audit."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from sqlalchemy import select, update

from app.models.agent import AgentAudit
from app.review.models import ReviewActionItemRow

_ARCHIVE_ONLY = re.compile(r'^AI 大脑自动执行：已写入 (?:.*[/\\])?docs[/\\]evolution[/\\]\d{4}-\d{2}-\d{2}\.md$')


def repair_archive_dispositions(sf, *, expected_digest: str | None = None) -> dict:
    """Dry-run unless an exact current plan digest is supplied by the operator."""
    with sf() as db:
        rows = db.execute(select(ReviewActionItemRow).where(
            ReviewActionItemRow.status == 'applied').order_by(ReviewActionItemRow.id)).scalars().all()
        plan = [{'id': r.id, 'trade_date': r.trade_date, 'category': r.category,
                 'title': r.title, 'note': r.resolution_note,
                 'resolved_at': r.resolved_at.isoformat() if r.resolved_at else None}
                for r in rows if _ARCHIVE_ONLY.fullmatch(r.resolution_note or '')]
        digest = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if expected_digest is None:
            return {'digest': digest, 'items': plan, 'changed': 0, 'dry_run': True}
        if expected_digest != digest:
            raise ValueError('修复计划已变化，必须重新读取；未写入')
        for item in plan:
            note = '历史归档误标已纠正；原建议尚待实施与验证。'
            changed = db.execute(update(ReviewActionItemRow).where(
                ReviewActionItemRow.id == item['id'], ReviewActionItemRow.status == 'applied',
                ReviewActionItemRow.resolution_note == item['note'],
                ReviewActionItemRow.trade_date == item['trade_date'],
                ReviewActionItemRow.category == item['category'], ReviewActionItemRow.title == item['title'],
                ReviewActionItemRow.resolved_at == (datetime.fromisoformat(item['resolved_at']) if item['resolved_at'] else None),
            ).values(status='recorded', resolution_note=note, resolved_at=None))
            if changed.rowcount != 1:
                raise ValueError('修复期间处置漂移；整批事务回滚')
            db.add(AgentAudit(actor='user', action='review.archive_repair',
                              target=f"review:{item['trade_date']}:{item['id']}",
                              before=json.dumps({'status': 'applied', **item}, ensure_ascii=False),
                              after=json.dumps({'status': 'recorded', 'note': note, 'plan_digest': digest}, ensure_ascii=False),
                              rollback_ref=digest))
        db.commit()
        return {'digest': digest, 'items': plan, 'changed': len(plan), 'dry_run': False}


def restore_archive_dispositions(sf, digest: str) -> dict:
    """Restore only unchanged repaired rows, not the whole live database backup."""
    with sf() as db:
        audits = db.scalars(select(AgentAudit).where(AgentAudit.action == 'review.archive_repair',
            AgentAudit.rollback_ref == digest)).all()
        if not audits:
            raise ValueError('没有匹配的修复回执')
        restored = 0
        for audit in audits:
            before, after = json.loads(audit.before), json.loads(audit.after)
            row = db.get(ReviewActionItemRow, before['id'])
            if not row or row.status != after['status'] or row.resolution_note != after['note'] or row.resolved_at is not None or (row.trade_date, row.category, row.title) != (before['trade_date'], before['category'], before['title']):
                raise ValueError('修复后记录已有变化，整批恢复取消')
            count = db.execute(update(ReviewActionItemRow).where(
                ReviewActionItemRow.id == row.id, ReviewActionItemRow.status == after['status'],
                ReviewActionItemRow.resolution_note == after['note'], ReviewActionItemRow.resolved_at.is_(None)
            ).values(status='applied', resolution_note=before['note'], resolved_at=datetime.fromisoformat(before['resolved_at']) if before['resolved_at'] else None))
            if count.rowcount != 1:
                raise ValueError('恢复期间状态变化，整批回滚')
            db.add(AgentAudit(actor='user', action='review.archive_repair_restore', target=audit.target,
                before=audit.after, after=audit.before, rollback_ref=digest))
            restored += 1
        db.commit()
        return {'restored': restored, 'digest': digest}
