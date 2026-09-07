"""助手受限工具调用测试（P0-3）。

覆盖三件事：
1. 解析/剥离：标记不能漏给用户，模型写得歪一点也要能兜住；
2. 校验：表外工具名、词典外代码、非交易日一律拒绝——这是"受限"的全部含义；
3. 执行与降级：拿到数据要能渲染，工具炸了只降级一行、绝不中断对话。
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.assistant.tools import (
    MAX_CALLS_PER_TURN,
    TOOL_SPECS,
    ToolCall,
    ToolContext,
    _valid_date,
    _valid_symbols,
    has_partial_tool_call,
    parse_tool_calls,
    run_tool,
    run_tool_calls,
    strip_tool_calls,
    tool_manifest,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------- 解析


def test_parse_single_call():
    calls = parse_tool_calls("{{tool:limit_up|date=2026-09-04}}\n")
    assert len(calls) == 1
    assert calls[0].name == "limit_up"
    assert calls[0].args == {"date": "2026-09-04"}


def test_parse_multiple_and_no_arg():
    calls = parse_tool_calls("{{tool:limit_up}}{{tool:brief}} 后面是正文")
    assert [c.name for c in calls] == ["limit_up", "brief"]
    assert calls[0].args == {}


def test_parse_ignores_malformed():
    assert parse_tool_calls("{{tool:}}") == []          # 空工具名
    assert parse_tool_calls("{{tool:limit_up|garbage}}") == [ToolCall("limit_up", {})]
    assert parse_tool_calls("正文没有标记") == []


def test_parse_with_spaces_and_chinese_comma():
    calls = parse_tool_calls("{{tool:quotes|symbols=600519，000001}}")
    assert calls[0].args["symbols"] == "600519，000001"


def test_strip_removes_marker_keeps_text():
    assert strip_tool_calls("{{tool:brief}}\n\n今天的简报是…").strip() == "今天的简报是…"
    assert strip_tool_calls("没有标记") == "没有标记"


def test_strip_preserves_inter_chunk_space():
    """流式按块剥离时不能吞掉词间空格——这是 strip() 最容易踩的坑。"""
    assert strip_tool_calls("今日涨停 ") + strip_tool_calls("38 家") == "今日涨停 38 家"


def test_has_partial_tool_call():
    """半个标记必须先按住——否则用户会看到 `{{` / `{{tool:limit` 这种鬼东西。

    流式是逐字符来的（`{{` 一块、`tool:limit_up` 一块、`}}` 一块），
    只认 `{{tool:` 完整前缀会先把 `{{` 漏出去（2026-09-06 真实链路实测踩到）。
    """
    assert has_partial_tool_call("{{")
    assert has_partial_tool_call("我想想{{tool:limit")
    assert has_partial_tool_call("{{tool:limit_up|date=2026-")
    assert not has_partial_tool_call("{{tool:brief}}")
    assert not has_partial_tool_call("普通文本")


def test_stream_like_chunking_never_leaks_marker():
    """按真实流式切块喂进去：任何前缀都不能漏给用户，闭合后正文要完整。"""
    chunks = ["{{", "tool:limit_up", "}}", "\n\n今日涨停 ", "38 家"]
    out = []
    pending = ""
    for c in chunks:
        pending += c
        if has_partial_tool_call(pending):
            continue
        out.append(strip_tool_calls(pending))
        pending = ""
    if pending:
        out.append(strip_tool_calls(pending))
    assert "".join(out).strip() == "今日涨停 38 家"


# ---------------------------------------------------------------- 校验


def test_valid_symbols_accepts_dict_codes_and_strips_suffix():
    codes, err = _valid_symbols("600519.SH, SH000001", {"600519", "000001"})
    assert err is None and codes == ["600519", "000001"]


def test_valid_symbols_rejects_unknown_and_non_code():
    _, err = _valid_symbols("600519", {"000001"})
    assert err and "不在实体词典" in err
    _, err = _valid_symbols("茅台", set())
    assert err and "非法" in err


def test_valid_symbols_caps_at_max():
    codes, _ = _valid_symbols(",".join(f"00000{i}" for i in range(9)), set())
    assert len(codes) == 6


def test_valid_symbols_empty_dict_allows_any_code():
    """字典没加载（CI/冷启动）时不能让工具整体失效，只做格式校验。"""
    codes, err = _valid_symbols("600519", set())
    assert err is None and codes == ["600519"]


def test_valid_date_calendar_and_future():
    days = {"2026-09-04", "2026-09-03"}
    assert _valid_date("2026-09-04", days) == ("2026-09-04", None)
    ok, err = _valid_date("2026-09-05", days)
    assert ok is None and "不是交易日" in err
    ok, err = _valid_date("2099-01-01", None)
    assert ok is None and "不能晚于今天" in err
    ok, err = _valid_date("2026/09/04", None)
    assert ok is None and "格式" in err


def test_valid_date_empty_means_default():
    assert _valid_date(None, set()) == (None, None)


def test_latest_trade_day_weekend_fallback():
    """日历不可用时不能直接拿今天去查——周日查池子只会得到空结果，
    而模型会照着"没有数据"回答用户。"""
    from datetime import timedelta

    from app.assistant.tools import _latest_trade_day

    ctx = ToolContext(provider=None)
    d = _latest_trade_day(ctx)          # 无日历 → 周末回退
    assert d.weekday() < 5
    assert d <= date.today()
    # 有日历时取 <= 今天的最近一天
    ctx2 = ToolContext(provider=None, trading_days={"2026-09-04"})
    assert _latest_trade_day(ctx2) == date(2026, 9, 4)
    # 未来日期不能当选
    ctx3 = ToolContext(provider=None, trading_days={(date.today() + timedelta(days=1)).isoformat()})
    assert _latest_trade_day(ctx3).weekday() < 5


# ---------------------------------------------------------------- 执行


class _FakeProvider:
    def __init__(self, boom: bool = False):
        self.boom = boom
        self.calls: list[tuple] = []

    async def get_limit_up_pool(self, trade_date):
        self.calls.append(("limit_up", trade_date))
        if self.boom:
            raise RuntimeError("ths 429")
        return [
            {"symbol": "600519", "name": "贵州茅台", "consecutive_boards": 1,
             "change_pct": 10.0, "reason": "白酒"},
        ]

    async def get_quotes(self, symbols):
        self.calls.append(("quotes", tuple(symbols)))
        return [{"symbol": s, "name": f"N{s}", "price": 1.0, "change_pct": 2.0} for s in symbols]

    async def get_hot_stock_list(self, period):
        return [{"symbol": "000001", "name": "平安银行", "rank": 1, "change_pct": 3.0}]


def _ctx(boom: bool = False, **kw) -> ToolContext:
    base = dict(provider=_FakeProvider(boom=boom), known_symbols={"600519", "000001"},
                trading_days={"2026-09-04"})
    base.update(kw)
    return ToolContext(**base)


def test_run_tool_unknown_name_rejected():
    out = _run(run_tool(ToolCall("rm_rf"), _ctx(), cache=None))
    assert "未登记" in out and "rm_rf" in out


def test_run_tool_renders_rows():
    out = _run(run_tool(ToolCall("limit_up", {"date": "2026-09-04"}), _ctx(), cache=None))
    assert "涨停池 2026-09-04" in out
    assert "贵州茅台" in out and "白酒" in out


def test_run_tool_rejects_bad_symbol():
    out = _run(run_tool(ToolCall("quotes", {"symbols": "999999"}), _ctx(), cache=None))
    assert "不在实体词典" in out


def test_run_tool_rejects_non_trade_date():
    out = _run(run_tool(ToolCall("limit_up", {"date": "2026-09-05"}), _ctx(), cache=None))
    assert "不是交易日" in out


def test_run_tool_degrades_on_exception():
    """工具炸了只降级一行——绝不能把整段对话带走。"""
    out = _run(run_tool(ToolCall("limit_up"), _ctx(boom=True), cache=None))
    assert "工具失败" in out and "ths 429" in out


def test_run_tool_calls_caps_at_limit():
    calls = [ToolCall("hot", {"period": "day"}) for _ in range(MAX_CALLS_PER_TURN + 2)]
    block, used = _run(run_tool_calls(calls, _ctx(), cache=None))
    assert len(used) == MAX_CALLS_PER_TURN
    assert "上限" in block


def test_run_tool_cache_hit(monkeypatch):
    ctx = _ctx()
    from app.core.ttl_cache import TTLCache

    cache = TTLCache("test.tools", ttl=30.0)
    out1 = _run(run_tool(ToolCall("limit_up"), ctx, cache=cache))
    out2 = _run(run_tool(ToolCall("limit_up"), ctx, cache=cache))
    assert "命中缓存" in out2
    assert out1.split("\n")[0] in out2
    assert len(ctx.provider.calls) == 1  # 第二次没回源


def test_run_tool_bad_enum_param():
    out = _run(run_tool(ToolCall("hot", {"period": "year"}), _ctx(), cache=None))
    assert "参数不合法" in out


def test_manifest_lists_every_tool():
    m = tool_manifest()
    for name in TOOL_SPECS:
        assert f"{{{{tool:{name}|" in m
    assert "只读" in m


def test_manifest_in_prompt_when_enabled():
    from app.assistant.prompt import build_system_prompt

    off = build_system_prompt(None, tools_enabled=False)
    on = build_system_prompt(None, tools_enabled=True)
    assert "可用工具" not in off
    assert "可用工具" in on and "{{tool:limit_up|" in on


def test_brief_tool_no_data_is_explicit():
    """没有简报就说没有——不能让模型自己脑补一份。"""
    class _NoBrief:
        @staticmethod
        def brief_for_today():
            return date.today().isoformat(), None

    monkeypatched = _ctx()
    import app.picks.morning_brief as mb

    orig = mb.brief_for_today
    mb.brief_for_today = _NoBrief.brief_for_today
    try:
        out = _run(run_tool(ToolCall("brief"), monkeypatched, cache=None))
    finally:
        mb.brief_for_today = orig
    assert "尚无盘前简报" in out


def test_review_tool_without_session_factory():
    out = _run(run_tool(ToolCall("review", {"date": "2026-09-04"}), _ctx(), cache=None))
    # 未配置会话 → 明确说不可用；配置了但没报告 → 明确说没有。两者都不能是空的
    assert out


@pytest.mark.parametrize("name", sorted(TOOL_SPECS))
def test_every_tool_has_desc_and_params(name):
    spec = TOOL_SPECS[name]
    assert spec.desc and spec.params
    assert spec.name == name
