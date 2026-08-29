"""同花顺金融数据服务 Provider（官方 fuyao API，https://fuyao.aicubes.cn）。

- 认证：X-api-key（key 只放 .env，禁止入库入前端）
- thscode 格式：600519.SH / 000001.SZ
- 能力：行情快照、交易日历、涨停/跌停/炸板池、连板天梯、龙虎榜（含概念标签）、
  财务三表、估值、集合竞价、异动、热榜、全市场导出
- 边界：不含 L2 十档/逐笔/分钟K（官方 capability-map 声明）
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import httpx

from app.data_providers.eastmoney import ProviderError
from app.schemas.market import LimitUpRecord, LongHuRecord, Quote

log = logging.getLogger(__name__)

SOURCE = "ths"
_TZ_SH = timezone(timedelta(hours=8))


def to_thscode(symbol: str) -> str:
    s = symbol.strip()
    if s.startswith(("6", "9", "5")):
        return f"{s}.SH"
    return f"{s}.SZ"


def from_thscode(thscode: str) -> str:
    return thscode.split(".")[0]


def date_ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=_TZ_SH).timestamp() * 1000)


class ThsFuyaoProvider:
    name = "ths"
    realtime = True

    def __init__(self, api_key: str, base_url: str = "https://fuyao.aicubes.cn", timeout: float = 8.0):
        if not api_key:
            raise ProviderError("ths api_key missing")
        self._client = httpx.AsyncClient(
            trust_env=False, timeout=timeout,
            headers={"X-api-key": api_key, "Accept": "application/json"},
        )
        self._base = base_url.rstrip("/")
        self._names: dict[str, str] = {}
        self._names_ts: float = 0.0

    async def _ensure_names(self, symbols: list[str]) -> None:
        """官方代码表 → {code: name} 映射，24h 缓存；失败不阻塞行情。"""
        import time as _time

        if self._names and _time.time() - self._names_ts < 86400:
            return
        if all(s in self._names for s in symbols):
            return
        try:
            names: dict[str, str] = {}
            offset = 0
            while True:
                data = await self._get(
                    "/api/meta/tickers/list",
                    {"exchange": "SH,SZ", "asset_type": "a-share", "limit": 10000, "offset": offset},
                )
                items = data.get("item") or []
                for it in items:
                    code = from_thscode(str(it.get("thscode") or ""))
                    if len(code) == 6 and it.get("name"):
                        names[code] = it["name"]
                if len(items) < 10000:
                    break
                offset += 10000
            if names:
                self._names = names
                self._names_ts = _time.time()
        except Exception as exc:
            log.warning("ths tickers/list failed: %s", exc)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict | None = None):
        resp = await self._client.get(f"{self._base}{path}", params=params)
        if resp.status_code != 200:
            raise ProviderError(f"ths {path} HTTP {resp.status_code}")
        payload = resp.json()
        if payload.get("code") != 0:
            raise ProviderError(f"ths {path} code={payload.get('code')} {payload.get('message')}")
        return payload.get("data") or {}

    # ---- 行情 ----

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        if not symbols:
            return []
        data = await self._get("/api/a-share/prices/snapshot", {"thscodes": ",".join(to_thscode(s) for s in symbols)})
        items = data.get("item") or []
        await self._ensure_names(symbols)
        quotes = [self._parse_quote(it) for it in items]
        quotes = [q for q in quotes if q is not None]
        for q in quotes:
            if q.name is None:
                q.name = self._names.get(q.symbol)
        if not quotes:
            raise ProviderError("ths snapshot empty")
        return quotes

    async def get_quote(self, symbol: str) -> Quote | None:
        quotes = await self.get_quotes([symbol])
        return quotes[0] if quotes else None

    def _parse_quote(self, it: dict) -> Quote | None:
        code = from_thscode(str(it.get("thscode") or ""))
        if len(code) != 6:
            return None
        num = lambda v: float(v) if v is not None else None  # noqa: E731
        ts_ms = num(it.get("timestamp")) if "timestamp" in it else None
        return Quote(
            symbol=code,
            name=it.get("name"),
            market="SH" if it.get("thscode", "").endswith(".SH") else "SZ",
            price=num(it.get("last_price")),
            open=num(it.get("open_price")),
            high=num(it.get("high_price")),
            low=num(it.get("low_price")),
            prev_close=num(it.get("prev_price")),
            change=num(it.get("price_change")),
            change_pct=round(num(it.get("price_change_ratio_pct")) or 0, 2),
            volume=num(it.get("volume")),
            amount=num(it.get("turnover")),
            data_timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else None,
            source=SOURCE,
        )

    INDEX_NAMES = {
        "000001.SH": "上证指数", "399001.SZ": "深证成指", "399006.SZ": "创业板指",
        "000688.SH": "科创50", "000300.SH": "沪深300", "000852.SH": "中证1000",
    }

    async def get_indices(self) -> list[Quote]:
        data = await self._get(
            "/api/a-share/prices/snapshot",
            {"thscodes": ",".join(self.INDEX_NAMES)},
        )
        quotes = []
        for it in data.get("item") or []:
            q = self._parse_quote(it)
            if q is None:
                continue
            q.name = self.INDEX_NAMES.get(it.get("thscode"), q.name)
            quotes.append(q)
        if not quotes:
            raise ProviderError("ths indices empty")
        return quotes

    # ---- 特殊数据 ----

    async def get_limit_up_pool(self, trade_date: date) -> list[LimitUpRecord]:
        data = await self._get(
            "/api/a-share/special-data/limit-up-pool",
            {"date_ms": date_ms(trade_date), "page": 1, "size": 200, "sort_field": "continue_day_cnt"},
        )
        out = []
        for it in data.get("item") or []:
            code = from_thscode(str(it.get("thscode") or ""))
            if len(code) != 6:
                continue
            num = lambda v: float(v) if v is not None else None  # noqa: E731
            out.append(
                LimitUpRecord(
                    symbol=code,
                    name=it.get("name"),
                    trade_date=trade_date,
                    price=num(it.get("last_price")),
                    change_pct=num(it.get("price_change_ratio_pct")),
                    first_seal_time=str(it.get("limit_up_time") or "") or None,
                    seal_amount=num(it.get("seal_money")),
                    turnover_rate=num(it.get("turnover_rate")),
                    consecutive_boards=int(num(it.get("continue_day_cnt")) or 1),
                    boards_stat=str(it.get("continue_day_text") or "") or None,
                    reason=it.get("limit_up_reason"),
                    source=SOURCE,
                )
            )
        if not out:
            raise ProviderError(f"ths limit-up-pool empty for {trade_date}")
        return out

    async def get_limit_break_pool(self, trade_date: date) -> list[LimitUpRecord]:
        """炸板池（涨停后开板未回封）。"""
        data = await self._get(
            "/api/a-share/special-data/limit-break-pool",
            {"date_ms": date_ms(trade_date), "page": 1, "size": 200},
        )
        out = []
        for it in data.get("item") or []:
            code = from_thscode(str(it.get("thscode") or ""))
            if len(code) != 6:
                continue
            num = lambda v: float(v) if v is not None else None  # noqa: E731
            out.append(
                LimitUpRecord(
                    symbol=code, name=it.get("name"), trade_date=trade_date,
                    price=num(it.get("last_price")), change_pct=num(it.get("price_change_ratio_pct")),
                    break_count=int(num(it.get("open_times")) or 0) if it.get("open_times") is not None else None,
                    turnover_rate=num(it.get("turnover_ratio_pct")),
                    amount=num(it.get("turnover")),
                    source=SOURCE,
                )
            )
        return out

    async def get_longhu_records(self, trade_date: date) -> list[LongHuRecord]:
        data = await self._get(
            "/api/a-share/special-data/dragon-tiger-list",
            {"date_ms": date_ms(trade_date), "size": 200},
        )
        out = []
        for it in data.get("stock_items") or []:
            code = from_thscode(str(it.get("thscode") or ""))
            if len(code) != 6:
                continue
            num = lambda v: float(v) if v is not None else None  # noqa: E731
            concepts = ",".join(c.get("name", "") for c in (it.get("concept_list") or [])[:5]) or None
            out.append(
                LongHuRecord(
                    symbol=code,
                    name=it.get("name"),
                    trade_date=trade_date,
                    close=num(it.get("last_price")),
                    change_pct=num(it.get("price_change_ratio_pct")),
                    amount=num(it.get("turnover")),
                    net_buy=num(it.get("net_buy")),
                    buy_amount=num(it.get("buy_amount")),
                    sell_amount=num(it.get("sell_amount")),
                    reason=it.get("reason") or concepts,
                    source=SOURCE,
                )
            )
        if not out:
            raise ProviderError(f"ths dragon-tiger empty for {trade_date}")
        return out

    async def get_trading_days(self) -> list[str]:
        """近一年交易日（YYYYMMDD 升序）。"""
        data = await self._get("/api/a-share/calendar/trading-days")
        return [str(it.get("date")) for it in data.get("item") or []]

    # ---- 协议其余方法：链上由其他 Provider 负责 ----

    async def get_kline(self, *args, **kwargs) -> list:
        raise ProviderError("ths kline via fuyao 未在本轮接入（腾讯已覆盖）")

    async def get_order_book(self, symbol: str):
        return None  # fuyao 无五档盘口，链上由腾讯提供

    async def get_trades(self, symbol: str) -> list:
        return []

    async def search(self, query: str) -> list:
        return []
