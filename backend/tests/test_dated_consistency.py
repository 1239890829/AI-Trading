"""**日期轴一致性**守卫（F6，2026-09-14）。

钉住的是一类**已实测闭证**的缺陷：**「业务日期」与「数据新鲜度」两条轴各说各话**。

## 背景（2026-09-14 用户报障「盘面板块的全部数据还是十一号的」）

`services/market_snapshot.default_trade_date` 与 `api/routes/market._prev_trade_date_async`
**各自建了一份 24 小时**的日历缓存挂在 hub 上（`cache_on(hub, "provider.trading_days",
86400)`；`cache_on` 以 `(holder, name)` 为键，且**首次调用传入的 TTL 生效**，两处各写
各的 TTL 是无效的）。而源头日历是**尾随窗口、不含未来日期**（09-13 22:28 抓 ⇒ 末日
09-11）。于是进程在前夜填充的列表，次日盘中仍在有效期内 ⇒ **整个交易日**都返回上一
交易日，`default_trade_date` 的 **14 个调用点**集体拿到旧日期。

同族缺陷还有第二处：`api/routes/market` 里 `data`（业务日期 09-11）与 `meta`
（Hub 实时健康度）**没有任何一致性校验**，于是同一响应并存
`data.trade_date=09-11` + `meta.is_realtime=true` + `freshness.state="ready"` +
`age_seconds=0.9` —— 过期数据拿到了最强的实时背书，正是红线 2
「禁止把过期缓存冒充实盘」要禁止的形态。

## 三条不变量

| 守卫 | 不变量 | 驱动 |
|---|---|---|
| G1 | 跨相位一致：日期必须**跟随日历推进**，不得被进程级缓存钉住 | 同一 hub 两次调用（前夜 → 次日盘中） |
| G2 | 不得早于「日历覆盖的最近交易日」：盘中**精确相等**，盘前只许退**一天** | 定点时钟 10:00 / 08:00 |
| G3 | meta 与 dated payload **绑定**：日期落后 ⇒ 不得出现实时背书 | 新鲜/落后两个日期各取一次 meta |

⚠️ **每条都做了「回退即红」的注入验证**，并在各用例 docstring 中写明注入法与实测结果。
"""
from __future__ import annotations

import asyncio
import time
from datetime import date

import pytest

from app.api.routes import market_envelope
from app.core.bjtime import BJ_TZ
from app.core.freshness import Freshness
from app.market import trade_calendar as tc
from app.services import market_snapshot


def _run(coro):
    return asyncio.run(coro)


class _Mono:
    """可推进的**单调时钟**（只用于 `tc.time.monotonic`）。

    为什么必须推进时钟、而不是把 `_last_attempt_mono` 清零：后者是**手改被测状态**，
    会把实现自己写下的限频位覆盖掉 —— 于是「重取被永久限频」这类缺陷照样全绿
    （同族教训见 `test_degradation_contracts._Clock` 的 docstring）。

    真实事故里 `_REFRESH_MIN_INTERVAL_SEC=300` 早已走完（前夜 22:28 → 次日 10:28
    相距 12 小时），所以驱动它必须让单调钟一起走过这段距离。
    """

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def monotonic(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _bj(year: int, month: int, day: int, hour: int, minute: int):
    from datetime import datetime

    return datetime(year, month, day, hour, minute, tzinfo=BJ_TZ)


#: 尾随日历的两个相位：前夜末日停在 09-11（周五），次日盘中已含 09-14（周一）。
#: 天数必须 ≥ `_MIN_DAYS`(5)，否则会掉进指数 K 线备源分支、测不到官方源路径。
_TAIL = [date(2026, 9, d) for d in (7, 8, 9, 10, 11)]
_FULL = _TAIL + [date(2026, 9, 14)]

_TODAY_0913 = date(2026, 9, 13)
_TODAY_0914 = date(2026, 9, 14)


class _TailProvider:
    """日历源桩：**第 1 次**返回尾随列表，之后返回已含 09-14 的列表。

    ⚠️ 计数必须放在桩里（KB-ENG-65 ㈠「桩缺方法不是守卫」同族）：若只返回固定列表，
    「第二次调用到底有没有重取日历」就无从判定，守卫会退化成恒真。
    """

    name = "stub-cal"
    realtime = True

    def __init__(self) -> None:
        self.calls = 0

    async def get_trading_days(self):
        self.calls += 1
        days = _TAIL if self.calls == 1 else _FULL
        return [d.strftime("%Y%m%d") for d in days]


class _FixedProvider:
    """恒定日历源（口径类守卫用，不关心重取次数）。"""

    name = "stub-cal"
    realtime = True

    def __init__(self, days: list[date], realtime: bool = True) -> None:
        self.days = list(days)
        self.realtime = realtime

    async def get_trading_days(self):
        return [d.strftime("%Y%m%d") for d in self.days]


class _Hub:
    """最小 hub 桩：`meta_payload()` 只用 provider.name / provider.realtime / is_stale() /
    last_success_refresh，且刻意走 `getattr` 回退（见 `hub_freshness` docstring）。"""

    def __init__(self, provider, *, stale: bool = False) -> None:
        self.provider = provider
        self.last_success_refresh = None
        self.stock_codes: list[str] = []
        self._stale = stale

    def is_stale(self) -> bool:
        return self._stale

    def freshness(self) -> Freshness:
        if self._stale:
            return Freshness.stale(reason="桩：行情链陈旧", source=self.provider.name)
        return Freshness.ready(source=self.provider.name, age_seconds=0.9)


@pytest.fixture()
def clean_calendar(monkeypatch, tmp_path):
    """清空日历进程内缓存，并把持久化兜底文件隔离到 tmp（**不碰** 真实 backend/data）。"""
    tc.invalidate_cache()
    monkeypatch.setattr(tc, "_PERSIST_PATH", tmp_path / "trade_calendar.json")
    monkeypatch.setattr(tc, "_persisted_cache", None)
    yield
    tc.invalidate_cache()


def _pin(monkeypatch, today: date, now) -> None:
    """把「日历侧」「服务侧」「信封侧」三处时钟一起钉死 —— 钉一半会得到看似随机的失败。

    `market_envelope`（2026-09-15 IMP-005 第三批抽出的共享信封模块）是**第三个**
    读时钟的层：`latest_trade_date()` 用的是**它自己命名空间**的 `beijing_today`。
    漏钉这一处，patch 会打在空气上、断言改绑真实运行日 —— 正是本函数首版踩过的
    「钉住外部时钟要钉到真正读时钟的那一层」。
    """
    monkeypatch.setattr(tc, "beijing_now", lambda: now)
    monkeypatch.setattr(tc, "beijing_today", lambda: today)
    monkeypatch.setattr(market_snapshot, "beijing_now", lambda: now)
    monkeypatch.setattr(market_snapshot, "beijing_today", lambda: today)
    monkeypatch.setattr(market_envelope, "beijing_today", lambda: today)


# ---------------------------------------------------------------- G1 跨相位一致


def test_default_trade_date_tracks_calendar_across_phases(monkeypatch, clean_calendar):
    """G1 · 同一 hub 从「前夜」到「次日盘中」，日期必须跟着日历走。

    *回退即红*：把 `default_trade_date` 还原成旧实现（自建
    `cache_on(hub, "provider.trading_days", 86400)`）后，第二次调用命中 24h 缓存
    ⇒ 返回 09-11 而断言要求 09-14 ⇒ **本用例精确变红**（注入实测：红；复原：绿）。

    同时断言 `provider.calls >= 2`：即使两相位取值不同，也必须**真的重取过**日历
    —— 挡住"碰巧返回了不同值"的实现（KB-ENG-65 ㊁：等价对照钉不住共用的错误判据）。
    """
    provider = _TailProvider()
    hub = _Hub(provider)
    mono = _Mono()
    monkeypatch.setattr(tc, "time", mono)

    _pin(monkeypatch, _TODAY_0913, _bj(2026, 9, 13, 22, 28))
    first = _run(market_snapshot.default_trade_date(hub))
    assert first == date(2026, 9, 11), f"前夜应取尾随日历末日 09-11，实际 {first}"
    assert provider.calls == 1

    # 前夜 → 次日盘中：真实相距 12 小时，单调钟必须同步走过（见 _Mono docstring）
    mono.advance(12 * 3600)
    _pin(monkeypatch, _TODAY_0914, _bj(2026, 9, 14, 10, 28))
    second = _run(market_snapshot.default_trade_date(hub))
    assert provider.calls >= 2, (
        "第二次调用没有重取日历 —— 缓存判据没有看「是否覆盖今天」"
        f"（源只被调用 {provider.calls} 次）"
    )
    assert second == date(2026, 9, 14), (
        f"日期未跟随日历推进：{second}（应为 09-14）—— 进程级缓存把日期钉在了旧快照上"
    )


# ---------------------------------------------------------------- G2 绝对口径


def test_default_trade_date_never_earlier_than_calendar(monkeypatch, clean_calendar):
    """G2 · 盘中**精确等于**日历上 ≤ 今天的最后一个交易日；盘前只许退**一天**。

    为什么需要这条独立守卫（而不只靠 G1）：G1 钉的是「跨相位是否推进」，一条
    **恒定早一天**的实现同样能满足"两相位不同值"。本用例直接断言**绝对取值**，
    不留「两路一起错」的空间（KB-ENG-65 ㊁）。

    *回退即红*：把盘前回溯条件从「`now < 09:15` 且 `latest == today`」放宽成
    「始终回溯」⇒ 第一条断言（盘中须为 09-14）变红；改成「从不回溯」⇒ 第二条
    （盘前须为 09-11）变红。注入实测：两种改法各自精确命中一条。
    """
    provider = _FixedProvider(_FULL)
    hub = _Hub(provider)

    # ── 盘中 10:00：当日池已形成 ⇒ 必须精确等于日历末日（09-14）
    _pin(monkeypatch, _TODAY_0914, _bj(2026, 9, 14, 10, 0))
    got = _run(market_snapshot.default_trade_date(hub))
    expected = tc.last_trade_date(_FULL, _TODAY_0914)
    assert got == expected == date(2026, 9, 14), (
        f"盘中日期 {got} 不等于日历末日 {expected}（不得早于覆盖日）"
    )

    # ── 盘前 08:00：当日涨停池/龙虎榜尚未形成 ⇒ 只许回溯**一个**交易日，不得更早
    tc.invalidate_cache()
    _pin(monkeypatch, _TODAY_0914, _bj(2026, 9, 14, 8, 0))
    got = _run(market_snapshot.default_trade_date(hub))
    assert got == tc.prev_trade_date(_FULL, _TODAY_0914) == date(2026, 9, 11), (
        f"盘前日期 {got} 既不是「退一天」（09-11）也不是当日（09-14）—— 口径漂移"
    )


# ---------------------------------------------------------------- G3 meta 绑定


def test_dated_meta_degrades_when_payload_date_lags(monkeypatch, clean_calendar):
    """G3 · 红线 2：`trade_date` 落后时**不得**出现 `is_realtime=true` / `ready`。

    结构是「先证基线新鲜、再证落后被降级」—— 只断言后者会退化成恒真
    （把 `is_realtime` 写成常量 False 也能通过），所以必须同时钉住**不降级**那一侧
    （三态纪律：既不许把过期当实时，也不许把实时当过期）。

    *回退即红*：把 `market.py` 中 4 处 `await dated_meta(hub, trade_date)` 改回
    `meta_payload(hub)`，或删掉 `dated_meta` 的降级分支 ⇒ 第 2 段断言
    `stale["is_realtime"] is False` 变红（注入实测：红；复原：绿）。

    ⚠️ 2026-09-15 IMP-005 第三批：`dated_meta` / `latest_trade_date` / `meta_payload`
    已从 `market.py` 抽到 **`market_envelope`** 且**去掉下划线**（跨分片共享的辅助
    必须是公开名 —— 路由间禁止 import 私有名）。本用例因此改指**实现所在模块**：
    patch `market_route` 命名空间会打在空气上（那里的同名名字只是 import 进来的引用）。
    """
    hub = _Hub(_FixedProvider(_FULL))
    _pin(monkeypatch, _TODAY_0914, _bj(2026, 9, 14, 10, 0))

    # ① 基线：数据日期 == 最近交易日 ⇒ 原样返回，不得过度降级
    fresh = _run(market_envelope.dated_meta(hub, _TODAY_0914))
    assert fresh["is_realtime"] is True, "新鲜载荷被误降级（把实时当过期，同样是失真）"
    assert fresh["is_stale"] is False
    assert (fresh.get("freshness") or {}).get("state") == "ready"

    # ② 落后一天：必须降级，且给出实际/应有两个日期（不隐藏落后）
    stale = _run(market_envelope.dated_meta(hub, date(2026, 9, 11)))
    assert stale["is_realtime"] is False, (
        "过期载荷拿到了实时背书（红线 2：禁止把过期缓存冒充实盘）"
    )
    assert stale["is_stale"] is True
    assert (stale.get("freshness") or {}).get("state") != "ready"
    assert stale["data_date"] == "2026-09-11"
    assert stale["expected_date"] == "2026-09-14"


# ---------------------------------------------------------------- 辅助判据自证


def test_is_trade_day_on_is_three_state(monkeypatch, clean_calendar):
    """`is_trade_day_on` 的三态自证 —— 它同时是 F5 闸门的判据本体。

    *回退即红*：去掉 `if d.weekday() >= 5: return False` 这一行（改为等日历覆盖）
    ⇒ 第 ① 段由 `False` 变 `None`，本用例变红。**这条不是装饰**：周末单独判而不等
    日历，是为了让 `unknown` 只在**真正不确定**时出现 —— 尾随日历在周末永远不覆盖
    今天，若只走「覆盖才判」，周末就会被无端降级成 `None`。
    """
    monkeypatch.setattr(tc, "beijing_today", lambda: _TODAY_0914)

    # ① 日历不可用（清了缓存且无持久化文件）+ 周末 ⇒ 仍是**确定**的 False
    tc.invalidate_cache()
    _pin(monkeypatch, _TODAY_0914, _bj(2026, 9, 14, 10, 0))
    assert tc.is_trade_day_on(date(2026, 9, 12)) is False, "周六必须确定为非交易日，不得降级成 unknown"

    # ② 日历不可用 + 工作日 ⇒ None（未判定），**不得**当 False 用
    assert tc.is_trade_day_on(_TODAY_0914) is None, "日历不可用时工作日必须是 unknown，不得断言休市"

    # ③ 日历未覆盖今天（尾随源末日 09-11）+ 今天 09-14 ⇒ None
    tc._cached_days = list(_TAIL)  # 直接种入「未覆盖今天」的缓存（模拟尾随源）
    tc._cached_at = time.monotonic()
    tc._cached_at_wall = _bj(2026, 9, 12, 10, 0)
    assert tc.is_trade_day_on(_TODAY_0914) is None, "日历未覆盖今天时不得断言今天非交易日"
    assert tc.is_trade_day_on(date(2026, 9, 11)) is True
    assert tc.is_trade_day_on(date(2026, 9, 10)) is True
