"""相位对账（strategy-evolution-plan 方向 1 P1）：昨日 switch_conditions 预测 vs 今日实际相位。

「今晚的判断变成明早能对账的东西」（engine.compute_sentiment 的注释）——
本模块把这句话从口号变成数据：sentiment_history 里昨日存档的切换条件文本
↔ 今日实际相位落点。

判定语义（四态，缺失显式 unavailable，绝不冒充命中）：
- transition_confirmed：今日相位 ∈ 昨日提示的切换目标集 且 ≠ 昨日相位；
- held：今日相位 == 昨日相位——switch 文本是叙事近似（引擎实际用 heat×earning
  矩阵），「条件未触发而延续」无法从文本严格证伪，如实标注而非当命中；
- off_path：今日相位既非延续、也不在昨日提示的目标集内——文本阈值与矩阵
  实现出现偏差或极端行情，复盘必须看见这类偏差；
- unavailable：无前一交易日存档（首日/序列断档），不判。

预测目标集解析：switch_conditions 按 '；' 分支、取每支 '→' 之后的相位名
（与 PHASE_ORDER 交集，防文本噪声误入）。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from app.sentiment.engine import PHASE_ORDER

log = logging.getLogger(__name__)


def parse_predicted_targets(switch_text: str | None) -> list[str]:
    """switch 条件文本 → 提示的切换目标相位集（保序去重）。"""
    if not switch_text:
        return []
    targets: list[str] = []
    for branch in str(switch_text).split("；"):
        if "→" not in branch:
            continue
        tail = branch.split("→", 1)[1]
        for ph in PHASE_ORDER:
            if ph in tail and ph not in targets:
                targets.append(ph)
    return targets


def reconcile(prev_entry: dict | None, today: dict) -> dict:
    """昨日情绪存档 + 今日情绪 → 对账结果（纯函数）。

    :param prev_entry: sentiment_history 前一交易日 detail JSON（含 phase/switch_conditions）。
    :param today: 今日 compute_sentiment 输出（phase/switch_conditions/indicators...）。
    """
    if not prev_entry or not prev_entry.get("phase"):
        return {
            "verdict": "unavailable",
            "verdict_label": "无法对账",
            "reason": "无前一交易日情绪存档（sentiment_history 缺失）",
            "prev": None,
            "predicted_targets": [],
            "actual_phase": today.get("phase"),
        }
    prev_phase = str(prev_entry["phase"])
    actual = today.get("phase")
    if not actual:
        # 今日相位缺失（采集 gap）：无法对账，绝不把「缺失」判成「偏离」
        return {
            "verdict": "unavailable",
            "verdict_label": "无法对账",
            "reason": "今日情绪相位缺失（采集失败），对账不成立",
            "prev": {"phase": prev_phase, "switch_conditions": prev_entry.get("switch_conditions")},
            "predicted_targets": [],
            "actual_phase": None,
        }
    predicted = parse_predicted_targets(prev_entry.get("switch_conditions"))

    if actual == prev_phase:
        verdict, label = "held", "相位延续"
        reason = f"昨日 {prev_phase} → 今日 {actual}；切换条件未触发（或未达阈值）"
    elif actual in predicted:
        verdict, label = "transition_confirmed", "切换兑现"
        reason = f"昨日 {prev_phase} 提示切换，今日实际落点 {actual}（在预测路径内）"
    else:
        verdict, label = "off_path", "偏离预测路径"
        reason = (
            f"昨日 {prev_phase} → 今日 {actual}；"
            f"不在昨日提示的目标集 {predicted or '（空）'} 内"
        )

    return {
        "verdict": verdict,
        "verdict_label": label,
        "reason": reason,
        "prev": {
            "phase": prev_phase,
            "switch_conditions": prev_entry.get("switch_conditions"),
        },
        "predicted_targets": predicted,
        "actual_phase": actual,
    }


def load_prev_entry(session_factory, today_key: str | None) -> dict | None:
    """sentiment_history → 今日之前最近一条的 detail JSON。

    :param today_key: 今日 YYYYMMDD；None 取库内最新一条（无今日锚时无从比较，
    返回最新存档供 unavailable 之外的兜底展示）。
    detail 损坏时降级用行级 phase 列（switch_conditions 显式 None，不臆造）。
    """
    from app.market.sentiment_history import SentimentHistoryRow

    with session_factory() as db:
        q = select(SentimentHistoryRow).order_by(SentimentHistoryRow.trade_date.desc())
        if today_key:
            q = q.where(SentimentHistoryRow.trade_date < today_key)
        row = db.execute(q.limit(1)).scalars().first()
    if row is None:
        return None
    try:
        return json.loads(row.detail or "{}") or None
    except (TypeError, ValueError):
        return {"phase": row.phase, "switch_conditions": None}
