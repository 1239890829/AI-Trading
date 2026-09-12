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
| `picks_pipeline.candidate_pool` | `store.list_events` / `svc.get_catalog` / `svc.member_symbols_bulk` | 同步 SQLite（事件行 + 1000 条题材目录 + 30 题材成分） | `POST /api/picks/generate` 手动触发时可落在**盘中** |
| `picks_pipeline.generate_picks_pipeline` | `store.list_events` / `_prev_combo_symbols` / `metric_history.percentile_of_value` | 同步 SQLite + 整份情绪历史文件读 | 调度 15:00+ / 手动触发 |
| `picks_pipeline.deep_score_candidates` | `svc.official_for_symbols_bulk` | 同步 SQLite（2 次批量查询） | 同上 |

严重度与"触发时是否在交易时段"强相关：`data_health_loop` 是唯一**常驻**跑在
交易时段的一条，优先级最高；pipeline 三条只在**手动触发**时才可能落在盘中
（调度路径在 15:00 之后）；`conclude_due` 无 DuckDB，最低。

## 已审计、**刻意不搬**的同步调用（G-2 的结论，别再"顺手"改）

判据不是"是不是同步"，而是"**搬了是否真的更好**"。下面两处是 2026-09-12 评审批次 5
逐处核对后的结论：

1. **`watcher.dispatch_alert` 里的 `brief_for_today` / `append_alert`（磁盘读写）——
   刻意留在循环上。** `append_alert` 是**读-改-写整个 JSON 简报文件**；同步执行时，
   从"读"到"写"之间没有 `await` ⇒ 对事件循环而言是**一次原子操作**。把它丢进线程池
   反而**制造**并发：两条 alert 同时 dispatch 时，两个 worker 线程会交错完成
   `读→改→写`，后写者覆盖前写者（丢提醒）——除非另加锁，那是更大的改动。
   ⚠️ **反直觉点：「同步」不等于「该搬去线程」**——同步的 read-modify-write 在单线程
   事件循环上是**天然安全**的，搬进线程池才是引入竞态。
2. **`watcher.dispatch_alert` 的 `ensure_system_rule` / `record_sighting`（单条小查询
   / 单行插入）——判定不改**：毫秒级、且 dispatch 频率远低于 1s 行情节奏，
   与下一条先例同族。

先例：`minute_backfill.backfill()` / `backfill_tdx()` 是 async 且直接调
`write_parquet_atomic`（同步磁盘写），但它们是**离线回填工具**、不在常驻循环，
写 parquet 属毫秒级（同循环里的 `await fetch_*` 是秒级网络请求，把这几毫秒
摊开得不明显）⇒ **2026-09-11 已核实、判定不改**。若日后它们被接进常驻调度，
需重新判定并加进下表。

## 两道断言的分工

1. **正向**：`to_thread(<fn>)` 必须出现 —— 证明这个调用点被包了；
2. **反向**：不得存在**未被包**的 `<fn>(` 调用 —— 抓"同一个函数还有另一处漏包"。

只有正向会漏掉后者：有人在相邻分支照抄一行 `results = conclude_due()`，
正向断言照样绿。两条一起才闭合。

反向检测是**上下文感知**的（`_bare_calls`）：逐个找 `<fn>(` 的出现，
只看**同一行前缀**——含 `to_thread(` 的算已包、`def` / `async def` 开头的算定义，
其余一律判为裸调用。不用正则 lookbehind，因为它处理不了 `async def`（宽度不同）
和跨行写法。

⚠️ **两种写法都要认**（G-2 新增那批用的是第二种）：
- `await asyncio.to_thread(fn, arg)` —— 语法上出现 `fn(`，反向断言看得到；
- `await asyncio.to_thread(obj.fn, arg)` / `to_thread(fn)` —— 传的是**函数引用**，
  文本里没有 `fn(`，反向断言**看不到这个调用点**（它只会在有人改回裸调用时命中）。
  这不是漏洞（正向断言照样钉住"必须包"），但读断言失败信息时要知道差别：
  第二种写法的条目**只有正向断言在起作用**。

**纯函数不必进表**：`_build_event_hits_index` 原本接 `store` 自己查库，2026-09-12
批次 5 改为接**事件行**（`list_events` 的结果由调用方预取一次、三处复用）⇒ 零 IO，
可安全留在循环上，**不需要**也不该给它包 `to_thread`（在 worker 线程里再开线程）。
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
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
    # ---- G-2（2026-09-12 评审批次 5）：把 pipeline 纳入守卫面 ----
    # 背景：管线由调度（15:00+）与 `POST /api/picks/generate`（手动，可在盘中）
    # **在事件循环上**直接 await，函数体内每一处同步 SQLite/磁盘读都会连着
    # QuoteHub 的秒级行情推送一起停摆。此前这 6 处都不在守卫覆盖内。
    (
        "app/services/picks_pipeline.py",
        "asyncio.to_thread(store.list_events, active_only=True, limit=30)",
        "list_events",
        "活跃事件（含 selectinload 方向行）的同步 SQLite 读——管线里**取数单点**，"
        "候选池/regime ev_texts/消息命中索引三处复用同一份（原先同查询跑三遍）",
    ),
    (
        "app/services/picks_pipeline.py",
        "asyncio.to_thread(svc.get_catalog, limit=1000)",
        "get_catalog",
        "题材目录同步 SQLite 读（最多 1000 条），供事件题材名→代码反查",
    ),
    (
        "app/services/picks_pipeline.py",
        "asyncio.to_thread(svc.member_symbols_bulk, wanted)",
        "member_symbols_bulk",
        "候选池题材成分批量查（同步 SQLite，每题材一个 session 的批量化版本）",
    ),
    (
        "app/services/picks_pipeline.py",
        "asyncio.to_thread(_prev_combo_symbols)",
        "_prev_combo_symbols",
        "昨日组合成员：同步 SQLite（DailyPickSet 最近一行）",
    ),
    (
        "app/services/picks_pipeline.py",
        'asyncio.to_thread(metric_history.percentile_of_value, "promo_1to2", promo_1to2)',
        "percentile_of_value",
        "情绪历史**整份文件读**（磁盘），promo/break 两个分位各一次——两次都在循环上",
    ),
    (
        "app/services/picks_pipeline.py",
        'asyncio.to_thread(svc.official_for_symbols_bulk, [c["symbol"] for c in deep])',
        "official_for_symbols_bulk",
        "逐候选「股票→官方题材」的批量反查（同步 SQLite 2 次查询，深评循环前一次）",
    ),
    (
        "app/services/picks_pipeline.py",
        "asyncio.to_thread(_persist_picks, today",
        "_persist_picks",
        "组合落库：同步 SQLite 读改写 + 两次大对象 json.dumps（管线末尾）",
    ),
]

_IDS = [f"{Path(g[0]).stem}::{g[2]}" for g in GUARDED]


def _mask_comments_and_strings(text: str) -> str:
    """把**注释与字符串字面量**（含 docstring）换成等长空白，只留代码骨架。

    为什么必须有这一步：本文件会在注释/docstring 里**点名**被守卫的调用
    （例如「同一条 `store.list_events(active_only=True, limit=30)` 跑了三遍」），
    纯文本扫描会把这段说明当成裸调用 —— 2026-09-12 加 pipeline 条目时**当场踩到**：
    新条目立刻假红，且失败信息断言"存在未被包裹的调用"，与事实不符
    （误报的失败信息比不报更糟：会引导后人去删掉那段正确的说明文字）。

    注释与字符串字面量**不可能发起调用**，剔掉它们不削弱守卫。用 `tokenize`
    而不是正则：需要正确跳过 `#` 出现在字符串里的情形，以及三引号跨行的 docstring。

    行号必须**逐个保留**（`_bare_calls` 靠行号定位并回报上下文）：单行 token 用等长
    空白原地替换；多行字符串的中间行整行置空但**保留换行符**，首尾行只清 token 覆盖
    的那一段。语法不完整（片段）时退回原文 —— 宁可严（可能误报）不可漏。
    """
    lines = text.splitlines(keepends=True)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError):
        return text
    for tok in toks:
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (r1, c1), (r2, c2) = tok.start, tok.end
        if r1 == r2:
            ln = lines[r1 - 1]
            lines[r1 - 1] = ln[:c1] + " " * (c2 - c1) + ln[c2:]
            continue
        lines[r1 - 1] = lines[r1 - 1][:c1] + "\n"   # 首行：清 token 起始处到行尾
        for r in range(r1 + 1, r2):                 # 中间行：整行置空（保留换行）
            lines[r - 1] = "\n"
        last = lines[r2 - 1]
        lines[r2 - 1] = " " * c2 + last[c2:]        # 末行：清 token 结束前的部分
    return "".join(lines)


def _async_context_lines(text: str) -> set[int] | None:
    """返回「处于 async 函数体内、且**不在其内部嵌套的同步 def 内**」的行号集合。

    为什么反向断言需要这个（2026-09-12 加路由层条目时踩到）：`_bare_calls` 原本是
    纯文本的，会把**同步函数体内**的调用也算成裸调用。而 `notifications._alert_items`
    是**同步**函数、内部有 `repo.list_events(limit=limit)` —— 那本身完全合法；
    真正的违规在**调用它的 async 端点**（`async def notifications` 直接 `_alert_items(...)`，
    没包 to_thread），是**另一层**的问题，文本扫描表达不了。
    ⇒ 判据收窄为「async 上下文里的裸调用」才既准又不会逼人删掉合法的同步实现。

    语法不完整时返回 `None`（调用方据此退回"不过滤"，宁可严不可漏）。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    async_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            async_ranges.append((node.lineno, node.end_lineno or node.lineno))
    if not async_ranges:
        return set()
    nested_sync: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not isinstance(node, ast.AsyncFunctionDef):
            end = node.end_lineno or node.lineno
            for a, b in async_ranges:
                if a < node.lineno and end <= b:      # 嵌套在某个 async 函数体内
                    nested_sync.append((node.lineno, end))
                    break
    lines: set[int] = set()
    for a, b in async_ranges:
        lines.update(range(a, b + 1))
    for a, b in nested_sync:
        lines.difference_update(range(a, b + 1))
    return lines


def _bare_calls(text: str, fn: str, *, async_only: bool = False) -> list[str]:
    """返回文件里**未被 to_thread 包住**的 `fn(` 调用所在行（含定义行则跳过）。

    只看代码骨架（`_mask_comments_and_strings`）——注释与 docstring 里提到该调用
    不算违规，否则"在注释里解释这条守卫"会把它自己弄红。

    `async_only=True`（路由层用）：只报**位于 async 函数体内**的裸调用。
    同步函数体内的调用是合法的（它自己不占事件循环），该层的问题是"谁调了它"，
    由调用点的条目负责。
    """
    masked = _mask_comments_and_strings(text)
    # ⚠️ 行号/列号一律在 **masked** 上算、回报时取 **原文** 的那一行：
    # 多行字符串的中间行被压成 `\n`，masked 的**偏移量与原文不同**（行数相同），
    # 拿 masked 的 offset 去原文切行会串行。故两套坐标分工使用。
    masked_lines = masked.splitlines()
    plain_lines = text.splitlines()
    async_lines = _async_context_lines(text) if async_only else None
    out: list[str] = []
    for m in re.finditer(re.escape(fn) + r"\(", masked):
        ln = masked.count("\n", 0, m.start())          # 行号（0-based）
        mline = masked_lines[ln] if ln < len(masked_lines) else ""
        mcol = m.start() - (masked.rfind("\n", 0, m.start()) + 1)
        prefix = mline[:mcol]
        if "to_thread(" in prefix:          # 同行已包（在代码骨架里判断才准：
            continue                        # 字符串里的 "to_thread(" 不算）
        if async_lines is not None and (ln + 1) not in async_lines:
            continue                        # 不在 async 上下文 ⇒ 这一层不归它管
        line = plain_lines[ln] if ln < len(plain_lines) else ""
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


# ---------------------------------------------------------------- 路由 / 事件层（2026-09-12）
#
# 背景：上面那张表只覆盖**常驻调度与管线**。`§6.13 中优`登记了一项「全仓 async 端点
# 同步 IO 扫面」，本轮把它做完并收口到本表。
#
# **范围是按实测定的，不是按"是不是同步"定的**（判据同 KB-ENG-67 三项）：
#
# | 调用 | 实测中位 | 处置 |
# | --- | --- | --- |
# | `EventStore.list_events(all, 2000)` | **83~85ms** | 搬（本表） |
# | `EventStore.list_events(active, 300)` | **21ms** | 搬 |
# | `EventStore.list_events(active, 100)` | **11ms** | 搬 |
# | `EventStore.list_events(all, 80)` | **9.6ms** | 搬 |
# | `EventStore.list_events(active, 30)` | **7.2ms** | 搬 |
# | `ThemeCatalogService.member_symbols_bulk(30)` | 4.9ms | 不搬（毫秒级） |
# | `ThemeCatalogService.get_catalog(1000)` | 1.6ms | 不搬 |
# | `notifications._alert_items`（rules + events 50） | 1.25ms | 不搬 |
# | `AgentTriage` 内联查询（alert.py，≤50 ids） | 0.76ms | 不搬 |
# | `ThemeCatalogService.get_members(code)` | 0.61ms | 不搬 |
# | `AlertRepository.list_events(50)` | **0.36ms** | 不搬 |
# | `agent_tasks.list_tasks(50)` | 0.53ms | 不搬 |
# | `WatchlistRepository.list_items()` | 0.14ms | 不搬 |
#
# ⚠️ **一个测量陷阱，如实记**：首版把 `/api/alerts/events` 记成 8.5ms，其实是**标签写错**
# ——那一行 lambda 调的是 `EventStore` 而不是 `AlertRepository`。重测后 `AlertRepository`
# 只有 0.36ms，据此**回退了**两处已写好的包裹（`alert.py` / `assistant.tools._t_alert_events`）。
# ⇒ 报耗时必须连同**被测对象**一起核对，标签与 lambda 不是一回事。
#
# ⚠️ **第二层（async 端点 → 同步 helper）也扫过**：`notifications._alert_items` /
# `_daily_pick_item`、`events._theme_names`（内含 `get_catalog`）、`market` 的情绪历史
# 系列 —— 逐项实测**全部 <5ms**，按上表同一条线判定不搬。故本层只登记 `EventStore.list_events`。

# ⚠️ **反向断言的接收者必须限定为 `store.list_events`（不能只写 `list_events`）**：
# 仓内另有 `AlertRepository.list_events`（实测 0.36ms，判定不搬）与
# `notifications._alert_items` 这类**同步 helper**里的调用。只写方法名会把它们一并
# 报成违规 —— 2026-09-12 首次运行时 `app/assistant/tools.py` 正是这样假红，
# 差点逼我去包一个 0.36ms 的调用。带接收者的字面量（`store.list_events(`）恰好只命中
# 真正被搬的那一族：**裸调用**才产生该 token，`to_thread(store.list_events, …)`
# 传的是函数引用、不含 `(`，因此天然不会自match。

#: (相对 backend/ 的路径, 必须出现的 to_thread 片段, 函数名, 阻塞源说明)
GUARDED_ROUTES: list[tuple[str, str, str, str]] = [
    (
        "app/api/routes/events.py",
        "asyncio.to_thread(store.list_events, active_only=active, limit=limit)",
        "store.list_events",
        "活跃事件列表（默认 30 条）——前端 30s 轮询的热端点，实测 7.2ms",
    ),
    (
        "app/api/routes/events.py",
        "asyncio.to_thread(store.list_events, active_only=True, limit=limit)",
        "store.list_events",
        "盘面相关性排序（limit≤200）——前端 30s 轮询，实测 11ms",
    ),
    (
        "app/api/routes/events.py",
        "asyncio.to_thread(store.list_events, active_only=True, limit=300)",
        "store.list_events",
        "个股关联事件（limit 固定 300）——个股详情热路径，实测 21ms",
    ),
    (
        "app/api/routes/events.py",
        "asyncio.to_thread(store.list_events, active_only=False, limit=2000)",
        "store.list_events",
        "方向回填扫描（limit 2000）——实测 83ms，本文件最重的同步阻塞",
    ),
    (
        "app/api/routes/events.py",
        "asyncio.to_thread(store.list_events, active_only=False, limit=2000)",
        "store.list_events",
        "题材焦点聚合（limit 2000）——实测 85ms",
    ),
    (
        "app/api/routes/notifications.py",
        "asyncio.to_thread(store.list_events, active_only=False, limit=80)",
        "store.list_events",
        "通知抽屉的新闻源（limit 80）——实测 9.6ms，前端按轮询取数",
    ),
    (
        "app/events/verify.py",
        "asyncio.to_thread(store.list_events, active_only=True, limit=limit)",
        "store.list_events",
        "热点验证环的活跃事件（端点 + 调度双入口，均落在事件循环上）",
    ),
    (
        "app/assistant/tools.py",
        "asyncio.to_thread(store.list_events, active_only=True, limit=limit)",
        "store.list_events",
        "助手 `news` 工具的事件源（limit 3~30，实测上限 7.2ms）",
    ),
    (
        "app/picks/morning_brief.py",
        "store.list_events, active_only=True, limit=EVENT_LIMIT",
        "store.list_events",
        "盘前简报的证据采集（在事件循环上执行）",
    ),
]

_ROUTE_IDS = [f"{Path(g[0]).stem}::{g[2]}#{i}" for i, g in enumerate(GUARDED_ROUTES)]


@pytest.mark.parametrize("rel,needle,fn,why", GUARDED_ROUTES, ids=_ROUTE_IDS)
def test_route_sync_io_calls_are_offloaded(rel: str, needle: str, fn: str, why: str) -> None:
    """路由 / 事件层的 `EventStore.list_events` 调用点必须走 `asyncio.to_thread`。

    反向断言带 `async_only=True`：只判 **async 上下文**里的裸调用。
    同步 helper（如 `notifications._alert_items`）内部的调用是合法的，不在此层判。

    ⚠️ **注入验证时的预期形状**：反向断言按**文件**判，所以 `events.py` 的 5 条登记项
    共用同一份反向检查 —— 在该文件里注入一处裸调用会**同时红 5 条**，这不是守卫失灵，
    而是"5 条登记项描述的是同一个文件"的必然结果。判读时看**报出的行**而不是条目数：
    2026-09-12 实测：注入 `events.py` 一处 ⇒ 5 红（4 条走反向、1 条走正向）；
    注入 `notifications.py` 一处 ⇒ **精确 1 红**（走正向 needle）。
    """
    path = BACKEND / rel
    assert path.exists(), f"{rel} 不存在（改路径了？同步更新本清单）"
    text = path.read_text(encoding="utf-8")
    assert needle in text, (
        f"{rel} 找不到 `{needle}` ⇒ 该调用点未走 to_thread，"
        f"同步调用会阻塞事件循环。阻塞源：{why}"
    )
    bare = _bare_calls(text, fn, async_only=True)
    assert not bare, (
        f"{rel} 的 async 上下文里存在未被 to_thread 包裹的 `{fn}(` 调用：\n  "
        + "\n  ".join(bare)
        + "\n⚠️ 该反向断言按**文件**判（同一文件的多条登记项共用），"
        "故报出的行**不一定**属于本条登记项——先看行内容再定位。"
        f"\n本条登记项的阻塞源：{why}"
    )


# ---------------------------------------------------------------- 守卫自身的守卫
#
# 反向检测靠**文本**找调用点 ⇒ 必须证明它分得清「代码」与「提到这段代码的文字」。
# 这一组是 2026-09-12 加 pipeline 条目时踩出来的：失败的**失败信息**当时指向一条
# docstring，却断言"存在未被包裹的调用"——误报的失败信息比不报更糟。


def test_docstring_and_comment_mentions_are_not_bare_calls():
    """注释 / docstring 里**点名**被守卫的调用，不算裸调用。

    否则"在注释里解释这条守卫"会把它自己弄红，后人只能把正确的说明文字删掉。
    """
    src = (
        'def f(store):\n'
        '    """同一条 `store.list_events(active_only=True, limit=30)` 跑了三遍。"""\n'
        '    # 旧写法：store.list_events(active_only=True, limit=30)\n'
        '    return 1\n'
    )
    assert _bare_calls(src, "list_events") == []


def test_masked_text_keeps_line_numbers_so_reported_line_is_the_real_one():
    """多行字符串被压平后，**行号必须仍然指向原文同一行**（否则失败信息会指错行）。"""
    src = (
        'x = """\n'
        "第一行说明 list_events(\n"
        '第二行说明\n'
        '"""\n'
        'real = store.list_events(1)   # 第 5 行，是裸调用\n'
    )
    assert _bare_calls(src, "list_events") == ["real = store.list_events(1)   # 第 5 行，是裸调用"]


def test_real_bare_call_is_still_flagged_under_masking():
    """正向对照：**真裸调用**必须照旧被抓（掩码不能把守卫也一起掩掉）。"""
    wrapped = '    rows = await asyncio.to_thread(store.list_events, active_only=True)\n'
    bare = "    rows = store.list_events(active_only=True)\n"
    assert _bare_calls(wrapped, "list_events") == []
    assert _bare_calls(wrapped + bare, "list_events") == [bare.strip()]


def test_to_thread_inside_a_string_does_not_count_as_wrapping():
    """字符串里的 `to_thread(` 不得被当成"已包"（掩码要修的**漏报**形状）。

    ⚠️ 字符串必须与调用**在同一行、且在调用之前**，否则测不到这条：
    前缀比较只看同行前缀，把字符串放到别的行上，不掩码也能通过（假绿）。
    """
    src = '    note = "应写成 asyncio.to_thread( 包一下"; rows = store.list_events(active_only=True)\n'
    assert _bare_calls(src, "list_events") == [
        'note = "应写成 asyncio.to_thread( 包一下"; rows = store.list_events(active_only=True)'
    ]


def test_incomplete_source_falls_back_to_raw_scan():
    """语法不完整（片段）时退回纯文本扫描——**宁可严（可能误报）不可漏**。

    判据要能**证明**发生了回退：取一段"既有注释、又语法不完整"的源（未闭合括号
    ⇒ `tokenize` 抛 TokenError）。掩码成功的话注释会被抹白；这里必须**原样保留**
    注释，且该注释里的提及会被当成裸调用报出来（刻意的严）。
    """
    src = "# 说明 list_events( 的调用\nrows = (\n"
    assert _mask_comments_and_strings(src) == src, "应原样退回，而不是静默把注释掩掉"
    assert _bare_calls(src, "list_events"), "退回纯文本后注释里的提及也算命中（严 > 漏）"


# ------------------------------------------------- async 感知（2026-09-12 路由层新增）
#
# 反向断言原本是纯文本的，会把**同步函数体内**的调用也算成裸调用。
# `notifications._alert_items` 是同步函数、内部有 `repo.list_events(limit=limit)`——
# 那本身合法；违规在**调用它的 async 端点**（另一层，文本扫描表达不了）。
# ⇒ 路由层条目一律带 `async_only=True`。


def test_async_only_ignores_module_level_sync_helper():
    """`async_only=True` 时，**模块级同步函数体内**的调用不算裸调用。

    这正是 `notifications._alert_items` 的形状（守卫首次运行时真实命中的假阳性）。
    """
    src = (
        "def _alert_items(repo, limit):\n"
        "    return [e for e in repo.list_events(limit=limit) if e.rule_id]\n"
        "\n"
        "async def notifications(repo):\n"
        "    items = _alert_items(repo, 50)\n"
        "    return items\n"
    )
    assert _bare_calls(src, "list_events") == ["return [e for e in repo.list_events(limit=limit) if e.rule_id]"], (
        "全文口径应报出该行（证明用例本身有效）"
    )
    assert _bare_calls(src, "list_events", async_only=True) == [], (
        "async 口径应放过同步 helper 体内的调用"
    )


def test_async_only_still_flags_async_body_calls():
    """正向对照：async 函数体内的裸调用**必须**照旧被抓（否则 async_only 变成"一律放过"）。"""
    src = (
        "async def list_events(store):\n"
        "    rows = store.list_events(active_only=True, limit=30)\n"
        "    return rows\n"
    )
    assert _bare_calls(src, "store.list_events", async_only=True) == [
        "rows = store.list_events(active_only=True, limit=30)"
    ]


def test_async_context_excludes_sync_def_nested_inside_async():
    """async 函数体内**嵌套的同步 def**，其函数体不算 async 上下文。

    没有这条，`async def` 里定义个同步小工具就会被误判——而它跑在调用方线程上。
    """
    src = (
        "async def outer():\n"
        "    def inner(store):\n"
        "        return store.list_events(limit=1)\n"
        "    a = store.list_events(limit=2)\n"
        "    return a\n"
    )
    lines = _async_context_lines(src)
    assert lines is not None
    assert 3 not in lines, "同步 def 体内的行不应算 async 上下文"
    assert 4 in lines, "async 函数体内的行应算 async 上下文"


def test_async_context_returns_none_on_unparsable_source():
    """语法不完整时返回 `None` ⇒ 调用方退回"不过滤"（严 > 漏），而不是静默返回空集。"""
    assert _async_context_lines("async def f(:\n") is None
