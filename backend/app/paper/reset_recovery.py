"""Same-transaction reset backup and guarded, explicit maintenance restoration."""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import DateTime, select, text

from app.models.paper import PaperAccount, PaperOrder, PaperPosition, PaperResetBackup

MODELS = (PaperAccount, PaperPosition, PaperOrder)


def snapshot(db, scope):
    return {model.__tablename__: [
        {column.name: ((value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value).isoformat() if isinstance(value, datetime) else value)
         for column in model.__table__.columns
         for value in [getattr(row, column.name)]}
        for row in db.scalars(select(model).where(model.scope == scope).order_by(model.id))
    ] for model in MODELS}


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def restore(engine, backup_id):
    with engine._sf() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        backup = db.get(PaperResetBackup, backup_id)
        if backup is None or backup.scope != engine.scope:
            raise ValueError("恢复副本不属于当前账户")
        if backup.restored:
            return {"restored": True, "replayed": True}
        latest = db.scalar(select(PaperResetBackup.id).where(
            PaperResetBackup.scope == engine.scope,
        ).order_by(PaperResetBackup.created_at.desc(), PaperResetBackup.id.desc()).limit(1))
        if latest != backup_id or digest(snapshot(db, engine.scope)) != backup.after_digest:
            raise ValueError("重置后账户已有变化，禁止覆盖；请先只读对账")
        payload = json.loads(backup.payload)
        for model in reversed(MODELS):
            db.query(model).filter(model.scope == engine.scope).delete(synchronize_session=False)
        # Recreate original IDs; a conflict in another scope aborts the whole transaction.
        for model in MODELS:
            for row in payload[model.__tablename__]:
                for column in model.__table__.columns:
                    if isinstance(column.type, DateTime) and row[column.name]:
                        row[column.name] = datetime.fromisoformat(row[column.name])
                db.add(model(**row))
        backup.restored = 1
        db.commit()
        return {"restored": True, "replayed": False}
