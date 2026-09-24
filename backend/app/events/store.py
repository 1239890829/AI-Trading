"""事件驱动选股：EventCard 存储/查询（architecture-design §1 E1）。

抽取（extract.py，纯函数）与存储（本模块）分离。EventStore 只做同步的
入库去重与查询；标的池计算需要异步懒同步成分，由路由层编排（见
app/api/routes/events.py）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import get_session_factory
from app.events.extract import build_event, dedupe_directions
from app.models.event import EventCard, EventDirection, EventObservation
from app.core.bjtime import beijing_now_naive

log = logging.getLogger(__name__)


class EventStore:
    def __init__(self, session_factory=None):
        self._sf = session_factory or get_session_factory()

    # -- write -------------------------------------------------------------

    def add_event(self, event: dict) -> tuple[EventCard, bool]:
        """按来源 ID / 标题归并事件，逐次保留来源观察；返回 (row, created)。

        内容修订只追加观察并暂停旧解释，不覆盖曾经用于决策的字段/方向。
        并发唯一键冲突重新读一次，确保冲突方观察不会被丢弃。
        """
        source = event.get("source") or ""
        source_item_id = str(event.get("source_item_id") or "").strip() or None
        symbols = list(dict.fromkeys(event.get("source_symbols") or
                                     ([event["source_symbol"]] if event.get("source_symbol") else [])))
        boards = list(dict.fromkeys(event.get("board_codes") or []))
        source_published = event.get("source_published_at")
        content = {
            "title": event["title"].strip(), "summary": (event.get("summary") or "").strip() or None,
            "url": event.get("url"), "source_published_at": source_published.isoformat() if source_published else None,
            "source_symbols": symbols, "board_codes": boards,
        }
        content_hash = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        identity = {"source": source, "source_item_id": source_item_id, "content_hash": content_hash}
        observation_key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

        for attempt in range(2):
            with self._sf() as db:
                try:
                    prior = db.execute(select(EventObservation).where(
                        EventObservation.observation_key == observation_key
                    )).scalar_one_or_none()
                    if prior is not None:
                        return db.get(EventCard, prior.event_id), False

                    # 来源条目 ID 比标题稳定：更正标题仍属于同一来源观察链。
                    row = None
                    if source_item_id:
                        linked = db.execute(select(EventObservation).where(
                            EventObservation.source == source,
                            EventObservation.source_item_id == source_item_id,
                        ).order_by(EventObservation.id.desc()).limit(1)).scalar_one_or_none()
                        if linked is not None:
                            row = db.get(EventCard, linked.event_id)
                    if row is None:
                        row = db.execute(select(EventCard).where(
                            EventCard.fingerprint == event["fingerprint"]
                        )).scalar_one_or_none()
                    created = row is None
                    if created:
                        row = EventCard(
                            fingerprint=event["fingerprint"], title=event["title"],
                            url=event.get("url"), summary=event.get("summary"), source=source,
                            source_tier=event.get("source_tier", 3),
                            published_at=event.get("published_at") or beijing_now_naive(),
                            fact_kind=event.get("fact_kind", "fact"),
                            certainty=event.get("certainty", "done"),
                            category=event.get("category", "other"),
                            half_life_hours=event.get("half_life_hours", 48),
                            source_symbol=event.get("source_symbol"), status="active",
                        )
                        db.add(row)
                        db.flush()

                    source_query = select(EventObservation).where(
                        EventObservation.event_id == row.id,
                        EventObservation.source == source,
                    )
                    source_query = source_query.where(
                        EventObservation.source_item_id == source_item_id
                    ) if source_item_id else source_query.where(EventObservation.source_item_id.is_(None))
                    prior_observation = db.execute(
                        source_query.order_by(EventObservation.id.desc()).limit(1)
                    ).scalar_one_or_none()
                    previous = {
                        "title": prior_observation.title if prior_observation else row.title,
                        "summary": prior_observation.summary if prior_observation else row.summary,
                        "source_published_at": (prior_observation.source_published_at
                                                if prior_observation else None),
                        "symbols": json.loads(prior_observation.source_symbols_json) if prior_observation else
                                   ([row.source_symbol] if row.source_symbol else []),
                    }
                    changed = not created and (
                        content["title"] != previous["title"] or
                        content["summary"] != previous["summary"] or
                        symbols != previous["symbols"] or
                        (prior_observation is not None and
                         source_published != previous["source_published_at"])
                    )
                    received = beijing_now_naive()
                    observation = EventObservation(
                        event_id=row.id, observation_key=observation_key,
                        content_hash=content_hash, source=source, source_item_id=source_item_id,
                        title=content["title"], summary=content["summary"], url=content["url"],
                        source_published_at=source_published, received_at=received,
                        available_at=received, source_symbols_json=json.dumps(symbols, ensure_ascii=False),
                        board_codes_json=json.dumps(boards, ensure_ascii=False),
                        change_kind=("initial" if created else
                                     "revision" if changed and (prior_observation or
                                                               (row.source == source and not source_item_id)) else
                                     "variant" if changed else "corroboration"),
                    )
                    db.add(observation)
                    db.flush()
                    if changed:
                        row.revision_pending_at = row.revision_pending_at or received
                    elif not created and not row.url and event.get("url"):
                        # URL 缺失可补；解释字段保持首次决策时的内容。
                        row.url = event["url"]

                    if created:
                        raw_dirs = event.get("directions") or []
                        dirs = dedupe_directions(raw_dirs)
                        if len(dirs) != len(raw_dirs):
                            log.warning("add_event: 方向行重复，已去重 %d 行（title=%s）",
                                        len(raw_dirs) - len(dirs), event.get("title"))
                        for d in dirs:
                            db.add(EventDirection(
                                event_id=row.id, observation_id=observation.id,
                                target_type=d.get("target_type", "theme"), target=d["target"],
                                direction=d.get("direction", 0), strength=d.get("strength", 1),
                                chain=d.get("chain", ""), basis=d.get("basis", ""),
                                matched_by=d.get("matched_by", "name"),
                            ))
                    db.commit()
                    db.refresh(row)
                    return row, created
                except IntegrityError:
                    db.rollback()
                    if attempt:
                        raise
        raise AssertionError("unreachable")

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
        if row.status != "active" or getattr(row, "revision_pending_at", None) is not None:
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
            stmt = select(EventCard).options(selectinload(EventCard.directions))
            if active_only:
                stmt = stmt.where(EventCard.status == "active", EventCard.revision_pending_at.is_(None))
            rows = db.execute(
                stmt.order_by(EventCard.published_at.desc()).limit(limit * 3)
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
            if row.revision_pending_at is not None:
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
            basis = db.execute(select(EventObservation.id).where(
                EventObservation.event_id == event_id
            ).order_by(EventObservation.id).limit(1)).scalar_one_or_none()
            for d in directions:
                db.add(EventDirection(
                    event_id=event_id,
                    observation_id=basis,
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

    def observations_of(self, event_id: int) -> list[EventObservation]:
        with self._sf() as db:
            return list(db.execute(select(EventObservation).where(
                EventObservation.event_id == event_id
            ).order_by(EventObservation.id)).scalars())
