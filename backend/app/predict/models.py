"""预判报告的持久化表（与复盘三表同一模式：结构化列检索 + payload 全量）。

两张表分工：
- `prediction_reports` 报告主体（按 target_date 覆盖——同一目标日的最新预判才有效）
- `prediction_themes`  题材级明细行：验证命中率统计（按消息级别/题材/环境分层）
  是方法论迭代的直接输入，必须可独立检索，不能埋 JSON。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base


class PredictionReportRow(Base):
    __tablename__ = "prediction_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    target_date: Mapped[str] = mapped_column(String(8), index=True)  # 预判目标交易日
    context: Mapped[str] = mapped_column(String(16), index=True)  # weekend|holiday|evening|...
    engine_version: Mapped[str] = mapped_column(String(16), default="v1")
    trigger: Mapped[str] = mapped_column(String(16), default="manual")

    theme_count: Mapped[int] = mapped_column(Integer, default=0)
    verdict_summary: Mapped[str] = mapped_column(String(200), default="")
    verify_status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending|verified

    payload: Mapped[str] = mapped_column(Text, default="")
    report_path: Mapped[str] = mapped_column(String(256), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class PredictionThemeRow(Base):
    __tablename__ = "prediction_themes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[str] = mapped_column(String(40), index=True)
    target_date: Mapped[str] = mapped_column(String(8), index=True)
    theme: Mapped[str] = mapped_column(String(60), index=True)
    verdict: Mapped[str] = mapped_column(String(12), index=True)  # 预判成立|可能成立|弱预期|不预判
    score: Mapped[float] = mapped_column(Float, default=0.0)
    engine_version: Mapped[str] = mapped_column(String(16), default="v1")

    # —— 目标日收盘后回填 ——
    verify_outcome: Mapped[str | None] = mapped_column(String(12), default=None, index=True)  # hit|partial|miss|expired
    formed: Mapped[int | None] = mapped_column(Integer, default=None)  # 题材当日是否成立（涨停家数≥3）
    limit_up_count_d1: Mapped[int | None] = mapped_column(Integer, default=None)
    leader_actual: Mapped[str | None] = mapped_column(String(60), default=None)  # ".symbol name x板"
    leader_hit: Mapped[int | None] = mapped_column(Integer, default=None)
    verify_note: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
