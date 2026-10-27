"""数据自采：行情、成交、持仓、账户操作。

需求 1 的硬要求：**数据缺失时明确标注，不做臆测**。
这里的每一处 `except` 都必须生成一条 `DataGap`，写明缺什么、为什么、
影响哪些结论。绝不允许用 0 / 空列表 / 默认值假装"没有"。

反例（本项目踩过）：K 线 6 位代码带前缀时被拼错成 `szsh000001`，
解析器返回空列表，空结果又被 failover 吞掉，最终只表现为"数据源不可用"——
没人知道**缺的是什么**。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.models.paper import SCOPE_MAIN, PaperAccount, PaperOrder, PaperPosition
from app.review.schemas import (
    DataGap,
    IndexQuote,
    MarketSnapshot,
    OrderRecord,
    PickEntry,
    PickReviewEntry,
    PicksSnapshot,
    PositionRecord,
    ShadowSnapshot,
    TradingSnapshot,
)

log = logging.getLogger(__name__)

from app.core.bjtime import BJ_TZ  # S2-8 时区收敛

# 等待快照首轮就绪的上界：收盘后数据已不再变化，等一会儿换完整维度更划算，
# 但不能无限等——复盘是定时任务，卡死比缺数据更糟。
_SNAPSHOT_WAIT_TIMEOUT = 120.0
_SNAPSHOT_WAIT_INTERVAL = 5.0


async def _await_snapshot_breadth(snapshot_service) -> dict:
    """有界等待快照服务首轮刷新完成，返回 breadth（超时仍空则返回 {}）。

    只等、不自己触发刷新：refresh 由 snapshot_service.run() 独占调度，
    复盘侧重复触发会和它抢新浪配额（K 线三源全断的前科就是高频请求触发）。
    """
    waited = 0.0
    while waited < _SNAPSHOT_WAIT_TIMEOUT:
        await asyncio.sleep(_SNAPSHOT_WAIT_INTERVAL)
        waited += _SNAPSHOT_WAIT_INTERVAL
        b = snapshot_service.breadth_payload().get("breadth")
        if b:
            log.info("review collector: snapshot ready after %.0fs wait", waited)
            return b
    log.warning("review collector: snapshot still empty after %.0fs", waited)
    return {}


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
    return dt.astimezone(BJ_TZ).strftime("%Y%m%d")


def _date_key(d: date) -> str:
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------- 市场环境


async def collect_market(
    hub, snapshot_service, trade_date: date, *, sentinel=None, theme_catalog=None
) -> MarketSnapshot:
    """Collect only source-date-bound facts; retain rejected values as audit records."""
    from types import SimpleNamespace

    from app.core.bjtime import beijing_now
    from app.market.breadth import compute_breadth
    from app.review.provenance import collect_sector_boards, date_key, evidence, finite, quote_evidence, source_time

    now = beijing_now()
    gaps: list[DataGap] = []
    provenance: dict = {}
    reported: dict = {}
    td = _date_key(trade_date)

    def gap(field: str, reason: str, *, source: str, severity: str = "warn") -> None:
        gaps.append(DataGap(field=field, source=source, reason=reason[:400],
                            impact=f"market.{field}", severity=severity))

    indices: list[IndexQuote] = []
    rejected_indices: list[dict] = []
    try:
        for q in hub.get_indices() or []:
            proof = quote_evidence(q, trade_date, now, allow_closed_observation=True)
            close, pct = finite(getattr(q, "price", None)), finite(getattr(q, "change_pct", None))
            if proof.state not in {"verified", "partial"} or close is None or close <= 0 or pct is None:
                rejected_indices.append({"symbol": getattr(q, "symbol", None),
                    "price": close, "change_pct": pct, "provenance": proof.model_dump()})
                continue
            indices.append(IndexQuote(symbol=q.symbol, name=q.name or q.symbol,
                close=close, change_pct=pct, amount=finite(getattr(q, "amount", None)),
                source=proof.source, data_timestamp=proof.source_as_of,
                received_at=proof.received_at, quality=getattr(q.quality, "value", q.quality),
                binding_state=proof.state))
        stamps = [q.data_timestamp for q in indices if q.data_timestamp]
        provenance["indices"] = evidence(trade_date,
            state=("partial" if rejected_indices or any(q.binding_state == "partial" for q in indices) else "verified") if indices else "unavailable",
            source=",".join(sorted({q.source for q in indices if q.source})) or "hub.get_indices",
            as_of=max(stamps) if stamps else None, received=now,
            source_day=trade_date.isoformat() if indices else None,
            reason=f"{len(indices)}条同日可信报价；{len(rejected_indices)}条缺项/异日未计入；各行保留原源钟")
        if rejected_indices:
            reported["indices"] = rejected_indices
        if not indices or rejected_indices:
            gap("indices", provenance["indices"].reason, source="hub.get_indices")
    except Exception as exc:
        provenance["indices"] = evidence(trade_date, source="hub.get_indices", reason=str(exc)[:200])
        gap("indices", str(exc), source="hub.get_indices")

    breadth: dict = {}
    frozen_rows: list[dict] = []
    receipt = None
    try:
        payload = snapshot_service.breadth_payload()
        if isinstance(payload.get("breadth"), dict):
            reported["breadth"] = dict(payload["breadth"])
        read = getattr(snapshot_service, "versioned_snapshot", None)
        rows, receipt = read() if callable(read) else ([], None)
        receipts = source_time(receipt)
        proofs = [quote_evidence(r, trade_date, now) for r in rows]
        # Never derive a source date from the local receipt, parquet directory or HH:MM tick.
        frozen_rows = [dict(r) for r, proof in zip(rows, proofs)
                       if proof.state in {"verified", "partial"} and finite(r.get("price")) is not None
                       and finite(r.get("change_pct")) is not None]
        if frozen_rows:
            breadth = compute_breadth(frozen_rows)
            provenance["breadth"] = evidence(trade_date,
                state="verified" if len(frozen_rows) == len(rows) and all(p.state == "verified" for p in proofs) else "partial",
                source=",".join(sorted({str(r.get("source")) for r in frozen_rows})),
                as_of=max(p.source_as_of for p in proofs if p.source_as_of and p.state in {"verified", "partial"}),
                received=receipts, source_day=trade_date.isoformat(),
                reason=f"同一冻结版本内{len(frozen_rows)}/{len(rows)}条源日期可核；仅该覆盖范围的涨跌与成交额，不冒充全集")
        else:
            provenance["breadth"] = evidence(trade_date, state="unverified" if reported.get("breadth") else "unavailable",
                source=payload.get("snapshot_source") or "snapshot_service", received=receipts,
                reason="缺完整来源日期，或源日期不属于报告日；原采集数值仅保留为未核记录")
            gap("breadth", provenance["breadth"].reason, source="snapshot_service")
    except Exception as exc:
        provenance["breadth"] = evidence(trade_date, source="snapshot_service", reason=str(exc)[:200])
        gap("breadth", str(exc), source="snapshot_service")

    if not breadth:
        from app.market import trade_calendar
        from app.review.daily_market import DEFAULT_DB_PATH, collect_daily_breadth

        # Read an existing completed source version. Never initiate a sync or use
        # the parquet filename / local receipt to invent the missing source day.
        if DEFAULT_DB_PATH.is_file():
            try:
                days = await trade_calendar.trading_days(hub.provider)
                daily, proof, audit = await asyncio.to_thread(
                    collect_daily_breadth, trade_date, trade_days=days, now=now)
                reported["daily_breadth_audit"] = audit
                if daily:
                    breadth = daily
                    provenance["breadth"] = proof
                    gaps = [g for g in gaps if g.field != "breadth"]
                    if (daily.get("coverage") or {}).get("missing_count"):
                        gap("breadth", proof.reason, source=proof.source or "marketdb")
            except Exception as exc:
                reported["daily_breadth_audit"] = {"unavailable_reason": str(exc)[:200]}

    sentiment: dict = {}
    try:
        if trade_date != now.date() or provenance["breadth"].state != "verified":
            raise ValueError("未具备当日完整可信宽度，不调用当前情绪计算冒充报告日结论")
        from app.services.market_context import compute_market_sentiment
        frozen = SimpleNamespace(breadth=breadth, snapshot=frozen_rows, last_success=receipt,
                                 poll_interval=getattr(snapshot_service, "poll_interval", 60.0))
        value = await compute_market_sentiment(hub, frozen)
        if date_key(value.get("trade_date")) != trade_date.isoformat():
            reported["sentiment"] = {k: value.get(k) for k in ("trade_date", "phase", "temperature", "caveats")}
            raise ValueError("情绪引擎实际日期与报告日不符，未进入当日结论")
        sentiment = value
        provenance["sentiment"] = evidence(trade_date, state="partial" if value.get("caveats") else "verified",
            source="market_context.compute_market_sentiment", as_of=provenance["breadth"].source_as_of,
            received=now, source_day=trade_date.isoformat(), reason="复用同一冻结宽度与引擎显式交易日；保留全部caveats")
    except Exception as exc:
        provenance["sentiment"] = evidence(trade_date, source="market_context.compute_market_sentiment", reason=str(exc)[:200])
        gap("sentiment", str(exc), source="market_context.compute_market_sentiment")

    themes: list[dict] = []
    theme_summary: dict = {}
    try:
        if trade_date != now.date():
            raise ValueError("题材构建含当前板块指标；历史复盘不调用当前混合数据构建")
        from app.services.theme_service import build_theme_board
        board = await build_theme_board(hub.provider, trade_date)
        # New observations freeze the same exact source identity as market/picks.
        # This is current catalog evidence, never proof of the report day's prices.
        from copy import deepcopy
        from app.services.official_match import attach_official_async

        observed_themes = deepcopy([t for t in board.get("themes", []) if isinstance(t, dict)][:10])
        await attach_official_async(SimpleNamespace(theme_catalog=theme_catalog), observed_themes, view_budget=None)
        reported["themes"] = observed_themes
        reported["theme_summary"] = {
            "theme_count": len(board.get("themes") or []),
            "broken_ladder": len(board.get("broken_ladder") or []),
            "summary": board.get("summary") or {},
        }
        # build_theme_board echoes the requested date but also reads _board_index()
        # without a date argument. Its mixed output cannot certify a source day.
        provenance["themes"] = evidence(trade_date, state="unverified",
            source="theme_service.build_theme_board", received=now,
            reason="原题材构建含当前板块/成员混合指标；顶层日期是请求回显，仅保留采集参考，未计入同日事实")
        gap("themes", provenance["themes"].reason, source="theme_service.build_theme_board")
    except Exception as exc:
        provenance["themes"] = evidence(trade_date, source="theme_service.build_theme_board", reason=str(exc)[:200])
        gap("themes", str(exc), source="theme_service.build_theme_board")

    sector_boards: dict[str, list] = {"industry": [], "concept": []}
    sector_board_summary: dict = {}
    try:
        boards, board_proof = collect_sector_boards(trade_date)
        provenance["sector_boards"] = board_proof
        # Preserve old owner values, but do not promote their absent source clocks.
        reported["sector_boards"] = boards
        if board_proof.state in {"verified", "partial"}:
            sector_boards = boards
    except Exception as exc:
        provenance["sector_boards"] = evidence(trade_date, source="board_flow.daily", reason=str(exc)[:200])
    if provenance["sector_boards"].state not in {"verified", "partial"}:
        from app.market.board_review import collect_review_boards

        try:
            verified = await collect_review_boards(hub.provider, trade_date, catalog_service=theme_catalog,
                                                   retry_missing=True)
        except Exception as exc:
            verified = {"state": "unavailable", "reason": f"板块日期证据读取失败：{type(exc).__name__}"}
        sector_board_summary = verified.get("summary") or {}
        reported["sector_board_audit"] = {key: value for key, value in verified.items() if key != "boards"}
        if verified.get("state") in {"verified", "partial"}:
            sector_boards = verified["boards"]
            provenance["sector_boards"] = evidence(trade_date, state=verified["state"],
                source=verified.get("source"), source_day=verified.get("source_trade_date"),
                as_of=verified.get("source_as_of"), received=verified.get("received_at"),
                reason=verified.get("reason") or "")
        elif verified.get("reason") and not any(boards for boards in reported.get("sector_boards", {}).values()):
            provenance["sector_boards"] = evidence(trade_date, source="board_flow.review_source",
                reason=verified["reason"])
    if provenance["sector_boards"].state not in {"verified", "partial"}:
        gap("sector_boards", provenance["sector_boards"].reason, source="board_flow.daily")
    elif any(summary.get("missing_count") for summary in sector_board_summary.values() if isinstance(summary, dict)):
        gap("sector_boards", "部分候选缺当日有效日线；仅已核样本参与强弱排序", source="board_flow.review_source")

    provider_health: dict | None = None
    try:
        if hasattr(hub.provider, "provider_health"):
            provider_health = hub.provider.provider_health()
    except Exception as exc:
        gap("provider_health", str(exc), source="composite.provider_health")
    if sentinel is not None:
        try:
            provider_health = dict(provider_health or {})
            provider_health["ths_reason_sentinel"] = sentinel.snapshot()
        except Exception as exc:
            gap("ths_reason_sentinel", str(exc), source="ths_sentinel.snapshot")
    provenance["provider_health"] = evidence(trade_date, state="partial" if provider_health is not None else "unavailable",
        source="composite.provider_health", received=now,
        reason="报告生成时刻的运行健康，不是报告交易日的历史行情事实")

    auction_premium = None
    try:
        if trade_date != now.date():
            raise ValueError("竞价端点仅供当日，历史复盘不读取当前竞价")
        from app.market.auction_premium import collect_premium
        value = await collect_premium(hub, trade_date)
        if date_key(value.get("trade_date")) != trade_date.isoformat():
            raise ValueError("竞价数据日期与报告日不同")
        # This owner currently drops the individual auction rows' source clocks.
        # Its trade_date echoes the request, so it is not independent date evidence.
        reported["auction_premium"] = value
        provenance["auction_premium"] = evidence(trade_date, state="unverified",
            source="auction_premium.collect_premium", received=now,
            reason="原竞价汇总未保逐行源日期/源钟；请求日回显不是源日证明，仅留采集参考")
        gap("auction_premium", provenance["auction_premium"].reason, source="auction_premium.collect_premium")
        if value.get("caveats"):
            gap("auction_premium", "；".join(value["caveats"]), source="auction_premium.collect_premium")
    except Exception as exc:
        provenance["auction_premium"] = evidence(trade_date, source="auction_premium.collect_premium", reason=str(exc)[:200])
        gap("auction_premium", str(exc), source="auction_premium.collect_premium")

    return MarketSnapshot(trade_date=td, contract_version="date_bound_v1", provenance=provenance,
        reported_values=reported, indices=indices, breadth=breadth, sentiment=sentiment,
        themes=themes, theme_summary=theme_summary, sector_boards=sector_boards,
        sector_board_summary=sector_board_summary, gaps=gaps,
        provider_health=provider_health, auction_premium=auction_premium)


# ---------------------------------------------------------------- 交易与账户


def collect_trading(
    session_factory, trade_date: date, price_map: dict[str, float] | None = None
) -> TradingSnapshot:
    """采集当日操作与账户数据（同步，走 DB）。

    :param price_map: symbol -> 最新价，用于持仓浮盈；缺失则 pnl 记 None 并标 gap
    """
    from app.core.bjtime import beijing_now
    from app.review.provenance import evidence

    collected = beijing_now()
    current_account = trade_date == collected.date()
    gaps: list[DataGap] = []
    td = _date_key(trade_date)
    price_map = (price_map or {}) if current_account else {}

    orders: list[OrderRecord] = []
    positions: list[PositionRecord] = []
    account: dict = {}

    db = session_factory()
    try:
        # --- 账户 ---
        # scope 过滤（S1-1 同族，2026-09-11）：trades 段只描述**用户交易账户 main**，
        # 影子账户（shadow）在下方 ShadowSnapshot 独立采集。此前三处查询均无 scope，
        # 影子持仓/委托会混进「账户与盈亏 / 操作评估」⇒ 复盘口径被污染且不可见。
        try:
            acc = (db.execute(
                select(PaperAccount).where(PaperAccount.scope == SCOPE_MAIN)
                .order_by(PaperAccount.id)
            ).scalars().first()) if current_account else None
            if acc:
                account = {
                    "cash": acc.cash,
                    "initial_cash": acc.initial_cash,
                    "updated_at": acc.updated_at.isoformat() if acc.updated_at else None,
                }
            else:
                gaps.append(DataGap(
                    field="account", source="paper_account",
                    reason="无账户记录" if current_account else "历史复盘不以当前账户余额冒充报告日余额",
                    impact="trades.账户与盈亏", severity="warn" if not current_account else "block",
                ))
        except Exception as exc:
            gaps.append(DataGap(
                field="account", source="paper_account", reason=str(exc)[:200],
                impact="trades.账户与盈亏", severity="block",
            ))

        # --- 当日订单（按北京时间过滤）---
        try:
            rows = db.execute(
                select(PaperOrder).where(PaperOrder.scope == SCOPE_MAIN)
                .order_by(PaperOrder.created_at)
            ).scalars().all()
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
            prows = (db.execute(
                select(PaperPosition).where(PaperPosition.scope == SCOPE_MAIN)
            ).scalars().all()) if current_account else []
            if not current_account:
                gaps.append(DataGap(field="positions", source="paper_position",
                    reason="没有报告日持仓快照；未用当前持仓和现价回填历史浮盈",
                    impact="trades.持仓浮盈", severity="warn"))
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

    # A filled sell price minus its order limit is slippage, not realized profit.
    # The order row has no acquisition-cost/lot or immutable fill-time ledger.
    realized = None
    if any(o.side == "sell" and o.status == "filled" for o in orders):
        gaps.append(DataGap(field="realized_pnl", source="paper_order",
            reason="原委托未保存本次卖出的成本批次；成交价减委托价不是已实现盈亏，未估算",
            impact="trades.已实现盈亏", severity="warn"))

    return TradingSnapshot(
        trade_date=td, contract_version="date_bound_v1",
        provenance={
            "orders": evidence(trade_date, state="verified" if not any(g.field == "orders" for g in gaps) else "unavailable",
                source="paper_order:main", source_day=trade_date.isoformat(), received=collected,
                reason="按原委托created_at的北京日期筛选；委托/已成交状态不等于该日新增成交"),
            "account": evidence(trade_date, state="partial" if account else "unavailable",
                source="paper_account:main", received=collected,
                reason="生成时刻主模拟账户余额，非历史收盘快照" if current_account else "历史账户快照未保存，未读取当前余额"),
            "positions": evidence(trade_date, state="partial" if current_account else "unavailable",
                source="paper_position:main", received=collected,
                reason="生成时刻主模拟持仓；缺可信同日价格不计算浮盈" if current_account else "无报告日持仓快照，当前持仓未冒充历史"),
        }, account=account, orders=orders, positions=positions,
        realized_pnl=round(realized, 2) if realized is not None else None,
        trade_count=len(orders), gaps=gaps,
    )


# ---------------------------------------------------------------- 每日精选（2026-09-04 新增维度）


def collect_picks(session_factory, trade_date: date) -> "PicksSnapshot":
    """采集每日精选组合与当日逐股归因（同步，走 DB；调用方丢线程池）。

    仅取复盘日的当日候选，兼容旧紧凑日期。逐股归因行
    （daily_pick_review）由 generate_daily_review 在复盘前置步写入；
    缺失时标 gap 降级，不冒充"全部达成"。
    """
    from app.models.daily_pick import DailyPickReview, DailyPickSet
    from app.picks.review_contract import bound_review, day_keys, finite_number, object_json, selection_version, trusted_review
    gaps: list[DataGap] = []
    td = _date_key(trade_date)
    iso, compact = day_keys(trade_date)

    with session_factory() as db:
        combo_date: str | None = None
        items_raw: list[dict] = []
        meta: dict = {}
        try:
            row = (
                db.execute(
                    select(DailyPickSet)
                    .where(DailyPickSet.date.in_((iso, compact)))
                    .order_by(DailyPickSet.date.asc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if row is not None:
                combo_date = iso
                items_raw = json.loads(row.items or "[]")
                meta = object_json(row.meta)
            if not items_raw:
                gaps.append(DataGap(
                    field="picks.combo", source="daily_pick_set",
                    reason=f"复盘日 {iso} 无当日精选组合（不将旧组合或未来组合当成本日推荐）",
                    impact="picks.每日精选对照", severity="warn",
                ))
        except Exception as exc:
            gaps.append(DataGap(
                field="picks.combo", source="daily_pick_set",
                reason=str(exc)[:200], impact="picks.每日精选对照", severity="warn",
            ))

        reviews: list[PickReviewEntry] = []
        try:
            rows = (
                db.execute(
                    select(DailyPickReview)
                    .where(DailyPickReview.date.in_((iso, compact)))
                    .order_by(DailyPickReview.date.asc(), DailyPickReview.symbol)
                )
                .scalars()
                .all()
            )
            excluded = 0
            seen = set()
            current_version = selection_version(iso, meta, items_raw)
            for r in sorted(rows, key=lambda r: (r.selection_version != current_version, r.date != iso, -r.id)):
                if r.symbol in seen:
                    continue
                seen.add(r.symbol)
                bound = bound_review(r.review_context, day=iso, meta=meta, items=items_raw, symbol=r.symbol, persisted_version=r.selection_version)
                eligible = finite_number(r.excess_pct) is not None and trusted_review(r.review_context, day=iso, meta=meta, items=items_raw, symbol=r.symbol, persisted_version=r.selection_version)
                excluded += int(not eligible)
                reviews.append(PickReviewEntry(
                    symbol=r.symbol, name=r.name, verdict=r.verdict,
                    reason_category=r.reason_category,
                    excess_pct=r.excess_pct, note=r.note, review_context=object_json(r.review_context),
                    binding_state="bound" if bound else "legacy_or_superseded", statistics_eligible=eligible,
                ))
            if excluded:
                gaps.append(DataGap(field="picks.review_binding", source="daily_pick_review",
                    reason=f"{excluded} 条旧未绑定、版本已改变或观察窗口不可评，已排除可信统计",
                    impact="picks.准确率与失误归因", severity="warn"))
            # 归因行缺失但组合存在：归因步没跑成功——诚实降级，不让准确率假装 100%
            if items_raw and not reviews:
                gaps.append(DataGap(
                    field="picks.reviews", source="daily_pick_review",
                    reason=f"复盘日 {td} 无逐股归因行（generate_daily_review 未成功执行）",
                    impact="picks.准确率与失误归因", severity="warn",
                ))
        except Exception as exc:
            gaps.append(DataGap(
                field="picks.reviews", source="daily_pick_review",
                reason=str(exc)[:200], impact="picks.准确率与失误归因", severity="warn",
            ))

    # --- 影子持仓（P0-B）：启用但当日无执行日志才标 gap（未启用属配置常态） ---
    shadow: "ShadowSnapshot | None" = None
    try:
        from app.core.config import settings as _settings
        from app.picks.shadow import collect_shadow_for_review, execution_log_issues

        if _settings.picks_shadow_enabled:
            shadow_raw = collect_shadow_for_review(session_factory, trade_date)
            shadow = ShadowSnapshot(enabled=True, **(shadow_raw or {}))
            for field, reason in execution_log_issues(shadow.execution, shadow.account).items():
                gaps.append(DataGap(
                    field=f"picks.shadow.{field}", source="picks/shadow", reason=reason,
                    impact="picks.影子对照（空仓 A/B）", severity="warn",
                ))
    except Exception as exc:
        log.warning("review collect: shadow snapshot failed: %s", exc)
        shadow = ShadowSnapshot(enabled=False)
        gaps.append(DataGap(
            field="picks.shadow", source="picks/shadow",
            reason=str(exc)[:200], impact="picks.影子对照（空仓 A/B）", severity="warn",
        ))

    return PicksSnapshot(
        trade_date=td,
        combo_date=combo_date,
        items=[
            PickEntry(
                symbol=i.get("symbol", "?"),
                name=i.get("name"),
                score=i.get("score"),
                themes=i.get("themes") or [],
                observation_only=bool(i.get("observation_only")),
                basis=_saved_pick_basis(i),
                selection_ref=i.get("selection_ref") if isinstance(i.get("selection_ref"), dict) else None,
                source_version=str(i.get("source_version")) if i.get("source_version") else None,
                event_refs=[r for r in (i.get("related_event_refs") or i.get("event_refs") or []) if isinstance(r, dict)],
            )
            for i in items_raw
        ],
        reviews=reviews,
        shadow=shadow,
        gaps=gaps,
    )


def _saved_pick_basis(item: dict) -> str | None:
    """Read the original scoring/observation explanation; never invent a rationale."""
    explicit = item.get("pick_basis") or item.get("source_basis")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    pieces: list[str] = []
    if item.get("observation_only"):
        reasons = item.get("follow_reasons")
        if isinstance(reasons, list):
            saved = [v for v in reasons if isinstance(v, str) and v.strip()]
            if saved:
                pieces.append("原观察约束：" + "；".join(saved))
    bases = item.get("bases")
    if isinstance(bases, dict):
        saved = [v for v in bases.values() if isinstance(v, str) and v.strip()]
        if saved:
            pieces.append("原评分依据（非买入理由）：" + "；".join(saved))
    return "。".join(pieces) or None
