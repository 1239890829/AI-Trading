"""Durable channel intent and attempts; acceptance is not delivery/read receipt."""
from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.watchlist import Base


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (UniqueConstraint("event_id", "channel", name="uq_outbox_event_channel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("alert_event.id", ondelete="SET NULL"), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    target: Mapped[str] = mapped_column(String(64))  # hash only; no recipient or credential
    payload: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    reason: Mapped[str] = mapped_column(String(128), default="queued")
    created_at_ms: Mapped[int] = mapped_column(BigInteger)
    expires_at_ms: Mapped[int] = mapped_column(BigInteger)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until_ms: Mapped[int | None] = mapped_column(BigInteger)
    send_started_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    accepted_at_ms: Mapped[int | None] = mapped_column(BigInteger)


class NotificationAttempt(Base):
    __tablename__ = "notification_attempt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outbox_id: Mapped[int] = mapped_column(ForeignKey("notification_outbox.id"), index=True)
    lease_token: Mapped[str] = mapped_column(String(64), unique=True)
    started_at_ms: Mapped[int] = mapped_column(BigInteger)
    finished_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(24), default="leased")
    reason: Mapped[str] = mapped_column(String(128), default="claimed")
