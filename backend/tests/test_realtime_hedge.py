"""P1-A 秒级方法对冲并发组测试（composite._call_hedged）。

方案 B（realtime-broker-feasibility §3.2）：主源宽限 HEDGE_DELAY 后备源并行起跑。
验证：正常路径备源零流量、快速失败串行下沉、主源挂起备源接手（含放弃记账）、
全失败抛错、冷却源不进并发组、realtime_rank 决定秒级链位次。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.data_providers import composite as composite_mod
from app.data_providers.composite import CompositeProvider
from app.data_providers.eastmoney import ProviderError


class Stub:
    """可编程延迟源：delay 后返回 result 或抛 exc；被 cancel 时记录。"""

    realtime = True

    def __init__(self, name: str, delay: float = 0.0, result=None, exc: Exception | None = None):
        self.name = name
        if name == "tencent":
            self.realtime_rank = 0
        self.delay = delay
        self.result = result
        self.exc = exc
        self.calls = 0
        self.cancelled = False

    async def get_quotes(self, symbols):
        self.calls += 1
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.exc is not None:
            raise self.exc
        return self.result

    async def get_kline(self, *args):
        return []


@pytest.fixture(autouse=True)
def _fast_hedge(monkeypatch):
    monkeypatch.setattr(composite_mod, "HEDGE_DELAY", 0.02)


def test_primary_answers_within_grace_backup_not_called():
    """主源在宽限内应答：直接用主源，备源零流量（正常路径与串行一致）。"""
    primary = Stub("tencent", delay=0.005, result=["q1"])
    backup = Stub("sina", delay=0.005, result=["q2"])
    comp = CompositeProvider([primary, backup])
    out = asyncio.run(comp.get_quotes(["600519"]))
    assert out == ["q1"]
    assert backup.calls == 0
    assert comp.provider_health()["last_good"]["get_quotes"] == "tencent"


def test_primary_fails_fast_serial_fallback():
    """主源快速失败：记账后串行走备源（主源活着，无须并发）。"""
    primary = Stub("tencent", delay=0.005, exc=ProviderError("boom"))
    backup = Stub("sina", delay=0.005, result=["q2"])
    comp = CompositeProvider([primary, backup])
    out = asyncio.run(comp.get_quotes(["600519"]))
    assert out == ["q2"]
    assert comp.provider_health()["last_good"]["get_quotes"] == "sina"
    assert comp._failures[("get_quotes", "tencent")] == 1


def test_primary_hangs_backup_takes_over_and_abandons_primary():
    """主源超宽限：备源并行接手，主源被 cancel 并记一次失败（连续 3 次进熔断）。"""
    primary = Stub("tencent", delay=5.0, result=["q1"])  # 挂起，远超宽限
    backup = Stub("sina", delay=0.005, result=["q2"])
    comp = CompositeProvider([primary, backup])
    out = asyncio.run(comp.get_quotes(["600519"]))
    assert out == ["q2"]
    assert primary.calls == 1 and primary.cancelled
    assert comp._failures[("get_quotes", "tencent")] == 1
    assert comp.provider_health()["last_good"]["get_quotes"] == "sina"


def test_hang_then_empty_falls_through_to_third():
    """主源挂起 + 备源空结果：继续串行到第三源，空结果同样记账。"""
    primary = Stub("tencent", delay=5.0, result=["q1"])
    backup = Stub("sina", delay=0.005, result=[])  # 空结果
    third = Stub("eastmoney", delay=0.005, result=["q3"])
    comp = CompositeProvider([primary, backup, third])
    out = asyncio.run(comp.get_quotes(["600519"]))
    assert out == ["q3"]
    assert comp._failures[("get_quotes", "sina")] == 1
    assert comp.provider_health()["last_good"]["get_quotes"] == "eastmoney"


def test_all_fail_raises_provider_error():
    primary = Stub("tencent", delay=0.005, exc=ProviderError("t down"))
    backup = Stub("sina", delay=0.005, exc=ProviderError("s down"))
    comp = CompositeProvider([primary, backup])
    with pytest.raises(ProviderError) as ei:
        asyncio.run(comp.get_quotes(["600519"]))
    assert "t down" in str(ei.value) and "s down" in str(ei.value)


def test_cooling_source_never_enters_hedge_pair():
    """备源冷却中：不进并发组，失败后串行落到第三源而非冷却源。"""
    primary = Stub("tencent", delay=5.0, result=["q1"])  # 挂起触发对冲
    backup = Stub("sina", delay=0.005, result=["q2"])
    third = Stub("eastmoney", delay=0.005, result=["q3"])
    comp = CompositeProvider([primary, backup, third])
    comp._cooldown_until[("get_quotes", "sina")] = time.monotonic() + 60  # 白盒：新浪冷却中
    out = asyncio.run(comp.get_quotes(["600519"]))
    assert out == ["q3"]
    assert backup.calls == 0


def test_realtime_rank_orders_chain():
    """秒级链位次：腾讯(0)→新浪(1)→东财(2)→其余(缺省 100 沉底)；非实时方法不动。"""
    ths = Stub("ths")
    tencent = Stub("tencent")
    eastmoney = Stub("eastmoney")
    eastmoney.realtime_rank = 2
    sina = Stub("sina")
    sina.realtime_rank = 1
    comp = CompositeProvider([ths, tencent, eastmoney, sina])
    assert [p.name for p in comp._pick("get_quotes")] == ["tencent", "sina", "eastmoney", "ths"]
    assert [p.name for p in comp._pick("get_kline")] == ["ths", "tencent", "eastmoney", "sina"]
