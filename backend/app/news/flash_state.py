"""快讯轮询游标/心跳状态（与轮询本体分离，测试可直接注入/断言）。"""

from __future__ import annotations

import time
from datetime import datetime

from sqlalchemy import select

from app.core.bjtime import BJ_TZ, beijing_now_naive
from app.core.db import get_session_factory
from app.models.event import FlashWatermark


class FlashCursor:
    """最近一轮轮询结果的可变状态（进程内，不落盘）。"""

    def __init__(self) -> None:
        self.last_fetch_at: float = 0.0
        self.last_ok: bool | None = None  # None=从未拉取
        self.last_count: int = 0
        self.last_created: int = 0

    def record_fetch(self, ok: bool, count: int) -> None:
        self.last_fetch_at = time.monotonic()
        self.last_ok = ok
        self.last_count = count

    def record_ingest(self, created: int) -> None:
        self.last_created = created

    def snapshot(self) -> dict:
        """三态快照：从未拉取时 last_ok=None（区别于失败 False）。"""
        return {
            "last_fetch_at": self.last_fetch_at,
            "last_ok": self.last_ok,
            "last_count": self.last_count,
            "last_created": self.last_created,
        }


class FlashCheckpointStore:
    """Persist coverage only after a source batch was fully fetched and ingested."""

    def __init__(self, session_factory=None) -> None:
        self._sf = session_factory or get_session_factory()

    def load(self, channels: list[int]) -> dict[int, FlashWatermark]:
        with self._sf() as db:
            return {r.channel: r for r in db.execute(select(FlashWatermark).where(
                FlashWatermark.channel.in_(channels)
            )).scalars()}

    def snapshot(self, channels: list[int]) -> list[dict]:
        rows = self.load(channels)
        return [{
            "channel": channel,
            "last_code": rows[channel].last_code if channel in rows else None,
            "last_show_time": rows[channel].last_show_time.isoformat(sep=" ")
            if channel in rows and rows[channel].last_show_time else None,
            "baseline_at": rows[channel].baseline_at.isoformat(sep=" ")
            if channel in rows and rows[channel].baseline_at else None,
            "last_complete_at": rows[channel].last_complete_at.isoformat(sep=" ")
            if channel in rows and rows[channel].last_complete_at else None,
            "gap_at": rows[channel].gap_at.isoformat(sep=" ")
            if channel in rows and rows[channel].gap_at else None,
            "gap_reason": rows[channel].gap_reason if channel in rows else "never_polled",
        } for channel in channels]

    def record(self, channels: list[int], coverage: dict[int, dict], *, ingest_ok: bool,
               expected_last_codes: dict[int, str] | None = None) -> None:
        now = beijing_now_naive()
        with self._sf() as db:
            for channel in channels:
                row = db.get(FlashWatermark, channel)
                if expected_last_codes is not None and (
                    row is None or row.last_code != expected_last_codes.get(channel)
                ):
                    raise ValueError("flash recovery frontier changed before checkpoint write")
                if row is None:
                    row = FlashWatermark(channel=channel)
                    db.add(row)
                row.last_fetch_at = now
                result = coverage.get(channel) or {}
                newest_code = result.get("newest_code")
                complete = bool(result.get("complete")) and ingest_ok and bool(newest_code)
                if complete:
                    prebaseline_gap = (
                        row.gap_reason == "prebaseline_unverifiable" or
                        (row.gap_at is not None and row.last_code is None and row.baseline_at is None)
                    )
                    row.last_code = newest_code
                    show_time = result.get("newest_show_time")
                    if isinstance(show_time, datetime):
                        row.last_show_time = (show_time if show_time.tzinfo is None else
                                              show_time.astimezone(BJ_TZ).replace(tzinfo=None))
                    row.baseline_at = row.baseline_at or now
                    row.last_complete_at = now
                    if prebaseline_gap:
                        row.gap_reason = "prebaseline_unverifiable"
                    else:
                        row.gap_at = None
                        row.gap_reason = None
                else:
                    row.gap_at = row.gap_at or now
                    row.gap_reason = ("ingest_error" if not ingest_ok else
                                      "missing_source_id" if result.get("complete") and not newest_code else
                                      str(result.get("reason") or "fetch_failed"))
            db.commit()
