"""Same-day execution recheck of preserved selection facts; never a new score or order."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math

from app.core.bjtime import beijing_now, to_beijing
from app.core.freshness import DEFAULT_FRESH_WITHIN_SECONDS, MAX_FUTURE_SKEW_SECONDS
from app.picks.gate import apply_gate_to_picks, describe_gate_context, evaluate_stand_aside
from app.picks.meta_confidence import DIMENSIONS, classify_confidence
from app.sentiment.engine import _PHASE_MATRIX

VERSION = "daily-recheck-v1"


def _clock(value):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        return to_beijing(stamp) if isinstance(stamp, datetime) and stamp.tzinfo else None
    except (TypeError, ValueError):
        return None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def seed_digest(seed):
    return hashlib.sha256(json.dumps(seed, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def gate_from_sentiment(sent: dict, stored: dict | None = None) -> dict:
    """Use the original gate thresholds and the existing shared metric owner."""
    from app.sentiment import metric_history

    gi = sent.get("gate_inputs") or {}
    promo, breaks = gi.get("promotion_1to2"), gi.get("break_rate")

    def percentiles():
        return ((metric_history.percentile_of_value("promo_1to2", promo) or {}).get("percentile"),
                (metric_history.percentile_of_value("break_rate", breaks) or {}).get("percentile"))

    try:
        p, b = await asyncio.to_thread(percentiles)
    except Exception:
        # Original gate's explicit absolute-threshold fallback, not a passing score.
        p, b = None, None
    gate = evaluate_stand_aside(phase=sent.get("phase"), promotion_1to2=promo,
        promotion_1to2_pctl=p, break_rate=breaks, break_rate_pctl=b,
        limit_down=gi.get("limit_down"), prev_zt_median_pct=gi.get("prev_zt_median_pct"),
        phase_unreliable=bool(sent.get("phase_unreliable")))
    gate.update(presentation=describe_gate_context(sent, gate), gate_source="live", recheck={
        "phase": sent.get("phase"), "trade_date": sent.get("trade_date"),
        "judged_at": sent.get("judged_at"), "stored_phase": (stored or {}).get("phase"),
        "phase_changed": (stored or {}).get("phase") != sent.get("phase"),
        "inputs_source": "sentiment.gate_inputs", "break_caliber": gi.get("break_caliber")})
    return gate


def context_issue(context, now):
    if not isinstance(context, dict) or context.get("state") != "ready":
        return "当前宏观执行复核不可用，不沿用早盘结论授权"
    if context.get("trade_date") != now.date().isoformat() or context.get("source_state") != "ready":
        return "宏观复核交易日或来源状态不匹配"
    if context.get("phase") not in {p for row in _PHASE_MATRIX for p in row} or not isinstance(context.get("gate"), dict):
        return "宏观相位或闸门事实缺失"
    if not isinstance(context["gate"].get("stand_aside"), bool):
        return "宏观闸门未提供明确判定"
    if context["gate"].get("phase") != context["phase"]:
        return "宏观相位与闸门来源不一致"
    for field in ("judged_at", "source_as_of"):
        stamp = _clock(context.get(field))
        if stamp is None:
            return "宏观复核缺少带时区的源时点"
        age = (now - stamp).total_seconds()
        if stamp.date() != now.date() or not -MAX_FUTURE_SKEW_SECONDS <= age <= DEFAULT_FRESH_WITHIN_SECONDS:
            return "宏观复核时点已过期或尚未发生"
    return None


async def live_execution_context(state, *, now):
    from app.services.market_context import get_cached_sentiment

    context = {"state": "unavailable", "trade_date": now.date().isoformat(),
               "judged_at": now.isoformat(), "reason": "当前宏观资料未就绪"}
    try:
        sent = await get_cached_sentiment(state, state.hub)
        fresh = sent.get("snapshot_freshness") or {}
        context.update(phase=sent.get("phase"), trade_date=sent.get("trade_date"),
                       judged_at=sent.get("judged_at"), source_as_of=fresh.get("as_of"),
                       source_state=fresh.get("state"))
        if sent.get("phase_unreliable"):
            context["reason"] = "宏观相位的原始判定不可靠"
            return context
        gate = await gate_from_sentiment(sent)
        context.update(state="ready", gate={k: v for k, v in gate.items()
                       if k not in {"presentation", "recheck", "gate_source"}})
        # Source work is awaited; its resulting facts cannot be stamped at request start.
        checked = beijing_now()
        issue = context_issue(context, checked)
        if checked.date() != now.date():
            issue = "宏观复核跨交易日完成，等待下一拍重新读取原入选"
        if issue:
            context.update(state="unavailable", reason=issue)
        else:
            context.pop("reason", None)
    except Exception as exc:
        context.update(state="unavailable", reason=f"宏观复核读取失败（{type(exc).__name__}）")
    return context


def _seed_issue(item, seed):
    if not isinstance(seed, dict) or seed.get("version") != 1:
        return "未保存未遮罩的原执行要素；不由历史卡片重造买入区间"
    required = {"score", "sub_scores", "dimension_evidence", "vetoes", "halt_penalty",
                "chip_signal", "buy_range", "observation_only", "follow_state"}
    if not required <= seed.keys() or not _number(seed.get("score")):
        return "原执行要素不完整"
    if seed["score"] != item.get("score") or seed["vetoes"] != (item.get("vetoes") or []):
        return "原执行要素与入选记录不一致"
    if (not isinstance(seed["sub_scores"], dict) or not isinstance(seed["dimension_evidence"], dict)
            or not isinstance(seed["vetoes"], list) or not isinstance(seed["observation_only"], bool)):
        return "原执行要素类型无效"
    if any(key not in DIMENSIONS or not _number(value) for key, value in seed["sub_scores"].items()):
        return "原评分分项无效"
    if not _number(seed["halt_penalty"]) or seed["halt_penalty"] < 0:
        return "原风险评估缺失或无效，不把未知扣分当零"
    br = seed.get("buy_range")
    if br is not None and (not isinstance(br, dict) or not all(_number(br.get(k)) and br[k] > 0 for k in ("low", "high"))
                           or br["low"] > br["high"]):
        return "原买入区间无效，不重造区间"
    try:
        seed_digest(seed)
    except (ValueError, TypeError):
        return "原执行要素无法核验"
    return None


def requalify_item(item, source_version, context, now):
    """Return a copy. Original selection, scores, vetoes and reference range never change."""
    out, seed = deepcopy(item), item.get("execution_seed")
    issue = _seed_issue(item, seed) or context_issue(context, now)
    qualification = {"version": VERSION, "selection_source_version": source_version,
                     "seed_digest": seed_digest(seed) if isinstance(seed, dict) and not _seed_issue(item, seed) else None,
                     "state": "unavailable" if issue else "ready", "reason": issue,
                     "context": deepcopy(context)}
    out["_execution_qualification"] = qualification
    if issue:
        out["confidence"] = {"tier": "observe", "label": "观察", "reasons": [issue]}
        out["observation_only"] = True
        out.pop("buy_range", None)
        return out
    out.update(buy_range=deepcopy(seed["buy_range"]), observation_only=seed["observation_only"])
    if seed["follow_state"] is None:
        out.pop("follow_state", None)
    else:
        out["follow_state"] = seed["follow_state"]
        if seed["follow_state"] == "blocked":
            out["observation_only"] = True
    out["confidence"] = classify_confidence(score=seed["score"], sub_scores=seed["sub_scores"],
        dimension_evidence=seed["dimension_evidence"], phase=context["phase"],
        chip_signal=seed["chip_signal"], halt_penalty=seed["halt_penalty"], veto_count=len(seed["vetoes"]))
    out = apply_gate_to_picks([out], context["gate"])[0]
    return out


def resolve_execution_item(item, contract, source_version, now):
    """Verify the archived recheck against the current original selection before an action."""
    qualification = contract.get("qualification")
    if qualification is None and "execution_seed" not in item:
        # Preserve the pre-existing fully qualified static path; never upgrade old observe rows.
        if item.get("observation_only"):
            return None, None, "原入选仅观察，缺少可重评的原执行要素"
        return deepcopy(item), None, None
    if (not isinstance(qualification, dict) or qualification.get("version") != VERSION
            or qualification.get("selection_source_version") != source_version
            or qualification.get("state") != "ready"):
        return None, None, "缺少同版有效执行复核"
    seed = item.get("execution_seed")
    issue = _seed_issue(item, seed)
    if issue:
        return None, None, issue
    if qualification.get("seed_digest") != seed_digest(seed):
        return None, None, "原执行要素身份已变化"
    context = qualification.get("context")
    issue = context_issue(context, now)
    if issue:
        return None, None, issue
    effective = requalify_item(item, source_version, context, now)
    inputs = contract.get("gate_inputs") or {}
    if ((effective.get("confidence") or {}).get("tier") != inputs.get("confidence_tier")
            or effective.get("buy_range") != inputs.get("buy_range")
            or bool(effective.get("observation_only")) != inputs.get("observation_only")
            or effective.get("follow_state") != inputs.get("follow_state")
            or effective.get("vetoes", []) != inputs.get("vetoes", [])):
        return None, None, "执行复核与原规则重建结果不一致"
    return effective, deepcopy(context), None
