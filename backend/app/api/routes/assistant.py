"""全局 AI 助手 API：POST /assistant/chat（SSE 流式）+ GET /assistant/entity-dict。

SSE 事件契约（每事件一行 `data: {json}\n\n`）：
- {"type":"meta","provider":..,"model":..,"sources":[..]}
                                            首事件，模型信息 + 本次注入快照的
                                            溯源清单（{symbol,name,source,as_of}），
                                            无快照时为空数组
- {"type":"delta","text":".."}               文本增量（可能多次）
- {"type":"error","message":"..","kind":"..","hint":".."}
                                            显式错误（之后仍发 done）。kind 取
                                            LLMFailure，hint 是可行动提示——
                                            quota 提示充值，gateway_error 提示
                                            稍后重试，不再笼统一句"调用失败"
- {"type":"done"}                            终态

客户端断开 → StreamingResponse 取消生成器 → asyncio.CancelledError →
在线程池里 close() ChatStream（claude_cli 路径杀子进程），不遗留孤儿进程。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.assistant.context import build_market_context, resolve_symbols
from app.assistant.prompt import PageContext, build_system_prompt
from app.assistant.tools import (
    ToolContext,
    has_partial_tool_call,
    parse_tool_calls,
    run_tool_calls,
    strip_tool_calls,
)
from app.core.config import settings
from app.core.llm_client import ChatStream, LLMError, hint_for, stream_chat_completion

log = logging.getLogger(__name__)

router = APIRouter(tags=["assistant"])

MAX_MESSAGES = 40
MAX_CONTENT_CHARS = 8000


class ChatMessageIn(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        if v not in ("user", "assistant"):
            raise ValueError("role 只允许 user/assistant")
        return v


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn] = Field(min_length=1, max_length=MAX_MESSAGES)
    page: PageContext | None = None

    @field_validator("messages")
    @classmethod
    def _check_messages(cls, v: list[ChatMessageIn]) -> list[ChatMessageIn]:
        if v[-1].role != "user":
            raise ValueError("最后一条消息必须是 user")
        for m in v:
            if not m.content.strip():
                raise ValueError("消息内容不能为空")
            if len(m.content) > MAX_CONTENT_CHARS:
                raise ValueError(f"单条消息超长（>{MAX_CONTENT_CHARS} 字符）")
        return v


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _build_messages(
    req: ChatRequest, market_block: str = "", tools_enabled: bool = False
) -> list[dict[str, str]]:
    system = build_system_prompt(req.page, tools_enabled=tools_enabled)
    if market_block:
        system += "\n" + market_block
    msgs = [{"role": "system", "content": system}]
    msgs += [{"role": m.role, "content": m.content} for m in req.messages]
    return msgs


def _open_stream(messages: list[dict[str, str]]) -> ChatStream:
    """按配置打开 LLM 流（独立函数便于测试替换）。"""
    return stream_chat_completion(
        settings.review_llm_base_url,
        settings.review_llm_api_key,
        settings.review_llm_model or settings.news_llm_model or "default",
        messages,
        provider=settings.llm_provider,
        cli_path=settings.llm_cli_path,
        temperature=0.3,
        timeout=30.0,
    )


async def _close_stream(stream: ChatStream) -> None:
    await asyncio.to_thread(stream.close)


async def _tool_context(
    request: Request, known_symbols: set[str] | None = None
) -> ToolContext | None:
    """构造工具执行上下文；拿不到 provider 返回 None（此时工具不可用，不注入清单）。

    交易日集合只在拿得到时用（trading_days 自带 24h 缓存）；拿不到就退化为
    只校验日期格式——校验是为了拦错，不能反过来成为工具的可用性瓶颈。
    """
    hub = getattr(request.app.state, "hub", None)
    provider = getattr(hub, "provider", None)
    if provider is None:
        return None
    days: set[str] = set()
    try:
        from app.market import trade_calendar as tc

        days = {d.isoformat() for d in await tc.trading_days(provider)}
    except Exception:  # noqa: BLE001
        days = set()
    try:
        from app.core.db import get_session_factory

        sf = get_session_factory()
    except Exception:  # noqa: BLE001
        sf = None
    return ToolContext(
        provider=provider,
        session_factory=sf,
        known_symbols=known_symbols or set(),
        trading_days=days,
    )


@router.post("/assistant/chat")
async def assistant_chat(req: ChatRequest, request: Request) -> StreamingResponse:
    provider = settings.llm_provider
    model = settings.review_llm_model or settings.news_llm_model or "default"

    # 实时快照注入：解析消息/页面里的标的 → 批量取价 → 提示词块 + 溯源清单（best-effort）
    market_block = ""
    market_sources: list[dict[str, str]] = []
    known_symbols: set[str] = set()
    try:
        entity = await asyncio.to_thread(_entity_payload, request)
        stocks = entity.get("stocks") or []
        known_symbols = {str(s.get("code")) for s in stocks if s.get("code")}
        codes = resolve_symbols(
            req.messages[-1].content, req.page, stocks
        )
        if codes:
            from app.api.routes.market import _batch_quotes

            hub = request.app.state.hub
            market_block, market_sources = await build_market_context(
                lambda syms: _batch_quotes(hub, syms), codes
            )
    except Exception as exc:  # noqa: BLE001  上下文构建是增强层，绝不拖垮聊天
        log.warning("assistant market context skipped: %s", exc)
        market_block = ""
        market_sources = []

    tool_ctx = await _tool_context(request, known_symbols) if settings.assistant_tools_enabled else None
    tools_enabled = tool_ctx is not None

    async def _stream_round(messages: list[dict[str, str]], collect: bool, sink: list[str] | None = None):
        """跑一轮流式生成。collect=True 时剥离工具标记，并把原始增量写入 sink。"""
        raw_parts: list[str] = []
        loop = asyncio.get_running_loop()
        stream = await asyncio.to_thread(_open_stream, messages)
        try:
            queue: asyncio.Queue = asyncio.Queue()

            def _produce() -> None:
                try:
                    for delta in stream:
                        loop.call_soon_threadsafe(queue.put_nowait, ("delta", delta))
                except BaseException as exc:  # noqa: BLE001  异常也走队列传给消费侧
                    loop.call_soon_threadsafe(queue.put_nowait, ("raise", exc))
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, ("eof", None))

            threading.Thread(target=_produce, daemon=True).start()
            pending = ""
            while True:
                kind, payload = await queue.get()
                if kind == "eof":
                    break
                if kind == "raise":
                    raise payload
                if not payload:
                    continue
                raw_parts.append(payload)
                if not collect:
                    yield _sse({"type": "delta", "text": payload})
                    continue
                # 收集模式：攒住文本，遇到"像是没打完的 {{tool:" 先不吐给用户，
                # 完整的 {{tool:...}} 直接剥离——标记行绝不能出现在界面上。
                pending += payload
                if has_partial_tool_call(pending) and len(pending) < 400:
                    continue
                out = strip_tool_calls(pending)
                pending = ""
                if out:
                    yield _sse({"type": "delta", "text": out})
            if collect and pending:
                out = strip_tool_calls(pending)
                if out:
                    yield _sse({"type": "delta", "text": out})
        finally:
            await _close_stream(stream)
            if sink is not None:
                sink.append("".join(raw_parts))

    async def event_stream():
        # 先发 meta：即使 LLM 不可用，前端也能渲染"正在生成"的状态再收到显式错误。
        # sources = 本次注入的实时快照溯源清单（来源/数据时间），前端据此显示脚注——
        # 回答里的每个数字都能追到"哪个源、几点的数据"。
        yield _sse({
            "type": "meta",
            "provider": provider,
            "model": model,
            "sources": market_sources,
        })
        messages = _build_messages(req, market_block, tools_enabled=tools_enabled)
        try:
            sink: list[str] = []
            # 第一轮：启用工具时走收集模式（开头可能是 {{tool:...}}，剥离后才给前端）
            async for chunk in _stream_round(messages, collect=tools_enabled, sink=sink):
                yield chunk

            raw = sink[0] if sink else ""
            calls = parse_tool_calls(raw) if tools_enabled else []
            if calls:
                ctx = tool_ctx
                if ctx is not None:
                    block, used = await run_tool_calls(calls, ctx)
                    log.info("assistant tools used=%s", used)
                    yield _sse({"type": "tools", "used": used})
                    followup = messages + [
                        {"role": "assistant", "content": strip_tool_calls(raw) or "（取数）"},
                        {"role": "user", "content":
                            "以下是工具返回的真实数据（只读，来源见各行）。"
                            "基于这些数据回答，并标注来源；工具没给到的信息如实说没有。\n\n" + block},
                    ]
                    # 第二轮：不再允许工具（防止无限循环）
                    async for chunk in _stream_round(followup, collect=True, sink=sink):
                        yield chunk
            yield _sse({"type": "done"})
        except asyncio.CancelledError:
            # 客户端断开（点了停止/关窗/跳页）：底层传输由 _stream_round 的 finally 关闭
            raise
        except LLMError as exc:
            # 带上分类：额度不足要提示充值，网关失败则提示稍后重试——
            # 过去两者都只回一句"调用失败"，用户无从判断该做什么。
            kind = getattr(exc, "kind", None)
            kind_value = kind.value if kind is not None else None
            log.warning("assistant chat LLMError kind=%s: %s", kind_value, exc)
            yield _sse({
                "type": "error",
                "message": str(exc),
                "kind": kind_value,
                "hint": hint_for(kind_value),
            })
            yield _sse({"type": "done"})
        except Exception as exc:  # noqa: BLE001  兜底：任何异常都显式报错，绝不裸断流
            log.exception("assistant chat failed")
            yield _sse({"type": "error", "message": f"助手内部错误：{exc}"})
            yield _sse({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 实体字典：股票名→代码（最新快照 parquet）+ 官方题材名。前端据此做
# 回复文本的最长匹配并渲染跳转链接。CI/无数据环境降级为空列表。
# ---------------------------------------------------------------------------

_ENTITY_TTL = 300.0
_entity_cache: dict = {"t": 0.0, "data": None}


def load_stock_names(parquet_dir: Path) -> list[dict[str, str]]:
    """最新快照 parquet → [{name, code}]。同名列保留先出现的（代码序靠前）。"""
    try:
        import polars as pl
    except ImportError:  # pragma: no cover - CI 环境无 polars
        return []
    day_dirs = sorted(
        (d for d in parquet_dir.iterdir() if d.is_dir() and d.name.isdigit()),
        key=lambda d: d.name,
    ) if parquet_dir.is_dir() else []
    for day in reversed(day_dirs):
        files = sorted(day.glob("*.parquet"))
        if not files:
            continue
        try:
            df = pl.read_parquet(files[-1], columns=["symbol", "name"])
        except Exception:  # noqa: BLE001  损坏文件按无数据处理（容错读纪律）
            continue
        names: dict[str, str] = {}
        for sym, name in zip(df["symbol"].to_list(), df["name"].to_list()):
            name = (name or "").strip()
            if len(name) < 2 or len(name) > 8 or not str(sym).isdigit():
                continue
            names.setdefault(name, str(sym))
        return [{"name": n, "code": c} for n, c in sorted(names.items())]
    return []


def _entity_payload(request: Request) -> dict:
    now = time.monotonic()
    cached = _entity_cache.get("data")
    if cached is not None and now - _entity_cache["t"] < _ENTITY_TTL:
        return cached
    stocks = load_stock_names(Path(settings.parquet_dir) / "snapshots")
    themes: list[str] = []
    svc = getattr(request.app.state, "theme_catalog", None)
    if svc is not None:
        try:
            rows = svc.get_catalog(limit=1000)
            themes = sorted({r.name.strip() for r in rows if len(r.name.strip()) >= 2})
        except Exception as exc:  # noqa: BLE001  目录库缺失不拖垮股票字典
            log.warning("entity-dict themes load failed: %s", exc)
    data = {"stocks": stocks, "themes": themes}
    _entity_cache.update(t=now, data=data)
    return data


@router.get("/assistant/entity-dict")
async def assistant_entity_dict(request: Request) -> dict:
    data = await asyncio.to_thread(_entity_payload, request)
    return {"data": data, "meta": {}}
