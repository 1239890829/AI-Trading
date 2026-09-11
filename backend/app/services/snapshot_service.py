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

from app.core.freshness import Freshness
from app.market import sina_market
from app.market.breadth import compute_breadth
from app.market.trade_calendar import in_trading_window

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
            # 时段感知降频（评审 O6，2026-09-01）：休市时段全市场数据静止
            # （昨收），仍 60s×56 页拉新浪纯属浪费且有被 WAF 限流风险
            # （K 线三源全断的前科就是高频请求触发）。连续竞价窗口外统一
            # 降到 240s 一轮，保底新鲜度（重启后盘前仍有昨收宽度数据）；
            # 窗口内保持原轮询与失败退避。
            live = in_trading_window()
            try:
                await self.refresh()
            except Exception as exc:
                self.consecutive_failures += 1
                self.last_error = str(exc)
                log.warning("snapshot refresh failed (%s): %s", type(exc).__name__, exc)
            if not live:
                await asyncio.sleep(240.0)
                continue
            delay = self.poll_interval if self.consecutive_failures == 0 else min(
                self.poll_interval * (2 ** min(self.consecutive_failures, 4)), 300.0
            )
            await asyncio.sleep(delay)

    def freshness(self) -> Freshness:
        """全市场快照的新鲜度（S2-1 契约的 **snapshot 样板**）。

        为什么不能只看 `breadth is None`（S1-3 实测缺陷）：`market_context` 过去
        只判"有没有"，于是**20 分钟前的宽度**配上当前涨停池照样算出「阶段」——
        数字全都合理、结论是错的，且界面上看不出任何异常。

        新鲜窗口用 **`poll_interval × 3`** 而不是固定秒数：轮询周期本身就是这个
        数据源的固有节奏（盘中 60s、休市 240s），写死一个常量会在休市时段
        恒定误报 stale。连续失败次数 >0 但数据仍在窗口内时降级为 `degraded`
        ——"数据还新鲜，但上游正在出问题"是需要提前知道的信号。
        """
        if self.breadth is None:
            return Freshness.unavailable(reason="全市场快照尚未就绪", source="sina")
        fresh_within = self.poll_interval * 3
        f = Freshness.from_age(
            as_of=self.last_success, fresh_within=fresh_within, source="sina",
            missing_reason="从未成功刷新过全市场快照，无法判定新鲜度",
        )
        if self.consecutive_failures and f.is_usable():
            return Freshness.degraded(
                as_of=f.as_of, age_seconds=f.age_seconds, source="sina",
                reason=f"上游连续失败 {self.consecutive_failures} 次，当前用的是上一次成功数据",
            )
        return f

    def breadth_payload(self) -> dict:
        age = None
        if self.last_success is not None:
            age = round((datetime.now(timezone.utc) - self.last_success).total_seconds(), 1)
        return {
            "breadth": self.breadth,
            # S2-1：统一降级契约。`snapshot_age_seconds` 等旧字段**保留**——
            # 助手工具与前端已在消费，本次只做"新增统一口径"，不做静默替换。
            "freshness": self.freshness().model_dump(mode="json"),
            "snapshot_age_seconds": age,
            "rows": len(self.snapshot),
            "saved_files": self.saved_files,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
        }
