"""Compare-and-swap manual records. No quote fallback or trading side effects."""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.db import utcnow
from app.models.asset_records import AssetRecord


class RecordConflict(ValueError):
    pass


def serialize(row: AssetRecord) -> dict:
    updated = row.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return {
        "instrument_id": row.instrument_id, "asset_type": row.asset_type,
        "source_symbol": row.source_symbol, "currency": row.currency,
        "name": row.name, "is_watched": row.is_watched,
        "units": float(row.units) if row.units is not None else None,
        "cost_total": float(row.cost_total) if row.cost_total is not None else None,
        "note": row.note, "version": row.version, "updated_at": updated.isoformat(),
        "basis": "manual_not_broker_verified",
    }


def list_records(factory, asset_type: str) -> dict:
    with factory() as db:
        rows = db.scalars(select(AssetRecord).where(AssetRecord.asset_type == asset_type,
                                                   AssetRecord.is_deleted.is_(False))
                          .order_by(AssetRecord.updated_at.desc(), AssetRecord.instrument_id)).all()
        return {"items": [serialize(row) for row in rows]}


def save_record(factory, instrument: dict, values: dict, expected_version: int) -> dict:
    record_id = instrument["instrument_id"]
    payload = {
        "asset_type": instrument["asset_type"], "source_symbol": instrument["source_symbol"],
        "currency": instrument["currency"], "name": values.get("name"),
        "is_watched": values["is_watched"], "units": values.get("units"),
        "cost_total": values.get("cost_total"), "note": values.get("note"), "updated_at": utcnow(),
        "is_deleted": False,
    }
    # Decimal remains the durable value. No FX conversion is implied.
    for key in ("units", "cost_total"):
        if payload[key] is not None:
            payload[key] = Decimal(str(payload[key]))
    with factory() as db:
        written_version = expected_version + 1
        if expected_version == 0:
            prior = db.get(AssetRecord, record_id)
            if prior is not None:
                # Keep a monotonic identity revision across delete/recreate.
                # An old open page must never regain a matching v1 (ABA).
                prior_version = prior.version
                written_version = prior_version + 1
                result = db.execute(update(AssetRecord).where(
                    AssetRecord.instrument_id == record_id,
                    AssetRecord.is_deleted.is_(True), AssetRecord.version == prior_version,
                ).values(**payload, version=written_version))
                if result.rowcount != 1:
                    db.rollback()
                    raise RecordConflict("记录已存在，请读取当前版本后再保存")
            else:
                db.add(AssetRecord(instrument_id=record_id, version=1, **payload))
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise RecordConflict("记录已存在，请读取当前版本后再保存") from exc
        else:
            result = db.execute(update(AssetRecord).where(
                AssetRecord.instrument_id == record_id, AssetRecord.version == expected_version,
                AssetRecord.is_deleted.is_(False),
            ).values(**payload, version=expected_version + 1))
            if result.rowcount != 1:
                db.rollback()
                raise RecordConflict("记录已变更或不存在，请重新读取后再保存")
            db.commit()
        # Return our accepted write, not an unrelated update/delete that may
        # commit before a post-commit SELECT. The next GET reconciles its version.
        return serialize(AssetRecord(instrument_id=record_id, version=written_version, **payload))


def remove_record(factory, record_id: str, expected_version: int) -> dict:
    with factory() as db:
        result = db.execute(update(AssetRecord).where(
            AssetRecord.instrument_id == record_id, AssetRecord.version == expected_version,
            AssetRecord.is_deleted.is_(False),
        ).values(is_deleted=True, version=expected_version + 1, name=None, is_watched=False,
                 units=None, cost_total=None, note=None, updated_at=utcnow()))
        if result.rowcount != 1:
            db.rollback()
            raise RecordConflict("记录已变更或不存在，未删除")
        db.commit()
    return {"instrument_id": record_id, "removed": True}
