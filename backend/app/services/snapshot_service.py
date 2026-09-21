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
from typing import Any

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

#: 新浪主源限流时的降级快照完整度门。低于主源自身 90% 完整性纪律就拒绝发布，
#: 不用一小撮报价冒充“全市场”。
FALLBACK_MIN_COVERAGE = 0.90
FALLBACK_BATCH_SIZE = 50
FALLBACK_SOURCE = "quote_fallback"
FALLBACK_REASON = "新浪全市场源限流，使用既有股票池 + 批量行情备源降级快照"


class MarketSnapshotService:
    def __init__(
        self, poll_interval: float, save_interval: float, parquet_dir: Path, *,
        quote_hub: Any | None = None,
    ):
        self.poll_interval = max(15.0, poll_interval)
        self.save_interval = max(self.poll_interval, save_interval)
        self.parquet_dir = parquet_dir
        self.quote_hub = quote_hub
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
        # One refresh worth of rows + fact time, swapped as one Python object so
        # readers can never pair snapshot B with last_success A mid-refresh.
        self._snapshot_version: tuple[datetime, tuple[dict, ...]] | None = None
        # Exact durable version metadata. ``saved_files`` is incremented only after
        # these two fields are set, making the counter a safe scheduler cursor.
        self.last_saved_path: Path | None = None
        self.last_saved_as_of: datetime | None = None
        self.last_saved_state: str | None = None
        self.last_saved_source: str | None = None
        self.last_saved_reason: str | None = None
        self.last_snapshot_source: str | None = None
        self.last_degraded_reason: str | None = None
        self.last_fallback_coverage: float | None = None
        self.last_save_error: str | None = None
        self.consecutive_save_failures = 0

    async def refresh(self) -> None:
        rows = await sina_market.fetch_market_snapshot()
        breadth = compute_breadth(rows)
        success_at = datetime.now(timezone.utc)
        # Publish public fields, then atomically swap the evidence tuple.  A reader
        # racing this refresh sees either the complete previous version or this one.
        self.snapshot = rows
        self.breadth = breadth
        self.last_success = success_at
        self._snapshot_version = (success_at, tuple(dict(row) for row in rows))
        self.last_error = None
        self.consecutive_failures = 0
        self.last_snapshot_source = "sina"
        self.last_degraded_reason = None
        self.last_fallback_coverage = None
        # 成功即视为已脱离限流：冷却标记的语义是"当前是否处于限流退避中"，
        # 不随成功一起清零会让一次限流终生压低抓取频率。
        self.rate_limited = False
        # parquet 写盘（polars 建表 + 文件 IO）是同步阻塞（评审 B6）：
        # 丢线程池执行，5550 行建表最坏数百毫秒不能卡事件循环
        await asyncio.to_thread(self._maybe_save)
        log.info("market snapshot refreshed: %s stocks, up=%s down=%s limit_up=%s",
                 self.breadth["total"], self.breadth["up"], self.breadth["down"], self.breadth["limit_up"])

    def _fallback_universe_rows(self) -> tuple[list[dict], str]:
        """取 fallback 股票全集；优先当前冻结版本，冷启动退到最新可读 Parquet。"""
        rows, _as_of = self.versioned_snapshot()
        if rows:
            return rows, "memory"

        from app.services.parquet_store import read_latest_snapshot

        read = read_latest_snapshot(self.parquet_dir)
        if not read.ok or read.df is None:
            raise RuntimeError(
                "fallback 无可用股票池：内存快照为空，且没有可读的历史 durable snapshot"
            )
        rows = [dict(row) for row in read.df.to_dicts()]
        if not rows:
            raise RuntimeError("fallback 最新 durable snapshot 为空")
        return rows, f"parquet:{read.path}"

    @staticmethod
    def _fallback_row(base: dict, quote, *, now: datetime) -> dict:
        """把批量备源 Quote 映射回全市场 snapshot 行；动态字段绝不沿用旧值。"""
        row = dict(base)
        dynamic = (
            "price", "open", "high", "low", "prev_close", "change",
            "change_pct", "volume", "amount", "turnover_rate", "ticktime",
        )
        if quote is None:
            for key in dynamic:
                row[key] = None
            row["source"] = f"{FALLBACK_SOURCE}:missing"
            row["received_at"] = now.isoformat()
            return row

        row.update({
            "symbol": str(quote.symbol).zfill(6),
            "name": quote.name or row.get("name"),
            "market": quote.market or row.get("market"),
            "price": quote.price,
            "open": quote.open,
            "high": quote.high,
            "low": quote.low,
            "prev_close": quote.prev_close,
            "change": quote.change,
            "change_pct": quote.change_pct,
            "volume": quote.volume,
            "amount": quote.amount,
            "turnover_rate": quote.turnover_rate,
            "mktcap": (quote.total_mktcap_yi * 1e4
                       if quote.total_mktcap_yi is not None else row.get("mktcap")),
            "nmc": (quote.float_mktcap_yi * 1e4
                    if quote.float_mktcap_yi is not None else row.get("nmc")),
            "ticktime": (quote.data_timestamp.isoformat()
                         if quote.data_timestamp is not None else None),
            "source": f"{quote.source or 'unknown'}_fallback",
            "received_at": now.isoformat(),
        })
        return row

    async def refresh_fallback(self) -> float:
        """新浪限流时用既有 universe + 批量行情备源生成**显式 degraded**快照。"""
        if self.quote_hub is None:
            raise RuntimeError("snapshot fallback 未注入 quote_hub")

        from app.services.quote_enrich import fetch_quotes_batched

        base_rows, universe_source = await asyncio.to_thread(self._fallback_universe_rows)
        symbols = sorted({
            str(row.get("symbol") or "").zfill(6)
            for row in base_rows
            if str(row.get("symbol") or "").isdigit()
            and len(str(row.get("symbol") or "")) <= 6
        })
        if not symbols:
            raise RuntimeError("snapshot fallback 股票池无有效 symbol")

        found = await fetch_quotes_batched(
            self.quote_hub, symbols, prefer_cache=False, batch_size=FALLBACK_BATCH_SIZE
        )
        # 盘中只把“当前可据此下结论”的报价计入覆盖率。HTTP 200 也可能
        # 携带旧时点/invalid 行，不能把它们算成 100% 的假覆盖。
        if in_trading_window():
            fresh_within = max(self.poll_interval * 3, 60.0)
            found = {
                symbol: quote for symbol, quote in found.items()
                if quote is not None
                and quote.freshness(fresh_within=fresh_within).state == "ready"
                and isinstance(quote.price, (int, float)) and quote.price > 0
            }
        coverage = len(found) / len(symbols)
        if coverage < FALLBACK_MIN_COVERAGE:
            raise RuntimeError(
                f"snapshot fallback coverage {coverage:.1%} < {FALLBACK_MIN_COVERAGE:.0%}"
            )

        now = datetime.now(timezone.utc)
        by_symbol = {str(row.get("symbol") or "").zfill(6): row for row in base_rows}
        rows = [
            self._fallback_row(by_symbol[symbol], found.get(symbol), now=now)
            for symbol in symbols
        ]
        breadth = compute_breadth(rows)
        self.snapshot = rows
        self.breadth = breadth
        self.last_success = now
        self._snapshot_version = (now, tuple(dict(row) for row in rows))
        self.last_snapshot_source = FALLBACK_SOURCE
        self.last_degraded_reason = FALLBACK_REASON
        self.last_fallback_coverage = round(coverage, 4)
        # 主源仍处于限流；刻意不清 consecutive_failures / rate_limited / last_error。
        await asyncio.to_thread(self._maybe_save)
        log.warning(
            "market snapshot fallback refreshed: %s/%s (%.1f%%), universe=%s",
            len(found), len(symbols), coverage * 100, universe_source,
        )
        return coverage

    async def _wait_rate_limit_with_fallback(self, delay: float) -> None:
        """主源冷却期间按 save_interval 补 degraded snapshot，不额外探测新浪。"""
        remaining = max(0.0, delay)
        while remaining > 0:
            step = min(remaining, self.save_interval)
            await asyncio.sleep(step)
            remaining -= step
            if remaining <= 0:
                break
            try:
                await self.refresh_fallback()
            except Exception as exc:  # noqa: BLE001 -- fallback 失败不能打断主源恢复探测
                log.warning("snapshot fallback during rate-limit cooldown failed: %s", exc)


    def versioned_snapshot(self) -> tuple[list[dict], datetime | None]:
        """Return one internally consistent in-memory snapshot version."""
        version = self._snapshot_version
        if version is None:
            return [dict(row) for row in self.snapshot], self.last_success
        as_of, rows = version
        return [dict(row) for row in rows], as_of


    def _maybe_save(self) -> None:
        if self._last_save is not None:
            elapsed = (datetime.now(timezone.utc) - self._last_save).total_seconds()
            if elapsed < self.save_interval:
                return
        try:
            import polars as pl

            from app.services.parquet_store import write_parquet_atomic

            version = self._snapshot_version
            if version is None:
                snapshot_as_of = self.last_success
                rows = [dict(row) for row in self.snapshot]
            else:
                snapshot_as_of, frozen = version
                rows = [dict(row) for row in frozen]
            now = datetime.now(timezone.utc)
            day_dir = self.parquet_dir / "snapshots" / now.strftime("%Y%m%d")
            path = day_dir / f"{now.strftime('%H%M%S')}.parquet"
            # 原子写（临时文件 + rename）：直接写目标路径时，进程被 kill
            # 会留下大小正常但内容损坏的 parquet。写成功后再发布 durable 元数据，
            # 最后递增 cursor，scheduler 因而不会看到“计数已新、路径仍旧”的半状态。
            write_parquet_atomic(pl.DataFrame(rows, infer_schema_length=None), path)
            self._last_save = now
            self.last_saved_path = path
            self.last_saved_as_of = snapshot_as_of or now
            saved_freshness = self.freshness()
            self.last_saved_state = saved_freshness.state
            self.last_saved_source = saved_freshness.source
            self.last_saved_reason = saved_freshness.reason
            self.last_save_error = None
            self.consecutive_save_failures = 0
            self.saved_files += 1
            log.info("snapshot saved: %s (%s rows)", path.name, len(rows))
        except Exception as exc:
            self.consecutive_save_failures += 1
            self.last_save_error = str(exc)
            log.exception("parquet save failed")

    async def run(self, *, first_delay: float = 0.0) -> None:
        """常驻轮询循环。

        **首轮错峰（`OPS-001`，2026-09-14）**：`first_delay > 0` 时，**首轮之前**等一次
        （默认 `0.0` = 保持既有行为，既有调用方与测试不受影响）。

        为什么是「**延迟**」而不是「**轮内降速**」——后者已被两次实测否定：

        1. **事故当轮的首个请求就 456**（`stock count`，见 `RATE_LIMIT_STATUS` 注释）
           ⇒ 轮内手段在结构上救不了首请求；
        2. **慢的反而先被封**：探针 `concurrency=1 + 批间隔 0.15s`（≈2.7 请求/秒）
           在**第 32 页**被封，而生产全速那轮（`concurrency=6`、无间隔，≈10+ 请求/秒）
           成功 ⇒ 判据**不是瞬时速率**，而是**时间窗内的累计请求数**。

        该模型下延迟是唯一有效手段：本任务单独跑（生产常态，休市 240s / 盘中 60s
        一轮 57 个请求）**已被长期验证安全**，问题只在启动瞬间与
        「26 个常驻任务首批 tick + `hub.refresh()`」**叠加**把窗口配额打满。
        故只需把首轮整体推后到突发窗口之后，**不必降低自身速率**。
        """
        if first_delay > 0:
            log.info("market snapshot 首轮延迟 %.0fs 启动（OPS-001 冷启动错峰）", first_delay)
            await asyncio.sleep(first_delay)
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
                delay = self._next_delay(live=live)
                log.warning(
                    "snapshot refresh rate-limited（新浪 WAF 限流第 %s 次，冷却 %.0fs 再试）: %s",
                    self.consecutive_failures, delay, exc,
                )
                try:
                    await self.refresh_fallback()
                except Exception as fallback_exc:  # noqa: BLE001
                    log.warning("snapshot fallback after rate-limit failed: %s", fallback_exc)
                await self._wait_rate_limit_with_fallback(delay)
                continue
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
        source = self.last_snapshot_source or "sina"
        f = Freshness.from_age(
            as_of=self.last_success, fresh_within=fresh_within, source=source,
            missing_reason="从未成功刷新过全市场快照，无法判定新鲜度",
        )
        if self.last_degraded_reason and f.state == "ready":
            return Freshness.degraded(
                as_of=f.as_of, age_seconds=f.age_seconds, source=source,
                reason=self.last_degraded_reason,
            )
        if self.consecutive_failures and f.state == "ready":
            return Freshness.degraded(
                as_of=f.as_of, age_seconds=f.age_seconds, source=source,
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
            "last_saved_path": str(self.last_saved_path) if self.last_saved_path is not None else None,
            "last_saved_as_of": self.last_saved_as_of.isoformat() if self.last_saved_as_of else None,
            "last_saved_state": self.last_saved_state,
            "consecutive_save_failures": self.consecutive_save_failures,
            "last_save_error": self.last_save_error,
            "snapshot_source": self.last_snapshot_source,
            "fallback_coverage": self.last_fallback_coverage,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
        }
