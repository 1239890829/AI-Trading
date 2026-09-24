from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import utcnow
from app.models.watchlist import Base


class EventCard(Base):
    """事件卡（architecture-design §1）：新闻事件 → 个股机会链路的注册单元。

    抽取自规则引擎（app/events/extract.py），LLM 增强层未接入前全部字段
    由规则产出并带 basis；status 仅存人工裁决（resolved/rejected），
    active/expired 由读取方按 half_life 实时计算。
    """

    __tablename__ = "event_card"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 标题指纹（归一化后 sha1），去重键：同一事件多源/重复推送只注册一次
    fingerprint: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    url: Mapped[str | None] = mapped_column(String(512), default=None)
    # 正文摘要（快讯源 summary 字段；无原文时弹窗降级展示，2026-09-09 补）
    summary: Mapped[str | None] = mapped_column(String(2048), default=None)
    source: Mapped[str] = mapped_column(String(64), default="")
    # 来源分级 1-5：官方公告 5 / 一线权威 4 / 主流财经 3 / 聚合转载 2 / 自媒体 1
    source_tier: Mapped[int] = mapped_column(Integer, default=3)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # fact=事实 / opinion=解读 / rumor=传闻（事实与解读分离，不得混写）
    fact_kind: Mapped[str] = mapped_column(String(8), default="fact")
    # done=已落地 / proposed=拟议 / rumor=传闻
    certainty: Mapped[str] = mapped_column(String(8), default="done")
    # policy=政策 / statement=发言 / data=数据 / rumor=传闻 / corporate=公司 / other
    category: Mapped[str] = mapped_column(String(16), default="other")
    half_life_hours: Mapped[int] = mapped_column(Integer, default=48)
    # 抽取该事件的来源个股（自选新闻采集链路携带；人工注册可为空）
    source_symbol: Mapped[str | None] = mapped_column(String(6), default=None)
    # active 为默认态（不落库，读取方计算）；这里只存人工裁决
    status: Mapped[str] = mapped_column(String(12), default="active")
    # LLM 辅助判定已做时间（P2-3 层1）：null=未试过，非空=已判过（含判中性），
    # 防重复调用烧钱。北京时间 naive，与 published_at 同口径。
    llm_judged_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    # 新观察与当前解释冲突时暂停机会消费；人工复核前不改写旧方向。
    revision_pending_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    directions: Mapped[list["EventDirection"]] = relationship(
        "EventDirection", back_populates="event", cascade="all, delete-orphan"
    )
    observations: Mapped[list["EventObservation"]] = relationship(
        "EventObservation", back_populates="event", cascade="all, delete-orphan"
    )


class EventInterpretation(Base):
    """Immutable decision-visible event state. Legacy cards have no invented history."""

    __tablename__ = "event_interpretation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event_card.id"), index=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("event_observation.id"))
    effective_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    state: Mapped[str] = mapped_column(String(16))  # active | pending | withdrawn
    payload_json: Mapped[str] = mapped_column(Text)
    review_note: Mapped[str | None] = mapped_column(Text)


class FlashWatermark(Base):
    """Durable per-channel 7x24 coverage frontier, independent of process heartbeat."""

    __tablename__ = "flash_watermark"

    channel: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_code: Mapped[str | None] = mapped_column(String(128))
    last_show_time: Mapped[datetime | None] = mapped_column(DateTime)
    baseline_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_complete_at: Mapped[datetime | None] = mapped_column(DateTime)
    gap_at: Mapped[datetime | None] = mapped_column(DateTime)
    gap_reason: Mapped[str | None] = mapped_column(String(128))


class EventObservation(Base):
    """来源原始观察。重复轮询幂等，原文修订追加，时间均为北京时间 naive。"""

    __tablename__ = "event_observation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event_card.id"), index=True)
    observation_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64))
    source_item_id: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(512))
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    # 本系统首次可用于决策的时点；目前采集链等于收到并成功入库的时点。
    available_at: Mapped[datetime] = mapped_column(DateTime)
    source_symbols_json: Mapped[str] = mapped_column(Text, default="[]")
    board_codes_json: Mapped[str] = mapped_column(Text, default="[]")
    change_kind: Mapped[str] = mapped_column(String(24))  # initial | corroboration | revision | variant

    event: Mapped[EventCard] = relationship("EventCard", back_populates="observations")


class EventDirection(Base):
    """事件 → 题材/个股 的方向映射行（architecture-design §1 direction_map）。

    同一事件可对 A 题材 +1、对 B 题材 -1（方向成对分析）；direction=0 表示
    仅确认关联、方向待判（不猜）。每行必带 basis（命中了什么词/规则）。
    """

    __tablename__ = "event_direction"
    __table_args__ = (UniqueConstraint("event_id", "target_type", "target", name="uq_event_direction"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event_card.id"), index=True)
    # theme=题材 / symbol=个股 / macro=宏观
    target_type: Mapped[str] = mapped_column(String(8), default="theme")
    target: Mapped[str] = mapped_column(String(64))
    # -1 利空 / 0 关联待判 / +1 利好
    direction: Mapped[int] = mapped_column(Integer, default=0)
    strength: Mapped[int] = mapped_column(Integer, default=1)
    # 传导链一句话，如 "海外算力受限 → 国产替代需求抬升"
    chain: Mapped[str] = mapped_column(String(256), default="")
    # 判定依据（命中词/规则名），可解释要求
    basis: Mapped[str] = mapped_column(String(256), default="")
    matched_by: Mapped[str] = mapped_column(String(16), default="name")  # name | alias
    # null 表示迁移前的解释，无法恢复其原始观察身份，不能伪造绑定。
    observation_id: Mapped[int | None] = mapped_column(ForeignKey("event_observation.id"), default=None)

    event: Mapped[EventCard] = relationship("EventCard", back_populates="directions")
