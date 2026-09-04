from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base

#: 账户域：main = 用户交易页签的真实模拟账户；shadow = 每日精选影子持仓
#:（picks-intraday-fusion-assessment.md §4，验证空仓闸门与执行闸门的对照账户）。
SCOPE_MAIN = "main"
SCOPE_SHADOW = "shadow"


class PaperAccount(Base):
    """模拟账户（scope 隔离的多账户：main / shadow）。"""

    __tablename__ = "paper_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(12), default=SCOPE_MAIN, index=True)
    cash: Mapped[float] = mapped_column(Float, default=1_000_000.0)
    initial_cash: Mapped[float] = mapped_column(Float, default=1_000_000.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PaperPosition(Base):
    __tablename__ = "paper_position"
    __table_args__ = (UniqueConstraint("scope", "symbol", name="uq_paper_position_scope_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(12), default=SCOPE_MAIN, index=True)
    symbol: Mapped[str] = mapped_column(String(12), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)  # 总持仓
    frozen_today: Mapped[int] = mapped_column(Integer, default=0)  # 当日买入（T+1 不可卖）
    buy_date: Mapped[str] = mapped_column(String(8), default="")  # 最近买入日 YYYYMMDD
    cost_price: Mapped[float] = mapped_column(Float, default=0.0)  # 摊薄成本
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def available(self) -> int:
        return max(0, self.quantity - self.frozen_today)


class PaperOrder(Base):
    """委托/成交单。status: pending|filled|rejected|cancelled"""

    __tablename__ = "paper_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(12), default=SCOPE_MAIN, index=True)
    symbol: Mapped[str] = mapped_column(String(12), index=True)
    side: Mapped[str] = mapped_column(String(4))  # buy / sell
    price: Mapped[float] = mapped_column(Float)  # 委托价
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    filled_price: Mapped[float | None] = mapped_column(Float, default=None)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str | None] = mapped_column(String(120), default=None)  # 拒绝/失败原因
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
