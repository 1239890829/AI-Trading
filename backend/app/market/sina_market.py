"""新浪全市场快照（Market Center API）。

实测 2026-08-28：node=hs_a 覆盖沪深京 A 股 5550 只，标准 JSON，字段：
symbol(sh600519)、name、trade(现价)、pricechange、changepercent、settlement(昨收)、
open/high/low、volume(股)、amount(元)、mktcap/nmc(万元)、turnoverratio、ticktime。
东财 clist 被限流后，这是全市场扫描的主源。
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from time import monotonic

import httpx

from app.data_providers.eastmoney import ProviderError

SOURCE = "sina_market"
_BASE = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
log = logging.getLogger(__name__)

#: 新浪 WAF 限流状态码。2026-09-14 实测：重启后端触发冷启动全量抓取（56 页）时
#: 首步 stock count 即返回 456，而**同一时刻命令行单发 curl 仍 200** ⇒ 限流判的是
#: 请求特征/瞬时并发，不是整 IP 封禁。代价：`snapshot_service` 连续两轮失败，
#: 快照约 6 分钟不就绪 ⇒ 盘面/工作台「两市成交额」与全市场宽度全线为空。
RATE_LIMIT_STATUS = 456


class SinaRateLimited(ProviderError):
    """新浪 WAF 限流（HTTP 456）。

    **必须与普通失败分开对待**：限流期内继续按常规退避重试（本仓原为
    120s → 240s → 300s cap）只会加深封禁，调用方须改用更长的冷却。
    单独成类（而不是靠匹配异常文本）是为了让调用方的判据结构化——
    消息文案随时可改，类型不会。
    """


class SinaSnapshotDeadline(ProviderError):
    """The original whole-market collection deadline has expired."""


class _IncompletePage(ProviderError):
    """A successful list response is short; one same-generation retry is allowed."""


def _raise_http(status: int, what: str) -> None:
    """把非 200 响应转为异常；**限流单独成类**，其余保持 `ProviderError`。"""
    if status == RATE_LIMIT_STATUS:
        raise SinaRateLimited(f"sina {what} HTTP {status}（WAF 限流）")
    raise ProviderError(f"sina {what} HTTP {status}")


def _f(v) -> float | None:
    try:
        number = float(v)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def parse_row(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    sym = str(raw.get("symbol") or "")
    code = str(raw.get("code") or "")
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    market = sym[:2].upper() if sym[:2] in {"sh", "sz", "bj"} else None
    if market is None or sym != market.lower() + code:
        return None
    return {
        "symbol": code,
        "name": raw.get("name"),
        "market": market,
        "price": _f(raw.get("trade")),
        "open": _f(raw.get("open")),
        "high": _f(raw.get("high")),
        "low": _f(raw.get("low")),
        "prev_close": _f(raw.get("settlement")),
        "change": _f(raw.get("pricechange")),
        "change_pct": _f(raw.get("changepercent")),
        "volume": _f(raw.get("volume")),  # 股
        "amount": _f(raw.get("amount")),  # 元
        "turnover_rate": _f(raw.get("turnoverratio")),
        "mktcap": _f(raw.get("mktcap")),  # 万元
        "nmc": _f(raw.get("nmc")),  # 流通市值 万元
        "ticktime": raw.get("ticktime"),
        "source": SOURCE,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }


async def fetch_market_snapshot(
    page_size: int = 100, concurrency: int = 6, timeout: float = 8.0, *, deadline: float | None = None,
) -> list[dict]:
    """Collect one complete generation; repair only failed pages within one deadline.

    The live snapshot owner supplies its nominal poll slot. Standalone reads use
    60 seconds. Good pages are never re-fetched or filled from an older snapshot.
    """
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be an integer from 1 to 100")
    if type(concurrency) is not int or not 1 <= concurrency <= 6:
        raise ValueError("concurrency must be an integer from 1 to 6")
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    deadline = monotonic() + 60.0 if deadline is None else deadline
    if not math.isfinite(deadline):
        raise ValueError("deadline must be finite")
    observed_rate_limit = None

    def remaining() -> float:
        left = deadline - monotonic()
        if left <= 0:
            raise SinaSnapshotDeadline("sina snapshot deadline exceeded")
        return left

    async def collect(client):
        async def request(path, params):
            nonlocal observed_rate_limit
            request_scope = asyncio.timeout(min(timeout, remaining()))
            try:
                async with request_scope:
                    response = await client.get(f"{_BASE}/{path}", params=params)
            except TimeoutError:
                if request_scope.expired():
                    remaining()  # Whole-round expiry is never a retryable page timeout.
                    raise httpx.ReadTimeout("sina request timeout") from None
                raise
            what = f"page {params['page']}" if "page" in params else "stock count"
            if response.status_code == RATE_LIMIT_STATUS:
                observed_rate_limit = SinaRateLimited(f"sina {what} HTTP {RATE_LIMIT_STATUS}（WAF 限流）")
                raise observed_rate_limit
            if response.status_code != 200:
                _raise_http(response.status_code, what)
            remaining()
            if request_scope.expired():  # A source must not suppress request cancellation.
                raise ProviderError(f"sina {what} returned after timeout")
            return response

        resp = await request("Market_Center.getHQNodeStockCount", {"node": "hs_a"})
        if resp.status_code != 200:
            _raise_http(resp.status_code, "stock count")
        count_text = resp.text.strip().strip('"')
        if not count_text.isascii() or not count_text.isdigit():
            raise ProviderError("sina stock count 响应不可解析（非数字）")
        total = int(count_text)
        if total <= 0:
            raise ProviderError(f"sina stock count 非正数: {total}")
        pages = (total + page_size - 1) // page_size

        async def page(p: int):
            r = await request(
                "Market_Center.getHQNodeData",
                {"page": p, "num": page_size, "sort": "symbol", "asc": "1", "node": "hs_a"},
            )
            if r.status_code != 200:
                _raise_http(r.status_code, f"page {p}")
            try:
                body = r.json()
            except ValueError:
                raise ProviderError(f"sina page {p} invalid JSON") from None
            if not isinstance(body, list):
                raise ProviderError(f"sina page {p} response is not a list")
            rows = [parse_row(raw) for raw in body]
            if any(row is None for row in rows):
                raise ProviderError(f"sina page {p} raw/normalized identity invalid")
            raw_ids = tuple(raw["symbol"] for raw in body)
            identities = {(row["market"], row["symbol"]) for row in rows}
            if len(set(raw_ids)) != len(body) or len(identities) != len(body):
                raise ProviderError(f"sina page {p} duplicate identity")
            expected = min(page_size, total - (p - 1) * page_size)
            if len(body) != expected:
                error = _IncompletePage if len(body) < expected else ProviderError
                raise error(f"sina snapshot incomplete: page {p} raw={len(body)} normalized={len(rows)} "
                            f"expected_page={expected} expected={total}")
            remaining()
            return rows, raw_ids

        async def chunk(numbers):
            remaining()
            tasks = [asyncio.create_task(page(p), name=f"sina-market-page-{p}") for p in numbers]
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            # Inspect the entire completed wave before any retry. A 456 cannot
            # hide behind an earlier timeout, short list, or bad contract.
            for result in results:
                if isinstance(result, (SinaRateLimited, asyncio.CancelledError)):
                    raise result
            return dict(zip(numbers, results))

        def retryable(result):
            return isinstance(result, (_IncompletePage, httpx.TimeoutException,
                                       httpx.NetworkError, httpx.RemoteProtocolError))

        def reject(p, error):
            if isinstance(error, ProviderError):
                raise error
            raise ProviderError(f"sina page {p} fetch failed ({type(error).__name__})") from None

        completed = {}
        retries_left = concurrency  # One existing wave, at most six extra requests total.
        for chunk_start in range(1, pages + 1, concurrency):
            numbers = list(range(chunk_start, min(chunk_start + concurrency, pages + 1)))
            results = await chunk(numbers)
            for p, result in results.items():
                if isinstance(result, Exception) and not retryable(result):
                    reject(p, result)  # Denial/challenge/invalid identity never adds a read.
            failed = [p for p, result in results.items() if isinstance(result, Exception)]
            if len(failed) > retries_left:
                log.warning("sina snapshot repair allowance exhausted: pages=%s remaining=%s", failed, retries_left)
                reject(failed[0], results[failed[0]])
            if failed:
                remaining()
                retries_left -= len(failed)
                log.warning("sina snapshot page repair: pages=%s extra_requests=%s", failed, len(failed))
                repaired = await chunk(failed)
                errors = [(p, result) for p, result in repaired.items() if isinstance(result, Exception)]
                if errors:
                    # Keep a concrete transport/contract error visible even
                    # when an earlier page is merely short in the same wave.
                    p, result = next(((p, error) for p, error in errors if not isinstance(error, _IncompletePage)),
                                     errors[0])
                    reject(p, result)
                results.update(repaired)
            completed.update(results)
        rows = [row for p in range(1, pages + 1) for row in completed[p][0]]
        raw_ids = [identity for p in range(1, pages + 1) for identity in completed[p][1]]
        identities = {(row["market"], row["symbol"]) for row in rows}
        if len(raw_ids) != total or len(set(raw_ids)) != total or len(rows) != total or len(identities) != total:
            raise ProviderError(f"sina snapshot incomplete: rows={len(rows)} unique={len(identities)} expected={total}")
        remaining()
        return rows

    scope = asyncio.timeout(remaining())
    try:
        async with scope, httpx.AsyncClient(trust_env=False, timeout=timeout, headers=_HEADERS) as client:
            return await collect(client)
    except TimeoutError:
        if scope.expired():
            if observed_rate_limit is not None:
                raise observed_rate_limit from None
            raise SinaSnapshotDeadline("sina snapshot deadline exceeded") from None
        raise
