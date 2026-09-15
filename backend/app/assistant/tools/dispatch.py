"""调用分发：单次调用与整轮调用（含次数/字符上限与缓存）。

`app/assistant/tools.py` 的内部切片（IMP-005，2026-09-15 从 1934 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app/assistant/tools/__init__.py` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

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
async def run_tool(call: ToolCall, ctx: ToolContext, cache: TTLCache | None = TOOL_CACHE) -> str:
    """执行单个工具调用，返回给模型看的文本；任何异常都降级为一行说明。"""
    spec = TOOL_SPECS.get(call.name)
    if spec is None:
        return f"【{call.name}】工具失败：未登记的工具名（可用：{'、'.join(TOOL_SPECS)}）"

    key = (call.name, tuple(sorted(call.args.items())))
    if cache is not None:
        hit, val = cache.get(key)
        if hit:
            return f"{val}\n（命中缓存，可能非最新）"

    try:
        text = await spec.handler(ctx, **call.args)
    except TypeError as exc:
        return f"【{call.name}】工具失败：参数不合法（{exc}）"
    except Exception as exc:  # noqa: BLE001  工具是增强层，失败只降级
        log.warning("assistant tool %s failed: %s", call.name, exc)
        return f"【{call.name}】工具失败：{type(exc).__name__}: {exc}"

    if cache is not None:
        cache.set(key, text)
    return text


async def run_tool_calls(
    calls: list[ToolCall], ctx: ToolContext, cache: TTLCache | None = TOOL_CACHE
) -> tuple[str, list[str]]:
    """批量执行（受 MAX_CALLS_PER_TURN 限制），返回 (回填文本, 调用名列表)。"""
    used: list[str] = []
    blocks: list[str] = []
    for c in calls[:MAX_CALLS_PER_TURN]:
        blocks.append(await run_tool(c, ctx, cache))
        used.append(c.name)
    if len(calls) > MAX_CALLS_PER_TURN:
        blocks.append(f"（本轮工具调用上限 {MAX_CALLS_PER_TURN} 次，其余 {len(calls) - MAX_CALLS_PER_TURN} 次已忽略）")
    return "\n\n".join(blocks), used
