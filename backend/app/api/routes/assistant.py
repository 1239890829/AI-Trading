"""全局 AI 助手 API：POST /assistant/chat（SSE 流式）+ GET /assistant/entity-dict。

SSE 事件契约（每事件一行 `data: {json}\n\n`）：
- {"type":"meta","provider":..,"model":..,"sources":[..]}
                                            首事件，模型信息 + 本次注入快照的
                                            溯源清单（{symbol,name,source,as_of}），
                                            无快照时为空数组
- {"type":"delta","text":".."}               文本增量（可能多次）
- {"type":"status","phase":"thinking"|"tools","used":["longhu"],"label":"龙虎榜"}
                                            进度提示（2026-09-11 新增）：thinking =
                                            正在生成/正在组织回答；tools = 正在取数，
                                            label 是中文短标签串。**取数与思考期间可能
                                            十几秒没有任何 delta**，前端必须靠它给出
                                            "思考中/正在取数"反馈，否则界面像卡死。
- {"type":"error","message":"..","kind":"..","hint":".."}
                                            显式错误（之后仍发 done）。kind 取
                                            LLMFailure，hint 是可行动提示——
                                            quota 提示充值，gateway_error 提示
                                            稍后重试，不再笼统一句"调用失败"
- {"type":"grounding","violations":[{code,detail},..]}
                                            流结束后对完整回答的接地校验结果；
                                            仅在存在违例时发送（无违例不发，
                                            零噪音）。非阻断：流式文本已渲染，
                                            事后撤回会破坏一致性——语义是
                                            "以下数字未经本次注入数据佐证"，
                                            前端可据此显示提示徽标（可忽略，
                                            未知事件类型天然跳过）。校验器：
                                            app/core/grounding（EVIDENCE_NOT_FOUND
                                            编造数字 / OUT_OF_SCOPE_INFERENCE
                                            指令性建议）；证据池 = 注入的行情
                                            快照块 + 工具真实返回。
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

from app.assistant.cognition import describe_gap, looks_like_false_denial
from app.assistant.context import build_market_context, resolve_symbols
from app.assistant.prompt import PageContext, build_system_prompt
from app.assistant.tools import (
    MAX_CALLS_PER_TURN,
    MAX_TOOL_ROUNDS,
    ToolContext,
    has_partial_tool_call,
    parse_tool_calls,
    run_tool_calls,
    strip_tool_calls,
    tool_label,
)
from app.core.config import settings
from app.core.grounding import grounding_violations
from app.core.llm_client import ChatStream, LLMError, hint_for, stream_chat_completion
from app.services.quote_enrich import fetch_quotes_list

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
        hub=hub,
        snapshot_service=getattr(request.app.state, "snapshot_service", None),
        paper_engine=getattr(request.app.state, "paper", None),
        event_store=getattr(request.app.state, "event_store", None),
    )


@router.post("/assistant/chat")
async def assistant_chat(req: ChatRequest, request: Request) -> StreamingResponse:
    provider = settings.llm_provider
    model = settings.review_llm_model or settings.news_llm_model or "default"

    # 实体词典先取：既用于快照标的解析，也是工具参数的实体校验来源。
    known_symbols: set[str] = set()
    stocks: list[dict[str, str]] = []
    try:
        entity = await asyncio.to_thread(_entity_payload, request)
        stocks = entity.get("stocks") or []
        known_symbols = {str(s.get("code")) for s in stocks if s.get("code")}
    except Exception as exc:  # noqa: BLE001  上下文构建是增强层，绝不拖垮聊天
        log.warning("assistant entity dict skipped: %s", exc)

    # 工具上下文**必须先于**快照块确定：快照块头文案依赖"有没有工具"
    # （2026-09-11 修复——无工具时写死的"你没有资金流/龙虎榜"与工具清单自相矛盾，
    # 模型据此答"我没有龙虎榜数据"，而这玩意的工具明明在清单里）。
    tool_ctx = await _tool_context(request, known_symbols) if settings.assistant_tools_enabled else None
    tools_enabled = tool_ctx is not None

    # 实时快照注入：解析消息/页面里的标的 → 批量取价 → 提示词块 + 溯源清单（best-effort）
    market_block = ""
    market_sources: list[dict[str, str]] = []
    try:
        codes = resolve_symbols(req.messages[-1].content, req.page, stocks)
        if codes:

            hub = request.app.state.hub
            market_block, market_sources = await build_market_context(
                lambda syms: fetch_quotes_list(hub, syms), codes, tools_enabled
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("assistant market context skipped: %s", exc)
        market_block = ""
        market_sources = []

    # 上下文注入扩展（AI 大脑 P1）：行情快照之外，把「持仓 + 最近精选 + 今日事件」
    # 也主动喂给模型——助手回答"我持仓怎么样"不再依赖用户贴数据。全部只读、
    # best-effort：任何一块缺失都静默跳过（增强层不拖垮聊天）。
    extra_block = ""
    try:
        extra_block = await asyncio.to_thread(_build_extra_context, request)
    except Exception as exc:  # noqa: BLE001
        log.warning("assistant extra context skipped: %s", exc)
    combined_block = market_block + (("\n" + extra_block) if extra_block else "")

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
        messages = _build_messages(req, combined_block, tools_enabled=tools_enabled)
        tool_block: str | None = None  # 工具真实返回——grounding 证据池的第二部分
        try:
            sink: list[str] = []
            # 首事件即进度：前端据此亮「思考中」，不必等到第一个 delta 才有反馈
            # （用户实测反馈：取数与思考期间界面完全静止，像卡死）。
            yield _sse({"type": "status", "phase": "thinking"})
            # 第一轮：启用工具时走收集模式（开头可能是 {{tool:...}}，剥离后才给前端）
            async for chunk in _stream_round(messages, collect=tools_enabled, sink=sink):
                yield chunk

            # 多轮取数（2026-09-11：由"只取一轮"改为最多 MAX_TOOL_ROUNDS 轮）：
            # 真实提问常是多跳的（先看异动 → 再查公告 → 再看资金），一轮两把工具不够，
            # 模型只能拿第一批数据硬答。每轮都重新解析**本轮**输出里的工具标记。
            rounds = 0
            used_tools: list[str] = []   # 跨轮累计：认知缺口检测要判"本轮有没有取过数"
            while tools_enabled and tool_ctx is not None and rounds < MAX_TOOL_ROUNDS:
                raw = sink[-1] if sink else ""
                calls = parse_tool_calls(raw)
                if not calls:
                    break
                rounds += 1
                names = [c.name for c in calls[:MAX_CALLS_PER_TURN]]
                yield _sse({
                    "type": "status",
                    "phase": "tools",
                    "used": names,
                    "label": "、".join(tool_label(n) for n in names),
                })
                block, used = await run_tool_calls(calls, tool_ctx)
                tool_block = f"{tool_block}\n\n{block}" if tool_block else block
                used_tools.extend(used)
                log.info("assistant tools round=%s used=%s", rounds, used)
                yield _sse({"type": "tools", "used": used,
                            "labels": [tool_label(n) for n in used]})
                if not used:
                    break
                messages = messages + [
                    {"role": "assistant", "content": strip_tool_calls(raw) or "（取数）"},
                    {"role": "user", "content":
                        "以下是工具返回的真实数据（只读，来源见各行）。"
                        "基于这些数据回答，并标注来源；工具没给到的信息如实说没有。"
                        f"如果还需要别的数据，可以在回答开头再写新的工具标记（最多还能取 "
                        f"{MAX_TOOL_ROUNDS - rounds} 轮）。\n\n" + block},
                ]
                yield _sse({"type": "status", "phase": "thinking"})
                async for chunk in _stream_round(messages, collect=True, sink=sink):
                    yield chunk

            # 接地校验（P2-F，Vibe-Trading grounding gate 思想）：对用户实际看到的
            # 全部文本（各轮 strip 工具标记后拼接）做数字/指令越权校验。流式场景
            # 文本已渲染，非阻断留痕——违例发 grounding 事件，无违例零噪音。
            # 证据池 = 注入的行情快照块 + 工具真实返回；两者都空时跳过（没有
            # 数据就没有"编造 vs 有据"的判定基准，校验只会全盘误杀）。
            answer_text = "".join(strip_tool_calls(part) for part in sink)
            evidence_texts = [t for t in (market_block, extra_block, tool_block) if t]
            if answer_text.strip() and evidence_texts:
                violations = grounding_violations(answer_text, evidence_texts)
                if violations:
                    log.warning("assistant grounding violations=%s", violations)
                    yield _sse({"type": "grounding", "violations": violations})
            # 认知缺口自曝（2026-09-11）：没调工具却声称"我没有这项数据"——
            # 大概率是工具覆盖缺口或提示词没说清，而非真的取不到。
            # 只留痕、不改答案：这是**信号**，日志累积起来就是一份自动产出的缺口清单。
            if tools_enabled and looks_like_false_denial(answer_text, tools_used=used_tools):
                log.warning(
                    "assistant 疑似认知缺口（未调工具却声称无数据）：%s",
                    describe_gap(answer_text, req.messages[-1].content),
                )
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


# ---------------------------------------------------------------------------
# 上下文注入扩展（AI 大脑 P1）：持仓 + 最近精选 + 今日事件
# ---------------------------------------------------------------------------

_EXTRA_MAX_CHARS = 1200


def _build_extra_context(request: Request) -> str:
    """同步线程内构建「持仓/精选/事件」紧凑块（DB 读路径，零外呼）。

    行情快照已在 market_block 覆盖，这里只补持仓与系统结论类数据。
    任何一块拿不到就跳过——绝不占位虚构。
    """
    from sqlalchemy import select

    from app.core.db import get_session_factory
    from app.models.alert import AlertEvent, AlertRule
    from app.models.daily_pick import DailyPickSet
    from app.picks.watcher import WATCHER_RULE_NAME
    from app.services.real_position_service import load_positions

    sf = get_session_factory()
    blocks: list[str] = []

    try:
        positions = load_positions(sf)
        if positions:
            rows = [
                f"{p.name or ''}({p.symbol}) {p.quantity}股 成本{p.avg_cost}"
                for p in positions[:5]
            ]
            blocks.append("【持仓（成本口径，实时价见行情块）】" + "；".join(rows))
    except Exception as exc:  # noqa: BLE001
        log.debug("extra context positions skipped: %s", exc)

    try:
        with sf() as db:
            row = db.execute(
                select(DailyPickSet).order_by(DailyPickSet.id.desc()).limit(1)
            ).scalars().first()
        if row is not None:
            import json as _json

            items = _json.loads(row.items or "[]")[:3]
            names = "；".join(
                f"{it.get('name', '')}({it.get('symbol', '')}) {it.get('score', '—')}分"
                for it in items
            )
            blocks.append(f"【最近精选 {row.date}】{names}")
    except Exception as exc:  # noqa: BLE001
        log.debug("extra context picks skipped: %s", exc)

    try:
        with sf() as db:
            rule_id = db.execute(
                select(AlertRule.id).where(AlertRule.name == WATCHER_RULE_NAME).limit(1)
            ).scalars().first()
            if rule_id is not None:
                n = len(db.execute(
                    select(AlertEvent.id).where(AlertEvent.rule_id == rule_id).limit(500)
                ).scalars().all())
                blocks.append(f"【watcher 事件】近期累计 {n} 条（详情用 events 工具取）")
    except Exception as exc:  # noqa: BLE001
        log.debug("extra context events skipped: %s", exc)

    out = "\n".join(b for b in blocks if b)
    return out[:_EXTRA_MAX_CHARS]


# ---------------------------------------------------------------------------
# 收盘 LLM 综述（AI 大脑 P1：叙事分析层，每日一次）
# ---------------------------------------------------------------------------

_SUMMARY_TIMEOUT_S = 180.0


def _collect_summary_evidence(request: Request) -> dict:
    """综述证据收集（同步线程）：相位/精选/事件/持仓/指数。失败块显式缺席。"""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.core.db import get_session_factory
    from app.market.sentiment_history import get_history
    from app.models.alert import AlertEvent, AlertRule
    from app.models.daily_pick import DailyPickSet
    from app.picks.watcher import WATCHER_RULE_NAME
    from app.services.real_position_service import load_positions

    sf = get_session_factory()
    ev: dict = {}

    try:
        rows = get_history(sf, days=1)
        if rows:
            ev["phase"] = {k: rows[0].get(k) for k in ("trade_date", "phase", "temperature", "confidence")}
    except Exception as exc:  # noqa: BLE001
        log.debug("summary phase skipped: %s", exc)

    try:
        with sf() as db:
            row = db.execute(
                select(DailyPickSet).order_by(DailyPickSet.id.desc()).limit(1)
            ).scalars().first()
        if row is not None:
            import json as _json

            items = _json.loads(row.items or "[]")
            ev["picks"] = {
                "date": row.date,
                "items": [
                    {k: it.get(k) for k in ("symbol", "name", "score", "confidence", "theme", "observation_only", "follow_state")}
                    for it in items[:8]
                ],
                "meta": {k: row_meta for k, row_meta in (_json.loads(row.meta or "{}")).items()
                         if k in ("market_phase", "gate", "limit_up_count", "market_pct", "regime")},
            }
    except Exception as exc:  # noqa: BLE001
        log.debug("summary picks skipped: %s", exc)

    try:
        with sf() as db:
            rule_id = db.execute(
                select(AlertRule.id).where(AlertRule.name == WATCHER_RULE_NAME).limit(1)
            ).scalars().first()
            if rule_id is not None:
                rows = db.execute(
                    select(AlertEvent).where(AlertEvent.rule_id == rule_id)
                    .order_by(AlertEvent.id.desc()).limit(60)
                ).scalars().all()
            else:
                rows = []
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        texts = []
        for r in rows:
            try:
                bj = (r.triggered_at + timedelta(hours=8)).strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                continue
            if bj != today:
                continue
            snap = r.snapshot
            if isinstance(snap, str):
                try:
                    snap = json.loads(snap)
                except Exception:  # noqa: BLE001
                    snap = {}
            t = (snap or {}).get("text")
            if t:
                texts.append(t)
        ev["events_today"] = texts[:12]
        ev["events_today_count"] = len(texts)
    except Exception as exc:  # noqa: BLE001
        log.debug("summary events skipped: %s", exc)

    try:
        positions = load_positions(sf)
        if positions:
            ev["positions"] = [
                {k: getattr(p, k) for k in ("symbol", "name", "quantity", "avg_cost", "realized_pnl")}
                for p in positions[:8]
            ]
    except Exception as exc:  # noqa: BLE001
        log.debug("summary positions skipped: %s", exc)

    return ev


@router.get("/assistant/daily-summary")
async def assistant_daily_summary(request: Request, force: bool = False) -> dict:
    """收盘 LLM 综述（15:35 automation 消费）：把当日系统数据变成人话与判断。

    与 15:45 清单式复盘分工：本端点是**叙事层**（相位→盘面→组合→关注点），
    复盘 automation 是**审计层**（三态判定/操作对账/处置闭环）。
    LLM 失败显式 available=False——automation 侧跳过发送，绝不发降级占位文。
    """
    import time as _time

    from app.core.llm_client import LLMError, chat_completion

    evidence = await asyncio.to_thread(_collect_summary_evidence, request)
    if not evidence:
        return {"available": False, "reason": "无可综述数据（相位/精选/事件/持仓全缺）"}

    import json as _json

    prompt = (
        "你是 A 股交易系统的收盘综述引擎。基于以下系统真实数据写一段 200~300 字的收盘综述：\n"
        "1) 市场相位与温度；2) 盘面与 watcher 异动的主线；3) 今日精选组合的处境"
        "（哪些票值得继续跟踪、哪些触发观察）；4) 明日开盘前最该确认的一件事。\n"
        "纪律：只使用给定数据，不编造数字；不构成买卖建议；语气克制、结论前置。\n\n"
        "数据（JSON）：\n" + _json.dumps(evidence, ensure_ascii=False)
    )
    messages = [
        {"role": "system", "content": "你是严谨的 A 股收盘综述引擎，输出中文纯文本。"},
        {"role": "user", "content": prompt},
    ]
    t0 = _time.monotonic()
    try:
        text = await asyncio.to_thread(
            chat_completion,
            settings.review_llm_base_url,
            settings.review_llm_api_key,
            settings.review_llm_model or settings.news_llm_model or "default",
            messages,
            provider=settings.llm_provider,
            cli_path=settings.llm_cli_path,
            timeout=_SUMMARY_TIMEOUT_S,
        )
    except LLMError as exc:
        kind = getattr(exc, "kind", None)
        return {
            "available": False,
            "reason": str(exc)[:200],
            "kind": kind.value if kind is not None else None,
        }
    return {
        "available": True,
        "text": text.strip(),
        "model": settings.review_llm_model or settings.news_llm_model,
        "elapsed_s": round(_time.monotonic() - t0, 1),
        "generated_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
