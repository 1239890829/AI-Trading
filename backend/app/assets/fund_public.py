"""Credential-free, bounded public fund descriptions and explicit holdings references."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import math
import re
import unicodedata
from typing import Callable

from bs4 import BeautifulSoup
from bs4.element import Tag
import httpx

from app.assets.funds import fund_code
from app.core.bjtime import BJ_TZ
from app.core.errors import AppError
from app.core.inflight_reads import InflightReads, LocalReadCapacityExceeded
from app.core.ttl_cache import TTLCache
from app.schemas.fund_public import (
    FundPublicEvidence, FundPublicField, FundPublicProfile, FundPublicReference,
    FundReferenceCurve, FundReferencePoint,
)

EASTMONEY = "https://fundf10.eastmoney.com"
HUAAN = "https://www.huaan.com.cn"
SINA = "https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/FdFundService.getEstimateNetworthPic"
MAX_BODY = 524_288
MAX_POINTS = 1000

FIELD_LABELS = {
    "name": "基金简称", "full_name": "法定名称", "fund_type": "基金类型", "manager": "基金管理人",
    "trustee": "基金托管人", "fund_manager": "基金经理", "currency": "份额币种",
    "established_date": "成立日期", "net_assets": "净资产规模", "benchmark": "业绩比较基准",
    "tracked_index": "跟踪标的", "investment_objective": "投资目标", "investment_scope": "投资范围",
    "investment_strategy": "投资策略", "subscription_status": "申购状态",
    "redemption_status": "赎回状态", "automatic_investment_status": "定投状态", "risk_level": "风险等级",
}


class FundPublicSourceError(AppError):
    status_code = 502
    code = "fund_public_source_unavailable"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _cell(node: Tag) -> str:
    # Real F10 rows omit some closing td tags. Do not swallow the next th/td.
    values = []
    for child in node.contents:
        if isinstance(child, Tag) and child.name in {"th", "td"}:
            break
        values.append(_text(child) if isinstance(child, Tag) else str(child))
    return re.sub(r"\s+", " ", " ".join(values)).strip()


def _known(value: str) -> bool:
    return bool(value and value not in {"--", "---", "暂无数据", "暂无", "-"})


def _field(key: str, value: str, source: str, url: str) -> FundPublicField | None:
    if not _known(value):
        return None
    limit = 6000 if key.startswith("investment_") else 600
    if len(value) > limit:
        raise FundPublicSourceError("基金公开资料字段过长", code="fund_public_shape_invalid")
    return FundPublicField(key=key, label=FIELD_LABELS[key], value=value, source=source, source_url=url)


def parse_profile_html(body: bytes, symbol: str, source: str) -> list[FundPublicField]:
    """Parse visible source tables only. Never execute remote JavaScript."""
    if source not in {"eastmoney_fund", "huaan_fund"}:
        raise ValueError("unsupported public product description")
    code = fund_code(symbol).split(".")[0]
    soup = BeautifulSoup(body, "html.parser", from_encoding="utf-8")
    candidates = []
    for table in soup.find_all("table"):
        keys = table.find_all("th") if source == "eastmoney_fund" else table.select("td.hd")
        for key in keys:
            if _text(key) == "基金代码":
                cell = key.find_next_sibling("td")
                if cell is not None:
                    candidates.append((table, _cell(cell)))
    if len(candidates) != 1 or not re.fullmatch(re.escape(code) + r"(?:[（(]前端[）)])?", candidates[0][1]):
        raise FundPublicSourceError("公开基金资料代码不一致", code="fund_public_identity_mismatch")
    table = candidates[0][0]
    source_url = f"{EASTMONEY}/jbgk_{code}.html" if source == "eastmoney_fund" else f"{HUAAN}/funds/{code}/index.shtml"
    mapping = {
        "基金简称": "name", "基金全称": "full_name", "法定名称": "full_name", "基金类型": "fund_type",
        "基金管理人": "manager", "基金托管人": "trustee", "基金经理人": "fund_manager", "基金经理": "fund_manager",
        "币种": "currency", "成立日期/规模": "established_date", "成立日期": "established_date",
        "净资产规模": "net_assets", "最新规模": "net_assets", "业绩比较基准": "benchmark",
        "跟踪标的": "tracked_index", "产品风险等级": "risk_level",
    }
    values: dict[str, str] = {}
    labels = table.find_all("th") if source == "eastmoney_fund" else table.select("td.hd")
    for label in labels:
        name = _text(label)
        cell = label.find_next_sibling("td")
        if cell is None:
            continue
        if name == "交易状态":
            for entry in cell.select("li.trade_status"):
                value = _text(entry)
                target = ("subscription_status" if "申购" in value else "redemption_status" if "赎回" in value
                          else "automatic_investment_status" if "定投" in value else None)
                if target:
                    if target in values and values[target] != value:
                        raise FundPublicSourceError("基金交易状态冲突", code="fund_public_shape_invalid")
                    values[target] = value
            continue
        if name not in mapping:
            continue
        key, value = mapping[name], _cell(cell)
        if _known(value):
            if key in values and values[key] != value:
                raise FundPublicSourceError("公开基金资料同字段冲突", code="fund_public_shape_invalid")
            values[key] = value
    if source == "eastmoney_fund":
        for label, key in {"投资目标": "investment_objective", "投资范围": "investment_scope",
                           "投资策略": "investment_strategy"}.items():
            headings = [h for h in soup.find_all("h4") if _text(h) == label]
            if len(headings) > 1:
                raise FundPublicSourceError("公开基金章节重复", code="fund_public_shape_invalid")
            heading = headings[0] if headings else None
            paragraph = heading.parent.find("p", recursive=False) if heading else None
            if paragraph:
                values[key] = _text(paragraph)
    result = [value for key, raw in values.items() if (value := _field(key, raw, source, source_url)) is not None]
    if not any(value.key == "name" for value in result) or not any(value.key == "full_name" for value in result):
        raise FundPublicSourceError("公开基金资料缺少名称", code="fund_public_shape_invalid")
    return result


def _number(value) -> float | None:
    if value is None or value in ("", "--", "---"):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise FundPublicSourceError("参考数值格式无效", code="fund_reference_shape_invalid")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise FundPublicSourceError("参考数值格式无效", code="fund_reference_shape_invalid") from exc
    if not math.isfinite(result):
        raise FundPublicSourceError("参考数值非有限数", code="fund_reference_shape_invalid")
    return result


def parse_reference(body: bytes, symbol: str, evidence: FundPublicEvidence, *,
                    observed_at: datetime | None = None) -> FundPublicReference:
    code = fund_code(symbol).split(".")[0]
    try:
        payload = json.loads(body)
        result = payload["result"]
        status = result["status"]["code"]
        if isinstance(status, bool) or status != 0:
            raise ValueError("source rejected reference")
        data = result["data"]
        if not isinstance(data, dict):
            raise ValueError("invalid data object")
    except (ValueError, TypeError, KeyError) as exc:
        raise FundPublicSourceError("基金参考来源未返回有效数据", code="fund_reference_shape_invalid") from exc
    rows = data.get("networth")
    if not isinstance(rows, list) or len(rows) > MAX_POINTS:
        raise FundPublicSourceError("基金参考序列无效", code="fund_reference_shape_invalid")
    desc = data.get("desc")
    warning = desc.get("text_base") if isinstance(desc, dict) else None
    if not isinstance(warning, str) or not warning.strip() or len(warning) > 3000:
        raise FundPublicSourceError("基金参考缺少来源口径说明", code="fund_reference_scope_missing")
    # Source prices called pre_nav are not proof of a fund NAV forecast. The
    # current response explicitly restricts them to previous-quarter holdings.
    if "前十大重仓" not in warning:
        raise FundPublicSourceError("基金参考口径发生变化", code="fund_reference_scope_changed")
    if evidence.received_at.tzinfo is None:
        raise FundPublicSourceError("接收钟未提供时区", code="fund_reference_time_invalid")
    received = evidence.received_at.astimezone(BJ_TZ)
    observed = (observed_at or evidence.received_at).astimezone(BJ_TZ)
    if observed_at is not None and (observed_at.tzinfo is None or observed < received):
        raise FundPublicSourceError("读取钟早于接收钟", code="fund_reference_time_invalid")
    points: dict[datetime, tuple[FundReferencePoint, FundReferencePoint]] = {}
    missing: set[str] = set()
    source_dates: set[date] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("symbol") != code:
            raise FundPublicSourceError("参考序列证券身份不一致", code="fund_reference_identity_mismatch")
        try:
            raw_date, raw_time = row["pre_date"], row["min_time"]
            if (not isinstance(raw_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date)
                    or not isinstance(raw_time, str) or not re.fullmatch(r"\d{2}:\d{2}:\d{2}", raw_time)):
                raise ValueError("invalid date/time")
            at = datetime.fromisoformat(f"{raw_date}T{raw_time}").replace(tzinfo=BJ_TZ)
            if at > received:
                raise ValueError("future source point")
        except (ValueError, TypeError, KeyError) as exc:
            raise FundPublicSourceError("参考序列源钟无效", code="fund_reference_time_invalid") from exc
        source_dates.add(at.date())
        pair = []
        for value_key, pct_key, ratio_key in (("pre_nav", "nav_pct", "growthrate"),
                                               ("pre_nav2", "nav2_pct", "growthrate2")):
            pct, value = _number(row.get(pct_key)), _number(row.get(value_key))
            ratio = _number(row.get(ratio_key))
            if ratio is not None and pct is not None and abs(ratio * 100 - pct) > .000051:
                missing.add(f"{pct_key}_ratio_conflict")
                pct = None
            if pct is None:
                missing.add(f"{pct_key}_missing")
            if value is not None and value <= 0:
                raise FundPublicSourceError("参考量非正数", code="fund_reference_shape_invalid")
            pair.append(FundReferencePoint(as_of=at, percent_pct=pct, reference_value=value))
        typed = (pair[0], pair[1])
        if at in points and points[at] != typed:
            raise FundPublicSourceError("参考序列同钟冲突", code="fund_reference_duplicate_conflict")
        points[at] = typed
    if len(source_dates) > 1:
        raise FundPublicSourceError("参考序列混入不同来源日", code="fund_reference_time_invalid")
    ordered = sorted(points.items())
    curves = [FundReferenceCurve(key=key, label=label,
                                latest_pct=ordered[-1][1][index].percent_pct if ordered else None,
                                points=[pair[index] for _, pair in ordered])
              for index, (key, label) in enumerate((("nav_pct", "来源口径一"), ("nav2_pct", "来源口径二")))]
    nav, nav_date = _number(data.get("worth")), None
    if nav is not None and nav <= 0:
        raise FundPublicSourceError("公布净值无效", code="fund_reference_shape_invalid")
    if data.get("worth_date") is not None:
        try:
            raw = data["worth_date"]
            if not isinstance(raw, str) or not re.fullmatch(r"\d{8}", raw):
                raise ValueError("invalid disclosed NAV date")
            nav_date = datetime.strptime(raw, "%Y%m%d").date()
            if nav_date > received.date():
                raise ValueError("future disclosed NAV")
        except (TypeError, ValueError) as exc:
            raise FundPublicSourceError("公布净值日无效", code="fund_reference_time_invalid") from exc
    heavy = data.get("heavy_stock_us_flag")
    if heavy is not None and (type(heavy) is not int or heavy not in (0, 1)):
        raise FundPublicSourceError("参考境外标识无效", code="fund_reference_shape_invalid")
    last = ordered[-1][0] if ordered else None
    today = last is not None and last.date() == observed.date()
    if not ordered:
        missing.add("reference_points_missing")
    return FundPublicReference(
        source_symbol=symbol, state="unavailable" if not ordered else "stale" if not today
        else "partial" if missing else "ready", warning=warning, source_as_of=last,
        trading_date=last.date() if last else None, is_current_day=today,
        age_seconds=(observed - last).total_seconds() if last else None,
        disclosed_nav=nav, disclosed_nav_date=nav_date,
        heavy_stock_us=bool(heavy) if heavy is not None else None, curves=curves,
        missing=sorted(missing), evidence=[evidence],
    )


class FundPublicService:
    """No key/cookie copy, arbitrary origin, retry, JavaScript execution or model call."""

    def __init__(self, *, enabled: bool = True, http_client: httpx.AsyncClient | None = None,
                 evidence_writer: Callable[[FundPublicEvidence, bytes], None] | None = None):
        if http_client is not None and any(name in http_client.headers
                                           for name in ("authorization", "x-api-key", "cookie", "proxy-authorization")):
            raise ValueError("public fund client must not carry credentials")
        self.enabled = enabled
        self._client = http_client
        self._writer = evidence_writer
        self._cache = TTLCache("fund-public-source", ttl=300, maxsize=128)
        self._requests = InflightReads(maxsize=4)
        self._operations = InflightReads(maxsize=4)
        self._closing = False
        self._close_task: asyncio.Task | None = None

    def _http(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=8, trust_env=False, follow_redirects=False,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=4))
        return self._client

    async def _raw(self, url: str, params: dict[str, str] | None = None, *, ttl: int = 300):
        if not self.enabled or self._closing:
            raise FundPublicSourceError("公开基金读取未启用或正在关闭", code="fund_public_disabled")
        params = params or {}
        allowed = (re.fullmatch(re.escape(EASTMONEY) + r"/jbgk_\d{6}\.html", url)
                   or re.fullmatch(re.escape(HUAAN) + r"/funds/\d{6}/index\.shtml", url)
                   or url == SINA)
        if (not allowed or (url != SINA and params)
                or (url == SINA and (set(params) != {"symbol"}
                    or not re.fullmatch(r"\d{6}", params.get("symbol", ""))))):
            raise ValueError("unsupported public fund source")
        key = (url, tuple(sorted(params.items())))
        hit, value = self._cache.get(key)
        if hit:
            return deepcopy(value)

        async def load():
            try:
                async with asyncio.timeout(8):
                    async with self._http().stream("GET", url, params=params, follow_redirects=False) as response:
                        if response.status_code != 200:
                            raise FundPublicSourceError("公开基金来源读取失败", code="fund_public_http_failed")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_BODY:
                                raise FundPublicSourceError("公开基金来源超出读取范围", code="fund_public_too_large")
                raw = bytes(body)
                received = _now()
                digest = hashlib.sha256(raw).hexdigest()
                identity = json.dumps([url, sorted(params.items()), digest, received.isoformat()])
                evidence = FundPublicEvidence(endpoint=url, params=params, received_at=received,
                    payload_sha256=digest, receipt_id=hashlib.sha256(identity.encode()).hexdigest())
                if self._writer is not None:
                    work = asyncio.create_task(asyncio.to_thread(self._writer, evidence, raw))
                    try:
                        await asyncio.shield(work)
                    except asyncio.CancelledError:
                        await asyncio.shield(work)
                        raise
                    evidence.raw_available = True
                result = raw, evidence
                self._cache.set(key, deepcopy(result), ttl=ttl)
                return result
            except (FundPublicSourceError, LocalReadCapacityExceeded):
                raise
            except (httpx.HTTPError, TimeoutError, ValueError, OSError) as exc:
                raise FundPublicSourceError("公开基金来源或回执读取失败") from exc

        return await self._requests.read(key, load)

    def _discard(self, url, params=None):
        self._cache.invalidate((url, tuple(sorted((params or {}).items()))))

    async def profile(self, code: str) -> FundPublicProfile:
        symbol = fund_code(code)
        if not self.enabled or self._closing:
            return FundPublicProfile(source_symbol=symbol, state="unavailable", missing=["fund_public_disabled"])

        async def load():
            fields: dict[str, FundPublicField] = {}
            evidence, missing, conflicts = [], set(), set()
            url = f"{EASTMONEY}/jbgk_{symbol.split('.')[0]}.html"
            try:
                async with asyncio.timeout(18):
                    body, receipt = await self._raw(url, ttl=3600)
                    evidence.append(receipt)
                    try:
                        for item in parse_profile_html(body, symbol, "eastmoney_fund"):
                            fields[item.key] = item
                    except FundPublicSourceError:
                        self._discard(url)
                        raise
                    manager = fields.get("manager")
                    if manager and manager.value in {"华安基金", "华安基金管理有限公司"}:
                        manager_url = f"{HUAAN}/funds/{symbol.split('.')[0]}/index.shtml"
                        try:
                            body, receipt = await self._raw(manager_url)
                            evidence.append(receipt)
                            try:
                                official = parse_profile_html(body, symbol, "huaan_fund")
                            except FundPublicSourceError:
                                self._discard(manager_url)
                                raise
                            for item in official:
                                previous = fields.get(item.key)
                                normalized = lambda text: unicodedata.normalize("NFKC", text).replace(" ", "")
                                if previous and normalized(previous.value) != normalized(item.value):
                                    conflicts.add(item.key)
                                fields[item.key] = item
                        except (FundPublicSourceError, LocalReadCapacityExceeded) as exc:
                            missing.add(getattr(exc, "code", "fund_public_local_busy"))
            except (FundPublicSourceError, LocalReadCapacityExceeded, TimeoutError) as exc:
                missing.add(getattr(exc, "code", "fund_public_timeout" if isinstance(exc, TimeoutError) else "fund_public_local_busy"))
            required = {"name", "full_name", "investment_scope", "benchmark", "currency",
                        "subscription_status", "redemption_status"}
            missing.update(required - fields.keys())
            return FundPublicProfile(source_symbol=symbol,
                state="unavailable" if not fields else "partial" if missing or conflicts else "ready",
                fields=list(fields.values()), evidence=evidence, missing=sorted(missing), conflicts=sorted(conflicts))

        try:
            return await self._operations.read(("profile", symbol), load)
        except LocalReadCapacityExceeded:
            return FundPublicProfile(source_symbol=symbol, state="unavailable", missing=["fund_public_local_busy"])

    async def reference(self, code: str) -> FundPublicReference:
        symbol = fund_code(code)
        if not self.enabled or self._closing:
            return FundPublicReference(source_symbol=symbol, state="unavailable", missing=["fund_public_disabled"])

        async def load():
            evidence = []
            params = {"symbol": symbol.split(".")[0]}
            try:
                body, receipt = await self._raw(SINA, params, ttl=60)
                evidence.append(receipt)
                try:
                    return parse_reference(body, symbol, receipt, observed_at=_now())
                except FundPublicSourceError:
                    self._discard(SINA, params)
                    raise
            except (FundPublicSourceError, LocalReadCapacityExceeded) as exc:
                return FundPublicReference(source_symbol=symbol, state="unavailable", evidence=evidence,
                    missing=[getattr(exc, "code", "fund_public_local_busy")])

        try:
            return await self._operations.read(("reference", symbol), load)
        except LocalReadCapacityExceeded:
            return FundPublicReference(source_symbol=symbol, state="unavailable", missing=["fund_public_local_busy"])

    async def aclose(self):
        self._closing = True
        if self._close_task is None:
            async def finish():
                try:
                    await self._operations.close()
                finally:
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
