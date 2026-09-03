"""每日精选组合的盘后逐股复盘（从 routes/picks.py generate_review 抽出，2026-09-04）。

抽出动机：复盘必须在**调度链里自动触发**（用户需求：复盘时必须统计今日推荐
个股的准确率），而原来这段逻辑内联在 FastAPI handler 里、依赖 request——
调度器拿不到 Request。抽出为纯 app 级函数后：
- `POST /api/picks/review/generate`（手动）与
- `ReviewService.run()`（15:30 全局复盘前置步）
走**同一条代码路径**，不存在"手动能跑、自动跑的是另一套"的分叉。

口径纪律（原样保留）：
- 组合 T-1 生成、T 日持有 → 复盘表现日 = 今天；
- 大盘基准缺失时 excess_pct=None（评审 B21），绝不拿个股涨幅冒充超额；
- classify_failure 六类归因 + review_entry_quality 买点质量（选错 vs 追高分开）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select

from app.picks.engine import classify_failure, review_entry_quality

log = logging.getLogger(__name__)


async def batch_quotes(hub, symbols: list[str]) -> dict[str, Any]:
    """腾讯批量快照（与个股行情同源），50 只/批。返回 symbol→Quote。

    原 routes/picks.py._batch_quotes 原样搬入（generate/generate_review 两处
    消费，从 routes 导入 app.picks 属正常依赖方向，反过来才是坏味道）。
    """
    composite = hub.provider if hasattr(hub.provider, "providers") else None
    target = next(
        (p for p in (composite.providers if composite else [hub.provider]) if p.name == "tencent"),
        hub.provider,
    )
    out: dict[str, Any] = {}
    for i in range(0, len(symbols), 50):
        try:
            for q in await target.get_quotes(symbols[i : i + 50]):
                out[q.symbol] = q
        except Exception as exc:
            log.warning("picks quotes batch %s failed: %s", i // 50, exc)
    return out


async def generate_daily_review(hub, snapshot_service, session_factory) -> dict:
    """对最近一份组合生成/刷新逐股复盘（表现日 = 今天；组合 T-1 生成、T 日持有）。

    :raises ValueError: 尚无组合 / 组合为空（调用方决定映射为 404 还是记 log 跳过）。
    :return: {"date", "reviews": [...], "market_pct"}
    """
    with session_factory() as db:
        from app.models.daily_pick import DailyPickSet

        row = db.execute(
            select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(1)
        ).scalar_one_or_none()
    if row is None:
        raise ValueError("尚无组合可复盘")
    try:
        items = json.loads(row.items)
    except Exception:
        items = []
    if not items:
        raise ValueError("组合为空")

    # 大盘基准：上证当日涨跌幅
    market_pct = None
    try:
        for q in hub.get_indices():
            if q.symbol == "000001":
                market_pct = q.change_pct
    except Exception:
        pass

    # 持有期市场相位（情绪误判判定需要）
    review_phase = None
    try:
        from app.services.market_context import compute_market_sentiment

        review_phase = (
            await compute_market_sentiment(hub, snapshot_service) or {}
        ).get("phase")
    except Exception as exc:
        log.warning("picks review: sentiment failed: %s", exc)

    reviews = []
    symbols = [i["symbol"] for i in items]
    quotes = await batch_quotes(hub, symbols)
    for it in items:
        sym = it["symbol"]
        q = quotes.get(sym)
        change = q.change_pct if q is not None else None
        # 基准缺失时 excess 必须为 None（评审 B21）：此前回退成个股涨幅本身，
        # 会把"大盘 +2% 时个股 +2%"记成超额 0、把"大盘 -2% 时个股 0%"记成 +2——
        # classify_failure 的归因统计被系统性污染。None 让下游显式处理"基准缺失"。
        excess = (
            round(change - market_pct, 2)
            if (change is not None and market_pct is not None)
            else None
        )
        # 买点质量：把「选错了」与「选对了但买点不对」分开，否则迭代方向会被污染
        entry = review_entry_quality(
            buy_range=it.get("buy_range"),
            day_open=q.open if q is not None else None,
            day_high=q.high if q is not None else None,
            day_low=q.low if q is not None else None,
            day_close=q.price if q is not None else None,
            observation_only=bool(it.get("observation_only")),
        )
        category, note = classify_failure(
            excess_pct=excess, entry=entry, market_phase=review_phase
        )
        # 买点质量并入 note：不额外加列（克制新增），但复盘必须能看到这段证据。
        # 闸门日也会带上——"本就不建议出手"本身就是需要留档的结论。
        note = f"{note}；{entry['basis']}"
        verdict = {"missed": "flat", "entry_bad": "bad", "sentiment_misread": "bad",
                   "logic_failed": "bad", "gone_well": "good"}.get(category, "flat")
        reviews.append(
            {
                "date": row.date, "symbol": sym, "name": it.get("name"),
                "verdict": verdict,
                "reason_category": category,
                "excess_pct": excess, "note": note,
                "entry": entry,
                "market_phase": review_phase,
            }
        )

    with session_factory() as db:
        from app.models.daily_pick import DailyPickReview

        # ⚠️ 不能用 db.merge：新对象主键为 None，merge 会走 INSERT 而非 UPDATE，
        # 重复复盘即撞 (date, symbol) 唯一约束。按业务键查询后更新才是正解。
        for r in reviews:
            row_r = db.execute(
                select(DailyPickReview).where(
                    DailyPickReview.date == r["date"],
                    DailyPickReview.symbol == r["symbol"],
                )
            ).scalar_one_or_none()
            if row_r is None:
                db.add(
                    DailyPickReview(
                        date=r["date"], symbol=r["symbol"], name=r.get("name"),
                        verdict=r["verdict"], reason_category=r["reason_category"],
                        excess_pct=r["excess_pct"], note=r["note"],
                    )
                )
            else:
                row_r.name = r.get("name")
                row_r.verdict = r["verdict"]
                row_r.reason_category = r["reason_category"]
                row_r.excess_pct = r["excess_pct"]
                row_r.note = r["note"]
        db.commit()
    return {"date": row.date, "reviews": reviews, "market_pct": market_pct}
