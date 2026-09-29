"""Research-only observations and closing census; never trading decisions."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.watchlist import Base


class LeaderResearchObservation(Base):
    __tablename__ = "leader_research_observation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_id: Mapped[str] = mapped_column(String(64), unique=True)
    symbol: Mapped[str] = mapped_column(String(6), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime)
    first_seen: Mapped[datetime] = mapped_column(DateTime)
    version: Mapped[str] = mapped_column(String(48))
    signature: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)


class LeaderResearchSession(Base):
    """One immutable closing census per date, independent of candidate selection."""
    __tablename__ = "leader_research_session"

    trade_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    as_of: Mapped[datetime] = mapped_column(DateTime)
    version: Mapped[str] = mapped_column(String(48))
    payload: Mapped[str] = mapped_column(Text)
