"""全市场快照的**可用性**：限流识别、退避判据、以及"未就绪"的成因披露。

## 背景（2026-09-14 用户报障：「两市成交额怎么没出来了」）

重启后端触发冷启动全量抓取（56 页），新浪 WAF 返回 **HTTP 456**：
13:31:59 / 13:33:59 两轮 `snapshot refresh failed (ProviderError): sina stock count
HTTP 456`，直到 13:38:04 才成功。期间 `MarketSnapshotService.snapshot` 一直为空 ⇒

- `/api/market/breadth`、`/api/market/heatmap` → 503；
- `/api/market/overview.total_amount = None` → 前端渲染 `--`。

而**同一时刻命令行 curl 新浪是 200** ⇒ 限流判的是请求特征/瞬时并发，不是整 IP 封禁。

## 本文件钉三件事（每件都对应一个真实会失效的判据）

1. **456 必须单独成类**（`SinaRateLimited`）——且要能**穿透 `asyncio.gather`
   的异常转换**。原实现在那里统一 `raise ProviderError(f"...")`，类型被抹平，
   调用方再也无从区分"限流"与"源坏了"。
2. **退避判据**（`_next_delay`）四态逐一钉死；其中「限流冷却优先于休市降频」
   是最容易在重构里丢掉的一条（实测 120s / 240s 两轮都仍在限流窗口内）。
3. **`total_amount is None` 必须带上成因**（`total_amount_freshness`）——
   否则前端只能显示 `--`，把"尚未就绪"塌缩成"真的没有"（三态纪律）。
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.data_providers.eastmoney import ProviderError
from app.market import sina_market
from app.market.breadth import compute_breadth
from app.market.sina_market import RATE_LIMIT_STATUS, SinaRateLimited
from app.services import snapshot_service
from app.schemas.market import Quote
from app.services.snapshot_service import (
    BACKOFF_CAP_SECONDS,
    IDLE_INTERVAL_SECONDS,
    RATE_LIMIT_COOLDOWN_CAP_SECONDS,
    RATE_LIMIT_COOLDOWN_SECONDS,
    MarketSnapshotService,
)

# ---------------------------------------------------------------- 工具


class _Resp:
    """`httpx.Response` 的最小替身（只用到 status_code / text / json）。"""

    def __init__(self, status_code: int, *, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


def _patch_http(monkeypatch, handler):
    """把 `httpx.AsyncClient` 换成按 URL 分派的替身（**不触网**）。"""

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            return handler(url, params)

    monkeypatch.setattr(sina_market.httpx, "AsyncClient", _Client)


def _svc() -> MarketSnapshotService:
    return MarketSnapshotService(
        poll_interval=60.0, save_interval=60.0, parquet_dir=Path("/tmp/ashare-test-unused"),
    )


# ---------------------------------------------------------------- ① 限流识别


def test_456_raises_dedicated_type():
    with pytest.raises(SinaRateLimited):
        sina_market._raise_http(RATE_LIMIT_STATUS, "stock count")


@pytest.mark.parametrize("status", [403, 500, 502, 504])
def test_other_statuses_stay_plain_provider_error(status):
    """**成对判据**：只有 456 算限流。

    把一切都当限流会让无关故障也被拖进 600s 冷却——判据放宽比收紧更危险，
    因为它把"上游已经恢复"白白推迟十分钟。
    """
    with pytest.raises(ProviderError) as ei:
        sina_market._raise_http(status, "stock count")
    assert not isinstance(ei.value, SinaRateLimited)


def test_rate_limited_keeps_provider_error_as_base():
    """限流是 `ProviderError` 的**特例**，既有 `except ProviderError` 兜底不得失效。"""
    assert issubclass(SinaRateLimited, ProviderError)


def test_stock_count_456_surfaces_as_rate_limited(monkeypatch):
    _patch_http(monkeypatch, lambda url, params: _Resp(RATE_LIMIT_STATUS, text=""))
    with pytest.raises(SinaRateLimited):
        asyncio.run(sina_market.fetch_market_snapshot())


def test_page_456_keeps_type_through_gather(monkeypatch):
    """⚠️ 钉住**最容易丢掉类型的那一步**。

    分页抓取走 `asyncio.gather(..., return_exceptions=True)`，其后的循环原先对
    每个异常统一 `raise ProviderError(f"sina page fetch failed: {item}")`
    —— 限流类型被抹平，于是 `run()` 的限流分支**永远命中不了**，
    退避退回常规节奏（120s/240s 两轮实测仍在限流窗口内）。
    """

    def handler(url, params):
        if "getHQNodeStockCount" in url:
            return _Resp(200, text='"200"')  # 200 只 ÷ page_size 100 ⇒ 2 页
        if (params or {}).get("page") == 2:
            return _Resp(RATE_LIMIT_STATUS, text="")
        return _Resp(200, payload=[{"symbol": "sh600519", "code": "600519", "name": "x"}])

    _patch_http(monkeypatch, handler)
    with pytest.raises(SinaRateLimited):
        asyncio.run(sina_market.fetch_market_snapshot(page_size=100, concurrency=6))


def test_snapshot_missing_one_page_is_not_published_as_complete(monkeypatch):
    """stock-count 是硬分母；少一整页不能因仍超过 90% 就冒充 ready。"""
    def handler(url, params):
        if "getHQNodeStockCount" in url:
            return _Resp(200, text='"101"')
        if (params or {}).get("page") == 2:
            return _Resp(200, payload=[])
        rows = [
            {"symbol": f"sh{600000 + i:06d}", "code": f"{600000 + i:06d}", "name": f"S{i}"}
            for i in range(100)
        ]
        return _Resp(200, payload=rows)

    _patch_http(monkeypatch, handler)
    with pytest.raises(ProviderError, match="snapshot incomplete.*expected=101"):
        asyncio.run(sina_market.fetch_market_snapshot(page_size=100, concurrency=6))


# ---------------------------------------------------------------- ② 退避判据


def test_next_delay_normal_uses_poll_interval():
    assert _svc()._next_delay(live=True) == 60.0


def test_next_delay_idle_when_market_closed():
    """休市降频（数据静止，没必要勤抓）。"""
    assert _svc()._next_delay(live=False) == IDLE_INTERVAL_SECONDS


@pytest.mark.parametrize("failures,expected", [(1, 120.0), (2, 240.0), (3, 300.0), (9, 300.0)])
def test_next_delay_exponential_backoff_with_cap(failures, expected):
    s = _svc()
    s.consecutive_failures = failures
    assert s._next_delay(live=True) == expected


def test_rate_limit_cooldown_beats_idle_downgrade():
    """限流冷却**优先于**休市降频。

    这是重构里最容易丢的一条：把 `rate_limited` 分支挪到 `not live` 之后，
    休市档（240s）就会盖掉冷却——而实测 240s 仍在限流窗口内。
    """
    s = _svc()
    s.rate_limited = True
    s.consecutive_failures = 1
    assert s._next_delay(live=True) == s._next_delay(live=False)
    assert s._next_delay(live=False) >= IDLE_INTERVAL_SECONDS


@pytest.mark.parametrize("failures,expected", [(1, 240.0), (2, 480.0), (3, 900.0), (7, 900.0)])
def test_rate_limit_cooldown_escalates_and_caps(failures, expected):
    """限流冷却**渐进**：首档 240s → 逐档加倍 → 封顶 900s。

    首档刻意**不取一个大的固定值**：实测本环境限流是**间歇**的
    （失败 5 次 / 成功 5 次交替），两次恢复间隔**均为 245s**
    （13:33:59✗ → 13:38:04✓；13:45:17✗ → 13:49:22✓；**n=2**）。
    固定 600s 会把数据陈旧度从 ~4 分钟恶化到 10 分钟——
    用恢复速度换来的封禁保护是虚的。封顶值保留「持续限流要显著退让」的原意。

    ⚠️ **样本量只有 2，且当日即出现反例**（13:56:59 后连续失败，
    14:14:43 实测 `age` 1064s / `fails=3`）⇒ 本用例只钉**档位映射关系**（行为），
    **不钉档位幅度的合理性**；幅度已挂账本 §6.6 行 23 · 🔶 观察 / 待重估。
    """
    s = _svc()
    s.rate_limited = True
    s.consecutive_failures = failures
    assert s._next_delay(live=True) == expected


def test_rate_limit_cap_exceeds_regular_backoff_cap():
    """成对：封顶必须**显著高于**常规退避上限，否则"限流单独分档"就没有意义。"""
    assert RATE_LIMIT_COOLDOWN_CAP_SECONDS > BACKOFF_CAP_SECONDS
    assert RATE_LIMIT_COOLDOWN_SECONDS >= IDLE_INTERVAL_SECONDS


def test_run_handles_rate_limit_before_generic_exception():
    """静态钉死 `except` 顺序：子类必须排在 `except Exception` **之前**。

    顺序反了的话，`SinaRateLimited` 被父类兜住 ⇒ `rate_limited` 永不置位，
    限流分支形同不存在——而**所有行为用例仍然全绿**（它只是退化成普通失败）。
    这正是 [[KB-ENG-65]] 说的"判据失效却全绿"，所以必须静态钉。
    """
    tree = ast.parse(Path(snapshot_service.__file__).read_text(encoding="utf-8"))
    run = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "run"),
        None,
    )
    assert run is not None, "未找到 MarketSnapshotService.run()"

    handler_sets = [
        [ast.unparse(h.type) if h.type else None for h in t.handlers]
        for t in ast.walk(run)
        if isinstance(t, ast.Try)
    ]
    target = next((names for names in handler_sets if "SinaRateLimited" in names), None)
    assert target is not None, "run() 里没有 SinaRateLimited 分支——限流判据被删了？"
    assert "Exception" in target, "run() 丢了通用兜底分支（非限流故障将不再计数）"
    assert target.index("SinaRateLimited") < target.index("Exception"), (
        f"except 顺序反了：{target} —— 子类会被父类兜住，限流分支永不生效"
    )


def test_refresh_publishes_one_atomic_version_bundle(monkeypatch, tmp_path):
    s = MarketSnapshotService(60.0, 60.0, tmp_path)
    monkeypatch.setattr(s, "_maybe_save", lambda: None)

    async def _fake_snapshot(**kwargs):
        return [{
            "symbol": "600001", "name": "甲", "price": 10.0,
            "change_pct": 1.0, "amount": 1.0e8,
        }]

    monkeypatch.setattr(sina_market, "fetch_market_snapshot", _fake_snapshot)
    asyncio.run(s.refresh())
    rows, as_of = s.versioned_snapshot()
    assert as_of == s.last_success and as_of is not None
    assert rows[0]["change_pct"] == 1.0

    # Even a later in-place mutation of the public compatibility list cannot mutate
    # the frozen evidence version that pairs with ``as_of``.
    s.snapshot[0]["change_pct"] = 9.9
    frozen, frozen_as_of = s.versioned_snapshot()
    assert frozen_as_of == as_of
    assert frozen[0]["change_pct"] == 1.0


def test_refresh_success_clears_rate_limited(monkeypatch, tmp_path):
    """一次限流不得**终生**压低抓取频率：成功时必须清标记。

    否则后续每次轮询都走 600s 冷却（本该 60s），新鲜度契约随之失真。
    """
    s = MarketSnapshotService(60.0, 60.0, tmp_path)
    s.rate_limited = True
    s.consecutive_failures = 3
    monkeypatch.setattr(s, "_maybe_save", lambda: None)

    async def _fake_snapshot(**kwargs):
        return [{"symbol": "600519", "name": "贵州茅台", "price": 10.0,
                 "change_pct": 1.0, "amount": 1.0e8}]

    monkeypatch.setattr(sina_market, "fetch_market_snapshot", _fake_snapshot)
    asyncio.run(s.refresh())

    assert s.rate_limited is False
    assert s.consecutive_failures == 0
    assert s.breadth is not None
    assert s._next_delay(live=True) == 60.0  # 回到正常节奏


def test_freshness_reason_distinguishes_rate_limit():
    """未就绪的**成因**要看得出：限流是"等一会儿会自愈"，源坏了不是。

    （成对：无标记时 reason 不得含"限流"。）
    """
    s = _svc()
    plain = s.freshness()
    s.rate_limited = True
    limited = s.freshness()
    assert plain.state == limited.state == "unavailable"
    assert "限流" not in (plain.reason or "")
    assert "限流" in (limited.reason or "")


# ---------------------------------------------------------------- ③ 冷启动首轮错峰（OPS-001）

#: 为什么钉「延迟」而不是「轮内降速」（2026-09-14 实测，见 kb/03 KB-ENG-83 补记）：
#: 探针 `concurrency=1 + 批间隔 0.15s`（≈2.7 请求/秒）在**第 32 页**被封，而生产
#: 全速那轮（`concurrency=6`、无间隔，≈10+ 请求/秒）成功 ⇒ **慢的反而先被封**
#: ⇒ 判据不是瞬时速率，而是**时间窗内的累计请求数**。该模型下轮内降速无效。


def test_run_postpones_first_refresh_by_first_delay(monkeypatch):
    """`first_delay > 0`：首轮**整体推后**，且**只在首轮**等一次。

    后半句是防「把 sleep 错放进 while 里」——那样每轮都等，轮询节奏被永久拖慢，
    而首轮错峰的目标只有一次。两件事共用一个参数，最容易在这里写错。
    """
    s = _svc()
    stamps: list[float] = []
    t0 = time.monotonic()

    async def _fake_refresh():
        stamps.append(time.monotonic() - t0)

    monkeypatch.setattr(s, "refresh", _fake_refresh)
    # 让循环快速转：否则默认休市要等 IDLE_INTERVAL_SECONDS(240s)，测不到第二轮
    monkeypatch.setattr(s, "_next_delay", lambda *, live: 0.05)

    async def _main():
        task = asyncio.create_task(s.run(first_delay=0.3))
        await asyncio.sleep(0.15)
        assert stamps == [], f"首轮应仍在延迟中，实际已抓：{stamps}"
        await asyncio.sleep(0.75)  # 越过 0.3s 延迟 + 若干轮
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_main())

    assert stamps, "延迟结束后仍未抓取"
    assert stamps[0] >= 0.28, f"首轮提前于 first_delay 执行：{stamps[0]:.3f}s"
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(gaps) >= 1, f"未观察到第二轮，测不出「只延迟一次」：{stamps}"
    assert all(g < 0.2 for g in gaps), f"延迟被放进循环（每轮都等）：间隔 {gaps}"


def test_run_without_first_delay_refreshes_immediately(monkeypatch):
    """成对判据：默认 `0.0` = **既有行为不变**（既有调用方与测试不受影响）。"""
    s = _svc()
    stamps: list[float] = []
    t0 = time.monotonic()

    async def _fake_refresh():
        stamps.append(time.monotonic() - t0)

    monkeypatch.setattr(s, "refresh", _fake_refresh)
    monkeypatch.setattr(s, "_next_delay", lambda *, live: 0.05)

    async def _main():
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_main())
    assert stamps, "默认未抓取"
    assert stamps[0] < 0.1, f"默认应立即抓取，实际首轮在 {stamps[0]:.3f}s"


def test_assembly_wires_cold_start_delay_into_snapshot_task():
    """**装配层结构臂**（逐字比对，最高优先级；见 [[KB-ENG-87]]）。

    `run()` 新增了 `first_delay`，但装配层若没把它接上，**功能等于不存在** ——
    而上面两条 `run()` 行为用例**仍然全绿**（[[KB-ENG-65]]「判据失效却全绿」同族）。
    这类空洞只有静态钉得住。

    ⚠️ **2026-09-15（`IMP-027` 装配重构）扫描面迁移**：调度器声明从 `app/main.py`
    搬到 `app/bootstrap/schedulers.py` ⇒ 旧判据扫 `main.py` 会**判据失效**（找不到
    该调用，报"未登记"假红；若改成 `is not None` 跳过则变成静默失效）。
    此处把扫描面从**单文件**改为**装配层白名单**（`app/main.py` + `app/bootstrap/*.py`）：
    搬家不失效，且下次在装配层内部再拆分也仍然生效。
    **判据强度一字未降**——断言、口径、失败文案全部保留，只换了"去哪找"。
    """
    from app import main as main_mod

    assert main_mod is not None  # 装配层入口必须存在（防白名单写空导致恒真）

    import app.bootstrap.schedulers as sched_mod  # noqa: F401  （扫描面之一，同时验证可导入）

    targets = [Path(main_mod.__file__)]
    targets += sorted(Path(sched_mod.__file__).parent.glob("*.py"))

    call = None
    for target in targets:
        tree = ast.parse(target.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "add"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant) \
                    and node.args[0].value == "market-snapshot":
                call = node
                break
        if call is not None:
            break

    assert call is not None, "装配层未登记 market-snapshot 任务（改名了？）"
    unparsed = ast.unparse(call)
    assert "first_delay" in unparsed, (
        f"market-snapshot 任务未传 first_delay ⇒ 冷启动错峰未接线：{unparsed}"
    )
    assert sched_mod.COLD_START_SNAPSHOT_DELAY_SECONDS > 0, (
        "COLD_START_SNAPSHOT_DELAY_SECONDS 非正数 ⇒ 错峰等于没做"
    )


# ---------------------------------------------------------------- ④ 成因披露

_AMOUNT = 1.2345e11


def _with_service(client, svc):
    """临时顶替 `app.state.snapshot_service`；返回还原函数（module 级 fixture
    共享进程状态，改动必须还原——见 conftest::client 的说明）。"""
    prev = getattr(client.app.state, "snapshot_service", None)
    client.app.state.snapshot_service = svc
    return lambda: setattr(client.app.state, "snapshot_service", prev)


def test_overview_exposes_reason_when_snapshot_not_ready(client):
    """`total_amount = None` 时**必须**同时给出成因。

    没有成因，前端就只能渲染 `--`（= 缺失），把"尚未就绪"与"真的没有"
    压成同一种显示——用户无法分辨"在加载"还是"坏了"（本次报障的直接原因）。
    """
    svc = _svc()
    svc.rate_limited = True
    restore = _with_service(client, svc)
    try:
        data = client.get("/api/market/overview").json()["data"]
    finally:
        restore()

    assert data["total_amount"] is None
    f = data["total_amount_freshness"]
    assert f is not None, "未就绪时不得省略成因字段"
    assert f["state"] == "unavailable"
    assert "限流" in (f["reason"] or "")


def test_overview_freshness_is_ready_when_snapshot_present(client):
    """成对的另一侧：有数据时 state 必须是 `ready`（防止把"总是 unavailable"写死）。"""
    svc = _svc()
    rows = [{"symbol": "600519", "name": "贵州茅台", "price": 10.0,
             "change_pct": 1.0, "amount": _AMOUNT}]
    svc.snapshot = rows
    svc.breadth = compute_breadth(rows)
    svc.last_success = datetime.now(timezone.utc)
    restore = _with_service(client, svc)
    try:
        data = client.get("/api/market/overview").json()["data"]
    finally:
        restore()

    assert data["total_amount"] == _AMOUNT
    assert data["total_amount_freshness"]["state"] == "ready"


def test_overview_amount_matches_breadth_total(client):
    """口径一致性：overview 的成交额必须与 breadth 的 `total_amount` 同源同值。

    （2026-09-01 曾因"把沪深300/中证1000 也加进去"而虚高 40%+，
    这条钉住"两处都走全市场快照求和"。）
    """
    svc = _svc()
    rows = [
        {"symbol": "600519", "name": "贵州茅台", "price": 10.0, "change_pct": 1.0, "amount": 5.0e10},
        {"symbol": "000001", "name": "平安银行", "price": 20.0, "change_pct": -1.0, "amount": 7.345e10},
    ]
    svc.snapshot = rows
    svc.breadth = compute_breadth(rows)
    svc.last_success = datetime.now(timezone.utc)
    restore = _with_service(client, svc)
    try:
        data = client.get("/api/market/overview").json()["data"]
        breadth = client.get("/api/market/breadth").json()["data"]["breadth"]
    finally:
        restore()

    assert data["total_amount"] == round(sum(r["amount"] for r in rows), 2)
    assert data["total_amount"] == round(breadth["total_amount"], 2)

# ---------------------------------------------------------------- ④ 新浪限流时腾讯降级快照


def _base_rows(n: int = 2) -> list[dict]:
    return [
        {
            "symbol": f"{600001 + i:06d}", "name": f"S{i}", "market": "SH",
            "price": 9.0, "open": 8.8, "high": 9.2, "low": 8.7,
            "prev_close": 8.9, "change": 0.1, "change_pct": 1.12,
            "volume": 1000.0, "amount": 9000.0, "turnover_rate": 1.0,
            "mktcap": 10000.0, "nmc": 8000.0, "ticktime": "old",
            "source": "sina_market",
        }
        for i in range(n)
    ]


def _quote(symbol: str, pct: float = 2.0) -> Quote:
    return Quote(
        symbol=symbol, name="Q" + symbol[-2:], market="SH", price=10.0,
        open=9.5, high=10.2, low=9.4, prev_close=9.8, change=0.2,
        change_pct=pct, volume=2000.0, amount=20000.0, turnover_rate=2.0,
        total_mktcap_yi=2.0, float_mktcap_yi=1.5,
        data_timestamp=datetime.now(timezone.utc), source="tencent",
    )


def test_fallback_refresh_uses_memory_universe_and_stays_degraded(monkeypatch, tmp_path):
    from app.services import quote_enrich

    svc = MarketSnapshotService(60.0, 300.0, tmp_path, quote_hub=object())
    svc.snapshot = _base_rows()
    svc._snapshot_version = (datetime.now(timezone.utc), tuple(dict(x) for x in svc.snapshot))
    svc.breadth = compute_breadth(svc.snapshot)
    svc.rate_limited = True
    svc.consecutive_failures = 1
    svc.last_error = "sina 456"
    monkeypatch.setattr(svc, "_maybe_save", lambda: None)

    async def _fake(_hub, symbols, **_kwargs):
        return {symbol: _quote(symbol) for symbol in symbols}

    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", _fake)
    coverage = asyncio.run(svc.refresh_fallback())
    assert coverage == 1.0
    assert svc.rate_limited is True and svc.consecutive_failures == 1
    assert svc.last_snapshot_source == "quote_fallback"
    assert svc.last_fallback_coverage == 1.0
    assert svc.snapshot[0]["price"] == 10.0
    assert svc.snapshot[0]["mktcap"] == 20000.0  # 2 亿元 -> 2 万万元
    fresh = svc.freshness()
    assert fresh.state == "degraded"
    assert fresh.source == "quote_fallback"
    assert "批量行情备源" in (fresh.reason or "")


def test_fallback_can_bootstrap_universe_from_latest_parquet(monkeypatch, tmp_path):
    import polars as pl
    from app.services import quote_enrich

    day = tmp_path / "snapshots" / "20260921"
    day.mkdir(parents=True)
    pl.DataFrame(_base_rows()).write_parquet(day / "120000.parquet")
    svc = MarketSnapshotService(60.0, 300.0, tmp_path, quote_hub=object())
    monkeypatch.setattr(svc, "_maybe_save", lambda: None)

    async def _fake(_hub, symbols, **_kwargs):
        assert symbols == ["600001", "600002"]
        return {symbol: _quote(symbol) for symbol in symbols}

    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", _fake)
    assert asyncio.run(svc.refresh_fallback()) == 1.0
    assert len(svc.snapshot) == 2
    assert svc.breadth and svc.breadth["total"] == 2


def test_fallback_rejects_low_coverage_without_publishing(monkeypatch, tmp_path):
    from app.services import quote_enrich

    svc = MarketSnapshotService(60.0, 300.0, tmp_path, quote_hub=object())
    original = _base_rows(10)
    svc.snapshot = [dict(x) for x in original]
    svc._snapshot_version = (datetime.now(timezone.utc), tuple(dict(x) for x in original))

    async def _fake(_hub, symbols, **_kwargs):
        return {symbol: _quote(symbol) for symbol in symbols[:8]}  # 80% < 90%

    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", _fake)
    with pytest.raises(RuntimeError, match="coverage"):
        asyncio.run(svc.refresh_fallback())
    assert svc.snapshot == original
    assert svc.last_snapshot_source is None


def test_fallback_rejects_stale_quotes_from_coverage_in_live_window(monkeypatch, tmp_path):
    from app.services import quote_enrich

    svc = MarketSnapshotService(60.0, 300.0, tmp_path, quote_hub=object())
    original = _base_rows(10)
    svc.snapshot = [dict(x) for x in original]
    svc._snapshot_version = (datetime.now(timezone.utc), tuple(dict(x) for x in original))
    monkeypatch.setattr(snapshot_service, "in_trading_window", lambda: True)

    async def _fake(_hub, symbols, **_kwargs):
        out = {symbol: _quote(symbol) for symbol in symbols}
        for symbol in symbols[-2:]:
            out[symbol].data_timestamp = datetime.now(timezone.utc) - timedelta(seconds=181)
        return out

    monkeypatch.setattr(quote_enrich, "fetch_quotes_batched", _fake)
    with pytest.raises(RuntimeError, match="coverage"):
        asyncio.run(svc.refresh_fallback())
    assert svc.snapshot == original


def test_fallback_missing_quote_clears_dynamic_fields():
    now = datetime.now(timezone.utc)
    row = MarketSnapshotService._fallback_row(_base_rows(1)[0], None, now=now)
    for key in ("price", "open", "high", "low", "prev_close", "change",
                "change_pct", "volume", "amount", "turnover_rate", "ticktime"):
        assert row[key] is None
    assert row["name"] == "S0"
    assert row["source"] == "quote_fallback:missing"


def test_rate_limit_run_invokes_fallback_but_preserves_primary_cooldown(monkeypatch):
    svc = _svc()
    calls: list[object] = []

    async def _primary():
        raise SinaRateLimited("sina 456")

    async def _fallback():
        calls.append("fallback")
        return 1.0

    async def _wait(delay):
        calls.append(delay)
        raise asyncio.CancelledError

    monkeypatch.setattr(svc, "refresh", _primary)
    monkeypatch.setattr(svc, "refresh_fallback", _fallback)
    monkeypatch.setattr(svc, "_wait_rate_limit_with_fallback", _wait)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(svc.run())
    assert svc.rate_limited is True and svc.consecutive_failures == 1
    assert calls == ["fallback", 240.0]


def test_rate_limit_wait_runs_fallback_between_primary_probes(monkeypatch):
    svc = _svc()
    svc.save_interval = 300.0
    sleeps: list[float] = []
    fallbacks: list[int] = []

    async def _sleep(delay):
        sleeps.append(delay)

    async def _fallback():
        fallbacks.append(1)
        return 1.0

    monkeypatch.setattr(snapshot_service.asyncio, "sleep", _sleep)
    monkeypatch.setattr(svc, "refresh_fallback", _fallback)
    asyncio.run(svc._wait_rate_limit_with_fallback(900.0))
    assert sleeps == [300.0, 300.0, 300.0]
    assert len(fallbacks) == 2


def test_fallback_save_persists_degraded_durable_metadata(tmp_path):
    svc = MarketSnapshotService(60.0, 60.0, tmp_path)
    now = datetime.now(timezone.utc)
    svc.snapshot = _base_rows()
    svc.breadth = compute_breadth(svc.snapshot)
    svc.last_success = now
    svc._snapshot_version = (now, tuple(dict(x) for x in svc.snapshot))
    svc.rate_limited = True
    svc.consecutive_failures = 1
    svc.last_snapshot_source = "quote_fallback"
    svc.last_degraded_reason = "fallback-test"
    svc._maybe_save()

    assert svc.saved_files == 1
    assert svc.last_saved_path is not None and svc.last_saved_path.exists()
    assert svc.last_saved_as_of == now
    assert svc.last_saved_state == "degraded"
    assert svc.last_saved_source == "quote_fallback"
    assert svc.last_saved_reason == "fallback-test"


def test_parquet_save_failure_is_structured_and_visible(monkeypatch, tmp_path):
    from app.services import parquet_store

    svc = MarketSnapshotService(60.0, 60.0, tmp_path)
    now = datetime.now(timezone.utc)
    svc.snapshot = _base_rows()
    svc.breadth = compute_breadth(svc.snapshot)
    svc.last_success = now
    svc._snapshot_version = (now, tuple(dict(x) for x in svc.snapshot))

    def _boom(*_args, **_kwargs):
        raise OSError("disk-full-test")

    monkeypatch.setattr(parquet_store, "write_parquet_atomic", _boom)
    svc._maybe_save()
    payload = svc.breadth_payload()
    assert svc.saved_files == 0
    assert payload["consecutive_save_failures"] == 1
    assert payload["last_save_error"] == "disk-full-test"


def test_fallback_staleness_is_not_masked_as_degraded():
    svc = _svc()
    svc.breadth = {"total": 1}
    svc.last_success = datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=300)
    svc.last_snapshot_source = "quote_fallback"
    svc.last_degraded_reason = "fallback"
    svc.consecutive_failures = 2
    assert svc.freshness().state == "stale"


def test_bootstrap_injects_quote_hub_into_snapshot_service():
    import ast
    from app.bootstrap import services

    tree = ast.parse(Path(services.__file__).read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "MarketSnapshotService"
    ]
    assert len(calls) == 1
    kw = {item.arg: ast.unparse(item.value) for item in calls[0].keywords}
    assert kw.get("quote_hub") == "hub"
