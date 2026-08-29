from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Callable

from app.data_quality.validator import mark_stale, validate_quote
from app.schemas.market import Quote, utcnow

log = logging.getLogger(__name__)


class QuoteHub:
    """行情缓存与广播中心。

    - 轮询 Provider → Normalizer（Provider 内部）→ Quality Validator → 缓存 → WebSocket
    - 刷新失败时：停止伪造实时数据，把缓存数据标记为 stale 并记录原因
    - 订阅者通过 asyncio.Queue 接收增量推送，消息带自增 seq
    """

    def __init__(
        self,
        provider,
        poll_interval: float,
        get_watchlist: Callable[[], list[str]] | None = None,
        history_len: int = 600,
    ):
        self.provider = provider
        self.poll_interval = max(1.0, poll_interval)
        self.get_watchlist = get_watchlist or (lambda: [])
        self.indices: dict[str, Quote] = {}
        self.quotes: dict[str, Quote] = {}
        self.quote_history: deque[tuple[str, datetime, float | None]] = deque(maxlen=history_len)
        self._subscribers: list[tuple[set[str] | None, asyncio.Queue]] = []
        self._seq = 0
        self.last_success_refresh: datetime | None = None
        self.last_attempt: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_failures = 0

    # ---------- 刷新 ----------

    async def refresh(self) -> None:
        self.last_attempt = utcnow()
        try:
            new_indices = await self.provider.get_indices()
            watchlist = self._safe_watchlist()
            new_quotes = await self.provider.get_quotes(watchlist) if watchlist else []
        except Exception as exc:  # ProviderError、网络错误等一律降级
            self.consecutive_failures += 1
            self.last_error = str(exc)
            log.warning("quote refresh failed (%s): %s", type(exc).__name__, exc)
            self._mark_all_stale()
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
        self._broadcast("quotes")

    def _safe_watchlist(self) -> list[str]:
        try:
            return self.get_watchlist()
        except Exception:
            log.exception("watchlist lookup failed; keeping previous watchlist")
            return list(self.quotes.keys())

    def _mark_all_stale(self) -> None:
        reason = "refresh_failed"
        for q in self.indices.values():
            mark_stale(q, reason)
        for q in self.quotes.values():
            mark_stale(q, reason)
        self._broadcast("stale")

    def is_stale(self) -> bool:
        if self.last_success_refresh is None:
            return True
        return utcnow() - self.last_success_refresh > timedelta(seconds=self.poll_interval * 3)

    # ---------- 读取 ----------

    def get_indices(self) -> list[Quote]:
        return list(self.indices.values())

    def get_quotes(self, symbols: list[str] | None = None) -> list[Quote]:
        if symbols:
            return [self.quotes[s] for s in symbols if s in self.quotes]
        return list(self.quotes.values())

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ---------- 订阅 ----------

    def subscribe(self, symbols: set[str] | None = None) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append((symbols, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers = [(syms, q) for syms, q in self._subscribers if q is not queue]

    def _broadcast(self, msg_type: str) -> None:
        seq = self.next_seq()
        ts = utcnow().isoformat()
        for symbols, queue in self._subscribers:
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
            await self.refresh()
            delay = self.poll_interval
            if self.consecutive_failures > 0:
                delay = min(self.poll_interval * (2 ** min(self.consecutive_failures, 4)), 60.0)
                log.info("provider degraded, next refresh in %.0fs", delay)
            await asyncio.sleep(delay)
