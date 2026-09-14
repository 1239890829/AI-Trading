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


def _raise_http(status: int, what: str) -> None:
    """把非 200 响应转为异常；**限流单独成类**，其余保持 `ProviderError`。"""
    if status == RATE_LIMIT_STATUS:
        raise SinaRateLimited(f"sina {what} HTTP {status}（WAF 限流）")
    raise ProviderError(f"sina {what} HTTP {status}")


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
        if resp.status_code != 200:
            _raise_http(resp.status_code, "stock count")
        if not resp.text.strip().strip('"').isdigit():
            raise ProviderError("sina stock count 响应不可解析（非数字）")
        total = int(resp.text.strip().strip('"'))
        pages = (total + page_size - 1) // page_size

        async def page(p: int) -> list[dict]:
            r = await client.get(
                f"{_BASE}/Market_Center.getHQNodeData",
                params={"page": p, "num": page_size, "sort": "symbol", "asc": "1", "node": "hs_a"},
            )
            if r.status_code != 200:
                _raise_http(r.status_code, f"page {p}")
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
                    if isinstance(item, SinaRateLimited):
                        # 限流必须**保留原类型**冒泡：调用方据此改用长冷却。
                        # 若在此处统一转成 ProviderError，最明确的限流信号就被
                        # 降级成「普通失败」，退避会退回常规节奏（2026-09-14 实测：
                        # 常规退避两轮（120s/240s）都仍在限流窗口内）。
                        raise item
                    raise ProviderError(f"sina page fetch failed: {item}")
                results.extend(item)
    rows = [r for r in (parse_row(x) for x in results) if r]
    if len(rows) < total * 0.9:
        raise ProviderError(f"sina snapshot incomplete: {len(rows)}/{total}")
    return rows
