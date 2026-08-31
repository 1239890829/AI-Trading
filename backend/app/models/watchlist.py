from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.db import utcnow


class Base(DeclarativeBase):
    pass


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(64), default=None)
    note: Mapped[str | None] = mapped_column(String(256), default=None)
    group_name: Mapped[str] = mapped_column(String(32), default="默认")
    source: Mapped[str] = mapped_column(String(32), default="user")
    quality: Mapped[str] = mapped_column(String(16), default="high")
    version: Mapped[int] = mapped_column(Integer, default=1)
    data_timestamp: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class WatchlistGroup(Base):
    """自选分组（2026-09-01 分组管理，评审报告 A1）。

    此前分组完全由 watchlist.group_name 派生——空分组直接消失，
    "先建组再放股"的操作路径不存在。本表持久化空分组；
    成员仍然以 watchlist.group_name 为准（两表并集即分组清单）。
    """

    __tablename__ = "watchlist_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
