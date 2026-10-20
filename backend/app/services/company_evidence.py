"""Bounded company research worker; AgentTask owns lifecycle, not these files.

The existing scheduler only enqueues the existing task type. A process lease and
stable evidence keys prevent duplicate paid calls. Reads never invoke this file.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from time import monotonic

from sqlalchemy import select

from app.core.bjtime import beijing_now, to_beijing, to_beijing_naive
from app.core.config import settings
from app.core.db import get_session_factory
from app.core.llm_client import LLMError, bounded_chat_completion, resolve_cli_path
from app.events import company_impact as ci
from app.events.attribution import read_attribution
from app.events.company_reasoning import PROTOCOL, compile_analysis, parse_selection_batch, selector_messages
from app.events.company_sources import collect_company
from app.events.company_state import load_state, put_profile, read_profile, write_state
from app.services import agent_budget


def _now() -> datetime:
    return beijing_now()


@contextmanager
def worker_lease(root: Path):
    if root.is_symlink():
        raise ValueError("invalid company worker root")
    root.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root / ".worker.lock", flags, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("公司依据任务正在由另一执行者处理") from None
        yield
    finally:
        os.close(descriptor)  # Closing releases the OS lease, including on process death.


async def _drain_stage(fn, *args, **kwargs):
    worker = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if worker.done() and not worker.cancelled():
            worker.exception()  # Retrieve even if the thread finished just before cancellation arrived.
        raise


def candidate_symbols(session_factory=None, *, now=None, limit=40) -> list[str]:
    """Only today's persisted rank/list consumers; no provider or full-market scan."""
    from app.models.daily_pick import DailyPickSet
    from app.models.opportunity_learning import OpportunityDecisionRun as Run, OpportunityDecisionSnapshot as Snapshot

    sf, aware = session_factory or get_session_factory(), to_beijing(now or _now())
    current = to_beijing_naive(aware)
    recorded_cutoff = aware.astimezone(timezone.utc).replace(tzinfo=None)
    day, symbols = current.date().isoformat(), []
    with sf() as db:
        # A later legitimate empty/degraded run must not resurrect an old rank.
        latest_run = db.execute(select(Run).where(
            Run.trade_date == day, Run.scenario == "intraday_opportunity", Run.as_of <= current,
        ).order_by(Run.as_of.desc(), Run.run_id.desc()).limit(1)).scalar_one_or_none()
        if latest_run:
            if latest_run.data_state == "ready":
                symbols.extend(db.execute(select(Snapshot.symbol).where(
                    Snapshot.run_id == latest_run.run_id, Snapshot.trade_date == day,
                    Snapshot.scenario == "intraday_opportunity", Snapshot.as_of <= current,
                    Snapshot.as_of == latest_run.as_of, Snapshot.stage == "rank", Snapshot.data_state == "ready",
                    Snapshot.decision == "ranked", Snapshot.rank >= 1,
                ).order_by(Snapshot.rank.asc(), Snapshot.id.asc()).limit(32)).scalars().all())
        pick = db.execute(select(DailyPickSet.items).where(
            DailyPickSet.date == day, DailyPickSet.created_at <= recorded_cutoff,
        ).limit(1)).scalar_one_or_none()
        if pick:
            try:
                rows = json.loads(pick)
                if isinstance(rows, list):
                    symbols.extend(v.get("symbol") for v in rows[:8] if isinstance(v, dict))
            except (ValueError, TypeError):
                pass
    return list(dict.fromkeys(v for v in symbols if isinstance(v, str) and re.fullmatch(r"\d{6}", v)))[:max(1, min(limit, 40))]


def _rotation_key(symbol):
    prior = load_state(symbol)
    return (bool(prior), str(prior.get("next_retry_at") or ""), str(prior.get("updated_at") or ""), symbol)


def _automatic_daily_limit(sf):
    """Reserve room within the shared budget; no second budget fact/table."""
    from app.models.agent import AgentResourceUsage as Usage

    with sf() as db:
        calls = len(db.execute(select(Usage.id).where(
            Usage.budget_date == _now().date().isoformat(), Usage.scope == agent_budget.SCOPE_AUTONOMY_LLM,
            Usage.purpose == "company_evidence.select", Usage.started_at.is_not(None),
        ).limit(4)).all())
    if calls >= 4:
        raise agent_budget.BudgetError("company_daily_budget", "公司自动判读当日已有4次调用；保留共享预算给其他消费者")


def _event(symbol, app, sf, *, event_id=None):
    catalog = getattr(getattr(app, "state", None), "theme_catalog", None)
    themes = {v["theme_name"] for v in catalog.get_official_for_symbol(symbol)} if catalog else set()
    result = read_attribution(sf, symbol=symbol, theme_names=themes, event_id=event_id,
                              limit=3 if event_id is None else 1, now=_now())["data"]
    for item in result["items"]:
        ref, context = item.get("interpretation_ref", {}), item.get("source_context", {})
        if (item.get("state") == "active" and item.get("is_active") is True
                and ref.get("state") == "active" and ref.get("observation_id") == context.get("observation_id")
                and all(type(ref.get(k)) is int and ref[k] > 0 for k in ("event_id", "version_id", "observation_id"))
                and item.get("timing", {}).get("available_at")):
            return item
    return None


def _event_key(item):
    return {k: item["interpretation_ref"][k] for k in ("event_id", "version_id", "observation_id")} if item else None


def generation_key(packet: dict, model: dict) -> str:
    documents = [{key: d[key] for key in ("id", "symbol", "company_name", "url", "sha256", "period", "excerpts")}
                 for d in packet["documents"]]
    return ci.digest({"symbol": packet["symbol"], "company_name": packet["company_name"],
                      "short_name": packet.get("short_name", ""), "event": packet["event"], "documents": documents,
                      "protocol": PROTOCOL, "model": model})


def _packet(symbol, profile, item):
    ref, source, timing = item["interpretation_ref"], item["source_context"], item["timing"]
    return {"schema": ci.SCHEMA, "id": f"{symbol}:{ref['event_id']}:{ref['version_id']}:{ref['observation_id']}",
            "symbol": symbol, "company_name": profile["company_name"], "short_name": profile.get("short_name") or "",
            "captured_at": _now().isoformat(), "documents": profile["documents"],
            "event": {**_event_key(item), "title": source["title"], "summary": source.get("summary"),
                      "url": source.get("url"), "available_at": timing["available_at"],
                      "received_at": timing.get("received_at"), "source_published_at": timing.get("source_published_at"),
                      "content_hash": item.get("source", {}).get("content_hash")}}


def _state(symbol, state, task_id, **fields):
    return write_state(symbol, state, now=_now(), task_id=task_id, **fields)


def _due(symbol, item, *, explicit=False):
    prior = load_state(symbol)
    if prior.get("event_ref") != _event_key(item):
        return True
    if prior.get("attempt_state") in {"started", "unknown"} or (not explicit and prior.get("state") == "canceled"):
        return False
    try:
        return not prior.get("next_retry_at") or ci.clock(prior["next_retry_at"]) <= _now()
    except (ValueError, TypeError):
        return False


async def run_company_evidence(rec, params, app):
    """One finite task, cooperative stages, one shared-budget selector call."""
    if not isinstance(params, dict) or set(params) - {"symbols", "automatic"}:
        raise ValueError("公司任务只接受symbols与automatic")
    if "automatic" in params and type(params["automatic"]) is not bool:
        raise ValueError("公司任务automatic须为布尔值")
    explicit = "symbols" in params and params.get("automatic") is not True
    if params.get("automatic") is True and not settings.company_evidence_enabled:
        return {"kind": "company_evidence", "state": "disabled", "symbols": []}
    symbols = params.get("symbols")
    if symbols is not None and (not isinstance(symbols, list) or not 1 <= len(symbols) <= 8
                               or any(not isinstance(v, str) or not re.fullmatch(r"\d{6}", v) for v in symbols)):
        raise ValueError("公司任务symbols须为1至8个六位证券代码")
    sf = get_session_factory()
    symbols = list(dict.fromkeys(symbols)) if symbols is not None else sorted(await _drain_stage(candidate_symbols, sf), key=_rotation_key)
    batch_size = 8 if explicit else max(1, min(8, settings.company_evidence_batch_size))
    model = {"provider": settings.llm_provider, "model": settings.review_llm_model}
    results, pending, touched = [], [], []
    await rec.checkpoint_async()
    with worker_lease(ci.ROOT):
        try:
            for symbol in symbols:
                if len(touched) >= batch_size:
                    break
                await rec.checkpoint_async()
                item = await _drain_stage(_event, symbol, app, sf)
                if not explicit and not _due(symbol, item):
                    continue
                touched.append(symbol)
                _state(symbol, "collecting", rec.task_id, event_ref=_event_key(item))
                start = monotonic()
                try:
                    profile = await _drain_stage(collect_company, symbol, as_of=_now(), root=ci.ROOT / "sources")
                except Exception:
                    _state(symbol, "source_failed", rec.task_id, source_status="failed",
                           missing=["公司正式披露采集未完成；已存资料没有被空结果覆盖。"],
                           next_retry_at=(_now() + timedelta(minutes=30)).isoformat())
                    results.append({"symbol": symbol, "state": "source_failed"})
                    continue
                await rec.checkpoint_async()
                await rec.add_async(name="公司公开依据", input_summary=symbol,
                        output_summary=f"来源状态={profile['status']}；文档={len(profile['documents'])}",
                        duration_ms=int((monotonic() - start) * 1000), ok=profile["status"] == "ready")
                if profile["status"] != "ready" or not profile["documents"]:
                    state = "source_failed" if profile["status"] == "failed" else "insufficient"
                    _state(symbol, state, rec.task_id, missing=profile.get("missing", [])[:12],
                           source_status=profile["status"], next_retry_at=profile.get("cache_until"))
                    results.append({"symbol": symbol, "state": state})
                    continue
                put_profile(profile)
                if item is None:
                    _state(symbol, "insufficient", rec.task_id, source_status="ready",
                           missing=["当前已采集资料中没有可绑定的现役事件。"], next_retry_at=profile.get("cache_until"))
                    results.append({"symbol": symbol, "state": "insufficient"})
                    continue
                retained = ci.read_saved(item, symbol, now=_now())
                if retained is not None and retained["verification"] == "author_source_reviewed_not_gold":
                    _state(symbol, "ready", rec.task_id, source_status="ready", result_key=None,
                           missing=retained["analysis"]["missing"], next_retry_at=profile.get("cache_until"))
                    results.append({"symbol": symbol, "state": "ready", "cached": True,
                                    "verification": retained["verification"]})
                    continue
                packet = _packet(symbol, profile, item)
                ci.validate_packet(packet)
                key, prior = generation_key(packet, model), load_state(symbol)
                if prior.get("generation_key") == key and prior.get("attempt_state") != "not_started":
                    # Reuse the exact selection after interrupted publication. A
                    # started/failed/unknown call without a selection is not resent.
                    if prior.get("attempt_state") == "succeeded" and isinstance(prior.get("selected"), dict):
                        cached = ci.read_saved(item, symbol, now=_now())
                        if cached is not None:
                            state = "ready" if cached["analysis"]["status"] == "conditional" else "insufficient"
                            _state(symbol, state, rec.task_id, source_status="ready", missing=cached["analysis"]["missing"],
                                   next_retry_at=profile.get("cache_until"))
                            results.append({"symbol": symbol, "state": state, "cached": True,
                                            "result_key": prior.get("result_key")})
                        else:
                            original = prior.get("packet")
                            try:
                                ci.validate_packet(original)
                                if generation_key(original, model) != key or not (
                                    ci.clock(original["captured_at"]) <= ci.clock(prior["generated_at"]) <= _now()
                                    < ci.clock(prior["generated_at"]) + timedelta(days=7)
                                ):
                                    raise ValueError("stale saved selection")
                                pending.append((original, key, prior, True))
                            except (ValueError, TypeError, KeyError):
                                _state(symbol, "interrupted", rec.task_id, missing=["原始输入或生成时间无法恢复；未重发模型或延长有效期。"],
                                       next_retry_at=profile.get("cache_until"))
                                results.append({"symbol": symbol, "state": "interrupted"})
                    else:
                        _state(symbol, "interrupted", rec.task_id, next_retry_at=profile.get("cache_until"))
                        results.append({"symbol": symbol, "state": "interrupted"})
                    continue
                pending.append((packet, key, profile, False))
            fresh = [v for v in pending if not v[3]]
            selections = {}
            usage_id = None
            if fresh:
                packets = [v[0] for v in fresh]
                messages = selector_messages(packets)
                await rec.checkpoint_async()
                lease, started = None, False
                usage = {"value": None}
                try:
                    if (not model["model"]
                            or (model["provider"] == "claude_cli" and not resolve_cli_path(settings.llm_cli_path))
                            or (model["provider"] != "claude_cli" and (
                                not settings.review_llm_base_url or not settings.review_llm_api_key))):
                        raise LLMError("configured model unavailable")
                    if not explicit:
                        await _drain_stage(_automatic_daily_limit, sf)
                    lease = await _drain_stage(agent_budget.reserve, agent_budget.SCOPE_AUTONOMY_LLM, purpose="company_evidence.select",
                        kind="model", task_id=rec.task_id, provider=model["provider"], model=model["model"],
                        timeout_seconds=180, max_retries=0,
                        input_chars=sum(len(m["content"]) for m in messages), session_factory=sf)
                    usage_id = lease["id"]
                    for packet, key, _, _ in fresh:
                        _state(packet["symbol"], "generating", rec.task_id, generation_key=key, usage_id=usage_id,
                               attempt_state="reserved", selected=None, packet=packet,
                               event_ref={k: packet["event"][k] for k in ("event_id", "version_id", "observation_id")})
                    await rec.checkpoint_async()
                    await _drain_stage(agent_budget.start, usage_id, sf)
                    started = True
                    for packet, _, _, _ in fresh:
                        _state(packet["symbol"], "generating", rec.task_id, attempt_state="started")
                    raw = await _drain_stage(bounded_chat_completion, settings.review_llm_base_url,
                        settings.review_llm_api_key, model["model"], messages, provider=model["provider"],
                        cli_path=settings.llm_cli_path, timeout=180,
                        usage_callback=lambda v: usage.__setitem__("value", v))
                    await _drain_stage(agent_budget.finish, usage_id, state="succeeded", usage=usage["value"], output_chars=len(raw),
                                        attempts=1, session_factory=sf)
                    await rec.checkpoint_async()
                    selections = parse_selection_batch(raw, packets)
                    for packet, _, _, _ in fresh:
                        _state(packet["symbol"], "generating", rec.task_id, attempt_state="succeeded",
                               selected=selections[packet["id"]], generated_at=_now().isoformat(),
                               raw_response_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
                except agent_budget.BudgetError:
                    if started:
                        await _drain_stage(agent_budget.finish, usage_id, state="failed", usage=usage["value"], attempts=1,
                                            error_kind="output_budget_exceeded", session_factory=sf)
                    elif lease:
                        await _drain_stage(agent_budget.release, lease["id"], sf)
                    for packet, key, _, _ in fresh:
                        _state(packet["symbol"], "invalid_response" if started else "budget_blocked", rec.task_id,
                               generation_key=key, attempt_state="failed" if started else "not_started",
                               next_retry_at=(_now() + timedelta(minutes=30)).isoformat())
                    raise
                except BaseException as error:
                    if started:
                        await _drain_stage(agent_budget.finish, usage_id, state="canceled" if isinstance(error, asyncio.CancelledError) else "failed",
                                            usage=usage["value"], attempts=1, error_kind=type(error).__name__, session_factory=sf)
                    elif lease:
                        await _drain_stage(agent_budget.release, lease["id"], sf)
                    state = ("canceled" if isinstance(error, asyncio.CancelledError) else "interrupted"
                             if isinstance(error, TimeoutError) else "model_failed" if isinstance(error, LLMError) else "invalid_response")
                    for packet, key, _, _ in fresh:
                        _state(packet["symbol"], state, rec.task_id, generation_key=key,
                               attempt_state=("unknown" if isinstance(error, asyncio.CancelledError) else "failed") if started else "not_started",
                               missing=["任务时间已用尽；当前步骤已结束，未发布回答。" if isinstance(error, TimeoutError)
                                        else "本轮模型调用或选择未通过，未发布回答。"],
                               next_retry_at=(_now() + timedelta(minutes=30)).isoformat())
                    raise
            for packet, key, saved, reused in pending:
                await rec.checkpoint_async()
                symbol = packet["symbol"]
                selected = saved["selected"] if reused else selections[packet["id"]]
                record = load_state(symbol)
                current = await _drain_stage(_event, symbol, app, sf, event_id=packet["event"]["event_id"])
                profile = read_profile(symbol)
                if current is None or profile is None or generation_key(_packet(symbol, profile, current), model) != key:
                    _state(symbol, "interrupted", rec.task_id, missing=["采集后公司原文或事件版本已变，旧回答未发布。"], next_retry_at=None)
                    results.append({"symbol": symbol, "state": "interrupted"})
                    continue
                await rec.checkpoint_async()
                result = ci.publish_automatic(packet, selected, model=model, usage_id=record["usage_id"],
                                               generated_at=record["generated_at"])
                analysis, _ = compile_analysis(packet, selected)
                state = "ready" if analysis["status"] == "conditional" else "insufficient"
                _state(symbol, state, rec.task_id, result_key=result, generation_key=key,
                       source_status="ready", missing=analysis["missing"], next_retry_at=profile.get("cache_until"))
                results.append({"symbol": symbol, "state": state, "result_key": result})
            await rec.add_async(name="公司判读结果", input_summary=f"{len(symbols)}只", output_summary=f"保存/复用{len(results)}项；不改变选股资格",
                    duration_ms=0, ok=True, llm={**model, "enhanced": bool(fresh)})
            return {"kind": "company_evidence", "symbols": results, "model_calls": int(bool(fresh)),
                    "state": "processed" if results else "no_due_candidates" if symbols else "no_candidates"}
        except asyncio.CancelledError:
            for symbol in touched:
                if load_state(symbol).get("state") in {"queued", "collecting", "generating"}:
                    _state(symbol, "canceled", rec.task_id)
            raise


async def scheduler_tick(app):
    if not settings.company_evidence_enabled:
        return
    from app.services import agent_tasks

    sf = get_session_factory()
    symbols = await _drain_stage(candidate_symbols, sf)
    for symbol in symbols:
        item = await _drain_stage(_event, symbol, app, sf)
        if _due(symbol, item):
            try:
                await agent_tasks.create_task_async("company_evidence", {"automatic": True}, created_by="scheduler")
            except agent_tasks.TaskQueueUnavailable:
                raise  # Registry health must observe persistence failures.
            except RuntimeError:
                pass  # The existing task owner is still running; no parallel queue.
            return
