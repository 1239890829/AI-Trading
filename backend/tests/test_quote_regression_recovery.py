"""Only a confirmed source-clock regression may spend the remaining read budget."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from app.core import freshness
from app.core.bjtime import BJ_TZ
from app.data_providers import composite as cp
from app.data_quality import validator
from app.market.indices import INDEX_MARKETS
from app.schemas.market import Quality, Quote
from app.services import quote_hub as qh

NOW = datetime.fromisoformat("2026-10-14T09:38:28.460+08:00").astimezone(timezone.utc)


def quote(symbol="600105", *, source="primary", age=1, price=10.0, market="SH", **updates):
    row = Quote(symbol=symbol, market=market, price=price, prev_close=10.0,
                data_timestamp=NOW - timedelta(seconds=age), source=source, received_at=NOW)
    return row.model_copy(update=updates)


class Source:
    realtime = True

    def __init__(self, name, rows):
        self.name, self.rows = name, rows
        self.calls = Counter()
        self.requested = []

    async def read(self, method, symbols=None):
        self.calls[method] += 1
        self.requested.append(symbols)
        if callable(self.rows):
            return await self.rows(method, symbols)
        return [q.model_copy(deep=True) for q in self.rows]

    async def get_quotes(self, symbols):
        return await self.read("get_quotes", symbols)

    async def get_indices(self):
        return await self.read("get_indices")


@pytest.fixture(autouse=True)
def clock_and_calendar(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(freshness, "datetime", Clock)
    monkeypatch.setattr(qh, "utcnow", lambda: NOW)
    monkeypatch.setattr(qh, "beijing_now", lambda: NOW.astimezone(BJ_TZ))
    monkeypatch.setattr(validator, "utcnow", lambda: NOW)
    monkeypatch.setattr(qh.tc, "in_trading_window", lambda: True)
    monkeypatch.setattr(qh.tc, "market_open_state", lambda *a, **k: qh.tc.MARKET_OPEN)


def hub_for(primary_rows, backup_rows, *, indices=False, current_age=2):
    primary = Source("primary", primary_rows)
    backup = Source("backup", backup_rows)
    hub = qh.QuoteHub(cp.CompositeProvider([primary, backup]), 1,
                      get_watchlist=lambda: ["600105", "600002"])
    if indices:
        hub.indices = {s: quote(s, market=m, age=current_age) for s, m in INDEX_MARKETS.items()}
    else:
        hub.quotes = {s: quote(s, age=current_age) for s in ["600105", "600002"]}
    return hub, primary, backup


def assert_retained_source_unchanged(current, before, *, expired=False):
    expected = dict(before)
    if expired:
        expected.update(quality=Quality.stale, quality_reasons=["source_time_regress_ignored"])
    assert current.model_dump() == expected


def test_regressed_stock_uses_fresh_backup_without_touching_normal_primary_row():
    async def check():
        hub, primary, backup = hub_for(
            [quote(age=3, price=10.3), quote("600002", age=1, price=10.1)],
            [quote(source="backup", age=0.5, price=10.2), quote("600002", source="backup", price=99)],
        )
        await hub._refresh_domain("quotes")
        assert hub.quotes["600105"].price == 10.2
        assert hub.quotes["600105"].source == "backup"
        assert hub.quotes["600105"].quality == Quality.high
        assert hub.quotes["600105"].freshness(fresh_within=10).age_seconds == 0.5
        assert hub.quotes["600002"].price == 10.1 and hub.quotes["600002"].source == "primary"
        assert backup.requested == [["600105"]]
        assert primary.calls == {"get_quotes": 1} and backup.calls == {"get_quotes": 1}
        assert not hub.last_quote_rejections and hub.last_batch_coverage == 1
        assert len(hub.quote_history) == 2, "accept/history advance only once per row"
        metrics = hub.provider.provider_health()["method_attempts"]
        assert metrics["get_quotes@primary"]["source_observation_rejected"] == 1
        assert metrics["get_quotes@backup"]["method_started"] == 1
    asyncio.run(check())


@pytest.mark.parametrize("change", [
    {"age": 3}, {"age": 2}, {"data_timestamp": None}, {"market": "SZ"},
    {"symbol": "600003"}, {"price": None}, {"price": -1},
    {"quality": Quality.stale}, {"quality": Quality.invalid}, {"source": "source_unknown"},
    {"age": -1},
])
@pytest.mark.parametrize("current_age", [2, 20])
def test_untrusted_backup_cannot_clear_original_regression(change, current_age):
    async def check():
        kwargs = {"source": "backup", "age": 1, **change}
        if change.get("age") in {2, 3}:
            kwargs["age"] = current_age + change["age"] - 2
        hub, _, backup = hub_for([quote(age=current_age + 1), quote("600002")],
                                 [quote(**kwargs)], current_age=current_age)
        old = hub.quotes["600105"].model_dump()
        await hub._refresh_domain("quotes")
        current = hub.quotes["600105"]
        assert_retained_source_unchanged(current, old, expired=current_age == 20)
        assert hub.last_quote_rejections == {"600105": "source_time_regress_ignored"}
        assert hub.last_batch_coverage == 0.5 and hub.last_missing_symbols == ["600105"]
        assert backup.calls == {"get_quotes": 1}
        expected_failures = 0 if current_age == 2 and change.get("age") in {2, 3} else 1
        assert hub.provider._failures.get(("get_quotes", "backup"), 0) == expected_failures
        assert len(hub.quote_history) == 1 and hub.quote_history[0][0] == "600002"
    asyncio.run(check())


@pytest.mark.parametrize(("previous_age", "backup_age"), [(20, 15), (172800, 86400)])
@pytest.mark.parametrize("indices", [False, True])
def test_newer_but_expired_or_previous_day_backup_is_not_recovery(previous_age, backup_age, indices):
    async def check():
        if indices:
            primary_rows = [quote(s, market=m, age=previous_age + 1 if s == "000001" else 1)
                            for s, m in INDEX_MARKETS.items()]
            backup_rows = [quote("000001", source="backup", age=backup_age)]
        else:
            primary_rows = [quote(age=previous_age + 1), quote("600002")]
            backup_rows = [quote(source="backup", age=backup_age)]
        hub, _, backup = hub_for(primary_rows, backup_rows, indices=indices, current_age=previous_age)
        domain, symbol = ("indices", "000001") if indices else ("quotes", "600105")
        current = hub.indices if indices else hub.quotes
        before = current[symbol].model_dump()
        await hub._refresh_domain(domain)
        assert_retained_source_unchanged(current[symbol], before, expired=True)
        assert (hub.last_index_rejections if indices else hub.last_quote_rejections) == {symbol: "source_time_regress_ignored"}
        assert backup.calls == {"get_indices" if indices else "get_quotes": 1}
        assert hub.provider._failures["get_indices" if indices else "get_quotes", "backup"] == 1
    asyncio.run(check())


def test_normal_monotonic_or_nonregression_gap_never_calls_backup():
    async def check():
        for rows in ([quote(), quote("600002")], [quote(data_timestamp=None), quote("600002")],
                     [quote(price=None), quote("600002")], [quote("600002")]):
            hub, _, backup = hub_for(rows, [quote(source="backup")])
            await hub._refresh_domain("quotes")
            assert not backup.calls
    asyncio.run(check())


def test_index_recovery_only_replaces_regressed_identity():
    async def check():
        primary_rows = [quote(s, market=m, age=3 if s == "000001" else 1) for s, m in INDEX_MARKETS.items()]
        backup_rows = [quote(s, market=m, source="backup", age=0.5, price=10.2) for s, m in INDEX_MARKETS.items()]
        hub, _, backup = hub_for(primary_rows, backup_rows, indices=True)
        await hub._refresh_domain("indices")
        assert hub.indices["000001"].source == "backup"
        assert all(q.source == "primary" for s, q in hub.indices.items() if s != "000001")
        assert not hub.last_index_rejections and hub.last_index_coverage == 1
        assert backup.calls == {"get_indices": 1}
    asyncio.run(check())


def test_remaining_budget_is_not_restarted_and_exhaustion_never_starts_backup(monkeypatch):
    async def check():
        for consumed in (0.6, 1.1):
            clock = [100.0]
            fake_time = NS(monotonic=lambda: clock[0])
            monkeypatch.setattr(cp, "time", fake_time)
            monkeypatch.setattr(qh, "time", fake_time, raising=False)
            monkeypatch.setattr(cp, "REALTIME_BUDGET_SECONDS", 1)
            async def primary_rows(method, symbols):
                clock[0] += consumed
                return [quote(age=3, price=10.3), quote("600002", price=10.1)]
            hub, _, backup = hub_for(primary_rows, [quote(source="backup")])
            before = {symbol: row.model_dump() for symbol, row in hub.quotes.items()}
            attempts = []
            original = hub.provider._attempt
            async def tracked(method, source, args, deadline=None):
                attempts.append((source.name, deadline))
                return await original(method, source, args, deadline)
            monkeypatch.setattr(hub.provider, "_attempt", tracked)
            await hub._refresh_domain("quotes")
            assert attempts[0] == ("primary", 101.0)
            if consumed < 1:
                assert attempts == [("primary", 101.0), ("backup", 101.0)]
                assert hub.quotes["600105"].source == "backup"
            else:
                assert attempts == [("primary", 101.0)]
                assert not backup.calls
                # The primary itself returned beyond the original deadline;
                # none of its rows reached Hub admission or regression recovery.
                for symbol, current in hub.quotes.items():
                    expected = {**before[symbol], "quality": Quality.stale, "quality_reasons": ["refresh_failed"]}
                    assert current.model_dump() == expected
                assert not hub.last_quote_rejections and not hub.quote_history
                assert hub._domains["quotes"].consecutive_failures == 1
                assert hub._domains["quotes"].last_error_type == "ProviderError"
                counts = hub.provider.provider_health()["method_attempts"]["get_quotes@primary"]
                assert counts["method_budget_exhausted"] == 1 and counts["method_returned"] == 0
    asyncio.run(check())


@pytest.mark.parametrize("current_age", [2, 20])
def test_hung_backup_is_cancelled_without_rewriting_retained_source_evidence(monkeypatch, current_age):
    async def check():
        monkeypatch.setattr(cp, "REALTIME_BUDGET_SECONDS", 0.03)
        cancelled = []
        async def hung(method, symbols):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        hub, _, _ = hub_for([quote(age=current_age + 1), quote("600002")], hung, current_age=current_age)
        before = hub.quotes["600105"].model_dump()
        await asyncio.wait_for(hub._refresh_domain("quotes"), timeout=1)
        assert cancelled == [True]
        assert_retained_source_unchanged(hub.quotes["600105"], before, expired=current_age == 20)
        assert hub.last_quote_rejections == {"600105": "source_time_regress_ignored"}
        assert hub.last_batch_coverage == 0.5
        assert hub.provider.provider_health()["method_attempts"]["get_quotes@backup"]["method_budget_exhausted"] == 1
        assert hub.provider._failures["get_quotes", "backup"] == 1
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(check())


def test_existing_low_quality_admission_remains_visible_as_low():
    async def check():
        # A known, fresh clock with a pct mismatch is low under the existing
        # validator, but passes the current-value admission gate.
        hub, _, _ = hub_for([quote(age=3), quote("600002")],
                            [quote(source="backup", price=10.2, change_pct=8)])
        await hub._refresh_domain("quotes")
        recovered = hub.quotes["600105"]
        assert recovered.source == "backup" and recovered.quality == Quality.low
        assert recovered.quality_reasons == ["change_pct_mismatch"]
        assert recovered.freshness(fresh_within=10).state == "degraded"
        assert not hub.last_quote_rejections
    asyncio.run(check())


@pytest.mark.parametrize("bad_rows", [None, [], "invalid", "error", "duplicate", "mixed-market"])
def test_bad_backup_falls_through_existing_order_to_next_trusted_source(bad_rows):
    async def check():
        async def bad(method, symbols):
            if bad_rows == "error":
                raise RuntimeError("offline backup failure")
            if isinstance(bad_rows, str) and bad_rows in {"duplicate", "mixed-market"}:
                return [quote(source="backup"), quote(source="backup", market="SZ" if bad_rows == "mixed-market" else "SH")]
            return bad_rows
        hub, _, backup = hub_for([quote(age=3), quote("600002")], bad)
        next_source = Source("next", [quote(source="next", age=0.5, price=10.2)])
        hub.provider = cp.CompositeProvider([hub.provider.providers[0], backup, next_source])
        await hub._refresh_domain("quotes")
        assert hub.quotes["600105"].source == "next"
        assert backup.requested == next_source.requested == [["600105"]]
        assert hub.provider._failures["get_quotes", "backup"] == 1
        assert not hub.last_quote_rejections
    asyncio.run(check())


@pytest.mark.parametrize("current_age", [2, 20])
def test_cancelling_recovery_preserves_normal_primary_progress_and_rejection(current_age):
    async def check():
        entered, cancelled = asyncio.Event(), []
        async def hung(method, symbols):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        hub, _, _ = hub_for([quote(age=current_age + 1), quote("600002", price=10.2)], hung,
                            current_age=current_age)
        before = hub.quotes["600105"].model_dump()
        # Regressions require a previous accepted observation in production.
        # Keep that completed round distinct from this still-in-flight recovery.
        previous_completed = NOW - timedelta(seconds=1)
        hub._domains["quotes"].last_success = previous_completed
        hub._domains["quotes"].succeeded = True
        hub._domains["quotes"].generation = 1
        normal_queue = hub.subscribe({"600002"})
        task = asyncio.create_task(hub._refresh_domain("quotes"))
        await asyncio.wait_for(entered.wait(), 1)
        assert hub.quotes["600002"].price == 10.2
        assert_retained_source_unchanged(hub.quotes["600105"], before, expired=current_age == 20)
        assert hub.last_quote_rejections == {"600105": "source_time_regress_ignored"}
        assert hub.last_batch_coverage == 0.5
        assert len(hub.quote_history) == 1
        assert "quotes" in hub._pending_domains
        assert hub._domains["quotes"].last_success == previous_completed
        assert hub._domains["quotes"].last_error is None and hub._domains["quotes"].succeeded
        hub._broadcast("quotes", changed_domains={"quotes"})
        normal_frame = normal_queue.get_nowait()
        assert normal_frame["type"] == "quotes"
        assert normal_frame["data"][0]["price"] == 10.2
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert cancelled == [True]
        assert_retained_source_unchanged(hub.quotes["600105"], before, expired=current_age == 20)
        assert hub.quotes["600002"].price == 10.2
        assert len(hub.quote_history) == 1
        assert hub.provider.provider_health()["method_attempts"]["get_quotes@backup"]["method_cancelled"] == 1
        assert ("get_quotes", "backup") not in hub.provider._failures
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(check())


def test_recovery_completion_clock_never_rewrites_actual_row_source_clock(monkeypatch):
    async def check():
        clock = [NOW]
        monkeypatch.setattr(qh, "utcnow", lambda: clock[0])
        async def failed(method, symbols):
            clock[0] += timedelta(seconds=3)
            return []
        hub, _, _ = hub_for([quote(age=3), quote("600002")], failed)
        await hub._refresh_domain("quotes")
        assert hub._domains["quotes"].last_success == NOW + timedelta(seconds=3)
        assert hub._domains["quotes"].succeeded and hub._domains["quotes"].generation == 1
        assert hub.quotes["600002"].data_timestamp == NOW - timedelta(seconds=1)
        assert hub.quotes["600002"].received_at == NOW
        assert hub.quotes["600105"].data_timestamp == NOW - timedelta(seconds=2)
    asyncio.run(check())


def test_normal_source_rows_are_only_validated_by_original_admission_gate(monkeypatch):
    async def check():
        counts = Counter()
        original = qh.validate_quote
        def tracked(q, prev):
            counts[q.symbol] += 1
            return original(q, prev)
        monkeypatch.setattr(qh, "validate_quote", tracked)
        hub, _, backup = hub_for([quote(), quote("600002")], [quote(source="backup")])
        await hub._refresh_domain("quotes")
        assert counts == {"600105": 1, "600002": 1}
        assert not backup.calls
    asyncio.run(check())


def test_late_recovery_rechecks_latest_current_and_does_not_replay_normal_history(monkeypatch):
    async def check():
        hub, _, _ = hub_for([quote(age=3), quote("600002", price=10.2)], [quote(source="backup")])
        async def recover(method, rejected, *, deadline, validate, idle_limited=False):
            # A more recent trusted observation arrives while the recovery is in flight.
            hub.quotes["600105"] = quote(source="latest", age=0.1, price=10.3)
            return {("600105", "SH"): quote(source="backup", age=0.5, price=10.1)}
        monkeypatch.setattr(hub.provider, "recover_quote_regressions", recover)
        await hub._refresh_domain("quotes")
        assert hub.quotes["600105"].price == 10.3 and hub.quotes["600105"].quality == Quality.high
        assert hub.quotes["600002"].price == 10.2
        assert len(hub.quote_history) == 1
    asyncio.run(check())


def test_recovery_deadline_uses_only_idle_slot_with_original_total_budget_as_upper_bound(monkeypatch):
    async def check():
        for budget, poll, consumed in [(4, 0.5, 0.1), (4, 0.5, 0.6), (0.2, 1, 0.1)]:
            clock = [100.0]
            fake_time = NS(monotonic=lambda: clock[0])
            monkeypatch.setattr(cp, "time", fake_time)
            monkeypatch.setattr(qh, "time", fake_time)
            monkeypatch.setattr(cp, "REALTIME_BUDGET_SECONDS", budget)
            async def primary_rows(method, symbols):
                clock[0] += consumed
                return [quote(age=3), quote("600002")]
            hub, _, backup = hub_for(primary_rows, [quote(source="backup")])
            hub.poll_interval = poll
            attempts = []
            original = hub.provider._attempt
            async def tracked(method, source, args, deadline=None):
                attempts.append((source.name, deadline))
                return await original(method, source, args, deadline)
            monkeypatch.setattr(hub.provider, "_attempt", tracked)
            await hub._refresh_domain("quotes")
            assert attempts[0] == ("primary", 100 + budget)
            if consumed < min(budget, poll):
                assert attempts[1] == ("backup", 100 + min(budget, poll))
                assert hub.quotes["600105"].source == "backup"
            else:
                assert not backup.calls
    asyncio.run(check())


def test_hung_recovery_does_not_delay_next_primary_round():
    async def check():
        next_primary, cancelled = asyncio.Event(), []
        primary_count = 0
        async def primary_rows(method, symbols):
            nonlocal primary_count
            primary_count += 1
            if primary_count > 1:
                next_primary.set()
                return [quote(age=0.1), quote("600002", age=0.1, price=10.3)]
            return [quote(age=3), quote("600002", price=10.2)]
        async def hung(method, symbols):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        hub, _, backup = hub_for(primary_rows, hung)
        hub.poll_interval = 0.5
        poller = asyncio.create_task(hub._run_domain("quotes"))
        try:
            # Existing 4s recovery cannot meet this bound; a 0.5s original
            # round has ample scheduling margin. No shared loop clock is frozen.
            await asyncio.wait_for(next_primary.wait(), timeout=2)
            await asyncio.wait_for(asyncio.shield(hub._domain_tasks["quotes"]), timeout=1)
            assert cancelled == [True] and backup.calls == {"get_quotes": 1}
            assert hub.quotes["600002"].price == 10.3
        finally:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
            await asyncio.gather(*hub._domain_tasks.values(), return_exceptions=True)
    asyncio.run(check())


def test_three_short_idle_timeouts_cannot_trip_backup_breaker(monkeypatch):
    async def check():
        clock = [100.0]
        fake_time = NS(monotonic=lambda: clock[0])
        monkeypatch.setattr(cp, "time", fake_time)
        monkeypatch.setattr(qh, "time", fake_time)
        monkeypatch.setattr(cp, "REALTIME_BUDGET_SECONDS", 0.51)
        async def primary_rows(method, symbols):
            clock[0] += 0.49
            return [quote(age=3), quote("600002")]
        async def hung(method, symbols):
            await asyncio.Event().wait()
        hub, primary, backup = hub_for(primary_rows, hung)
        hub.poll_interval = 0.5
        for _ in range(3):
            await hub._refresh_domain("quotes")
        metrics = hub.provider.provider_health()["method_attempts"]["get_quotes@backup"]
        assert metrics["method_started"] == metrics["method_budget_exhausted"] == 3
        assert not hub.provider._in_cooldown("get_quotes", "backup")
        assert ("get_quotes", "backup") not in hub.provider._failures
        async def unavailable(method, symbols):
            raise RuntimeError("real primary transport failure")
        primary.rows = unavailable
        backup.rows = [quote(source="backup")]
        assert (await hub.provider.get_quotes(["600105"]))[0].source == "backup"
        assert backup.calls["get_quotes"] == 4
    asyncio.run(check())


@pytest.mark.parametrize("indices", [False, True])
@pytest.mark.parametrize("backup_age", [2, 2.5])
def test_nonadvancing_trusted_recovery_does_not_disable_normal_fallback(indices, backup_age):
    async def check():
        method = "get_indices" if indices else "get_quotes"
        if indices:
            primary_rows = [quote(s, market=m, age=3 if s == "000001" else 1)
                            for s, m in INDEX_MARKETS.items()]
            backup_rows = [quote(s, market=m, source="backup", age=backup_age)
                           for s, m in INDEX_MARKETS.items()]
        else:
            primary_rows = [quote(age=3), quote("600002")]
            backup_rows = [quote(source="backup", age=backup_age)]
        hub, primary, backup = hub_for(primary_rows, backup_rows, indices=indices)
        domain = "indices" if indices else "quotes"
        symbol = "000001" if indices else "600105"
        current = hub.indices if indices else hub.quotes
        original = current[symbol].model_dump()
        for _ in range(cp.FAILURE_THRESHOLD):
            await hub._refresh_domain(domain)
            assert_retained_source_unchanged(current[symbol], original)
            assert (hub.last_index_rejections if indices else hub.last_quote_rejections) == {symbol: "source_time_regress_ignored"}

        async def unavailable(method, symbols):
            raise RuntimeError("real primary transport failure")
        primary.rows = unavailable
        backup.rows = ([quote(s, market=m, source="backup", age=0.5)
                        for s, m in INDEX_MARKETS.items()] if indices
                       else [quote(source="backup", age=0.5)])
        rows = await (hub.provider.get_indices() if indices
                      else hub.provider.get_quotes(["600105"]))
        assert rows and all(row.source == "backup" for row in rows)
        assert backup.calls[method] == cp.FAILURE_THRESHOLD + 1
        metrics = hub.provider.provider_health()["method_attempts"][f"{method}@backup"]
        assert metrics["source_observation_rejected"] == cp.FAILURE_THRESHOLD
    asyncio.run(check())


def test_nonadvancing_recovery_preserves_real_failure_history_without_incrementing_it():
    async def check():
        hub, _, backup = hub_for([quote(age=3), quote("600002")],
                                 [quote(source="backup", age=2)])
        hub.provider._record_failure("get_quotes", "backup")
        hub.provider._record_failure("get_quotes", "backup")
        await hub._refresh_domain("quotes")
        assert backup.calls == {"get_quotes": 1}
        assert hub.provider._failures["get_quotes", "backup"] == 2
        assert not hub.provider._in_cooldown("get_quotes", "backup")
        assert "get_quotes" not in hub.provider._last_good or hub.provider._last_good["get_quotes"] != "backup"
    asyncio.run(check())


@pytest.mark.parametrize("change", [
    {"price": None}, {"price": -1}, {"quality": Quality.stale},
    {"quality": Quality.invalid}, {"data_timestamp": None}, {"volume": -1},
])
@pytest.mark.parametrize("current_age", [2, 20])
def test_nonadvancing_but_untrusted_recovery_still_counts_as_source_failure(change, current_age):
    async def check():
        hub, _, _ = hub_for([quote(age=current_age + 1), quote("600002")],
                            [quote(source="backup", age=current_age, **change)], current_age=current_age)
        before = hub.quotes["600105"].model_dump()
        await hub._refresh_domain("quotes")
        assert hub.provider._failures["get_quotes", "backup"] == 1
        assert_retained_source_unchanged(hub.quotes["600105"], before, expired=current_age == 20)
        assert hub.last_quote_rejections == {"600105": "source_time_regress_ignored"}
    asyncio.run(check())


@pytest.mark.parametrize("current_age", [2, 20])
def test_nonadvancing_row_does_not_hide_another_invalid_recovery_target(current_age):
    async def check():
        hub, _, _ = hub_for([quote(age=current_age + 1), quote("600002", age=current_age + 1)],
                            [quote(source="backup", age=current_age),
                             quote("600002", source="backup", age=current_age, price=None)], current_age=current_age)
        before = {symbol: row.model_dump() for symbol, row in hub.quotes.items()}
        await hub._refresh_domain("quotes")
        assert hub.provider._failures["get_quotes", "backup"] == 1
        for symbol, current in hub.quotes.items():
            assert_retained_source_unchanged(current, before[symbol], expired=current_age == 20)
        assert hub.last_quote_rejections == {symbol: "source_time_regress_ignored" for symbol in before}
        assert hub.last_batch_coverage == 0 and not hub.quote_history
    asyncio.run(check())


@pytest.mark.parametrize("method", ["get_kline", "get_news", "unknown_method"])
def test_recovery_orchestration_cannot_expand_provider_capabilities(method):
    async def check():
        source = Source("primary", [])
        provider = cp.CompositeProvider([source])
        with pytest.raises(ValueError, match="only supports quote batches"):
            await provider.recover_quote_regressions(
                method, {}, deadline=0, validate=lambda row, key: row,
            )
        assert not source.calls
    asyncio.run(check())
