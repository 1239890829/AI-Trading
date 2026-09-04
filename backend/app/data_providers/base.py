from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from app.schemas.market import (
    Kline,
    LimitDownRecord,
    LimitUpRecord,
    LongHuRecord,
    OrderBook,
    Quote,
    SymbolSearchItem,
    Trade,
)


@runtime_checkable
class MarketDataProvider(Protocol):
    """统一 Provider 协议（full.md §2.2）。

    所有 Provider 返回的数据都必须带 source / quality / received_at 审计字段，
    数据失败时返回 None 或空列表，不得伪造实时数据。
    """

    name: str

    async def get_indices(self) -> list[Quote]: ...

    async def get_quote(self, symbol: str) -> Quote | None: ...

    async def get_quotes(self, symbols: list[str]) -> list[Quote]: ...

    async def get_kline(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Kline]: ...

    async def get_order_book(self, symbol: str) -> OrderBook | None: ...

    async def get_trades(self, symbol: str) -> list[Trade]: ...

    async def get_limit_up_pool(self, trade_date: date) -> list[LimitUpRecord]: ...

    async def get_limit_down_pool(self, trade_date: date) -> list[LimitDownRecord]: ...

    async def get_longhu_records(self, trade_date: date) -> list[LongHuRecord]: ...

    async def search(self, query: str) -> list[SymbolSearchItem]: ...

    async def aclose(self) -> None: ...
