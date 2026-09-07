"""信号健康度监控（方向 1「动态适应」× 方向 5「复盘迭代」的反馈环）。

把 DailyPickReview（每日精选复盘命中记录：verdict/excess_pct）聚合为
组合日序列，输出滚动胜率、期望超额与 **CUSUM 均值下漂检测**——
策略趋于失效时显式预警，而不是靠人翻复盘发现。

三态纪律：
- 样本不足（组合日 < min_groups）→ status="insufficient"，**不判 ok**；
- 基准缺失（excess_pct=None）的观测跳过统计，绝不填 0（"没参与"≠"超额为0"）；
- 本服务只做评估与呈现；预警接线（通知中心/自动 action_items）为 P1。

CUSUM（单侧下漂）：
  S_0=0; S_t = max(0, S_{t-1} + (mu0 - x_t) - delta)；S 超过 threshold → drift。
  mu0 取全历史组合日超额均值（基线），delta 为容忍漂移（结构性换挡时
  避免瞬时噪声触发）。检测的是「持续跑输历史基线」。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import select

log = logging.getLogger(__name__)

WINDOW_GROUPS = 20        # 滚动统计窗口（组合日数）
MIN_GROUPS = 10           # 判 ok 的最少样本（不足 → insufficient）
CUSUM_DELTA = 0.10        # 容忍漂移（pct）
CUSUM_THRESHOLD = 1.5     # 漂移累积告警阈值
WARN_WIN_RATE = 0.40      # 滚动 good 占比低于此 → warning
WARN_MEAN_EXCESS = -1.0   # 滚动日均超额低于此（pct）→ warning


def evaluate_signal_health(
    groups: list[dict],
    window: int = WINDOW_GROUPS,
    min_groups: int = MIN_GROUPS,
    cusum_delta: float = CUSUM_DELTA,
    cusum_threshold: float = CUSUM_THRESHOLD,
) -> dict:
    """纯函数：按 date 升序的组合日序列 → 健康度评估。

    :param groups: [{date, phase, n, good, bad, flat, mean_excess}]
                   mean_excess 允许 None（当日全部缺基准）。
    :returns: {status, window: {...}, cusum: {...}, history: [...], counts}
    """
    if not groups:
        return {"status": "insufficient", "reason": "无命中记录", "history": [],
                "window": None, "cusum": None, "counts": {"groups": 0}}

    recent = groups[-window:]
    total = sum(g["n"] for g in recent)
    good = sum(g["good"] for g in recent)
    bad = sum(g["bad"] for g in recent)
    flat = sum(g["flat"] for g in recent)
    win_rate = round(good / total, 4) if total > 0 else None

    xs = [g["mean_excess"] for g in groups if g["mean_excess"] is not None]
    mean_excess = round(sum(xs) / len(xs), 4) if xs else None

    # —— CUSUM 下漂（全历史序列；None 观测跳过不断链）——
    cusum_out: dict | None = None
    if len(xs) >= min_groups:
        mu0 = sum(xs) / len(xs)
        s = 0.0
        s_max = 0.0
        for x in xs:
            s = max(0.0, s + (mu0 - x) - cusum_delta)
            s_max = max(s_max, s)
        cusum_out = {
            "mu0": round(mu0, 4),
            "s_max": round(s_max, 4),
            "threshold": cusum_threshold,
            "delta": cusum_delta,
            "drift": bool(s_max > cusum_threshold),
        }

    # —— 状态判定（优先级：insufficient > drift > warning > ok）——
    if len(groups) < min_groups:
        status = "insufficient"
    elif cusum_out and cusum_out["drift"]:
        status = "drift"
    elif (win_rate is not None and win_rate < WARN_WIN_RATE) or (
        mean_excess is not None and mean_excess < WARN_MEAN_EXCESS
    ):
        status = "warning"
    else:
        status = "ok"

    return {
        "status": status,
        "window": {
            "groups": len(recent),
            "total_picks": total,
            "win_rate": win_rate,
            "good": good, "bad": bad, "flat": flat,
            "mean_excess": mean_excess,
        },
        "cusum": cusum_out,
        "history": [
            {
                "date": g["date"], "phase": g.get("phase"),
                "n": g["n"], "good": g["good"], "bad": g["bad"], "flat": g["flat"],
                "mean_excess": g["mean_excess"],
            }
            for g in groups
        ],
        "counts": {"groups": len(groups)},
    }


def collect_signal_health(session_factory, window: int = WINDOW_GROUPS) -> dict:
    """读库 → 组合日聚合 → evaluate。库异常显式降级（绝不静默当 ok）。"""
    try:
        with session_factory() as db:
            from app.models.daily_pick import DailyPickReview, DailyPickSet

            rows = db.execute(
                select(DailyPickReview)
                .order_by(DailyPickReview.date.asc(), DailyPickReview.symbol.asc())
            ).scalars().all()
            sets = db.execute(
                select(DailyPickSet).order_by(DailyPickSet.date.asc())
            ).scalars().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("signal_health: 读取失败，显式降级：%s", exc)
        return {"status": "error", "reason": str(exc)}

    phase_by_date: dict[str, str | None] = {}
    for s in sets:
        phase = None
        try:
            phase = (json.loads(s.meta or "{}") or {}).get("market_phase")
        except (ValueError, TypeError):
            phase = None
        phase_by_date[s.date] = phase

    by_date: dict[str, dict] = {}
    for r in rows:
        g = by_date.setdefault(
            r.date, {"date": r.date, "phase": phase_by_date.get(r.date),
                     "n": 0, "good": 0, "bad": 0, "flat": 0, "ex": []},
        )
        g["n"] += 1
        if r.verdict == "good":
            g["good"] += 1
        elif r.verdict == "bad":
            g["bad"] += 1
        else:
            g["flat"] += 1
        if r.excess_pct is not None:
            g["ex"].append(float(r.excess_pct))

    groups = [
        {
            "date": g["date"], "phase": g["phase"], "n": g["n"],
            "good": g["good"], "bad": g["bad"], "flat": g["flat"],
            "mean_excess": round(sum(g["ex"]) / len(g["ex"]), 3) if g["ex"] else None,
        }
        for g in sorted(by_date.values(), key=lambda x: x["date"])
    ]
    out = evaluate_signal_health(groups, window=window)
    out["generated_at"] = datetime.now().isoformat(timespec="seconds")
    out["source"] = "daily_pick_review"
    out["caveat"] = (
        "excess_pct 列 NOT NULL DEFAULT 0：历史基准缺失行会被固化为 0.0"
        "（note 带[基准缺失]标记可甄别），统计含此类行时偏保守"
    )
    return out
