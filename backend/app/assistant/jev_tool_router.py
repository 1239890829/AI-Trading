"""Jev-based assistant tool-group router.

The execution allowlist remains TOOL_SPECS. This module only narrows which
read-only tools are described to the generative model after a calibrated
shadow period; it never grants a tool or executes one.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from urllib.parse import urlsplit
from uuid import uuid4
from typing import Any

from app.assistant.tools.registry import TOOL_SPECS
from app.core.jev_client import evaluate, record_routing_observation

log = logging.getLogger(__name__)

TOOL_GROUPS: dict[str, dict[str, Any]] = {
    "market_realtime": {
        "description": "仅当问题需要当前/历史行情、盘口逐笔、K线分时、大盘、板块/资金流、大宗或气候数值时使用；一般新闻/公告/持仓身份查询不属于本组。",
        "tools": (
            "quotes", "orderbook", "trades", "auction", "kline", "minute",
            "market_overview", "boards", "board_flow", "capital_flow",
            "commodity", "climate",
        ),
    },
    "market_structure": {
        "description": "仅当问题明确需要涨跌停/炸板/龙虎榜、人气异动、情绪相位、题材梯队或题材成分时使用；普通公司公告不属于本组。",
        "tools": (
            "limit_up", "limit_down", "limit_break", "longhu", "hot", "anomaly",
            "sentiment", "themes", "theme_members",
        ),
    },
    "events_news": {
        "description": "盘中事件、全网资讯、传导链、预警记录、公司资料/公告/相关新闻；询问某股今天发生什么、为何异动、有什么公告时优先属于本组。",
        "tools": ("events", "news", "chain", "alert_events", "basics"),
    },
    "selection_research": {
        "description": "仅当用户明确询问每日精选、因子档案、策略回测、盘前简报、盘后复盘、做T决策记录或KB知识正文/反例时使用；一般的持仓、新闻、公告或行情查询不属于本组。",
        "tools": ("picks", "factor_profile", "backtest", "brief", "review", "minute_decisions", "kb"),
    },
    "account_positions": {
        "description": "仅当问题需要当前持仓、自选股身份/清单或模拟交易账户数据时使用；询问某只非持仓股票不自动属于本组。",
        "tools": ("positions", "watchlist", "paper"),
    },
    "governance": {
        "description": "仅当问题明确询问参数变更审计、Agent 任务中心或系统治理状态时使用；普通投资研究不属于本组。",
        "tools": ("param_changes", "agent_tasks"),
    },
}


def all_group_tools() -> set[str]:
    return {name for group in TOOL_GROUPS.values() for name in group["tools"]}


def validate_group_coverage() -> None:
    missing = set(TOOL_SPECS) - all_group_tools()
    extra = all_group_tools() - set(TOOL_SPECS)
    if missing or extra:
        raise RuntimeError(f"Jev tool groups drifted: missing={sorted(missing)} extra={sorted(extra)}")


# These checks describe installed dependencies, not freshness or upstream health.
# All candidates still come from the single execution registry.
_DB_TOOLS = {"positions", "watchlist", "picks", "minute_decisions", "events",
             "sentiment", "alert_events", "review", "param_changes", "agent_tasks"}
_PRIVATE_REQUEST = re.compile(r"持仓|仓位|账户|自选|成本价|盈亏|资金余额|资产余额|我买|我卖|草稿|密码|凭据")
_EXPLICIT_TOOLS = {
    "market_overview": r"大盘|两市成交|涨跌家数",
    "limit_up": r"涨停池|涨停板|连板天梯",
    "limit_down": r"跌停池", "limit_break": r"炸板池",
    "longhu": r"龙虎榜", "minute": r"分时", "kline": r"[Kk]\s*线",
    "news": r"全网(?:新闻|资讯)|快讯", "basics": r"公告|财报",
}
_ROUTE_SLOT = threading.Lock()


def available_tools(ctx) -> set[str]:
    names = set(TOOL_SPECS)
    if ctx.session_factory is None:
        names -= _DB_TOOLS
    for name, dependency in (("paper", "paper_engine"), ("news", "event_store"),
                             ("theme_members", "theme_catalog")):
        if getattr(ctx, dependency, None) is None:
            names.discard(name)
    if ctx.hub is None and ctx.snapshot_service is None:
        names.discard("market_overview")
    return names


def explicit_requirements(user_text: str) -> set[str]:
    # An observable request hint, not gold or a general semantic intent classifier.
    if re.search(r"解释|什么是|原理|怎么形成|如何形成|定义", user_text):
        return set()
    return {name for name, pattern in _EXPLICIT_TOOLS.items() if re.search(pattern, user_text)}


def _page_state(page: Any | None) -> dict:
    # Free-form page titles/context/drafts are never sent to the router.
    if page is None:
        return {}
    get = page.get if isinstance(page, dict) else lambda k: getattr(page, k, None)
    path = urlsplit(str(get("path") or "")).path
    allowed_paths = {"/workbench", "/market", "/stock", "/events", "/themes", "/picks", "/review", "/research"}
    state = {"path": path} if path in allowed_paths else {}
    symbol = str(get("symbol") or "")
    if re.fullmatch(r"\d{6}", symbol):
        state["symbol"] = symbol
    return state


def _fallback(baseline: set[str], reason: str, **extra) -> dict:
    return {"ok": False, "narrowed": False, "tools": sorted(baseline),
            "baseline": sorted(baseline), "reason": reason, **extra}


def route_tool_groups(user_text: str, page: Any | None = None,
                      allowed_tools: set[str] | None = None) -> dict:
    """Suggest only installed, caller-authorized candidates; never execute a tool."""
    from app.core.config import settings

    validate_group_coverage()
    baseline = set(TOOL_SPECS) if allowed_tools is None else set(allowed_tools) & set(TOOL_SPECS)
    mode = str(getattr(settings, "jev_assistant_tool_mode", "off") or "off").strip().lower()
    required = explicit_requirements(str(user_text or ""))
    if mode not in {"shadow", "cascade"}:
        return _fallback(baseline, "off", required=sorted(required))
    page_state = _page_state(page)
    if _PRIVATE_REQUEST.search(str(user_text)) or re.search(
        r"positions|watchlist|paper|account|draft",
        str((page or {}).get("path", "") if isinstance(page, dict) else getattr(page, "path", "")), re.I
    ):
        return _fallback(baseline, "private_context", required=sorted(required))
    if required:
        return _fallback(baseline, "explicit_request", required=sorted(required))
    groups = {g: spec for g, spec in TOOL_GROUPS.items() if baseline & set(spec["tools"])}
    if len(groups) < 2:
        return _fallback(baseline, "no_competition")

    state = {
        "request": str(user_text or "")[:4000],
        "page": page_state,
        "available_tools": sorted(baseline),
    }
    questions = {}
    for group, spec in groups.items():
        questions[group] = {
            "type": "noul",
            "instructions": {
                "task": f"判断当前用户问题是否可能需要工具组 {group} 才能基于真实系统数据回答。",
                "rules": [
                    "可以同时需要多个工具组。",
                    "只判断是否需要该类真实数据，不直接回答用户问题。",
                    "如果纯常识/解释即可回答，相关工具组应判否。",
                ],
            },
            "criteria": {
                "true": spec["description"],
                "false": "当前问题无需这个工具组的数据即可正确回答。",
            },
        }

    result = evaluate(state, questions, purpose="assistant_tool_router")
    if not isinstance(result, dict):
        return _fallback(baseline, "invalid_response")
    metadata = {k: result.get(k) for k in ("model", "usage", "latency_ms")}
    if not result.get("ok"):
        return _fallback(baseline, result.get("reason") or "unavailable", **metadata)

    answers = result.get("answers")
    if not isinstance(answers, dict):
        return _fallback(baseline, "invalid_answers", **metadata)
    probs: dict[str, float] = {}
    for group in groups:
        answer = answers.get(group)
        p = answer.get("noul") if isinstance(answer, dict) and answer.get("type", "noul") == "noul" else None
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0.0 <= float(p) <= 1.0:
            return _fallback(baseline, "invalid_noul", **metadata)
        probs[group] = float(p)

    threshold = float(getattr(settings, "jev_assistant_tool_min_noul", 0.60))
    max_groups = max(1, int(getattr(settings, "jev_assistant_tool_max_groups", 3)))
    selected_groups = [g for g, p in sorted(probs.items(), key=lambda kv: (-kv[1], kv[0])) if p >= threshold]
    selected_groups = selected_groups[:max_groups]
    if not selected_groups:
        record_routing_observation("assistant_tools", len(baseline), len(baseline), coverage_ok=None)
        return {
            "ok": True, "narrowed": False, "tools": sorted(baseline), "baseline": sorted(baseline),
            "groups": [], "probabilities": probs, "reason": "no_group_above_threshold", **metadata,
        }

    selected = baseline & {name for group in selected_groups for name in TOOL_GROUPS[group]["tools"]}
    record_routing_observation("assistant_tools", len(baseline), len(selected), coverage_ok=None)
    return {
        "ok": True,
        "narrowed": len(selected) < len(baseline),
        "tools": sorted(selected),
        "baseline": sorted(baseline),
        "model": result.get("model"),
        "usage": result.get("usage"),
        "latency_ms": result.get("latency_ms"),
        "groups": selected_groups,
        "probabilities": probs,
        "reason": "jev_groups",
    }

def observe_route(route: dict | None, used_tools: list[str]) -> None:
    """Compare a shadow/cascade shortlist with tools the generative model actually used."""
    if not route or not route.get("ok"):
        return
    selected = set(route.get("tools") or [])
    used = {str(x) for x in used_tools if x}
    # No tools were needed: useful for reduction stats, but there is no positive recall target.
    coverage_ok = None if not used else used <= selected
    record_routing_observation(
        "assistant_tools_observed",
        len(route.get("baseline") or TOOL_SPECS),
        len(selected),
        coverage_ok=coverage_ok,
    )
    if coverage_ok is False:
        log.info("Jev assistant tool shadow missed %d used tool(s)", len(used - selected))


class RouteTrace:
    """One request's metadata; no raw request, arguments, response, or portfolio."""
    def __init__(self, mode, baseline, text, session_factory):
        self.id = uuid4().hex
        self.mode = mode
        self.baseline = set(baseline)
        self.input_sha256 = hashlib.sha256(text.encode()).hexdigest()
        self.required = explicit_requirements(text)
        self.sf = session_factory
        self.route = _fallback(self.baseline, "unavailable")
        self.calls: list[dict] = []
        self.terminal = "failed"
        self.false_denial = False
        self.presented = set(baseline)
        self.registry_sha256 = hashlib.sha256(json.dumps(
            {name: [spec.desc, spec.params, spec.handler.__module__, spec.handler.__qualname__]
             for name, spec in TOOL_SPECS.items()}, sort_keys=True, ensure_ascii=False,
        ).encode()).hexdigest()

    def start(self, text, page):
        if not _ROUTE_SLOT.acquire(blocking=False):
            self.route = _fallback(self.baseline, "skipped_busy")
            return None

        def run():
            try:
                return route_tool_groups(text, page, self.baseline)
            except Exception as exc:  # routing must fall back to the authorized baseline
                return _fallback(self.baseline, type(exc).__name__)
            finally:
                _ROUTE_SLOT.release()  # release only when the synchronous worker actually exits
        return asyncio.create_task(asyncio.to_thread(run), name="assistant-route")

    async def finish(self, task):
        if task is not None:
            try:
                self.route = await asyncio.shield(task)
            except asyncio.CancelledError:
                # Canceling to_thread's coroutine does not stop the model HTTP worker.
                self.route = await asyncio.shield(task)
                await asyncio.to_thread(self.save)
                raise
        await asyncio.to_thread(self.save)

    def save(self):
        executed = [c["tool"] for c in self.calls if c["state"] in {"returned", "failed", "cache_hit"}]
        try:
            observe_route(self.route, executed)
        except Exception as exc:
            log.warning("assistant route telemetry failed: %s", type(exc).__name__)
        payload = {
            "terminal": self.terminal, "reason": self.route.get("reason"),
            "recommendation": self.route.get("tools", []),
            "groups": self.route.get("groups", []), "probabilities": self.route.get("probabilities", {}),
            "adopted": self.presented != self.baseline,
            "presented_tools": sorted(self.presented),
            "outside_shortlist": sorted(set(executed) - set(self.route.get("tools", []))),
            "fallback": self.presented == self.baseline,
            "fallback_reason": "shadow_original" if self.mode == "shadow" else self.route.get("reason"),
            "calls": self.calls, "executed": sorted(set(executed)),
            "required_missing": sorted(self.required - set(executed)),
            "required_unavailable": sorted(self.required - self.baseline),
            "false_denial_hint": self.false_denial,
            "model": self.route.get("model"), "usage": self.route.get("usage"),
            "latency_ms": self.route.get("latency_ms"), "human_coverage": None,
        }
        if self.sf is None:
            log.info("assistant route receipt unavailable: no session factory")
            return
        try:
            from app.models.agent import AgentAudit
            with self.sf() as db:
                db.add(AgentAudit(
                    actor="assistant", action="assistant.route", target=self.id,
                    before=json.dumps({"contract": "assistant-route-v1", "mode": self.mode,
                                       "input_sha256": self.input_sha256, "registry_sha256": self.registry_sha256,
                                       "authorized_available": sorted(self.baseline),
                                       "filtered_unavailable": sorted(set(TOOL_SPECS) - self.baseline)},
                                      ensure_ascii=False),
                    after=json.dumps(payload, ensure_ascii=False),
                ))
                db.commit()
        except Exception as exc:  # an audit failure cannot invalidate a streamed answer
            log.warning("assistant route receipt failed: %s", type(exc).__name__)
