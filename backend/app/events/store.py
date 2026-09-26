"""事件驱动选股：EventCard 存储/查询（architecture-design §1 E1）。

抽取（extract.py，纯函数）与存储（本模块）分离。EventStore 只做同步的
入库去重与查询；标的池计算需要异步懒同步成分，由路由层编排（见
app/api/routes/events.py）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from calendar import timegm
from datetime import datetime

from sqlalchemy import Integer, cast, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.db import get_session_factory
from app.events.extract import build_event, dedupe_directions
from app.models.event import (
    EventCard, EventDirection, EventObservation, EventInterpretation, EventWithdrawalLink,
)
from app.core.bjtime import beijing_now_naive

log = logging.getLogger(__name__)


def record_interpretation(db, row: EventCard, observation_id: int, *,
                          state: str, effective_at: datetime, note: str | None = None) -> EventInterpretation:
    """Append the state consumers could see from this time; never rewrite prior evidence."""
    directions = [] if state != "active" else [
        {"target_type": d.target_type, "target": d.target, "direction": d.direction,
         "strength": d.strength, "chain": d.chain, "basis": d.basis,
         "matched_by": d.matched_by, "observation_id": d.observation_id}
        for d in sorted(row.directions, key=lambda item: (item.target_type, item.target))
    ]
    payload = {
        "title": row.title, "summary": row.summary, "url": row.url, "source": row.source,
        "source_tier": row.source_tier, "published_at": row.published_at.isoformat() if row.published_at else None,
        "fact_kind": row.fact_kind, "certainty": row.certainty, "category": row.category,
        "half_life_hours": row.half_life_hours, "source_symbol": row.source_symbol,
        "directions": directions,
    }
    version = EventInterpretation(
        event_id=row.id, observation_id=observation_id, effective_at=effective_at,
        state=state, payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        review_note=note,
    )
    db.add(version)
    db.flush()
    return version


def _interpretation_ref(row: EventCard, version: EventInterpretation | None) -> dict:
    """Identify the current interpretation without inventing legacy history."""
    return {
        "event_id": row.id,
        "version_id": version.id if version else None,
        "observation_id": version.observation_id if version else None,
        "available_at": version.effective_at.isoformat(sep=" ") if version else None,
        "state": version.state if version else "unknown",
    }


def _latest_pending_observation(db, row: EventCard) -> EventObservation | None:
    if row.revision_pending_at is None:
        return None
    return db.execute(select(EventObservation).where(
        EventObservation.event_id == row.id,
        EventObservation.change_kind.in_(("revision", "variant")),
        EventObservation.received_at >= row.revision_pending_at,
    ).order_by(EventObservation.id.desc()).limit(1)).scalar_one_or_none()


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
                    if created or changed:
                        db.flush()
                        record_interpretation(db, row, observation.id,
                                              state="active" if created else "pending",
                                              effective_at=received)
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
            changed = row.status != status
            row.status = status
            if changed:
                latest = db.execute(select(EventObservation.id).where(
                    EventObservation.event_id == event_id
                ).order_by(EventObservation.id.desc()).limit(1)).scalar_one_or_none()
                if latest is not None:
                    record_interpretation(db, row, latest,
                                          state="pending" if row.revision_pending_at else
                                                "active" if status == "active" else "withdrawn",
                                          effective_at=beijing_now_naive(), note=f"状态裁决：{status}")
            db.commit()
            db.refresh(row)
            return row

    def review_revision(self, event_id: int, *, expected_observation_id: int,
                        action: str, note: str, interpretation: dict | None = None) -> EventInterpretation:
        """Resolve a pending revision against the latest exact observation under one transaction."""
        if action not in {"adopt", "retain", "withdraw"}:
            raise ValueError("非法复核动作")
        if not note.strip():
            raise ValueError("复核依据不能为空")
        with self._sf() as db:
            row = db.get(EventCard, event_id)
            if row is None:
                raise LookupError("事件不存在")
            # Corroboration can arrive after a correction. It must not displace the
            # observation being reviewed or silently clear the pending correction.
            latest = _latest_pending_observation(db, row)
            if latest is None or latest.id != expected_observation_id:
                raise ValueError("观察版本已变化或事件无需复核")
            if action != "withdraw" and row.status != "active":
                raise ValueError("非活跃事件不得恢复为可消费状态")
            if action == "adopt":
                if interpretation is None:
                    raise ValueError("采纳修订需要完整人工解释")
                dirs = [dict(d) for d in interpretation["directions"]]
                keys = [(d["target_type"], d["target"]) for d in dirs]
                if len(keys) != len(set(keys)):
                    raise ValueError("方向目标重复")
                source_symbols = json.loads(latest.source_symbols_json)
                for symbol in source_symbols:
                    if symbol.isdigit() and len(symbol) == 6 and ("symbol", symbol) not in keys:
                        dirs.append({
                            "target_type": "symbol", "target": symbol, "direction": 0,
                            "strength": 1, "chain": "来源标的关联，逐股方向待核",
                            "basis": f"修订来源列出 {symbol}；未核定个股影响方向",
                            "matched_by": "source",
                        })
                row.title, row.summary, row.url = latest.title, latest.summary, latest.url
                row.source, row.source_symbol = latest.source, (
                    source_symbols[0] if len(source_symbols) == 1 else None
                )
                for field in ("source_tier", "category", "fact_kind", "certainty", "half_life_hours"):
                    setattr(row, field, interpretation[field])
                for direction in list(row.directions):
                    db.delete(direction)
                db.flush()
                for d in dirs:
                    db.add(EventDirection(event_id=event_id, observation_id=latest.id, **d))
            elif interpretation is not None:
                raise ValueError("保留或撤回无需新解释")
            if action == "withdraw":
                row.status = "rejected"
            row.revision_pending_at = None
            db.flush()
            db.expire(row, ["directions"])
            # The review may race a new observation; a write transaction serializes SQLite writers.
            version = record_interpretation(
                db, row, latest.id, state="withdrawn" if action == "withdraw" else "active",
                effective_at=beijing_now_naive(), note=note.strip(),
            )
            db.commit()
            return version

    def review_symbol_direction(self, event_id: int, *, expected_observation_id: int,
                                expected_interpretation_id: int, symbol: str,
                                direction: int, strength: int, chain: str,
                                basis: str, note: str) -> EventInterpretation:
        """Adjudicate one multi-symbol source link without changing other symbols."""
        if direction not in (-1, 1) or not 1 <= strength <= 5:
            raise ValueError("逐股审定需要有效方向和强度")
        if not all((chain.strip(), basis.strip(), note.strip())):
            raise ValueError("逐股审定需要传导链、来源依据和人工复核记录")
        with self._sf() as db:
            row = db.get(EventCard, event_id)
            if row is None:
                raise LookupError("事件不存在")
            observation = db.get(EventObservation, expected_observation_id)
            if observation is None or observation.event_id != event_id:
                raise ValueError("来源观察不属于当前事件")
            source_symbols = json.loads(observation.source_symbols_json)
            if len(source_symbols) < 2 or symbol not in source_symbols:
                raise ValueError("标的未列于多标的来源观察")

            latest_id = select(func.max(EventInterpretation.id)).where(
                EventInterpretation.event_id == event_id
            ).scalar_subquery()
            # The no-op update obtains the writer lock and checks the version in
            # the same statement. A concurrent review cannot reuse the old head.
            claimed = db.execute(update(EventCard).where(
                EventCard.id == event_id,
                EventCard.status == "active",
                EventCard.revision_pending_at.is_(None),
                latest_id == expected_interpretation_id,
            ).values(status=EventCard.status))
            if claimed.rowcount != 1:
                raise ValueError("事件已变化、待复核或当前解释版本不匹配")
            db.refresh(row)
            latest = db.get(EventInterpretation, expected_interpretation_id)
            if latest.state != "active" or latest.observation_id != expected_observation_id:
                raise ValueError("来源观察并非当前解释依据")
            if row.certainty == "proposed" and strength > 1:
                raise ValueError("拟议事件的方向强度不得超过 1")
            target = db.execute(select(EventDirection).where(
                EventDirection.event_id == event_id,
                EventDirection.target_type == "symbol",
                EventDirection.target == symbol,
            )).scalar_one_or_none()
            if target is None or target.direction != 0 or target.matched_by != "source":
                raise ValueError("标的不是待判的多标的来源关联")
            target.direction = direction
            target.strength = strength
            target.chain = chain.strip()
            target.basis = basis.strip()
            target.matched_by = "manual"
            target.observation_id = observation.id
            db.flush()
            db.expire(row, ["directions"])
            version = record_interpretation(
                db, row, observation.id, state="active",
                effective_at=beijing_now_naive(), note=f"逐股人工审定 {symbol}：{note.strip()}",
            )
            db.commit()
            return version

    def link_withdrawal(self, event_id: int, *, target_observation_id: int,
                        notice_observation_id: int, expected_interpretation_id: int,
                        note: str) -> tuple[EventWithdrawalLink, EventObservation]:
        """Confirm a distinct same-source notice and withdraw the old claim atomically.

        Source IDs alone do not express this relation. The caller supplies two exact
        observations and a human evidence note; no title similarity is inferred.
        """
        basis = note.strip()
        if not basis:
            raise ValueError("撤回关联依据不能为空")
        for attempt in range(2):
            with self._sf() as db:
                try:
                    target = db.get(EventObservation, target_observation_id)
                    notice = db.get(EventObservation, notice_observation_id)
                    if target is None or notice is None:
                        raise LookupError("来源观察不存在")
                    if target.event_id != event_id or notice.event_id == event_id:
                        raise ValueError("撤回通知须来自另一事件并指向本事件观察")
                    if (target.source != notice.source or not target.source_item_id
                            or not notice.source_item_id
                            or target.source_item_id == notice.source_item_id):
                        raise ValueError("撤回关联需要同来源、不同且非空的条目 ID")
                    existing = db.execute(select(EventWithdrawalLink).where(
                        EventWithdrawalLink.target_observation_id == target.id,
                        EventWithdrawalLink.notice_observation_id == notice.id,
                    )).scalar_one_or_none()
                    if existing is not None:
                        if (existing.prior_interpretation_id != expected_interpretation_id
                                or existing.note != basis):
                            raise ValueError("撤回关联已存在但复核版本或依据不同")
                        return existing, notice

                    row = db.get(EventCard, event_id)
                    latest = db.execute(select(EventInterpretation).where(
                        EventInterpretation.event_id == event_id
                    ).order_by(EventInterpretation.id.desc()).limit(1)).scalar_one_or_none()
                    if (row is None or row.status != "active" or row.revision_pending_at is not None
                            or latest is None or latest.id != expected_interpretation_id
                            or latest.state != "active" or latest.observation_id != target.id):
                        raise ValueError("旧事件已变化、待复核或观察并非当前解释依据")
                    now = beijing_now_naive()
                    row.status = "rejected"
                    version = record_interpretation(
                        db, row, target.id, state="withdrawn", effective_at=now, note=basis,
                    )
                    db.flush()
                    link = EventWithdrawalLink(
                        target_observation_id=target.id, notice_observation_id=notice.id,
                        prior_interpretation_id=latest.id, withdrawn_interpretation_id=version.id,
                        note=basis, linked_at=now,
                    )
                    db.add(link)
                    db.commit()
                    return link, notice
                except IntegrityError:
                    db.rollback()
                    if attempt:
                        raise
        raise AssertionError("unreachable")

    # -- read ---------------------------------------------------------------

    @staticmethod
    def evidence_visible(row: EventCard, now: datetime | None = None) -> bool:
        """Current publication and latest interpretation must both be visible."""
        from app.core.bjtime import BJ_TZ, beijing_now_naive as _bj

        now = now or _bj()
        if now.tzinfo is not None:
            now = now.astimezone(BJ_TZ).replace(tzinfo=None)
        published = row.published_at
        if published is None:
            return False
        if published.tzinfo is not None:
            published = published.astimezone(BJ_TZ).replace(tzinfo=None)
        if published > now:
            return False
        ref = getattr(row, "interpretation_ref", None)
        if not isinstance(ref, dict) or ref.get("version_id") is None:
            return True  # No attached version: do not invent a clock for legacy rows.
        try:
            effective = datetime.fromisoformat(ref["available_at"])
            if effective.tzinfo is not None:
                effective = effective.astimezone(BJ_TZ).replace(tzinfo=None)
            return effective <= now
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def is_active(row: EventCard, now: datetime | None = None) -> bool:
        """active = 未被裁决、证据已可见且距发布 < 半衰期 × 2。

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
        if not EventStore.evidence_visible(row, now=now):
            return False
        age_hours = (now - published).total_seconds() / 3600
        return 0 <= age_hours < row.half_life_hours * 2

    def list_events(self, *, active_only: bool = True, limit: int = 30,
                    published_since: datetime | None = None,
                    visible_at: datetime | None = None,
                    status: str | None = None,
                    exclude_pending: bool = False) -> list[EventCard]:
        # 卡片、方向与最新解释须来自同一条 SELECT。分次读取时，修订可在方向与
        # 版本查询之间提交，使旧方向错误地绑定到新的 pending/active 版本。
        from sqlalchemy.orm import joinedload

        now = beijing_now_naive() if active_only else None
        with self._sf() as db:
            latest_id = select(func.max(EventInterpretation.id)).where(
                EventInterpretation.event_id == EventCard.id
            ).correlate(EventCard).scalar_subquery()
            stmt = select(EventCard, EventInterpretation).outerjoin(
                EventInterpretation, EventInterpretation.id == latest_id
            ).options(joinedload(EventCard.directions))
            # Non-active consumers can request an evidence window without
            # half-life filtering. Apply it before LIMIT so future/withdrawn
            # cards cannot crowd out older visible evidence.
            if published_since is not None:
                stmt = stmt.where(EventCard.published_at >= published_since)
            if visible_at is not None:
                stmt = stmt.where(
                    EventCard.published_at <= visible_at,
                    or_(EventInterpretation.id.is_(None),
                        EventInterpretation.effective_at <= visible_at),
                )
            if status is not None:
                stmt = stmt.where(EventCard.status == status)
            if exclude_pending:
                stmt = stmt.where(EventCard.revision_pending_at.is_(None))
            if active_only:
                # 先在 SQL 排除过期卡，再做 LIMIT；否则近期短寿命卡可占满窗口，
                # 把较早但仍有效的政策卡从精选、简报等消费者的结果中挤掉。
                # SQLite julianday 只保留毫秒，边界前 100μs 的活跃卡会误删；
                # DateTime 在本库按 YYYY-MM-DD HH:MM:SS.ffffff 保存，整型微秒
                # 比较与 is_active 的严格 < 判据一致，不依赖宿主时区。
                now_us = timegm(now.timetuple()) * 1_000_000 + now.microsecond
                published_us = (
                    cast(func.strftime("%s", EventCard.published_at), Integer) * 1_000_000
                    + cast(func.substr(EventCard.published_at, 21, 6), Integer)
                )
                stmt = stmt.where(
                    EventCard.status == "active", EventCard.revision_pending_at.is_(None),
                    published_us <= now_us,
                    published_us + EventCard.half_life_hours * 7_200_000_000 > now_us,
                    # 最新解释尚未可见时不能绑定其当前方向；保守留待下一拍，
                    # 旧卡无版本仍以 unknown 保留，不倒填历史身份。
                    or_(EventInterpretation.id.is_(None),
                        EventInterpretation.effective_at <= now),
                )
            pairs = db.execute(
                stmt.order_by(EventCard.published_at.desc()).limit(limit * 3)
            ).unique().all()
            rows = []
            for row, version in pairs:
                row.interpretation_ref = _interpretation_ref(row, version)
                rows.append(row)
        out = [r for r in rows if self.is_active(r, now=now)] if active_only else list(rows)
        return out[:limit]

    def get_event(self, event_id: int) -> EventCard | None:
        # Detail and stock-pool callers serialize directions with this version ref.
        # Keep both in the same SELECT so a concurrent review cannot mix versions.
        from sqlalchemy.orm import joinedload

        with self._sf() as db:
            latest_id = select(func.max(EventInterpretation.id)).where(
                EventInterpretation.event_id == EventCard.id
            ).correlate(EventCard).scalar_subquery()
            stmt = (select(EventCard, EventInterpretation)
                    .outerjoin(EventInterpretation, EventInterpretation.id == latest_id)
                    .options(joinedload(EventCard.directions))
                    .where(EventCard.id == event_id))
            pair = db.execute(stmt).unique().one_or_none()
            if pair is None:
                return None
            row, version = pair
            row.interpretation_ref = _interpretation_ref(row, version)
            return row

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
            # The route filters candidates, but the status can change before
            # this write transaction begins. Never append an active version to
            # a resolved/rejected card.
            if row.status != "active" or row.revision_pending_at is not None:
                return 0
            has_dirs = db.execute(
                select(EventDirection).where(EventDirection.event_id == event_id)
            ).scalars().first() is not None
            metadata_changed = False
            if category and (row.category or "other") == "other" and category != "other":
                row.category = category
                metadata_changed = True
                if half_life_hours:
                    row.half_life_hours = half_life_hours
            if has_dirs or not directions:
                if metadata_changed:
                    basis = db.execute(select(EventObservation.id).where(
                        EventObservation.event_id == event_id
                    ).order_by(EventObservation.id).limit(1)).scalar_one_or_none()
                    if basis is not None:
                        record_interpretation(db, row, basis, state="active",
                                              effective_at=beijing_now_naive(), note="事件类别补全")
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
            if basis is not None:
                db.flush()
                db.expire(row, ["directions"])
                record_interpretation(db, row, basis, state="active",
                                      effective_at=beijing_now_naive(), note="方向补全")
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

    def withdrawal_links_of(self, event_id: int) -> list[tuple[EventWithdrawalLink, EventObservation]]:
        """Return confirmed links with the immutable notice observation they cite."""
        targets = select(EventObservation.id).where(EventObservation.event_id == event_id)
        with self._sf() as db:
            return list(db.execute(select(EventWithdrawalLink, EventObservation).join(
                EventObservation, EventWithdrawalLink.notice_observation_id == EventObservation.id
            ).where(EventWithdrawalLink.target_observation_id.in_(targets))
              .order_by(EventWithdrawalLink.id)).all())

    def interpretations_of(self, event_id: int) -> list[EventInterpretation]:
        with self._sf() as db:
            return list(db.execute(select(EventInterpretation).where(
                EventInterpretation.event_id == event_id
            ).order_by(EventInterpretation.id)).scalars())

    def pending_observation_of(self, event_id: int) -> EventObservation | None:
        with self._sf() as db:
            row = db.get(EventCard, event_id)
            return _latest_pending_observation(db, row) if row else None

    def interpretation_at(self, event_id: int, as_of: datetime) -> EventInterpretation | None:
        """Return a version only after both source publication and version recording."""
        with self._sf() as db:
            return db.execute(select(EventInterpretation).join(
                EventCard, EventCard.id == EventInterpretation.event_id,
            ).where(
                EventInterpretation.event_id == event_id,
                EventInterpretation.effective_at <= as_of,
                EventCard.published_at <= as_of,
            ).order_by(EventInterpretation.effective_at.desc(),
                       EventInterpretation.id.desc()).limit(1)).scalar_one_or_none()
