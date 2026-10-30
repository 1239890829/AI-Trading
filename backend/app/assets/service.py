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
    parse_terms, rows, parse_catalog, validate_quota_shape,
)
from app.assets.us_market import UsMarketService, observe_quote
from app.assets.alpaca_data import AlpacaDataService
from app.assets.fund_public import FundPublicService
from app.core.bjtime import BJ_TZ
from app.core.errors import AppError
from app.core.inflight_reads import InflightReads, LocalReadCapacityExceeded
from app.core.ttl_cache import TTLCache
from app.schemas.assets import (
    AssetCapabilities, AssetCapability, AssetEvidence, AssetInstrument, AssetSearchResult,
    FundEstimateSection, FundExposure, FundHoldingsSection, FundLookthroughSection,
    FundCatalog, FundNavSection, FundResearchSection, FundSnapshot, FundTerm, FundTermsSection,
)
from app.schemas.us_assets import UsSearchResult
from app.schemas.fund_public import FundPublicProfile, FundPublicReference

SEARCH = "/api/meta/tickers/search"
NAV = "/api/fund/performance/nav"
HOLDINGS = "/api/fund/portfolio/holdings"
PROFILE = "/api/fund/profile/detail"
QUOTA = "/api/fund/quota/list"
MAX_BODY = 1_048_576


def _utcnow():
    return datetime.now(timezone.utc)


class AssetReadService:
    def __init__(self, api_key: str = "", base_url: str = "https://fuyao.aicubes.cn", *,
                 enabled: bool = True, evidence_dir: Path | None = None,
                 http_client: httpx.AsyncClient | None = None, us_enabled: bool = False,
                 public_enabled: bool = False, alpaca_data_key: str = "", alpaca_data_secret: str = ""):
        # Only the documented official host receives the existing credential.
        if base_url.rstrip("/") != "https://fuyao.aicubes.cn":
            raise ValueError("asset reads require the official THS HTTPS origin")
        self.enabled = enabled and bool(api_key)
        self.us = UsMarketService(enabled=us_enabled,
                                  evidence_dir=Path(evidence_dir) / "us" if evidence_dir is not None else None)
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
        self.public = FundPublicService(enabled=public_enabled,
                                       evidence_writer=self._save_evidence if self.evidence_dir is not None else None)
        self.overnight = AlpacaDataService(enabled=us_enabled, api_key=alpaca_data_key,
                                          secret_key=alpaca_data_secret,
                                          evidence_writer=self._save_evidence if self.evidence_dir is not None else None)

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
                            capabilities=["search", "official_nav", "disclosed_holdings", "lookthrough", "fees_and_rules", "source_qdii_catalog"] if self.enabled else [],
                            unavailable=["realtime_nav", "verified_investment_mandate", "automatic_recommendation"],
                            source="ths_official"),
            AssetCapability(asset_type="us_equity", state="available" if self.us.enabled else "blocked", source="tencent",
                            reason="公开常规报价、日K与自选查看；时段覆盖和延迟分别标注" if self.us.enabled else "美股公开读取尚未启用",
                            capabilities=["search", "regular_quote", "daily_history", "watchlist"] if self.us.enabled else [],
                            unavailable=["pre_market", "post_market", "overnight_last_trade", "intraday", "verified_feed_delay"]),
        ])

    def _check_enabled(self):
        if self._closing:
            raise AppError("基金读取服务正在关闭", code="asset_service_closing", status_code=503)
        if not self.enabled:
            raise AppError("基金数据读取尚未启用或来源未配置", code="asset_source_disabled", status_code=503)

    async def _raw(self, endpoint: str, params: dict[str, str | int]):
        self._check_enabled()
        if endpoint not in {SEARCH, NAV, HOLDINGS, PROFILE, QUOTA}:
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
                if not isinstance(payload, dict) or payload.get("code") != 0 or "data" not in payload:
                    raise AssetSourceError("基金来源未返回有效业务数据", code="fund_source_request_failed")
                data = payload["data"]
                if endpoint == QUOTA:
                    validate_quota_shape(data)
                    has_items = bool(data)
                else:
                    if not isinstance(data, dict):
                        raise AssetSourceError("基金来源未返回有效业务数据", code="fund_source_request_failed")
                    has_items = bool(rows(data, max_rows=400))
                digest = hashlib.sha256(body).hexdigest()
                # The body is content-addressed, but a receipt describes this
                # actual observation. An unchanged body fetched after TTL must
                # not reuse an older receipt carrying a different receive clock.
                identity = json.dumps([endpoint, sorted(params.items()), digest, received.isoformat()], ensure_ascii=False)
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
                ttl = 300 if endpoint in {NAV, QUOTA} else 1800 if endpoint == PROFILE else 900
                self._cache.set(key, deepcopy(result), ttl=ttl if has_items else 30)
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
        """One body per content identity; only fixed source URLs and public params."""
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

    async def catalog(self, purchase: str = "all", tab: str = "meiguo") -> FundCatalog:
        if purchase not in {"all", "source_buyable"} or tab != "meiguo":
            raise AppError("来源候选目录参数未支持", code="invalid_fund_catalog", status_code=422)
        params = {"tab": '["meiguo"]'}
        # Omitting buy is distinct from explicitly asking for the source's
        # buyable list. Neither result establishes a negative purchase verdict.
        if purchase == "source_buyable":
            params["buy"] = "true"
        data, evidence = await self._raw(QUOTA, params)
        try:
            return parse_catalog(data, purchase, evidence)
        except AssetSourceError:
            self._discard(QUOTA, params)
            raise

    async def search(self, query: str, asset_type: str = "fund", limit: int = 20):
        query = query.strip()
        if not 1 <= len(query) <= 80 or not 1 <= limit <= 20:
            raise AppError("搜索参数不在支持范围", code="invalid_asset_search", status_code=422)
        if asset_type == "us_equity":
            if not self.us.enabled:
                return UsSearchResult(query=query, state="blocked", reason=self.capabilities().items[1].reason)
            return await self.us.search(query, limit)
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

    async def resolve_us(self, code: str):
        return await self.us.resolve(code)

    async def us_snapshot(self, code: str, limit: int = 120):
        snapshot = (await self.us.snapshot(code, limit)).model_copy(deep=True)
        # This optional observation never replaces the Tencent last price or
        # claims a trade, consolidated feed or all-session availability.
        snapshot.overnight_data = await self.overnight.latest(snapshot.instrument)
        if snapshot.overnight_data.state == "available" and snapshot.overnight_data.authorization_verified and snapshot.overnight_data.quote is not None:
            snapshot.missing = [item for item in snapshot.missing if item != "overnight_source"]
            # A bid/ask observation does not establish a latest trade.
            snapshot.missing.append("overnight_last_trade")
        snapshot.regular = observe_quote(snapshot.regular, _utcnow())
        return snapshot

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
                # The same total budget includes identity resolution. A slow
                # optional section must not discard already completed facts.
                deadline = asyncio.get_running_loop().time() + 30
                return await self._snapshot(code, range, deadline)
            except TimeoutError as exc:
                raise AssetSourceError("基金读取超过本次时限", code="fund_source_timeout") from exc
        return await self._snapshots.read((code, range), load)

    async def _snapshot(self, code: str, range: str, deadline: float) -> FundSnapshot:
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(max(0, deadline - loop.time())):
            verified = await self.resolve_fund(code)
        today = _utcnow().astimezone(BJ_TZ).date()

        async def nav_read():
            evidence = None
            try:
                data, evidence = await self._raw(NAV, {"thscode": code, "range": range, "nav_type": "unit,adj"})
                return parse_nav(data, code, verified.currency, today, evidence)
            except (AssetSourceError, LocalReadCapacityExceeded) as exc:
                self._discard(NAV, {"thscode": code, "range": range, "nav_type": "unit,adj"})
                return FundNavSection(state="unavailable", reason=getattr(exc, "detail", "本机来源读取忙"),
                                      missing=[getattr(exc, "code", "fund_source_local_busy")], currency=verified.currency,
                                      evidence=[evidence] if evidence else [])

        async def terms_read():
            evidence = None
            try:
                data, evidence = await self._raw(PROFILE, {"thscode": code})
                return parse_terms(data, code, evidence)
            except (AssetSourceError, LocalReadCapacityExceeded) as exc:
                self._discard(PROFILE, {"thscode": code})
                return FundTermsSection(state="unavailable", reason=getattr(exc, "detail", "本机来源读取忙"),
                                        missing=[getattr(exc, "code", "fund_source_local_busy")],
                                        evidence=[evidence] if evidence else [])

        holdings_task = None

        async def lookthrough_read():
            root = await asyncio.shield(holdings_task)
            try:
                return await self._lookthrough(code, root, today)
            except (AssetSourceError, LocalReadCapacityExceeded) as exc:
                return FundLookthroughSection(state="unavailable", reason=getattr(exc, "detail", "本机来源读取忙"),
                    missing=[getattr(exc, "code", "fund_source_local_busy")], evidence=root.evidence)

        children = {"nav": asyncio.create_task(nav_read()), "terms": asyncio.create_task(terms_read()),
                    "holdings": asyncio.create_task(self._holdings(code, today))}
        holdings_task = children["holdings"]
        children["lookthrough"] = asyncio.create_task(lookthrough_read())
        children["public_profile"] = asyncio.create_task(self.public.profile(code))
        children["reference"] = asyncio.create_task(self.public.reference(code))
        try:
            _, pending = await asyncio.wait(children.values(), timeout=max(0, deadline - loop.time()))
            # Only local waiters are canceled. Source owners remain registered
            # until their own deadlines and aclose drains them before shutdown.
            for child in pending:
                child.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            timeout_reason = "本次30秒读取预算已用尽"
            unavailable = {
                "nav": FundNavSection(state="unavailable", reason=timeout_reason,
                    missing=["fund_source_timeout"], currency=verified.currency),
                "terms": FundTermsSection(state="unavailable", reason=timeout_reason, missing=["fund_source_timeout"]),
                "holdings": FundHoldingsSection(state="unavailable", reason=timeout_reason, missing=["fund_source_timeout"]),
                "lookthrough": FundLookthroughSection(state="unavailable", reason=timeout_reason, missing=["fund_source_timeout"]),
                "public_profile": FundPublicProfile(source_symbol=code, state="unavailable", missing=["fund_public_timeout"]),
                "reference": FundPublicReference(source_symbol=code, state="unavailable", missing=["fund_public_timeout"]),
            }
            result = {key: unavailable[key] if task in pending else task.result() for key, task in children.items()}
        except BaseException:
            for child in children.values():
                child.cancel()
            await asyncio.gather(*children.values(), return_exceptions=True)
            raise
        nav, terms, holdings, lookthrough = (result[key] for key in ("nav", "terms", "holdings", "lookthrough"))
        public_profile, reference = result["public_profile"], result["reference"]
        public_fields = {field.key: field for field in public_profile.fields}
        currency = public_fields.get("currency")
        if public_profile.source_symbol != code or currency is not None and currency.value != verified.currency:
            reason = "fund_public_identity_mismatch" if public_profile.source_symbol != code else "fund_public_currency_mismatch"
            public_profile = public_profile.model_copy(update={"state": "unavailable", "fields": [],
                "missing": sorted(set(public_profile.missing + [reason]))})
            reference = FundPublicReference(source_symbol=code, state="unavailable", missing=[reason],
                                            evidence=reference.evidence)
            public_fields = {}
        if reference.source_symbol != code:
            reference = FundPublicReference(source_symbol=code, state="unavailable", missing=["fund_reference_identity_mismatch"],
                                            evidence=reference.evidence)
        supplemented = False
        for key in ("investment_scope", "benchmark", "subscription_status"):
            field = public_fields.get(key)
            if field is not None and getattr(terms, key) is None:
                setattr(terms, key, field.value)
                terms.missing = [value for value in terms.missing if value != key]
                supplemented = True
        if supplemented:
            terms.source = "mixed_public_sources"
            terms.evidence = list({item.receipt_id: item for item in terms.evidence + public_profile.evidence}.values())
            terms.state = "partial" if terms.missing or public_profile.conflicts else "ready"
            terms.reason = "费率及规则按来源原文；投资范围、基准和申购状态由逐字段公开产品说明补充"
        checks = [FundTerm(label="净值", value="可核" if nav.latest and nav.latest.unit_nav is not None else "缺失"),
                  FundTerm(label="费率", value="可核" if terms.fees else "缺失"),
                  FundTerm(label="申赎规则", value="有来源规则" if terms.dealing_rules else "缺失"),
                  FundTerm(label="投资范围", value="公开资料具备" if terms.investment_scope else "缺失"),
                  FundTerm(label="业绩基准", value="公开资料具备" if terms.benchmark else "缺失"),
                  FundTerm(label="申购状态", value=terms.subscription_status or "缺失")]
        research = FundResearchSection(state="partial" if any(check.value != "缺失" for check in checks) else "unavailable",
                                       reason="按已取得资料逐项核对；公开产品说明不等于监管文件核验，不构成推荐或策略准入",
                                       missing=sorted(set(terms.missing + (["unit_nav"] if not nav.latest or nav.latest.unit_nav is None else []))),
                                       checks=checks, evidence=terms.evidence)
        estimate = FundEstimateSection(state="unavailable", reason="缺同口径当前持仓、境外行情与估值汇率，不能可靠估算",
                                       missing=["current_positions", "same_basis_market_prices", "valuation_fx"], currency=verified.currency)
        return FundSnapshot(instrument=verified, state="partial", received_at=_utcnow(), nav=nav, holdings=holdings,
                            lookthrough=lookthrough, terms=terms, research=research, estimate=estimate,
                            public_profile=public_profile, reference=reference)

    async def _holdings(self, code, today):
        evidence = None
        try:
            data, evidence = await self._raw(HOLDINGS, {"thscode": code})
            return parse_holdings(data, code, today, evidence)
        except (AssetSourceError, LocalReadCapacityExceeded) as exc:
            self._discard(HOLDINGS, {"thscode": code})
            return FundHoldingsSection(state="unavailable", reason=getattr(exc, "detail", "本机来源读取忙"),
                                       missing=[getattr(exc, "code", "fund_source_local_busy")],
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
                try:
                    await self._snapshots.close()
                finally:
                    try:
                        await self._requests.close()
                    finally:
                        try:
                            await self.public.aclose()
                        finally:
                            try:
                                await self.us.aclose()
                            finally:
                                try:
                                    await self.overnight.aclose()
                                finally:
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
