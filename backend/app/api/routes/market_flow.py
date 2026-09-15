"""成交与资金流：两市成交、资金流（含板块资金流）、个股资金流。

（自 `market.py` 切出，2026-09-15 IMP-005 批 3。**只搬位置，未改逻辑**：
分片正文与原文件对应定义逐字相同。跨分片共用的信封辅助在 `market_envelope`。）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from app.api.deps import get_hub
from app.core.ttl_cache import cache_on
from app.market.normalizer import main_board
from app.services.quote_hub import QuoteHub

router = APIRouter(tags=["market"])
log = logging.getLogger("app.api.routes.market")

from app.api.routes.market_envelope import (
    meta_payload,
)


@router.get("/market/turnover")
async def market_turnover(hub: QuoteHub = Depends(get_hub)) -> dict:
    """今日两市成交额（沪深口径）：实时 + 昨日同一时刻对比 + 全日估算 + 分时曲线。

    数据源与降级策略见 app/market/fund_flow.py 模块头。沪深京口径的市场总览
    total_amount 各自独立、互不冒充。
    """
    from app.market.fund_flow import get_turnover_today

    return {"data": await get_turnover_today(hub), "meta": meta_payload(hub)}


@router.get("/market/turnover/history")
async def market_turnover_history(
    hub: QuoteHub = Depends(get_hub),
    days: int = Query(default=10, ge=2, le=20),
) -> dict:
    """近 N 个交易日全日成交额 + vs 前一交易日增减（由近及远）。"""
    from app.market.fund_flow import get_turnover_history

    return {"data": await get_turnover_history(hub, days), "meta": meta_payload(hub)}


@router.get("/market/turnover/day")
async def market_turnover_day(
    hub: QuoteHub = Depends(get_hub),
    date: str = Query(description="交易日 YYYY-MM-DD"),
) -> dict:
    """指定历史交易日 vs 前一交易日：全日成交额增减 + 分时累计曲线对比。"""
    from app.market.fund_flow import get_turnover_day

    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"date 格式须为 YYYY-MM-DD：{date!r}") from exc
    return {"data": await get_turnover_day(hub, d), "meta": meta_payload(hub)}


@router.get("/market/fund-flow/intraday")
async def market_fund_flow_intraday() -> dict:
    """今日分钟级资金流累计曲线（沪深合计，五档；延迟约 15 分钟的东财免费口径）。

    历史日的分钟资金流数据源不提供——历史回看走 /market/fund-flow/history（日度）。
    """
    from app.market.fund_flow import get_fund_flow_intraday

    return {"data": await get_fund_flow_intraday(), "meta": {}}


@router.get("/market/fund-flow")
async def market_fund_flow() -> dict:
    """实时资金流五档净额（东财大盘口径：主力/超大/大/中/小单，沪深合计）。

    机构/游资在实时全市场数据源中不存在拆分（仅龙虎榜日度有），不提供臆造字段。
    """
    from app.market.fund_flow import get_fund_flow_realtime

    return {"data": await get_fund_flow_realtime(), "meta": {}}


@router.get("/market/fund-flow/history")
async def market_fund_flow_history(
    hub: QuoteHub = Depends(get_hub),
    days: int = Query(default=30, ge=5, le=40),
) -> dict:
    """日度资金流序列（沪深合计，由近及远；本地落盘优先，落后时拉东财补齐）。"""
    from app.market.fund_flow import get_fund_flow_history

    return {"data": await get_fund_flow_history(hub, days), "meta": meta_payload(hub)}


@router.get("/market/board-fund-flow")
async def market_board_fund_flow(
    kind: str = Query(default="concept", description="concept 概念 / industry 行业"),
    range: str = Query(default="intraday", description="intraday 今日 / 5d / 10d / 20d"),
) -> dict:
    """板块资金流榜（L2 唯一实现，docs/summary/architecture-design.md §2 四层级模型）。

    - intraday/5d/10d：一次翻页全量（f62 今日 / f164 5日 / f174 10日，官方字段已实测），
      前端排序筛选纯内存，切换不回源；
    - 20d 与连续流入天数：只读落盘（每日收盘后自动沉淀 Top 板块 daykline），读路径零外呼；
    - 板块 f62 是东财官方板块口径，不与个股新浪口径混算。
    """
    from app.market.board_flow import get_board_fund_flow

    if kind not in ("concept", "industry"):
        raise HTTPException(status_code=422, detail="kind 仅允许 concept / industry")
    if range not in ("intraday", "5d", "10d", "20d"):
        raise HTTPException(status_code=422, detail="range 仅允许 intraday / 5d / 10d / 20d")
    return {"data": await get_board_fund_flow(kind, range), "meta": {}}


def _valid_board_code(board_code: str) -> str | None:
    code = board_code.strip().upper()
    if code.startswith("BK") and code[2:].isdigit() and len(code) in (5, 6):
        return code
    return None


@router.get("/market/board-fund-flow/{board_code}/minute")
async def market_board_flow_minute(board_code: str) -> dict:
    """板块分钟五档资金流累计曲线（东财延迟 ~15min 口径，available=false 时显式 reason）。"""
    from app.market.board_flow import get_board_minute

    code = _valid_board_code(board_code)
    if code is None:
        raise HTTPException(status_code=422, detail=f"board_code 须为 BKxxxx 形式：{board_code!r}")
    return {"data": await get_board_minute(code), "meta": {}}


@router.get("/market/board-fund-flow/{board_code}/members")
async def market_board_flow_members(board_code: str) -> dict:
    """板块成员个股资金排行 Top20（板块内资金龙头；东财成员口径）。"""
    from app.market.board_flow import get_board_members

    code = _valid_board_code(board_code)
    if code is None:
        raise HTTPException(status_code=422, detail=f"board_code 须为 BKxxxx 形式：{board_code!r}")
    return {"data": await get_board_members(code), "meta": {}}


@router.get("/market/board-fund/by-symbols")
async def market_board_fund_by_symbols(
    request: Request,
    symbols: str = Query(description="逗号分隔 6 位代码，≤50"),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """自选行「所属板块资金」徽标（P1-4，2026-09-10）。

    **主板块口径** = 东财 F10 ssbk 行业三级的 **L2（Ⅱ级）行**（`classify_boards` 按
    `IS_PRECISE` 分段得到行业段）——「这只股是做什么的」的最近语义层级。**不用概念段首个**：
    那是 ssbk 返回顺序里的首个概念标签，与相关性无关（2026-09-10 实测茅台→「味蕾经济」、
    平安银行→「跨境支付」，语义不成立）。无行业段时才回落概念首个并如实标注 `level`。
    板块按 **BOARD_CODE 直取**（不按名字匹配——行业三级名带罗马数字后缀「白酒Ⅱ」，
    板块榜里是「白酒」，走名字会引入歧义）。

    **板块资金** = 东财 f62，经 `theme_service.board_rows_for_codes`（L3 映射 →
    复用 board_flow 盘中 30s 缓存，**零额外上游调用**）。连续流入天数只对落盘
    Top 板块可判，其余 None（三态，区别于 0=今日净流出）。资料/板块取不到 →
    该 symbol **不出现在返回里**，绝不臆造。
    """
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        raise HTTPException(status_code=422, detail="symbols 不能为空")
    if len(sym_list) > 50:
        raise HTTPException(status_code=422, detail="单批最多 50 个代码")

    cache = cache_on(request.app.state, "market.board-fund.by-symbols", 60, maxsize=64)
    key = (tuple(sym_list),)

    async def _build() -> dict:
        # 所属板块是**低频变更**数据（成分调整才变）→ 6h 缓存，避免自选轮询反复打 F10。
        board_cache = cache_on(request.app.state, "market.board-fund.main-board", 6 * 3600, maxsize=512)
        sem = asyncio.Semaphore(8)

        async def _main_board(sym: str) -> tuple[str, dict | None]:
            hit, cached = board_cache.get(sym)
            if hit:
                return sym, cached
            async with sem:
                try:
                    profile = await hub.provider.get_company_profile(sym)
                except Exception as exc:  # noqa: BLE001 单只失败不影响其余
                    log.debug("board-fund: profile %s failed: %s", sym, exc)
                    return sym, None
            groups = profile.get("board_groups") or {}
            ref = main_board(groups, profile.get("board_codes") or {})
            board_cache.set(sym, ref)
            return sym, ref

        pairs = await asyncio.gather(*[_main_board(s) for s in sym_list])
        from app.services.theme_service import board_rows_for_codes

        rows = await board_rows_for_codes([r["code"] for _, r in pairs if r and r.get("code")])
        boards: dict[str, dict] = {}
        for sym, ref in pairs:
            row = rows.get(ref.get("code")) if ref else None
            if not row:
                continue  # 板块代码未在东财板块榜中 → 缺省，不臆造
            boards[sym] = {
                "board_name": row.get("name") or (ref or {}).get("name"),
                "board_code": row.get("board_code"),
                "kind": row.get("kind"),
                "level": (ref or {}).get("level"),
                "change_pct": row.get("change_pct"),
                "main_net_yi": row.get("main_net_yi"),
                "main_net_ratio": row.get("main_net_ratio"),
                "streak": row.get("streak"),
            }
        return {
            "boards": boards,
            "note": None if boards else "所属板块资金暂不可用（公司资料或板块列表取不到）",
        }

    _, payload = await cache.get_or_set(key, _build)
    return {"data": payload, "meta": meta_payload(hub)}


@router.get("/capital-flow/{symbol}")
async def capital_flow(
    symbol: str,
    days: int = Query(default=30, ge=1, le=100),
    hub: QuoteHub = Depends(get_hub),
) -> dict:
    """个股资金流（新浪口径：主力=超大单+大单；按单笔成交额四级拆分，见页面口径说明）。"""
    try:
        rows = await hub.provider.get_capital_flow(symbol, days)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"资金流数据源失败：{exc}")
    streak = 0
    for r in rows:  # rows 按日期倒序
        if (r.get("net_main") or 0) > 0:
            streak += 1
        else:
            break
    return {
        "data": {
            "symbol": symbol,
            "days": len(rows),
            "flow": rows,
            "streak_in": streak,
            "definition": "主力净流入 = 超大单净额 + 大单净额（新浪按单笔成交金额划分：≥50万股或100万元视为大单级别，具体阈值为新浪口径，属估算数据非交易所披露）",
        },
        "meta": meta_payload(hub),
    }


@router.get("/stock-flow")
async def market_stock_flow(symbols: str = Query(default="", description="逗号分隔6位代码，≤60 只")) -> dict:
    """个股资金流（当日累计五档净额，亿元；方向2 P1）。

    口径见 app/market/stock_flow.py：北交所无个股资金流 → 进 no_data 显式列出；
    全部失败 → items 空且 degraded 非空（绝不填 0）。
    """
    from app.market import stock_flow

    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    payload = await stock_flow.get_stock_flow(syms)
    return {"data": payload, "meta": {}}
