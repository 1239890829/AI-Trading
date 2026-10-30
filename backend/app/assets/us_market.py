"""Bounded public Tencent US reads. No A-share Hub, model, order or account access."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from zoneinfo import ZoneInfo

import httpx

from app.core.errors import AppError
from app.core.inflight_reads import InflightReads, LocalReadCapacityExceeded
from app.core.ttl_cache import TTLCache
from app.schemas.asset_evidence import AssetEvidence
from app.schemas.us_assets import (
    UsDailyPoint, UsDailySection, UsInstrument, UsRegularQuote,
    UsSearchCandidate, UsSearchResult, UsSnapshot,
)

QUOTE_URL = "https://qt.gtimg.cn/"
SEARCH_URL = "https://smartbox.gtimg.cn/s3/"
DAILY_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
SOURCE_URLS = frozenset({QUOTE_URL, SEARCH_URL, DAILY_URL})
NY = ZoneInfo("America/New_York")
CANONICAL = re.compile(r"^([A-Z][A-Z0-9.\-]{0,23})\.([A-Z]{1,4})$")
# Tencent's own US page maps these venues. A USD field alone does not
# establish a US listing, and unknown venue suffixes are not inferred.
US_VENUES = frozenset({"OQ", "N", "AM"})
US_SECURITY_TYPES = {"GP": "stock", "GP-ETF": "etf"}
TICKER = re.compile(r"^[A-Z][A-Z0-9\-]{0,15}$")
MAX_BODY = 1_048_576


def _utcnow():
    return datetime.now(timezone.utc)


class UsSourceError(AppError):
    status_code = 502
    code = "us_source_unavailable"


def _canonical_match(value: str):
    matched = CANONICAL.fullmatch(value)
    return matched if matched and matched[2] in US_VENUES else None


def _identity(code: str) -> tuple[str, str | None]:
    if not isinstance(code, str):
        raise AppError("请选择来源中的美股代码", code="invalid_us_identity", status_code=422)
    value = code.strip().upper()
    matched = _canonical_match(value)
    if matched:
        return matched[1], value
    if TICKER.fullmatch(value):
        return value, None
    raise AppError("请选择来源中的完整美股代码", code="invalid_us_identity", status_code=422)


def _number(value, *, positive=False, nonnegative=False, required=False):
    if value is None or value == "":
        if required:
            raise UsSourceError("来源缺少必要报价", code="us_quote_value_missing")
        return None
    if isinstance(value, bool):
        raise UsSourceError("来源数值无效", code="us_source_value_invalid")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise UsSourceError("来源数值无效", code="us_source_value_invalid") from exc
    if not math.isfinite(result) or positive and result <= 0 or nonnegative and result < 0:
        raise UsSourceError("来源数值无效", code="us_source_value_invalid")
    return result


def clock_phase(now: datetime) -> str:
    current = now.astimezone(NY)
    if current.weekday() == 5 or current.weekday() == 6 and current.time() < time(20):
        return "weekend"
    wall = current.time()
    if time(4) <= wall < time(9, 30):
        return "pre_market"
    if time(9, 30) <= wall < time(16):
        return "regular"
    if time(16) <= wall < time(20):
        return "post_market"
    return "overnight"


def observe_quote(quote: UsRegularQuote, at: datetime) -> UsRegularQuote:
    """Recheck observation age without changing the original source/receive clocks."""
    if at.tzinfo is None or quote.source_time.tzinfo is None:
        raise UsSourceError("美股核对时钟缺少时区", code="us_source_clock_invalid")
    source = quote.source_time.astimezone(NY)
    # Only this source's regular-session facts have been verified. Preserve
    # the existing 60-second and greater-than-seven-source-date-day rules.
    regular = time(9, 30) <= source.time() <= time(16, 0, 59)
    closing = regular and source.time() >= time(16)
    observation = ("regular_close" if closing else "regular_quote") if regular else "session_unverified"
    if (at.astimezone(NY).date() - source.date()).days > 7:
        observation = "stale"
    elif regular and not closing and (at - quote.source_time).total_seconds() > 60:
        observation = "stale"
    return quote.model_copy(update={"observation": observation}, deep=True)


def parse_quote(body: bytes, requested: str, evidence: AssetEvidence) -> tuple[UsInstrument, UsRegularQuote]:
    ticker, canonical = _identity(requested)
    header = "us" + ticker.replace(".", "__")
    try:
        text = body.decode("gb18030")
    except UnicodeError as exc:
        raise UsSourceError("美股来源编码无效", code="us_source_encoding_invalid") from exc
    assignment = re.fullmatch(r'\s*v_([A-Za-z0-9_\-]+)="([^"\r\n]*)";?\s*', text)
    if not assignment or assignment[1] != header:
        raise UsSourceError("美股来源证券身份不一致", code="us_identity_mismatch")
    values = assignment[2].split("~")
    if not 57 <= len(values) <= 120 or values[0] != "200" or values[56] not in US_SECURITY_TYPES:
        raise UsSourceError("美股报价合同无效", code="us_quote_shape_invalid")
    source_symbol = values[2].upper()
    identity = _canonical_match(source_symbol)
    if not identity or identity[1] != ticker or canonical is not None and source_symbol != canonical:
        raise UsSourceError("美股来源证券身份不一致", code="us_identity_mismatch")
    if values[35] != "USD":
        raise UsSourceError("美股来源未明确美元币种", code="us_currency_unknown")
    if not values[1].strip() or len(values[1]) > 160 or len(values[46]) > 240:
        raise UsSourceError("美股来源名称无效", code="us_name_invalid")
    try:
        source_time = datetime.strptime(values[30], "%Y-%m-%d %H:%M:%S").replace(tzinfo=NY)
    except ValueError as exc:
        raise UsSourceError("美股来源时钟无效", code="us_source_clock_invalid") from exc
    if source_time.year < 1990 or source_time > evidence.received_at + timedelta(seconds=60):
        raise UsSourceError("美股来源时钟无效", code="us_source_clock_invalid")
    instrument = UsInstrument(
        instrument_id=f"us_equity:US:{source_symbol}:USD", symbol=ticker, source_symbol=source_symbol,
        name=values[1], english_name=values[46] or None, exchange_source_code=identity[2],
        security_type=US_SECURITY_TYPES[values[56]],
    )
    quote = UsRegularQuote(
        price=_number(values[3], positive=True, required=True),
        previous_close=_number(values[4], positive=True, required=True),
        open=_number(values[5], positive=True), high=_number(values[33], positive=True),
        low=_number(values[34], positive=True), change=_number(values[31]), change_pct=_number(values[32]),
        volume_shares=_number(values[36], nonnegative=True), turnover=_number(values[37], nonnegative=True),
        source_time=source_time, received_at=evidence.received_at, observation="session_unverified", evidence=[evidence],
    )
    return instrument, observe_quote(quote, evidence.received_at)


def parse_search(body: bytes, query: str, evidence: AssetEvidence, limit: int = 20) -> UsSearchResult:
    try:
        decoded = body.decode("gb18030")
        assignment = re.fullmatch(r'\s*v_hint=("(?:[^"\\]|\\.)*");?\s*', decoded)
        if not assignment:
            raise ValueError("invalid search assignment")
        content = json.loads(assignment[1])
    except (UnicodeError, ValueError) as exc:
        raise UsSourceError("美股搜索合同无效", code="us_search_shape_invalid") from exc
    lines = content.split("^") if content else []
    if len(lines) > 100:
        raise UsSourceError("美股搜索超出读取范围", code="us_search_too_large")
    items, issues, seen = [], set(), set()
    for line in lines:
        row = line.split("~")
        if len(row) != 5 or row[0] != "us" or row[3] != "pg" or row[4] not in US_SECURITY_TYPES:
            issues.add("unsupported_source_identity")
            continue
        code = row[1].upper()
        match = _canonical_match(code)
        if not match or not row[2].strip() or len(row[2]) > 160 or code in seen:
            issues.add("invalid_or_duplicate_source_identity")
            continue
        seen.add(code)
        items.append(UsSearchCandidate(symbol=match[1], source_symbol=code, name=row[2], source_kind=row[4],
                                      security_type=US_SECURITY_TYPES[row[4]]))
    if len(items) > limit:
        issues.add("source_results_truncated")
    return UsSearchResult(query=query, state="partial" if issues else "ready", items=items[:limit],
                          issues=sorted(issues), evidence=[evidence])


def parse_daily(body: bytes, source_symbol: str, evidence: AssetEvidence, limit: int = 120) -> UsDailySection:
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0:
            raise ValueError("source failed")
        data = payload["data"]
        if not isinstance(data, dict) or "us" + source_symbol not in data:
            raise ValueError("wrong source identity")
        section = data["us" + source_symbol]
        values = section["day"]
        if not isinstance(values, list) or not 1 <= len(values) <= limit:
            raise ValueError("invalid daily count")
        points = []
        for row in values:
            if not isinstance(row, list) or not 6 <= len(row) <= 16:
                raise ValueError("invalid daily row")
            day = date.fromisoformat(row[0])
            if day.year < 1990 or day > evidence.received_at.astimezone(NY).date() or points and day <= points[-1].date:
                raise ValueError("invalid daily date")
            opening, close, high, low = [_number(v, positive=True, required=True) for v in row[1:5]]
            volume = _number(row[5], nonnegative=True, required=True)
            if high < max(opening, close, low) or low > min(opening, close):
                raise ValueError("invalid daily price range")
            points.append(UsDailyPoint(date=day, open=opening, close=close, high=high, low=low, volume_shares=volume))
        return UsDailySection(state="ready", source_symbol=source_symbol, points=points, evidence=[evidence])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise UsSourceError("美股日线身份或数值无效", code="us_daily_invalid") from exc


class UsMarketService:
    def __init__(self, *, enabled: bool = True, evidence_dir: Path | None = None,
                 http_client: httpx.AsyncClient | None = None):
        self.enabled = enabled
        self.evidence_dir = Path(evidence_dir) if evidence_dir is not None else None
        if self.evidence_dir is not None and not self.evidence_dir.is_absolute():
            raise ValueError("US evidence directory must be absolute")
        self._client = http_client
        self._cache = TTLCache("us-source-reads", ttl=30, maxsize=128)
        self._requests = InflightReads(maxsize=4)
        self._snapshots = InflightReads(maxsize=2)
        self._closing = False
        self._close_task: asyncio.Task | None = None
        self._evidence_lock = threading.Lock()

    def _http(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=8, trust_env=False, follow_redirects=False,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=4))
        return self._client

    def _check(self):
        if self._closing:
            raise AppError("美股读取服务正在关闭", code="us_service_closing", status_code=503)
        if not self.enabled:
            raise AppError("美股公开数据读取未启用", code="us_source_disabled", status_code=503)

    async def _raw(self, endpoint: str, params: dict[str, str], ttl: int, parser):
        self._check()
        if endpoint not in SOURCE_URLS:
            raise ValueError("unsupported US source endpoint")
        key = (endpoint, tuple(sorted(params.items())))
        hit, value = self._cache.get(key)
        if hit:
            return deepcopy(value)

        async def load():
            hit, value = self._cache.get(key)
            if hit:
                return value
            try:
                async with asyncio.timeout(10):
                    async with self._http().stream("GET", endpoint, params=params, follow_redirects=False) as response:
                        if response.status_code != 200:
                            raise UsSourceError("美股来源请求失败", code="us_source_request_failed")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_BODY:
                                raise UsSourceError("美股来源响应过大", code="us_source_too_large")
                received = _utcnow()
                digest = hashlib.sha256(body).hexdigest()
                receipt = hashlib.sha256(json.dumps([endpoint, sorted(params.items()), digest, received.isoformat()]).encode()).hexdigest()
                evidence = AssetEvidence(endpoint=endpoint, params=params, received_at=received,
                                         payload_sha256=digest, receipt_id=receipt)
                value = parser(bytes(body), evidence)
                if self.evidence_dir is not None:
                    write = asyncio.create_task(asyncio.to_thread(self._save_evidence, evidence, bytes(body)))
                    try:
                        await asyncio.shield(write)
                    except asyncio.CancelledError:
                        await asyncio.shield(write)
                        raise
                    # The result and cache expose the persisted evidence state.
                    evidence.raw_available = True
                    value = parser(bytes(body), evidence)
                self._cache.set(key, deepcopy(value), ttl=ttl)
                return value
            except (UsSourceError, LocalReadCapacityExceeded):
                raise
            except (httpx.HTTPError, TimeoutError, UnicodeError, ValueError, OSError) as exc:
                raise UsSourceError("美股来源或证据读取失败", code="us_source_request_failed") from exc

        return await self._requests.read(key, load)

    def _save_evidence(self, evidence: AssetEvidence, body: bytes):
        with self._evidence_lock:
            root = self.evidence_dir
            root.mkdir(parents=True, exist_ok=True)
            raw = root / f"{evidence.payload_sha256}.body.bin"
            receipt = root / f"{evidence.receipt_id}.receipt.json"
            if raw.exists() and hashlib.sha256(raw.read_bytes()).hexdigest() != evidence.payload_sha256:
                raise OSError("stored US evidence hash mismatch")
            record = {**evidence.model_dump(mode="json"), "raw_available": True, "raw_file": raw.name}
            encoded = json.dumps(record, ensure_ascii=False, allow_nan=False).encode()
            added = len(encoded) + (0 if raw.exists() else len(body))
            if sum(p.stat().st_size for p in root.iterdir() if p.is_file()) + added > 64 * 1024 * 1024:
                raise OSError("US evidence storage capacity reached")
            if not raw.exists():
                self._atomic(raw, body)
            if not receipt.exists():
                self._atomic(receipt, encoded)

    @staticmethod
    def _atomic(target: Path, content: bytes):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, prefix=".us-read-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    async def search(self, query: str, limit: int = 20) -> UsSearchResult:
        self._check()
        query = query.strip()
        if not 1 <= len(query) <= 80 or not 1 <= limit <= 20:
            raise AppError("美股搜索参数无效", code="invalid_us_search", status_code=422)
        # The source request/cache stores its complete bounded result; the caller
        # limit is a view and must not truncate another caller's cache contract.
        result = await self._raw(SEARCH_URL, {"t": "us", "q": query}, 300,
                                lambda body, evidence: parse_search(body, query, evidence, 100))
        if len(result.items) > limit:
            result.items = result.items[:limit]
            result.state = "partial"
            result.issues = sorted(set(result.issues) | {"source_results_truncated"})
        return result

    async def _quote(self, code: str):
        ticker, _ = _identity(code)
        request = "us" + ticker.replace(".", "__")
        # A shared raw quote may serve a bare ticker and its exact canonical
        # alias; identity validation belongs to each caller after that read.
        instrument, quote = await self._raw(QUOTE_URL, {"q": request}, 15,
            lambda content, receipt: parse_quote(content, ticker, receipt))
        _, canonical = _identity(code)
        if canonical is not None and instrument.source_symbol != canonical:
            raise UsSourceError("美股来源证券身份不一致", code="us_identity_mismatch")
        return instrument, observe_quote(quote, _utcnow())

    async def resolve(self, code: str) -> UsInstrument:
        self._check()
        value, _ = await self._quote(code)
        return value

    async def snapshot(self, code: str, limit: int = 120) -> UsSnapshot:
        self._check()
        _, canonical = _identity(code)
        if limit != 120:
            raise AppError("美股本轮日线范围仅支持120根", code="invalid_us_range", status_code=422)

        async def build():
            async with asyncio.timeout(22):
                instrument, quote = await self._quote(code)
                try:
                    daily = await self._raw(DAILY_URL, {"param": f"us{instrument.source_symbol},day,,,{limit},"}, 300,
                        lambda body, evidence: parse_daily(body, instrument.source_symbol, evidence, limit))
                except (UsSourceError, LocalReadCapacityExceeded) as exc:
                    daily = UsDailySection(state="unavailable", reason=getattr(exc, "code", "local_read_capacity_exceeded"),
                                           source_symbol=instrument.source_symbol)
                missing = ["pre_market_source", "post_market_source", "overnight_source", "intraday_source", "exchange_calendar", "feed_delay_unknown"]
                if daily.state != "ready":
                    missing.append("daily_source")
                observed_at = _utcnow()
                return UsSnapshot(instrument=instrument, regular=observe_quote(quote, observed_at), daily=daily,
                    clock_phase=clock_phase(observed_at), received_at=observed_at, missing=missing)
        try:
            return await self._snapshots.read((canonical or code.upper(), limit), build)
        except TimeoutError as exc:
            raise AppError("美股资料整轮读取超时", code="us_snapshot_timeout", status_code=503) from exc

    async def aclose(self):
        if self._close_task is None:
            self._closing = True

            async def close():
                try:
                    await self._snapshots.close()
                finally:
                    try:
                        await self._requests.close()
                    finally:
                        if self._client is not None:
                            await self._client.aclose()
            self._close_task = asyncio.create_task(close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError
