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
        self._task: asyncio.Task | None = None

    def update_quotes(self, quotes: dict[str, dict]) -> None:
        """由 QuoteHub 或外部定时推送最新行情。"""
        self._quotes = quotes

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="alert-engine")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                log.exception("alert engine tick failed")
            await asyncio.sleep(self._interval)

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
