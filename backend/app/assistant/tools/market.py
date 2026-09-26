"""行情与个股工具：报价、涨跌停、龙虎榜、盘口成交、K 线分时、资金流、财务基础、大盘概览、板块。

`app/assistant/tools.py` 的内部切片（IMP-005，2026-09-15 从 1934 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app/assistant/tools/__init__.py` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from app.market.tdx_tick import fetch_trades_with_tdx_fallback, trades_failure_detail
from app.services.market_snapshot import PoolDateError, verify_limit_down_date

from .core import (
    TIMEFRAMES,
    ToolContext,
    _clip,
    _fmt_rows,
    _fmt_yi,
    _int_arg,
    _one_symbol,
    _rec,
    _resolve_date,
    _valid_symbols,
    log,
)
async def _t_quotes(ctx: ToolContext, **kw) -> str:
    raw = kw.get("symbols", "")
    codes, e = _valid_symbols(raw, ctx.known_symbols or None, allow_index=True)
    if e:
        return f"参数不合法：{e}"
    quotes = await ctx.provider.get_quotes(codes)
    return _fmt_rows("实时快照", list(quotes or []), [
        ("name", ""), ("symbol", ""), ("price", "现价"),
        ("change_pct", "涨跌幅"), ("amount", "成交额"),
    ])


async def _t_limit_up(ctx: ToolContext, **kw) -> str:
    d, e = _resolve_date(ctx, kw.get("date"))
    if e:
        return f"参数不合法：{e}"
    rows = await ctx.provider.get_limit_up_pool(d)
    rows = list(rows or [])
    return _fmt_rows(f"涨停池 {d}", rows, [
        ("name", ""), ("symbol", ""), ("consecutive_boards", "连板"),
        ("change_pct", "涨幅"), ("reason", "题材"),
    ], total=len(rows))


async def _t_limit_down(ctx: ToolContext, **kw) -> str:
    d, e = _resolve_date(ctx, kw.get("date"))
    if e:
        return f"参数不合法：{e}"
    try:
        await verify_limit_down_date(ctx.provider, d)
    except PoolDateError as exc:
        return f"跌停池日期不可用：{exc}"
    rows = list((await ctx.provider.get_limit_down_pool(d)) or [])
    return _fmt_rows(f"跌停池 {d}", rows, [
        ("name", ""), ("symbol", ""), ("change_pct", "跌幅"),
        ("consecutive_days", "连跌天"),
    ], total=len(rows))


async def _t_limit_break(ctx: ToolContext, **kw) -> str:
    d, e = _resolve_date(ctx, kw.get("date"))
    if e:
        return f"参数不合法：{e}"
    rows = list((await ctx.provider.get_limit_break_pool(d)) or [])
    return _fmt_rows(f"炸板池 {d}", rows, [
        ("name", ""), ("symbol", ""), ("break_count", "开板次"),
        ("change_pct", "涨幅"),
    ], total=len(rows))


async def _t_longhu(ctx: ToolContext, **kw) -> str:
    """龙虎榜：默认当日全市场榜；给 `symbols` 则转个股维度（席位明细 + 上榜历史表现）。

    个股维度（2026-09-11 补）解决的是「我持仓/关注的这只票有没有上龙虎榜、谁在买」——
    此前只有全市场榜，助手被问个股龙虎榜时只能答"我没有"，而系统里明明有
    `GET /api/longhu/{symbol}`。
    """
    syms = (kw.get("symbols") or "").strip()
    if syms:
        code, e = _one_symbol(ctx, syms, "symbols")
        if e:
            return f"参数不合法：{e}"
        return await _longhu_stock(ctx, code, kw.get("date"))
    d, e = _resolve_date(ctx, kw.get("date"))
    if e:
        return f"参数不合法：{e}"
    rows = list((await ctx.provider.get_longhu_records(d)) or [])
    return _fmt_rows(f"龙虎榜 {d}", rows, [
        ("name", ""), ("symbol", ""), ("net_buy", "净买"),
        ("change_pct", "涨幅"), ("reason", "原因"),
    ], total=len(rows))


async def _longhu_stock(ctx: ToolContext, code: str, raw_date: str | None) -> str:
    d, e = _resolve_date(ctx, raw_date)
    if e:
        return f"参数不合法：{e}"
    try:
        detail = _rec(await ctx.provider.get_longhu_detail(code, d))
    except Exception as exc:  # noqa: BLE001  东财未上榜会抛 ProviderError，属正常空态
        detail = {}
        log.info("longhu_detail %s %s: %s", code, d, exc)
    buys = [_rec(x) for x in (detail.get("buy_seats") or [])]
    sells = [_rec(x) for x in (detail.get("sell_seats") or [])]

    lines = [f"【{code} 龙虎榜席位 {d}（东财口径）】"]
    if not buys and not sells:
        lines.append(f"- {d} 无席位记录（该日未上榜，或数据源尚未更新当日榜）")
    for s in buys[:5]:
        lines.append(
            f"- 买 {s.get('seat') or '—'}（{s.get('seat_type') or '—'}）："
            f"买入 {_fmt_yi(s.get('buy'))}，卖出 {_fmt_yi(s.get('sell'))}，净额 {_fmt_yi(s.get('net'))}"
        )
    for s in sells[:5]:
        lines.append(
            f"- 卖 {s.get('seat') or '—'}（{s.get('seat_type') or '—'}）："
            f"买入 {_fmt_yi(s.get('buy'))}，卖出 {_fmt_yi(s.get('sell'))}，净额 {_fmt_yi(s.get('net'))}"
        )

    try:
        history = [_rec(h) for h in (await ctx.provider.get_longhu_history(code)) or []]
    except Exception as exc:  # noqa: BLE001
        history = []
        log.info("longhu_history %s: %s", code, exc)
    if history:
        win = [h for h in history if h.get("after_5d") is not None]
        lines.append(f"- 上榜历史 {len(history)} 次，最近 5 次：")
        for h in history[:5]:
            lines.append(
                f"  · {h.get('trade_date')}：涨跌 {h.get('change_pct')}%"
                f"，净买 {_fmt_yi(h.get('net_buy'))}"
                f"，T+5 {h.get('after_5d')}%"
            )
        if win:
            avg = sum(h["after_5d"] for h in win) / len(win)  # type: ignore[arg-type]
            rate = sum(1 for h in win if (h["after_5d"] or 0) > 0) / len(win)
            lines.append(f"- 历史 T+5 均值 {avg:+.2f}%，胜率 {rate:.0%}（样本 {len(win)} 次）")
    lines.append("- 口径：同一日可能同时披露日榜与三日榜（区间不同，不可相加）。")
    return _clip("\n".join(lines))


async def _t_orderbook(ctx: ToolContext, **kw) -> str:
    """盘口五档（P2-28① 批量登记）。

    ⚠️ 仅 L1 五档——不是 L2，工具说明与回答都不得暗示有逐笔委托队列或十档。
    取不到时如实说"取不到"，不编造档位。
    """
    codes, e = _valid_symbols(kw.get("symbols", ""), ctx.known_symbols or None)
    if e:
        return f"参数不合法：{e}"
    ob = await ctx.provider.get_order_book(codes[0])
    if not ob:
        return f"盘口：{codes[0]} 取不到（数据源未提供该股的盘口）"
    rec = _rec(ob)
    bids = list(rec.get("bids") or rec.get("bid") or [])[:5]
    asks = list(rec.get("asks") or rec.get("ask") or [])[:5]
    if not bids and not asks:
        return f"盘口：{codes[0]} 返回为空（数据源未提供档位数据）"
    lines = [f"盘口 {codes[0]}（五档，L1）"]
    for label, rows in (("卖", asks), ("买", bids)):
        for i, r in enumerate(rows, 1):
            rr = _rec(r)
            lines.append(f"{label}{i} {rr.get('price', '')} × {rr.get('volume', rr.get('qty', ''))}")
    return "\n".join(lines)


async def _t_trades(ctx: ToolContext, **kw) -> str:
    """逐笔成交（P2-28① 批量登记）。

    2026-09-16 `IMP-038`：改走 `fetch_trades_with_tdx_fallback`（provider 链主源 +
    TDX 直连备源），与 `/api/trades/{symbol}` 共用同一条降级链。**"两源都失败"与
    "两源都为空"必须给出不同文案**——把数据源故障说成"没有逐笔"是在制造事实。
    """
    codes, e = _valid_symbols(kw.get("symbols", ""), ctx.known_symbols or None)
    if e:
        return f"参数不合法：{e}"
    rows, source, detail = await fetch_trades_with_tdx_fallback(
        ctx.provider.get_trades, codes[0], limit=200
    )
    if not rows:
        # 保留「取不到」这一结论词（`tests/test_depth_tools.py` 钉住的用户可见契约：
        # 取不到要**如实说取不到**），后面接**原因**——"取数失败"与"两源都为空"
        # 必须可分辨，否则模型会把数据源故障讲成"该股没有逐笔"。
        #
        # ⚠️ 判据用 `trades_failure_detail`（故障白名单），**不能写 `if detail:`**：
        # 备源未启用时 `detail = "chain: empty; tdx: disabled"` 同样非空，
        # 那样会把"没有数据"讲成"取数失败"（2026-09-16 实测踩到）。
        failed = trades_failure_detail(detail)
        if failed:
            why = f"取数失败（{failed}）"
        else:
            why = "两源均未给出数据（非取数故障）" + (f"：{detail}" if detail else "")
        return f"逐笔成交：{codes[0]} 取不到——{why}"
    return _fmt_rows(f"逐笔成交 {codes[0]}（源 {source}）", rows[:30], [
        ("time", "时间"), ("price", "价格"), ("volume", "量"), ("side", "方向"),
    ], total=len(rows))


async def _t_auction(ctx: ToolContext, **kw) -> str:
    """集合竞价快照（P2-28① 批量登记）。

    ⚠️ **仅 ths 一源**（`get_auction_snapshot` 的注释写明）——其他 provider 没有实现。
    因此取不到是**常态而非异常**：必须明确说"该源未提供"，绝不能让模型据此回答
    "没有竞价数据"（那是源覆盖问题，不是事实）。见 P2-15 N2（竞价备源）。
    """
    codes, e = _valid_symbols(kw.get("symbols", ""), ctx.known_symbols or None)
    if e:
        return f"参数不合法：{e}"
    stage = (kw.get("stage") or "final").strip()
    try:
        rows = list((await ctx.provider.get_auction_snapshot(codes, stage)) or [])
    except Exception as exc:  # noqa: BLE001
        return f"竞价快照：读取失败（{exc}）——当前仅 ths 一源，非该源可见时取不到属正常"
    if not rows:
        return ("竞价快照：取不到（当前仅 ths 一源提供，其他数据源无此能力；"
                "这不代表该股没有竞价数据）")
    return _fmt_rows(f"集合竞价 {stage}", rows, [
        ("name", ""), ("symbol", ""), ("price", "竞价"), ("change_pct", "涨幅"),
        ("volume", "量"),
    ], total=len(rows))


async def _t_boards(ctx: ToolContext, **kw) -> str:
    btype = (kw.get("board_type") or "hangye").strip()
    if btype not in ("hangye", "gainian"):
        return f"参数不合法：board_type 只接受 hangye/gainian，收到 {btype!r}"
    rows = list((await ctx.provider.get_board_rankings(btype)) or [])
    return _fmt_rows(f"板块排行 {btype}", rows, [
        ("board_name", ""), ("change_pct", "涨幅"),
        ("turnover_rate", "换手"), ("leader_name", "领涨"),
    ], total=len(rows))


async def _t_board_flow(ctx: ToolContext, **kw) -> str:
    """板块主力资金排行（P1-3）：助手可答「今天资金在堆哪个方向」。

    口径 = 东财官方板块 f62 主力净额（`board_flow.get_board_fund_flow`，与市场页
    「资金」tab 同一入口）。板块/个股/新浪口径**不混算**（数据源纪律）；取不到
    如实说明，绝不编造数值。
    """
    kind = (kw.get("kind") or "concept").strip() or "concept"
    rng = (kw.get("range") or "intraday").strip() or "intraday"
    if kind not in ("concept", "industry"):
        return f"工具参数错误：kind 只支持 concept|industry，收到 {kind!r}"
    if rng not in ("intraday", "5d", "10d"):
        return f"工具参数错误：range 只支持 intraday|5d|10d，收到 {rng!r}"
    from app.market.board_flow import get_board_fund_flow

    try:
        payload = await get_board_fund_flow(kind, rng)
    except Exception as exc:  # noqa: BLE001  取不到如实说，不编造
        log.warning("board_flow tool failed: %s", exc)
        return "板块资金流取数失败（上游不可达）——请如实说明取不到，不要编造数值"
    rows = (payload or {}).get("rows") or []
    if not payload or not payload.get("available") or not rows:
        return f"板块资金流暂不可用（kind={kind} range={rng}）——如实说明无数据即可"
    label = {"concept": "概念板块", "industry": "行业板块"}[kind]
    span = {"intraday": "当日", "5d": "近 5 日", "10d": "近 10 日"}[rng]
    lines = [f"【{label}主力净额排行（{span}，东财 f62 口径，Top10）】"]
    for r in rows[:10]:
        net = r.get("main_net_yi")
        pct = r.get("change_pct")
        extra = ""
        if rng == "intraday":
            streak = r.get("streak")
            if streak:
                extra += f"｜连续 {streak} 日净流入"
            delta = r.get("rank_delta")
            if delta:
                extra += f"｜榜位{'↑' if delta > 0 else '↓'}{abs(delta)}"
        lines.append(
            f"- {r.get('name', '—')}：净额 "
            f"{f'{net:.2f} 亿' if net is not None else '--'}"
            f"｜涨幅 {f'{pct:+.2f}%' if pct is not None else '--'}{extra}"
        )
    deg = payload.get("degraded") or []
    if deg:
        lines.append(f"- 口径说明：{'；'.join(deg)}")
    lines.append(f"- 数据时间 {payload.get('updated_at') or '—'}；供参考，不构成买卖建议")
    return _clip("\n".join(lines))


async def _t_commodity(ctx: ToolContext, **kw) -> str:
    """大宗商品异动 → 板块传导线索（P1-33，2026-09-11）。

    ⚠️ **输出里必须保留「时点结构」声明**（`commodity_chain.TIMING_NOTE`）：
    实测隔夜口径不成立（分组差 t 多在 ±2 内、原油为负），商品**无领先性**。
    助手引用本工具时**不得**把它转述成「明天某板块会涨」（红线 3）。
    """
    from app.market.commodity_chain import collect as _collect_commodity

    try:
        payload = await _collect_commodity()
    except Exception as exc:  # noqa: BLE001  取不到如实说，不编造
        log.warning("commodity tool failed: %s", exc)
        return "大宗商品行情取数失败（上游不可达）——请如实说明取不到，不要编造数值"
    if not payload:
        return "大宗商品行情暂不可用（全部来源不可达）——如实说明无数据即可"

    kw_raw = (kw.get("keyword") or "").strip()
    rows = payload.get("rows") or []
    if kw_raw:
        hit = [r for r in rows if kw_raw in r.get("label", "") or kw_raw in r.get("sw_name", "")]
        if not hit:
            names = "、".join(r.get("label", "") for r in rows)
            return f"未找到匹配「{kw_raw}」的商品/行业（可选：{names}）"
        rows = hit

    lines = ["【大宗商品异动 → 板块传导线索（一阶价格，新浪主连）】"]
    for r in rows:
        if r.get("skip_reason"):
            lines.append(f"- {r['label']}（{r['sw_name']}）：{r['skip_reason']}")
            continue
        head = (f"- {r['label']}（{r['sw_name']}）{r['date']}："
                f"{r['change']:+.2f}%（{r['zone']}，死区 ±{r['dead_zone']}%）")
        # ⚠️ mid_signal 与 mid_edge 必须**同时**成立才可进入格式化分支：
        #    只判 mid_signal 会让「半填充行」把整条工具崩成"参数不合法"（误导性错误，
        #    实测由 test_commodity_tool_always_carries_timing_note 逼出）。
        edge, edge_t, edge_n = r.get("mid_edge"), r.get("mid_t"), r.get("mid_n")
        if r.get("mid_signal") and edge is not None:
            head += (f"｜**中期线索**：历史上该异动对应 {r['sw_name']} 中期"
                     f"（约 5 日）超额 {edge:+.3f}pp（t={edge_t}，n={edge_n}）")
        elif r.get("mid_verified"):
            head += "｜中期线索：本次无异动（死区内）"
        else:
            head += "｜中期线索：该链未通过实证，不给板块含义"
        lines.append(head)

    lines.append(f"- 时点结构（重要）：{payload.get('timing_note')}")
    lines.append(f"- 数据时间 {payload.get('as_of') or '—'}；{payload.get('disclaimer')}")
    return _clip("\n".join(lines))


async def _t_kline(ctx: ToolContext, **kw) -> str:
    """个股 K 线（日线/分钟线）区间摘要 + 最近若干根明细。"""
    code, e = _one_symbol(ctx, kw.get("symbol") or kw.get("symbols") or "")
    if e:
        return f"参数不合法：{e}"
    tf = (kw.get("timeframe") or "1d").strip().lower()
    if tf not in TIMEFRAMES:
        return f"参数不合法：timeframe 只接受 {'/'.join(TIMEFRAMES)}，收到 {tf!r}"
    limit = _int_arg(kw.get("limit"), 10, 3, 30)
    bars = [_rec(b) for b in (await ctx.provider.get_kline(code, tf)) or []]
    if not bars:
        return f"{code} 无 {tf} K 线数据（新股/停牌/数据源缺，或该周期无数据）"
    rows = bars[-limit:]
    closes = [r.get("close") for r in bars if r.get("close") is not None]
    span = None
    if len(closes) >= 2 and closes[0]:
        span = (closes[-1] / closes[0] - 1) * 100
    src = rows[-1].get("source") or "—"
    lines = [f"【{code} {tf} K线 最近 {len(rows)} 根（本次共取 {len(bars)} 根，来源 {src}）】"]
    lines.append(
        f"- 区间：{str(bars[0].get('ts'))[:16]} → {str(bars[-1].get('ts'))[:16]}"
        + (f"，区间涨跌 {span:+.2f}%（{closes[0]} → {closes[-1]}）" if span is not None else "")
    )
    for r in rows:
        pct = r.get("change_pct")
        vol = r.get("volume")
        lines.append(
            f"- {str(r.get('ts'))[:16]}：开 {r.get('open')} 高 {r.get('high')} "
            f"低 {r.get('low')} 收 {r.get('close')}"
            + (f"，涨跌 {pct:+.2f}%" if isinstance(pct, (int, float)) else "")
            + (f"，量 {vol / 1e4:.0f} 万股" if isinstance(vol, (int, float)) and vol else "")
        )
    return _clip("\n".join(lines))


async def _t_minute(ctx: ToolContext, **kw) -> str:
    """当日分时走势摘要——把 ~240 个分钟点压成结构化区间（开/高/低/振幅/关键时点）。"""
    code, e = _one_symbol(ctx, kw.get("symbol") or kw.get("symbols") or "")
    if e:
        return f"参数不合法：{e}"
    try:
        pts = [_rec(p) for p in (await ctx.provider.get_minute_line(code)) or []]
    except Exception as exc:  # noqa: BLE001
        return f"{code} 分时数据取数失败：{type(exc).__name__}: {exc}（可换 K 线工具或到工作台分时页签查看）"
    priced = [(str(p.get("ts")), p.get("price")) for p in pts if isinstance(p.get("price"), (int, float))]
    if not priced:
        return f"{code} 今日暂无分时数据（非交易日/停牌/数据源缺）"
    src = pts[0].get("source") or "—"
    hi = max(priced, key=lambda x: x[1])
    lo = min(priced, key=lambda x: x[1])
    open_p, last_p = priced[0][1], priced[-1][1]
    amp = (hi[1] - lo[1]) / lo[1] * 100 if lo[1] else None
    lines = [f"【{code} 当日分时摘要（来源 {src}，共 {len(priced)} 个分钟点）】"]
    lines.append(f"- 时间范围：{priced[0][0]} → {priced[-1][0]}")
    lines.append(
        f"- 开 {open_p}｜最高 {hi[1]}（{hi[0]}）｜最低 {lo[1]}（{lo[0]}）｜最新 {last_p}"
    )
    if amp is not None:
        lines.append(f"- 日内振幅 {amp:.2f}%（(最高−最低)/最低）")
    tail_n = min(15, len(priced))
    tail = priced[-tail_n:]
    if tail[0][1]:
        lines.append(
            f"- 最近 {tail_n} 分钟：{tail[0][1]} → {tail[-1][1]}"
            f"（{(tail[-1][1] / tail[0][1] - 1) * 100:+.2f}%）"
        )
    step = max(1, len(pts) // 8)
    lines.append(
        "- 走势采样：" + "、".join(f"{str(p.get('ts'))[-5:]} {p.get('price')}" for p in pts[::step])
    )
    lines.append("- 口径：分钟收盘价序列；分时点不含逐笔成交明细，指数分时无「均价」概念。")
    return _clip("\n".join(lines))


async def _t_capital_flow(ctx: ToolContext, **kw) -> str:
    """个股资金流（新浪 MoneyFlow 口径：主力 = 超大单 + 大单）。"""
    code, e = _one_symbol(ctx, kw.get("symbol") or kw.get("symbols") or "")
    if e:
        return f"参数不合法：{e}"
    days = _int_arg(kw.get("days"), 5, 3, 20)
    try:
        rows = [_rec(r) for r in (await ctx.provider.get_capital_flow(code, days)) or []]
    except Exception as exc:  # noqa: BLE001
        return f"{code} 资金流取数失败：{type(exc).__name__}: {exc}"
    if not rows:
        return f"{code} 无资金流数据（新浪 MoneyFlow 未覆盖该标的/停牌）"
    src = rows[0].get("source") or "—"
    lines = [
        f"【{code} 个股资金流 近 {len(rows)} 日（来源 {src}；"
        "主力净额 = 超大单 + 大单，按单笔金额划分属估算、非交易所披露）】"
    ]
    for r in rows[:10]:
        net = r.get("net_main")
        seg = (
            f"｜超大 {_fmt_yi(r.get('net_super'))}、大 {_fmt_yi(r.get('net_big'))}"
            f"、中 {_fmt_yi(r.get('net_mid'))}、小 {_fmt_yi(r.get('net_small'))}"
            if r.get("net_super") is not None
            else ""
        )
        lines.append(
            f"- {r.get('date')}：收盘 {r.get('close')}"
            f"｜涨跌 {r.get('change_pct')}%｜主力净额 {_fmt_yi(net)}{seg}"
        )
    streak = 0
    for r in rows:  # 由近及远
        if (r.get("net_main") or 0) > 0:
            streak += 1
        else:
            break
    lines.append(f"- 自最新交易日往前，连续主力净流入 {streak} 日")
    lines.append("- 口径边界：这是**个股**口径，与板块 f62 口径不可相加或互相替代。")
    return _clip("\n".join(lines))


async def _t_basics(ctx: ToolContext, **kw) -> str:
    """个股「基本面 + 消息面」一条通：公司资料 + 财务摘要 + 最近公告 + 相关新闻。

    合成一条而不是拆四条的理由：用户问「为什么跌/这家公司怎么样」时，
    资料/财务/公告/新闻几乎总是一起被需要，拆开只会吃掉宝贵的工具调用配额。
    """
    code, e = _one_symbol(ctx, kw.get("symbol") or kw.get("symbols") or "")
    if e:
        return f"参数不合法：{e}"
    lines: list[str] = [f"【{code} 基本面与消息面】"]
    missing: list[str] = []

    try:
        prof = _rec(await ctx.provider.get_company_profile(code))
        bits = [f"名称 {prof.get('name') or '—'}", f"行业 {prof.get('industry') or '—'}"]
        if prof.get("region"):
            bits.append(f"地区 {prof['region']}")
        lines.append("- 公司：" + "，".join(bits))
        if prof.get("main_business"):
            lines.append(f"- 主营：{str(prof['main_business'])[:120]}")
        concepts = (prof.get("board_groups") or {}).get("concept") or prof.get("boards") or []
        if concepts:
            lines.append("- 所属概念：" + "、".join(str(c) for c in concepts[:12]))
        if prof.get("core_themes"):
            lines.append("- 核心题材：" + "、".join(str(t) for t in prof["core_themes"][:5]))
    except Exception as exc:  # noqa: BLE001
        missing.append(f"公司资料（{type(exc).__name__}）")

    try:
        fin = [_rec(f) for f in (await ctx.provider.get_financials(code, 4)) or []]
        for f in fin[:4]:
            lines.append(
                f"- 财报报告期 {f.get('report_date')}（公告日 {f.get('notice_date') or '未知'}）："
                f"营收（东财口径） {_fmt_yi(f.get('revenue'))}"
                f"（同比 {f.get('revenue_yoy')}%）｜净利 {_fmt_yi(f.get('net_profit'))}"
                f"（同比 {f.get('profit_yoy')}%）｜毛利率 {f.get('gross_margin')}%"
                f"｜ROE {f.get('roe')}%｜EPS {f.get('eps')}"
            )
        if not fin:
            missing.append("财务（无数据）")
    except Exception as exc:  # noqa: BLE001
        missing.append(f"财务（{type(exc).__name__}）")

    try:
        anns = [_rec(a) for a in (await ctx.provider.get_announcements(code, 8)) or []]
        if anns:
            lines.append(f"- 最近公告 {len(anns)} 条（东财）：")
            for a in anns[:8]:
                lines.append(f"  · {a.get('date')}｜{a.get('type') or '—'}｜{str(a.get('title'))[:60]}")
        else:
            missing.append("公告（无数据）")
    except Exception as exc:  # noqa: BLE001
        missing.append(f"公告（{type(exc).__name__}）")

    try:
        news = [_rec(n) for n in (await ctx.provider.get_news(code, 5)) or []]
        if news:
            lines.append("- 相关新闻：")
            for n in news[:5]:
                lines.append(f"  · {n.get('date')}｜{str(n.get('title'))[:50]}")
    except Exception as exc:  # noqa: BLE001
        missing.append(f"新闻（{type(exc).__name__}）")

    if missing:
        lines.append(f"- 未取到：{'、'.join(missing)}（可到工作台个股详情查看或稍后重试）")
    lines.append("- 口径：东财 F10/业绩报表/公告接口；公告标题为原文，未改写。")
    return _clip("\n".join(lines))


async def _t_market_overview(ctx: ToolContext, **kw) -> str:
    """大盘概览：指数快照 + 涨跌家数宽度 + 两市成交额（与市场页「市场概览」同口径）。"""
    lines: list[str] = ["【大盘概览】"]
    hub = ctx.hub
    if hub is not None:
        try:
            idx = list(hub.get_indices() or [])
        except Exception as exc:  # noqa: BLE001
            idx = []
            log.info("market_overview indices: %s", exc)
        for q in idx[:8]:
            price = getattr(q, "price", None)
            pct = getattr(q, "change_pct", None)
            lines.append(
                f"- {getattr(q, 'name', '') or ''}（{getattr(q, 'symbol', '')}）："
                f"{price if price is not None else '—'}"
                f"｜涨跌 {f'{pct:+.2f}%' if isinstance(pct, (int, float)) else '—'}"
            )
    svc = ctx.snapshot_service
    if svc is not None:
        try:
            payload = svc.breadth_payload()
            b = payload.get("breadth") or {}
            age = payload.get("snapshot_age_seconds")
            lines.append(
                f"- 涨跌家数：涨 {b.get('up', '—')} / 跌 {b.get('down', '—')} / 平 {b.get('flat', '—')}"
                f"｜涨停 {b.get('limit_up', '—')} / 跌停 {b.get('limit_down', '—')}"
                f"｜覆盖 {b.get('total', '—')} 只"
            )
            lines.append(f"- 两市成交额合计 {_fmt_yi(b.get('total_amount'))}")
            if age is not None:
                lines.append(f"- 快照新鲜度：{age} 秒前（腾讯/新浪全市场快照口径，非逐笔）")
        except Exception as exc:  # noqa: BLE001
            log.info("market_overview breadth: %s", exc)
    if len(lines) == 1:
        return "大盘概览暂不可用（指数与全市场快照均未就绪）——如实说明取不到即可"
    lines.append("- 口径：指数为实时快照；涨跌家数/成交额来自全市场快照（含停牌统计口径）。")
    return _clip("\n".join(lines))


def _snapshot_map_sync(ctx: ToolContext, d: date) -> dict[str, dict]:
    """同步实现（供 asyncio.to_thread 调用，Parquet 读是阻塞调用）。"""
    svc = ctx.snapshot_service
    base = Path(getattr(svc, "parquet_dir", "")) / "snapshots" if svc is not None else None
    if base is None or not Path(base).exists():
        return {}
    from app.services.parquet_store import read_latest_in_dir

    want = d.strftime("%Y%m%d")
    day_dirs = sorted((p for p in Path(base).iterdir() if p.is_dir()), reverse=True)
    exact = base / want
    chosen = exact if exact.is_dir() and sorted(exact.glob("*.parquet")) else None
    if chosen is None:
        for day in day_dirs:
            if day.name <= want and sorted(day.glob("*.parquet")):
                chosen = day
                break
    if chosen is None:
        return {}
    read = read_latest_in_dir(chosen, columns=["symbol", "change_pct"])
    if not read.ok:
        return {}
    df = read.df
    return {
        str(s): {"change_pct": p}
        for s, p in zip(df["symbol"].to_list(), df["change_pct"].to_list())
    }
