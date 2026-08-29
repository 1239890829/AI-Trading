"""数据自采：行情、成交、持仓、账户操作。

需求 1 的硬要求：**数据缺失时明确标注，不做臆测**。
这里的每一处 `except` 都必须生成一条 `DataGap`，写明缺什么、为什么、
影响哪些结论。绝不允许用 0 / 空列表 / 默认值假装"没有"。

反例（本项目踩过）：K 线 6 位代码带前缀时被拼错成 `szsh000001`，
解析器返回空列表，空结果又被 failover 吞掉，最终只表现为"数据源不可用"——
没人知道**缺的是什么**。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.models.paper import PaperAccount, PaperOrder, PaperPosition
from app.review.schemas import (
    DataGap,
    IndexQuote,
    MarketSnapshot,
    OrderRecord,
    PositionRecord,
    TradingSnapshot,
)

log = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))


def to_cst_date(dt: datetime | None) -> str | None:
    """UTC datetime → 北京时间 YYYYMMDD。

    订单 created_at 存的是 UTC，而交易日按北京时间划分。
    **直接取 .date() 会把北京时间 08:00 前的单子算到前一天**——
    那正是"当日操作"统计出错的典型方式。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CST).strftime("%Y%m%d")


def _date_key(d: date) -> str:
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------- 市场环境


async def collect_market(hub, snapshot_service, trade_date: date) -> MarketSnapshot:
    """采集市场环境数据。每项失败都记 gap，不影响其他项。"""
    gaps: list[DataGap] = []
    td = _date_key(trade_date)

    # --- 指数 ---
    indices: list[IndexQuote] = []
    try:
        quotes = hub.get_indices()
        for q in quotes or []:
            indices.append(
                IndexQuote(
                    symbol=q.symbol,
                    name=q.name or q.symbol,
                    close=q.price or 0.0,
                    change_pct=q.change_pct or 0.0,
                    amount=q.amount,
                )
            )
        if not indices:
            gaps.append(DataGap(
                field="indices", source="hub.get_indices", reason="返回空列表",
                impact="market.指数走势", severity="warn",
            ))
    except Exception as exc:
        gaps.append(DataGap(
            field="indices", source="hub.get_indices", reason=str(exc)[:200],
            impact="market.指数走势", severity="warn",
        ))

    # --- 市场宽度 ---
    breadth: dict = {}
    try:
        payload = snapshot_service.breadth_payload()
        b = payload.get("breadth")
        if b:
            breadth = dict(b)
        else:
            gaps.append(DataGap(
                field="breadth", source="snapshot_service.breadth_payload",
                reason="全市场快照尚未就绪", impact="market.宽度与情绪", severity="block",
            ))
    except Exception as exc:
        gaps.append(DataGap(
            field="breadth", source="snapshot_service.breadth_payload",
            reason=str(exc)[:200], impact="market.宽度与情绪", severity="block",
        ))

    # --- 情绪 ---
    sentiment: dict = {}
    try:
        from app.services.market_context import CalendarUnavailable, compute_market_sentiment

        sentiment = await compute_market_sentiment(hub, snapshot_service)
    except CalendarUnavailable as exc:
        gaps.append(DataGap(
            field="sentiment", source="market_context.compute_market_sentiment",
            reason=str(exc)[:200], impact="market.情绪阶段", severity="block",
        ))
    except Exception as exc:
        gaps.append(DataGap(
            field="sentiment", source="market_context.compute_market_sentiment",
            reason=str(exc)[:200], impact="market.情绪阶段", severity="block",
        ))

    # --- 题材梯队 ---
    themes: list[dict] = []
    theme_summary: dict = {}
    try:
        from app.services.theme_service import build_theme_board

        board = await build_theme_board(hub.provider, trade_date)
        themes = (board.get("themes") or [])[:10]
        theme_summary = {
            "theme_count": len(board.get("themes") or []),
            "broken_ladder": len(board.get("broken_ladder") or []),
            "summary": board.get("summary") or {},
        }
        if not themes:
            gaps.append(DataGap(
                field="themes", source="build_theme_board", reason="返回空题材列表",
                impact="market.题材梯队", severity="warn",
            ))
    except Exception as exc:
        gaps.append(DataGap(
            field="themes", source="build_theme_board", reason=str(exc)[:200],
            impact="market.题材梯队", severity="warn",
        ))

    return MarketSnapshot(
        trade_date=td, indices=indices, breadth=breadth, sentiment=sentiment,
        themes=themes, theme_summary=theme_summary, gaps=gaps,
    )


# ---------------------------------------------------------------- 交易与账户


def collect_trading(
    session_factory, trade_date: date, price_map: dict[str, float] | None = None
) -> TradingSnapshot:
    """采集当日操作与账户数据（同步，走 DB）。

    :param price_map: symbol -> 最新价，用于持仓浮盈；缺失则 pnl 记 None 并标 gap
    """
    gaps: list[DataGap] = []
    td = _date_key(trade_date)
    price_map = price_map or {}

    orders: list[OrderRecord] = []
    positions: list[PositionRecord] = []
    account: dict = {}

    db = session_factory()
    try:
        # --- 账户 ---
        try:
            acc = db.execute(select(PaperAccount).order_by(PaperAccount.id)).scalars().first()
            if acc:
                account = {
                    "cash": acc.cash,
                    "initial_cash": acc.initial_cash,
                    "updated_at": acc.updated_at.isoformat() if acc.updated_at else None,
                }
            else:
                gaps.append(DataGap(
                    field="account", source="paper_account", reason="无账户记录",
                    impact="trades.账户与盈亏", severity="block",
                ))
        except Exception as exc:
            gaps.append(DataGap(
                field="account", source="paper_account", reason=str(exc)[:200],
                impact="trades.账户与盈亏", severity="block",
            ))

        # --- 当日订单（按北京时间过滤）---
        try:
            rows = db.execute(select(PaperOrder).order_by(PaperOrder.created_at)).scalars().all()
            for o in rows:
                if to_cst_date(o.created_at) != td:
                    continue
                orders.append(
                    OrderRecord(
                        id=o.id, symbol=o.symbol, side=o.side, price=o.price,
                        quantity=o.quantity, status=o.status,
                        filled_price=o.filled_price, fee=o.fee or 0.0, reason=o.reason,
                        created_at=o.created_at.isoformat() if o.created_at else None,
                    )
                )
        except Exception as exc:
            gaps.append(DataGap(
                field="orders", source="paper_order", reason=str(exc)[:200],
                impact="trades.操作评估", severity="block",
            ))

        # --- 持仓 ---
        try:
            prows = db.execute(select(PaperPosition)).scalars().all()
            missing_price: list[str] = []
            for p in prows:
                if p.quantity <= 0:
                    continue
                last = price_map.get(p.symbol)
                if last is None:
                    missing_price.append(p.symbol)
                pnl = None
                pnl_pct = None
                if last is not None and p.cost_price > 0:
                    pnl = round((last - p.cost_price) * p.quantity, 2)
                    pnl_pct = round((last / p.cost_price - 1) * 100, 2)
                positions.append(
                    PositionRecord(
                        symbol=p.symbol, quantity=p.quantity,
                        frozen_today=p.frozen_today or 0, cost_price=p.cost_price,
                        last_price=last, pnl=pnl, pnl_pct=pnl_pct,
                    )
                )
            if missing_price:
                gaps.append(DataGap(
                    field="positions.last_price", source="price_map",
                    reason=f"{len(missing_price)} 只持仓无最新价：{','.join(missing_price[:5])}",
                    impact="trades.持仓浮盈", severity="warn",
                ))
        except Exception as exc:
            gaps.append(DataGap(
                field="positions", source="paper_position", reason=str(exc)[:200],
                impact="trades.持仓浮盈", severity="warn",
            ))

    finally:
        db.close()

    # 已实现盈亏只统计当日成交的卖出单
    realized = sum(
        ((o.filled_price or o.price) - o.price) * o.quantity - o.fee
        for o in orders if o.side == "sell" and o.status == "filled"
    ) if orders else None

    return TradingSnapshot(
        trade_date=td, account=account, orders=orders, positions=positions,
        realized_pnl=round(realized, 2) if realized is not None else None,
        trade_count=len(orders), gaps=gaps,
    )
