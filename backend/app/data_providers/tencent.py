"""腾讯行情 Provider（主源）。

端点（实测 2026-08-28，直连可用，Level-1 快照 3 秒级）：
- 实时快照/五档: qt.gtimg.cn/q=sh600519,sz000001   (GBK, ~ 分隔)
- K 线:          web.ifzq.gtimg.cn/appstock/app/fqkline/get (日/周, qfq 前复权)
- 分钟线:        ifzq.gtimg.cn/appstock/app/kline/mkline   (m1~m60)
- 搜索:          smartbox.gtimg.cn/s3/?v=2&q=...&t=all     (GBK)

快照字段（0 起）：1名称 3现价 4昨收 5今开 6量(手) 9-18买五档价量 19-28卖五档价量
30时间(北京) 31涨跌 32涨跌% 33最高 34最低 36量(手) 37额(万) 38换手%
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.data_providers.eastmoney import ProviderError
from app.schemas.market import (
    Kline,
    OrderBook,
    OrderBookLevel,
    Quote,
    SymbolSearchItem,
)

log = logging.getLogger(__name__)

SOURCE = "tencent"
_TZ_BJ = timezone(timedelta(hours=8))

INDEX_SECIDS = ["s_sh000001", "s_sz399001", "s_sz399006", "s_sh000688", "s_sh000300", "s_sh000852"]

_TIMEFRAME_PARAM = {
    "1m": "m1", "5m": "m5", "15m": "m15", "30m": "m30", "60m": "m60",
    "1d": ("day", "qfq"), "1w": ("week", "qfq"),
}

_ROW_RE = re.compile(r'v_(sh|sz|bj)(\d{6})="([^"]*)"')


def _num(v: str | None) -> float | None:
    if v is None or v.strip() in {"", "-"}:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _bj(v: str | None) -> datetime | None:
    n = _num(v)
    if n is None:
        return None
    try:
        return datetime.strptime(str(int(n)), "%Y%m%d%H%M%S").replace(tzinfo=_TZ_BJ).astimezone(timezone.utc)
    except ValueError:
        return None


def _price_raw(v: str | None) -> float | None:
    n = _num(v)
    return n


def to_tencent_symbol(symbol: str) -> str:
    s = symbol.strip()
    if s.startswith(("6", "9", "5")):
        return f"sh{s}"
    return f"sz{s}"


def parse_quote(prefix: str, fields: list[str]) -> Quote:
    volume_hands = _num(fields[36] if len(fields) > 36 else None)
    amount_wan = _num(fields[37] if len(fields) > 37 else None)
    q = Quote(
        symbol=fields[2],
        name=fields[1] or None,
        market=prefix.upper(),
        price=_num(fields[3]),
        prev_close=_num(fields[4]),
        open=_num(fields[5]),
        change=_num(fields[31] if len(fields) > 31 else None),
        change_pct=_num(fields[32] if len(fields) > 32 else None),
        high=_num(fields[33] if len(fields) > 33 else None),
        low=_num(fields[34] if len(fields) > 34 else None),
        volume=volume_hands * 100 if volume_hands is not None else None,
        amount=amount_wan * 1e4 if amount_wan is not None else None,
        turnover_rate=_num(fields[38] if len(fields) > 38 else None),
        data_timestamp=_bj(fields[30] if len(fields) > 30 else None),
        source=SOURCE,
    )
    # 指数等无成交场景 price 为 0/空时交给 Quality Validator 判定
    return q


def parse_order_book(symbol: str, fields: list[str]) -> OrderBook:
    def levels(start: int) -> list[OrderBookLevel]:
        out = []
        for i in range(start, min(start + 10, len(fields) - 1), 2):
            price = _num(fields[i])
            vol = _num(fields[i + 1])
            if price is None or price <= 0:
                continue
            out.append(OrderBookLevel(price=price, volume=vol * 100 if vol is not None else None))
        return out

    return OrderBook(
        symbol=symbol,
        bids=levels(9),
        asks=levels(19),
        data_timestamp=_bj(fields[30] if len(fields) > 30 else None),
        source=SOURCE,
    )


def parse_search_row(code_field: str, name: str) -> SymbolSearchItem | None:
    m = re.match(r"^(sh|sz|bj)(\d{6})$", code_field.strip().lower())
    if not m:
        return None
    return SymbolSearchItem(
        symbol=m.group(2),
        name=name,
        market=m.group(1).upper(),
        source=SOURCE,
    )


def parse_kline_payload(symbol: str, timeframe: str, payload: dict) -> list[Kline]:
    """解析 fqkline/mkline 响应。键规则：日/周线前复权为 qfqday/qfqweek（无则回退不复权），分钟线直取 m5 等。"""
    spec = _TIMEFRAME_PARAM.get(timeframe)
    if spec is None:
        raise ProviderError(f"tencent unsupported timeframe: {timeframe}")
    minute = not isinstance(spec, tuple)
    key = spec if minute else spec[0]
    node = (payload.get("data") or {}).get(to_tencent_symbol(symbol)) or {}
    if not isinstance(node, dict):
        raise ProviderError(f"tencent kline bad payload for {symbol}")
    rows = node.get(key) if minute else (node.get(f"qfq{key}") or node.get(key)) or []
    bars: list[Kline] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        ts_raw = str(row[0])
        if minute:
            try:
                ts = datetime.strptime(ts_raw, "%Y%m%d%H%M").replace(tzinfo=_TZ_BJ).astimezone(timezone.utc)
            except ValueError:
                continue
        else:
            try:
                ts = datetime.strptime(ts_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        vol_hand = _num(str(row[5]))
        bars.append(
            Kline(
                symbol=symbol,
                timeframe=timeframe,
                ts=ts,
                open=_num(str(row[1])),
                close=_num(str(row[2])),
                high=_num(str(row[3])),
                low=_num(str(row[4])),
                volume=vol_hand * 100 if vol_hand is not None else None,
                source=SOURCE,
            )
        )
    bars.sort(key=lambda b: b.ts)
    return bars


class TencentProvider:
    name = SOURCE
    realtime = True

    def __init__(self, timeout: float = 5.0):
        self._client = httpx.AsyncClient(
            trust_env=False,  # 行情源均为国内站，直连，不走用户系统代理
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _snapshot(self, symbols: list[str]) -> dict[str, tuple[str, list[str]]]:
        codes = ",".join(to_tencent_symbol(s) for s in symbols)
        resp = await self._client.get(f"https://qt.gtimg.cn/q={codes}")
        if resp.status_code != 200:
            raise ProviderError(f"tencent snapshot HTTP {resp.status_code}")
        text = resp.content.decode("gbk", errors="replace")
        found = _ROW_RE.findall(text)
        if not found:
            raise ProviderError("tencent snapshot empty reply")
        return {code: (prefix, fields) for prefix, code, raw in found if (fields := raw.split("~"))}

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        if not symbols:
            return []
        snap = await self._snapshot(symbols)
        quotes = []
        for s in symbols:
            entry = snap.get(s)
            if entry:
                quotes.append(parse_quote(entry[0], entry[1]))
        if not quotes:
            raise ProviderError("tencent snapshot no rows")
        return quotes

    async def get_quote(self, symbol: str) -> Quote | None:
        quotes = await self.get_quotes([symbol])
        return quotes[0] if quotes else None

    async def get_indices(self) -> list[Quote]:
        # 指数用全量快照（s_ 简版字段不同，复用同一解析）
        codes = ",".join(c.removeprefix("s_") for c in INDEX_SECIDS)
        resp = await self._client.get(f"https://qt.gtimg.cn/q={codes}")
        if resp.status_code != 200:
            raise ProviderError(f"tencent indices HTTP {resp.status_code}")
        text = resp.content.decode("gbk", errors="replace")
        quotes = []
        for prefix, code, raw in _ROW_RE.findall(text):
            fields = raw.split("~")
            q = parse_quote(prefix, fields)
            q.symbol = code
            quotes.append(q)
        if not quotes:
            raise ProviderError("tencent indices empty reply")
        return quotes

    async def get_kline(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Kline]:
        spec = _TIMEFRAME_PARAM.get(timeframe)
        if spec is None:
            raise ProviderError(f"tencent unsupported timeframe: {timeframe}")
        if isinstance(spec, tuple):  # day/week
            param = f"{to_tencent_symbol(symbol)},{spec[0]},,,320,{spec[1]}"
            url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        else:  # 分钟线 m1~m60
            param = f"{to_tencent_symbol(symbol)},{spec},,320"
            url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
        resp = await self._client.get(url, params={"param": param})
        if resp.status_code != 200:
            raise ProviderError(f"tencent kline HTTP {resp.status_code}")
        bars = parse_kline_payload(symbol, timeframe, resp.json())
        if start is not None:
            bars = [b for b in bars if b.ts >= start]
        if end is not None:
            bars = [b for b in bars if b.ts <= end]
        if not bars:
            raise ProviderError(f"tencent kline empty for {symbol} {timeframe}")
        return bars

    async def get_order_book(self, symbol: str) -> OrderBook | None:
        snap = await self._snapshot([symbol])
        entry = snap.get(symbol)
        if not entry:
            return None
        return parse_order_book(symbol, entry[1])

    async def get_trades(self, symbol: str) -> list:
        return []  # 腾讯免费逐笔无稳定端点；链上由东财 details 或同花顺分时补齐

    async def get_limit_up_pool(self, trade_date) -> list:
        return []  # 由链上东财 push2ex 提供

    async def get_longhu_records(self, trade_date) -> list:
        return []  # 由链上东财 datacenter 提供

    async def search(self, query: str) -> list[SymbolSearchItem]:
        resp = await self._client.get(
            "https://smartbox.gtimg.cn/s3/", params={"v": "2", "q": query, "t": "all"}
        )
        if resp.status_code != 200:
            raise ProviderError(f"tencent search HTTP {resp.status_code}")
        text = resp.content.decode("gbk", errors="replace")
        body = text.split('"', 1)
        if len(body) < 2:
            return []
        items = []
        for group in body[1].rsplit('"', 1)[0].split("^"):
            parts = group.split("~")
            if len(parts) >= 2:
                item = parse_search_row(parts[1], parts[0])
                if item:
                    items.append(item)
        return items
