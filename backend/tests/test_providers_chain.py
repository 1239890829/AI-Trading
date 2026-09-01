from __future__ import annotations

import asyncio
from datetime import date

from datetime import datetime

from app.data_providers.composite import CompositeProvider
from app.data_providers.eastmoney import ProviderError
from app.data_providers.tencent import parse_quote, parse_order_book, parse_search_row

# 2026-08-28 盘后实测（sh600519）
REAL_SNAPSHOT = (
    "1~贵州茅台~600519~1297.40~1292.30~1289.00~16126~8576~7550"
    "~1297.35~5~1297.20~1~1297.10~3~1297.01~3~1297.00~11"
    "~1297.40~9~1297.50~11~1297.55~2~1297.68~1~1297.70~1"
    "~~20260828161500~5.10~0.39~1297.89~1288.00~1297.40/16126/2086008422~16126~208601~0.13~19.92~~"
).split("~")


def test_tencent_parse_quote_fields():
    q = parse_quote("sh", REAL_SNAPSHOT)
    assert q.symbol == "600519"
    assert q.name == "贵州茅台"
    assert q.market == "SH"
    assert q.price == 1297.40
    assert q.prev_close == 1292.30
    assert q.open == 1289.00
    assert q.high == 1297.89 and q.low == 1288.00
    assert q.change == 5.10 and q.change_pct == 0.39
    assert q.volume == 1_612_600  # 手 → 股
    assert q.amount == 2_086_010_000  # 万 → 元（与快照 2086008422 元一致）
    assert q.turnover_rate == 0.13
    assert q.source == "tencent"
    # 北京时间 16:15:00 → UTC 08:15
    assert q.data_timestamp is not None and q.data_timestamp.hour == 8 and q.data_timestamp.minute == 15


def test_tencent_parse_quote_passes_validator():
    from app.data_quality.validator import validate_quote

    q = validate_quote(parse_quote("sh", REAL_SNAPSHOT))
    assert q.quality.value in {"high", "low"}, q.quality_reasons


def test_tencent_order_book_bids_below_asks():
    ob = parse_order_book("600519", REAL_SNAPSHOT)
    assert len(ob.bids) == 5 and len(ob.asks) == 5
    assert ob.bids[0].price == 1297.35 and ob.bids[0].volume == 500
    assert ob.asks[0].price == 1297.40 and ob.asks[0].volume == 900
    assert ob.bids[0].price < ob.asks[0].price
    assert ob.bids == sorted(ob.bids, key=lambda l: -l.price)
    assert ob.asks == sorted(ob.asks, key=lambda l: l.price)


def test_tencent_search_row():
    item = parse_search_row("sh600519", "贵州茅台")
    assert item and item.symbol == "600519" and item.market == "SH"


class _FakeResp:
    def __init__(self, text: str):
        self.status_code = 200
        self._text = text

    @property
    def content(self):
        return self._text.encode("gbk")


class _FakeClient:
    def __init__(self, text: str):
        self._text = text

    async def get(self, url, params=None):
        return _FakeResp(self._text)

    async def aclose(self):
        return None


def test_tencent_get_quotes_prefixed_index_symbol_roundtrip():
    """带前缀指数查询（sh000001）必须命中并保持调用方形态——
    旧实现 _snapshot 的 key 是响应里的裸代码，snap.get("sh000001") 永远 miss，
    报"tencent snapshot no rows"假象（2026-08-31 指数详情面板实测抓到）。"""
    from app.data_providers.tencent import TencentProvider

    # 腾讯真实指数行：v_sh000001="1~上证指数~000001~3986.30~..."
    text = 'v_sh000001="1~上证指数~000001~3986.30~3952.18~3926.53~576656606~0~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~0.00~0~~20260831155302~34.12~0.86~3986.30~3926.50~3986.30/576656606/1014292552054~576656606~101429255~1.19~17.34~~3986.30~3926.50~1.51~623529.54~706919.43~0.00~-1~-1~1.17~0~3949.79~~~~~~101429255.2054~0.0000~0~ ~ZS~0.44~2.69~~~~4258.86~3732.84~0.09~4.64~-1.03~48481";'
    p = TencentProvider()
    p._client = _FakeClient(text)  # type: ignore[assignment]
    try:
        quotes = asyncio.run(p.get_quotes(["sh000001"]))
        assert len(quotes) == 1
        assert quotes[0].symbol == "sh000001"  # 查询形态，非响应里的裸 000001
        assert quotes[0].name == "上证指数"
        assert quotes[0].price == 3986.30
    finally:
        asyncio.run(p.aclose())


# 2026-08-28 fqkline 实测响应结构（截取）
KLINE_PAYLOAD = {
    "code": 0,
    "data": {"sh600519": {"qfqday": [
        ["2025-05-09", "1499.409", "1511.599", "1517.869", "1495.469", "23672.000"],
        ["2025-05-12", "1518.419", "1524.919", "1539.349", "1517.029", "24735.000"],
    ], "qt": {"time": "20260828161500"}}},
}

MKLINE_PAYLOAD = {
    "code": 0,
    "data": {"sh600519": {"m5": [
        ["202608280935", "1289.10", "1289.90", "1290.00", "1288.80", "3100.000"],
        ["202608280940", "1289.90", "1290.50", "1291.00", "1289.70", "2800.000"],
    ]}},
}


def test_tencent_parse_kline_day_uses_qfqday_key():
    from app.data_providers.tencent import parse_kline_payload

    bars = parse_kline_payload("600519", "1d", KLINE_PAYLOAD)
    assert len(bars) == 2
    assert bars[0].ts.isoformat().startswith("2025-05-09")
    assert bars[0].open == 1499.409 and bars[0].close == 1511.599
    assert bars[0].volume == 2_367_200  # 手 → 股
    assert bars == sorted(bars, key=lambda b: b.ts)


def test_tencent_parse_kline_minute():
    from app.data_providers.tencent import parse_kline_payload

    bars = parse_kline_payload("600519", "5m", MKLINE_PAYLOAD)
    assert len(bars) == 2
    assert bars[1].close == 1290.5
    assert bars[1].ts.hour == 1  # UTC 01:40 = 北京 09:40


def test_tencent_parse_kline_empty_data_returns_empty():
    # 空 data → 空列表；"empty" 异常由 provider.get_kline 统一抛出
    from app.data_providers.tencent import parse_kline_payload

    assert parse_kline_payload("600519", "1d", {"code": 0, "data": {}}) == []


def test_composite_failover_to_fallback():
    class FlakyThenDown:
        name = "broken"
        realtime = False
        dead = False

        async def get_quotes(self, symbols):
            if self.dead:
                raise ProviderError("down")
            self.dead = True  # 首次成功，之后失败
            return [{"symbol": "600519", "source": "broken"}]

        async def aclose(self):
            return None

    class Fallback:
        name = "fallback"
        realtime = False

        async def get_quotes(self, symbols):
            return [{"symbol": "600519", "source": "fallback"}]

    chain = CompositeProvider([FlakyThenDown(), Fallback()])
    first = asyncio.run(chain.get_quotes(["600519"]))
    assert first[0]["source"] == "broken"
    second = asyncio.run(chain.get_quotes(["600519"]))
    assert second[0]["source"] == "fallback"
    assert chain.switch_log and "get_quotes: broken -> fallback" in chain.switch_log[0]
    assert chain.name == "chain(broken→fallback)"


def test_composite_all_fail_raises():
    class Broken:
        name = "broken"
        realtime = False

        async def get_quotes(self, symbols):
            raise ProviderError("down")

    chain = CompositeProvider([Broken()])
    try:
        asyncio.run(chain.get_quotes(["600519"]))
        raise AssertionError("should raise")
    except ProviderError as exc:
        assert "all providers failed" in str(exc)


def test_composite_skips_empty_results():
    class Empty:
        name = "empty"
        realtime = False

        async def get_limit_up_pool(self, trade_date):
            return []

    class Full:
        name = "full"
        realtime = True

        async def get_limit_up_pool(self, trade_date):
            return [{"ok": True}]

    chain = CompositeProvider([Empty(), Full()])
    pool = asyncio.run(chain.get_limit_up_pool(date(2026, 8, 28)))
    assert pool == [{"ok": True}]


FIXED_CLOCK = lambda: datetime(2026, 8, 28, 10, 30, 45)  # noqa: E731


def test_normalize_search_filters_non_six_digit():
    from app.market.normalizer import normalize_search

    # 东财 suggest 会混入港股（5 位代码）——必须过滤
    assert normalize_search({"Code": "03750", "Name": "宁德时代"}) is None
    ok = normalize_search({"Code": "300750", "Name": "宁德时代", "MktNum": 0})
    assert ok == ("300750", "宁德时代", "SZ")


def test_mock_minute_line_shape():
    from app.data_providers.mock import MockProvider

    pts = asyncio.run(MockProvider(clock=FIXED_CLOCK).get_minute_line("600519"))
    assert 5 <= len(pts) <= 240
    assert pts[0]["price"] > 0 and pts[0]["source"] == "mock"
    assert pts[0]["ts"] <= pts[-1]["ts"]


def test_hub_broadcasts_stale_on_provider_failure():
    from app.services.quote_hub import QuoteHub

    class Down:
        name = "down"

        async def get_indices(self):
            raise ProviderError("blocked")

        async def get_quotes(self, symbols):
            raise ProviderError("blocked")

    hub = QuoteHub(provider=Down(), poll_interval=5, get_watchlist=lambda: ["600519"])
    from app.schemas.market import Quote

    hub.indices = {"000001": Quote(symbol="000001", price=3300.0, source="down")}
    hub.quotes = {"600519": Quote(symbol="600519", price=100.0, source="down")}
    q = hub.subscribe()
    asyncio.run(hub.refresh())
    msg = q.get_nowait()
    assert msg["type"] == "stale"
    assert hub.consecutive_failures == 1


def test_hub_get_quotes_falls_back_to_indices():
    """指数行情兜底：indices 缓存的 key 是裸 000001（与腾讯/ths 的 get_indices 一致），
    而 WS 订阅/详情链路查询用的是带前缀形态 sh000001——不兜底则指数永远收不到行情。
    返回的 Quote 必须带查询形态的 symbol（model_copy，不变异共享缓存对象）。

    ⚠️ 2026-09-01 语义收紧（P0 撞码修复）：**只有带 sh/sz/bj 前缀的查询**才回退
    indices——裸 6 位代码是股票（000001 平安银行↔上证指数、000688 国城矿业↔科创50
    撞码），裸查询命中指数曾是"指数数据冒充股票行情"事故的根因。"""
    from app.schemas.market import Quote
    from app.services.quote_hub import QuoteHub

    class Up:
        name = "up"

        async def get_indices(self):
            return []

        async def get_quotes(self, symbols):
            return []

    hub = QuoteHub(provider=Up(), poll_interval=5, get_watchlist=lambda: ["600519"])
    hub.indices = {"000001": Quote(symbol="000001", name="上证指数", price=3300.0, source="up")}
    hub.quotes = {"600519": Quote(symbol="600519", price=100.0, source="up")}
    got = hub.get_quotes(["600519", "sh000001"])
    assert [x.symbol for x in got] == ["600519", "sh000001"]
    assert got[1].name == "上证指数"
    # 裸 000001（平安银行）绝不命中指数缓存——宁可空返回也不冒充
    assert hub.get_quotes(["000001"]) == []
    # 带前缀指数查询可达（broadcast 对 sh000001 形态订阅可达）
    assert [x.symbol for x in hub.get_quotes(["sh000001"])] == ["sh000001"]
    # 双源都缺失的 symbol 返回空列表而不是 KeyError
    assert hub.get_quotes(["999999"]) == []


# ---------------------------------------------------------------- 熔断（circuit breaker）


def _mk_quote(sym: str):
    from app.schemas.market import Quote

    return Quote(symbol=sym, price=10.0, source="good")


def test_breaker_opens_after_consecutive_failures():
    """连续失败 3 次 → 进入冷却，后续请求跳过坏源不再往上撞。

    2026-08-31 腾讯 WAF 封禁期间，每个 K 线请求都在向已知挂掉的源撞一遍
    （白白增加延迟与封禁期），熔断就是为这种情况准备的。
    """
    from app.data_providers.composite import CompositeProvider, FAILURE_THRESHOLD

    calls = {"fail": 0}

    class FailP:
        name = "fail"

        async def get_quotes(self, symbols):
            calls["fail"] += 1
            raise Exception("boom")

    class GoodP:
        name = "good"

        async def get_quotes(self, symbols):
            return [_mk_quote(s) for s in symbols]

    comp = CompositeProvider([FailP(), GoodP()])

    async def drive(n):
        for _ in range(n):
            await comp.get_quote("600519")

    asyncio.run(drive(FAILURE_THRESHOLD))
    assert calls["fail"] == FAILURE_THRESHOLD
    # 冷却已开启：再请求时坏源不被调用，好源直接顶上
    q = asyncio.run(comp.get_quote("600519"))
    assert calls["fail"] == FAILURE_THRESHOLD  # 没有增加
    assert q.symbol == "600519"
    st = comp.breaker_state()
    assert "get_quotes@fail" in st and st["get_quotes@fail"]["cooldown_left"] > 0


def test_breaker_resets_on_success_and_empty_counts_as_failure():
    """成功清零计数；空结果同样计入失败（空往往是源异常的前兆）。"""
    import asyncio

    from app.data_providers.composite import CompositeProvider

    state = {"n": 0}

    class FlakyP:
        name = "flaky"

        async def get_quotes(self, symbols):
            state["n"] += 1
            if state["n"] <= 2:
                return None  # 空结果：计失败
            return [_mk_quote(s) for s in symbols]

    comp = CompositeProvider([FlakyP()])
    # 单源且空结果 → 全链失败抛 ProviderError（预期），但失败计数仍要累计
    for _ in range(2):
        try:
            asyncio.run(comp.get_quote("600519"))
        except Exception:
            pass
    st = comp.breaker_state()
    assert st["get_quotes@flaky"]["failures"] == 2
    # 第三次成功 → 计数清零、无熔断残留
    q = asyncio.run(comp.get_quote("600519"))
    assert comp.breaker_state() == {}
    assert q.symbol == "600519"
