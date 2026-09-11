"""跨日回放（组合稳定性验证；纯函数，不依赖行情）。

为什么需要它：换股门槛、炒作阶段切换、复盘迭代这三套机制**只有单日样本时无法验证**——
"组合稳不稳定"本质是多日性质。单看一天，任何门槛都显得有效。

能力边界（诚实标注）：完整六维管线中，只有**梯队**与**技术**两个维度可由历史数据重建
（涨停池支持历史日期、K 线本来就是历史的）。消息（EventStore 只留近期事件）、
情绪（需全市场宽度快照）、基本面/资金（快照是当前的）无法回放。
所以本模块验证的是**换股门槛与梯队阶段系数的多日行为**，不是完整选股质量。

验证方法：同一份每日候选评分，分别跑"有门槛"与"无门槛（threshold=0，即纯排序 top-N）"
两条轨迹，对比日均换手、平均持有天数、换股次数——差值即门槛的稳定效果。
"""

from __future__ import annotations

from app.picks.engine import MAX_PICKS, MAX_SWAPS_PER_DAY, REPLACE_THRESHOLD, apply_replacement_threshold


def holding_spans(daily: list[list[str]]) -> dict[str, list[int]]:
    """每个 symbol 的**连续**持有段长度（出现中断即分段）。

    :param daily: 按日期升序的每日组合 symbol 列表
    """
    spans: dict[str, list[int]] = {}
    current: dict[str, int] = {}
    for symbols in daily:
        seen = set(symbols)
        for s in list(current):
            if s not in seen:
                spans.setdefault(s, []).append(current.pop(s))
        for s in seen:
            current[s] = current.get(s, 0) + 1
    for s, n in current.items():
        spans.setdefault(s, []).append(n)
    return spans


def stability_stats(daily: list[list[str]], *, max_picks: int) -> dict:
    """组合稳定性指标。日数 <2 时换手类指标无意义，返回 None（不编造）。"""
    days = len(daily)
    if days == 0:
        return {"days": 0, "replacements_total": 0, "avg_turnover_pct": None,
                "avg_holding_days": None, "max_holding_days": None, "unique_symbols": 0}
    swaps = 0
    for prev, cur in zip(daily, daily[1:]):
        swaps += len(set(cur) - set(prev))
    spans = holding_spans(daily)
    flat = [n for v in spans.values() for n in v]
    return {
        "days": days,
        "replacements_total": swaps,
        "replacements_per_day": round(swaps / (days - 1), 2) if days > 1 else None,
        # 日均换手：每日换入只数 / 组合容量，衡量"组合成员被替换的比例"
        "avg_turnover_pct": round(swaps / (days - 1) / max_picks * 100, 1) if days > 1 else None,
        "avg_holding_days": round(sum(flat) / len(flat), 2) if flat else None,
        "max_holding_days": max(flat) if flat else None,
        "unique_symbols": len(spans),
    }


def replay_picks(
    daily_ranked: list[tuple[str, list[dict]]],
    *,
    threshold: float = REPLACE_THRESHOLD,
    max_picks: int = MAX_PICKS,
    max_swaps: int | None = MAX_SWAPS_PER_DAY,
    min_score: float = 0.0,
) -> dict:
    """逐日回放：应用换股门槛与每日换股上限，输出轨迹 + 稳定性指标 + 对照组。

    :param daily_ranked: 按日期升序的 [(date, 候选按综合分降序)]，候选需含 symbol/score
    :param max_swaps: 每日最多换入几只（None = 不限）
    :param min_score: 入选门槛。**默认 0.0（不在回放里启用），这不是笔误**——
        回放的分数是历史重建分，只有梯队+技术两维可回放（见模块 docstring），
        其绝对量级与线上六维综合分**不可比**；把线上标定的绝对门槛（MIN_PICK_SCORE）
        套到二维重建分上属于量纲错误，会把回放结果整体压成空组合。
        所以这里默认关闭、只由调用方显式开启；线上路径（picks.py 路由）用引擎默认值。
    :return: {daily, stats, baseline, threshold_effect, ...}
    """
    daily: list[list[str]] = []
    per_day: list[dict] = []
    prev: list[str] = []
    for date_str, ranked in daily_ranked:
        kept, replaced = apply_replacement_threshold(
            prev, ranked, threshold, max_picks, max_swaps, min_score=min_score
        )
        symbols = [k["symbol"] for k in kept]
        daily.append(symbols)
        scores = [k.get("score") for k in kept if isinstance(k.get("score"), (int, float))]
        per_day.append(
            {
                "date": date_str,
                "symbols": symbols,
                "score_avg": round(sum(scores) / len(scores), 2) if scores else None,
                "replaced": replaced,
            }
        )
        prev = symbols

    # 对照组：threshold=0 且无换股上限（等价于每日纯排序取 top-N）
    baseline_daily: list[list[str]] = []
    for _, ranked in daily_ranked:
        baseline_daily.append([c["symbol"] for c in ranked[:max_picks]])

    stats = stability_stats(daily, max_picks=max_picks)
    base = stability_stats(baseline_daily, max_picks=max_picks)
    saved = None
    if stats["replacements_total"] is not None and base["replacements_total"] is not None:
        saved = base["replacements_total"] - stats["replacements_total"]
    return {
        "daily": per_day,
        "stats": stats,
        "baseline": base,
        "threshold": threshold,
        "max_picks": max_picks,
        "max_swaps": max_swaps,
        "threshold_effect": {
            "swaps_avoided": saved,
            "swaps_avoided_pct": (
                round(saved / base["replacements_total"] * 100, 1)
                if saved is not None and base["replacements_total"]
                else None
            ),
        },
    }
