"""快讯轮询游标/心跳状态（与轮询本体分离，测试可直接注入/断言）。"""

from __future__ import annotations

import time


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
