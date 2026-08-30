"""预判证据采集（docs/theme-prediction.md §二 数据清单）。

交叉验证的数据面与用途：
- ths 热股榜         → 人气先导信号（最强可量化指标，我爱我家案例）
- 个股新闻（东财）   → 题材关键词联动 + 政策级别判定（消息→个股唯一数据驱动通路）
- 近 5 日涨停池      → 题材新鲜度判定（旧题材占用 vs 全新方向）+ 个股概念标签
- 龙虎榜             → 游资资金验证（net_buy）
- 情绪引擎           → 市场环境适配（冰点/分歧出龙头，高潮难接力）
- 题材看板           → 当前已激活题材清单（新鲜度交叉）

任一来源失败记 gap 继续跑（红线：缺失显式标注，不臆测）。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from app.market.trade_calendar import last_trade_date, recent_trade_dates, trading_days
from app.services.theme_service import parse_theme_tags

log = logging.getLogger(__name__)

_BJ_DELTA = timedelta(hours=8)


def cst_now() -> datetime:
    """当前北京时间（内部数据统一 UTC 存储，展示层转 CST）。"""
    return datetime.now(timezone.utc) + _BJ_DELTA


def guess_context(now_cst: datetime) -> str:
    """运行时点语境：weekend / holiday / pre_market / intraday / evening。"""
    wd = now_cst.weekday()  # 0=Mon
    t = now_cst.hour * 100 + now_cst.minute
    if wd >= 5:
        return "weekend"
    if t < 915:
        return "pre_market"
    if 915 <= t <= 1505:
        return "intraday"
    return "evening"


def next_weekday_after(d: date) -> date:
    """目标交易日：严格晚于 d 的下一个周一至周五（节假日不校准，报告内声明）。"""
    from datetime import timedelta

    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


async def collect_predict_evidence(
    hub,
    snapshot_service,
    keywords: list[str],
    hot_top: int = 10,
    news_limit: int = 8,
) -> dict:
    """采集预判所需全部数据面。返回 {数据: 值, gaps: [缺失标注]}。"""
    gaps: list[str] = []
    now_cst = cst_now()
    today = now_cst.date()

    provider = hub.provider

    # ---- 交易日历 ----
    days: list[date] = []
    try:
        days = await trading_days(provider)
    except Exception as exc:
        gaps.append(f"trade_calendar: {exc}")

    last_td = last_trade_date(days, today) if days else None
    if last_td is None:
        # 日历不可用时按自然周回退：向前走到上一个工作日
        last_td = today - timedelta(days=1)
        while last_td.weekday() >= 5:
            last_td -= timedelta(days=1)
        gaps.append("trade_calendar: 官方日历不可用，上一交易日按自然周推算")

    # ---- 热股榜（人气先导信号）----
    hot: list[dict] = []
    try:
        hot = await provider.get_hot_stock_list("day")
    except Exception as exc:
        gaps.append(f"ths_hot_list: {exc}")

    # ---- 近 5 个交易日涨停池：个股标签 + 题材活跃度 ----
    tags_by_symbol: dict[str, set[str]] = {}
    theme_tag_freq: dict[str, int] = {}
    if last_td:
        recent = recent_trade_dates(days, last_td, 5) if days else [last_td]
        for d in recent:
            try:
                pool = await provider.get_limit_up_pool(d)
            except Exception as exc:
                gaps.append(f"ths_limit_up({d}): {exc}")
                continue
            for rec in pool:
                tags = parse_theme_tags(rec.reason or "")
                if tags:
                    tags_by_symbol.setdefault(rec.symbol, set()).update(tags)
                for t in tags:
                    theme_tag_freq[t] = theme_tag_freq.get(t, 0) + 1

    # ---- 当前题材看板（已激活题材，用于新鲜度）----
    active_themes: list[dict] = []
    try:
        from app.services.theme_service import build_theme_board

        board = await build_theme_board(provider, last_td)
        active_themes = [
            {"theme": c["theme"], "score": c.get("strength_score"), "tier": c.get("strength_tier")}
            for c in (board.get("themes") or [])
        ]
    except Exception as exc:
        gaps.append(f"theme_board: {exc}")

    # ---- 候选股明细：热榜 top N + 新闻联动 + 龙虎榜资金 ----
    candidates: list[dict] = []
    for it in hot[:hot_top]:
        sym, name = it["symbol"], it.get("name") or ""
        c = {
            "symbol": sym,
            "name": name,
            "hot_rank": it["rank"],
            "heat": it.get("heat"),
            "news_matched": [],
            "news_sample": [],
            "tags": sorted(tags_by_symbol.get(sym, set())),
            "dragon_net_buy": None,
        }
        try:
            news = await provider.get_news(sym, news_limit)
            for n in news or []:
                title = str(n.get("title") or n.get("summary") or "")
                if not title:
                    continue
                c["news_sample"].append(title[:60])
                if any(k in title for k in keywords):
                    c["news_matched"].append(title[:80])
        except Exception as exc:
            gaps.append(f"news({sym}): {exc}")
        candidates.append(c)

    if last_td:
        try:
            dragon = await provider.get_longhu_records(last_td)
            dragon_map = {r.symbol: r for r in dragon}
            for c in candidates:
                r = dragon_map.get(c["symbol"])
                if r:
                    c["dragon_net_buy"] = r.net_buy
        except Exception as exc:
            gaps.append(f"ths_longhu({last_td}): {exc}")

    # ---- 市场环境 ----
    env: dict = {}
    try:
        from app.services.market_context import compute_market_sentiment

        env = await compute_market_sentiment(hub, snapshot_service)
    except Exception as exc:
        gaps.append(f"market_context: {exc}")

    return {
        "now_cst": now_cst.isoformat(),
        "context": guess_context(now_cst),
        "last_trade_date": last_td.strftime("%Y%m%d") if last_td else None,
        "target_date": next_weekday_after(today).strftime("%Y%m%d"),
        "keywords": keywords,
        "hot_list": hot,
        "candidates": candidates,
        "tags_by_symbol_size": len(tags_by_symbol),
        "theme_tag_freq": theme_tag_freq,
        "active_themes": active_themes,
        "env": env,
        "gaps": gaps,
    }
