"""市场情绪引擎（full.md §5.5）。

规则化、可解释：每个阶段判定必须给出使用的指标与数值、置信度、可能的误判原因、阶段切换条件。
数据源：全市场快照（宽度）+ 今日/昨日涨停池（东财封单法口径）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median

PHASE_ORDER = ["冰点", "修复", "发酵", "高潮", "分歧", "退潮"]

# 温度权重（可配置；总和不必然为 1，结果归一化到 0-100）
_WEIGHTS = {"limit_up": 0.25, "height": 0.20, "prev_zt_perf": 0.30, "breadth": 0.15, "break_penalty": 0.10}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _limit_pct(symbol: str, name: str | None) -> float:
    if symbol.startswith(("300", "301", "688", "689")):
        return 20.0
    if symbol.startswith(("43", "83", "87", "92")):
        return 30.0
    if name and "ST" in name.upper():
        return 5.0
    return 10.0


def _prev_zt_performance(pool_yesterday, snapshot: list[dict]) -> dict:
    """昨日涨停股今日表现：平均/中位收益、翻红率、再涨停率。"""
    snap = {r["symbol"]: r for r in snapshot}
    perfs: list[float] = []
    red = re_limit = 0
    for rec in pool_yesterday:
        r = snap.get(rec.symbol)
        if not r or r.get("change_pct") is None:
            continue
        pct = r["change_pct"]
        perfs.append(pct)
        if pct > 0:
            red += 1
        if pct >= _limit_pct(rec.symbol, r.get("name")) - 0.15:
            re_limit += 1
    n = len(perfs)
    return {
        "sample": n,
        "avg_pct": round(sum(perfs) / n, 2) if n else None,
        "median_pct": round(median(perfs), 2) if n else None,
        "red_rate": round(red / n, 3) if n else None,
        "re_limit_rate": round(re_limit / n, 3) if n else None,
    }


def compute_sentiment(breadth: dict, pool_today, pool_yesterday, snapshot: list[dict]) -> dict:
    prev = _prev_zt_performance(pool_yesterday, snapshot)
    boards = [max(1, int(r.consecutive_boards or 1)) for r in pool_today]
    max_board = max(boards) if boards else 0
    ladder: dict[str, int] = {}
    for b in boards:
        key = "5板+" if b >= 5 else f"{b}板"
        ladder[key] = ladder.get(key, 0) + 1
    ladder = {k: ladder[k] for k in sorted(ladder, key=lambda x: int(x.replace("板+", "").replace("板", "")))}

    limit_up = breadth.get("limit_up") or 0
    # 炸板近似：价格法涨停数 - 封单法涨停数（收盘价贴板但尾盘未封住）
    breaks = max(0, limit_up - len(pool_today))
    break_rate = round(breaks / max(limit_up + breaks, 1), 3) if limit_up else None

    indicators = [
        {"name": "涨停家数", "value": limit_up, "note": "价格法（快照）"},
        {"name": "连板高度", "value": f"{max_board}板", "note": "封单法口径"},
        {"name": "连板家数", "value": sum(1 for b in boards if b >= 2), "note": "≥2板"},
        {"name": "首板家数", "value": sum(1 for b in boards if b == 1), "note": ""},
        {"name": "昨日涨停今日均值", "value": prev["avg_pct"], "note": f"样本{prev['sample']}只"},
        {"name": "昨日涨停中位数", "value": prev["median_pct"], "note": ""},
        {"name": "翻红率", "value": prev["red_rate"], "note": ""},
        {"name": "再涨停率", "value": prev["re_limit_rate"], "note": ""},
        {"name": "炸板率(近似)", "value": break_rate, "note": "价格法与封单法差值"},
        {"name": "上涨/下跌", "value": f"{breadth.get('up')}/{breadth.get('down')}", "note": ""},
    ]

    # ---- 温度 0-100（加权，可解释） ----
    s_limit = _clamp(limit_up / 100 * 100)
    s_height = _clamp(max_board / 6 * 100)
    s_perf = _clamp(((prev["avg_pct"] if prev["avg_pct"] is not None else -5) + 5) / 10 * 100)
    s_breadth = _clamp(
        (breadth.get("up") or 0) / max((breadth.get("up") or 0) + (breadth.get("down") or 1), 1) * 100
    )
    s_break = _clamp(100 - (break_rate or 0) * 200)
    temperature = round(
        _WEIGHTS["limit_up"] * s_limit
        + _WEIGHTS["height"] * s_height
        + _WEIGHTS["prev_zt_perf"] * s_perf
        + _WEIGHTS["breadth"] * s_breadth
        + _WEIGHTS["break_penalty"] * s_break,
        1,
    )

    # ---- 阶段判定（按序规则，命中即停；每条附依据） ----
    avg = prev["avg_pct"]
    reasons: list[str] = []
    caveats: list[str] = []
    if prev["sample"] < 10:
        caveats.append("昨日涨停样本不足10只，表现类指标置信度受限")
    if breadth.get("total", 0) == 0:
        caveats.append("全市场快照缺失，宽度类指标不可用")

    def has(cond: bool) -> bool:
        return bool(cond)

    if limit_up < 25 and max_board <= 2 and has(avg is not None and avg < -2):
        phase, reasons = "冰点", [f"涨停仅{limit_up}家(<25)", f"高度仅{max_board}板(≤2)", f"昨涨停均值{avg}%(<-2%)"]
    elif max_board >= 5 and has(avg is not None and avg > 3) and limit_up >= 60:
        phase, reasons = "高潮", [f"高度{max_board}板(≥5)", f"昨涨停均值{avg}%(>3%)", f"涨停{limit_up}家(≥60)"]
    elif has(avg is not None and avg < -3) and max_board >= 3:
        phase, reasons = "退潮", [f"昨涨停均值{avg}%(<-3%)", f"高度仍有{max_board}板", "断板亏钱效应显现"]
    elif has(avg is not None and avg < 0) and max_board >= 3 and limit_up >= 40:
        phase, reasons = "分歧", [f"昨涨停均值{avg}%(转负)", f"高度{max_board}板(≥3)", f"涨停{limit_up}家仍多(≥40)"]
    elif has(avg is not None and avg > 2) and limit_up >= 40:
        phase, reasons = "发酵", [f"昨涨停均值{avg}%(>2%)", f"涨停{limit_up}家(≥40)"]
    elif has(avg is not None and avg >= 0) and limit_up >= 25:
        phase, reasons = "修复", [f"昨涨停均值{avg}%(转正)", f"涨停回升至{limit_up}家(≥25)"]
    else:
        phase, reasons = "冰点", [f"涨停{limit_up}家", f"高度{max_board}板", f"昨涨停均值{avg}%", "各指标均处于低位"]

    confidence = "中"
    if not caveats and avg is not None:
        agree = (temperature >= 55 and avg > 0) or (temperature < 45 and avg <= 0)
        confidence = "高" if agree else "中"
    if caveats:
        confidence = "低"

    switch = {
        "冰点": "涨停回升≥40家且昨涨停均值转正 → 修复",
        "修复": "涨停≥40家且昨涨停均值>2% → 发酵；均值再转负 → 冰点",
        "发酵": "高度≥5板且昨涨停均值>3%且涨停≥60家 → 高潮；均值转负且高度≥3板 → 分歧",
        "高潮": "昨涨停均值回落<0% → 分歧；涨停家数腰斩且均值<-3% → 退潮",
        "分歧": "均值修复>2% → 发酵；均值<-3% → 退潮",
        "退潮": "涨停<25家且高度≤2板 → 冰点",
    }[phase]

    return {
        "phase": phase,
        "temperature": temperature,
        "confidence": confidence,
        "reasons": reasons,
        "misjudge_caveats": caveats
        or ["规则引擎基于阈值，题材极端结构（如单一超级题材）可能误判；建议结合龙虎榜与资金流交叉验证"],
        "switch_conditions": switch,
        "indicators": indicators,
        "ladder": ladder,
        "judged_at": datetime.now(timezone.utc).isoformat(),
    }
