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

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable

from app.core.ttl_cache import TTLCache

log = logging.getLogger(__name__)

MAX_CALLS_PER_TURN = 2
MAX_ROWS = 20          # 单个工具最多渲染多少行
MAX_CHARS = 1500       # 单个工具输出字符上限（防撑爆提示词）
MAX_SYMBOLS = 6

TOOL_CACHE = TTLCache("assistant.tools", ttl=60.0, maxsize=64)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TOOL_RE = re.compile(r"\{\{tool:([a-z_]+)((?:\|[^\{\}]*)*)\}\}")
_SYM_RE = re.compile(r"^\d{6}$")


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

def _valid_symbols(raw: str, known: set[str] | None) -> tuple[list[str], str | None]:
    parts = [p.strip() for p in re.split(r"[,，、;\s]+", raw or "") if p.strip()]
    if not parts:
        return [], "symbols 为空"
    if len(parts) > MAX_SYMBOLS:
        parts = parts[:MAX_SYMBOLS]
    codes: list[str] = []
    for p in parts:
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
    if parsed > date.today():
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


def _latest_trade_day(ctx: ToolContext) -> date:
    """最近交易日：优先用交易日历，拿不到就退周末回退规则（与 /api/limit-up 同口径）。

    绝不直接用 date.today() 当默认——周六周日拿它去查池子只会得到空结果，
    而模型会照着"没有数据"回答用户（2026-09-06 周日实测：今天=周日 → 必须退到周五）。
    """
    if ctx.trading_days:
        today = date.today().isoformat()
        past = sorted(d for d in ctx.trading_days if d <= today)
        if past:
            return datetime.strptime(past[-1], "%Y-%m-%d").date()
    d = date.today()
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

async def _t_quotes(ctx: ToolContext, **kw) -> str:
    raw = kw.get("symbols", "")
    codes, e = _valid_symbols(raw, ctx.known_symbols or None)
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
    d, e = _resolve_date(ctx, kw.get("date"))
    if e:
        return f"参数不合法：{e}"
    rows = list((await ctx.provider.get_longhu_records(d)) or [])
    return _fmt_rows(f"龙虎榜 {d}", rows, [
        ("name", ""), ("symbol", ""), ("net_buy", "净买"),
        ("change_pct", "涨幅"), ("reason", "原因"),
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


TOOL_SPECS: dict[str, ToolSpec] = {
    "quotes": ToolSpec("quotes", "批量实时行情快照", "symbols=600519,000001（≤6 只）", _t_quotes),
    "limit_up": ToolSpec("limit_up", "某交易日涨停池", "date=YYYY-MM-DD（可省略=最近交易日）", _t_limit_up),
    "limit_down": ToolSpec("limit_down", "某交易日跌停池", "date=YYYY-MM-DD（可省略）", _t_limit_down),
    "limit_break": ToolSpec("limit_break", "某交易日炸板池", "date=YYYY-MM-DD（可省略）", _t_limit_break),
    "longhu": ToolSpec("longhu", "某交易日龙虎榜", "date=YYYY-MM-DD（可省略）", _t_longhu),
    "boards": ToolSpec("boards", "板块排行榜", "board_type=hangye|gainian（默认 hangye）", _t_boards),
    "hot": ToolSpec("hot", "人气热股榜", "period=day|week|month（默认 day）", _t_hot),
    "anomaly": ToolSpec("anomaly", "当日异动原因（可按代码查为什么异动）", "symbols=可选，逗号分隔≤6只；缺省=全市场榜", _t_anomaly),
    "review": ToolSpec("review", "某交易日复盘报告要点", "date=YYYY-MM-DD（可省略）", _t_review),
    "brief": ToolSpec("brief", "今日盘前简报", "无参数", _t_brief),
}


def tool_manifest() -> str:
    """给提示词的工具清单（只读 + 受限，明确边界）。"""
    lines = [
        "## 可用工具（只读，受限）",
        "需要真实数据时，在回答**开头单独一行**写 {{tool:名称|参数=值}}，",
        "系统会取数后把结果回填给你，你再继续回答（标记行不会显示给用户）。",
        "约束：每轮最多 2 次；不在下表的名称/参数会被拒绝；取不到就如实说没有，绝不编造。",
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
