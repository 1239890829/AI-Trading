"""Optional personal Market Data reads. No brokerage, account or order endpoint."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, time, timezone
import hashlib
import json
import math
import re
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

from app.core.inflight_reads import InflightReads, LocalReadCapacityExceeded
from app.core.ttl_cache import TTLCache
from app.schemas.alpaca_data import AlpacaIndicativeQuote, AlpacaOvernightResult
from app.schemas.asset_evidence import AssetEvidence
from app.schemas.us_assets import UsInstrument

LATEST_QUOTES = "https://data.alpaca.markets/v2/stocks/quotes/latest"
NY = ZoneInfo("America/New_York")
MAX_BODY = 65_536
READ_TIMEOUT = 8
FRESHNESS_SECONDS = 60
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,23}$")
CANONICAL = re.compile(r"^([A-Z][A-Z0-9.\-]{0,23})\.(OQ|N|AM)$")
QUOTE_FIELDS = frozenset({"t", "ap", "as", "ax", "bp", "bs", "bx", "c", "z"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def overnight_window(at: datetime) -> bool:
    """A request window, not proof of an exchange session or holiday calendar."""
    local = at.astimezone(NY)
    return (local.time() >= time(20) and local.weekday() in {6, 0, 1, 2, 3}
            or local.time() < time(4) and local.weekday() in {0, 1, 2, 3, 4})


def _verified_identity(instrument) -> bool:
    if not isinstance(instrument, UsInstrument):
        return False
    match = CANONICAL.fullmatch(instrument.source_symbol)
    return bool(match and SYMBOL.fullmatch(instrument.symbol) and match[1] == instrument.symbol
        and instrument.exchange_source_code == match[2] and instrument.market == "US"
        and instrument.asset_type == "us_equity" and instrument.currency == "USD"
        and instrument.source == "tencent" and instrument.security_type in {"stock", "etf"}
        and instrument.instrument_id == f"us_equity:US:{instrument.source_symbol}:USD")


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate source field")
        result[key] = value
    return result


def _price(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid source price")
    # The official contract defines zero as an absent active bid/ask.
    return float(value) if value else None


def parse_quote(body: bytes, symbol: str, received: datetime) -> AlpacaIndicativeQuote:
    payload = json.loads(body, object_pairs_hook=_json_object)
    if (not isinstance(payload, dict) or set(payload) - {"quotes", "currency"}
            or payload.get("currency", "USD") != "USD"):
        raise ValueError("invalid source currency or envelope")
    quotes = payload.get("quotes")
    if not isinstance(quotes, dict) or set(quotes) != {symbol}:
        raise ValueError("source symbol mismatch")
    row = quotes[symbol]
    if (not isinstance(row, dict) or set(row) - QUOTE_FIELDS
            or not QUOTE_FIELDS - {"z"} <= set(row) or row.get("z") not in {None, "N"}):
        raise ValueError("unverified overnight quote contract")
    stamp = row["t"]
    if not isinstance(stamp, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})", stamp):
        raise ValueError("invalid source timestamp")
    source_time = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    if source_time > received or source_time.year < 2016 or not overnight_window(source_time):
        raise ValueError("source timestamp outside overnight contract")
    # The current API specifies shares; older quote sizes used round lots.
    # This latest-only reader must not label that historical unit as shares.
    if source_time < datetime(2025, 11, 3, tzinfo=timezone.utc):
        raise ValueError("historical quote size unit is not supported")
    bid, ask = _price(row["bp"]), _price(row["ap"])
    if bid is not None and ask is not None and bid > ask:
        raise ValueError("crossed source quote")
    for key in ("bs", "as"):
        if type(row[key]) is not int or not 0 <= row[key] <= 4_294_967_295:
            raise ValueError("invalid source quote size")
    for key in ("bx", "ax"):
        if not isinstance(row[key], str) or not re.fullmatch(r"[A-Za-z0-9 ]{1,8}", row[key]):
            raise ValueError("invalid source exchange")
    conditions = row["c"]
    if (not isinstance(conditions, list) or len(conditions) > 8
            or any(not isinstance(c, str) or not re.fullmatch(r"[A-Za-z0-9 @]{1,4}", c) for c in conditions)):
        raise ValueError("invalid source quote condition")
    return AlpacaIndicativeQuote(bid_price=bid, ask_price=ask,
        bid_size_shares=row["bs"], ask_size_shares=row["as"],
        bid_exchange=row["bx"], ask_exchange=row["ax"], conditions=conditions,
        source_time=source_time, source_timestamp=stamp, received_at=received, tape=row.get("z"))


class AlpacaDataService:
    def __init__(self, *, enabled: bool = False, api_key: str = "", secret_key: str = "",
                 http_client: httpx.AsyncClient | None = None,
                 evidence_writer: Callable[[AssetEvidence, bytes], None] | None = None):
        for value in (api_key, secret_key):
            if not isinstance(value, str) or len(value) > 256 or value and not re.fullmatch(r"[!-~]+", value):
                raise ValueError("invalid Market Data credential format")
        if http_client is not None and (http_client.auth is not None or http_client.cookies
                or http_client.params or any(name in http_client.headers for name in (
                    "authorization", "proxy-authorization", "cookie", "x-api-key",
                    "apca-api-key-id", "apca-api-secret-key"))):
            raise ValueError("injected Market Data client must not carry ambient credentials or parameters")
        self.enabled = enabled
        self._api_key = api_key
        self._secret_key = secret_key
        self._client = http_client
        self._writer = evidence_writer
        self._cache = TTLCache("alpaca-overnight-quotes", ttl=5, maxsize=128)
        self._requests = InflightReads(maxsize=4)
        self._authorization_failure: str | None = None
        self._closing = False
        self._close_task: asyncio.Task | None = None

    def _http(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=READ_TIMEOUT, trust_env=False, follow_redirects=False,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=4))
        return self._client

    @staticmethod
    def _result(instrument, state, reason, *, checked=None, **values):
        identity = ({"symbol": instrument.symbol, "instrument_id": instrument.instrument_id}
                    if _verified_identity(instrument) else {})
        return AlpacaOvernightResult(state=state, reason=reason, checked_at=checked or _now(), **identity, **values)

    def _guard(self, instrument):
        if not self.enabled:
            return self._result(instrument, "disabled", "alpaca_data_disabled")
        if self._closing:
            return self._result(instrument, "unavailable", "alpaca_data_closing")
        if not _verified_identity(instrument):
            return self._result(instrument, "unavailable", "verified_us_identity_required")
        if not self._api_key or not self._secret_key:
            return self._result(instrument, "missing_credentials", "alpaca_data_credentials_missing")
        if self._authorization_failure is not None:
            return self._result(instrument, "authorization_unverified", self._authorization_failure)
        if not overnight_window(_now()):
            return self._result(instrument, "unavailable", "outside_overnight_request_window")
        return None

    def _observe(self, instrument, quote, evidence):
        checked = _now()
        age = (checked - quote.source_time).total_seconds()
        values = dict(quote=quote, evidence=[evidence], received_at=quote.received_at,
                      authorization_verified=True, source_age_seconds=age, checked=checked,
                      missing=["source_tape"] if quote.tape is None else [])
        if age < 0:
            return self._result(instrument, "unavailable", "source_quote_clock_ahead", **values)
        if not overnight_window(checked):
            return self._result(instrument, "stale", "outside_overnight_request_window", **values)
        if age > FRESHNESS_SECONDS:
            return self._result(instrument, "stale", "source_quote_too_old", **values)
        if quote.bid_price is None or quote.ask_price is None or not quote.bid_size_shares or not quote.ask_size_shares:
            return self._result(instrument, "unavailable", "no_active_two_sided_indicative_quote", **values)
        return self._result(instrument, "available", "source_indicative_bid_ask", **values)

    async def latest(self, instrument: UsInstrument) -> AlpacaOvernightResult:
        if isinstance(instrument, UsInstrument):
            instrument = instrument.model_copy(deep=True)
        guarded = self._guard(instrument)
        if guarded is not None:
            return guarded
        key = (instrument.instrument_id,)
        hit, cached = self._cache.get(key)
        if hit:
            return self._observe(instrument, *deepcopy(cached))

        async def load():
            guarded = self._guard(instrument)
            if guarded is not None:
                return guarded
            hit, cached = self._cache.get(key)
            if hit:
                return self._observe(instrument, *deepcopy(cached))
            params = {"symbols": instrument.symbol, "feed": "overnight", "currency": "USD"}
            headers = {"APCA-API-KEY-ID": self._api_key, "APCA-API-SECRET-KEY": self._secret_key}
            try:
                async with asyncio.timeout(READ_TIMEOUT):
                    async with self._http().stream("GET", LATEST_QUOTES, params=params, headers=headers,
                                                   follow_redirects=False) as response:
                        if response.status_code in {401, 403}:
                            self._authorization_failure = f"alpaca_data_http_{response.status_code}"
                            return self._result(instrument, "authorization_unverified", self._authorization_failure)
                        if response.status_code != 200:
                            return self._result(instrument, "unavailable", f"alpaca_data_http_{response.status_code}")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_BODY:
                                return self._result(instrument, "unavailable", "alpaca_data_response_too_large")
                    raw = bytes(body)
                    # Only a small, validated quote can reach the evidence writer.
                    # Authentication and error bodies are never logged or stored.
                    if self._api_key.encode() in raw or self._secret_key.encode() in raw:
                        return self._result(instrument, "unavailable", "alpaca_data_sensitive_response_rejected")
                    received = _now()
                    quote = parse_quote(raw, instrument.symbol, received)
                    digest = hashlib.sha256(raw).hexdigest()
                    observation = json.dumps([LATEST_QUOTES, sorted(params.items()), digest, received.isoformat()])
                    evidence = AssetEvidence(endpoint=LATEST_QUOTES, params=params, received_at=received,
                        payload_sha256=digest, receipt_id=hashlib.sha256(observation.encode()).hexdigest())
                    if self._writer is not None:
                        write = asyncio.create_task(asyncio.to_thread(self._writer, evidence, raw))
                        try:
                            await asyncio.shield(write)
                        except asyncio.CancelledError:
                            await asyncio.shield(write)
                            raise
                        evidence.raw_available = True
                    cached = (quote, evidence)
                    self._cache.set(key, deepcopy(cached))
                    return self._observe(instrument, *cached)
            except TimeoutError:
                return self._result(instrument, "unavailable", "alpaca_data_timeout")
            except httpx.TimeoutException:
                return self._result(instrument, "unavailable", "alpaca_data_timeout")
            except httpx.HTTPError:
                return self._result(instrument, "authorization_unverified", "alpaca_data_transport_failed")
            except (ValueError, TypeError, KeyError, OverflowError):
                return self._result(instrument, "unavailable", "alpaca_data_contract_invalid")
            except OSError:
                return self._result(instrument, "unavailable", "alpaca_data_evidence_write_failed")

        try:
            return await self._requests.read(key, load)
        except LocalReadCapacityExceeded:
            return self._result(instrument, "unavailable", "alpaca_data_local_busy")

    async def aclose(self):
        self._closing = True
        if self._close_task is None:
            async def finish():
                try:
                    await self._requests.close()
                finally:
                    if self._client is not None:
                        await self._client.aclose()
            self._close_task = asyncio.create_task(finish())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError
