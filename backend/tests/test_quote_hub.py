"""QuoteHub.get_quotes 的裸代码/指数撞码回归（2026-09-01 P0）。

背景：裸 000001（平安银行）查询曾直接命中 indices 返回上证指数数据
（实测返回 3979.88 冒充股票行情）。项目纪律：裸代码=股票，指数必须带前缀。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.core.bjtime import beijing_today
from app.schemas.market import Quality, Quote
from app.services import quote_hub as qh
from app.services.quote_hub import QuoteHub


def make_q(symbol: str, name: str, price: float) -> Quote:
    return Quote(
        symbol=symbol,
        name=name,
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


def test_bare_stock_symbol_never_falls_back_to_index():
    hub = QuoteHub(provider=None, poll_interval=10)
    hub.indices["000001"] = make_q("000001", "上证指数", 3979.88)
    # quotes 缓存里没有平安银行 → 裸代码查询不得命中指数缓存
    assert hub.get_quotes(["000001"]) == []
    # 000688 国城矿业 ↔ 科创50 同类撞码
    hub.indices["000688"] = make_q("000688", "科创50", 1679.0)
    assert hub.get_quotes(["000688"]) == []


def test_prefixed_index_query_hits_indices():
    hub = QuoteHub(provider=None, poll_interval=10)
    hub.indices["000001"] = make_q("000001", "上证指数", 3979.88)
    out = hub.get_quotes(["sh000001"])
    assert len(out) == 1
    assert out[0].name == "上证指数"
    assert out[0].symbol == "sh000001"  # 以查询形态返回


def test_watchlist_stock_takes_priority():
    hub = QuoteHub(provider=None, poll_interval=10)
    hub.indices["000001"] = make_q("000001", "上证指数", 3979.88)
    hub.quotes["000001"] = make_q("000001", "平安银行", 12.34)
    out = hub.get_quotes(["000001"])
    assert len(out) == 1
    assert out[0].name == "平安银行"
    assert out[0].price == 12.34


# ---- _poll_symbols（2026-09-02）：轮询池 = 自选 ∪ 订阅者订阅集的裸 6 位代码 ----

def test_poll_symbols_merges_subscriber_symbols():
    """详情面板看非自选股：订阅集里的裸代码必须并入轮询池，否则该股
    永远不进缓存 → WS/REST 都取不到 → 分时/K线只剩 60s 校准慢通道。"""
    hub = QuoteHub(provider=None, poll_interval=10, get_watchlist=lambda: ["600105"])
    hub.subscribe({"002594", "sh000001"})  # 详情页订阅个股 + 大盘指数叠加
    pool = hub._poll_symbols()
    assert "600105" in pool  # 自选保留
    assert "002594" in pool  # 订阅的个股并入
    assert "sh000001" not in pool  # 带前缀指数不进池（get_indices 单独维护）


def test_poll_symbols_excludes_prefixed_and_non_numeric():
    """防串纪律：裸代码=股票。带前缀/字母/非 6 位形态绝不进 get_quotes 轮询池。"""
    hub = QuoteHub(provider=None, poll_interval=10, get_watchlist=lambda: [])
    hub.subscribe({"sz000001", "300750", "abc123", "12345"})
    assert hub._poll_symbols() == ["300750"]


def test_poll_symbols_follows_subscribe_lifecycle():
    """订阅/退订/改订阅集都即时反映在轮询池（每轮现算，零额外状态）。"""
    hub = QuoteHub(provider=None, poll_interval=10, get_watchlist=lambda: [])
    q = hub.subscribe({"002594"})
    assert hub._poll_symbols() == ["002594"]
    hub.update_symbols(q, {"600519"})
    assert hub._poll_symbols() == ["600519"]
    hub.unsubscribe(q)
    assert hub._poll_symbols() == []


def test_poll_symbols_tolerates_none_subscription():
    """subscribe(None)（旧协议：全部自选）不影响轮询池合并。"""
    hub = QuoteHub(provider=None, poll_interval=10, get_watchlist=lambda: ["600105"])
    hub.subscribe(None)
    assert hub._poll_symbols() == ["600105"]


# ---- _mark_batch_gaps（R18，2026-09-14）：批量"部分成功"不得冒充高质量 ----
#
# 缺陷：refresh() 只对**返回了的符号**写缓存。缺失符号既没被更新也没被标记，
# quality 仍是上一轮的 high，而 Hub 已记成功并 _broadcast("quotes") ⇒
# 源漏返回的标的拿**上一轮的报价**冒充实时推送（红线 2）。
# 守卫判据刻意用两路独立读数：① quality 枚举 ② 由它派生的 Quote.freshness().state
# ——只断 quality 会漏掉"派生契约没跟上"这一类（KB-ENG-72：覆盖面与判据同等重要）。


class _BatchProvider:
    """最小 provider 桩：可精确控制"源这一轮回哪些符号"。

    带 ``name`` / ``realtime`` 以匹配 `_meta` 的取值面。**不实现**
    ``get_trading_days`` / ``get_kline``，故 ``trading_days`` 会落到持久化
    日历文件——这正是下面 `isolate_calendar` 必须存在的原因。
    """

    name = "batch-stub"
    realtime = True

    def __init__(self, quotes: list[Quote]) -> None:
        self.quotes = quotes
        self.quote_calls = 0

    async def get_indices(self) -> list[Quote]:
        return []

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        self.quote_calls += 1
        want = set(symbols)
        return [q.model_copy(deep=True) for q in self.quotes if q.symbol in want]


@pytest.fixture
def isolate_calendar(monkeypatch):
    """把交易日历压成"不可用"→ `_refresh_closed_state` 不干预（verdict=None）。

    不这样做，断言结果会被**宿主时钟**决定：本机持久化日历末日为最近交易日，
    "今天"通常不在其中 ⇒ ``verdict=False`` ⇒ ``_mark_all_stale("market_closed")``
    覆盖掉 ``batch_missing``，用例在盘后绿、盘中红。这与「时区是宿主的属性」
    同族：**验证环境的属性会决定结论**，必须显式隔离，不能靠"跑的时候是盘后"。
    """

    async def _no_days(_provider, lookback_days: int = 120):
        return []

    monkeypatch.setattr(qh.tc, "trading_days", _no_days)


def _hub_with(quotes: list[Quote]) -> tuple[QuoteHub, _BatchProvider]:
    prov = _BatchProvider(quotes)
    hub = QuoteHub(
        provider=prov,
        poll_interval=1,
        get_watchlist=lambda: ["600105", "600519"],
    )
    return hub, prov


def test_batch_missing_symbol_with_old_cache_is_marked_stale(isolate_calendar):
    """源漏返回 A 只回 B：A 的**旧缓存**必须降级，不能随 quotes 冒充实时。"""
    a = make_q("600105", "平安银行", 10.0)
    b = make_q("600519", "贵州茅台", 100.0)
    hub, prov = _hub_with([a, b])

    asyncio.run(hub.refresh())  # 首轮：全回
    assert hub.last_missing_symbols == []
    assert hub.last_batch_coverage == 1.0
    assert hub.quotes["600105"].quality == Quality.high

    prov.quotes = [b]  # 第二轮：源漏了 A
    asyncio.run(hub.refresh())

    assert hub.last_missing_symbols == ["600105"]
    assert hub.last_batch_coverage == pytest.approx(0.5)
    stale_a = hub.quotes["600105"]
    assert stale_a.quality == Quality.stale              # ① 自评质量
    assert stale_a.quality_reasons == ["batch_missing"]  # 归因可追溯到成因
    assert stale_a.freshness().state == "stale"          # ② 派生契约同步诚实
    assert stale_a.price == 10.0                         # 旧值保留，不丢数据
    assert hub.quotes["600519"].quality == Quality.high  # 回了的仍新鲜
    assert hub.consecutive_failures == 0                 # 仍记成功——这是"部分成功"


def test_hub_freshness_is_not_degraded_by_partial_gap(isolate_calendar):
    """**刻意不接线**：个别标的缺失不让 Hub 级 freshness 整体降级。

    理由：`hub.freshness()` 是 `/api/health` 与 `_meta.is_realtime` 的输入，
    200 只自选里漏 1 只就把整页判成"非实时"是过度反应；逐标的诚实
    （`Quote.freshness()`）已经覆盖这个缺陷。本用例把该**决定**钉住——
    若将来要改成整体降级，必须显式改这里并重新论证（而非悄悄漂移）。
    """
    a = make_q("600105", "平安银行", 10.0)
    b = make_q("600519", "贵州茅台", 100.0)
    hub, prov = _hub_with([a, b])
    asyncio.run(hub.refresh())

    prov.quotes = [b]
    asyncio.run(hub.refresh())

    assert hub.last_missing_symbols == ["600105"]
    assert hub.is_stale() is False
    assert hub.freshness().state == "ready"


def test_batch_all_missing_marks_every_cached_symbol_stale(isolate_calendar):
    """源"成功但一个都没回"：缓存里每一只都必须是 stale，且覆盖率记 0。"""
    a = make_q("600105", "平安银行", 10.0)
    b = make_q("600519", "贵州茅台", 100.0)
    hub, prov = _hub_with([a, b])
    asyncio.run(hub.refresh())

    prov.quotes = []
    asyncio.run(hub.refresh())

    assert prov.quote_calls == 2
    assert hub.last_batch_coverage == 0.0
    assert hub.last_missing_symbols == ["600105", "600519"]
    assert {q.quality for q in hub.quotes.values()} == {Quality.stale}


def test_missing_symbol_without_cache_is_not_fabricated(isolate_calendar):
    """无旧缓存的缺失标的不臆造 Quote——消费方按 missing 处理（三态，不给假值）。"""
    hub, _ = _hub_with([make_q("600105", "平安银行", 10.0)])

    asyncio.run(hub.refresh())

    assert hub.last_missing_symbols == ["600519"]
    assert hub.last_batch_coverage == pytest.approx(0.5)
    assert "600519" not in hub.quotes
    assert hub.get_quotes(["600519"]) == []


def test_batch_gap_recovers_when_source_returns_again(isolate_calendar):
    """恢复是**自动**的：下一轮源回齐 → 缺失清单清空、标注撤销、回 high。"""
    a = make_q("600105", "平安银行", 10.0)
    b = make_q("600519", "贵州茅台", 100.0)
    hub, prov = _hub_with([a, b])
    asyncio.run(hub.refresh())

    prov.quotes = [b]
    asyncio.run(hub.refresh())
    assert hub.quotes["600105"].quality == Quality.stale

    prov.quotes = [a, b]
    asyncio.run(hub.refresh())

    assert hub.last_missing_symbols == []
    assert hub.last_batch_coverage == 1.0
    assert hub.quotes["600105"].quality == Quality.high
    assert hub.quotes["600105"].quality_reasons != ["batch_missing"]


def test_market_closed_reason_overrides_batch_missing(monkeypatch):
    """顺序被钉住：`_refresh_closed_state` 在 `_mark_batch_gaps` 之后执行，
    以更贴近成因的 `market_closed` 覆盖逐标的归因；覆盖率取证仍保留。

    若有人把 `_mark_batch_gaps` 挪到它之后，缺失标的会留着 `batch_missing`
    归因，本用例变红——这是有意为之的**顺序守卫**。
    """

    async def _today_days(_provider, lookback_days: int = 120):
        return [beijing_today()]

    monkeypatch.setattr(qh.tc, "trading_days", _today_days)
    monkeypatch.setattr(qh.tc, "in_wide_market_window", lambda _dt: False)  # 非交易时段

    a = make_q("600105", "平安银行", 10.0)
    b = make_q("600519", "贵州茅台", 100.0)
    hub, prov = _hub_with([a, b])
    asyncio.run(hub.refresh())

    prov.quotes = [b]  # 同时漏 A
    asyncio.run(hub.refresh())

    assert hub._closed_marked is True
    assert hub.quotes["600105"].quality == Quality.stale
    assert hub.quotes["600105"].quality_reasons == ["market_closed"]
    assert hub.last_missing_symbols == ["600105"]  # 取证面不受归因覆盖影响
