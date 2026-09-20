from __future__ import annotations

import asyncio
import json
import logging
import time

from app.core.bjtime import BJ_TZ
from app.core.db import utcnow
from app.core.config import settings
from app.notifiers import get_notifier_registry
from app.repositories.alert_repo import AlertRepository
from app.repositories.notification_outbox import encode, rule_snapshot
from app.repositories.watchlist_repo import WatchlistRepository
from app.schemas.market import Quote

log = logging.getLogger(__name__)

_BUY_POINT_SEND_SPACING_SECONDS = 0.5


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
        self._fresh_within = max(settings.poll_interval_seconds, settings.stale_after_seconds)
        self._registry = get_notifier_registry()
        self._quotes: dict[str, dict] = {}
        self._last_buy_point_send_monotonic = 0.0
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
            try:
                if in_window:
                    await self._tick()
                else:
                    self._repo.outbox.reconcile(self._now_ms())
            except Exception:
                log.exception("alert engine tick failed")
            await asyncio.sleep(self._interval if in_window else self._idle_interval)

    async def _tick(self) -> None:
        await self._deliver_pending()
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
                notifier = self._registry.get("feishu")
                target = notifier.delivery_target() if notifier is not None else ""
                now_ms = self._now_ms()
                event = self._repo.record_trigger(
                    rule_id=rule.id,
                    symbol=symbol,
                    trigger_value=value,
                    threshold=rule.threshold,
                    snapshot=quote,
                    outbox_target=target, now_ms=now_ms,
                    expires_at_ms=now_ms + int(self._fresh_within * 1000),
                )
                delivered = await self._registry.dispatch(event, rule, exclude=("feishu",))
                if delivered:
                    self._repo.update_last_triggered(rule.id, event.triggered_at)
                    self._repo.update_event_channels(event.id, delivered)
        await self._deliver_pending()

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _delivery_block(self, row, event, rule, notifier) -> str | None:
        from app.market.trade_calendar import in_trading_window

        if event is None or rule is None or not rule.enabled:
            return "event_or_rule_removed_or_disabled"
        try:
            payload = json.loads(row.payload)
        except (TypeError, ValueError):
            return "intent_payload_invalid"
        if encode(rule_snapshot(rule)) != encode(payload.get("rule")):
            return "rule_or_channels_changed"
        if not row.target or notifier is None or notifier.delivery_target() != row.target:
            return "channel_unconfigured_or_target_changed"
        if not in_trading_window():
            return "outside_trading_window"

        intent = payload.get("intent") or {}
        if intent.get("kind") == "picks_buy_point":
            return self._buy_point_delivery_block(event, intent)

        if event.symbol not in self._resolve_symbols(rule):
            return "symbol_no_longer_in_scope"
        quote = self._quotes.get(event.symbol)
        if not quote or not (quote.get("data_timestamp") or quote.get("received_at")):
            return "quote_time_unknown"
        try:
            q = Quote.model_validate(quote)
            ts = q.data_timestamp or q.received_at
            if ts.tzinfo is None or int(ts.timestamp() * 1000) > self._now_ms():
                return "quote_time_untrusted"
            if q.freshness(fresh_within=self._fresh_within).state != "ready":
                return "quote_not_fresh"
        except (ValueError, TypeError):
            return "quote_invalid"
        value = self._extract_value(rule.condition_type, quote)
        if value is None or not self._condition_met(rule.condition_type, value, rule.threshold):
            return "condition_no_longer_met"
        return None

    def _buy_point_delivery_block(self, event, intent: dict) -> str | None:
        """Revalidate a queued buy-point against the archived decision fact, not price-rule syntax."""
        try:
            snap = json.loads(event.snapshot or "{}")
        except (TypeError, ValueError):
            return "buy_point_snapshot_invalid"
        card = snap.get("card")
        ref = snap.get("execution_ref") or {}
        if not isinstance(card, dict) or not isinstance(ref, dict):
            return "buy_point_payload_incomplete"

        trade_date = str(intent.get("trade_date") or "")
        decision_id = str(intent.get("decision_id") or "")
        decision_version = str(intent.get("decision_version") or "")
        if not trade_date or not decision_id or not decision_version:
            return "buy_point_intent_identity_missing"
        if ref.get("decision_id") != decision_id or ref.get("decision_version") != decision_version:
            return "buy_point_event_identity_mismatch"
        if ref.get("execution_snapshot_state") != "ready":
            return "buy_point_event_execution_not_ready"

        from app.picks.opportunity_learning import latest_notification_execution

        latest = latest_notification_execution(trade_date, self._repo._session_factory).get(event.symbol)
        if not latest:
            return "buy_point_decision_missing"
        if latest.get("decision_id") != decision_id or latest.get("decision_version") != decision_version:
            return "buy_point_decision_superseded"
        if latest.get("gate_decision") != "passed" or latest.get("archived_decision") != "eligible":
            return "buy_point_decision_no_longer_eligible"
        executable = latest.get("executable_snapshot") or {}
        if executable.get("state") != "ready":
            return "buy_point_execution_not_ready"
        return None

    async def _deliver_pending(self) -> None:
        outbox = self._repo.outbox
        outbox.reconcile(self._now_ms())
        for outbox_id in outbox.pending_ids():
            row = outbox.claim(outbox_id, self._now_ms())
            if row is None:
                continue
            event = self._repo.get_event(row.event_id)
            rule = self._repo.get_rule(event.rule_id) if event is not None else None
            notifier = self._registry.get(row.channel)
            reason = self._delivery_block(row, event, rule, notifier)
            if reason:
                outbox.finish(row, "suppressed", reason, self._now_ms())
                continue
            try:
                intent_kind = (json.loads(row.payload).get("intent") or {}).get("kind")
            except (TypeError, ValueError):
                intent_kind = None
            if intent_kind == "picks_buy_point":
                elapsed = time.monotonic() - self._last_buy_point_send_monotonic
                if elapsed < _BUY_POINT_SEND_SPACING_SECONDS:
                    await asyncio.sleep(_BUY_POINT_SEND_SPACING_SECONDS - elapsed)
            # The pacing wait may cross expiry; begin_send is the authoritative
            # last-moment boundary and must re-check lease/expiry after waiting.
            if not outbox.begin_send(row, self._now_ms()):
                outbox.finish(row, "expired", "send_window_closed", self._now_ms())
                continue
            try:
                result = await asyncio.wait_for(notifier.send_result(event, rule), timeout=25.0)
            except Exception:
                result = None
            if intent_kind == "picks_buy_point":
                self._last_buy_point_send_monotonic = time.monotonic()
            # Cancellation/process loss leaves a started lease for conservative
            # reconciliation. Only an explicit platform rejection may become a
            # permanent failure; timeout/network/malformed receipts stay unknown.
            if result is None or result.outcome == "unknown":
                state = "unknown"
                reason = result.reason if result is not None else "acceptance_unconfirmed"
            elif result.outcome == "explicit_rejected":
                state = "permanent_failed"
                reason = result.reason
            else:
                state = "accepted"
                reason = result.reason
            outbox.finish(row, state, reason, self._now_ms())

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
            last = last.replace(tzinfo=BJ_TZ)
        now = utcnow()
        delta = (now - last).total_seconds()
        return delta < rule.cooldown_seconds
