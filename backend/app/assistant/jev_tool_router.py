"""Jev-based assistant tool-group router.

The execution allowlist remains TOOL_SPECS. This module only narrows which
read-only tools are described to the generative model after a calibrated
shadow period; it never grants a tool or executes one.
"""
from __future__ import annotations

import logging
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
        "description": "仅当用户明确询问每日精选、因子档案、策略回测、盘前简报、盘后复盘或做T决策记录时使用；一般的持仓、新闻、公告或行情查询不属于本组。",
        "tools": ("picks", "factor_profile", "backtest", "brief", "review", "minute_decisions"),
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


def _page_state(page: Any | None) -> dict:
    if page is None:
        return {}
    if isinstance(page, dict):
        return {k: page.get(k) for k in ("path", "symbol", "title") if page.get(k)}
    return {
        k: getattr(page, k)
        for k in ("path", "symbol", "title")
        if getattr(page, k, None)
    }

def route_tool_groups(user_text: str, page: Any | None = None) -> dict:
    """Return a fail-open tool shortlist. No tool execution happens here."""
    from app.core.config import settings

    validate_group_coverage()
    mode = str(getattr(settings, "jev_assistant_tool_mode", "off") or "off").strip().lower()
    baseline = set(TOOL_SPECS)
    if mode not in {"shadow", "cascade"}:
        return {"ok": False, "narrowed": False, "tools": sorted(baseline), "reason": "off"}

    state = {
        "request": str(user_text or "")[:4000],
        "page": _page_state(page),
    }
    questions = {}
    for group, spec in TOOL_GROUPS.items():
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
    if not result.get("ok"):
        return {"ok": False, "narrowed": False, "tools": sorted(baseline), "reason": result.get("reason")}

    probs: dict[str, float] = {}
    for group in TOOL_GROUPS:
        answer = (result.get("answers") or {}).get(group)
        p = answer.get("noul") if isinstance(answer, dict) else None
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0.0 <= float(p) <= 1.0:
            return {"ok": False, "narrowed": False, "tools": sorted(baseline), "reason": "invalid_noul"}
        probs[group] = float(p)

    threshold = float(getattr(settings, "jev_assistant_tool_min_noul", 0.60))
    max_groups = max(1, int(getattr(settings, "jev_assistant_tool_max_groups", 3)))
    selected_groups = [g for g, p in sorted(probs.items(), key=lambda kv: (-kv[1], kv[0])) if p >= threshold]
    selected_groups = selected_groups[:max_groups]
    if not selected_groups:
        record_routing_observation("assistant_tools", len(baseline), len(baseline), coverage_ok=None)
        return {
            "ok": True, "narrowed": False, "tools": sorted(baseline),
            "groups": [], "probabilities": probs, "reason": "no_group_above_threshold",
        }

    selected = {name for group in selected_groups for name in TOOL_GROUPS[group]["tools"]}
    record_routing_observation("assistant_tools", len(baseline), len(selected), coverage_ok=None)
    return {
        "ok": True,
        "narrowed": len(selected) < len(baseline),
        "tools": sorted(selected),
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
        len(TOOL_SPECS),
        len(selected),
        coverage_ok=coverage_ok,
    )
    if coverage_ok is False:
        log.info("Jev assistant tool shadow missed %d used tool(s)", len(used - selected))
