"""Provider 链：主源失败自动切换备源（full.md §2.2 自动降级）。

- 逐方法 failover：行情走腾讯→新浪→东财，涨停池/龙虎榜走东财。
- 切换记录 switch_log（数据源切换日志），供 /api/health 展示。
- 全链失败抛 ProviderError → QuoteHub 标记 stale，绝不伪造实时数据。
"""
from __future__ import annotations

import time

import logging
from datetime import date, datetime

from app.data_providers.eastmoney import ProviderError

log = logging.getLogger(__name__)

#: 连续失败多少次后熔断（冷却期内不再请求该源的该方法）
FAILURE_THRESHOLD = 3
#: 熔断冷却时长（秒）
COOLDOWN_SECONDS = 60.0

_ROUTED = (
    "get_indices", "get_quotes", "get_quote", "get_kline", "get_order_book",
    "get_trades", "get_limit_up_pool", "get_longhu_records", "search", "get_board_rankings",
    "get_longhu_detail", "get_longhu_history", "get_capital_flow", "get_financials",
    "get_limit_break_pool", "get_trading_days", "get_announcements", "get_news", "get_company_profile",
)


class CompositeProvider:
    realtime = True

    def __init__(self, providers: list):
        if not providers:
            raise ValueError("CompositeProvider needs at least one provider")
        self.providers = providers
        self.switch_log: list[str] = []
        self._last_good: dict[str, str] = {}
        self._failures: dict[tuple[str, str], int] = {}   # (method, provider) → 连续失败次数
        self._cooldown_until: dict[tuple[str, str], float] = {}  # (method, provider) → 解禁时间戳

    # ---- 熔断（circuit breaker）----

    def _in_cooldown(self, method: str, provider: str) -> bool:
        until = self._cooldown_until.get((method, provider))
        return until is not None and time.monotonic() < until

    def _cooldown_left(self, method: str, provider: str) -> float:
        until = self._cooldown_until.get((method, provider)) or 0.0
        return max(0.0, until - time.monotonic())

    def _record_failure(self, method: str, provider: str) -> None:
        key = (method, provider)
        n = self._failures.get(key, 0) + 1
        self._failures[key] = n
        if n >= FAILURE_THRESHOLD:
            self._cooldown_until[key] = time.monotonic() + COOLDOWN_SECONDS
            log.warning(
                "provider %s 的 %s 连续失败 %d 次 → 熔断 %.0fs", provider, method, n, COOLDOWN_SECONDS
            )

    def _record_success(self, method: str, provider: str) -> None:
        self._failures.pop((method, provider), None)
        self._cooldown_until.pop((method, provider), None)

    def breaker_state(self) -> dict:
        """熔断状态快照（供 /api/system 观测：哪些源被判死了）。"""
        return {
            f"{m}@{p}": {"failures": n, "cooldown_left": round(self._cooldown_left(m, p), 1)}
            for (m, p), n in self._failures.items()
        }

    @property
    def name(self) -> str:
        return "chain(" + "→".join(p.name for p in self.providers) + ")"

    async def aclose(self) -> None:
        for p in self.providers:
            await p.aclose()

    def _pick(self, method: str):
        return [p for p in self.providers if hasattr(p, method)]

    async def _call(self, method: str, *args):
        errors: list[str] = []
        for p in self._pick(method):
            # 熔断：该源在此方法上连续失败过多 → 冷却期内直接跳过，
            # 不再把请求打给一个已知挂掉的源（雪崩时尤其重要：
            # 2026-08-31 腾讯 WAF 封禁期间，每个 K 线请求都在往墙上撞）
            if self._in_cooldown(method, p.name):
                errors.append(f"{p.name}: 熔断冷却中（{self._cooldown_left(method, p.name):.0f}s）")
                continue
            try:
                result = await getattr(p, method)(*args)
            except Exception as exc:
                errors.append(f"{p.name}: {exc}")
                log.warning("provider %s %s failed: %s", p.name, method, exc)
                self._record_failure(method, p.name)
                continue
            if result is None or (isinstance(result, (list, tuple)) and len(result) == 0):
                errors.append(f"{p.name}: empty")
                # 空结果同样计入失败：K 线/池子返回空往往是源已异常的前兆
                self._record_failure(method, p.name)
                continue
            self._record_success(method, p.name)
            if self._last_good.get(method) != p.name:
                if method in self._last_good:
                    msg = f"{method}: {self._last_good[method]} -> {p.name}"
                    self.switch_log.append(msg)
                    log.info("provider switched: %s", msg)
                self._last_good[method] = p.name
            return result
        raise ProviderError(f"all providers failed for {method}: " + "; ".join(errors))

    async def get_indices(self) -> list:
        return await self._call("get_indices")

    async def get_quotes(self, symbols: list[str]) -> list:
        return await self._call("get_quotes", symbols)

    async def get_quote(self, symbol: str):
        quotes = await self.get_quotes([symbol])
        return quotes[0] if quotes else None

    async def get_kline(self, symbol: str, timeframe: str, start: datetime | None = None, end: datetime | None = None) -> list:
        return await self._call("get_kline", symbol, timeframe, start, end)

    async def get_order_book(self, symbol: str):
        return await self._call("get_order_book", symbol)

    async def get_trades(self, symbol: str) -> list:
        return await self._call("get_trades", symbol)

    async def get_limit_up_pool(self, trade_date: date) -> list:
        return await self._call("get_limit_up_pool", trade_date)

    async def get_longhu_records(self, trade_date: date) -> list:
        return await self._call("get_longhu_records", trade_date)

    async def search(self, query: str) -> list:
        return await self._call("search", query)

    async def get_minute_line(self, symbol: str) -> list:
        return await self._call("get_minute_line", symbol)

    async def get_board_rankings(self, board_type: str = "hangye") -> list:
        return await self._call("get_board_rankings", board_type)

    async def get_longhu_detail(self, symbol: str, trade_date) -> dict:
        return await self._call("get_longhu_detail", symbol, trade_date)

    async def get_longhu_history(self, symbol: str, limit: int = 30) -> list:
        return await self._call("get_longhu_history", symbol, limit)

    async def get_capital_flow(self, symbol: str, days: int = 30) -> list:
        return await self._call("get_capital_flow", symbol, days)

    async def get_financials(self, symbol: str, periods: int = 8) -> list:
        return await self._call("get_financials", symbol, periods)

    async def get_limit_break_pool(self, trade_date) -> list:
        return await self._call("get_limit_break_pool", trade_date)

    async def get_trading_days(self) -> list:
        return await self._call("get_trading_days")

    async def get_company_profile(self, symbol: str) -> dict:
        return await self._call("get_company_profile", symbol)

    async def get_announcements(self, symbol: str, limit: int = 10) -> list:
        return await self._call("get_announcements", symbol, limit)

    async def get_news(self, symbol: str, limit: int = 10) -> list:
        return await self._call("get_news", symbol, limit)

    async def get_hot_stock_list(self, period: str = "day") -> list:
        """热股榜（仅 ths 实现；无备源，失败向上抛由调用方标 gap）。"""
        return await self._call("get_hot_stock_list", period)

    async def get_hot_stock_list_history(self, d: date) -> list:
        """历史热股榜（仅 ths 实现）。"""
        return await self._call("get_hot_stock_list_history", d)

    async def get_auction_snapshot(self, symbols: list[str], stage: str = "final") -> list:
        """集合竞价快照（仅 ths 实现）。"""
        return await self._call("get_auction_snapshot", symbols, stage)

    async def get_auction_benchmark(self, d: date) -> list:
        """短线风向标竞价基准（仅 ths 实现）。"""
        return await self._call("get_auction_benchmark", d)

    async def get_adjustment_events(self, symbol: str, start: date | None = None, end: date | None = None) -> list:
        """复权事件流（仅 ths 实现，单只；空事件流合法）。"""
        return await self._call("get_adjustment_events", symbol, start, end)
