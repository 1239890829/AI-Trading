"""全局 AI 助手 API：POST /assistant/chat（SSE 流式）+ GET /assistant/entity-dict。

SSE 事件契约（每事件一行 `data: {json}\n\n`）：
- {"type":"meta","provider":..,"model":..}   首事件，模型信息
- {"type":"delta","text":".."}               文本增量（可能多次）
- {"type":"error","message":".."}            显式错误（之后仍发 done）
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

from app.assistant.context import build_market_block, resolve_symbols
from app.assistant.prompt import PageContext, build_system_prompt
from app.core.config import settings
from app.core.llm_client import ChatStream, LLMError, stream_chat_completion

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


def _build_messages(req: ChatRequest, market_block: str = "") -> list[dict[str, str]]:
    system = build_system_prompt(req.page)
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


@router.post("/assistant/chat")
async def assistant_chat(req: ChatRequest, request: Request) -> StreamingResponse:
    provider = settings.llm_provider
    model = settings.review_llm_model or settings.news_llm_model or "default"

    # 实时快照注入：解析消息/页面里的标的 → 批量取价 → 提示词块（best-effort）
    market_block = ""
    try:
        entity = await asyncio.to_thread(_entity_payload, request)
        codes = resolve_symbols(
            req.messages[-1].content, req.page, entity.get("stocks") or []
        )
        if codes:
            from app.api.routes.market import _batch_quotes

            hub = request.app.state.hub
            market_block = await build_market_block(
                lambda syms: _batch_quotes(hub, syms), codes
            )
    except Exception as exc:  # noqa: BLE001  上下文构建是增强层，绝不拖垮聊天
        log.warning("assistant market context skipped: %s", exc)
        market_block = ""

    async def event_stream():
        # 先发 meta：即使 LLM 不可用，前端也能渲染"正在生成"的状态再收到显式错误
        yield _sse({"type": "meta", "provider": provider, "model": model})
        loop = asyncio.get_running_loop()
        stream: ChatStream | None = None
        try:
            stream = await asyncio.to_thread(
                _open_stream, _build_messages(req, market_block)
            )
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
            while True:
                kind, payload = await queue.get()
                if kind == "eof":
                    break
                if kind == "raise":
                    raise payload
                if payload:
                    yield _sse({"type": "delta", "text": payload})
            yield _sse({"type": "done"})
        except asyncio.CancelledError:
            # 客户端断开（点了停止/关窗/跳页）：必须杀掉底层传输
            if stream is not None:
                await _close_stream(stream)
            raise
        except LLMError as exc:
            log.warning("assistant chat LLMError: %s", exc)
            yield _sse({"type": "error", "message": str(exc)})
            yield _sse({"type": "done"})
        except Exception as exc:  # noqa: BLE001  兜底：任何异常都显式报错，绝不裸断流
            log.exception("assistant chat failed")
            yield _sse({"type": "error", "message": f"助手内部错误：{exc}"})
            yield _sse({"type": "done"})
        finally:
            if stream is not None:
                await _close_stream(stream)

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
