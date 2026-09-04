"""竞价溢价比因子（system-review §4.2 P0 三项中最后补齐的一个）。

口径（社区实测，docs/system-review-2026-09-02.md §四）：**今日竞价开盘价 / 昨日涨停封板价**，
对昨日涨停股，昨收=封板价，故溢价 ≈ ths 竞价快照的 auction_pct（竞价涨跌幅 vs 昨收）。

- < +3%（即 <103%）多为「一日游」，接力风险区；
- ≥ +5% 视为抢筹强溢价；
- 中间为正常承接。

三态纪律：竞价数据缺失（data_status 非 ready/final / 字段 null）= unknown，
**绝不冒充 0 或剔除后假装 judged**——汇总时 unknown 单列，占比分母只算可判定样本。
"""

from __future__ import annotations

import logging
from datetime import date
from statistics import median

log = logging.getLogger(__name__)

#: 一日游风险阈值（溢价 < 3%，即竞价 < 昨收×103%）
PREMIUM_WEAK_PCT = 3.0
#: 抢筹强溢价阈值
PREMIUM_STRONG_PCT = 5.0

BAND_STRONG = "strong"
BAND_NORMAL = "normal"
BAND_WEAK = "weak"
BAND_UNKNOWN = "unknown"


def classify_premium(pct: float | None) -> str:
    """单股溢价分档。None → unknown（缺失≠平开）。"""
    if pct is None:
        return BAND_UNKNOWN
    if pct >= PREMIUM_STRONG_PCT:
        return BAND_STRONG
    if pct < PREMIUM_WEAK_PCT:
        return BAND_WEAK
    return BAND_NORMAL


def summarize_premiums(pcts: list[float | None]) -> dict:
    """昨日涨停股集合的竞价溢价汇总。

    median / 占比只对可判定样本计算；unknown 单列计数。
    全部缺失时 judged=0，median/占比为 None（调用方据此显式降级，勿当 0 用）。
    """
    judged = [p for p in pcts if p is not None]
    n = len(pcts)
    unknown = n - len(judged)
    if not judged:
        return {
            "total": n,
            "judged": 0,
            "unknown": unknown,
            "median_pct": None,
            "weak_share": None,
            "strong_share": None,
        }
    weak = sum(1 for p in judged if p < PREMIUM_WEAK_PCT)
    strong = sum(1 for p in judged if p >= PREMIUM_STRONG_PCT)
    return {
        "total": n,
        "judged": len(judged),
        "unknown": unknown,
        "median_pct": round(median(judged), 2),
        "weak_share": round(weak / len(judged), 4),
        "strong_share": round(strong / len(judged), 4),
    }


async def collect_premium(hub, asof: date) -> dict:
    """采集昨日涨停股今日竞价溢价分布（端点与复盘 collector 共用）。

    任何一步失败都折进 caveats 显式降级，绝不抛出——因子视角缺一面 ≠ 整体不可用。
    返回 {trade_date, pool_date, items, summary, caveats}。
    """
    from app.market.trade_calendar import prev_trade_date, trading_days

    caveats: list[str] = []
    days = await trading_days(hub.provider)
    pool_date = prev_trade_date(days, asof)
    if pool_date is None:
        return {"trade_date": asof.isoformat(), "pool_date": None, "items": [],
                "summary": None, "caveats": ["交易日历不可用，无法定位昨日"]}

    try:
        pool = await hub.provider.get_limit_up_pool(pool_date)
    except Exception as exc:  # noqa: BLE001
        caveats.append(f"昨日({pool_date})涨停池拉取失败：{exc}")
        pool = []
    if not pool:
        caveats.append(f"昨日({pool_date})涨停池为空，无溢价可算")

    items: list[dict] = []
    symbols = [r.symbol for r in pool]
    auction_rows: list[dict] = []
    # ths 竞价快照单次 ≤100 只，分批拉
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        try:
            auction_rows.extend(await hub.provider.get_auction_snapshot(chunk, stage="final"))
        except Exception as exc:  # noqa: BLE001
            log.warning("auction premium chunk %d failed: %s", i // 100 + 1, exc)
            caveats.append(f"竞价快照批次 {i // 100 + 1} 拉取失败：{exc}")
    auction_by_sym = {r["symbol"]: r for r in auction_rows}

    for rec in pool:
        row = auction_by_sym.get(rec.symbol)
        pct = (row or {}).get("auction_pct")
        items.append({
            "symbol": rec.symbol,
            "name": rec.name or (row or {}).get("name"),
            "boards": rec.consecutive_boards,
            "premium_pct": pct,
            "band": classify_premium(pct),
            "data_status": (row or {}).get("data_status"),
        })

    summary = summarize_premiums([it["premium_pct"] for it in items])
    return {
        "trade_date": asof.isoformat(),
        "pool_date": pool_date.isoformat(),
        "items": items,
        "summary": summary,
        "caveats": caveats,
    }
