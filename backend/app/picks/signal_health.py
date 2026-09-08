"""信号健康度监控（方向 1「动态适应」× 方向 5「复盘迭代」的反馈环）。

把 DailyPickReview（每日精选复盘命中记录：verdict/excess_pct）聚合为
组合日序列，输出滚动胜率、期望超额与 **CUSUM 均值下漂检测**——
策略趋于失效时显式预警，而不是靠人翻复盘发现。

三态纪律：
- 样本不足（组合日 < min_groups）→ status="insufficient"，**不判 ok**；
- 基准缺失（excess_pct=None）的观测跳过统计，绝不填 0（"没参与"≠"超额为0"）；
- 预警接线：maybe_alert_signal_health（warning/drift → alert_event +
  NotifierRegistry，当日同状态去重）；复盘报告消费方 build_strategy_health_dimension
  自动生成改进项（drift=P0 / warning=P1）。

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

from app.core.db import get_session_factory

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
        "excess_pct 已 nullable 化（迁移 f6b2c8e4a9d3）：基准缺失观测以 None 跳过统计；"
        "nullable 化之前的历史行若存在被固化的 0.0，note 带[基准缺失]标记可甄别"
    )
    return out


# ---------------------------------------------------------------- 预警接线（P1）


SIGNAL_HEALTH_RULE_NAME = "__signal_health__"


def _default_health_channels() -> str:
    """健康度规则默认 channels（与 watcher 同一配置源；feishu 未配置时通道层显式跳过）。"""
    import json

    from app.core.config import settings

    return json.dumps(
        [c.strip() for c in settings.picks_watcher_channels.split(",") if c.strip()]
    )


def ensure_signal_health_rule(session_factory):
    """信号健康度专用系统规则（get-or-create）。

    与 watcher 规则**分立**：通知中心按规则名分流，策略级预警不该混进
    盘中跟踪的个股事件流（kind 语义、频次、生命周期都不同）。
    """
    from app.models.alert import AlertRule

    with session_factory() as db:
        row = (
            db.query(AlertRule)
            .filter(AlertRule.name == SIGNAL_HEALTH_RULE_NAME)
            .one_or_none()
        )
        if row is None:
            row = AlertRule(
                name=SIGNAL_HEALTH_RULE_NAME,
                enabled=1,
                condition_type="signal_health",
                scope="all",
                threshold=0.0,
                channels=_default_health_channels(),
            )
            db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def _health_alert_text(health: dict) -> str:
    w = health.get("window") or {}
    c = health.get("cusum") or {}
    parts = [f"滚动{w.get('groups', '?')}组合日胜率 {w.get('win_rate')}"]
    if w.get("mean_excess") is not None:
        parts.append(f"日均超额 {w['mean_excess']:+g}%")
    if health.get("status") == "drift" and c:
        parts.append(
            f"CUSUM 下漂 s_max={c.get('s_max')}>{c.get('threshold')}（历史基线 {c.get('mu0'):+g}%）"
        )
    parts.append("策略持续跑输历史基线——建议复核入选逻辑与相位适配，评估降低出手档位")
    return "；".join(parts)


async def maybe_alert_signal_health(state, health: dict) -> dict:
    """warning/drift → 告警（alert_event + NotifierRegistry：in_app/feishu/log）。

    每自然日**同状态只发一次**（drift 自 warning 升级时 status 不同，允许再发）；
    ok/insufficient/error 静默跳过——insufficient 是样本不足不是告警。
    日常调用点：复盘 run() 尾部（盘后一日一次，频次天然受控）。

    :param state: app.state（alert_repo 缺失时现场构造，兼容测试裸 state）。
    """
    status = health.get("status")
    if status not in ("warning", "drift"):
        return {"dispatched": False, "reason": f"status={status} 无需告警"}

    from datetime import datetime

    from app.models.alert import AlertEvent
    from app.notifiers import get_notifier_registry
    from app.repositories.alert_repo import AlertRepository
    from sqlalchemy import select

    session_factory = get_session_factory()
    rule = ensure_signal_health_rule(session_factory)
    repo = getattr(state, "alert_repo", None) or AlertRepository(session_factory)

    # 当日去重：按 snapshot.status 比对（triggered_at 为 naive UTC，全库同口径）
    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        with session_factory() as db:
            rows = db.execute(
                select(AlertEvent)
                .where(AlertEvent.rule_id == rule.id)
                .order_by(AlertEvent.triggered_at.desc())
                .limit(10)
            ).scalars().all()
    except Exception:  # noqa: BLE001 — 去重查询失败不阻断告警本体
        rows = []
    for e in rows:
        if not e.triggered_at or e.triggered_at < day_start:
            continue
        # snapshot 列是 JSON 字符串（String(1024)），读回必须反序列化
        raw = e.snapshot
        if isinstance(raw, str):
            try:
                snap = json.loads(raw)
            except (TypeError, ValueError):
                snap = {}
        else:
            snap = raw if isinstance(raw, dict) else {}
        if (snap or {}).get("status") == status:
            return {"dispatched": False, "reason": "今日同状态已告警"}

    event = repo.record_trigger(
        rule.id,
        "000000",
        float((health.get("cusum") or {}).get("s_max") or 0.0),
        float((health.get("cusum") or {}).get("threshold") or 0.0),
        snapshot={
            "kind": "signal_health",
            "direction": "信号健康",
            "status": status,
            "text": _health_alert_text(health),
        },
    )
    channels = await get_notifier_registry().dispatch(event, rule)
    repo.update_event_channels(event.id, channels)
    log.warning("[SIGNAL-HEALTH] status=%s %s", status, _health_alert_text(health))
    return {"dispatched": True, "event_id": event.id, "channels": channels}
