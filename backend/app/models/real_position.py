from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base


class RealTrade(Base):
    """真实持仓成交流水（CONTEXT.md: Trade Flow）。

    用户在券商（如同花顺）实际成交后手工录入的账本事实层：按**实际成交价**
    （fill_price）记账，绝不按行情现价。录入后不可篡改（可删除重录）——
    修正历史请删流水重录，或在 Position View 上做覆盖（见 RealPositionOverride）。
    """

    __tablename__ = "real_trade"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str | None] = mapped_column(String(64), default=None)
    # buy / sell
    side: Mapped[str] = mapped_column(String(4), default="buy")
    fill_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # 佣金/税费等合计（可选，默认 0）；买入费用计入成本，卖出费用抵减盈亏
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    traded_at: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    note: Mapped[str | None] = mapped_column(String(256), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RealPositionOverride(Base):
    """真实持仓视图的手动覆盖（CONTEXT.md: Position View 的覆盖层）。

    用户可直接修正某标的的持仓数量与总成本（修正券商数据出入、分红除权等）。
    存在覆盖行时，聚合 API 以覆盖值为准并在前端标注「已手动修正」；
    删除覆盖行即回退到流水聚合值。"""

    __tablename__ = "real_position_override"
    __table_args__ = (UniqueConstraint("symbol", name="uq_real_position_override_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # 覆盖后的总成本（金额）；avg_cost = total_cost / quantity
    total_cost: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(String(256), default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
