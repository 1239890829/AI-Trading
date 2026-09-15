"""题材工具：成分股、题材强度、热门股。

`app/assistant/tools.py` 的内部切片（IMP-005，2026-09-15 从 1934 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app/assistant/tools/__init__.py` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

import asyncio

from .core import (
    ToolContext,
    _clip,
    _fmt_rows,
    _int_arg,
    _resolve_date,
)
from .market import (
    _snapshot_map_sync,
)
async def _t_theme_members(ctx: ToolContext, **kw) -> str:
    """题材成分明细：给一个题材名，返回它的官方成分股。

    P2-28① 的第一个工具。此前助手被问「XX 题材有哪些票」时只能凭印象作答——
    官方题材目录（含成分）后端早已就绪，只是**没有登记给助手**（能力空窗）。

    三条纪律：
    1. 目录服务不可用/为空时**明确说"取不到"**，绝不凭印象编造成分
       （编成分比说不知道危险得多）；
    2. 多个题材同名/模糊命中时，把候选列出来让用户/模型指认，不擅自挑一个；
    3. 成分过多时截断并如实说明总数，不假装只有这些。
    """
    keyword = (kw.get("keyword") or "").strip()
    if not keyword:
        return "参数缺失：keyword（题材名，如 代糖 / 玉米 / 创新药）"

    svc = ctx.theme_catalog
    if svc is None:
        return "题材成分：官方题材目录服务不可用（不是没有该题材，是取不到）"
    try:
        catalog = list(svc.get_catalog(limit=1000) or [])
    except Exception as exc:  # noqa: BLE001
        return f"题材成分：目录读取失败（{exc}）"
    if not catalog:
        return "题材成分：官方题材目录为空（可能尚未同步）"

    hits = [c for c in catalog if keyword in str(getattr(c, "name", "") or "")]
    if not hits:
        sample = "、".join(str(getattr(c, "name", "")) for c in catalog[:8])
        return f"未找到含「{keyword}」的题材。目录中前几项示例：{sample}"

    if len(hits) > 1:
        cand = "、".join(f"{getattr(c, 'name', '')}({getattr(c, 'code', '')})" for c in hits[:8])
        return f"「{keyword}」命中 {len(hits)} 个题材，请指明是哪一个：{cand}"

    one = hits[0]
    code, name = str(getattr(one, "code", "")), str(getattr(one, "name", "") or keyword)
    try:
        members = list(svc.get_members(code) or [])
    except Exception as exc:  # noqa: BLE001
        return f"题材成分：{name} 成员读取失败（{exc}）"
    if not members:
        return f"题材成分：{name}（{code}）暂无成分数据"

    rows = [{"symbol": str(getattr(m, "symbol", "") or m.get("symbol", "")),
             "name": str(getattr(m, "name", "") or (m.get("name", "") if isinstance(m, dict) else ""))}
            for m in members]
    return _fmt_rows(f"题材成分 {name}（{code}）", rows,
                     [("name", ""), ("symbol", "")], total=len(rows))


async def _t_hot(ctx: ToolContext, **kw) -> str:
    period = (kw.get("period") or "day").strip()
    if period not in ("day", "week", "month"):
        return f"参数不合法：period 只接受 day/week/month，收到 {period!r}"
    rows = list((await ctx.provider.get_hot_stock_list(period)) or [])
    return _fmt_rows(f"热股榜 {period}", rows, [
        ("name", ""), ("symbol", ""), ("rank", "排名"),
        ("change_pct", "涨幅"), ("hot_value", "热度"),
    ], total=len(rows))


async def _t_themes(ctx: ToolContext, **kw) -> str:
    """题材梯队看板：涨停池按题材重组后的强度/阶段/健康度（与盘面页同源）。"""
    d, e = _resolve_date(ctx, kw.get("date"))
    if e:
        return f"参数不合法：{e}"
    limit = _int_arg(kw.get("limit"), 10, 3, 30)
    try:
        from app.services.theme_service import build_theme_board

        snap = await asyncio.to_thread(_snapshot_map_sync, ctx, d) if ctx.snapshot_service else {}
        board = await build_theme_board(ctx.provider, d, snapshot_map=snap or None)
    except Exception as exc:  # noqa: BLE001
        return f"题材梯队构建失败：{type(exc).__name__}: {exc}（涨停池不可用时会这样，请稍后重试）"
    cards = list(board.get("themes") or [])
    summary = board.get("summary") or {}
    if not cards:
        return f"{d} 无题材梯队数据（当日无涨停或非交易日）"
    lines = [
        f"【题材梯队 {board.get('trade_date') or d}】"
        f"涨停 {summary.get('limit_up_total', '—')} 家｜题材 {summary.get('theme_count', '—')} 个"
        f"｜最高连板 {summary.get('market_max_boards', '—')}"
        f"｜炸板率 {summary.get('market_break_rate', '—')}"
    ]
    for c in cards[:limit]:
        perf = c.get("performance") or {}
        core = c.get("core") or {}
        seg = f"｜核心 {core.get('name')} {core.get('boards')}板" if core.get("name") else ""
        lines.append(
            f"- {c.get('theme')}：强度 {c.get('strength_score')}（{c.get('strength_tier') or '—'}）"
            f"｜阶段 {c.get('stage') or '—'}｜梯队 {c.get('formation') or '—'}"
            f"｜涨停 {perf.get('limit_up_count', '—')} 家/最高 {perf.get('max_boards', '—')} 板{seg}"
        )
        if c.get("health_note"):
            lines.append(f"  · 健康度：{c['health_note']}")
        if c.get("risks"):
            lines.append(f"  · 风险：{'；'.join(str(r) for r in c['risks'][:3])}")
    lines.append("- 口径：题材归因来自同花顺官方涨停原因；涨停池非交易时段为最近交易日收盘口径。")
    return _clip("\n".join(lines))
