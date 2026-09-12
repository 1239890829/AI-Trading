"""新浪全市场快照（Market Center API）。

实测 2026-08-28：node=hs_a 覆盖沪深京 A 股 5550 只，标准 JSON，字段：
symbol(sh600519)、name、trade(现价)、pricechange、changepercent、settlement(昨收)、
open/high/low、volume(股)、amount(元)、mktcap/nmc(万元)、turnoverratio、ticktime。
东财 clist 被限流后，这是全市场扫描的主源。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from app.data_providers.eastmoney import ProviderError

SOURCE = "sina_market"
_BASE = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_row(raw: dict) -> dict | None:
    sym = str(raw.get("symbol") or "")
    code = str(raw.get("code") or "")
    if len(code) != 6 or not code.isdigit():
        return None
    market = sym[:2].upper() if sym[:2] in {"sh", "sz", "bj"} else None
    if market is None:
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


async def fetch_market_snapshot(page_size: int = 100, concurrency: int = 6, timeout: float = 8.0) -> list[dict]:
    """抓取沪深京全市场快照，返回统一字段列表。"""
    async with httpx.AsyncClient(trust_env=False, timeout=timeout, headers=_HEADERS) as client:
        resp = await client.get(f"{_BASE}/Market_Center.getHQNodeStockCount", params={"node": "hs_a"})
        if resp.status_code != 200 or not resp.text.strip().strip('"').isdigit():
            raise ProviderError(f"sina stock count HTTP {resp.status_code}")
        total = int(resp.text.strip().strip('"'))
        pages = (total + page_size - 1) // page_size

        async def page(p: int) -> list[dict]:
            r = await client.get(
                f"{_BASE}/Market_Center.getHQNodeData",
                params={"page": p, "num": page_size, "sort": "symbol", "asc": "1", "node": "hs_a"},
            )
            if r.status_code != 200:
                raise ProviderError(f"sina page {p} HTTP {r.status_code}")
            body = r.json() if isinstance(r, object) else []
            return body if isinstance(body, list) else []

        results: list[dict] = []
        for chunk_start in range(1, pages + 1, concurrency):
            chunk = await asyncio.gather(
                *(page(p) for p in range(chunk_start, min(chunk_start + concurrency, pages + 1))),
                return_exceptions=True,
            )
            for item in chunk:
                if isinstance(item, Exception):
                    raise ProviderError(f"sina page fetch failed: {item}")
                results.extend(item)
    rows = [r for r in (parse_row(x) for x in results) if r]
    if len(rows) < total * 0.9:
        raise ProviderError(f"sina snapshot incomplete: {len(rows)}/{total}")
    return rows
