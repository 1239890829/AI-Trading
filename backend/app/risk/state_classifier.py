"""市场状态分类器。

基于现有数据（指数快照、市场宽度、情绪引擎）做规则判定，
输出状态 + 依据，供风险引擎与前端展示。
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def classify_market_state(indices: dict, breadth: dict | None, sentiment: dict | None) -> tuple[str, list[str]]:
    """返回 (state, reasons)。"""
    reasons: list[str] = []

    if not breadth or not sentiment:
        return "数据不足", ["市场宽度或情绪数据尚未就绪"]

    # 指数趋势：上证指数 vs MA20/MA60（ hub.indices 里已有 change_pct，趋势用价格位置粗略估计）
    sh = indices.get("000001")
    sh_price = _safe_float(_get(sh, "price"))
    sh_change = _safe_float(_get(sh, "change_pct"))
    # 当日涨幅 >1% 视为短期强，<-1% 视为弱
    short_trend = "strong" if sh_change > 1.0 else ("weak" if sh_change < -1.0 else "neutral")
    reasons.append(f"上证指数 {sh_price:.2f}（{sh_change:+.2f}%），短期趋势={short_trend}")

    # 宽度
    up = int(breadth.get("up", 0))
    down = int(breadth.get("down", 0))
    total = int(breadth.get("total", 1))
    advance_ratio = up / total if total > 0 else 0.0
    limit_up = int(breadth.get("limit_up", 0))
    limit_down = int(breadth.get("limit_down", 0))
    reasons.append(f"涨跌比 {up}/{down}={advance_ratio:.1%}，涨停{limit_up}/跌停{limit_down}")

    # 情绪
    phase = sentiment.get("phase") or "未知"
    temperature = _safe_float(sentiment.get("temperature"))
    confidence = sentiment.get("confidence") or "低"
    reasons.append(f"情绪阶段={phase}，温度={temperature:.1f}，置信度={confidence}")

    # 波动/恐慌：跌停家数多 或 指数跌幅大
    panic = limit_down >= 50 or sh_change < -3.0
    if panic:
        return "恐慌/极端波动", reasons + ["跌停家数≥50 或 上证跌幅>3%，触发极端波动状态"]

    # 趋势组合判定
    if short_trend == "strong" and advance_ratio > 0.55 and temperature >= 50:
        return "强势多头", reasons
    if short_trend == "weak" and advance_ratio < 0.45 and temperature < 40:
        return "下跌趋势", reasons
    if advance_ratio >= 0.50 and temperature >= 40:
        return "震荡偏多", reasons
    if advance_ratio <= 0.45 and temperature < 45:
        return "震荡偏空", reasons
    return "震荡", reasons
