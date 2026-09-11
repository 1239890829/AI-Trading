"""S2-2 收尾：带 `stop` 的常驻循环必须**能被 stop 立即唤醒**。

背景（2026-09-11 实测）：循环里写裸 `asyncio.sleep(间隔)` 时，`stop` 只在**下一轮开头**
才被看到 ⇒ 每次停机白烧一个宽限窗口再被强制 cancel。当日实测日志：

    lifespan shutdown: data-health-sentinel 超过 10s 未退出，强制 cancel
    lifespan shutdown: pre-limit-radar 超过 10s 未退出，强制 cancel
    lifespan shutdown: position-monitor 超过 10s 未退出，强制 cancel

三次停机共约 20s 纯浪费，且强 cancel 可能打断正在写的动作。修法是统一改
`app.core.scheduler.wait_or_stop(stop, 间隔)`；本文件把这个行为固化下来。

为什么必须单独测：这类缺陷**不报错、不影响功能**，只在停机日志里留一行 WARNING，
靠人工翻日志才能发现（回归测试是唯一的自动护栏）。
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.picks.exit_engine import position_loop
from app.picks.pre_limit_radar import pre_limit_loop

# 远小于被测循环的睡眠间隔（15s / 30s / 300s），裸 sleep 必然超时
_STOP_BUDGET_S = 2.0


def _run(coro):
    return asyncio.run(coro)


async def _assert_stops_promptly(loop_factory, *, idle_probe: float = 0.3) -> None:
    """① 未 set stop 时不得自行退出（防空转）② set 后必须立刻退。"""
    idle = asyncio.create_task(loop_factory(asyncio.Event()))
    done, _ = await asyncio.wait({idle}, timeout=idle_probe)
    assert not done, "未收到 stop 却在观察窗内自行退出（用例退化为空转）"
    idle.cancel()
    await asyncio.gather(idle, return_exceptions=True)

    stop = asyncio.Event()
    task = asyncio.create_task(loop_factory(stop))
    await asyncio.sleep(0.05)  # 确保已进入睡眠点
    stop.set()
    await asyncio.wait_for(task, timeout=_STOP_BUDGET_S)


def test_pre_limit_loop_stops_promptly(monkeypatch):
    from app.picks import pre_limit_radar

    # 盘外分支：跳过 sweep（不发请求），只考「睡眠是否可被打断」
    monkeypatch.setattr(pre_limit_radar, "radar_active_now", lambda *a, **k: False)
    _run(_assert_stops_promptly(lambda stop: pre_limit_loop(None, stop)))


def test_position_loop_stops_promptly(monkeypatch):
    from app.picks import pre_limit_radar

    monkeypatch.setattr(pre_limit_radar, "radar_active_now", lambda *a, **k: False)
    _run(_assert_stops_promptly(lambda stop: position_loop(None, stop)))


def test_llm_aux_loop_startup_delay_is_interruptible():
    """启动先等 300s 让目录/快讯就绪——这 300s 也必须可打断。

    该等待在循环体**之外**，最容易被漏掉：源码里 `asyncio.sleep(300)` 与
    `wait_for(stop.wait(), ...)` 只隔几行，但停机时它先被执行，
    于是「主循环可中断」并不能让停机变快。
    """
    from app.events.llm_aux import llm_aux_loop

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(llm_aux_loop(None, stop=stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=_STOP_BUDGET_S)

    _run(scenario())
