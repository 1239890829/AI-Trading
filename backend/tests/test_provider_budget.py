"""S2-3 请求级预算：`CompositeProvider._call` 的 failover **硬上界**。

背景（2026-09-11 账本 §6.5）：四源串行各自带 HTTP 超时（腾讯/东财/新浪 5s、ths 8s），
但**没有总预算**——全挂时单个请求悬停 20~30s（= 各源超时之和）。而 FastAPI 侧没有
请求超时，前端只能干等，后端连接/事件循环也被占住。

契约（本文件锁住的就是这五条）：
1. 全链挂起 → 在预算内抛 `ProviderError`，且文案**显式**说明预算耗尽（不伪装成普通失败）；
2. 预算不得伤害健康路径——正常调用照旧返回，不多一次等待；
3. 超出剩余预算的源按**失败**记账（进熔断），不是无限挂账；
4. 秒级方法用更紧的预算（Hub 以 1Hz 轮询这些方法，等不起）；
5. 预算值对外可观测（`provider_health()`），不是藏在代码里的魔法数。

⚠️ 预算**只**作用于通用路由路径（`_call` → `_call_serial` / `_call_hedged`）。
`search` / `get_limit_down_pool` 刻意不接：它们的"空结果 = 合法语义"（无匹配 / 0 家跌停）
必须建立在**问完所有源**之上，预算提前中断会把"没问完"变成假的"搜不到 / 没有跌停"。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.data_providers import composite as composite_mod
from app.data_providers.composite import CompositeProvider
from app.data_providers.eastmoney import ProviderError


class Stub:
    """可编程源：`delay` 秒后返回 `result` 或抛 `exc`。"""

    realtime = True

    def __init__(self, name: str, *, delay: float = 0.0, result=None, exc=None):
        self.name = name
        self.delay = delay
        self.result = result if result is not None else f"{name}-ok"
        self.exc = exc
        self.calls = 0
        #: 每次调用**实际被给到的时长**（被 `wait_for` 掐断也记）——
        #: 「预算是总量还是每源配额」只能靠这个量判，见 BUG-007 的修法说明。
        self.granted_seconds: list[float] = []

    async def _run(self):
        self.calls += 1
        t0 = time.monotonic()
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.exc is not None:
                raise self.exc
            return self.result
        finally:
            self.granted_seconds.append(time.monotonic() - t0)

    async def get_kline(self, *args):  # 非秒级：走默认预算
        return await self._run()

    async def get_quotes(self, symbols):  # 秒级：走紧预算
        return await self._run()


@pytest.fixture(autouse=True)
def _small_budget(monkeypatch):
    """把预算压到 0.15s/0.1s：测的是"预算生效"这件事，不是真实取值。"""
    monkeypatch.setattr(composite_mod, "REQUEST_BUDGET_SECONDS", 0.15)
    monkeypatch.setattr(composite_mod, "REALTIME_BUDGET_SECONDS", 0.1)


def test_exhausted_budget_raises_with_explicit_reason():
    """全链挂起：预算内抛错，且文案点明"预算"，不混同于普通上游失败。"""
    stubs = [Stub(f"s{i}", delay=30.0) for i in range(4)]
    comp = CompositeProvider(stubs)

    t0 = time.monotonic()
    with pytest.raises(ProviderError) as ei:
        asyncio.run(comp.get_kline("600519", "day"))
    elapsed = time.monotonic() - t0

    msg = str(ei.value)
    assert "请求预算" in msg, msg
    assert elapsed < 1.0, f"预算失效：耗时 {elapsed:.2f}s（各源 30s 挂起）"
    # 只问了前两个源就必须放弃——不能把四个源的空转全跑完
    assert sum(s.calls for s in stubs) <= 2, [s.calls for s in stubs]


def test_timeout_is_recorded_as_failure_for_breaker():
    """被预算掐掉的源要记失败（连续 3 次进熔断），不能无限挂账。"""
    stubs = [Stub(f"s{i}", delay=30.0) for i in range(3)]
    comp = CompositeProvider(stubs)

    with pytest.raises(ProviderError):
        asyncio.run(comp.get_kline("600519", "day"))

    assert comp._failures.get(("get_kline", "s0")) == 1
    assert comp.provider_health()["breakers"]["get_kline@s0"]["state"] == "watch"


def test_budget_does_not_harm_healthy_path():
    """健康路径：首源快速失败 → 次源正常返回，不得因预算多等或误判。"""
    bad = Stub("s0", exc=ProviderError("down"))
    good = Stub("s1")
    comp = CompositeProvider([bad, good])

    t0 = time.monotonic()
    out = asyncio.run(comp.get_kline("600519", "day"))
    elapsed = time.monotonic() - t0

    assert out == "s1-ok"
    assert elapsed < 0.1
    assert comp.provider_health()["last_good"]["get_kline"] == "s1"
    assert comp._failures.get(("get_kline", "s0")) == 1


def test_remaining_budget_is_shared_across_sources(monkeypatch):
    """预算是**总量**而非每源配额：第二个源只能拿到**残值**，拿不到一份新配额。

    ⚠️ **2026-09-15 修（账本 `BUG-007`）**：原判据「总耗时 < 0.3s」+ 0.15s 预算在满载下
    **会偶发假红** —— s0 只吃 0.1s（余量仅 1.5×），调度抖动一大，预算提前耗尽，
    s1 就被 `_attempt` 判「请求预算已耗尽（未发起）」而**根本不被调用**（`hang.calls == 0`）。
    根因 = **判据把"墙钟"当成了精确量**（真因经读实现确认：`_attempt` 在 `left <= 0` 时直接返回，
    且 `_call_serial` 在 `time.monotonic() >= deadline` 时 `break`）。
    ⚠️ 诚实声明：**人为 8×/24× 忙循环负载 16 次均未能复现**，故本修法依据是机制推理 +
    余量分析，**不是"复现—修复"对照**（见账本 §6.40）。

    现判据对两种合法结局都成立，且**两者都只在"共享预算"下可能发生**：
    · s1 被发起 ⇒ 它被给到的时长应 ≈ `预算 − s0 实际用时`（**残值**），而非一份完整预算；
    · s1 被跳过 ⇒ 报错文案必须是「未发起」（预算已被 s0 吃光）。
    ⇒ **每源配额**的实现里，s1 必然拿到一份**完整预算**（`granted ≈ budget`），判据即红。
    另留 5× 余量（预算 0.5s / s0 吃 0.1s），使"跳过"分支在日常与 CI 上极少走到。
    """
    budget = 0.5
    monkeypatch.setattr(composite_mod, "REQUEST_BUDGET_SECONDS", budget)
    eaten = 0.1  # s0 吃掉的时长 = 预算的 1/5（余量 5×）

    slow_fail = Stub("s0", delay=eaten, exc=ProviderError("down"))
    hang = Stub("s1", delay=30.0)
    comp = CompositeProvider([slow_fail, hang])

    t0 = time.monotonic()
    with pytest.raises(ProviderError) as ei:
        asyncio.run(comp.get_kline("600519", "day"))
    elapsed = time.monotonic() - t0

    assert slow_fail.calls == 1
    assert elapsed < budget * 2, f"总耗时 {elapsed:.2f}s —— 像是每源各发了一份预算"
    assert comp._failures.get(("get_kline", "s0")) == 1

    if hang.calls:
        # 分支 ①：s1 被发起 ⇒ 拿到的是**残值**（≈budget−eaten），不是一份新配额
        granted = hang.granted_seconds[-1]
        assert 0 < granted < budget * 0.95, (
            f"s1 被给到 {granted:.2f}s（预算 {budget:.2f}s）—— "
            "≥一份完整预算 ⇒ 预算没有跨源共享"
        )
        assert comp._failures.get(("get_kline", "s1")) == 1  # 被预算掐断同样记失败
    else:
        # 分支 ②：预算已被 s0 吃光 ⇒ 文案必须点明"未发起"（而不是悄悄少问一个源）
        assert "未发起" in str(ei.value), str(ei.value)
        # 没发起过请求不是它的责任，**不应**记失败（否则会误伤熔断统计）
        assert comp._failures.get(("get_kline", "s1")) is None


def test_realtime_method_uses_tighter_budget():
    """秒级方法（Hub 1Hz）必须比低频方法更快放弃。"""
    comp = CompositeProvider([Stub("s0", delay=30.0), Stub("s1", delay=30.0)])

    assert comp.budget_for("get_quotes") == composite_mod.REALTIME_BUDGET_SECONDS
    assert comp.budget_for("get_kline") == composite_mod.REQUEST_BUDGET_SECONDS
    assert composite_mod.REALTIME_BUDGET_SECONDS < composite_mod.REQUEST_BUDGET_SECONDS

    t0 = time.monotonic()
    with pytest.raises(ProviderError):
        asyncio.run(comp.get_quotes(["600519"]))
    assert time.monotonic() - t0 < 1.0


def test_provider_health_exposes_budget():
    """预算值必须可观测，否则调参与故障归因只能靠读代码。"""
    comp = CompositeProvider([Stub("s0")])
    budgets = comp.provider_health()["budget_seconds"]
    assert budgets == {
        "realtime": composite_mod.REALTIME_BUDGET_SECONDS,
        "default": composite_mod.REQUEST_BUDGET_SECONDS,
    }
