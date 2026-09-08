from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base


class DailyPickSet(Base):
    """每日精选组合（CONTEXT.md: Daily Picks）。

    每交易日一行（date 唯一）：T 日收盘后生成 T+1 组合并持久化，
    保证「组合一致性」可回溯——复盘与换股门槛都以历史行为准。
    items 为精选卡片数组 JSON（≤5 只），meta 为五维权重与市场快照。
    """

    __tablename__ = "daily_pick_set"
    __table_args__ = (UniqueConstraint("date", name="uq_daily_pick_set_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # 生成日 YYYY-MM-DD
    items: Mapped[str] = mapped_column(String(8192), default="[]")  # JSON：精选卡片数组
    meta: Mapped[str] = mapped_column(String(2048), default="{}")  # JSON：权重/市场状态/候选池统计
    replaced: Mapped[str] = mapped_column(String(1024), default="[]")  # JSON：换股记录 [{out, in, delta}]
    # JSON：深评落选者摘要 [{symbol, name, score, tech, rank}]（≤20 条）——
    # 消融验证（联动方案 P3）的数据地基：tech-only 对照回放 30 日后验收「组合 vs 单维」
    rejected: Mapped[str] = mapped_column(String(16384), default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DailyPickReview(Base):
    """选股复盘日志（CONTEXT.md: Pick Review）。

    每日收盘后对组合逐只的回顾：实际走势 vs 入选理由；走坏原因归类为
    固定枚举（event_expired/board_receding/market_drag/data_issue/news_gap/
    logic_failed/gone_well），供周末元结论统计与权重微调建议。
    """

    __tablename__ = "daily_pick_review"
    __table_args__ = (UniqueConstraint("date", "symbol", name="uq_daily_pick_review_date_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str] = mapped_column(String(12))
    name: Mapped[str | None] = mapped_column(String(64), default=None)
    # good / flat / bad：当日走势判定（相对大盘超额）
    verdict: Mapped[str] = mapped_column(String(8), default="flat")
    # event_expired / board_receding / market_drag / data_issue / news_gap / logic_failed / gone_well
    reason_category: Mapped[str] = mapped_column(String(24), default="gone_well")
    # 相对上证超额收益；None = 大盘基准缺失（评审 B21：绝不拿个股涨幅冒充超额）。
    # 2026-09-08 nullable 化（迁移 f6b2c8e4a9d3）：此前 NOT NULL + default=0.0 会把
    # 基准缺失固化成 0.0，与「超额恰为 0」不可区分（CUSUM/均值统计被污染）。
    excess_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
