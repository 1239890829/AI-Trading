"""基础设施：解析/校验/数据类/公共格式化（被各域共用，不含任何具体工具）。

`app/assistant/tools.py` 的内部切片（IMP-005，2026-09-15 从 1934 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app/assistant/tools/__init__.py` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable
from app.core.ttl_cache import TTLCache
from app.core.bjtime import beijing_today
# 切片说明（IMP-005）：原文件此处为 `logging.getLogger(__name__)`；搬进子模块后
# `__name__` 会变成 "app.assistant.tools.core" ⇒ 改为**硬编码原日志名**，
# 保持日志通道与拆分前完全一致（全仓无按 logger 名过滤的配置，实测确认）。
log = logging.getLogger("app.assistant.tools")


MAX_CALLS_PER_TURN = 4   # 单轮工具调用上限（2026-09-11：2 → 4）


MAX_TOOL_ROUNDS = 2      # 取数后可再取一轮（多跳追问，如先看异动再查公告），防死循环


MAX_ROWS = 20            # 单个工具最多渲染多少行


MAX_CHARS = 1500         # 单个工具输出字符上限（防撑爆提示词）


MAX_SYMBOLS = 6


TOOL_CACHE = TTLCache("assistant.tools", ttl=60.0, maxsize=64)


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


_TOOL_RE = re.compile(r"\{\{tool:([a-z_]+)((?:\|[^\{\}]*)*)\}\}")


_SYM_RE = re.compile(r"^\d{6}$")


_INDEX_RE = re.compile(r"^(sh|sz|bj)\d{6}$")


TIMEFRAMES: tuple[str, ...] = ("1d", "1w", "60m", "30m", "15m", "5m", "1m")


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
