"""Research-only Jev semantic decisions for quant experiments.

This module never changes production scores, orders, position sizes, risk gates,
or strategy parameters. It produces bounded labels for shadow datasets and
experiment routing; promotion remains governed by backtests and evidence gates.
"""
from __future__ import annotations

from typing import Any

from app.core.jev_client import evaluate

PICK_ARCHETYPES = {
    "lurk": "潜伏：题材/资金尚未一致加速，重点是起爆前埋伏与试盘回踩。",
    "first_start": "首启：从低位/沉寂状态首次形成明确启动证据，尚未进入连续加速。",
    "limit_relay": "连板接力：涨停梯队与赚钱效应支持的连续封板/接力形态。",
    "trend_acceleration": "趋势拉升：非纯连板，沿趋势结构持续抬升并出现加速。",
    "break_to_trend": "断板转趋势：连板中断后没有失效，转为趋势结构继续走强。",
    "restart": "再启动/二波：已有一轮行情或调整后重新出现有效启动。",
    "pullback_reversal": "低位反转/超跌反攻：下跌或走弱后出现可验证的修复反转。",
    "event_driven": "事件驱动：主要由新政策、产业、公司事件或供需变化触发。",
    "unclear": "证据不足或同时混合多种形态，不能可靠归类。",
}

REVIEW_FAILURES = {
    "selection": "选股本身错误：标的/题材/角色选择不对。",
    "entry": "进入位置或进入时机错误，标的方向可能没错但追高/过早。",
    "exit": "退出纪律或持有管理错误。",
    "risk": "仓位、风险暴露或硬门处理问题。",
    "environment": "市场/题材环境发生变化或原环境判断不适用。",
    "data": "数据缺失、陈旧、来源/口径错误导致判断失真。",
    "execution": "执行/系统/工具链问题，而非策略逻辑本身。",
    "unknown": "证据不足，无法可靠归因。",
}

def _choice(state: dict, *, purpose: str, instructions: str, criteria: dict[str, str]) -> dict:
    result = evaluate(
        state,
        {
            "choice": {
                "type": "choice",
                "instructions": instructions,
                "criteria": criteria,
            }
        },
        purpose=purpose,
    )
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason"), "skipped": result.get("skipped", False)}
    answer = (result.get("answers") or {}).get("choice")
    if not isinstance(answer, dict):
        return {"ok": False, "reason": "invalid_answer"}
    choice = str(answer.get("choice") or "")
    confidence = answer.get("confidence")
    if choice not in criteria or not isinstance(confidence, (int, float)):
        return {"ok": False, "reason": "invalid_choice"}
    return {
        "ok": True,
        "choice": choice,
        "confidence": float(confidence),
        "model": result.get("model"),
        "usage": result.get("usage") or {},
        "latency_ms": result.get("latency_ms"),
    }


def classify_pick_archetype(state: dict[str, Any]) -> dict:
    """Classify an already-observed candidate into a bounded shadow archetype."""
    return _choice(
        state,
        purpose="quant_shadow_pick_archetype",
        instructions=(
            "只根据调用方提供的 point-in-time 事实判断最接近的短线形态。"
            "不预测收益，不生成新战法，不把事后涨幅当作当时证据。"
            "证据混合或不足时选 unclear。"
        ),
        criteria=PICK_ARCHETYPES,
    )


def choose_bounded_tactic(
    state: dict[str, Any],
    candidates: dict[str, str],
    *,
    purpose: str = "quant_shadow_tactic_router",
) -> dict:
    """Choose only among caller-supplied, already-valid candidate tactics/actions.

    The caller remains responsible for numeric gates, T+1, fees, price limits,
    position/risk constraints, and all execution. One candidate needs no model call.
    """
    clean = {str(k): str(v) for k, v in candidates.items() if str(k) and str(v)}
    if not clean:
        return {"ok": False, "reason": "no_candidates", "skipped": True}
    if len(clean) == 1:
        only = next(iter(clean))
        return {
            "ok": True, "choice": only, "confidence": None,
            "model": "deterministic_single_candidate", "usage": {}, "latency_ms": 0.0,
        }
    return _choice(
        state,
        purpose=purpose,
        instructions=(
            "从调用方已经通过硬规则筛选的候选战术/动作中选择当前状态最匹配的一项。"
            "不得创造新动作，不得越过候选集合，不得把选择解释为收益保证。"
        ),
        criteria=clean,
    )


def classify_review_failure(state: dict[str, Any]) -> dict:
    """Shadow attribution for post-trade/post-pick review; no automatic remediation."""
    return _choice(
        state,
        purpose="quant_shadow_review_failure",
        instructions=(
            "依据调用方提供的当时证据与事后结果，对失败的主要来源做单一主因归类。"
            "区分选错、买点/时机、退出、风险、环境、数据与执行问题；证据不足选 unknown。"
        ),
        criteria=REVIEW_FAILURES,
    )
