"""Point-in-time opportunity decisions and their separately versioned outcomes."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base


class OpportunityDecisionSnapshot(Base):
    """Append-only evidence for one symbol at one pipeline stage."""

    __tablename__ = "opportunity_decision_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # ⚠️ 唯一性由**表级 UniqueConstraint** 承担、索引保持非唯一：这不是笔误，而是与
    # **已应用的生产迁移**（`migrations/versions/7d4e2c9a6b1f`，真实库版本号已到该 revision）
    # 逐字对齐的结果。写成 `unique=True, index=True`（unique index）语义等价、但形状不同
    # ⇒ `create_all` 兜底分支建出的库与 alembic 建出的库会分叉（2026-09-16 实测）。
    # 已应用的迁移是历史、不可回改，故以模型对齐迁移，而不是反过来。
    snapshot_id: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime, index=True)
    scenario: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(24), index=True)
    symbol: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    source_theme: Mapped[str] = mapped_column(String(64), default="")
    decision: Mapped[str] = mapped_column(String(24), index=True)
    rank: Mapped[int | None] = mapped_column(Integer, default=None)
    strategy_version: Mapped[str] = mapped_column(String(64))
    feature_version: Mapped[str] = mapped_column(String(64))
    data_state: Mapped[str] = mapped_column(String(16), default="ready")
    # 历史列名：决策时点引用报价（通知/回放参考），**不是成交价**；真实模拟成交只认 PaperOrder.filled_price。
    entry_price: Mapped[float | None] = mapped_column(Float, default=None)
    evidence: Mapped[str] = mapped_column(Text, default="{}")
    # ── 场景化 KB 路由的记录项（蓝图 §5，`RSH-027` 切片 1）─────────────────────
    # `kb_ids` = 该决策**实际引用并采纳**的 KB 条目 ID（JSON 数组，保序去重）。
    # `kb_refs` = 引用状态 + 支持/冲突依据（JSON 对象），形如
    #   `{"state": "not_consulted"|"cited"|"rejected", "status": {...}, "support": [...], "conflict": {...}}`。
    # ⚠️ **`state` 不可省**：`kb_ids == "[]"` 既可能是「本就没引 KB」（现状），
    # 也可能是「引了但全被驳回」（异常）——两者在读取侧必须可区分
    # （「规则缺失」与「无事件」不可同形，`BUG-016` 教训）。写入一律走
    # `kb_routing.snapshot_citations()`，别处不得手搓这两个字段。
    # ⚠️ 本列**不参与**任何决策：KB 进入个股收益打分须先过有/无 KB 消融
    # （蓝图 §5），由 `kb_routing.assert_scoring_admission_is_evidence_gated()` 拦。
    kb_ids: Mapped[str] = mapped_column(Text, default="[]")
    kb_refs: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("snapshot_id"),
        UniqueConstraint(
            "run_id", "stage", "symbol", "source_theme",
            name="uq_opportunity_snapshot_run_stage_symbol_theme",
        ),
    )


class OpportunityOutcomeLabel(Base):
    """Outcome attached later without rewriting the point-in-time snapshot."""

    __tablename__ = "opportunity_outcome_label"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("opportunity_decision_snapshot.snapshot_id"), index=True
    )
    horizon: Mapped[str] = mapped_column(String(16), default="d0_close")
    target_date: Mapped[str] = mapped_column(String(10), index=True)
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    label: Mapped[str] = mapped_column(String(16), default="unknown")
    reference_price: Mapped[float | None] = mapped_column(Float, default=None)
    outcome_price: Mapped[float | None] = mapped_column(Float, default=None)
    return_pct: Mapped[float | None] = mapped_column(Float, default=None)
    # ── 成本与可成交性口径（`RSH-026` 第二批，2026-09-16）与 `return_pct` **并列而非替换** ──
    # `return_pct` = 信号方向毛收益（决策时点价 → D0 收盘，不含成本，恒有值）；
    # `net_return_pct` 是兼容历史 schema 的列名。对当前 `d0_close` 标签，它是
    # 「决策价→同日收盘」扣双边成本后的**D0 成本调整代理**，不是 A 股 T+1 下可实现净收益。
    # 仅 `fill_state == "ok"` 时有值；不可成交/无现价留 `None`，严禁记 0。
    fill_state: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    cost_pct: Mapped[float | None] = mapped_column(Float, default=None)
    net_return_pct: Mapped[float | None] = mapped_column(Float, default=None)
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="daily_close")
    # RSH-026 path outcomes: separate future/result facts from the immutable decision snapshot.
    # `path_state` is explicit because NULL/zero cannot distinguish "not collected" from "no excursion".
    path_state: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    path_high_price: Mapped[float | None] = mapped_column(Float, default=None)
    path_low_price: Mapped[float | None] = mapped_column(Float, default=None)
    mfe_pct: Mapped[float | None] = mapped_column(Float, default=None)
    mae_pct: Mapped[float | None] = mapped_column(Float, default=None)
    path_bar_count: Mapped[int | None] = mapped_column(Integer, default=None)
    limit_state: Mapped[str] = mapped_column(String(24), default="unknown")
    first_limit_time: Mapped[str | None] = mapped_column(String(8), default=None)
    time_to_limit_minutes: Mapped[float | None] = mapped_column(Float, default=None)
    path_source: Mapped[str] = mapped_column(String(64), default="")
    path_version: Mapped[str] = mapped_column(String(32), default="")
    path_reason: Mapped[str] = mapped_column(Text, default="")
    # Terminal resolution time for either labeled or explicit terminal-unknown path facts.
    path_resolved_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "horizon", name="uq_opportunity_outcome_snapshot_horizon"),
    )
