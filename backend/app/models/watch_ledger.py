"""盘中跟踪台账 ORM（猎场批次 A，需求 7/8/9/10/11 持久化底座）。"""
from __future__ import annotations

from sqlalchemy import Integer, String, Float, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base


class WatchLedger(Base):
    """当日盘中跟踪台账：首见登记、收盘清算、逐股判定、统计底座。

    UniqueConstraint(trade_date, symbol)：当日同股唯一——首见登记（INSERT OR
    IGNORE 语义），后续 sighting 不覆盖已有 reason（入选依据是首见时刻的证据）。
    """

    __tablename__ = "watch_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(32), default="")
    layer: Mapped[str] = mapped_column(String(16), default="today_strongest")
    source_theme: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    is_leader: Mapped[int] = mapped_column(Integer, default=0)
    boards: Mapped[int] = mapped_column(Integer, default=0)
    entry_price: Mapped[float | None] = mapped_column(Float, default=None)
    entry_time: Mapped[str] = mapped_column(String(8), default="")
    status: Mapped[str] = mapped_column(String(16), default="tracking", index=True)
    close_price: Mapped[float | None] = mapped_column(Float, default=None)
    pnl_pct: Mapped[float | None] = mapped_column(Float, default=None)
    verdict: Mapped[str | None] = mapped_column(String(16), default=None)
    verdict_reason: Mapped[str | None] = mapped_column(Text, default=None)
    merged_into_picks: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("trade_date", "symbol", name="uq_watch_ledger_date_symbol"),
    )
