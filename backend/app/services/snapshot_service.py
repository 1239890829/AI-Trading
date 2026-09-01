"""全市场快照服务：轮询抓取 → 宽度计算 → 内存缓存 → Parquet 落库。

- 内存快照供 /api/market/breadth 实时读取；
- Parquet 每 save_interval 写一份（data/parquet/snapshots/YYYYMMDD/HHMMSS.parquet），
  为情绪周期、历史宽度、Phase 5-6 因子与回测提供时点数据底座。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.market import sina_market
from app.market.breadth import compute_breadth

log = logging.getLogger(__name__)


class MarketSnapshotService:
    def __init__(self, poll_interval: float, save_interval: float, parquet_dir: Path):
        self.poll_interval = max(15.0, poll_interval)
        self.save_interval = max(self.poll_interval, save_interval)
        self.parquet_dir = parquet_dir
        self.snapshot: list[dict] = []
        self.breadth: dict | None = None
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_failures = 0
        self.saved_files = 0
        self._last_save: datetime | None = None

    async def refresh(self) -> None:
        rows = await sina_market.fetch_market_snapshot()
        self.snapshot = rows
        self.breadth = compute_breadth(rows)
        self.last_success = datetime.now(timezone.utc)
        self.last_error = None
        self.consecutive_failures = 0
        # parquet 写盘（polars 建表 + 文件 IO）是同步阻塞（评审 B6）：
        # 丢线程池执行，5550 行建表最坏数百毫秒不能卡事件循环
        await asyncio.to_thread(self._maybe_save)
        log.info("market snapshot refreshed: %s stocks, up=%s down=%s limit_up=%s",
                 self.breadth["total"], self.breadth["up"], self.breadth["down"], self.breadth["limit_up"])

    def _maybe_save(self) -> None:
        if self._last_save is not None:
            elapsed = (datetime.now(timezone.utc) - self._last_save).total_seconds()
            if elapsed < self.save_interval:
                return
        try:
            import polars as pl

            from app.services.parquet_store import write_parquet_atomic

            day_dir = self.parquet_dir / "snapshots" / datetime.now(timezone.utc).strftime("%Y%m%d")
            path = day_dir / f"{datetime.now(timezone.utc).strftime('%H%M%S')}.parquet"
            # 原子写（临时文件 + rename）：直接写目标路径时，进程被 kill
            # 会留下大小正常、内容却损坏的 parquet——实测 2026-08-30 就产生了 7 个，
            # 只要最新那份落在其中，选股器与情绪端点就全线 502。
            write_parquet_atomic(
                pl.DataFrame(self.snapshot, infer_schema_length=None), path
            )
            self._last_save = datetime.now(timezone.utc)
            self.saved_files += 1
            log.info("snapshot saved: %s (%s rows)", path.name, len(self.snapshot))
        except Exception:
            log.exception("parquet save failed")

    async def run(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception as exc:
                self.consecutive_failures += 1
                self.last_error = str(exc)
                log.warning("snapshot refresh failed (%s): %s", type(exc).__name__, exc)
            delay = self.poll_interval if self.consecutive_failures == 0 else min(
                self.poll_interval * (2 ** min(self.consecutive_failures, 4)), 300.0
            )
            await asyncio.sleep(delay)

    def breadth_payload(self) -> dict:
        age = None
        if self.last_success is not None:
            age = round((datetime.now(timezone.utc) - self.last_success).total_seconds(), 1)
        return {
            "breadth": self.breadth,
            "snapshot_age_seconds": age,
            "rows": len(self.snapshot),
            "saved_files": self.saved_files,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
        }
