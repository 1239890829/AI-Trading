from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import utcnow
from app.models.watchlist import Base


class AlertRule(Base):
    """用户创建的预警规则。"""

    __tablename__ = "alert_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Integer, default=1)  # SQLite bool
    condition_type: Mapped[str] = mapped_column(String(32), default="price_above")
    # price_above / price_below / change_pct_above / change_pct_below
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    # symbols 存储 JSON 数组字符串；scope=watchlist 时为空
    symbols: Mapped[str | None] = mapped_column(String(512), default=None)
    scope: Mapped[str] = mapped_column(String(16), default="watchlist")  # watchlist / symbols / all
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300)
    # channels 存储 JSON 数组字符串，如 ["in_app", "log"]
    channels: Mapped[str] = mapped_column(String(256), default='["in_app", "log"]')
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    events: Mapped[list["AlertEvent"]] = relationship(
        "AlertEvent", back_populates="rule", cascade="all, delete-orphan", lazy="dynamic"
    )


class AlertEvent(Base):
    """预警触发记录。"""

    __tablename__ = "alert_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(Integer, ForeignKey("alert_rule.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(12), index=True)
    trigger_value: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    acknowledged: Mapped[bool] = mapped_column(Integer, default=0)
    delivered_channels: Mapped[str | None] = mapped_column(String(256), default=None)
    # 触发瞬间的报价快照（JSON）
    snapshot: Mapped[str | None] = mapped_column(String(1024), default=None)

    rule: Mapped["AlertRule"] = relationship("AlertRule", back_populates="events")
