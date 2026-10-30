"""Manual fund records; never consumed by A-share signals or paper accounts."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base


class AssetRecord(Base):
    __tablename__ = "asset_record"

    instrument_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    asset_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_watched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    units: Mapped[Decimal | None] = mapped_column(Numeric(28, 8), nullable=True)
    cost_total: Mapped[Decimal | None] = mapped_column(Numeric(28, 8), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
