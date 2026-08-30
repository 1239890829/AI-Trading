"""做 T 信号回测运行器（docs/minute-chart-plan.md 模块 4.3）。

输入：backfill 落地的 Parquet（新浪 5 分钟，22 个交易日——数据深度边界见
minute_backfill.py 模块头注）。60 日 1 分钟需 miniQMT/掘金升级后重跑。

口径（与 docs/backtest-rules.md 对齐）：
- 逐日 as_of：每天独立跑引擎（信号只用当日 ≤t 数据）；
- 结算：复用 minute_decisions 的窗口三分类口径（correct ≥8bp=2×成本 /
  wrong 反向 / invalid 未达阈值 / expired 数据不足）；
- 样本内外：按交易日排序切分（默认 2/3 : 1/3），**禁止先看结果再调参**
  ——本运行器只产出证据，不改引擎阈值；
- 成本：正确判定阈值 8bp 已含佣金+印花税+滑点（方案口径）。

输出：JSON 报告落 data/review/backtest/，含命中率、三分类分布、
按方向拆分、指标触发分布、错误归因主因分布、样本内外对比。
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.market.minute_backfill import load_symbol
from app.market.minute_decisions import THRESHOLD_PCT
from app.market.minute_signals import compute_minute_signals

log = logging.getLogger(__name__)

report_dir_default = Path(__file__).resolve().parents[3] / "data" / "review" / "backtest"


def _split_days(days: list[str], in_ratio: float = 2 / 3) -> tuple[list[str], list[str]]:
    """按时间排序切样本内/外（默认 2/3 : 1/3）。"""
    ordered = sorted(days)
    cut = max(1, int(len(ordered) * in_ratio))
    return ordered[:cut], ordered[cut:]


def apply_adjustments(points: list[dict], events: list[dict]) -> tuple[list[dict], int]:
    """复权修正（前复权，纯函数）：把除权日**之前**的价格调整到除权日口径。

    factor = (C_prev - dividend) / (C_prev * (1 + bonus))，C_prev 取除权日前
    最后一根分钟点的价格（≈前日收盘，数据自洽、无需额外日 K 请求）。
    价格类字段（price/avg/cum_amount）同步缩放；量不变。
    事件可乱序传入；多个事件独立叠加（乘法交换）。空事件流原样返回。

    :param events: [{ex_date: date, dividend: float, bonus: float}]（ths 事件流口径）
    :return: (修正后的 points, 实际生效的事件数)
    """
    if not points or not events:
        return points, 0
    out = [dict(p) for p in points]
    applied = 0
    for ev in events:
        ex = ev.get("ex_date")
        dividend = float(ev.get("dividend") or 0.0)
        bonus = float(ev.get("bonus") or 0.0)
        if ex is None or (dividend <= 0 and bonus <= 0):
            continue
        ex_date = ex if isinstance(ex, date) else datetime.strptime(str(ex)[:10], "%Y-%m-%d").date()
        prev_pts = [p for p in out if (datetime.fromisoformat(p["ts"]) + timedelta(hours=8)).date() < ex_date]
        if not prev_pts:
            continue  # 除权日在窗口之前：窗口内价格已是新口径
        c_prev = prev_pts[-1]["price"]
        if not c_prev or c_prev <= 0:
            continue
        factor = (c_prev - dividend) / (c_prev * (1 + bonus))
        if abs(factor - 1.0) < 1e-9:
            continue
        for p in out:
            if (datetime.fromisoformat(p["ts"]) + timedelta(hours=8)).date() < ex_date:
                if p.get("price") is not None:
                    p["price"] = round(p["price"] * factor, 4)
                if p.get("avg") is not None:
                    p["avg"] = round(p["avg"] * factor, 4)
                if p.get("cum_amount") is not None:
                    p["cum_amount"] = round(p["cum_amount"] * factor, 2)
        applied += 1
    return out, applied


def _evaluate(signal: dict, points: list[dict]) -> dict:
    """窗口三分类结算（与 minute_decisions.settle_decision 同口径）。

    数据粒度自适应：1 分钟数据 30 分钟窗口 = 30 bar；5 分钟数据 = 6 bar——
    "数据不足"判定按粒度取 bar 数下限（30min / 粒度的一半），不硬编码。
    """
    trigger = datetime.fromisoformat(signal["ts"])
    end = trigger + timedelta(minutes=30)
    win = [p["price"] for p in points if trigger < datetime.fromisoformat(p["ts"]) <= end]
    step = (datetime.fromisoformat(points[1]["ts"]) - datetime.fromisoformat(points[0]["ts"])).total_seconds() / 60 if len(points) > 1 else 1.0
    min_bars = max(3, int(30 / max(step, 1) / 2))
    if len(win) < min_bars:
        return {"outcome": "expired", "optimal_spread_pct": None}
    best, worst = max(win), min(win)
    sp = signal["signal_price"]
    if signal["bias"] == "低吸偏向":
        optimal = (best - sp) / sp * 100
        adverse = (worst - sp) / sp * 100
    else:
        optimal = (sp - worst) / sp * 100
        adverse = (best - sp) / sp * 100
    optimal, adverse = round(optimal, 3), round(adverse, 3)
    if optimal >= THRESHOLD_PCT:
        outcome = "correct"
    elif adverse <= -THRESHOLD_PCT:
        outcome = "wrong"
    else:
        outcome = "invalid"
    return {"outcome": outcome, "optimal_spread_pct": optimal, "adverse_spread_pct": adverse}


def _aggregate(entries: list[dict]) -> dict:
    """一组已结算信号 → 统计摘要。"""
    n = len(entries)
    outcomes = Counter(e["outcome"] for e in entries)
    judged = outcomes["correct"] + outcomes["wrong"]
    corrects = [e["optimal_spread_pct"] for e in entries
                if e["outcome"] == "correct" and e["optimal_spread_pct"] is not None]
    causes = Counter(
        (e.get("attribution") or {}).get("primary_cause")
        for e in entries if e["outcome"] == "wrong"
    )
    dirs = Counter(e["bias"] for e in entries)
    return {
        "signals": n,
        "correct": outcomes["correct"], "wrong": outcomes["wrong"],
        "invalid": outcomes["invalid"], "expired": outcomes["expired"],
        "hit_rate": round(outcomes["correct"] / judged, 3) if judged else None,
        "avg_optimal_spread_pct": round(sum(corrects) / len(corrects), 3) if corrects else None,
        "by_bias": dict(dirs),
        "wrong_primary_causes": dict(causes),
    }


def run_backtest(
    symbols: list[str],
    *,
    parquet_dir: Path | None = None,
    in_ratio: float = 2 / 3,
    adjustment_events: dict[str, list[dict]] | None = None,
) -> dict:
    """按日回测全部样本池，聚合样本内/外统计。

    adjustment_events: symbol → 复权事件流（由调用方异步拉取注入；None=不做复权修正）。
    """
    # 1. 全量信号（逐日 as_of 跑引擎）+ 窗口结算
    entries: list[dict] = []
    day_index: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    adjustments_applied: dict[str, int] = {}
    per_symbol_days: dict[str, dict[str, list[dict]]] = {}
    for sym in symbols:
        pts = load_symbol(parquet_dir, sym)
        if not pts:
            log.warning("no backfill data for %s, skipped", sym)
            continue
        if adjustment_events:
            pts, applied = apply_adjustments(pts, adjustment_events.get(sym) or [])
            if applied:
                adjustments_applied[sym] = applied
                log.info("adjustment applied for %s: %d events", sym, applied)
        by_day: dict[str, list[dict]] = defaultdict(list)
        for p in pts:
            by_day[(datetime.fromisoformat(p["ts"]) + timedelta(hours=8)).strftime("%Y%m%d")].append(p)
        per_symbol_days[sym] = dict(by_day)
        days = sorted(by_day)
        # 逐日 prev-day 统计量（昨量/近 5 日振幅）——首日缺失由引擎显式降级
        for di, d in enumerate(days):
            pts_day = by_day[d]
            yesterday_vol = None
            daily_vol_pct = None
            if di > 0:
                prev = by_day[days[di - 1]]
                yesterday_vol = sum(p["volume"] for p in prev if p.get("volume"))
            prev5 = [days[j] for j in range(max(0, di - 5), di)]
            if prev5:
                rngs = []
                for pd_ in prev5:
                    pb = by_day[pd_]
                    hi = max(p["price"] for p in pb)
                    lo = min(p["price"] for p in pb)
                    if pb[-1]["price"]:
                        rngs.append((hi - lo) / pb[-1]["price"] * 100)
                if rngs:
                    daily_vol_pct = round(sum(rngs) / len(rngs), 3)
            out = compute_minute_signals(pts_day, yesterday_vol=yesterday_vol, daily_vol_pct=daily_vol_pct)
            for s in out["signals"]:
                ev = _evaluate(s, pts_day)
                wrong = ev["outcome"] == "wrong"
                att = None
                if wrong:
                    triggered = s["triggered"]
                    att = _loa_att(triggered, s["score"])
                entries.append({
                    "symbol": sym, "trade_date": d, "ts": s["ts"], "bias": s["bias"],
                    "score": s["score"], "signal_price": s["signal_price"],
                    "trigger_keys": [h["key"] for h in s["triggered"]],
                    "attribution": att, **ev,
                })
        # 保留 day_index 供切分使用
        for d in days:
            day_index[d][sym] = by_day[d]

    all_days = sorted(day_index)
    in_days, out_days = _split_days(all_days, in_ratio)
    in_set, out_set = set(in_days), set(out_days)

    in_entries = [e for e in entries if e["trade_date"] in in_set]
    out_entries = [e for e in entries if e["trade_date"] in out_set]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "data_days": len(all_days), "from": all_days[0] if all_days else None,
        "to": all_days[-1] if all_days else None,
        "split": {"in_days": len(in_days), "out_days": len(out_days)},
        "granularity": "5min (sina, 1023-bar 上限)",
        "threshold_pct": THRESHOLD_PCT,
        "adjustments_applied": adjustments_applied,
        "sample_in": _aggregate(in_entries),
        "sample_out": _aggregate(out_entries),
        "overall": _aggregate(entries),
        "note": "阈值未经校准——本报告只产出证据；校准需在样本内完成后再看样本外。价格已按复权事件流前复权（窗口内除权日修正）",
    }
    return report


def _loa_att(triggered: list[dict], score: float) -> dict:
    """leave-one-out 归因（与 minute_decisions._attribute 同口径的轻量版）。"""
    total_w = 0.90
    primary, worst_wo = None, None
    for h in triggered:
        score_wo = (score * total_w - h["weight"] * h["direction"]) / total_w
        if worst_wo is None or abs(score_wo) < abs(worst_wo):
            worst_wo, primary = score_wo, h
    return {
        "primary_cause": primary["key"] if primary else None,
        "score_without": round(worst_wo, 3) if worst_wo is not None else None,
    }


def save_report(report: dict, out_dir: Path | None = None) -> Path:
    out = out_dir or report_dir_default
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    path = out / f"minute-backtest-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log.info("backtest report saved: %s", path)
    return path


def main() -> None:
    """CLI 入口：python -m app.market.minute_backtest [symbol ...]（回拉+复权+回测一步到位）"""
    import asyncio
    import sys

    from app.market.minute_backfill import backfill

    symbols = sys.argv[1:] or ["600519", "000001", "300750", "601318"]
    pulled = asyncio.run(backfill(symbols))
    failures = pulled.pop("_failures", {})
    log.info("backfill done: %s, failures: %s", pulled, failures)

    ok_symbols = [s for s in symbols if s in pulled]

    async def _fetch_events() -> dict[str, list[dict]]:
        """复权事件流（ths 官方，全历史事件量级极小）；失败不阻断回测（报告注明）。"""
        try:
            from app.core.config import settings
            from app.data_providers.ths import ThsFuyaoProvider

            prov = ThsFuyaoProvider(settings.ths_api_key, settings.ths_base_url)
            try:
                out: dict[str, list[dict]] = {}
                for s in ok_symbols:
                    try:
                        evs = await prov.get_adjustment_events(s)
                        if evs:
                            out[s] = evs
                    except Exception as exc:
                        log.warning("adjustment events failed for %s: %s", s, exc)
                return out
            finally:
                await prov.aclose()
        except Exception as exc:
            log.warning("adjustment events unavailable: %s", exc)
            return {}

    events_by_sym = asyncio.run(_fetch_events())
    report = run_backtest(ok_symbols, adjustment_events=events_by_sym or None)
    report["backfill_failures"] = failures
    path = save_report(report)
    print(json.dumps({
        "report": str(path),
        "days": f"{report['from']}→{report['to']} ({report['data_days']}d)",
        "pulled": pulled,
        "adjustments": report["adjustments_applied"],
        "overall": report["overall"],
        "sample_in": report["sample_in"],
        "sample_out": report["sample_out"],
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
