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


class MinuteDecisionRow(Base):
    """做 T 信号决策链（docs/minute-chart-plan.md 模块 5）。

    三段式生命周期：触发即记录（open）→ 30 分钟窗口后结算（correct|wrong|invalid|expired）。
    结算含 leave-one-out 错误归因——剔除哪个指标会翻转结论，那个就是错误主因。
    """
    __tablename__ = "minute_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[str] = mapped_column(String(8), index=True)
    trigger_ts: Mapped[str] = mapped_column(String(32), index=True)  # 触发时刻（UTC ISO）
    signal_price: Mapped[float] = mapped_column(Float)
    bias: Mapped[str] = mapped_column(String(8))                     # 低吸偏向|高抛偏向
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(8), default="medium")
    triggered: Mapped[str] = mapped_column(Text, default="[]")       # JSON: IndicatorHit 列表
    invalidate_condition: Mapped[str] = mapped_column(Text, default="")
    # —— 执行链（结算时自动关联 paper 成交）——
    executed: Mapped[int] = mapped_column(Integer, default=0)
    executed_price: Mapped[float | None] = mapped_column(Float, default=None)
    realized_spread_pct: Mapped[float | None] = mapped_column(Float, default=None)
    # —— 窗口结算 ——
    best_price: Mapped[float | None] = mapped_column(Float, default=None)
    worst_price: Mapped[float | None] = mapped_column(Float, default=None)
    optimal_spread_pct: Mapped[float | None] = mapped_column(Float, default=None)
    outcome: Mapped[str | None] = mapped_column(String(12), default=None, index=True)  # correct|wrong|invalid|expired
    error_attribution: Mapped[str | None] = mapped_column(Text, default=None)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
