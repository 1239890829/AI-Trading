"""S1-4 可见性出口：涨跌停价探针（`services/data_health_loop.limit_price_probe`）。

背景：S1-4 把「缺限价」从**静默放行**改成**拒单**——红线 5 的正确严口径，
但它把一次上游故障放大成"全站下不了单"。探针让这种放大**可见**，
否则系统会自认"一切正常、今天没有信号"。
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.core.scheduler import SchedulerRegistry
from app.schemas.market import Quote
from app.services.data_health_loop import limit_price_probe, scheduler_probe


class _FakeHub:
    def __init__(self, provider):
        self.provider = provider


class _FakeState:
    def __init__(self, provider=None, *, with_hub: bool = True):
        if with_hub:
            self.hub = _FakeHub(provider)


class _FakeProvider:
    name = "fake"

    def __init__(self, *, quote=None, exc=None):
        self._quote = quote
        self._exc = exc

    async def get_quote(self, symbol):
        if self._exc is not None:
            raise self._exc
        return self._quote


def _run(coro):
    return asyncio.run(coro)


def test_no_hub_is_not_an_issue():
    """未装配行情链（精简启动/单测）不得误报。"""
    assert _run(limit_price_probe(_FakeState(with_hub=False))) is None


def test_ready_limits_yield_no_issue():
    q = Quote(symbol="600519", price=100.0, limit_up_price=110.0, limit_down_price=90.0, source="t")
    assert _run(limit_price_probe(_FakeState(_FakeProvider(quote=q)))) is None


def test_unavailable_limits_yield_stable_issue():
    q = Quote(symbol="600519", price=100.0, source="t")
    issue = _run(limit_price_probe(_FakeState(_FakeProvider(quote=q))))
    assert issue is not None and "涨跌停价不可用" in issue
    # 同一状态两次探测文案必须**逐字相同**——否则哨兵的字符串去重会被打穿，
    # 退化成每 15 分钟推一次飞书。
    assert issue == _run(limit_price_probe(_FakeState(_FakeProvider(quote=q))))


def test_partial_limits_yield_issue():
    q = Quote(symbol="600519", price=100.0, limit_up_price=110.0, source="t")
    issue = _run(limit_price_probe(_FakeState(_FakeProvider(quote=q))))
    assert issue is not None and "partial" in issue


def test_missing_quote_yields_issue():
    issue = _run(limit_price_probe(_FakeState(_FakeProvider(quote=None))))
    assert issue is not None and "取不到行情" in issue


def test_provider_exception_reports_class_name_only():
    """只用异常类名：完整消息每轮可能不同（含耗时/行号），会把去重打穿。"""
    issue = _run(limit_price_probe(_FakeState(_FakeProvider(exc=RuntimeError("upstream 500 @ 12:03")))))
    assert issue == "涨跌停价探针失败：RuntimeError"


# ------------------------------------------------- S2-2 调度器死亡探针（同一出口）
def test_scheduler_probe_without_registry_is_not_an_issue():
    """未装配注册表（精简启动/单测）不得误报。"""
    assert scheduler_probe(_FakeState(with_hub=False)) is None


def test_scheduler_probe_clean_registry_returns_none():
    async def scenario():
        reg = SchedulerRegistry()
        reg.add("ok", lambda: asyncio.sleep(3600))
        await reg.start()
        state = _FakeState(with_hub=False)
        state.schedulers = reg
        assert scheduler_probe(state) is None
        await reg.shutdown(grace=0.05)

    _run(scenario())


def test_scheduler_probe_reports_dead_names_with_stable_text():
    """文案只含任务名（稳定值）：含数量/时间戳会打穿 AnomalyPushGuard 的字符串去重。"""

    async def scenario():
        reg = SchedulerRegistry()

        async def boom() -> None:
            raise RuntimeError("dead")

        reg.add("fragile", boom, restart=False)
        await reg.start()
        await asyncio.sleep(0.05)
        state = _FakeState(with_hub=False)
        state.schedulers = reg
        issue = scheduler_probe(state)
        assert issue is not None and "fragile" in issue
        assert issue == scheduler_probe(state)  # 逐字稳定
        await reg.shutdown()

    _run(scenario())


# ------------------------------------------------- S2-2 收尾：常驻循环必须能被 stop 立即唤醒
def test_data_health_loop_returns_promptly_on_stop(monkeypatch):
    """停机不得靠 force cancel。

    `_INTERVAL_SECONDS` = 900：裸 `asyncio.sleep` 时 stop 事件只在**下一轮循环开头**
    才被看到，于是每次停机都要烧满宽限窗口并被强制 cancel——实测日志
    「data-health-sentinel 超过 10s 未退出，强制 cancel」。本用例断言的是
    「stop 一 set 就退」（2s 上限远小于 900s），裸 sleep 会直接超时失败。
    """
    from app.market import trade_calendar
    from app.services import data_health_loop as mod

    # 盘外分支：跳过探测体，只考「睡眠是否可被打断」
    monkeypatch.setattr(trade_calendar, "in_trading_window", lambda *a, **k: False)

    async def scenario():
        # ① 非空断言：没有 stop 时它必须还在睡——否则 ② 可能是"循环自己退了"的空转
        idle = asyncio.create_task(
            mod.data_health_loop(_FakeState(with_hub=False), asyncio.Event())
        )
        done, _ = await asyncio.wait({idle}, timeout=0.3)
        assert not done, "未收到 stop 却在 0.3s 内自行退出（用例退化为空转）"
        idle.cancel()
        await asyncio.gather(idle, return_exceptions=True)

        # ② stop 一 set 即退
        stop = asyncio.Event()
        task = asyncio.create_task(
            mod.data_health_loop(_FakeState(with_hub=False), stop)
        )
        await asyncio.sleep(0.05)   # 确保已进入睡眠点
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

    _run(scenario())
