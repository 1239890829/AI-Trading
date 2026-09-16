"""TDX 逐笔适配层（`IMP-038`，2026-09-16）。

本文件钉住的是**语义**，不是接线。四条判据各自对应一个"看起来对、实际错"的坑：

1. **时间戳是真 UTC，不是东财那种"伪 UTC"** —— 差 8 小时：UI 上就是 09:30 vs 17:30。
   东财 `normalizer.normalize_trade` 把北京墙钟直接 `replace(tzinfo=utc)`，
   前端 `timeText()` 按浏览器本地时区渲染 ⇒ UTC+8 下显示 17:30。本层**故意与之相反**。
2. **`bs_flag` 方向映射与东财相反**（TDX `0=买/1=卖`，东财 `1=买/2=卖`）——
   照搬东财的映射表会让买卖**完全反色**，而颜色错不会抛任何异常。
3. **手动分页**（库内自动分页在 `count>1000` 时**页序拼反**：实测 `count=2000`
   首尾同分钟、`mono_inc=False`）⇒ 每次请求量恒 ≤ `PAGE_SIZE`，后取的页插到前面。
4. **降级链可分辨**："两源都失败"与"两源都为空"必须给出不同 `detail`——
   把数据源故障说成"这只票没有逐笔"是在制造事实（红线 2/3 同族）。

全部用例**不触网**：TDX 连接在 `_get_client` 里，测试注入假 client / 假 fetch。
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.bjtime import BJ_TZ
from app.market import tdx_tick
from app.schemas.market import Trade

_DAY = date(2026, 9, 16)


# ---------------------------------------------------------------- 时间戳口径


def test_row_to_trade_uses_true_utc_not_pseudo_utc():
    """09:25:00 北京 ⇒ `01:25:00+00:00`（**真** UTC）。

    ⚠️ 本用例**故意与东财 `normalizer.normalize_trade` 相反**：那边把北京墙钟
    直接 `replace(tzinfo=utc)`（"伪 UTC"，`test_normalizer.py` 用 `hour == 9` 钉住）。
    若有人为了"与东财一致"把本函数改成伪 UTC，这里必须变红——前端 `timeText()`
    按浏览器本地时区渲染，伪 UTC 在 UTC+8 下显示成 **17:25**。
    """
    t = tdx_tick.row_to_trade(
        "600519", {"time": time(9, 25, 0), "price": 1258.0, "vol": 3, "bs_flag": 0}, _DAY
    )
    assert t is not None
    assert t.ts == datetime(2026, 9, 16, 1, 25, tzinfo=timezone.utc)
    assert t.ts.astimezone(BJ_TZ).strftime("%H:%M:%S") == "09:25:00"


def test_row_to_trade_accepts_string_and_datetime_time():
    """`time` 列实测是 `datetime.time`，但列 dtype 是 `object` ⇒ 两种形态都要容错。"""
    row = {"price": 10.0, "vol": 1, "bs_flag": 0}
    a = tdx_tick.row_to_trade("600519", {**row, "time": "09:30:05"}, _DAY)
    b = tdx_tick.row_to_trade(
        "600519", {**row, "time": datetime(2026, 9, 16, 9, 30, 5)}, _DAY
    )
    assert a is not None and b is not None
    assert a.ts == b.ts == datetime(2026, 9, 16, 1, 30, 5, tzinfo=timezone.utc)


# ---------------------------------------------------------------- 方向映射


@pytest.mark.parametrize(
    "flag,expected",
    [
        (0, "buy"),       # TDX：0=买入
        (1, "sell"),      # TDX：1=卖出（东财此处是"买"，方向反色即源于照搬）
        (2, "neutral"),   # 中性
        (5, "neutral"),   # 盘后定价
        (None, "neutral"),  # 缺失不猜方向
        (9, "neutral"),   # 未知取值不猜
    ],
)
def test_row_to_trade_bs_flag_mapping(flag, expected):
    row = {"time": time(9, 30, 0), "price": 10.0, "vol": 1, "bs_flag": flag}
    t = tdx_tick.row_to_trade("600519", row, _DAY)
    assert t is not None and t.side == expected


def test_row_to_trade_drops_unparsable_rows():
    """时间/价格/量任一不可解析 ⇒ None（由调用方丢弃），**不伪造 0**。"""
    base = {"time": time(9, 30, 0), "price": 10.0, "vol": 1, "bs_flag": 0}
    assert tdx_tick.row_to_trade("600519", {**base, "time": "not-a-time"}, _DAY) is None
    assert tdx_tick.row_to_trade("600519", {**base, "price": None}, _DAY) is None
    assert tdx_tick.row_to_trade("600519", {**base, "vol": None}, _DAY) is None
    assert tdx_tick.row_to_trade("600519", {**base, "price": "abc"}, _DAY) is None


def test_row_to_trade_marks_source_and_lot_unit():
    """`source` 必须是 `tdx`（前端 `SOURCE_LABELS` 靠它取中文名），量纲 = 手。"""
    t = tdx_tick.row_to_trade(
        "600519", {"time": time(9, 30, 0), "price": 1258.0, "vol": 7, "bs_flag": 1}, _DAY
    )
    assert t is not None
    assert t.source == tdx_tick.TDX_SOURCE == "tdx"
    assert t.volume == 7.0


# ---------------------------------------------------------------- 市场判定


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("600519", 1), ("601398", 1), ("688981", 1), ("900901", 1),   # 沪（含科创/B股）
        ("000001", 0), ("002594", 0), ("300750", 0), ("302132", 0),   # 深（含创业板）
        ("920819", 2), ("430047", 2), ("830799", 2), ("870204", 2), ("889999", 2),  # 北交所
        ("600519.SH", 1), ("000001.SZ", 0), ("920819.BJ", 2),
        # 后缀优先：000001 裸码判 SZ（平安银行），带 .SH 是上证指数 —— 仅凭首位数字必错
        ("000001.SH", 1),
    ],
)
def test_tdx_market_covers_beijing_and_prefers_suffix(symbol, expected):
    """北交所段（43/83/87/88/92）必须判成 BJ=2。

    ⚠️ 判错市场**不报错、只返回空**（实测 600519 传 SZ 返 0 行）⇒ 这个用例是
    唯一能在离线环境里发现映射错误的地方。`tdx_kline.py` 的
    `Market.SH if symbol[0] in "69" else Market.SZ` 会把 920819 判成 SH（恒空）。
    """
    assert tdx_tick.tdx_market(symbol) == expected


@pytest.mark.parametrize("bad", ["sh000001", "60051", "6005190", "", "abc123", "600519.XX"])
def test_split_symbol_rejects_non_stock_codes(bad):
    with pytest.raises(ValueError):
        tdx_tick.tdx_market(bad)


# ---------------------------------------------------------------- 分页


class _FakeFrame(list):
    """够用的 DataFrame 替身：`len()` + `to_dict("records")`（本层只用到这两个）。"""

    def to_dict(self, orient):  # noqa: ARG002
        return list(self)


class _FakeClient:
    """按**真实语义**返回分页窗口：`start` 以「最新」为原点、窗口内升序。

    每次调用都断言 `count <= PAGE_SIZE` —— 超页就会触发库内自动分页（页序拼反），
    这正是本层要防的那件事。
    """

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.calls: list[tuple[int, str, int, int, int | None]] = []

    def get_transactions(self, market, code, count=2000, start=0, date=None):
        assert count <= tdx_tick.PAGE_SIZE, f"请求量 {count} 超过单页上限，会触发库内错误分页"
        self.calls.append((market, code, count, start, date))
        end = len(self.rows) - start
        begin = max(0, end - count)
        return _FakeFrame(self.rows[begin:end])


def _rows(n: int) -> list[dict]:
    """`n` 行升序逐笔（09:30:00 起、每 3 秒一笔 —— 与实测粒度一致）。"""
    base = datetime(2026, 9, 16, 9, 30, 0)
    out = []
    for i in range(n):
        out.append(
            {
                "time": (base + timedelta(seconds=3 * i)).time(),
                "price": 10.0 + i * 0.01,
                "vol": 1,
                "bs_flag": 0,
            }
        )
    return out


def _patch_client(monkeypatch, client) -> None:
    monkeypatch.setattr(tdx_tick, "_get_client", lambda timeout=None: client)


def test_fetch_tdx_trades_fast_path_is_single_ipc(monkeypatch):
    """`limit ≤ PAGE_SIZE` ⇒ **一次** IPC 拿最新 N 笔（实测 median 20.1ms）。

    这条是性能契约：翻全天 4 页要 150–300ms，而逐笔是 10s 轮询的热路径。
    """
    client = _FakeClient(_rows(2500))
    _patch_client(monkeypatch, client)
    rows = tdx_tick.fetch_tdx_trades("600519", limit=50)
    assert len(rows) == 50
    assert len(client.calls) == 1, "快路径不该翻页"
    assert client.calls[0][2] == 50 and client.calls[0][3] == 0
    # 取到的是**最新** 50 笔（升序），不是最早 50 笔
    assert [r.price for r in rows] == [r["price"] for r in _rows(2500)[-50:]]
    assert all(rows[i].ts <= rows[i + 1].ts for i in range(len(rows) - 1))


def test_fetch_tdx_trades_full_day_paginates_manually_in_ascending_order(monkeypatch):
    """`limit=None` ⇒ 手动翻页取全天，**整体仍升序**（后取的页插到前面）。

    若有人改回"让库自动分页"（`count=len` 一次要），本用例会红：库内把最新
    1000 笔放前面、更早的 extend 在后 ⇒ `mono_inc=False`。
    """
    client = _FakeClient(_rows(3867))
    _patch_client(monkeypatch, client)
    rows = tdx_tick.fetch_tdx_trades("600519")
    assert len(rows) == 3867
    # 每页恒请求 1000（调用方无法预知剩余量）：3867 = 1000×3 + 867，
    # 末页只回 867 < 1000 ⇒ 判定已到当日首笔、停止翻页（不会多打一次空请求）。
    assert [c[2] for c in client.calls] == [1000, 1000, 1000, 1000]
    assert [c[3] for c in client.calls] == [0, 1000, 2000, 3000]
    assert all(rows[i].ts <= rows[i + 1].ts for i in range(len(rows) - 1))
    assert rows[0].price == _rows(3867)[0]["price"]  # 首笔是当日最早


def test_fetch_tdx_trades_empty_result_is_not_an_error(monkeypatch):
    """空列表 = 合法结果（北交所部分标的实测恒空），**不是**异常。"""
    client = _FakeClient([])
    _patch_client(monkeypatch, client)
    assert tdx_tick.fetch_tdx_trades("430047", limit=10) == []


def test_bad_connection_is_dropped_so_next_call_rebuilds(monkeypatch):
    """协议级错误 ⇒ 丢弃单例（`auto_reconnect` 只管 socket 断开，兜不住这种）。"""

    class _Boom:
        closed = False

        def get_transactions(self, *a, **k):
            raise RuntimeError("protocol error")

        def close(self):
            _Boom.closed = True

    fake = _Boom()
    _patch_client(monkeypatch, fake)
    monkeypatch.setattr(tdx_tick, "_client", fake)
    monkeypatch.setattr(tdx_tick, "_client_pid", os.getpid())
    with pytest.raises(RuntimeError):
        tdx_tick.fetch_tdx_trades("600519", limit=10)
    assert tdx_tick._client is None, "坏连接必须被丢弃，否则后续请求全部陪葬"
    assert _Boom.closed is True


# ---------------------------------------------------------------- 降级链


def _trade(source: str) -> Trade:
    return Trade(symbol="600519", ts=datetime(2026, 9, 16, 1, 30, tzinfo=timezone.utc),
                 price=1.0, volume=1.0, side="buy", source=source)


@pytest.fixture
def tdx_enabled(monkeypatch):
    """打开降级备源。

    ⚠️ **测试环境默认是关的**（`conftest` 置 `ASHARE_TRADES_TDX_FALLBACK_ENABLED=false`）
    —— TDX 走真实 TCP、mock 替不掉它，开着会让"链返回空/异常"的用例真的去连服务器。
    要测降级链本身就在这里显式打开，并**注入假 `fetch_tdx_trades`**（不触网）。
    """
    monkeypatch.setattr(tdx_tick.settings, "trades_tdx_fallback_enabled", True)


def _run(primary, *, limit=50):
    return asyncio.run(tdx_tick.fetch_trades_with_tdx_fallback(primary, "600519", limit=limit))


def test_fallback_returns_chain_result_without_touching_tdx(monkeypatch):
    """链命中 ⇒ 用链的结果，**TDX 完全不被触达**（这是测试无网的前提）。

    若把 TDX 改回主源，`ASHARE_DATA_PROVIDER=mock` 的测试会绕过 mock 直连真实
    TDX 服务器（实测确实建连并返回真数据）⇒ 单测变成"有网才过"。
    """

    async def primary(symbol):  # noqa: ARG001
        return [_trade("eastmoney")]

    def _boom(**kw):
        raise AssertionError("链已命中，不该调用 TDX")

    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", _boom)
    rows, source, detail = _run(primary)
    assert source == "eastmoney" and detail == ""
    assert [r.source for r in rows] == ["eastmoney"]


def test_fallback_accepts_dict_rows_from_stub_providers(monkeypatch):
    """行是 **dict** 时也不得抛（测试桩常给 dict）。

    ⚠️ 回归背景：首版写 `rows[0].source` ⇒ 对 dict 抛 `AttributeError`，而异常被
    降级链**吞掉** ⇒ 表现成"链失败"而非"代码有 bug"，两条既有用例因此静默走到
    TDX 并拿到真数据（2026-09-16 全量实测踩到）。
    """

    async def primary(symbol):  # noqa: ARG001
        return [{"symbol": "600519", "source": "eastmoney", "price": 10.5}]

    def _boom(**kw):
        raise AssertionError("链已命中，不该调用 TDX")

    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", _boom)
    rows, source, detail = _run(primary)
    assert len(rows) == 1 and source == "eastmoney" and detail == ""


def test_fallback_uses_tdx_when_chain_fails(monkeypatch, tdx_enabled):
    """链失败 ⇒ TDX 接管，`source` 标 `tdx`（前端据此显示 3 秒聚合口径）。"""

    async def primary(symbol):  # noqa: ARG001
        raise RuntimeError("Server disconnected without sending a response.")

    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", lambda *a, **k: [_trade("tdx")])
    rows, source, detail = _run(primary)
    assert source == "tdx" and detail == ""
    assert len(rows) == 1


def test_fallback_uses_tdx_when_chain_returns_empty(monkeypatch, tdx_enabled):
    """链"通而空"（三个 `return []` 占位）也算失败 ⇒ 继续走 TDX。"""

    async def primary(symbol):  # noqa: ARG001
        return []

    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", lambda *a, **k: [_trade("tdx")])
    rows, source, _ = _run(primary)
    assert source == "tdx" and len(rows) == 1


def test_fallback_is_skipped_when_disabled(monkeypatch):
    """**测试环境默认路径**：开关关闭时**绝不触达 TDX**（不建连、不 import easy_tdx）。

    这是单测无网确定性的守卫——去掉它，任何"链返回空/异常"的用例都会真的连服务器。
    """
    monkeypatch.setattr(tdx_tick.settings, "trades_tdx_fallback_enabled", False)

    async def primary(symbol):  # noqa: ARG001
        return []

    def _boom(**kw):
        raise AssertionError("开关关闭时不得调用 TDX")

    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", _boom)
    rows, source, detail = _run(primary)
    assert (rows, source) == ([], "none")
    assert detail == "chain: empty; tdx: disabled"


def test_fallback_distinguishes_all_failed_from_all_empty(monkeypatch, tdx_enabled):
    """**核心判据**：两源都失败 vs 两源都为空，`detail` 必须可分辨。

    此前两者都渲染成"暂无逐笔"，等于把数据源故障说成"这只票没有逐笔"。
    """

    async def failing(symbol):  # noqa: ARG001
        raise RuntimeError("WAF blocked")

    def tdx_failing(*a, **k):
        raise RuntimeError("tdx timeout")

    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", tdx_failing)
    rows, source, detail = _run(failing)
    assert rows == [] and source == "none"
    assert "chain: WAF blocked" in detail and "tdx: tdx timeout" in detail

    async def empty(symbol):  # noqa: ARG001
        return []

    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", lambda *a, **k: [])
    _, _, detail_empty = _run(empty)
    assert detail_empty == "chain: empty; tdx: empty"
    assert detail_empty != detail, "两种情形的 detail 不得相同"


def test_fallback_never_raises(monkeypatch, tdx_enabled):
    """降级链本身**不抛异常**：异常一律转成 detail，由调用方决定报错方式。"""

    async def failing(symbol):  # noqa: ARG001
        raise RuntimeError("boom")

    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", lambda *a, **k: [])
    rows, source, detail = _run(failing)
    assert (rows, source) == ([], "none")
    assert detail


def test_fallback_passes_limit_to_tdx(monkeypatch, tdx_enabled):
    """`limit` 必须透传到 TDX（否则备源会白翻全天）。"""
    seen: dict = {}

    async def primary(symbol):  # noqa: ARG001
        return []

    def spy(symbol, **kw):
        seen.update({"symbol": symbol, **kw})
        return []

    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", spy)
    _run(primary, limit=37)
    assert seen["symbol"] == "600519" and seen["limit"] == 37


def test_close_tdx_client_is_idempotent(monkeypatch):
    """收尾关闭幂等（应用 shutdown / 测试清理都会调，重复调不得抛）。"""
    monkeypatch.setattr(tdx_tick, "_client", None)
    tdx_tick.close_tdx_client()
    tdx_tick.close_tdx_client()
    assert tdx_tick._client is None


# ---------------------------------------------------------------- 故障分类（消费方共用）


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("", ""),
        ("chain: empty; tdx: disabled", ""),
        ("chain: empty; tdx: empty", ""),
        ("chain: WAF blocked; tdx: empty", "chain: WAF blocked"),
        ("chain: empty; tdx: tdx timeout", "tdx: tdx timeout"),
        # ⚠️ 真实 `ProviderError` 的文本**不含任何关键字**——按 "Error/timeout"
        # 之类字样判故障会把这条真故障漏成"没数据"，所以判据必须是**白名单**。
        (
            "chain: Server disconnected without sending a response.; tdx: empty",
            "chain: Server disconnected without sending a response.",
        ),
    ],
)
def test_failure_detail_separates_empty_from_failure(detail, expected):
    """`trades_failure_detail` 是「取数失败」与「没数据」的**唯一**判据。"""
    assert tdx_tick.trades_failure_detail(detail) == expected


def test_failure_classifier_agrees_with_producer(monkeypatch, tdx_enabled):
    """生产者 ⇄ 分类器**必须同步**（改了一边忘了另一边就在这里红）。

    三个结局：链命中 ⇒ 无故障；两源都空 ⇒ 无故障；两源都抛 ⇒ 有故障。
    若有人把 `detail` 的标记串改名（如 `tdx: disabled` → `tdx: off`）而不同步
    白名单，第二条会立刻红——这正是 2026-09-16 把"没数据"报成"取数失败"的成因。
    """

    async def ok(symbol):  # noqa: ARG001
        return [_trade("eastmoney")]

    async def empty(symbol):  # noqa: ARG001
        return []

    async def boom(symbol):  # noqa: ARG001
        raise RuntimeError("chain down")

    monkeypatch.setattr(tdx_tick, "fetch_tdx_trades", lambda *a, **k: [])
    assert tdx_tick.trades_failure_detail(_run(ok)[2]) == ""
    assert tdx_tick.trades_failure_detail(_run(empty)[2]) == ""

    monkeypatch.setattr(
        tdx_tick, "fetch_tdx_trades", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tdx down"))
    )
    failed = tdx_tick.trades_failure_detail(_run(boom)[2])
    assert "chain down" in failed and "tdx down" in failed


# ---------------------------------------------------------------- 消费方接线（路由层）


class _FakeHub:
    """`meta_payload` 需要的最小面（provider.name / is_stale / last_success_refresh）。

    刻意**只**实现 `is_stale`：`hub_freshness` 明确保留了这条历史接口面的回退路径
    （见 `market_envelope.hub_freshness` docstring），桩按最小面实现才不会
    把"加一个只读字段"变成破坏性改动。
    """

    def __init__(self, name: str = "mock", trades=None) -> None:
        async def _empty(symbol):  # noqa: ARG001
            return []

        self.provider = SimpleNamespace(name=name, realtime=False, get_trades=trades or _empty)
        self.last_success_refresh = None
        self.last_batch_coverage = None

    def is_stale(self) -> bool:
        return True


def test_trades_route_returns_empty_not_502_when_no_data():
    """两源都**没给出数据** ⇒ 200 + 空列表 + `meta.trades_detail`，**不是** 502。

    ⚠️ 回归背景：首版判据是 `if not rows and detail:`，而备源未启用时
    `detail = "chain: empty; tdx: disabled"` **非空** ⇒ 把"没数据"报成
    "数据源失败"（2026-09-16 全量实测踩到）。前端据此会把源问题显示成
    "该股没有逐笔"，方向正好反了。
    """
    from app.api.routes.market_quotes import trades as trades_route

    body = asyncio.run(trades_route("600519", 10, _FakeHub()))
    assert body["data"] == []
    assert body["meta"]["trades_source"] == "none"
    assert body["meta"]["trades_detail"] == "chain: empty; tdx: disabled"


def test_trades_route_502s_only_on_real_failure():
    """真故障（链抛异常）才 502，且 detail 带**原因**。

    备源在测试环境默认关闭（`conftest`）⇒ `detail = "chain: <exc>; tdx: disabled"`，
    分类器取出的故障片段只有链那一段。
    """
    from app.api.routes.market_quotes import trades as trades_route

    async def boom(symbol):  # noqa: ARG001
        raise RuntimeError("eastmoney WAF blocked")

    with pytest.raises(HTTPException) as ei:
        asyncio.run(trades_route("600519", 10, _FakeHub(trades=boom)))
    assert ei.value.status_code == 502
    assert "eastmoney WAF blocked" in str(ei.value.detail)
    assert "tdx: disabled" not in str(ei.value.detail), "非故障片段不该进 502 文案"
