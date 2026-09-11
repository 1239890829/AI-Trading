"""事件循环阻塞守卫：同步 IO 函数不得被 async 上下文**直接**调用。

## 这一类问题的形状（为什么必须用源码级守卫）

`await` 只让出控制权，**同步调用不会**。一个含 DuckDB 查询 / SQLite 全表读 /
网络请求的同步函数，若被 async 函数直接调用，整个事件循环（含 QuoteHub 的
1s 行情 WS 推送）会在这段时间里停摆。症状**只有行情卡顿**——测试全绿、类型
全对、日志无异常，常规手段抓不到。故用源码级断言把调用点锁住。

判定口径是**"调用方是否在事件循环上"**，不是"被调方是不是同步"：
`minute_backfill.backfill()` / `backfill_tdx()` 是 async 且直接调
`write_parquet_atomic`（同步磁盘写），但它们是**离线回填工具**、不在常驻循环，
写 parquet 属毫秒级（同循环里的 `await fetch_*` 是秒级网络请求，把这几毫秒
摊开得不明显）⇒ **2026-09-11 已核实、判定不改**。若日后它们被接进常驻调度，
需重新判定并加进下表。

⚠️ **反向陷阱**：若某函数本身是被 `to_thread` 调起的（如 `fetch_tdx_minutes`），
它内部**不该**再包 `to_thread`——那是在 worker 线程里再开线程。本守卫只断言
"async 调用点必须包"，不要求同步函数内部自包。

## 已收口的调用点

| 调用点 | 同步函数 | 阻塞源 | 触发场景 |
| --- | --- | --- | --- |
| `evolution.evolution_scheduler` | `conclude_due` | SQLite 全表读 × 每条到期实验 | 每日 16:00 后 |
| `evolution.evolution_scheduler` | `evaluate_and_promote_shadow` | DuckDB 1027 万行 + LEAD 窗口 | 每日 15:00 后 |
| `evolution.generate_agenda` | `collect_inputs` | DuckDB + 多路 SQLite + 磁盘 | 15:45 议程 / 手动触发 |
| `data_health_loop.data_health_loop` | `_collect_data_health` | DuckDB + 多路 SQLite + 磁盘 | **交易时段每 15 分钟** |

严重度与"触发时是否在交易时段"强相关：`data_health_loop` 是唯一常驻跑在
**交易时段**的一条，优先级最高；`conclude_due` 无 DuckDB，最低。

## 两道断言的分工

1. **正向**：`to_thread(<fn>)` 必须出现 —— 证明这个调用点被包了；
2. **反向**：不得存在**未被包**的 `<fn>(` 调用 —— 抓"同一个函数还有另一处漏包"。

只有正向会漏掉后者：有人在相邻分支照抄一行 `results = conclude_due()`，
正向断言照样绿。两条一起才闭合。

反向检测是**上下文感知**的（`_bare_calls`）：逐个找 `<fn>(` 的出现，
只看**同一行前缀**——含 `to_thread(` 的算已包、`def` / `async def` 开头的算定义，
其余一律判为裸调用。不用正则 lookbehind，因为它处理不了 `async def`（宽度不同）
和跨行写法。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

#: (相对 backend/ 的路径, 必须出现的 to_thread 片段, 函数名, 阻塞源说明)
GUARDED: list[tuple[str, str, str, str]] = [
    (
        "app/services/evolution.py",
        "asyncio.to_thread(conclude_due)",
        "conclude_due",
        "到期实验裁决：内部 SQLite 全表读（DailyPickReview/DailyPickSet，且每条到期实验各读一遍）",
    ),
    (
        "app/services/evolution.py",
        "asyncio.to_thread(evaluate_and_promote_shadow)",
        "evaluate_and_promote_shadow",
        "影子队列评估：内部 DuckDB 全表查询（1027 万行 + LEAD 窗口函数）",
    ),
    (
        "app/services/evolution.py",
        "asyncio.to_thread(collect_inputs",
        "collect_inputs",
        "议程证据汇总：内部 _collect_data_health 走 duckdb.connect + 多路 SQLite 全表读",
    ),
    (
        "app/services/data_health_loop.py",
        "asyncio.to_thread(_collect_data_health",
        "_collect_data_health",
        "数据健康哨兵：duckdb.connect(market.duckdb) 的 MAX(date_ms) + 多路 SQLite 读",
    ),
]

_IDS = [f"{Path(g[0]).stem}::{g[2]}" for g in GUARDED]


def _bare_calls(text: str, fn: str) -> list[str]:
    """返回文件里**未被 to_thread 包住**的 `fn(` 调用所在行（含定义行则跳过）。"""
    out: list[str] = []
    for m in re.finditer(re.escape(fn) + r"\(", text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.start())
        line = text[line_start:line_end if line_end != -1 else len(text)]
        prefix = line[: m.start() - line_start]
        if "to_thread(" in prefix:          # 同行已包
            continue
        # 注意用**整行** strip 后判断：`prefix.strip()` 会把 "def " 的尾空格一并
        # 吃掉，导致 `def fn(` 被判成裸调用（2026-09-11 首版即踩）。
        if line.strip().startswith(("def ", "async def ")):  # 是定义
            continue
        out.append(line.strip())
    return out


@pytest.mark.parametrize("rel,needle,fn,why", GUARDED, ids=_IDS)
def test_sync_io_calls_are_offloaded(rel: str, needle: str, fn: str, why: str) -> None:
    """调用点必须走 `asyncio.to_thread`，且不得另有裸同步调用。

    失败时先看是不是**改名**了：改了名就把本表的 needle/fn 一起更新，
    不要为了让测试变绿而删掉 to_thread。
    """
    path = BACKEND / rel
    assert path.exists(), f"{rel} 不存在（改路径了？同步更新本清单）"
    text = path.read_text(encoding="utf-8")
    assert needle in text, (
        f"{rel} 找不到 `{needle}` ⇒ 该调用点未走 to_thread，"
        f"同步调用会阻塞事件循环。阻塞源：{why}"
    )
    bare = _bare_calls(text, fn)
    assert not bare, (
        f"{rel} 存在未被 to_thread 包裹的 `{fn}(` 调用（正向断言抓不到这种）：\n  "
        + "\n  ".join(bare)
        + f"\n阻塞源：{why}"
    )
