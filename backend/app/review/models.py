"""复盘报告的持久化表。

三张表分工：
- `review_reports`    报告主体，`payload` 存完整 JSON（用于历史 diff 与重放）
- `review_action_items` 改进项单独建表：它是唯一会被"确认/应用/回退"的东西，
  必须能独立检索与更新，不能埋在 JSON 里
- `review_meta_insights` 元结论：方法论自我迭代的证据链

`payload` 与结构化列并存是有意的：结构化列用于**检索与统计**
（如"某方法论版本产生了多少 P0"），payload 用于**完整还原与 diff**。
只存 JSON 会失去检索能力，只存列会丢失细节。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base


class ReviewReportRow(Base):
    __tablename__ = "review_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    trade_date: Mapped[str] = mapped_column(String(8), index=True)
    methodology_version: Mapped[str] = mapped_column(String(16), index=True, default="v1")

    model_requested: Mapped[str] = mapped_column(String(64), default="")
    model_actual: Mapped[str] = mapped_column(String(64), default="")
    model_degraded: Mapped[int] = mapped_column(Integer, default=0)
    model_cost: Mapped[float | None] = mapped_column(Float, default=None)

    summary: Mapped[str] = mapped_column(Text, default="")
    gap_count: Mapped[int] = mapped_column(Integer, default=0)
    action_item_count: Mapped[int] = mapped_column(Integer, default=0)

    payload: Mapped[str] = mapped_column(Text, default="")  # 完整报告 JSON
    report_path: Mapped[str] = mapped_column(String(256), default="")  # 落盘路径

    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ReviewActionItemRow(Base):
    __tablename__ = "review_action_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_id: Mapped[str] = mapped_column(String(40), index=True)
    trade_date: Mapped[str] = mapped_column(String(8), index=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(24), index=True)  # parameter|strategy|data|process
    priority: Mapped[str] = mapped_column(String(4), index=True)  # P0|P1|P2
    expected_impact: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    target: Mapped[str] = mapped_column(String(120), default="")
    proposed_change: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(16), index=True, default="pending")
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class ReviewMetaInsightRow(Base):
    __tablename__ = "review_meta_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_id: Mapped[str] = mapped_column(String(40), index=True)
    trade_date: Mapped[str] = mapped_column(String(8), index=True)
    methodology_version: Mapped[str] = mapped_column(String(16), index=True)
    dimension: Mapped[str] = mapped_column(String(32), index=True)
    observation: Mapped[str] = mapped_column(Text)
    effectiveness: Mapped[str] = mapped_column(String(16), index=True)  # effective|ineffective|unknown
    evidence: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
