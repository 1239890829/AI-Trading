"""账户工具：真实持仓、模拟盘、自选股。

`app/assistant/tools.py` 的内部切片（IMP-005，2026-09-15 从 1934 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app/assistant/tools/__init__.py` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

from typing import Any

from .core import (
    MAX_ROWS,
    MAX_SYMBOLS,
    ToolContext,
    _clip,
    log,
)
async def _t_positions(ctx: ToolContext, **kw) -> str:
    """当前持仓与浮动盈亏（AI 大脑 P1：助手触达持仓数据资产）。"""
    from app.services.real_position_service import load_positions

    if ctx.session_factory is None:
        return "工具不可用：未配置数据库会话"

    import asyncio as _asyncio

    positions = await _asyncio.to_thread(load_positions, ctx.session_factory)
    if not positions:
        return "当前无持仓"
    quotes = {}
    try:
        syms = [p.symbol for p in positions][:MAX_SYMBOLS]
        got = await ctx.provider.get_quotes(syms)
        quotes = {q.symbol: q for q in got or []}
    except Exception as exc:  # noqa: BLE001  行情缺失 → 回退成本价口径，如实标注
        quotes = {}
        log.warning("positions tool quotes failed: %s", exc)
    lines = [f"【持仓 {len(positions)} 只】"]
    total_unreal = 0.0
    have_price = True
    for p in positions[:MAX_ROWS]:
        q = quotes.get(p.symbol)
        last = getattr(q, "price", None) if q is not None else None
        if last is None:
            last = p.avg_cost  # 缺行情回退成本价（与 /api/positions 同口径）
            have_price = False
        unreal = round((last - p.avg_cost) * p.quantity, 2)
        total_unreal += unreal
        pct = round((last / p.avg_cost - 1) * 100, 2) if p.avg_cost else None
        lines.append(
            f"- {p.name or ''}({p.symbol})：{p.quantity} 股｜成本 {p.avg_cost}｜现价 {last}"
            f"｜浮动 {unreal}（{pct}%）"
        )
    tail = f"浮动盈亏合计 {round(total_unreal, 2)}"
    if not have_price:
        tail += "（部分缺实时价，按成本价口径，非真实市值）"
    lines.append(f"- {tail}")
    return _clip("\n".join(lines))


async def _t_watchlist(ctx: ToolContext, **kw) -> str:
    """自选股清单 + 实时行情（按分组）。"""
    if ctx.session_factory is None:
        return "工具不可用：未配置数据库会话"
    import asyncio as _asyncio

    from app.repositories.watchlist_repo import WatchlistRepository

    def _load() -> list[dict]:
        repo = WatchlistRepository(ctx.session_factory)
        return [
            {"symbol": i.symbol, "name": i.name, "group_name": i.group_name, "note": i.note}
            for i in repo.list_items()
        ]

    items = await _asyncio.to_thread(_load)
    if not items:
        return "自选股为空"
    quotes: dict[str, Any] = {}
    try:
        got = await ctx.provider.get_quotes([i["symbol"] for i in items][:MAX_SYMBOLS])
        quotes = {q.symbol: q for q in got or []}
    except Exception as exc:  # noqa: BLE001
        log.info("watchlist quotes: %s", exc)
    groups: dict[str, list[str]] = {}
    lines = [f"【自选股 {len(items)} 只（行情为实时快照口径）】"]
    for i in items[:MAX_ROWS]:
        q = quotes.get(i["symbol"])
        price = getattr(q, "price", None) if q is not None else None
        pct = getattr(q, "change_pct", None) if q is not None else None
        groups.setdefault(i.get("group_name") or "默认", []).append(i["symbol"])
        lines.append(
            f"- [{i.get('group_name') or '默认'}] {i.get('name') or ''}({i['symbol']})："
            f"现价 {price if price is not None else '—'}"
            f"｜涨跌 {f'{pct:+.2f}%' if isinstance(pct, (int, float)) else '—'}"
        )
    if groups:
        lines.append("- 分组：" + "；".join(f"{g} {len(v)} 只" for g, v in groups.items()))
    if not quotes:
        lines.append("- 注：实时行情未取到，仅列出清单（可用 quotes 工具单独取价）")
    return _clip("\n".join(lines))


async def _t_paper(ctx: ToolContext, **kw) -> str:
    """模拟交易账户：资产汇总 + 持仓浮盈（只读；不提供下单）。"""
    engine = ctx.paper_engine
    if engine is None:
        return "工具不可用：模拟交易引擎未初始化"
    try:
        positions = list(engine.positions_with_pnl({}) or [])
    except Exception as exc:  # noqa: BLE001
        return f"模拟账户读取失败：{type(exc).__name__}: {exc}"
    quotes: dict[str, Any] = {}
    try:
        got = await ctx.provider.get_quotes([p.get("symbol") for p in positions][:MAX_SYMBOLS])
        quotes = {q.symbol: q for q in got or []}
    except Exception as exc:  # noqa: BLE001
        log.info("paper quotes: %s", exc)
    for p in positions:
        q = quotes.get(p.get("symbol"))
        last = getattr(q, "price", None) if q is not None else None
        if last is not None and p.get("cost_price"):
            p["last_price"] = last
            p["pnl"] = round((last - p["cost_price"]) * p["quantity"], 2)
    mv = sum((p.get("last_price") or p.get("cost_price") or 0) * p.get("quantity", 0) for p in positions)
    try:
        summary = engine.account_summary(mv)
    except Exception as exc:  # noqa: BLE001
        return f"模拟账户汇总失败：{type(exc).__name__}: {exc}"
    lines = ["【模拟账户（只读；本系统不接真实券商）】"]
    for k in ("initial_cash", "cash", "market_value", "total_assets", "total_pnl", "total_pnl_pct"):
        if isinstance(summary, dict) and summary.get(k) is not None:
            lines.append(f"- {k}：{summary[k]}")
    if positions:
        lines.append(f"- 持仓 {len(positions)} 只：")
        for p in positions[:MAX_ROWS]:
            lines.append(
                f"  · {p.get('name') or ''}({p.get('symbol')})：{p.get('quantity')} 股"
                f"｜成本 {p.get('cost_price')}｜现价 {p.get('last_price') or '—'}"
                f"｜浮动 {p.get('pnl', '—')}"
            )
    else:
        lines.append("- 当前无持仓")
    return _clip("\n".join(lines))
