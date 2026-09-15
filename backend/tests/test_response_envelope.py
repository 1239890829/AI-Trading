"""B2 response_model 兼容性：声明了 response_model 的端点，其真实输出
必须能无损解析进声明的模型——任何字段丢失都会被这里抓住。

直接调用路由函数（绕过 Depends），再 model_validate 进 Envelope。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.api.routes import market_longhu as longhu_route
from app.api.routes import market_pools as pools_route
from app.api.routes import market_quotes as quotes_route
from app.api.routes import market_stock as stock_route
from app.schemas.envelope import (
    Envelope,
    KlinePayload,
    LimitUpPoolPayload,
    LongHuPayload,
)
from app.schemas.market import (
    Kline,
    LimitUpRecord,
    LongHuRecord,
    Quote,
    SymbolSearchItem,
    Trade,
)


class _FakeProvider:
    name = "chain(mock)"

    async def search(self, query: str):
        # 2026-09-02 起路由不再兜底 MockProvider：上游无匹配就是空列表，
        # envelope 测试用桩给数据，不再借道"吞异常→mock 假数据"的旧路径
        from app.schemas.market import SymbolSearchItem as _S

        return [_S(symbol="600519", name="贵州茅台", market="SH", source="mock")]

    async def get_kline(self, symbol, timeframe, start, end):
        return [_kline("600519")]

    async def get_limit_up_pool(self, d):
        return [_limit_up()]

    async def get_limit_break_pool(self, d):
        return [_limit_up()]

    async def get_longhu_records(self, d):
        return [_longhu()]


class _FakeHub:
    provider = _FakeProvider()
    last_success_refresh = datetime.now(timezone.utc)

    def is_stale(self):
        return False

    def get_quotes(self, symbols=None):
        return [_quote()]


def _quote() -> Quote:
    return Quote(symbol="600519", name="贵州茅台", price=1500.0, source="mock")


def _kline(symbol: str) -> Kline:
    return Kline(symbol=symbol, timeframe="1d", ts=datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc), close=1500.0, source="mock")


def _limit_up() -> LimitUpRecord:
    return LimitUpRecord(symbol="000560", name="我爱我家", trade_date=date(2026, 8, 28), consecutive_boards=2, reason="住房政策", source="mock")


def _longhu() -> LongHuRecord:
    return LongHuRecord(symbol="000560", name="我爱我家", trade_date=date(2026, 8, 28), net_buy=1.2e8, source="mock")


def test_quotes_envelope_no_field_loss():
    import asyncio

    payload = asyncio.run(quotes_route.quotes(symbols="600519", hub=_FakeHub()))
    env = Envelope[list[Quote]].model_validate(payload)
    assert env.data[0].symbol == "600519"
    # 关键不变式：model_dump 产生的字段集 == 模型字段集（丢字段 = 前端断粮）
    assert set(payload["data"][0].keys()) == set(Quote.model_fields.keys())


def test_kline_envelope():
    import asyncio

    payload = asyncio.run(quotes_route._kline_payload(_FakeHub(), "600519", "1d", 10, None, None))
    env = Envelope[KlinePayload].model_validate(payload)
    assert env.data.bars[0].close == 1500.0
    assert env.data.timeframe == "1d"


def test_limit_up_envelope():
    import asyncio

    hub = _FakeHub()
    p1 = asyncio.run(pools_route.limit_up(date_str=None, hub=hub))
    for payload in (p1,):
        env = Envelope[LimitUpPoolPayload].model_validate(payload)
        assert env.data.pool[0].consecutive_boards == 2
        assert set(payload["data"]["pool"][0].keys()) == set(LimitUpRecord.model_fields.keys())


def test_longhu_envelope():
    import asyncio

    payload = asyncio.run(longhu_route.longhu(date_str=None, hub=_FakeHub()))
    env = Envelope[LongHuPayload].model_validate(payload)
    assert env.data.records[0].net_buy == 1.2e8


def test_search_envelope():
    import asyncio
    import json

    resp = asyncio.run(stock_route.search(q="茅台", hub=_FakeHub()))
    # 路由返回 JSONResponse（带 no-store 头）：envelope 结构必须不变
    assert resp.headers["cache-control"] == "no-store"
    payload = json.loads(resp.body)
    env = Envelope[list[SymbolSearchItem]].model_validate(payload)
    assert env.data[0].symbol == "600519"


def test_trade_envelope():
    payload = {"data": [Trade(symbol="600519", price=1500.0, side="buy", source="mock").model_dump(mode="json")], "meta": {}}
    env = Envelope[list[Trade]].model_validate(payload)
    assert env.data[0].side == "buy"


# ---------------------------------------------------------------- B2 第二批：聚合载荷

def test_sentiment_payload_keeps_extra_fields():
    """extra="allow"：引擎新增字段透传不丢（聚合载荷的演进安全阀）。"""
    from app.schemas.envelope import Envelope, SentimentPayload

    payload = {"data": {"phase": "分歧", "temperature": 62.0, "some_future_field": {"x": 1}}, "meta": {}}
    env = Envelope[SentimentPayload].model_validate(payload)
    dumped = env.model_dump()
    assert dumped["data"]["phase"] == "分歧"
    assert dumped["data"]["some_future_field"] == {"x": 1}, "extra 字段必须透传"


def test_theme_board_envelope_skeleton():
    from app.schemas.envelope import Envelope, ThemeBoardPayload

    payload = {
        "data": {
            "trade_date": "2026-08-28",
            "themes": [{"theme": "房地产", "strength_score": 6.5, "anything_new": 1}],
            "summary": {"limit_up_total": 81, "theme_count": 39, "market_max_boards": 7, "brand_new_key": True},
            "caveats": ["x"],
        },
        "meta": {},
    }
    env = Envelope[ThemeBoardPayload].model_validate(payload)
    assert env.data.summary.limit_up_total == 81
    assert env.data.summary.market_max_boards == 7
    # 卡片 dict 透传（字段级契约由前端镜像维护）
    assert env.data.themes[0]["anything_new"] == 1


def test_minute_line_envelope():
    from app.schemas.envelope import Envelope, MinuteLinePayload

    payload = {
        "data": {
            "symbol": "600519",
            "points": [{"ts": "2026-08-28T01:30:00+00:00", "price": 1289.0, "volume": 8100.0, "source": "tdx"}],
            "vr_baseline_5m": [220600.0, 2643500.0],
        },
        "meta": {},
    }
    env = Envelope[MinuteLinePayload].model_validate(payload)
    assert env.data.points[0].price == 1289.0
    assert env.data.vr_baseline_5m == [220600.0, 2643500.0]


def test_overview_envelope():
    from app.schemas.envelope import Envelope, OverviewPayload

    from tests.test_response_envelope import _quote

    payload = {"data": {"indices": [_quote().model_dump(mode="json")], "total_amount": 123.4}, "meta": {}}
    env = Envelope[OverviewPayload].model_validate(payload)
    assert env.data.indices[0].symbol == "600519"
    assert env.data.total_amount == 123.4
