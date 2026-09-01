"""QuoteHub 秒级化（2026-09-01）行为回归。

用户可见目标：自选涨跌幅 WS 推送节奏 5s → 1s（poll_interval_seconds=1.0），
配套语义：
- 瞬时失败不闪 stale：数据年龄 < stale_after 时保持 last good data；
- 超过 stale_after（或从未成功）才标 stale 广播（红线 2 不冒充实时）；
- 休市每周期只广播一条 stale（不再 stale+quotes 连发冲掉"休市"状态）；
- 实时方法（quotes/indices 等）腾讯源优先（付费/慢源不挡秒级链路）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.data_providers.eastmoney import ProviderError
from app.schemas.market import Quote
from app.services.quote_hub import QuoteHub


def _q(symbol: str, price: float) -> Quote:
    return Quote(
        symbol=symbol,
        name=symbol,
        market="SH",
        price=price,
        prev_close=price,
        change=0.0,
        change_pct=0.0,
        volume=1.0,
        amount=1.0,
        data_timestamp=datetime.now(timezone.utc),
        source="test",
    )


class _Flaky:
    """先成功后失败的源：控制每次 get_quotes 的行为。"""

    name = "flaky"

    def __init__(self) -> None:
        self.fail = False

    async def get_indices(self) -> list[Quote]:
        if self.fail:
            raise ProviderError("blocked")
        return []

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        if self.fail:
            raise ProviderError("blocked")
        return [_q(s, 10.0) for s in symbols]


def test_transient_failure_within_stale_after_keeps_live_data():
    """单次刷新失败：数据年龄 ~0 < stale_after → 不标 stale、不广播过期。"""
    src = _Flaky()
    hub = QuoteHub(provider=src, poll_interval=1.0, get_watchlist=lambda: ["600519"], stale_after=10.0)
    q = hub.subscribe({"600519"})

    async def _noop() -> None:
        return None

    hub._refresh_closed_state = _noop  # type: ignore[method-assign]  # 休市判定另行测试

    asyncio.run(hub.refresh())
    assert q.get_nowait()["type"] == "quotes"
    assert hub.last_success_refresh is not None

    src.fail = True
    asyncio.run(hub.refresh())
    assert hub.consecutive_failures == 1
    assert not hub.is_stale()
    assert q.empty()  # 未广播 stale——前端不闪"数据过期"
    # 缓存仍是最后一次成功的数据（不是空、不是伪实时的新数据）
    assert hub.get_quotes(["600519"])[0].price == 10.0


def test_failure_beyond_stale_after_marks_stale():
    """数据年龄超过 stale_after 的失败必须标 stale 并广播（红线 2）。"""
    src = _Flaky()
    hub = QuoteHub(provider=src, poll_interval=1.0, get_watchlist=lambda: ["600519"], stale_after=10.0)
    q = hub.subscribe({"600519"})

    async def _noop() -> None:
        return None

    hub._refresh_closed_state = _noop  # type: ignore[method-assign]
    asyncio.run(hub.refresh())
    q.get_nowait()

    # 把"最后成功时间"拨回 11s 前 → 本次失败时数据年龄已越界
    hub.last_success_refresh = _utcnow_shifted(-11.0)
    src.fail = True
    asyncio.run(hub.refresh())
    assert hub.is_stale()
    msg = q.get_nowait()
    assert msg["type"] == "stale"
    assert msg["data"][0]["quality"] == "stale"


def test_first_failure_without_success_marks_stale_immediately():
    """启动后从未成功（age=inf）：首个失败立即 stale，绝不无中生有。"""
    src = _Flaky()
    src.fail = True
    hub = QuoteHub(provider=src, poll_interval=1.0, get_watchlist=lambda: ["600519"], stale_after=10.0)
    hub.quotes = {"600519": _q("600519", 99.0)}  # 预置缓存模拟历史数据
    q = hub.subscribe({"600519"})
    asyncio.run(hub.refresh())
    assert q.get_nowait()["type"] == "stale"


def test_closed_market_broadcasts_single_stale_message():
    """休市：每周期只发一条 stale（market_closed），不再追加 quotes 消息。"""
    src = _Flaky()
    hub = QuoteHub(provider=src, poll_interval=1.0, get_watchlist=lambda: ["600519"], stale_after=10.0)

    async def _closed() -> None:
        async def fake_closed_state() -> None:
            hub._closed_marked = True
            hub._mark_all_stale(reason="market_closed")

        # 只替换休市判定（不触网），其余走真实 refresh 逻辑
        hub._refresh_closed_state = fake_closed_state  # type: ignore[method-assign]
        await hub.refresh()

    asyncio.run(_closed())
    q = hub.subscribe({"600519"})
    asyncio.run(_closed())
    msg = q.get_nowait()
    assert msg["type"] == "stale"
    assert msg["data"][0]["quality_reasons"] is not None
    assert "market_closed" in msg["data"][0]["quality_reasons"]
    assert q.empty()  # 无第二条 quotes 消息——前端"休市"状态不再被冲回 live


def _utcnow_shifted(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def test_realtime_methods_prefer_tencent_rank():
    """秒级实时方法按 realtime_rank 排序：腾讯(0)先于 ths(默认100)；其余方法维持构造顺序。"""
    from app.data_providers.composite import REALTIME_METHODS, CompositeProvider

    class _P:
        def __init__(self, name: str, rank: int | None = None):
            self.name = name
            if rank is not None:
                self.realtime_rank = rank

        # _pick 按 hasattr 过滤，桩必须带被路由的方法
        async def get_quotes(self, symbols):
            return []

        async def get_kline(self, *args):
            return []

    ths, tx, em = _P("ths"), _P("tencent", rank=0), _P("eastmoney")
    comp = CompositeProvider([ths, tx, em])
    # 实时方法：腾讯插队到最前，其余相对顺序不变
    assert [p.name for p in comp._pick("get_quotes")] == ["tencent", "ths", "eastmoney"]
    assert "get_indices" in REALTIME_METHODS
    # 非实时方法：维持构造顺序（低频调用不改变既有路由习惯）
    assert [p.name for p in comp._pick("get_kline")] == ["ths", "tencent", "eastmoney"]


def test_update_symbols_reuses_queue_and_refilters_broadcast():
    """subscribe 后原地改订阅集：同一队列继续收到推送、且按新集合过滤。

    回归 2026-09-01 P0：旧实现 subscribe 切换 = unsubscribe+新队列，writer
    parked 在旧队列 get() 上 → 推送静默死亡（前端 30s 重连快照假象）。"""
    src = _Flaky()
    hub = QuoteHub(provider=src, poll_interval=1.0, get_watchlist=lambda: ["600519", "000001"], stale_after=10.0)

    async def _noop() -> None:
        return None

    hub._refresh_closed_state = _noop  # type: ignore[method-assign]

    q = hub.subscribe({"600519"})
    asyncio.run(hub.refresh())
    first = q.get_nowait()
    assert [x["symbol"] for x in first["data"]] == ["600519"]

    # 原地改订阅集：同一队列对象，后续广播按新集合过滤
    assert hub.update_symbols(q, {"600519", "000001"}) is True
    asyncio.run(hub.refresh())
    second = q.get_nowait()
    assert sorted(x["symbol"] for x in second["data"]) == ["000001", "600519"]

    # 未知队列返回 False，不抛
    assert hub.update_symbols(asyncio.Queue(), {"600519"}) is False


def test_poll_interval_floor_allows_one_second():
    """poll_interval=1.0 不被旧的 max(1.0) 之外的下限抬高；0.5 以下抬到 0.5。"""
    hub = QuoteHub(provider=None, poll_interval=1.0)
    assert hub.poll_interval == 1.0
    hub2 = QuoteHub(provider=None, poll_interval=0.1)
    assert hub2.poll_interval == 0.5


@pytest.mark.parametrize("interval,stale_after,expect", [(1.0, 0.5, 1.0), (5.0, 10.0, 10.0)])
def test_stale_after_floor(interval: float, stale_after: float, expect: float):
    """stale_after 不得低于 poll_interval（否则每个周期都在标 stale）。"""
    hub = QuoteHub(provider=None, poll_interval=interval, stale_after=stale_after)
    assert hub.stale_after == expect
