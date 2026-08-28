"""Mock Provider：确定性演示数据，source 一律标记为 "mock"，绝不冒充实盘。

随机种子由 (symbol, 日期, 分钟) 派生：同分钟内多次请求结果一致，
分钟变化时价格小幅随机游走（步幅远小于涨跌停限制），便于演示 WS 推送与质量校验。
"""
from __future__ import annotations

import random
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from app.schemas.market import (
    Kline,
    LimitUpRecord,
    LongHuRecord,
    OrderBook,
    OrderBookLevel,
    Quote,
    SymbolSearchItem,
    Trade,
)

SOURCE = "mock"

UNIVERSE: list[tuple[str, str, str]] = [
    ("600519", "贵州茅台", "SH"),
    ("000001", "平安银行", "SZ"),
    ("300750", "宁德时代", "SZ"),
    ("601318", "中国平安", "SH"),
    ("000858", "五粮液", "SZ"),
    ("002594", "比亚迪", "SZ"),
    ("688981", "中芯国际", "SH"),
    ("601127", "赛力斯", "SH"),
    ("600036", "招商银行", "SH"),
    ("300059", "东方财富", "SZ"),
    ("601899", "紫金矿业", "SH"),
    ("000333", "美的集团", "SZ"),
    ("002230", "科大讯飞", "SZ"),
    ("688041", "海光信息", "SH"),
    ("603259", "药明康德", "SH"),
    ("002475", "立讯精密", "SZ"),
]

INDEX_BASES: list[tuple[str, str, str, float]] = [
    ("000001", "SH", "上证指数", 3300.0),
    ("399001", "SZ", "深证成指", 10500.0),
    ("399006", "SZ", "创业板指", 2150.0),
    ("000688", "SH", "科创50", 950.0),
    ("000300", "SH", "沪深300", 3900.0),
    ("000852", "SH", "中证1000", 5600.0),
]


def _seed(*parts) -> int:
    material = "|".join(str(p) for p in parts)
    return int.from_bytes(material.encode("utf-8")[:32], "big") & 0xFFFFFFFF


def _now_minute_key(now: datetime) -> tuple[date, int]:
    return now.date(), now.hour * 60 + now.minute


def _base_price(symbol: str) -> float:
    n = int(symbol) if symbol.isdigit() else abs(hash(symbol))
    return round(5 + (n % 97) + (n % 89) / 10, 2)


def _quote_for(symbol: str, name: str | None, market: str | None, ts: datetime, rnd: random.Random) -> Quote:
    base = _base_price(symbol)
    prev_close = round(base * (1 + rnd.uniform(-0.02, 0.02)), 2)
    price = round(prev_close * (1 + rnd.uniform(-0.008, 0.008)), 2)
    change_pct = round((price / prev_close - 1) * 100, 2)
    change = round(price - prev_close, 2)
    open_ = round(prev_close * (1 + rnd.uniform(-0.005, 0.005)), 2)
    high = round(max(open_, price) * (1 + rnd.uniform(0, 0.004)), 2)
    low = round(min(open_, price) * (1 - rnd.uniform(0, 0.004)), 2)
    volume_hands = rnd.randint(50_000, 5_000_000)
    return Quote(
        symbol=symbol,
        name=name,
        market=market,
        price=price,
        open=open_,
        high=high,
        low=low,
        prev_close=prev_close,
        change=change,
        change_pct=change_pct,
        volume=volume_hands * 100,
        amount=round(volume_hands * 100 * price, 2),
        turnover_rate=round(rnd.uniform(0.3, 8.0), 2),
        data_timestamp=ts,
        source=SOURCE,
    )


class MockProvider:
    name = "mock"
    realtime = False

    def __init__(self, clock: Callable[[], datetime] = datetime.now):
        self._clock = clock

    async def aclose(self) -> None:
        return None

    async def get_indices(self) -> list[Quote]:
        d, minute = _now_minute_key(self._clock())
        ts = datetime.now(timezone.utc)
        quotes = []
        for symbol, market, name, base in INDEX_BASES:
            rnd = random.Random(_seed("idx", symbol, d, minute))
            price = round(base * (1 + rnd.uniform(-0.015, 0.015)), 2)
            prev = round(base * (1 + rnd.uniform(-0.01, 0.01)), 2)
            quotes.append(
                Quote(
                    symbol=symbol,
                    name=name,
                    market=market,
                    price=price,
                    change=round(price - prev, 2),
                    change_pct=round((price / prev - 1) * 100, 2),
                    amount=round(base * rnd.uniform(2.0e8, 4.0e8), 2),
                    prev_close=prev,
                    data_timestamp=ts,
                    source=SOURCE,
                )
            )
        return quotes

    async def get_quote(self, symbol: str) -> Quote | None:
        quotes = await self.get_quotes([symbol])
        return quotes[0] if quotes else None

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        d, minute = _now_minute_key(self._clock())
        ts = datetime.now(timezone.utc)
        known = {code: (name, mkt) for code, name, mkt in UNIVERSE}
        quotes = []
        for symbol in symbols:
            name, market = known.get(symbol, (None, "SH" if symbol.startswith(("6", "9", "5")) else "SZ"))
            rnd = random.Random(_seed("q", symbol, d, minute))
            quotes.append(_quote_for(symbol, name, market, ts, rnd))
        return quotes

    async def get_kline(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Kline]:
        if timeframe not in {"1d", "1w"}:
            raise ValueError(f"mock kline 仅支持 1d/1w，收到 {timeframe}")
        limit = 250
        today = self._clock().date()
        base = _base_price(symbol)
        rnd = random.Random(_seed("k", symbol, today))
        closes: list[float] = []
        p = base
        for _ in range(limit):
            p = max(0.5, p * (1 + rnd.uniform(-0.02, 0.02)))
            closes.append(round(p, 2))
        bars: list[Kline] = []
        day = today
        i = limit - 1
        while len(bars) < limit and i >= 0:
            if day.weekday() < 5:  # 跳过周末
                close = closes[i]
                prev_close = closes[i - 1] if i > 0 else base
                open_ = round(prev_close * (1 + rnd.uniform(-0.005, 0.005)), 2)
                high = round(max(open_, close) * (1 + rnd.uniform(0, 0.01)), 2)
                low = round(min(open_, close) * (1 - rnd.uniform(0, 0.01)), 2)
                volume_hands = rnd.randint(100_000, 3_000_000)
                bars.append(
                    Kline(
                        symbol=symbol,
                        timeframe=timeframe,
                        ts=datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
                        open=open_,
                        high=high,
                        low=low,
                        close=close,
                        volume=volume_hands * 100,
                        amount=round(volume_hands * 100 * close, 2),
                        change_pct=round((close / prev_close - 1) * 100, 2),
                        turnover_rate=round(rnd.uniform(0.5, 6.0), 2),
                        source=SOURCE,
                    )
                )
                i -= 1
            day -= timedelta(days=1)
        bars.sort(key=lambda b: b.ts)
        if start is not None:
            bars = [b for b in bars if b.ts >= start]
        if end is not None:
            bars = [b for b in bars if b.ts <= end]
        return bars

    async def get_order_book(self, symbol: str) -> OrderBook | None:
        q = await self.get_quote(symbol)
        assert q is not None and q.price is not None
        rnd = random.Random(_seed("ob", symbol, *_now_minute_key(self._clock())))
        spread = max(round(q.price * 0.001, 2), 0.01)
        bids = [
            OrderBookLevel(price=round(q.price - spread * (i + 1), 2), volume=rnd.randint(10, 900) * 100)
            for i in range(5)
        ]
        asks = [
            OrderBookLevel(price=round(q.price + spread * (i + 1), 2), volume=rnd.randint(10, 900) * 100)
            for i in range(5)
        ]
        return OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            data_timestamp=q.data_timestamp,
            source=SOURCE,
        )

    async def get_trades(self, symbol: str) -> list[Trade]:
        q = await self.get_quote(symbol)
        assert q is not None and q.price is not None
        rnd = random.Random(_seed("t", symbol, *_now_minute_key(self._clock())))
        now = datetime.now(timezone.utc)
        trades = []
        price = q.price
        for i in range(30):
            price = round(price * (1 + rnd.uniform(-0.002, 0.002)), 2)
            side = rnd.choice(["buy", "sell", "neutral"])
            trades.append(
                Trade(
                    symbol=symbol,
                    ts=now - timedelta(seconds=(30 - i) * 3),
                    price=price,
                    volume=rnd.randint(1, 500) * 100,
                    side=side,
                    source=SOURCE,
                )
            )
        return trades

    async def get_limit_up_pool(self, trade_date: date) -> list[LimitUpRecord]:
        rnd = random.Random(_seed("zt", trade_date))
        records = []
        for idx, (code, name, mkt) in enumerate(UNIVERSE[:6]):
            limit_pct = 20.0 if code.startswith(("300", "688")) else 10.0
            price = round(_base_price(code) * (1 + limit_pct / 100), 2)
            first = rnd.randint(9 * 3600 + 2500, 14 * 3600 + 3000)
            records.append(
                LimitUpRecord(
                    symbol=code,
                    name=name,
                    trade_date=trade_date,
                    price=price,
                    change_pct=limit_pct,
                    first_seal_time=f"{first // 3600:02d}:{first % 3600 // 60:02d}:{first % 60:02d}",
                    last_seal_time=f"{rnd.randint(9, 14):02d}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}",
                    break_count=rnd.randint(0, 2),
                    seal_amount=rnd.randint(1, 30) * 1e7,
                    turnover_rate=round(rnd.uniform(1, 20), 2),
                    consecutive_boards=rnd.randint(1, 3),
                    boards_stat=f"{rnd.randint(1, 3)}天{rnd.randint(1, 2)}板",
                    source=SOURCE,
                )
            )
        return records

    async def get_longhu_records(self, trade_date: date) -> list[LongHuRecord]:
        rnd = random.Random(_seed("lh", trade_date))
        records = []
        for code, name, _mkt in UNIVERSE[:5]:
            net = rnd.randint(-3, 8) * 1e7
            records.append(
                LongHuRecord(
                    symbol=code,
                    name=name,
                    trade_date=trade_date,
                    close=round(_base_price(code) * (1 + rnd.uniform(-0.05, 0.08)), 2),
                    change_pct=round(rnd.uniform(-9.5, 10.0), 2),
                    turnover_rate=round(rnd.uniform(2, 25), 2),
                    amount=round(rnd.uniform(2, 15) * 1e8, 2),
                    net_buy=net,
                    buy_amount=abs(net) + rnd.randint(1, 5) * 1e7,
                    sell_amount=abs(net) + rnd.randint(0, 4) * 1e7,
                    reason="日涨幅偏离值达到7%的前五只证券",
                    source=SOURCE,
                )
            )
        return records

    async def search(self, query: str) -> list[SymbolSearchItem]:
        q = query.strip()
        if not q:
            return []
        items = []
        for code, name, market in UNIVERSE:
            if q in code or (name and q in name):
                items.append(SymbolSearchItem(symbol=code, name=name, market=market, source=SOURCE))
        return items[:10]
