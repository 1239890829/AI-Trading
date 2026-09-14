"""助手受限工具调用（P0-3，2026-09-06）。

## 为什么需要
此前助手"没有手"：只能引用注入提示词里那几只标的的快照，其余全靠模型背书 →
问"今天涨停池什么情况"只能编。竞品（问财/妙想）的分水岭就在工具调用。

## 为什么是"受限"
- **只读**：工具全部走既有 provider / 存储的读路径，没有任何写操作；
- **白名单**：工具名与参数枚举在 TOOL_SPECS 里写死，模型给不出表外的东西；
- **校验**：个股代码必须是 6 位且命中实体词典（字典为空时才放行纯格式），
  日期必须是 YYYY-MM-DD 且在交易日历内（日历不可用时才退化为格式校验）；
- **有界**：每轮调用次数上限、单次输出字符上限、结果 TTL 缓存（省配额）。

## 调用协议
模型在回答**开头单独一行**写 `{{tool:名称|参数=值|参数2=值2}}`，后端执行后把结果
作为 system 消息回填再让模型继续生成。标记行不会流给用户（assistant.py 会剥离）。

失败一律降级为一行"工具失败：<原因>"交给模型，绝不中断对话——工具是增强层。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.core.ttl_cache import TTLCache
from app.core.bjtime import beijing_now, beijing_today  # S2-8 时区收敛

log = logging.getLogger(__name__)

MAX_CALLS_PER_TURN = 4   # 单轮工具调用上限（2026-09-11：2 → 4）
MAX_TOOL_ROUNDS = 2      # 取数后可再取一轮（多跳追问，如先看异动再查公告），防死循环
MAX_ROWS = 20            # 单个工具最多渲染多少行
MAX_CHARS = 1500         # 单个工具输出字符上限（防撑爆提示词）
MAX_SYMBOLS = 6

TOOL_CACHE = TTLCache("assistant.tools", ttl=60.0, maxsize=64)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TOOL_RE = re.compile(r"\{\{tool:([a-z_]+)((?:\|[^\{\}]*)*)\}\}")
_SYM_RE = re.compile(r"^\d{6}$")
# 指数必须带市场前缀（项目纪律：裸代码=股票，指数须写 sh000001），
# 与 QuoteHub.get_quotes 的兜底规则一致。
_INDEX_RE = re.compile(r"^(sh|sz|bj)\d{6}$")

TIMEFRAMES: tuple[str, ...] = ("1d", "1w", "60m", "30m", "15m", "5m", "1m")


# ---------------------------------------------------------------- 解析

@dataclass
class ToolCall:
    name: str
    args: dict[str, str] = field(default_factory=dict)


def parse_tool_calls(text: str) -> list[ToolCall]:
    """从模型输出里抽工具调用；非法/空参数名丢弃（不抛异常）。"""
    out: list[ToolCall] = []
    for m in _TOOL_RE.finditer(text or ""):
        name = m.group(1)
        args: dict[str, str] = {}
        for seg in m.group(2).split("|"):
            if not seg.strip():
                continue
            if "=" not in seg:
                continue
            k, v = seg.split("=", 1)
            k = k.strip()
            if k:
                args[k] = v.strip()
        out.append(ToolCall(name=name, args=args))
    return out


def strip_tool_calls(text: str) -> str:
    """剥离已完整出现的工具标记（流式输出前调用）。

    刻意**不 strip 空白**：流式按块调用时，块尾空格是正文的一部分（"今日涨停 " + "38 家"），
    每次都 strip 会吞掉词间空格。要不要去掉首尾空行由调用方决定。
    """
    return _TOOL_RE.sub("", text or "")


def has_partial_tool_call(text: str) -> bool:
    """尾部是否像是没打完的标记——是就先按住不 flush，避免半个 `{{tool:` 漏给用户。

    判据刻意放宽到"出现过 `{{` 但还没闭合"：流式是逐字符来的，第一块可能只有 `{{`，
    只认 `{{tool:` 完整前缀会先把 `{{` 吐出去（2026-09-06 实测踩到）。
    """
    t = text or ""
    i = t.rfind("{{")
    if i < 0:
        return False
    return "}}" not in t[i:]


# ---------------------------------------------------------------- 校验

def _valid_symbols(
    raw: str,
    known: set[str] | None,
    max_symbols: int = MAX_SYMBOLS,
    allow_index: bool = False,
) -> tuple[list[str], str | None]:
    """解析标的列表。

    `allow_index=True` 时额外接受带市场前缀的指数（sh000001），且**不做实体词典校验**
    ——指数不在股票词典里，用它去卡会把「问大盘」直接判成非法参数。
    """
    parts = [p.strip() for p in re.split(r"[,，、;\s]+", raw or "") if p.strip()]
    if not parts:
        return [], "symbols 为空"
    if len(parts) > max_symbols:
        parts = parts[:max_symbols]
    codes: list[str] = []
    for p in parts:
        if allow_index and _INDEX_RE.match(p.strip().lower()):
            codes.append(p.strip().lower())
            continue
        # 允许带市场后缀（600519.SH / SH600519），只取 6 位数字
        digits = re.sub(r"\D", "", p)
        if not _SYM_RE.match(digits):
            return [], f"非法的标的代码：{p}"
        if known and digits not in known:
            return [], f"标的不在实体词典内：{p}"
        codes.append(digits)
    return codes, None


def _valid_date(raw: str | None, trading_days: set[str] | None) -> tuple[str | None, str | None]:
    """返回 (YYYY-MM-DD 字符串, 错误)。空值 = 用最近交易日（由调用方解析）。"""
    if not raw:
        return None, None
    d = raw.strip()
    if not _DATE_RE.match(d):
        return None, f"日期格式应为 YYYY-MM-DD，收到 {raw!r}"
    try:
        parsed = datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        return None, f"日期不合法：{raw!r}"
    if parsed > beijing_today():
        return None, f"日期不能晚于今天：{d}"
    if trading_days and d not in trading_days:
        return None, f"{d} 不是交易日"
    return d, None


# ---------------------------------------------------------------- 工具定义

@dataclass
class ToolSpec:
    name: str
    desc: str                       # 给提示词的一行说明
    params: str                     # 参数说明（提示词用）
    handler: Callable[..., Awaitable[str]]


def _rec(r: Any) -> dict[str, Any]:
    """pydantic / dict 统一成 dict。"""
    if isinstance(r, dict):
        return r
    dump = getattr(r, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:  # noqa: BLE001
            pass
    return dict(getattr(r, "__dict__", {}) or {})


def _fmt_rows(title: str, rows: list[Any], cols: list[tuple[str, str]], total: int | None = None) -> str:
    """通用表格渲染：cols = [(字段名, 标题)]，缺失值渲染 —。"""
    lines = [f"【{title}】"]
    for r in rows[:MAX_ROWS]:
        d = _rec(r)
        cells = []
        for k, label in cols:
            v = d.get(k)
            cells.append(f"{label} {v}" if v is not None else f"{label} —")
        lines.append("- " + "，".join(cells))
    if total is not None and total > len(rows[:MAX_ROWS]):
        lines.append(f"（共 {total} 条，已截断至 {MAX_ROWS} 条）")
    if len(lines) == 1:
        lines.append("- 无数据")
    return _clip("\n".join(lines))


def _clip(s: str) -> str:
    if len(s) <= MAX_CHARS:
        return s
    return s[:MAX_CHARS] + f"…（已截断，原 {len(s)} 字）"


@dataclass
class ToolContext:
    """工具执行上下文（只读依赖）。"""
    provider: Any                    # composite provider
    session_factory: Any = None      # 复盘报告读取
    known_symbols: set[str] = field(default_factory=set)   # 实体词典代码集
    trading_days: set[str] = field(default_factory=set)     # 交易日集合（字符串）
    hub: Any = None                  # QuoteHub：指数快照（市场概览工具）
    snapshot_service: Any = None     # 全市场快照服务：涨跌家数/成交额（市场概览工具）
    paper_engine: Any = None         # 模拟交易引擎（模拟账户工具）
    event_store: Any = None          # 事件库：全网快讯/资讯流（news 工具）
    theme_catalog: Any = None        # 官方题材目录服务：题材成分明细（themes 工具）


def _latest_trade_day(ctx: ToolContext) -> date:
    """最近**已确认**交易日：优先用交易日历，拿不到就退周末回退规则（与 /api/limit-up 同口径）。

    绝不直接用 date.today() 当默认——周六周日拿它去查池子只会得到空结果，
    而模型会照着"没有数据"回答用户（2026-09-06 周日实测：今天=周日 → 必须退到周五）。

    ⚠️ **本函数不做「今天是否交易日」的判定，只取最近一个已确认的交易日**——
    这两件事的语义不同，别拿它替代 `tc.is_trade_day_on(d) -> True/False/None`：
      * 返回值是 `date`（契约如此），**无法表达 unknown**；
      * 日历**未覆盖今天**（限频/双源失败时 `trading_days()` 刻意返回旧快照）时，
        结果会**回退到上一交易日**。这**不等于**"今天休市"，只是"没有更晚的已确认交易日"。
    输入来源已收口：`ctx.trading_days` 取自 `tc.trading_days(provider)`（freshness 为
    「覆盖今天」优先，见 F2），异常时退化为 `set()` 走下面的正向兜底 ⇒ **不会把未知判成休市**。
    """
    if ctx.trading_days:
        today = beijing_today().isoformat()
        past = sorted(d for d in ctx.trading_days if d <= today)
        if past:
            return datetime.strptime(past[-1], "%Y-%m-%d").date()
    d = beijing_today()
    return {5: d - timedelta(days=1), 6: d - timedelta(days=2)}.get(d.weekday(), d)


def _resolve_date(ctx: ToolContext, raw: str | None) -> tuple[date, str | None]:
    """解析日期；**非法就报错，绝不静默回退到最近交易日**——
    否则模型以为自己查的是 9/5，实际拿到 9/4 的数据还照说不误（比报错更危险）。"""
    d, e = _valid_date(raw, ctx.trading_days or None)
    if e:
        return _latest_trade_day(ctx), e
    if d:
        return datetime.strptime(d, "%Y-%m-%d").date(), None
    return _latest_trade_day(ctx), None


# ---- handlers ------------------------------------------------------------

def _fmt_yi(v: Any, unit: str = "元") -> str:
    """金额自适应单位（与前端 lib/format.ts::fmtAmount 同口径）。None → —。"""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f} 亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.0f} 万"
    return f"{v:.0f}{unit}" if unit else f"{v:.0f}"


def _int_arg(raw: Any, default: int, lo: int, hi: int) -> int:
    """工具参数取整并夹到 [lo, hi]；脏值退回默认值（工具是增强层，不为参数报错）。"""
    try:
        v = int(str(raw).strip()) if raw not in (None, "") else default
    except (TypeError, ValueError):
        v = default
    return max(lo, min(v, hi))


def _one_symbol(ctx: ToolContext, raw: str, field: str = "symbol") -> tuple[str | None, str | None]:
    codes, e = _valid_symbols(raw, ctx.known_symbols or None, max_symbols=1)
    if e:
        return None, f"{field} 不合法：{e}"
    return codes[0], None


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
    rows = list((await ctx.provider.get_limit_down_pool(d)) or [])
    return _fmt_rows(f"跌停池 {d}", rows, [
        ("name", ""), ("symbol", ""), ("change_pct", "跌幅"),
        ("consecutive_limit_down_days", "连跌天"),
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
    """逐笔成交（P2-28① 批量登记）。"""
    codes, e = _valid_symbols(kw.get("symbols", ""), ctx.known_symbols or None)
    if e:
        return f"参数不合法：{e}"
    rows = list((await ctx.provider.get_trades(codes[0])) or [])
    if not rows:
        return f"逐笔成交：{codes[0]} 取不到（数据源未提供逐笔）"
    return _fmt_rows(f"逐笔成交 {codes[0]}", rows[:30], [
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


async def _t_param_changes(ctx: ToolContext, **kw) -> str:
    """参数变更单（审计留痕，P2-28① 批量）。

    白名单之外不可改；每次应用/回滚都有留痕。取不到时说明，不编造变更记录。
    """
    if ctx.session_factory is None:
        return "参数变更：无数据源（session_factory 未提供）"
    try:
        from sqlalchemy import select

        from app.models.agent import AgentParamChange

        with ctx.session_factory() as db:
            rows = db.execute(
                select(AgentParamChange).order_by(AgentParamChange.id.desc()).limit(20)
            ).scalars().all()
            recs = [{"id": r.id, "key": r.key, "before": r.before, "after": r.after,
                     "status": r.status,
                     "at": r.created_at.isoformat() if r.created_at else ""} for r in rows]
    except Exception as exc:  # noqa: BLE001
        return f"参数变更：读取失败（{exc}）"
    if not recs:
        return "参数变更：暂无变更记录"
    return _fmt_rows("参数变更（最近 20 条）", recs, [
        ("id", ""), ("key", "参数"), ("before", "原值"), ("after", "新值"),
        ("status", "状态"), ("at", "时间"),
    ], total=len(recs))


async def _t_agent_tasks(ctx: ToolContext, **kw) -> str:
    """任务中心：最近任务的类型/状态/风险等级（P2-28① 批量）。"""
    if ctx.session_factory is None:
        return "任务中心：无数据源（session_factory 未提供）"
    try:
        from sqlalchemy import select

        from app.models.agent import AgentTask

        with ctx.session_factory() as db:
            rows = db.execute(
                select(AgentTask).order_by(AgentTask.id.desc()).limit(20)
            ).scalars().all()
            recs = [{"id": r.id, "type": r.type, "status": r.status,
                     "risk_level": r.risk_level or "",
                     "at": r.created_at.isoformat() if r.created_at else ""} for r in rows]
    except Exception as exc:  # noqa: BLE001
        return f"任务中心：读取失败（{exc}）"
    if not recs:
        return "任务中心：暂无任务"
    return _fmt_rows("任务中心（最近 20 条）", recs, [
        ("id", ""), ("type", "类型"), ("status", "状态"), ("risk_level", "风险"), ("at", "时间"),
    ], total=len(recs))


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


async def _t_alert_events(ctx: ToolContext, **kw) -> str:
    """预警触发记录（P2-28① 清单外补登记）。

    数据源是 `alert_event` 表，走 `AlertRepository.list_events`（不自己拼 SQL——
    仓库层已封装口径）。

    ⚠️ `triggered_at` 是**北京时间**（该列用 `beijing_now_naive`），与 agent 域的
    UTC naive **不同**（见结转表 #6）——直接展示即可，**不得再 +8h**。
    """
    if ctx.session_factory is None:
        return "预警记录：无数据源（session_factory 未提供）"
    try:
        limit = int(kw.get("limit") or 20)
    except (TypeError, ValueError):
        return "参数不合法：limit 必须是整数"
    limit = max(1, min(limit, 50))
    sym = (kw.get("symbol") or "").strip() or None

    try:
        from app.repositories.alert_repo import AlertRepository

        # 2026-09-12 实测判定**不搬线程**：`AlertRepository.list_events(limit≤50)` 中位 **0.36ms**
        # （对照 `EventStore.list_events` 7.2~85ms）⇒ 毫秒级，与 `ensure_system_rule` 同族。
        events = AlertRepository(ctx.session_factory).list_events(limit=limit)
    except Exception as exc:  # noqa: BLE001
        return f"预警记录：读取失败（{exc}）"

    rows = [
        {"id": e.id, "rule_id": e.rule_id, "symbol": e.symbol,
         "trigger_value": getattr(e, "trigger_value", None),
         "threshold": getattr(e, "threshold", None),
         "at": e.triggered_at.isoformat() if getattr(e, "triggered_at", None) else ""}
        for e in (events or [])
        if not sym or str(getattr(e, "symbol", "")) == sym
    ]
    if not rows:
        return f"预警记录：暂无触发记录（symbol={sym or '全部'}）"
    return _fmt_rows(
        f"预警触发记录（{sym or '全部'}，最近 {len(rows)} 条 · 时间为北京时间）",
        rows,
        [("id", ""), ("symbol", ""), ("trigger_value", "触发值"), ("threshold", "阈值"),
         ("at", "时间")],
        total=len(rows),
    )


async def _t_boards(ctx: ToolContext, **kw) -> str:
    btype = (kw.get("board_type") or "hangye").strip()
    if btype not in ("hangye", "gainian"):
        return f"参数不合法：board_type 只接受 hangye/gainian，收到 {btype!r}"
    rows = list((await ctx.provider.get_board_rankings(btype)) or [])
    return _fmt_rows(f"板块排行 {btype}", rows, [
        ("board_name", ""), ("change_pct", "涨幅"),
        ("turnover_rate", "换手"), ("leader_name", "领涨"),
    ], total=len(rows))


async def _t_hot(ctx: ToolContext, **kw) -> str:
    period = (kw.get("period") or "day").strip()
    if period not in ("day", "week", "month"):
        return f"参数不合法：period 只接受 day/week/month，收到 {period!r}"
    rows = list((await ctx.provider.get_hot_stock_list(period)) or [])
    return _fmt_rows(f"热股榜 {period}", rows, [
        ("name", ""), ("symbol", ""), ("rank", "排名"),
        ("change_pct", "涨幅"), ("hot_value", "热度"),
    ], total=len(rows))


async def _t_review(ctx: ToolContext, **kw) -> str:
    from app.review.storage import get_report

    d, e = _resolve_date(ctx, kw.get("date"))
    if e:
        return f"参数不合法：{e}"
    if ctx.session_factory is None:
        return "工具不可用：未配置数据库会话"
    rep = get_report(ctx.session_factory, d.isoformat())
    if rep is None:
        return f"{d} 没有复盘报告"
    summary = getattr(rep, "summary", None) or {}
    lines = [f"【复盘报告 {d}】"]
    if isinstance(summary, dict):
        for k in ("headline", "market_summary", "conclusion"):
            if summary.get(k):
                lines.append(f"- {k}：{summary[k]}")
    items = list(getattr(rep, "action_items", None) or [])
    if items:
        lines.append(f"- 改进项 {len(items)} 条：")
        for it in items[:10]:
            dd = _rec(it)
            lines.append(f"  · [{dd.get('status', '—')}] {dd.get('title', '')[:40]}")
    return _clip("\n".join(lines))


async def _t_brief(ctx: ToolContext, **kw) -> str:
    from app.picks.morning_brief import brief_for_today

    target, payload = brief_for_today()
    if payload is None:
        return f"{target} 尚无盘前简报"
    lines = [f"【盘前简报 {target}】"]
    for d in (payload.get("directions") or [])[:5]:
        lines.append(
            f"- {d.get('direction', '')}｜{d.get('entry_mode', '—')}"
            f"｜触发：{'; '.join((d.get('trigger_conditions') or [])[:2]) or '—'}"
        )
    lines.append(f"- 提醒：{len(payload.get('alerts') or [])} 条")
    return _clip("\n".join(lines))


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


async def _t_sentiment(ctx: ToolContext, **kw) -> str:
    """近几日情绪相位（AI 大脑 P1：助手读得到市场温度）。"""
    if ctx.session_factory is None:
        return "工具不可用：未配置数据库会话"
    from app.market.sentiment_history import get_history

    import asyncio as _asyncio

    rows = await _asyncio.to_thread(get_history, ctx.session_factory, 5)
    if not rows:
        return "尚无情绪历史存档"
    lines = ["【近 5 日情绪相位】"]
    for r in rows[:5]:
        d = _rec(r)
        lines.append(
            f"- {d.get('trade_date', '—')}：{d.get('phase', '—')}"
            f"｜温度 {d.get('temperature') if d.get('temperature') is not None else '—'}"
            f"｜置信 {d.get('confidence') or '—'}"
            f"{'（相位不可靠）' if d.get('phase_unreliable') else ''}"
        )
    return _clip("\n".join(lines))


async def _t_events(ctx: ToolContext, **kw) -> str:
    """今日 watcher 异动/确认/证伪事件（AI 大脑 P1：助手读得到盘中事件流）。"""
    if ctx.session_factory is None:
        return "工具不可用：未配置数据库会话"
    from datetime import datetime

    from sqlalchemy import select

    from app.models.alert import AlertEvent, AlertRule
    from app.picks.watcher import WATCHER_RULE_NAME
    def _q():
        with ctx.session_factory() as db:  # type: ignore[misc]
            rule_ids = db.execute(
                select(AlertRule.id).where(AlertRule.name == WATCHER_RULE_NAME)
            ).scalars().all()
            if not rule_ids:
                return []
            rows = db.execute(
                select(AlertEvent)
                .where(AlertEvent.rule_id.in_(rule_ids))
                .order_by(AlertEvent.id.desc())
                .limit(40)
            ).scalars().all()
            return [
                {"triggered_at": str(r.triggered_at), "snapshot": r.snapshot}
                for r in rows
            ]

    import asyncio as _asyncio
    import json as _json

    rows = await _asyncio.to_thread(_q)
    today = beijing_now().strftime("%Y-%m-%d")
    today_rows = []
    for r in rows:
        # triggered_at 已是**北京 naive**（2026-09-09 口径统一）→ 直接比对，
        # 不再 +8h（旧注释「UTC naive」是统一前的残留，双重偏移会让 16:00
        # 之后的告警落到次日而被过滤掉）。
        try:
            dt = datetime.fromisoformat(r["triggered_at"])
        except Exception:  # noqa: BLE001
            continue
        if dt.strftime("%Y-%m-%d") == today:
            today_rows.append(r)
    if not today_rows:
        return "今日暂无 watcher 事件（含确认/证伪/大单异动）"
    lines = [f"【今日 watcher 事件 {len(today_rows)} 条】"]
    for r in today_rows[:10]:
        snap = r["snapshot"]
        if isinstance(snap, str):
            try:
                snap = _json.loads(snap)
            except Exception:  # noqa: BLE001
                snap = {}
        lines.append(f"- {snap.get('text') or snap.get('kind') or '（无文本）'}")
    return _clip("\n".join(lines))


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


async def _t_climate(ctx: ToolContext, **kw) -> str:
    """气候一阶相位（ENSO/ONI）→ 板块前瞻线索（P1-32，2026-09-11）。

    ⚠️ **必须同时输出两段声明**：①`timing_note`（ONI 滞后、无领先性）
    ②`empirical_verdict`（人工传导链**未获数据支持**，基础化工方向甚至相反）。
    少任何一段，助手都可能把它转述成「厄尔尼诺来了，买化肥」（红线 3）。
    """
    from app.market.climate import collect as _collect_climate

    try:
        payload = await _collect_climate()
    except Exception as exc:  # noqa: BLE001  取不到如实说，不编造
        log.warning("climate tool failed: %s", exc)
        return "气候指数（NOAA ONI）取数失败——请如实说明取不到，不要编造相位"
    if not payload:
        return "气候指数暂不可用（NOAA ONI 不可达）——如实说明无数据即可"

    lines = ["【气候一阶相位（NOAA ONI，重叠三月季）】"]
    state = payload.get("state")
    note = payload.get("unjudged_reason")
    if state is None:
        lines.append(f"- 本次未判定：{note or '数据不足'}")
    else:
        label = {"el_nino": "厄尔尼诺", "la_nina": "拉尼娜", "neutral": "中性"}.get(state, state)
        strength = payload.get("strength")
        band = {"weak": "弱", "moderate": "中等", "strong": "强", "very_strong": "超强"}
        seg = f"（{band.get(strength, strength)}）" if strength else ""
        alert = payload.get("alert") or ""
        lines.append(
            f"- 当前相位：**{label}**{seg}；同向连续 {payload.get('consecutive')} 个季"
            + (f"；行业上 ONI 阈值 ±{payload.get('threshold')}，"
               f"连续 {payload.get('persist_seasons')} 季才算「确立」" if alert else "")
        )
        if alert:
            lines.append(
                f"- **预警态**：已连续越线 {payload.get('consecutive')} 季但未满 "
                f"{payload.get('persist_seasons')} 季 ⇒ 尚未构成官方「{alert} 确立」"
            )
        latest = payload.get("latest") or {}
        if latest:
            lines.append(
                f"- 最新季：{latest.get('season')} {latest.get('year')}"
                f"（季末 {latest.get('end_date')}）ANOM={latest.get('anom'):+.2f}"
            )
        series = payload.get("series") or []
        if series:
            lines.append(
                "- 近几季：" + "、".join(f"{s['season']} {s['anom']:+.2f}" for s in series)
            )
        links = payload.get("candidate_links") or []
        if links:
            lines.append(
                "- 候选题材（**人工映射，未获数据支持，仅线索**）："
                + "、".join(f"{r['target']}（强度{r['strength']}）" for r in links)
            )

    lines.append(f"- 时点结构（重要）：{payload.get('timing_note')}")
    lines.append(f"- 实证判读（重要）：{payload.get('empirical_verdict')}")
    lines.append(f"- 数据时间 {payload.get('as_of') or '—'}；{payload.get('disclaimer')}")
    return _clip("\n".join(lines))


# ---- 扩展工具（2026-09-11）：把「系统已有、助手取不到」的数据资产接上 --------
# 背景：用户实测「明明系统里都有的数据，助手答『我没有』」。逐项核对后是两类问题：
# ① 提示词自我否认（见 prompt.py / context.py 的同日修复）；
# ② **工具覆盖缺口**——后端端点早就存在，助手侧没登记（分时/K线/资金流/个股龙虎榜/
#    公告财务/指数宽度/题材梯队/自选/模拟账户）。下列工具一律走与页面**同一个
#    provider 读路径**，口径与页面一致，不含任何二次加工。


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
                f"- 财报 {f.get('report_date')}：营收 {_fmt_yi(f.get('revenue'))}"
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


async def _t_news(ctx: ToolContext, **kw) -> str:
    """全网资讯/快讯事件流（热点消息 + 利好利空方向映射）。

    2026-09-11 补：用户实测助手答「我今天没有全网新闻/资讯类的实时数据源，无法直接列
    今日热点新闻」——**这是错的**，系统有完整的新闻事件管道（`app/news/flash.py` 双域
    快讯采集 → `EventStore` → `GET /api/events*`，即市场页「事件面板」的数据源），
    只是助手侧没登记工具。与个股消息面（公告/新闻标题）不同，这里给的是**全市场**视角。

    ⚠️ 输出必须保留方向行的 `basis`（依据）——事件→题材的方向映射是**系统推断**，
    不是官方结论（红线 3：只给关联 + 依据 + 失效条件，不得转述成买卖建议）。
    """
    store = ctx.event_store
    if store is None:
        return "资讯事件流不可用：事件库未初始化（如实说明取不到即可，不要编造新闻）"
    limit = _int_arg(kw.get("limit"), 10, 3, 30)
    try:
        rows = list(await asyncio.to_thread(store.list_events, active_only=True, limit=limit) or [])
    except Exception as exc:  # noqa: BLE001
        return f"资讯事件流读取失败：{type(exc).__name__}: {exc}"
    if not rows:
        return "当前无活跃资讯事件（非交易时段/快讯采集未产出，属正常空态）"

    lines = [f"【活跃资讯事件 {len(rows)} 条（来源：系统快讯事件流，与市场页事件面板同源）】"]
    for r in rows:
        rec = _rec(r)
        when = str(rec.get("published_at") or "")[:16]
        lines.append(
            f"- [{rec.get('source') or '—'}｜{when}] {str(rec.get('title') or '')[:60]}"
        )
        summary = rec.get("summary")
        if summary:
            lines.append(f"  · 摘要：{str(summary)[:80]}")
        dirs = rec.get("directions") or []
        for d in dirs[:3]:
            dd = _rec(d)
            lines.append(
                f"  · 方向推断：{dd.get('target') or '—'}"
                f"（{dd.get('direction') or '—'}，强度 {dd.get('strength') or '—'}）"
                f"｜依据：{str(dd.get('basis') or '—')[:50]}"
            )
    lines.append(
        "- 口径：事件与方向映射由系统规则/LLM 从公开快讯抽取，"
        "**方向是推断不是官方结论**，只作线索；不构成买卖建议。"
    )
    return _clip("\n".join(lines))


async def _t_chain(ctx: ToolContext, **kw) -> str:  # noqa: ARG001 — 纯函数工具，不需要 ctx
    """事件→板块传导链检索（P2-5，`chain|keyword=`）。

    需求形态的澄清（账本 P2-5 曾把两件事混为一件）：
    - `events|hot` **已无必要**——「热榜」由 `hot` 工具覆盖 ⇒ 该子项销账；
    - `chain|keyword` 是**真的缺**：`chains.py` 原有的是 `match_chains(title)`——
      **给定一条新闻标题、返回它命中哪些链**（正向，抽取时用）；而需求是
      **给定一个关键词、返回相关链**（反向索引，检索时用）。两者语义不同，
      故新增 `find_chains()` 而非改 `match_chains`。

    三条纪律（测试锁住）：
    1. **必须随结论给口径**——本表是**人工维护的映射索引，不是已验证的行情规律**；
       其中强度/弹性数字源自单日单案例观察（KB-DEC-019 反固化条款）。只报
       "厄尔尼诺→化肥" 而不带这句，模型会把它当选股依据讲给用户。
    2. **方向不固定的链不给方向**——非农/利率的 direction 取决于事件正文里的
       意外差/主导矛盾，这里无从判定，如实说"待判"，绝不填个默认值。
    3. 未命中时给**触发词示例**帮模型换个说法，而不是只说"没找到"。
    """
    from app.events.chains import chain_keywords, find_chains

    keyword = (kw.get("keyword") or "").strip()
    if not keyword:
        return ("参数缺失：keyword（事件关键词，如 厄尔尼诺 / 非农 / 加息 / OpenAI）。"
                f"当前支持的触发词：{'、'.join(chain_keywords())}")

    hits = find_chains(keyword)
    if not hits:
        return (f"未找到与「{keyword}」相关的传导链。传导链按**触发词**建索引，"
                f"当前支持的触发词：{'、'.join(chain_keywords())}")

    lines = [f"【传导链检索：{keyword}】命中 {len(hits)} 条"]
    for g in hits:
        lines.append(f"## {g['label']}（命中触发词「{g['matched_keyword']}」）")
        if g["targets"]:
            for t in g["targets"]:
                lines.append(f"- {t['target']}（类型 {t['target_type']}，"
                             f"方向 {'+' if t['direction'] > 0 else ''}{t['direction']}，"
                             f"强度 {t['strength']}）")
        else:
            lines.append("- 该链的目标板块随事件正文动态确定，此处不预置")
        lines.append(f"- 方向口径：{g['direction_note']}")
    lines.append(
        "- ⚠️ 口径：本表是**人工维护的映射索引（关键词 → 题材），不是已验证的行情规律**；"
        "强度/弹性数字源自单日单案例观察、**未经数据验证**，只作线索与可解释性参考，"
        "不得当结论引用、不得据此跨题材外推。"
    )
    lines.append("- ⚠️ 不构成买卖建议。")
    return _clip("\n".join(lines))


# ---- 回测（P2-28① 收尾，2026-09-12）--------------------------------------
#
# 为什么是「执行型」而不是「读现成结果」：回测结果在生产侧**本来就不持久化**
# ——`POST /api/backtest/run` 是同步计算、无结果表（见 `routes/backtest.py` 模块头），
# 所以「读现成结果」这条路根本不存在；而新增结果表属**写库 + schema 变更**
# （需确认），故这里走**现算、不留存**（与 `minute_decisions` 的「惰性结算」同思路：
# 不改变既有写路径）。
#
# 耗时已实测（2026-09-12，真实数据 600519 / 500 根）：冷启 **0.90s**（含 500 根日K
# 网络取数）／热缓存 **0.004s** ⇒ 远低于助手工具可接受量级；TTL 缓存（60s）由
# 执行层统一套用，同一问题重复问不会重复算。
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


TOOL_SPECS: dict[str, ToolSpec] = {
    # P2-5（2026-09-12）：事件→板块传导链的**反向检索**（关键词 → 链）。
    # 与 events 工具的分工：events 给「发生了什么」，本工具给「它可能传到哪些板块」。
    "chain": ToolSpec(
        "chain",
        "事件→板块传导链检索（给关键词，返回关联的链条与目标板块；"
        "映射索引非已验证规律，强度值未经数据验证）",
        "keyword=事件关键词（如 厄尔尼诺 / 非农 / 加息 / OpenAI）",
        _t_chain,
    ),
    "quotes": ToolSpec("quotes", "批量实时行情快照（指数需带前缀，如 sh000001）", "symbols=600519,000001（≤6 只）", _t_quotes),
    "limit_up": ToolSpec("limit_up", "某交易日涨停池", "date=YYYY-MM-DD（可省略=最近交易日）", _t_limit_up),
    "limit_down": ToolSpec("limit_down", "某交易日跌停池", "date=YYYY-MM-DD（可省略）", _t_limit_down),
    "limit_break": ToolSpec("limit_break", "某交易日炸板池", "date=YYYY-MM-DD（可省略）", _t_limit_break),
    "longhu": ToolSpec("longhu", "龙虎榜：当日全市场榜；给 symbols 则查个股席位明细", "date=YYYY-MM-DD（可省略）｜symbols=600519（可选，个股维度）", _t_longhu),
    # P2-28①（2026-09-11）：题材成分明细——此前只有「题材梯队」没有「成分」，
    # 助手被问「某题材有哪些票」时只能凭印象作答。
    "theme_members": ToolSpec("theme_members", "官方题材成分明细（给题材名，返回成分股列表）",
                              "keyword=题材名（如 代糖 / 创新药）", _t_theme_members),
    # P2-28① 批量（2026-09-11）：盘口 / 逐笔 / 集合竞价——三个都是 provider 直连，
    # 一次性登记，避免逐个加、每次都动一遍能力清单守卫（KB-ENG-49）。
    "orderbook": ToolSpec("orderbook", "盘口五档（L1，非 L2）",
                          "symbols=单只代码", _t_orderbook),
    "trades": ToolSpec("trades", "逐笔成交明细",
                       "symbols=单只代码", _t_trades),
    "auction": ToolSpec("auction", "集合竞价快照（仅 ths 一源，取不到属正常）",
                        "symbols=逗号分隔代码（≤5）｜stage=final（默认）", _t_auction),
    # P2-28① 第三批（2026-09-11）：因子档案 / 参数变更单 / 任务中心——
    # 均用既有数据源（factors.report 纯函数 + 已注入的 session_factory），
    # 一次登记，避免逐个动能力清单守卫。
    "factor_profile": ToolSpec("factor_profile",
                               "因子档案：本地全历史 IC/ICIR 评估（样本内，未做样本外验证）",
                               "无参数", _t_factor_profile),
    "param_changes": ToolSpec("param_changes", "参数变更单（审计留痕，最近 20 条）",
                              "无参数", _t_param_changes),
    "agent_tasks": ToolSpec("agent_tasks", "任务中心：最近任务与状态（最近 20 条）",
                            "无参数", _t_agent_tasks),
    # P2-28① 最后一项（2026-09-11）：做T决策库。惰性结算与 /api/market/minute-decisions 同口径。
    "minute_decisions": ToolSpec("minute_decisions",
                                 "做T决策库（记录→结算→错误归因；open=未到结算窗口，非失败）",
                                 "symbol=可选，按标的代码过滤｜limit=条数（1~50，默认 20）",
                                 _t_minute_decisions),
    # P2-28① 清单外补登记（2026-09-11）：预警触发记录。走 AlertRepository，不自己拼 SQL。
    "alert_events": ToolSpec("alert_events", "预警触发记录（时间为北京时间）",
                             "symbol=可选｜limit=条数（1~50，默认 20）", _t_alert_events),
    "boards": ToolSpec("boards", "板块排行榜", "board_type=hangye|gainian（默认 hangye）", _t_boards),
    "hot": ToolSpec("hot", "人气热股榜", "period=day|week|month（默认 day）", _t_hot),
    "anomaly": ToolSpec("anomaly", "当日异动原因（可按代码查为什么异动）", "symbols=可选，逗号分隔≤6只；缺省=全市场榜", _t_anomaly),
    "review": ToolSpec("review", "某交易日复盘报告要点", "date=YYYY-MM-DD（可省略）", _t_review),
    "brief": ToolSpec("brief", "今日盘前简报", "无参数", _t_brief),
    # AI 大脑 P1 扩容（2026-09-08）：持仓/精选/情绪/事件——系统数据资产对助手开放
    "picks": ToolSpec("picks", "最近一次每日精选组合（含置信档）", "无参数", _t_picks),
    "positions": ToolSpec("positions", "当前持仓与浮动盈亏", "无参数", _t_positions),
    "sentiment": ToolSpec("sentiment", "近 5 日情绪相位", "无参数", _t_sentiment),
    "events": ToolSpec("events", "今日 watcher 异动/确认/证伪事件（自选池监控）", "无参数", _t_events),
    "news": ToolSpec(
        "news", "全网资讯/快讯事件流（今日热点消息 + 利好利空方向推断）",
        "limit=10（3~30）",
        _t_news,
    ),
    # P1-3（2026-09-10）：板块资金流——回答「今天资金在堆哪个方向」
    # 参数说明刻意不用 `|`（那是工具调用的分段符，写进去会被解析成无 `=` 的段而丢弃）
    "board_flow": ToolSpec(
        "board_flow", "板块主力净额排行（东财 f62 口径）",
        "kind=concept（默认）｜range=intraday（默认）｜可选值 industry / 5d / 10d",
        _t_board_flow,
    ),
    # P1-33（2026-09-11）：大宗商品一阶价格 → 板块传导线索（实测标定，无隔夜领先性）
    # 参数说明同样刻意不用 `|`（工具调用分段符）
    "commodity": ToolSpec(
        "commodity", "大宗商品异动与板块传导线索（含「无领先性」时点声明）",
        "keyword=可选，按商品名或行业名过滤（如 原油 / 钢铁 / 有色）",
        _t_commodity,
    ),
    # P1-32（2026-09-11）：气候一阶相位（ENSO/ONI）——把「厄尔尼诺」从新闻关键词
    # 升级为一阶指数；输出强制携带「滞后无领先性」+「人工链未获支持」双声明
    "climate": ToolSpec(
        "climate", "气候相位（厄尔尼诺/拉尼娜/中性）与候选传导链（含实证判读声明）",
        "无参数",
        _t_climate,
    ),
    # ---- 个股与大盘数据面扩容（2026-09-11，用户实测「系统明明有、助手说没有」）----
    "kline": ToolSpec(
        "kline", "个股 K 线（日线/分钟线）区间涨跌与最近若干根明细",
        "symbol=600519｜timeframe=1d（默认），可选 1w/60m/30m/15m/5m/1m｜limit=10（3~30）",
        _t_kline,
    ),
    "minute": ToolSpec(
        "minute", "当日分时走势摘要（开/高/低/振幅/关键时点/末段变化）",
        "symbol=600519",
        _t_minute,
    ),
    "capital_flow": ToolSpec(
        "capital_flow", "个股资金流（主力=超大单+大单，连续净流入天数）",
        "symbol=600519｜days=5（3~20）",
        _t_capital_flow,
    ),
    "basics": ToolSpec(
        "basics", "个股基本面与消息面：公司资料 + 财务摘要 + 最近公告 + 相关新闻",
        "symbol=600519",
        _t_basics,
    ),
    "market_overview": ToolSpec(
        "market_overview", "大盘概览：指数快照 + 涨跌家数宽度 + 两市成交额",
        "无参数",
        _t_market_overview,
    ),
    "themes": ToolSpec(
        "themes", "题材梯队看板（强度/阶段/健康度，涨停池按题材重组）",
        "date=YYYY-MM-DD（可省略）｜limit=10（3~30）",
        _t_themes,
    ),
    "watchlist": ToolSpec(
        "watchlist", "自选股清单与实时行情（按分组）",
        "无参数",
        _t_watchlist,
    ),
    "paper": ToolSpec(
        "paper", "模拟交易账户：资产汇总与持仓浮盈（只读）",
        "无参数",
        _t_paper,
    ),
    # P2-28① 收尾（2026-09-12）：回测。生产侧**无结果表可读**（端点同步计算、不持久化），
    # 故登记为「现算不留存」的执行型工具；耗时实测 0.9s 冷启，明细见 `_t_backtest` 头注。
    "backtest": ToolSpec(
        "backtest",
        "单标的日线策略回测（历史统计事实·样本内·未做参数优化，不构成买卖建议）",
        "symbol=单只代码（6 位）｜strategy=ma_cross/ma_breakout｜"
        "bars=回看日K根数（100~500，默认 250）",
        _t_backtest,
    ),
}


# 工具名 → 中文短标签（"正在取数：龙虎榜" 这类进度提示与工具回执用）。
# 与 TOOL_SPECS 的键集由 tests/test_assistant.py 守卫，防新增工具漏标签。
TOOL_LABELS: dict[str, str] = {
    "chain": "传导链",
    "quotes": "实时行情",
    "limit_up": "涨停池",
    "limit_down": "跌停池",
    "limit_break": "炸板池",
    "longhu": "龙虎榜",
    "theme_members": "题材成分",
    "orderbook": "盘口",
    "trades": "逐笔",
    "auction": "集合竞价",
    "factor_profile": "因子档案",
    "param_changes": "参数变更",
    "agent_tasks": "任务中心",
    "minute_decisions": "做T决策",
    "alert_events": "预警记录",
    "boards": "板块排行",
    "hot": "人气热榜",
    "anomaly": "异动原因",
    "review": "复盘报告",
    "brief": "盘前简报",
    "picks": "每日精选",
    "positions": "持仓",
    "sentiment": "情绪相位",
    "events": "盘中事件",
    "news": "资讯快讯",
    "board_flow": "板块资金流",
    "commodity": "大宗商品",
    "climate": "气候相位",
    "kline": "K线",
    "minute": "分时走势",
    "capital_flow": "个股资金流",
    "basics": "公司资料与公告",
    "market_overview": "大盘概览",
    "themes": "题材梯队",
    "watchlist": "自选股",
    "paper": "模拟账户",
    "backtest": "策略回测",
}


def tool_label(name: str) -> str:
    """工具名 → 中文短标签（进度提示与回执展示用；前端不再自己维护一份映射）。"""
    return TOOL_LABELS.get(name) or name


def tool_manifest() -> str:
    """给提示词的工具清单（只读 + 受限，明确边界）。"""
    lines = [
        "## 可用工具（只读，受限）",
        "需要真实数据时，在回答**开头单独一行**写 {{tool:名称|参数=值}}，",
        "系统会取数后把结果回填给你，你再继续回答（标记行不会显示给用户）。",
        f"可以先取一批数、看过结果**再取第二批**（最多 {MAX_TOOL_ROUNDS} 轮，"
        f"每轮最多 {MAX_CALLS_PER_TURN} 次）；不在下表的名称/参数会被拒绝；取不到就如实说没有，绝不编造。",
        "**常见误判**：下表覆盖了行情/K线/分时/资金流/龙虎榜/公告财务/资讯快讯/"
        "指数宽度/题材/精选/持仓/事件等绝大部分数据需求——**先取数，再下结论**，"
        "不要凭印象回答「我没有这项数据」。",
    ]
    for spec in TOOL_SPECS.values():
        lines.append(f"- {{{{tool:{spec.name}|{spec.params}}}}} → {spec.desc}")
    return "\n".join(lines)


async def run_tool(call: ToolCall, ctx: ToolContext, cache: TTLCache | None = TOOL_CACHE) -> str:
    """执行单个工具调用，返回给模型看的文本；任何异常都降级为一行说明。"""
    spec = TOOL_SPECS.get(call.name)
    if spec is None:
        return f"【{call.name}】工具失败：未登记的工具名（可用：{'、'.join(TOOL_SPECS)}）"

    key = (call.name, tuple(sorted(call.args.items())))
    if cache is not None:
        hit, val = cache.get(key)
        if hit:
            return f"{val}\n（命中缓存，可能非最新）"

    try:
        text = await spec.handler(ctx, **call.args)
    except TypeError as exc:
        return f"【{call.name}】工具失败：参数不合法（{exc}）"
    except Exception as exc:  # noqa: BLE001  工具是增强层，失败只降级
        log.warning("assistant tool %s failed: %s", call.name, exc)
        return f"【{call.name}】工具失败：{type(exc).__name__}: {exc}"

    if cache is not None:
        cache.set(key, text)
    return text


async def run_tool_calls(
    calls: list[ToolCall], ctx: ToolContext, cache: TTLCache | None = TOOL_CACHE
) -> tuple[str, list[str]]:
    """批量执行（受 MAX_CALLS_PER_TURN 限制），返回 (回填文本, 调用名列表)。"""
    used: list[str] = []
    blocks: list[str] = []
    for c in calls[:MAX_CALLS_PER_TURN]:
        blocks.append(await run_tool(c, ctx, cache))
        used.append(c.name)
    if len(calls) > MAX_CALLS_PER_TURN:
        blocks.append(f"（本轮工具调用上限 {MAX_CALLS_PER_TURN} 次，其余 {len(calls) - MAX_CALLS_PER_TURN} 次已忽略）")
    return "\n\n".join(blocks), used
