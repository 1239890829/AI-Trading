"""事件驱动选股：EventCard 存储/查询（linkage-design §4.4 E1）。

抽取（extract.py，纯函数）与存储（本模块）分离。EventStore 只做同步的
入库去重与查询；标的池计算需要异步懒同步成分，由路由层编排（见
app/api/routes/events.py）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import utcnow, get_session_factory
from app.events.extract import build_event
from app.models.event import EventCard, EventDirection


class EventStore:
    def __init__(self, session_factory=None):
        self._sf = session_factory or get_session_factory()

    # -- write -------------------------------------------------------------

    def add_event(self, event: dict) -> tuple[EventCard, bool]:
        """按指纹去重入库。返回 (row, created)；重复事件返回已有行 + False。

        IntegrityError（并发插入/历史脏数据）优雅降级为「重复」而不是 500。
        """
        with self._sf() as db:
            existing = db.execute(
                select(EventCard).where(EventCard.fingerprint == event["fingerprint"])
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False
            row = EventCard(
                fingerprint=event["fingerprint"],
                title=event["title"],
                url=event.get("url"),
                source=event.get("source") or "",
                source_tier=event.get("source_tier", 3),
                published_at=event.get("published_at") or utcnow(),
                fact_kind=event.get("fact_kind", "fact"),
                certainty=event.get("certainty", "done"),
                category=event.get("category", "other"),
                half_life_hours=event.get("half_life_hours", 48),
                source_symbol=event.get("source_symbol"),
                status="active",
            )
            db.add(row)
            db.flush()
            for d in event.get("directions") or []:
                db.add(EventDirection(
                    event_id=row.id,
                    target_type=d.get("target_type", "theme"),
                    target=d["target"],
                    direction=d.get("direction", 0),
                    strength=d.get("strength", 1),
                    chain=d.get("chain", ""),
                    basis=d.get("basis", ""),
                    matched_by=d.get("matched_by", "name"),
                ))
            try:
                db.commit()
            except IntegrityError:
                # 并发/脏数据触发的唯一约束冲突：按重复处理
                db.rollback()
                existing = db.execute(
                    select(EventCard).where(EventCard.fingerprint == event["fingerprint"])
                ).scalar_one_or_none()
                if existing is None:
                    raise
                return existing, False
            db.refresh(row)
            return row, True

    def register(self, title: str, **kwargs) -> tuple[EventCard, bool]:
        """规则抽取 + 入库一步到位（route 手动注册/批量抽取共用）。"""
        theme_names = kwargs.pop("theme_names", None)
        return self.add_event(build_event(title, theme_names=theme_names, **kwargs))

    def set_status(self, event_id: int, status: str) -> EventCard | None:
        if status not in ("active", "resolved", "rejected"):
            raise ValueError(f"非法状态：{status!r}")
        with self._sf() as db:
            row = db.get(EventCard, event_id)
            if row is None:
                return None
            row.status = status
            db.commit()
            db.refresh(row)
            return row

    # -- read ---------------------------------------------------------------

    @staticmethod
    def is_active(row: EventCard, now: datetime | None = None) -> bool:
        """active = 未被人工裁决 且 现在距发布 < 半衰期 × 2（过期置灰不删，可回溯）。"""
        if row.status != "active":
            return False
        now = now or utcnow()
        published = row.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_hours = (now - published).total_seconds() / 3600
        return age_hours < row.half_life_hours * 2

    def list_events(self, *, active_only: bool = True, limit: int = 30) -> list[EventCard]:
        # selectinload 预加载 directions：session 关闭后 row 是 detached，
        # 懒加载会抛 DetachedInstanceError
        from sqlalchemy.orm import selectinload

        with self._sf() as db:
            rows = db.execute(
                select(EventCard)
                .options(selectinload(EventCard.directions))
                .order_by(EventCard.published_at.desc())
                .limit(limit * 3)
            ).scalars().all()
        out = [r for r in rows if self.is_active(r)] if active_only else list(rows)
        return out[:limit]

    def get_event(self, event_id: int) -> EventCard | None:
        with self._sf() as db:
            return db.get(EventCard, event_id)

    def directions_of(self, event_id: int) -> list[EventDirection]:
        with self._sf() as db:
            return list(
                db.execute(
                    select(EventDirection).where(EventDirection.event_id == event_id)
                ).scalars()
            )
