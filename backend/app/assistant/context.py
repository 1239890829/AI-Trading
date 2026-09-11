"""实时行情快照注入（best-effort 增强层）。

把用户消息/页面上下文中提到的标的（代码或股票名）解析出来，批量拉
实时快照，格式化成「实时数据快照」块拼进系统提示——让助手能回答
"XX 现在多少钱"这类问题，同时**只许引用快照内的数字**。

设计纪律：
- 解析不到任何标的 → 完全跳过（不发行情请求，提示词恢复"无实时数据"语义）；
- 行情拉取失败 → 降级为空块并记日志，绝不拖垮聊天主链路；
- 标的上限 6 只：防一条消息塞一堆代码把 prompt 撑爆。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.assistant.prompt import PageContext
from app.schemas.market import Quote

log = logging.getLogger(__name__)

MAX_SYMBOLS = 6

_TOKEN_RE_CACHE: dict[int, re.Pattern[str]] = {}


def _token_regex(stocks: list[dict[str, str]]) -> re.Pattern[str]:
    """股票名 alternation + 6 位代码。按 stocks 对象 id 缓存（entity-dict 本身有 TTL）。"""
    cached = _TOKEN_RE_CACHE.get(id(stocks))
    if cached is not None:
        return cached
    names = sorted((s["name"] for s in stocks if len(s["name"]) >= 2), key=len, reverse=True)
    escaped = "|".join(re.escape(n) for n in names)
    re_obj = re.compile(rf"(?<![0-9A-Za-z])(?:[0-9]{{6}}|{escaped})(?![0-9A-Za-z])")
    _TOKEN_RE_CACHE.clear()  # 只保留最新一份（entity-dict 刷新后旧正则作废）
    _TOKEN_RE_CACHE[id(stocks)] = re_obj
    return re_obj


def resolve_symbols(
    text: str, page: PageContext | None, stocks: list[dict[str, str]]
) -> list[str]:
    """从消息文本解析标的代码；页面上下文标的优先。去重保序，≤6。

    - 6 位数字必须存在于字典（code 集合）才算个股——金额/手数/日期不误判；
    - 股票名经字典映射为代码。
    """
    code_set = {s["code"] for s in stocks}
    name_to_code = {s["name"]: s["code"] for s in stocks}
    out: list[str] = []
    if page and page.symbol.strip().isdigit() and page.symbol.strip() in code_set:
        out.append(page.symbol.strip())
    if text:
        regex = _token_regex(stocks)
        for m in regex.finditer(text):
            token = m[0]
            code = token if token.isdigit() and token in code_set else name_to_code.get(token)
            if code and code not in out:
                out.append(code)
            if len(out) >= MAX_SYMBOLS:
                break
    return out[:MAX_SYMBOLS]


def _fmt_num(v: float | None, suffix: str = "") -> str:
    return "—" if v is None else f"{v:g}{suffix}"


def _as_of(q: Quote) -> str:
    """数据本身的时间（行情时间戳优先，没有就用接收时间）。北京时间 HH:MM。"""
    dt = q.data_timestamp or q.received_at
    if dt is None:
        return "未知"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%H:%M")


def format_quote_line(q: Quote) -> str:
    """单只标的一行紧凑行情。字段缺失显示 —，绝不臆造。

    2026-09-06 溯源要求（P0-4）：行尾必带「来源 / 数据时间 / 口径」三件套——
    - 来源=行情 provider（tencent/sina/…），数据时间=行情自身时间戳；
    - 质量非 high 时补 ⚠ 标记与原因，模型**不许**拿降级数据下确定性结论。
    """
    amount = ""
    if q.amount is not None:
        amount = f"，成交额 {q.amount / 1e8:.2f} 亿" if q.amount >= 1e8 else f"，成交额 {q.amount / 1e4:.0f} 万"
    line = (
        f"- {q.name or ''}（{q.symbol}）：现价 {_fmt_num(q.price)}，"
        f"涨跌幅 {_fmt_num(q.change_pct, '%')}，今开 {_fmt_num(q.open)}，"
        f"最高 {_fmt_num(q.high)}，最低 {_fmt_num(q.low)}，昨收 {_fmt_num(q.prev_close)}{amount}"
    )
    line += f"｜来源 {q.source or '未知'}，数据时间 {_as_of(q)}，口径 实时快照"
    if q.quality is not None and getattr(q.quality, "value", q.quality) != "high":
        reasons = "；".join(q.quality_reasons or [])
        line += f"｜⚠ 数据质量 {getattr(q.quality, 'value', q.quality)}"
        if reasons:
            line += f"（{reasons}）"
        line += "：可陈述数值，但不要据此下确定性结论"
    return line


def quote_sources(quotes: list[Quote]) -> list[dict[str, str]]:
    """快照溯源清单：给 SSE meta 事件与前端脚注消费。

    每项 {symbol,name,source,as_of}，as_of 为行情自身时间（北京时间 HH:MM）。
    与提示词里的快照行同源，保证"回答里的数字"和"页面脚注"说的是同一份数据。
    """
    out: list[dict[str, str]] = []
    for q in quotes:
        out.append({
            "symbol": q.symbol,
            "name": q.name or "",
            "source": q.source or "未知",
            "as_of": _as_of(q),
        })
    return out


async def build_market_context(
    hub_provider_get_quotes, codes: list[str], tools_enabled: bool = False
) -> tuple[str, list[dict[str, str]]]:
    """拉实时快照 →（提示词块, 溯源清单）；无标的/失败返回 ("", [])。

    `hub_provider_get_quotes` 为注入的批量取价协程（测试替换用），
    生产传 `lambda syms: _batch_quotes(hub, syms)`。

    `tools_enabled` 决定块头怎么写（2026-09-11 修复）：此前的措辞是
    "快照之外的实时信息（资金流/龙虎榜/其他个股等）你都没有"——它写死了无工具
    时代的事实，接入工具后变成**与工具清单直接冲突的指令**，模型据此回答
    "我没有龙虎榜数据"。现在有工具时改为"未被快照覆盖的用工具取"。
    """
    if not codes:
        return "", []
    try:
        quotes: list[Quote] = list(await hub_provider_get_quotes(codes))
    except Exception as exc:  # noqa: BLE001  行情失败降级，不拖垮聊天
        log.warning("assistant market block failed: %s", exc)
        return "", []
    by_code = {q.symbol: q for q in quotes}
    used = [by_code[c] for c in codes if c in by_code and (by_code[c].price is not None or by_code[c].name)]
    lines = [format_quote_line(q) for q in used]
    if not lines:
        return "", []
    now = datetime.now(timezone.utc).astimezone()
    if tools_enabled:
        scope = (
            "这是**已提前注入**的快照；快照之外的标的与维度（K线/分时/资金流/龙虎榜/"
            "公告财务/指数等）请用「可用工具」按需取数，不要因为快照里没有就回答「我没有」。"
        )
    else:
        scope = (
            "以下是你**仅有的**实时数据：回答价格/涨跌幅时只准引用这些数字；"
            "快照之外的实时信息（资金流/龙虎榜/其他个股等）你都没有，请如实说明并引导用户到对应页面查看。"
        )
    header = (
        f"## 实时数据快照（拉取于北京时间 {now:%H:%M}）\n"
        + scope + "\n"
        "**溯源纪律**：引用快照数字时必须带上「来源 + 数据时间 + 口径」"
        "（每行末尾已给出，照抄即可）；行尾有 ⚠ 数据质量标记时，只陈述数值、不下确定性结论；"
        "没有被快照覆盖、也没有工具支撑的数字一律不许出现。\n"
    )
    return header + "\n".join(lines) + "\n", quote_sources(used)


async def build_market_block(
    hub_provider_get_quotes, codes: list[str], tools_enabled: bool = False
) -> str:
    """只要提示词块的便捷包装（路由用 build_market_context 拿溯源清单）。"""
    block, _ = await build_market_context(hub_provider_get_quotes, codes, tools_enabled)
    return block
