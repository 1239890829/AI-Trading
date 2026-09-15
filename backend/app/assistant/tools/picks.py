"""选股工具：每日精选、盘中决策、因子画像、异动、回测。

`app/assistant/tools.py` 的内部切片（IMP-005，2026-09-15 从 1934 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app/assistant/tools/__init__.py` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from .core import (
    MAX_ROWS,
    ToolContext,
    _clip,
    _fmt_rows,
    _int_arg,
    _one_symbol,
)
async def _t_factor_profile(ctx: ToolContext, **kw) -> str:
    """因子档案：本地全历史 IC/ICIR 评估结论（P2-28① 批量）。

    ⚠️ 口径必须随结论一起给出：**样本内结论，未做样本外验证**，不得直接当选股权重。
    产物缺失/超期时如实说明（三态），不凭印象说"某因子有效"。
    """
    try:
        from app.factors.report import ic_evidence
    except Exception as exc:  # noqa: BLE001
        return f"因子档案：读取失败（{exc}）"
    try:
        ev = ic_evidence(limit=8)
    except Exception as exc:  # noqa: BLE001
        return f"因子档案：评估产物读取失败（{exc}）"

    if not ev.get("available"):
        return f"因子档案：当前无可用评估产物（{ev.get('reason') or '尚未生成'}）"

    parts = []
    if ev.get("stale"):
        parts.append(f"⚠️ 产物已超期（{ev.get('age_days')} 天 > {ev.get('max_age_days')}），结论可能过时")
    counts = ev.get("counts") or {}
    parts.append(
        f"因子评估：PASS {counts.get('pass', 0)} / 观察 {counts.get('conditional', 0)} "
        f"/ 未过 {counts.get('fail', 0)}"
    )
    for f in (ev.get("top") or [])[:8]:
        direction = {1: "正向", -1: "反向"}.get(f.get("direction"), "方向未定")
        parts.append(
            f"· {f.get('name')}（{f.get('category')}）T+{f.get('horizon')} "
            f"IC {f.get('ic_mean')} ICIR {f.get('icir')} {direction} · {f.get('verdict')}"
        )
    if ev.get("caveat"):
        parts.append(f"口径：{ev['caveat']}")
    return "\n".join(parts)


async def _t_minute_decisions(ctx: ToolContext, **kw) -> str:
    """做 T 决策库（记录 → 结算 → 错误归因），P2-28① 最后一项。

    与 `/api/market/minute-decisions` 同口径：读时**惰性结算**到期的 open 记录
    （12:00 后才结算，盘中到期的那部分也能算）。

    纪律：**outcome=open 表示"还没到结算窗口"，不是失败**——
    展示时必须给出各 outcome 的计数，避免模型把一堆 open 读成"决策全错"。
    """
    if ctx.session_factory is None:
        return "做T决策：无数据源（session_factory 未提供）"
    symbol = (kw.get("symbol") or "").strip() or None
    try:
        limit = int(kw.get("limit") or 20)
    except (TypeError, ValueError):
        return "参数不合法：limit 必须是整数"
    limit = max(1, min(limit, 50))

    try:
        from app.core.bjtime import beijing_now
        from app.market import minute_decisions as md

        # 结算是同步阻塞（查 K 线）⇒ 丢到线程，别卡住事件循环
        if beijing_now().hour >= 12:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(md.settle_due, ctx.session_factory,
                                       md.tdx_points, symbol)
        items = await asyncio.to_thread(md.list_decisions, ctx.session_factory,
                                       symbol, limit)
    except Exception as exc:  # noqa: BLE001
        return f"做T决策：读取失败（{exc}）"

    if not items:
        return f"做T决策：暂无记录（symbol={symbol or '全部'}）"

    counts: dict[str, int] = {}
    for it in items:
        k = it.get("outcome") or "open"
        counts[k] = counts.get(k, 0) + 1
    head = "、".join(f"{k} {v}" for k, v in sorted(counts.items()))
    body = _fmt_rows(
        f"做T决策（{symbol or '全部'}，最近 {len(items)} 条 · {head}）",
        items,
        [("symbol", ""), ("trigger_ts", "触发"), ("bias", "方向"),
         ("signal_price", "信号价"), ("realized_spread_pct", "已实现%"),
         ("outcome", "结果")],
        total=len(items),
    )
    return body + "\n注：outcome=open 表示尚未到结算窗口，不是失败。"


async def _t_anomaly(ctx: ToolContext, **kw) -> str:
    """当日异动原因（ths 独占，today-only）：全市场榜或按代码查「为什么异动」。"""
    syms_raw = (kw.get("symbols") or "").strip()
    try:
        if syms_raw:
            symbols = [s.strip() for s in syms_raw.split(",") if s.strip()][:6]
            records = list((await ctx.provider.get_anomaly_stock(symbols)) or [])
            title = f"异动原因（{','.join(symbols)}）"
        else:
            records = list((await ctx.provider.get_anomaly_list(None)) or [])
            title = "当日全市场异动（前 30 条）"
            records = records[:30]
    except Exception as exc:  # 数据源失败显式带出，不静默
        return f"异动数据暂不可用：{exc}"
    if not records:
        return f"{title}：当日无匹配异动记录（today-only，非交易日/未产生异动属正常）"
    return _fmt_rows(title, records, [
        ("name", ""), ("symbol", ""), ("tag", "标签"),
        ("analysis", "原因"), ("keywords", "关键词"),
    ], total=len(records))


async def _t_picks(ctx: ToolContext, **kw) -> str:
    """最近一次每日精选组合（AI 大脑 P1 工具扩容：让助手读得到选股结论）。"""
    if ctx.session_factory is None:
        return "工具不可用：未配置数据库会话"
    from sqlalchemy import select

    from app.models.daily_pick import DailyPickSet

    def _q():
        with ctx.session_factory() as db:  # type: ignore[misc]
            return db.execute(
                select(DailyPickSet).order_by(DailyPickSet.id.desc()).limit(1)
            ).scalars().first()

    import asyncio as _asyncio

    row = await _asyncio.to_thread(_q)
    if row is None:
        return "尚无每日精选组合"
    import json as _json

    items = _json.loads(row.items or "[]")
    meta = _json.loads(row.meta or "{}")
    lines = [f"【每日精选 {row.date}】相位 {meta.get('market_phase', '—')}｜gate {meta.get('gate', '—')}"]
    for it in items[:MAX_ROWS]:
        conf = it.get("confidence")
        conf_s = f"｜置信 {conf}" if conf else ""
        obs = ""
        if it.get("follow_state") == "followable":
            obs = "｜可跟（闸门日：不给买入范围，参与须经影子持仓验证）"
        elif it.get("observation_only"):
            obs = "｜仅观察"
        lines.append(
            f"- {it.get('name', '')}({it.get('symbol', '')})：score {it.get('score', '—')}"
            f"｜{it.get('theme') or '无题材'}{conf_s}{obs}"
            f"｜止损 {it.get('stop_loss', '—')}"
        )
    return _clip("\n".join(lines))


async def _t_backtest(ctx: ToolContext, **kw) -> str:
    """单标的日线策略回测（与 /api/backtest/run **同引擎、同默认成本**）。

    ⚠️ 三条口径必须随结论一起给出，否则模型会把「历史样本内表现」读成
    「这只票能赚钱」——本项目最危险的误读之一：
    ① 结果是**历史统计事实**，不构成买卖建议；
    ② 是**样本内**表现，未做样本外验证、未做参数优化；
    ③ 成本用**代码默认**（未套用 mandate 文件）——生产若配了 mandate，数字会有差异。
    """
    code, e = _one_symbol(ctx, kw.get("symbol") or kw.get("symbols") or "")
    if e:
        return f"参数不合法：{e}"

    from app.market.backtest import STRATEGY_REGISTRY, build_strategy, run_backtest

    sid = (kw.get("strategy") or kw.get("strategy_id") or "").strip()
    if not sid:
        opts = "、".join(
            f"{k}（{STRATEGY_REGISTRY[k]['name']}）" for k in sorted(STRATEGY_REGISTRY)
        )
        return f"参数不合法：strategy 缺失。可选策略：{opts}"
    if sid not in STRATEGY_REGISTRY:
        return (f"参数不合法：strategy 只接受 "
                f"{'/'.join(sorted(STRATEGY_REGISTRY))}，收到 {sid!r}")

    n_bars = _int_arg(kw.get("bars"), 250, 100, 500)

    from app.market.mandate import resolve_backtest_request

    try:
        r = resolve_backtest_request(
            symbol=code, strategy_id=sid, params=None, bars=n_bars, mandate_name=None,
        )
        strategy = build_strategy(r.strategy_id, r.params)
    except ValueError as exc:
        return f"参数不合法：{exc}"

    def _compute() -> tuple[Any, str | None]:
        """同步实现（供 asyncio.to_thread 调用；日K取数 + 回测都是阻塞调用）。"""
        from app.market.tdx_kline import tdx_daily_bars

        bars = tdx_daily_bars(r.symbol, count=r.bars)
        if not bars or len(bars) < 60:
            return None, (f"{r.symbol} 日K数据不足（拿到 {len(bars) if bars else 0} 根，"
                          f"回测至少需 60 根）——新股 / 长期停牌 / 数据源缺都可能")
        return run_backtest(bars, strategy, r.config), None

    try:
        report, err = await asyncio.to_thread(_compute)
    except Exception as exc:  # noqa: BLE001
        return f"回测执行失败：{exc}"
    if err:
        return err

    def _pct(v: Any, sign: bool = True) -> str:
        """收益率带符号（涨跌方向有意义）；**比率类不带**——
        「最大回撤 +16.90%」会被读成"涨了 16.9%"，方向恰好反了（本工具首版实测踩到）。"""
        try:
            n = float(v) * 100
        except (TypeError, ValueError):
            return "—"
        return f"{n:+.2f}%" if sign else f"{n:.2f}%"

    extra = report.extra_metrics or {}
    n_span = len(report.equity_ts)
    span = f"{str(report.equity_ts[0])[:10]} → {str(report.equity_ts[-1])[:10]}" if n_span else "—"
    strat_name = STRATEGY_REGISTRY[r.strategy_id]["name"]
    raw_hold = extra.get("avg_holding_bars")
    hold = f"{float(raw_hold):.2f}" if isinstance(raw_hold, (int, float)) else "—"
    lines = [
        f"【{r.symbol} 日线回测 · {r.strategy_id}（{strat_name}）"
        f"· {n_span} 根日K（{span}）】",
        f"- 区间收益 {_pct(report.total_return)}｜买入持有 {_pct(report.benchmark_return)}"
        f"｜超额 {_pct(report.excess_return)}",
        f"- 年化 {_pct(report.annual_return)}｜最大回撤 {_pct(report.max_drawdown, sign=False)}"
        f"（{report.max_drawdown_days} 个交易日）",
        f"- 夏普 {report.sharpe:.2f}｜索提诺 {report.sortino:.2f}｜卡玛 {report.calmar:.2f}",
        f"- 成交 {int(extra.get('n_trades') or len(report.trades))} 次"
        f"｜胜率 {_pct(report.win_rate, sign=False)}｜盈亏比 {report.profit_loss_ratio:.2f}"
        f"｜平均持有 {hold} 交易日",
    ]
    if report.in_return or report.out_return:
        lines.append(
            f"- 样本内外分离：样本内 {_pct(report.in_return)}｜样本外 {_pct(report.out_return)}"
        )
    lines.append(
        f"- ⚠️ 口径：**历史统计事实（样本内），不构成买卖建议**；策略参数取注册表默认值"
        f"（{r.params}），**未做参数优化与样本外验证**；成本为代码默认"
        f"（佣金 {r.config.commission_rate:.5f} / 印花税 {r.config.stamp_tax:.4f} / "
        f"滑点 {r.config.slippage_bp:g}bp / 一字涨停拒买·跌停拒卖 / T+1）；"
        f"样本仅 {n_span} 根日K，区间越短结论越不稳，**不得据此外推为选股依据**。"
    )
    return _clip("\n".join(lines))
