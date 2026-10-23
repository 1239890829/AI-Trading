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

    async def get_order_book(self, symbol):
        return await self.get_quotes([symbol])


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
    """主源挂起 + 备源空结果：按原链轮换第三源，空结果同样记账。"""
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


@pytest.mark.parametrize('method', ['get_quotes', 'get_order_book'])
@pytest.mark.parametrize('backup_result', ['error', 'empty'])
def test_failed_backup_cannot_abandon_a_pending_healthy_primary(method, backup_result):
    async def run():
        gate, backup_failed = asyncio.Event(), asyncio.Event()
        class Primary(Stub):
            async def get_quotes(self, symbols):
                self.calls += 1
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
                return ['original-primary']
        class Backup(Stub):
            async def get_quotes(self, symbols):
                self.calls += 1
                backup_failed.set()
                if backup_result == 'error': raise ProviderError('offline backup failed')
                return []
        primary, backup = Primary('tencent'), Backup('sina')
        comp = CompositeProvider([primary, backup])
        request = asyncio.create_task(getattr(comp, method)(['600519'] if method == 'get_quotes' else '600519'))
        try:
            await asyncio.wait_for(backup_failed.wait(), 1)
            for _ in range(10): await asyncio.sleep(0)
            assert not request.done(), 'backup failure must leave the original primary alive within its deadline'
            gate.set()
            assert await request == ['original-primary']
        finally:
            gate.set()
            await asyncio.gather(request, return_exceptions=True)
        assert primary.calls == backup.calls == 1 and not primary.cancelled
        assert comp._failures.get((method, 'tencent'), 0) == 0
        assert comp._failures[(method, 'sina')] == 1
        assert comp.provider_health()['last_good'][method] == 'tencent'
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


def test_failed_backup_rotates_original_tail_without_starving_pending_primary():
    async def run():
        primary_gate, third_started = asyncio.Event(), asyncio.Event()
        class Primary(Stub):
            async def get_quotes(self, symbols):
                self.calls += 1
                try: await primary_gate.wait()
                except asyncio.CancelledError: self.cancelled = True; raise
                return ['original-primary']
        class Third(Stub):
            async def get_quotes(self, symbols):
                self.calls += 1; third_started.set()
                await asyncio.Event().wait()
        primary = Primary('tencent'); backup = Stub('sina', exc=ProviderError('offline failure'))
        third = Third('eastmoney'); comp = CompositeProvider([primary, backup, third])
        request = asyncio.create_task(comp.get_quotes(['600519']))
        try:
            await asyncio.wait_for(third_started.wait(), 1)
            assert not primary.cancelled
            primary_gate.set()
            assert await request == ['original-primary']
        finally:
            primary_gate.set(); await asyncio.gather(request, return_exceptions=True)
        assert primary.calls == backup.calls == third.calls == 1
        assert not primary.cancelled and asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


@pytest.mark.parametrize('late', ['swallow_timeout', 'block_loop'])
def test_source_return_after_original_deadline_cannot_be_adopted(late):
    async def run():
        class Late(Stub):
            async def get_quotes(self, symbols):
                self.calls += 1
                if late == 'block_loop':
                    time.sleep(0.03)  # Intentional isolated loop-blocking counterexample.
                else:
                    try: await asyncio.Event().wait()
                    except asyncio.CancelledError: pass
                return ['too-late']
        source = Late('tencent'); comp = CompositeProvider([source])
        _, result, error = await comp._attempt('get_quotes', source, (['600519'],), time.monotonic() + 0.01)
        assert result is None and isinstance(error, composite_mod.BudgetExhausted)
        counts = comp.provider_health()['method_attempts']['get_quotes@tencent']
        assert counts['method_returned'] == 0 and counts['method_budget_exhausted'] == 1
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


def test_cancel_owner_drains_primary_and_rotated_backup():
    async def run():
        third_started = asyncio.Event()

        class Hanging(Stub):
            async def get_quotes(self, symbols):
                self.calls += 1
                if self.name == 'eastmoney':
                    third_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        primary = Hanging('tencent')
        backup = Stub('sina', exc=ProviderError('backup failed'))
        third = Hanging('eastmoney')
        comp = CompositeProvider([primary, backup, third])
        request = asyncio.create_task(comp.get_quotes(['600519']))
        try:
            await asyncio.wait_for(third_started.wait(), 1)
        finally:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        assert primary.cancelled and third.cancelled
        assert primary.calls == backup.calls == third.calls == 1
        assert comp._failures[('get_quotes', 'sina')] == 1
        assert comp._failures.get(('get_quotes', 'tencent'), 0) == 0
        assert 'get_quotes' not in comp.provider_health()['last_good']
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())


def test_primary_failure_after_grace_keeps_pending_backup():
    async def run():
        backup_started, primary_failed, release_backup = (
            asyncio.Event(), asyncio.Event(), asyncio.Event()
        )

        class Primary(Stub):
            async def get_quotes(self, symbols):
                self.calls += 1
                await backup_started.wait()
                primary_failed.set()
                raise ProviderError('primary failed after grace')

        class Backup(Stub):
            async def get_quotes(self, symbols):
                self.calls += 1
                backup_started.set()
                await release_backup.wait()
                return ['backup']

        primary, backup, third = Primary('tencent'), Backup('sina'), Stub('eastmoney')
        comp = CompositeProvider([primary, backup, third])
        request = asyncio.create_task(comp.get_quotes(['600519']))
        try:
            await asyncio.wait_for(primary_failed.wait(), 1)
            assert not request.done()
            release_backup.set()
            assert await request == ['backup']
        finally:
            release_backup.set()
            await asyncio.gather(request, return_exceptions=True)
        assert primary.calls == backup.calls == 1 and third.calls == 0
        assert comp._failures[('get_quotes', 'tencent')] == 1
        assert comp.provider_health()['last_good']['get_quotes'] == 'sina'
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())


def test_both_pending_attempts_exhaust_one_original_deadline_without_starting_tail():
    async def run():
        primary = Stub('tencent', delay=1, result=['late primary'])
        backup = Stub('sina', delay=1, result=['late backup'])
        third = Stub('eastmoney', result=['unstarted'])
        comp = CompositeProvider([primary, backup, third])
        deadline = time.monotonic() + 0.06
        with pytest.raises(ProviderError) as error:
            await comp._call_hedged('get_quotes', (['600519'],), [primary, backup, third], [], deadline)
        assert 'tencent' in str(error.value) and 'sina' in str(error.value)
        assert primary.calls == backup.calls == 1 and third.calls == 0
        assert primary.cancelled and backup.cancelled
        counts = comp.provider_health()['method_attempts']
        for source in ['tencent', 'sina']:
            assert counts[f'get_quotes@{source}']['method_budget_exhausted'] == 1
            assert comp._failures[('get_quotes', source)] == 1
        assert counts['get_quotes@eastmoney']['method_started'] == 0
        assert 'get_quotes' not in comp.provider_health()['last_good']
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())


@pytest.mark.parametrize('late_outcome', ['success', 'exception'])
def test_abandoned_primary_is_drained_without_overwriting_backup_winner(late_outcome):
    async def run():
        drained = asyncio.Event()

        class LatePrimary(Stub):
            async def get_quotes(self, symbols):
                self.calls += 1
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    await asyncio.sleep(0)
                    drained.set()
                    if late_outcome == 'exception':
                        raise ProviderError('late loser exception')
                    return ['late loser success']

        primary = LatePrimary('tencent')
        backup = Stub('sina', result=['backup winner'])
        comp = CompositeProvider([primary, backup])
        assert await comp.get_quotes(['600519']) == ['backup winner']
        assert drained.is_set() and primary.cancelled
        assert primary.calls == backup.calls == 1
        assert comp._failures[('get_quotes', 'tencent')] == 1
        assert comp.provider_health()['last_good']['get_quotes'] == 'sina'
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())


def test_same_completion_batch_records_backup_failure_before_primary_wins():
    async def run():
        gate, backup_started = asyncio.Event(), asyncio.Event()

        class Primary(Stub):
            async def get_quotes(self, symbols):
                self.calls += 1
                await gate.wait()
                return ['primary']

        class Backup(Stub):
            async def get_quotes(self, symbols):
                self.calls += 1
                backup_started.set()
                await gate.wait()
                raise ProviderError('same-batch backup failure')

        primary, backup = Primary('tencent'), Backup('sina')
        comp = CompositeProvider([primary, backup])
        request = asyncio.create_task(comp.get_quotes(['600519']))
        try:
            await asyncio.wait_for(backup_started.wait(), 1)
            gate.set()
            assert await request == ['primary']
        finally:
            gate.set()
            await asyncio.gather(request, return_exceptions=True)
        assert comp._failures.get(('get_quotes', 'tencent'), 0) == 0
        assert comp._failures[('get_quotes', 'sina')] == 1
        assert comp.provider_health()['last_good']['get_quotes'] == 'tencent'
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())
