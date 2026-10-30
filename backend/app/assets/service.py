"""Bounded, independently owned THS fund reads; no quotes or trading side effects."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

import httpx

from app.assets.funds import (
    AssetSourceError, FUND_CODE, fund_code, instrument, parse_holdings, parse_nav,
    parse_terms, rows,
)
from app.core.bjtime import BJ_TZ
from app.core.errors import AppError
from app.core.inflight_reads import InflightReads, LocalReadCapacityExceeded
from app.core.ttl_cache import TTLCache
from app.schemas.assets import (
    AssetCapabilities, AssetCapability, AssetEvidence, AssetInstrument, AssetSearchResult,
    FundEstimateSection, FundExposure, FundHoldingsSection, FundLookthroughSection,
    FundNavSection, FundResearchSection, FundSnapshot, FundTerm, FundTermsSection,
)

SEARCH = "/api/meta/tickers/search"
NAV = "/api/fund/performance/nav"
HOLDINGS = "/api/fund/portfolio/holdings"
PROFILE = "/api/fund/profile/detail"
MAX_BODY = 1_048_576


def _utcnow():
    return datetime.now(timezone.utc)


class AssetReadService:
    def __init__(self, api_key: str = "", base_url: str = "https://fuyao.aicubes.cn", *,
                 enabled: bool = True, evidence_dir: Path | None = None,
                 http_client: httpx.AsyncClient | None = None):
        # Only the documented official host receives the existing credential.
        if base_url.rstrip("/") != "https://fuyao.aicubes.cn":
            raise ValueError("asset reads require the official THS HTTPS origin")
        self.enabled = enabled and bool(api_key)
        self.evidence_dir = Path(evidence_dir) if evidence_dir is not None else None
        self._client = http_client
        self._client_options = dict(
            base_url=base_url, headers={"X-api-key": api_key, "Accept": "application/json"},
            timeout=8, trust_env=False, follow_redirects=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )
        self._cache = TTLCache("fund-source-reads", ttl=300, maxsize=128)
        self._requests = InflightReads(maxsize=4)
        self._snapshots = InflightReads(maxsize=2)
        self._closing = False
        self._close_task: asyncio.Task | None = None
        self._evidence_lock = threading.Lock()

    def _http(self):
        # An unused/disabled asset view must not allocate a TLS context or pool
        # during every ordinary A-share application startup.
        if self._client is None:
            self._client = httpx.AsyncClient(**self._client_options)
        return self._client

    def capabilities(self) -> AssetCapabilities:
        return AssetCapabilities(items=[
            AssetCapability(asset_type="fund", state="available" if self.enabled else "blocked",
                            reason="正式净值与披露研究；不提供基金交易" if self.enabled else "基金数据读取尚未启用或来源未配置",
                            capabilities=["search", "official_nav", "disclosed_holdings", "lookthrough", "fees_and_rules"] if self.enabled else [],
                            unavailable=["realtime_nav", "verified_investment_mandate", "automatic_recommendation"],
                            source="ths_official"),
            AssetCapability(asset_type="us_equity", state="blocked", source=None,
                            reason="尚无获准的完整美股行情源；不以单交易所或延迟行情冒充完整实时行情",
                            capabilities=[], unavailable=["regular", "pre_market", "post_market", "overnight"]),
        ])

    def _check_enabled(self):
        if self._closing:
            raise AppError("基金读取服务正在关闭", code="asset_service_closing", status_code=503)
        if not self.enabled:
            raise AppError("基金数据读取尚未启用或来源未配置", code="asset_source_disabled", status_code=503)

    async def _raw(self, endpoint: str, params: dict[str, str | int]):
        self._check_enabled()
        if endpoint not in {SEARCH, NAV, HOLDINGS, PROFILE}:
            raise ValueError("unsupported fund endpoint")
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
                    async with self._http().stream("GET", endpoint, params=params) as response:
                        if response.status_code != 200:
                            raise AssetSourceError("基金来源请求未成功", code="fund_source_request_failed")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_BODY:
                                raise AssetSourceError("基金来源响应超出本次读取范围", code="fund_source_too_large")
                received = _utcnow()
                payload = json.loads(body)
                if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
                    raise AssetSourceError("基金来源未返回有效业务数据", code="fund_source_request_failed")
                data = payload["data"]
                rows(data, max_rows=400)
                digest = hashlib.sha256(body).hexdigest()
                identity = json.dumps([endpoint, sorted(params.items()), digest], ensure_ascii=False)
                receipt = hashlib.sha256(identity.encode()).hexdigest()
                evidence = AssetEvidence(endpoint=endpoint, params=params, received_at=received,
                                         payload_sha256=digest, receipt_id=receipt)
                if self.evidence_dir is not None:
                    write = asyncio.create_task(asyncio.to_thread(self._save_evidence, evidence, bytes(body)))
                    try:
                        await asyncio.shield(write)
                    except asyncio.CancelledError:
                        await asyncio.shield(write)
                        raise
                    evidence.raw_available = True
                result = (data, evidence)
                ttl = 300 if endpoint == NAV else 1800 if endpoint == PROFILE else 900
                self._cache.set(key, deepcopy(result), ttl=ttl if data["item"] else 30)
                return result
            except (AssetSourceError, LocalReadCapacityExceeded):
                raise
            except (httpx.HTTPError, TimeoutError, ValueError, OSError, KeyError, TypeError) as exc:
                raise AssetSourceError("基金来源或证据读取失败", code="fund_source_request_failed") from exc

        return await self._requests.read(key, load)

    def _discard(self, endpoint, params):
        # A transport-successful body may still fail its business contract.
        # Never let that malformed body pin an unavailable field for its TTL.
        self._cache.invalidate((endpoint, tuple(sorted(params.items()))))

    def _save_evidence(self, evidence: AssetEvidence, body: bytes):
        """One public response per content identity. Never save auth/headers/URL."""
        with self._evidence_lock:
            self._write_evidence(evidence, body)

    def _write_evidence(self, evidence: AssetEvidence, body: bytes):
        root = self.evidence_dir
        root.mkdir(parents=True, exist_ok=True)
        raw_target = root / f"{evidence.payload_sha256}.body.json"
        receipt_target = root / f"{evidence.receipt_id}.receipt.json"
        raw_exists = raw_target.exists()
        if raw_exists:
            if hashlib.sha256(raw_target.read_bytes()).hexdigest() != evidence.payload_sha256:
                raise OSError("stored fund evidence hash mismatch")
        if receipt_target.exists():
            saved = json.loads(receipt_target.read_text(encoding="utf-8"))
            if not raw_exists or saved.get("payload_sha256") != evidence.payload_sha256:
                raise OSError("stored fund receipt reference mismatch")
            return
        record = {**evidence.model_dump(mode="json"), "raw_available": True,
                  "raw_file": raw_target.name}
        content = json.dumps(record, ensure_ascii=False, allow_nan=False).encode()
        added_bytes = len(content) + (0 if raw_exists else len(body))
        if sum(p.stat().st_size for p in root.glob("*.json")) + added_bytes > 64 * 1024 * 1024:
            raise OSError("fund evidence storage capacity reached")
        if not raw_exists:
            self._atomic_evidence_file(raw_target, body)
        self._atomic_evidence_file(receipt_target, content)

    @staticmethod
    def _atomic_evidence_file(target: Path, content: bytes):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, prefix=".fund-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    async def search(self, query: str, asset_type: str = "fund", limit: int = 20) -> AssetSearchResult:
        query = query.strip()
        if not 1 <= len(query) <= 80 or not 1 <= limit <= 20:
            raise AppError("搜索参数不在支持范围", code="invalid_asset_search", status_code=422)
        if asset_type == "us_equity":
            return AssetSearchResult(query=query, asset_type="us_equity", state="blocked", reason=self.capabilities().items[1].reason)
        if asset_type != "fund":
            raise AppError("资产类别未支持", code="invalid_asset_search", status_code=422)
        data, evidence = await self._raw(SEARCH, {"q": query, "asset_type": "fund-otc", "limit": limit})
        source_rows = rows(data, max_rows=limit)
        symbols = [row["thscode"] for row in source_rows if isinstance(row.get("thscode"), str)]
        conflicts = {code for code in symbols if symbols.count(code) > 1}
        found, issues = [], set()
        for row in source_rows:
            try:
                if isinstance(row.get("thscode"), str) and row["thscode"] in conflicts:
                    raise AssetSourceError("来源目录重复", code="duplicate_fund_identity")
                value = instrument(row)
                found.append(value)
            except AssetSourceError as exc:
                issues.add(exc.code)
                self._discard(SEARCH, {"q": query, "asset_type": "fund-otc", "limit": limit})
        if not found:
            issues.add("source_name_search_may_be_incomplete")
        return AssetSearchResult(query=query, asset_type="fund", state="partial" if issues else "ready", items=found,
                                 reason="名称目录可能不完整，可用基金正式代码查询" if not found else None,
                                 issues=sorted(issues), evidence=[evidence])

    async def resolve_fund(self, code: str) -> AssetInstrument:
        code = fund_code(code)
        data, _ = await self._raw(SEARCH, {"q": code, "asset_type": "fund-otc", "limit": 2})
        candidates = rows(data, max_rows=2)
        matches = [row for row in candidates if row.get("thscode") == code]
        if len(matches) != 1:
            if candidates:
                self._discard(SEARCH, {"q": code, "asset_type": "fund-otc", "limit": 2})
            raise AppError("来源未能唯一确认该基金", code="fund_identity_unconfirmed", status_code=404)
        try:
            return instrument(matches[0])
        except AssetSourceError:
            self._discard(SEARCH, {"q": code, "asset_type": "fund-otc", "limit": 2})
            raise

    async def snapshot(self, code: str, range: str = "year") -> FundSnapshot:
        code = fund_code(code)
        self._check_enabled()
        if range not in {"month", "year"}:
            raise AppError("净值区间未支持", code="invalid_fund_range", status_code=422)
        async def load():
            try:
                async with asyncio.timeout(30):
                    return await self._snapshot(code, range)
            except TimeoutError as exc:
                raise AssetSourceError("基金读取超过本次时限", code="fund_source_timeout") from exc
        return await self._snapshots.read((code, range), load)

    async def _snapshot(self, code: str, range: str) -> FundSnapshot:
        verified = await self.resolve_fund(code)
        today = _utcnow().astimezone(BJ_TZ).date()

        async def nav_read():
            evidence = None
            try:
                data, evidence = await self._raw(NAV, {"thscode": code, "range": range, "nav_type": "unit,adj"})
                return parse_nav(data, code, verified.currency, today, evidence)
            except AssetSourceError as exc:
                self._discard(NAV, {"thscode": code, "range": range, "nav_type": "unit,adj"})
                return FundNavSection(state="unavailable", reason=exc.detail, missing=[exc.code], currency=verified.currency,
                                      evidence=[evidence] if evidence else [])

        async def terms_read():
            evidence = None
            try:
                data, evidence = await self._raw(PROFILE, {"thscode": code})
                return parse_terms(data, code, evidence)
            except AssetSourceError as exc:
                self._discard(PROFILE, {"thscode": code})
                return FundTermsSection(state="unavailable", reason=exc.detail, missing=[exc.code],
                                        evidence=[evidence] if evidence else [])

        children = [asyncio.create_task(nav_read()), asyncio.create_task(terms_read())]
        try:
            nav, terms = await asyncio.gather(*children)
        except BaseException:
            for child in children:
                child.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            raise
        holdings = await self._holdings(code, today)
        lookthrough = await self._lookthrough(code, holdings, today)
        checks = [FundTerm(label="净值", value="可核" if nav.latest and nav.latest.unit_nav is not None else "缺失"),
                  FundTerm(label="费率", value="可核" if terms.fees else "缺失"),
                  FundTerm(label="申赎规则", value="有来源规则" if terms.dealing_rules else "缺失"),
                  FundTerm(label="投资范围", value="缺正式文件"),
                  FundTerm(label="业绩基准", value="缺正式文件")]
        research = FundResearchSection(state="unavailable", reason="正式投资范围、基准与申购状态未核实，暂不列为可推荐",
                                       missing=sorted(set(terms.missing + (["unit_nav"] if not nav.latest or nav.latest.unit_nav is None else []))),
                                       checks=checks, evidence=terms.evidence)
        estimate = FundEstimateSection(state="unavailable", reason="缺同口径当前持仓、境外行情与估值汇率，不能可靠估算",
                                       missing=["current_positions", "same_basis_market_prices", "valuation_fx"], currency=verified.currency)
        return FundSnapshot(instrument=verified, state="partial", received_at=_utcnow(), nav=nav, holdings=holdings,
                            lookthrough=lookthrough, terms=terms, research=research, estimate=estimate)

    async def _holdings(self, code, today):
        evidence = None
        try:
            data, evidence = await self._raw(HOLDINGS, {"thscode": code})
            return parse_holdings(data, code, today, evidence)
        except AssetSourceError as exc:
            self._discard(HOLDINGS, {"thscode": code})
            return FundHoldingsSection(state="unavailable", reason=exc.detail, missing=[exc.code],
                                       evidence=[evidence] if evidence else [])

    async def _lookthrough(self, code, root, today):
        found, missing, evidence = [], set(root.missing), list(root.evidence)
        targets = 0
        for holding in root.items:
            if holding.weight_pct is None or holding.report_date is None:
                continue
            if holding.asset_type == "stock":
                found.append(FundExposure(source_symbol=holding.source_symbol, name=holding.name,
                                          weight_pct=holding.weight_pct, via=[code], report_date=holding.report_date))
            elif holding.asset_type == "fund":
                if targets >= 2 or holding.source_symbol == code or not FUND_CODE.fullmatch(holding.source_symbol):
                    missing.add("target_fund_outside_read_scope")
                    continue
                targets += 1
                target = await self._holdings(holding.source_symbol, today)
                evidence.extend(target.evidence)
                if target.report_date is None or target.report_date != holding.report_date:
                    missing.add("target_report_not_aligned")
                    continue
                missing.update(target.missing)
                for child in target.items:
                    if child.asset_type == "stock" and child.weight_pct is not None and child.report_date == holding.report_date:
                        found.append(FundExposure(source_symbol=child.source_symbol, name=child.name,
                                                  weight_pct=round(holding.weight_pct * child.weight_pct / 100, 8),
                                                  via=[code, holding.source_symbol], report_date=holding.report_date))
        coverage = round(sum(row.weight_pct for row in found), 6)
        if coverage > 100.000001:
            raise AssetSourceError("穿透覆盖超出净值，拒绝展示", code="fund_weight_unsupported")
        if coverage < 100:
            missing.add("uncovered_exposure")
        return FundLookthroughSection(state="partial" if found else "unavailable", reason="按披露权重穿透；未披露部分不重归一",
                                      missing=sorted(missing), items=found, evidence=evidence,
                                      covered_weight_pct=coverage, uncovered_weight_pct=round(100 - coverage, 6))

    async def aclose(self):
        self._closing = True
        if self._close_task is None:
            async def drain():
                await self._snapshots.close()
                await self._requests.close()
                if self._client is not None:
                    await self._client.aclose()
            self._close_task = asyncio.create_task(drain())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError
