"""调用分发：单次调用与整轮调用（含次数/字符上限与缓存）。

`app/assistant/tools.py` 的内部切片（IMP-005，2026-09-15 从 1934 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app/assistant/tools/__init__.py` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

import inspect

from app.core.ttl_cache import TTLCache

from .core import (
    MAX_CALLS_PER_TURN,
    TOOL_CACHE,
    ToolCall,
    ToolContext,
    log,
)
from .registry import (
    TOOL_SPECS,
)
async def run_tool(call: ToolCall, ctx: ToolContext, cache: TTLCache | None = TOOL_CACHE,
                   *, allowed_tools: set[str] | None = None, receipts: list[dict] | None = None) -> str:
    """执行单个工具调用，返回给模型看的文本；任何异常都降级为一行说明。"""
    def record(state):
        if receipts is not None:
            # Unknown model-generated names might contain private strings; don't persist them.
            receipts.append({"tool": call.name if call.name in TOOL_SPECS else None, "state": state})

    spec = TOOL_SPECS.get(call.name)
    if spec is None:
        record("unregistered")
        return f"【{call.name}】工具失败：未登记的工具名（可用：{'、'.join(TOOL_SPECS)}）"

    if allowed_tools is not None and call.name not in allowed_tools:
        record("unavailable")
        return f"【{call.name}】工具不可用：本请求未提供所需只读依赖"
    try:
        inspect.signature(spec.handler).bind(ctx, **call.args)
    except TypeError:
        record("invalid_args")
        return f"【{call.name}】工具失败：参数不合法"

    key = (call.name, tuple(sorted(call.args.items())))
    # Repository content can change inside the generic TTL; KB hashes must name
    # current bytes. The KB tool therefore rereads, rather than certifying cache.
    use_cache = cache is not None and call.name != "kb"
    if use_cache:
        hit, val = cache.get(key)
        if hit:
            record("cache_hit")
            return f"{val}\n（命中缓存，可能非最新）"

    try:
        text = await spec.handler(ctx, **call.args)
    except TypeError as exc:
        record("failed")
        return f"【{call.name}】工具失败：参数不合法（{exc}）"
    except Exception as exc:  # noqa: BLE001  工具是增强层，失败只降级
        record("failed")
        log.warning("assistant tool %s failed: %s", call.name, exc)
        return f"【{call.name}】工具失败：{type(exc).__name__}: {exc}"

    record("returned")
    if use_cache:
        cache.set(key, text)
    return text


async def run_tool_calls(
    calls: list[ToolCall], ctx: ToolContext, cache: TTLCache | None = TOOL_CACHE,
    *, allowed_tools: set[str] | None = None, receipts: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """批量执行（受 MAX_CALLS_PER_TURN 限制），返回 (回填文本, 调用名列表)。"""
    used: list[str] = []
    blocks: list[str] = []
    for c in calls[:MAX_CALLS_PER_TURN]:
        blocks.append(await run_tool(c, ctx, cache, allowed_tools=allowed_tools, receipts=receipts))
        used.append(c.name)
    if len(calls) > MAX_CALLS_PER_TURN:
        blocks.append(f"（本轮工具调用上限 {MAX_CALLS_PER_TURN} 次，其余 {len(calls) - MAX_CALLS_PER_TURN} 次已忽略）")
    return "\n\n".join(blocks), used
