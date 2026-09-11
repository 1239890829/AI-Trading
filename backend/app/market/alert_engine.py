from __future__ import annotations

import asyncio
import json
import logging
from datetime import timezone

from app.core.db import utcnow
from app.notifiers import get_notifier_registry
from app.repositories.alert_repo import AlertRepository
from app.repositories.watchlist_repo import WatchlistRepository

log = logging.getLogger(__name__)


class AlertEngine:
    """后台轮询预警规则，生成 AlertEvent 并通过通知通道派发。"""

    def __init__(
        self,
        alert_repo: AlertRepository,
        watchlist_repo: WatchlistRepository,
        interval: float = 5.0,
    ):
        self._repo = alert_repo
        self._watchlist_repo = watchlist_repo
        self._interval = interval
        self._registry = get_notifier_registry()
        self._quotes: dict[str, dict] = {}
        # 盘外空转节奏（P2-9）：不得低于 interval，也不低于 5 分钟
        self._idle_interval = max(300.0, interval)

    def update_quotes(self, quotes: dict[str, dict]) -> None:
        """由 QuoteHub 或外部定时推送最新行情。"""
        self._quotes = quotes

    async def run(self) -> None:
        """常驻入口：由 SchedulerRegistry 托管（S2-2 起不再自行 create_task）。

        P2-9（并入 S2-2）：此前恒定 `interval`（默认 5s）一拍、**无时段门控**——
        盘外行情不再变化，空转纯属浪费；且用当日收盘价反复求值规则只受冷却窗口
        约束，等于用陈旧价格制造"新"触发。现在盘外**只空转不判读**，节奏同时
        降到 `_idle_interval`。
        """
        from app.market.trade_calendar import in_trading_window

        while True:
            in_window = in_trading_window()
            if in_window:
                try:
                    await self._tick()
                except Exception:
                    log.exception("alert engine tick failed")
            await asyncio.sleep(self._interval if in_window else self._idle_interval)

    async def _tick(self) -> None:
        rules = self._repo.list_rules(enabled_only=True)
        if not rules:
            return

        for rule in rules:
            symbols = self._resolve_symbols(rule)
            for symbol in symbols:
                quote = self._quotes.get(symbol)
                if not quote:
                    continue
                value = self._extract_value(rule.condition_type, quote)
                if value is None:
                    continue
                if not self._condition_met(rule.condition_type, value, rule.threshold):
                    continue
                if self._in_cooldown(rule):
                    continue
                event = self._repo.record_trigger(
                    rule_id=rule.id,
                    symbol=symbol,
                    trigger_value=value,
                    threshold=rule.threshold,
                    snapshot=quote,
                )
                delivered = await self._registry.dispatch(event, rule)
                if delivered:
                    self._repo.update_last_triggered(rule.id, event.triggered_at)
                    self._repo.update_event_channels(event.id, delivered)

    def _resolve_symbols(self, rule) -> list[str]:
        if rule.scope == "all":
            return list(self._quotes.keys())
        if rule.scope == "watchlist":
            return self._watchlist_repo.list_symbols()
        if rule.symbols:
            try:
                return json.loads(rule.symbols)
            except Exception:
                return []
        return []

    def _extract_value(self, condition_type: str, quote: dict):
        if condition_type.startswith("price_"):
            return quote.get("price")
        if condition_type.startswith("change_pct_"):
            return quote.get("change_pct")
        return None

    def _condition_met(self, condition_type: str, value: float, threshold: float) -> bool:
        if condition_type.endswith("_above"):
            return value >= threshold
        if condition_type.endswith("_below"):
            return value <= threshold
        return False

    def _in_cooldown(self, rule) -> bool:
        if not rule.last_triggered_at or rule.cooldown_seconds <= 0:
            return False
        last = rule.last_triggered_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        now = utcnow()
        delta = (now - last).total_seconds()
        return delta < rule.cooldown_seconds
