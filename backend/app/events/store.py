"""事件驱动选股：EventCard 存储/查询（linkage-design §4.4 E1）。

抽取（extract.py，纯函数）与存储（本模块）分离。EventStore 只做同步的
入库去重与查询；标的池计算需要异步懒同步成分，由路由层编排（见
app/api/routes/events.py）。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import get_session_factory
from app.events.extract import build_event, dedupe_directions
from app.models.event import EventCard, EventDirection
from app.core.bjtime import beijing_now_naive

log = logging.getLogger(__name__)


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
                # 增量回填：url/summary 修复前入库的旧行这两列为 None；重拉同一
                # 快讯时补上（幂等——仅补缺失，绝不覆盖已有值）。这让「快讯源
                # 带链接/摘要」的修复对存量事件立即生效，而非等自然过期。
                updated = False
                if not existing.url and event.get("url"):
                    existing.url = event["url"]
                    updated = True
                if not existing.summary and event.get("summary"):
                    existing.summary = event["summary"]
                    updated = True
                if updated:
                    db.commit()
                return existing, False
            row = EventCard(
                fingerprint=event["fingerprint"],
                title=event["title"],
                url=event.get("url"),
                summary=event.get("summary"),
                source=event.get("source") or "",
                source_tier=event.get("source_tier", 3),
                published_at=event.get("published_at") or beijing_now_naive(),
                fact_kind=event.get("fact_kind", "fact"),
                certainty=event.get("certainty", "done"),
                category=event.get("category", "other"),
                half_life_hours=event.get("half_life_hours", 48),
                source_symbol=event.get("source_symbol"),
                status="active",
            )
            db.add(row)
            db.flush()
            raw_dirs = event.get("directions") or []
            dirs = dedupe_directions(raw_dirs)
            if len(dirs) != len(raw_dirs):
                # 产生层（build_event）本应已去重。走到这里说明有别的生产者直接
                # 构造了 event dict —— **保住数据、但把问题留在日志里**，不静默吞掉
                # （静默就会变成"某个来源的方向行永远少一行"这种查不出来的偏差）。
                log.warning(
                    "add_event: 方向行存在 (target_type,target) 重复，已去重 %d 行（title=%s）",
                    len(raw_dirs) - len(dirs), event.get("title"),
                )
            for d in dirs:
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
                # 并发/脏数据触发的唯一约束冲突：按重复处理。
                # 注：**同一事件内方向行重复**这一路已在上面按 (target_type,target)
                # 去重消化（2026-09-10），所以走到这里通常只有"并发插入同一指纹"；
                # 只有 fingerprint 也查不到才 re-raise（真异常，如实抛不吞）。
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
        """active = 未被人工裁决 且 现在距发布 < 半衰期 × 2（过期置灰不删，可回溯）。

        2026-09-09 时区口径：published_at 统一北京 naive，now 也取北京 naive
        （此前把北京 naive 当 UTC 解释，age 虚增 8h，事件提前"过期"）。
        """
        if row.status != "active":
            return False

        from app.core.bjtime import BJ_TZ, beijing_now_naive as _bj

        now = now or _bj()
        if now.tzinfo is not None:
            now = now.astimezone(BJ_TZ).replace(tzinfo=None)
        published = row.published_at
        if published is not None and published.tzinfo is not None:
            published = published.astimezone(BJ_TZ).replace(tzinfo=None)
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

    def backfill_event(
        self, event_id: int, *, category: str | None = None,
        half_life_hours: int | None = None, directions: list[dict] | None = None,
    ) -> int:
        """存量补全（2026-09-09，快讯漏传 theme_names 的存量修复）。

        幂等语义：**仅在事件当前没有任何 direction 行时**写入，绝不覆盖已有判定；
        category 仅在旧值为 other（未识别）时升级，避免把已识别类别改坏。
        :returns: 写入的 direction 行数（0 = 无需补/无需改）
        """
        with self._sf() as db:
            row = db.get(EventCard, event_id)
            if row is None:
                return 0
            has_dirs = db.execute(
                select(EventDirection).where(EventDirection.event_id == event_id)
            ).scalars().first() is not None
            if category and (row.category or "other") == "other" and category != "other":
                row.category = category
                if half_life_hours:
                    row.half_life_hours = half_life_hours
            if has_dirs or not directions:
                db.commit()
                return 0
            for d in directions:
                db.add(EventDirection(
                    event_id=event_id,
                    target_type=d.get("target_type") or "theme",
                    target=d.get("target") or "",
                    direction=int(d.get("direction") or 0),
                    strength=int(d.get("strength") or 1),
                    chain=d.get("chain") or "",
                    basis=d.get("basis") or "",
                    matched_by=d.get("matched_by") or "",
                ))
            db.commit()
            return len(directions)

    def directions_of(self, event_id: int) -> list[EventDirection]:
        with self._sf() as db:
            return list(
                db.execute(
                    select(EventDirection).where(EventDirection.event_id == event_id)
                ).scalars()
            )
