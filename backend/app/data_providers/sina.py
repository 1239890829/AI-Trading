"""新浪行情 Provider（备源）。

hq.sinajs.cn/list=sh600519,sz000001（GBK，需 Referer）。
字段（0 起）：0名称 1今开 2昨收 3现价 4最高 5最低 6买一价 7卖一价 8量(股) 9额(元)
10-19 买五档量价交替 20-29 卖五档量价交替 30日期 31时间(北京)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.data_providers.eastmoney import ProviderError
from app.data_providers.tencent import to_tencent_symbol  # 同一 sh/sz 前缀规则
from app.schemas.market import OrderBook, OrderBookLevel, Quote, SymbolSearchItem

SOURCE = "sina"
_TZ_BJ = timezone(timedelta(hours=8))

_ROW = None


def _num(v: str | None) -> float | None:
    if v is None or not v.strip():
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _ts(date_s: str, time_s: str) -> datetime | None:
    try:
        return datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=_TZ_BJ).astimezone(timezone.utc)
    except ValueError:
        return None


class SinaProvider:
    name = SOURCE
    realtime = True

    def __init__(self, timeout: float = 5.0):
        self._client = httpx.AsyncClient(
            trust_env=False,  # 行情源均为国内站，直连，不走用户系统代理
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch(self, symbols: list[str]) -> dict[str, list[str]]:
        codes = ",".join(to_tencent_symbol(s) for s in symbols)
        resp = await self._client.get(f"https://hq.sinajs.cn/list={codes}")
        if resp.status_code != 200:
            raise ProviderError(f"sina HTTP {resp.status_code}")
        text = resp.content.decode("gbk", errors="replace")
        out: dict[str, list[str]] = {}
        for line in text.splitlines():
            if '="' not in line:
                continue
            key = line.split("=", 1)[0].strip().removeprefix("var hq_str_")
            body = line.split('="', 1)[1].rsplit('"', 1)[0]
            fields = body.split(",")
            if len(fields) >= 32:
                out[key.removeprefix("sh").removeprefix("sz").removeprefix("bj")] = fields
        if not out:
            raise ProviderError("sina empty reply")
        return out

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        if not symbols:
            return []
        rows = await self._fetch(symbols)
        quotes = []
        for s in symbols:
            f = rows.get(s)
            if not f:
                continue
            quotes.append(
                Quote(
                    symbol=s,
                    name=f[0] or None,
                    market="SH" if to_tencent_symbol(s).startswith("sh") else "SZ",
                    price=_num(f[3]),
                    open=_num(f[1]),
                    high=_num(f[4]),
                    low=_num(f[5]),
                    prev_close=_num(f[2]),
                    volume=_num(f[8]),  # 新浪已是股
                    amount=_num(f[9]),
                    change=(_num(f[3]) - _num(f[2])) if _num(f[3]) is not None and _num(f[2]) else None,
                    change_pct=(
                        round((_num(f[3]) - _num(f[2])) / _num(f[2]) * 100, 2)
                        if _num(f[3]) is not None and _num(f[2])
                        else None
                    ),
                    data_timestamp=_ts(f[30], f[31]),
                    source=SOURCE,
                )
            )
        if not quotes:
            raise ProviderError("sina no rows")
        return quotes

    async def get_quote(self, symbol: str) -> Quote | None:
        quotes = await self.get_quotes([symbol])
        return quotes[0] if quotes else None

    async def get_indices(self) -> list[Quote]:
        return await self.get_quotes(["000001", "399001", "399006", "000688", "000300", "000852"])

    async def get_order_book(self, symbol: str) -> OrderBook | None:
        rows = await self._fetch([symbol])
        f = rows.get(symbol)
        if not f:
            return None

        def levels(start: int) -> list[OrderBookLevel]:
            out = []
            for i in range(start, min(start + 10, 30), 2):
                vol = _num(f[i])
                price = _num(f[i + 1])
                if price is None or price <= 0:
                    continue
                out.append(OrderBookLevel(price=price, volume=vol))
            return out

        return OrderBook(
            symbol=symbol,
            bids=levels(10),
            asks=levels(20),
            data_timestamp=_ts(f[30], f[31]),
            source=SOURCE,
        )

    async def get_kline(self, *args, **kwargs) -> list:
        raise ProviderError("sina kline not implemented")

    async def get_trades(self, symbol: str) -> list:
        return []

    async def get_limit_up_pool(self, trade_date) -> list:
        return []

    async def get_longhu_records(self, trade_date) -> list:
        return []

    async def search(self, query: str) -> list[SymbolSearchItem]:
        return []

    async def get_board_rankings(self, board_type: str = "hangye") -> list[dict]:
        """行业(hangye)/概念(concept)板块排行，一次请求全量（新浪闪电排行）。"""
        param = {"hangye": "hangye", "concept": "class"}.get(board_type)
        if param is None:
            raise ProviderError(f"sina unknown board_type: {board_type}")
        resp = await self._client.get(f"https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param={param}")
        if resp.status_code != 200:
            raise ProviderError(f"sina boards HTTP {resp.status_code}")
        text = resp.content.decode("gbk", errors="replace")
        return parse_board_list(text, source=self.name)


def parse_board_list(text: str, source: str = SOURCE) -> list[dict]:
    """解析 S_Finance_bankuai_* JS 变量为板块排行列表。

    字段序（~分隔，实测 2026-08-29）：node,名称,成分股数,均价,价格变动,涨跌幅%,
    总量(股),总额(元),领涨股(含市场前缀),领涨停幅%,领涨股价,领涨涨跌,领涨股名
    """
    import json as _json
    import re as _re

    m = _re.search(r"=\s*(\{.*\})", text, _re.S)
    if not m:
        raise ProviderError("sina boards empty payload")
    data = _json.loads(m.group(1))
    out = []
    for raw in data.values():
        f = raw.split(",")
        if len(f) < 13:
            continue
        num = lambda v: float(v) if v not in ("", "--") else None  # noqa: E731
        leader = f[8]
        out.append({
            "name": f[1],
            "count": int(float(f[2])) if f[2] else None,
            "change_pct": num(f[5]),
            "volume": num(f[6]),
            "amount": num(f[7]),
            "leader_symbol": leader[2:] if len(leader) > 2 else None,
            "leader_name": f[12] if len(f) > 12 else None,
            "leader_change_pct": num(f[9]) if len(f) > 9 else None,
            "leader_price": num(f[10]) if len(f) > 10 else None,
            "source": source,
        })
    if not out:
        raise ProviderError("sina boards parsed empty")
    return out
