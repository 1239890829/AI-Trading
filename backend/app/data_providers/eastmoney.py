from __future__ import annotations

import logging
from datetime import date, datetime

import httpx

from app.market import normalizer as nz
from app.schemas.market import (
    Kline,
    LimitUpRecord,
    LongHuRecord,
    OrderBook,
    Quote,
    SymbolSearchItem,
    Trade,
)

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 指数 secid：沪 1.、深 0.
INDEX_SECIDS = [
    ("1.000001", "上证指数"),
    ("0.399001", "深证成指"),
    ("0.399006", "创业板指"),
    ("1.000688", "科创50"),
    ("1.000300", "沪深300"),
    ("1.000852", "中证1000"),
]

TIMEFRAME_KLT = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "1d": 101, "1w": 102}

QUOTE_FIELDS = "f12,f13,f14,f2,f3,f4,f5,f6,f8,f15,f16,f17,f18,f124"
ORDERBOOK_FIELDS = (
    "f43,f57,f58,f86,"
    "f31,f32,f33,f34,f35,f36,f37,f38,f39,f40,"
    "f11,f12,f13,f14,f15,f16,f17,f18,f19,f20"
)


class ProviderError(RuntimeError):
    """数据源请求失败。调用方必须停止将其当作实时数据使用。"""


def to_secid(symbol: str) -> str:
    s = symbol.strip()
    if not s:
        raise ProviderError("empty symbol")
    if s.startswith(("6", "9", "5")):
        return f"1.{s}"
    return f"0.{s}"


class EastmoneyProvider:
    """东方财富免费行情接口。免费接口无契约，字段可能漂移，失败必须降级处理。"""

    name = "eastmoney"
    realtime = True

    def __init__(self, timeout: float = 5.0):
        self._client = httpx.AsyncClient(
            trust_env=False,  # 行情源均为国内站，直连，不走用户系统代理
            timeout=timeout,
            headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, url: str, params: dict) -> dict:
        try:
            resp = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ProviderError(f"HTTP {resp.status_code} from {url}")
        body = resp.content.strip()
        if not body:
            raise ProviderError(f"empty reply from {url} (可能被限流)")
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(f"invalid JSON from {url}") from exc

    async def _ulist(self, secids: list[str], fields: str) -> list[dict]:
        payload = await self._get_json(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            {"fltt": "2", "invt": "2", "fields": fields, "secids": ",".join(secids)},
        )
        data = payload.get("data") or {}
        diff = data.get("diff") or []
        return diff

    async def get_indices(self) -> list[Quote]:
        secids = [sid for sid, _ in INDEX_SECIDS]
        rows = await self._ulist(secids, "f12,f13,f14,f2,f3,f4,f6,f124")
        by_code = {r.get("f12"): r for r in rows}
        quotes: list[Quote] = []
        for sid, fallback_name in INDEX_SECIDS:
            raw = by_code.get(sid.split(".", 1)[1]) or {"f12": sid.split(".", 1)[1], "f14": fallback_name}
            quotes.append(nz.normalize_index(raw))
        return quotes

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        if not symbols:
            return []
        rows = await self._ulist([to_secid(s) for s in symbols], QUOTE_FIELDS)
        return [nz.normalize_quote(r) for r in rows if r.get("f12")]

    async def get_quote(self, symbol: str) -> Quote | None:
        quotes = await self.get_quotes([symbol])
        return quotes[0] if quotes else None

    async def get_kline(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Kline]:
        klt = TIMEFRAME_KLT.get(timeframe)
        if klt is None:
            raise ProviderError(f"unsupported timeframe: {timeframe}")
        payload = await self._get_json(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            {
                "secid": to_secid(symbol),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": str(klt),
                "fqt": "1",  # 前复权
                "end": "20500101",
                "lmt": "1000",
            },
        )
        klines_data = (payload.get("data") or {}).get("klines") or []
        bars = [nz.normalize_kline_row(symbol, timeframe, row) for row in klines_data]
        bars = [b for b in bars if b is not None]
        if start is not None:
            bars = [b for b in bars if b.ts >= start]
        if end is not None:
            bars = [b for b in bars if b.ts <= end]
        return bars

    async def get_order_book(self, symbol: str) -> OrderBook | None:
        payload = await self._get_json(
            "https://push2.eastmoney.com/api/qt/stock/get",
            {"fltt": "2", "invt": "2", "fields": ORDERBOOK_FIELDS, "secid": to_secid(symbol)},
        )
        raw = payload.get("data")
        if not raw:
            return None
        return nz.normalize_order_book(symbol, raw)

    async def get_trades(self, symbol: str) -> list[Trade]:
        payload = await self._get_json(
            "https://push2his.eastmoney.com/api/qt/stock/details/get",
            {
                "secid": to_secid(symbol),
                "fields1": "f1,f2,f3,f4,f5",
                "fields2": "f51,f52,f53,f54,f55",
                "pos": "-100",
            },
        )
        details = (payload.get("data") or {}).get("details") or []
        trades = [nz.normalize_trade(symbol, row) for row in details]
        return [t for t in trades if t is not None]

    async def get_limit_up_pool(self, trade_date: date) -> list[LimitUpRecord]:
        payload = await self._get_json(
            "https://push2ex.eastmoney.com/getTopicZTPool",
            {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": "0",
                "pagesize": "500",
                "sort": "fbt:asc",
                "date": trade_date.strftime("%Y%m%d"),
            },
        )
        pool = (payload.get("data") or {}).get("pool") or []
        records = [nz.normalize_limit_up(r, trade_date) for r in pool]
        return [r for r in records if r is not None]

    async def get_longhu_records(self, trade_date: date) -> list[LongHuRecord]:
        payload = await self._get_json(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            {
                "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
                "columns": "ALL",
                "filter": f"(TRADE_DATE='{trade_date.isoformat()}')",
                "pagesize": "500",
                "pageno": "1",
                "sort": "BILLBOARD_NET_AMT",
                "order": "desc",
                "source": "WEB",
                "client": "WEB",
            },
        )
        result = payload.get("result") or {}
        rows = result.get("data") or []
        records = [nz.normalize_longhu(r) for r in rows]
        return [r for r in records if r is not None]

    async def search(self, query: str) -> list[SymbolSearchItem]:
        payload = await self._get_json(
            "https://searchapi.eastmoney.com/api/suggest/get",
            {"input": query, "type": "14", "token": "D43BF722C8E33BDC906FB84D85E326E8", "count": "10"},
        )
        table = (payload.get("QuotationCodeTable") or {})
        items = []
        for raw in table.get("Data") or []:
            parsed = nz.normalize_search(raw)
            if parsed is None:
                continue
            code, name, market = parsed
            items.append(SymbolSearchItem(symbol=code, name=name, market=market, source=self.name))
        return items
