"""Execution-owned hunting attempts. PaperOrder remains the fill authority."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base


class HuntingShadowAttempt(Base):
    __tablename__ = "hunting_shadow_attempt"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str] = mapped_column(String(6))
    decision_id: Mapped[str] = mapped_column(String(64), index=True)
    decision_version: Mapped[str] = mapped_column(String(64))
    snapshot_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(256), default="")
    entry_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
