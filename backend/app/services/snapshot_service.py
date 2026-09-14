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
from app.market.sina_market import SinaRateLimited
from app.market.trade_calendar import in_trading_window

log = logging.getLogger(__name__)

#: 休市时段轮询间隔：全市场数据静止（昨收），仍按盘中节奏拉 56 页纯属浪费
#: 且有被 WAF 限流风险（K 线三源全断的前科就是高频请求触发）。
IDLE_INTERVAL_SECONDS = 240.0
#: 常规失败退避上限（指数退避 2^n 封顶）。
BACKOFF_CAP_SECONDS = 300.0
#: 限流冷却**首档**（`SinaRateLimited` / HTTP 456）。连续限流则逐档加倍，
#: 封顶 `RATE_LIMIT_COOLDOWN_CAP_SECONDS`。
#:
#: **为什么是渐进式而不是一个大的固定值**（2026-09-14 实测改口径）：
#: 本环境的新浪限流是**间歇**的——观测窗口内失败 5 次 / 成功 5 次交替
#: （13:42:17✓ 13:43:17✗ 13:45:17✗ 13:49:22✓ 13:50:22✗），两次恢复间隔
#: **均为 245s**（13:33:59✗ → 13:38:04✓；13:45:17✗ → 13:49:22✓；**n=2**）。
#: 若首档就固定 600s，小限流会把数据陈旧度从 ~4 分钟恶化到 10 分钟
#: （而常规退避实测已能在 1–2 轮内恢复）——**用恢复速度换来的封禁保护是虚的**。
#: 保留的意图由封顶值承担：`900s` 显著长于常规退避上限 `300s`，
#: 持续限流时仍会一路退让。
#:
#: ⚠️ **但上面的「恢复间隔 245s」只有 2 个样本，当日即出现反例**（2026-09-14）：
#: 13:56:59 末次成功之后连续失败，至 **14:14:43 实测 `age` 已达 1064s、`fails=3`
#: 仍在用旧数据**；重试间隔实测 240s → 480s →（900s 待发），与档位逐一吻合。
#: ⇒ ① 该反例**反证限流结构化路径确已在生产生效**；② 它同时暴露**本档位的固有代价：
#: **恢复探测延迟最坏 = `RATE_LIMIT_COOLDOWN_CAP_SECONDS`（900s）**，而旧口径在 cap 处
#: 仅 300s ⇒「更保守的退避」换来的是**更慢地发现上游已恢复**。
#: **首档与封顶目前都只据 n=2 定，须更长观测后重估（已挂账本 §6.6 行 23 · 🔶 观察）。**
RATE_LIMIT_COOLDOWN_SECONDS = 240.0
#: 限流冷却上限：显著高于 `BACKOFF_CAP_SECONDS`，体现"限流比普通失败更该退让"。
RATE_LIMIT_COOLDOWN_CAP_SECONDS = 900.0


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
        #: 上一轮失败是否为**上游限流**（HTTP 456）。与 `consecutive_failures`
        #: 分开：前者决定"用多长的冷却"，后者决定"指数退避到几档"，两者判据不同。
        self.rate_limited = False
        self.saved_files = 0
        self._last_save: datetime | None = None

    async def refresh(self) -> None:
        rows = await sina_market.fetch_market_snapshot()
        self.snapshot = rows
        self.breadth = compute_breadth(rows)
        self.last_success = datetime.now(timezone.utc)
        self.last_error = None
        self.consecutive_failures = 0
        # 成功即视为已脱离限流：冷却标记的语义是"当前是否处于限流退避中"，
        # 不随成功一起清零会让一次限流终生压低抓取频率。
        self.rate_limited = False
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
            #
            # 2026-09-14：退避判据抽到 `_next_delay()`，并**新增限流分支**——
            # 原先所有失败（含 456 限流）共用同一套退避，导致限流期内仍在
            # 按常规节奏重试（见 `RATE_LIMIT_COOLDOWN_SECONDS` 注释）。
            live = in_trading_window()
            try:
                await self.refresh()
            except SinaRateLimited as exc:
                # 子类必须排在 `except Exception` 之前，否则被父类兜住、判据失效。
                self.consecutive_failures += 1
                self.last_error = str(exc)
                self.rate_limited = True
                log.warning(
                    "snapshot refresh rate-limited（新浪 WAF 限流第 %s 次，冷却 %.0fs 再试）: %s",
                    self.consecutive_failures, self._next_delay(live=live), exc,
                )
            except Exception as exc:
                self.consecutive_failures += 1
                self.last_error = str(exc)
                self.rate_limited = False
                log.warning("snapshot refresh failed (%s): %s", type(exc).__name__, exc)
            await asyncio.sleep(self._next_delay(live=live))

    def _next_delay(self, *, live: bool) -> float:
        """下一轮抓取前的等待秒数（三重判据，优先级从高到低）。

        1. **限流冷却** —— `SinaRateLimited` 后退避到 `RATE_LIMIT_COOLDOWN_SECONDS`；
        2. **时段降频** —— 休市统一 `IDLE_INTERVAL_SECONDS`（数据静止，没必要勤抓）；
        3. **常规退避** —— 成功用 `poll_interval`；失败按 2^n 指数退避并封顶。

        ⚠️ 限流**优先于休市降频**：那档（240s）是按「上游正常、只是数据静止」
        设计的；限流期用它等于继续按正常节奏敲上游——实测 120s/240s 两轮
        都仍在限流窗口内。
        抽成纯函数是为了**可测**：原先它是 `run()` 里的内联分支，
        `run()` 是 `while True`，只能靠"跑一整天观察日志"验证。
        """
        if self.rate_limited:
            # 渐进式：首档 240s，连续限流逐档加倍，封顶 900s（见常量注释的实测依据）。
            step = max(self.consecutive_failures - 1, 0)
            return min(
                RATE_LIMIT_COOLDOWN_SECONDS * (2 ** min(step, 3)),
                RATE_LIMIT_COOLDOWN_CAP_SECONDS,
            )
        if not live:
            return IDLE_INTERVAL_SECONDS
        if self.consecutive_failures == 0:
            return self.poll_interval
        return min(
            self.poll_interval * (2 ** min(self.consecutive_failures, 4)),
            BACKOFF_CAP_SECONDS,
        )

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
            # 未就绪时把**成因**说清楚：限流是"等一会儿会自愈"，与"源坏了"是
            # 两种处境——前端据此显示「加载中…」而不是当成缺数据（见 kb 三态纪律）。
            reason = (
                "全市场快照尚未就绪（上游限流，正在冷却重试）"
                if self.rate_limited else "全市场快照尚未就绪"
            )
            return Freshness.unavailable(reason=reason, source="sina")
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
