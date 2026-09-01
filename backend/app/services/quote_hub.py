from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.data_quality.validator import mark_stale, validate_quote
from app.schemas.market import Quote, utcnow

log = logging.getLogger(__name__)


def _cst_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


def _in_market_hours(dt: datetime) -> bool:
    """含集合竞价与收盘定价时段的宽松交易窗口（09:15-15:05）。"""
    t = dt.hour * 100 + dt.minute
    return 915 <= t <= 1505


async def _empty() -> list:
    """gather 分支占位：自选为空时无 quotes 请求（与旧串行行为一致）。"""
    return []


class QuoteHub:
    """行情缓存与广播中心。

    - 轮询 Provider → Normalizer（Provider 内部）→ Quality Validator → 缓存 → WebSocket
    - 刷新失败时：停止伪造实时数据，把缓存数据标记为 stale 并记录原因
      （2026-09-01 秒级化修订：数据年龄未超 stale_after 的瞬时失败不标不广播——
      1Hz 节奏下单次网络抖动不该让前端闪"数据过期"）
    - 订阅者通过 asyncio.Queue 接收增量推送，消息带自增 seq
    """

    def __init__(
        self,
        provider,
        poll_interval: float,
        get_watchlist: Callable[[], list[str]] | None = None,
        history_len: int = 600,
        stale_after: float = 10.0,
    ):
        self.provider = provider
        self.poll_interval = max(0.5, poll_interval)
        self.stale_after = max(self.poll_interval, stale_after)
        self.get_watchlist = get_watchlist or (lambda: [])
        self.indices: dict[str, Quote] = {}
        self.quotes: dict[str, Quote] = {}
        self.quote_history: deque[tuple[str, datetime, float | None]] = deque(maxlen=history_len)
        # 每个元素是 ([symbols_cell], queue)：symbols 装在单元素列表里以便
        # update_symbols 原地改写（writer 与 reader 共享同一队列，绝不换队列）
        self._subscribers: list[tuple[list[set[str] | None], asyncio.Queue]] = []
        self._seq = 0
        self.last_success_refresh: datetime | None = None
        self.last_attempt: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_failures = 0
        # 休市状态沿触发（红线 2：休市日数据不得冒充实时）
        self._closed_marked = False

    # ---------- 刷新 ----------

    async def refresh(self) -> None:
        self.last_attempt = utcnow()
        try:
            # 指数与自选并行拉取（2026-09-01 秒级化）：串行两次 HTTP 会把
            # 1s 固定节奏的实际周期拉长到 ~1.6s，并行后单周期 ≈ 最慢一路
            watchlist = self._safe_watchlist()
            new_indices, new_quotes = await asyncio.gather(
                self.provider.get_indices(),
                self.provider.get_quotes(watchlist) if watchlist else _empty(),
            )
        except Exception as exc:  # ProviderError、网络错误等一律降级
            self.consecutive_failures += 1
            self.last_error = str(exc)
            age = (
                (utcnow() - self.last_success_refresh).total_seconds()
                if self.last_success_refresh is not None
                else float("inf")
            )
            # 数据年龄超过 stale_after 才标 stale 并广播：瞬时失败（单次网络
            # 抖动、源 5xx 一两轮）期间缓存数据仍远新于阈值，保持 live 语义；
            # 启动后从未成功（age=inf）立即标，绝不无中生有（红线 2）。
            if age >= self.stale_after:
                log.warning(
                    "quote refresh failed (%s): %s — data age %.0fs >= stale_after, marking stale",
                    type(exc).__name__, exc, age,
                )
                self._mark_all_stale()
            else:
                log.warning("quote refresh failed (%s): %s — keeping last good data", type(exc).__name__, exc)
            return
        for q in new_indices:
            prev = self.indices.get(q.symbol)
            validate_quote(q, prev)
            self.indices[q.symbol] = q
        by_symbol = {q.symbol: q for q in new_quotes}
        for symbol in watchlist:
            if symbol in by_symbol:
                q = by_symbol[symbol]
                prev = self.quotes.get(symbol)
                validate_quote(q, prev)
                self.quotes[symbol] = q
                if q.price is not None and q.data_timestamp is not None:
                    self.quote_history.append((q.symbol, q.data_timestamp, q.price))
        self.consecutive_failures = 0
        self.last_error = None
        self.last_success_refresh = utcnow()
        await self._refresh_closed_state()
        # 休市：_refresh_closed_state 已广播 stale（market_closed），不再补发
        # quotes 消息——此前每周期 stale+quotes 连发两条，且 quotes 类型会把
        # 前端状态从"休市"冲回"实时推送"（状态闪烁）。
        if not self._closed_marked:
            self._broadcast("quotes")

    async def _refresh_closed_state(self) -> None:
        """休市判定（红线 2）：非交易日或非交易时段的数据一律标 stale，
        防止休市日"刷新一直成功"把周五收盘数据冒充实时（待办池 #16）。
        日历不可用时返回 None → 不干预（未知不判，保持原行为）。"""
        from app.market import trade_calendar as tc

        verdict: bool | None = None
        try:
            now = _cst_now()
            days = await tc.trading_days(self.provider)
            if days:
                verdict = tc.is_trade_day(days, now.date()) and _in_market_hours(now)
        except Exception as exc:
            log.debug("market-open check unavailable: %s", exc)
            return
        if verdict is False:
            # 每轮重标（2026-09-01 修复：原沿触发只在首轮标 stale，之后 refresh()
            # 又把 validator 判定的新数据存回缓存——盘前质量在 stale/low/invalid
            # 之间震荡，出现"可疑/非法"误标）。休市态稳定为 stale("market_closed")。
            if not self._closed_marked:
                self._closed_marked = True
                log.info("market closed: cached quotes marked stale")
            self._mark_all_stale(reason="market_closed")
        elif verdict is True and self._closed_marked:
            # 重新开盘：恢复由下次校验决定，这里只清标记（数据会被本轮 refresh 刷新）
            self._closed_marked = False

    def _safe_watchlist(self) -> list[str]:
        try:
            return self.get_watchlist()
        except Exception:
            log.exception("watchlist lookup failed; keeping previous watchlist")
            return list(self.quotes.keys())

    def _mark_all_stale(self, reason: str = "refresh_failed") -> None:
        for q in self.indices.values():
            mark_stale(q, reason)
        for q in self.quotes.values():
            mark_stale(q, reason)
        self._broadcast("stale")

    def is_stale(self) -> bool:
        if self.last_success_refresh is None:
            return True
        if self._closed_marked:
            return True  # 休市：最近交易日数据，绝不冒充实时（红线 2）
        return utcnow() - self.last_success_refresh > timedelta(seconds=self.stale_after)

    # ---------- 读取 ----------

    def get_indices(self) -> list[Quote]:
        return list(self.indices.values())

    def get_quotes(self, symbols: list[str] | None = None) -> list[Quote]:
        # 指数兜底：带前缀查询（sh000001，指数详情链路的规范形态）归一化成裸代码查
        # indices，并以查询形态返回（model_copy 不变异共享缓存对象）。
        # ⚠️ 裸 6 位代码**绝不**回退 indices（2026-09-01 P0 修复）：000001 平安银行
        # 与上证指数、000688 国城矿业与科创50 撞码——裸代码查询曾直接命中
        # indices 返回指数数据冒充股票行情（实测 000001 返回上证指数 3979.88）。
        # 项目纪律：裸代码=股票，指数必须带前缀（CONTEXT.md）。
        if symbols:
            out = []
            for s in symbols:
                q = self.quotes.get(s)
                if q is None and len(s) >= 3 and s[:2].lower() in ("sh", "sz", "bj"):
                    q = self.indices.get(s[2:])
                    if q is not None:
                        q = q.model_copy(update={"symbol": s})
                if q is not None:
                    out.append(q)
            return out
        return list(self.quotes.values())

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ---------- 订阅 ----------

    def subscribe(self, symbols: set[str] | None = None) -> asyncio.Queue:
        """注册订阅者。返回的队列终身复用——**改订阅集用 update_symbols，
        绝不 unsubscribe+subscribe 换新队列**：writer 正 parked 在旧队列的
        get() 上，换队列后 writer 永远等在孤儿队列（2026-09-01 实测事故：
        前端 loadBase 触发 subscribe → 推送静默死亡 → 前端 32s 自愈重连 →
        用户体感"约 30 秒才更新一次"）。符号集存单元格以便原地更新。"""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(([symbols, ], queue))
        return queue

    def update_symbols(self, queue: asyncio.Queue, symbols: set[str] | None) -> bool:
        """原地更新订阅集（同一队列，writer 无感）。队列不存在返回 False。"""
        for cell, q in self._subscribers:
            if q is queue:
                cell[0] = symbols
                return True
        return False

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers = [(cell, q) for cell, q in self._subscribers if q is not queue]

    def _broadcast(self, msg_type: str) -> None:
        seq = self.next_seq()
        ts = utcnow().isoformat()
        for cell, queue in self._subscribers:
            symbols = cell[0]
            payload = self.get_quotes(sorted(symbols) if symbols is not None else None)
            if not payload:
                continue
            queue.put_nowait(
                {
                    "type": msg_type,
                    "seq": seq,
                    "ts": ts,
                    "data": [q.model_dump(mode="json") for q in payload],
                }
            )

    # ---------- 后台循环 ----------

    async def run(self) -> None:
        while True:
            started = utcnow()
            await self.refresh()
            elapsed = (utcnow() - started).total_seconds()
            delay = self.poll_interval
            if self.consecutive_failures > 0:
                delay = min(self.poll_interval * (2 ** min(self.consecutive_failures, 4)), 60.0)
                log.info("provider degraded, next refresh in %.0fs", delay)
            elif self._closed_marked:
                # 休市数据静止：降频到 5s 保活（省 Provider 配额/流量），开盘
                # 恢复检测延迟 ≤5s；交易时段（含竞价/午间）保持秒级节奏
                delay = max(delay, 5.0)
            # 固定节奏：扣除本轮刷新耗时，保证推送周期 = poll_interval 而非
            # poll_interval + 网络耗时（1s 档位下串行耗时的稀释不可忽略）
            await asyncio.sleep(max(0.0, delay - elapsed))
