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

from app.core.bjtime import beijing_today
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
from app.market import trade_calendar


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
    # 生产侧用 beijing_today() 判定「今天」，测试必须同源（KB-TRADE-02）
    assert d <= beijing_today()
    # 有日历时取 <= 今天的最近一天
    ctx2 = ToolContext(provider=None, trading_days={"2026-09-04"})
    assert _latest_trade_day(ctx2) == date(2026, 9, 4)
    # 未来日期不能当选
    ctx3 = ToolContext(provider=None, trading_days={(beijing_today() + timedelta(days=1)).isoformat()})
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


def test_limit_down_tool_checks_calendar_and_uses_actual_consecutive_field(monkeypatch):
    from app.assistant.tools.market import _t_limit_down

    calls = []
    async def pool(day):
        calls.append(day)
        return [{"symbol": "301010", "name": "晶雪节能", "consecutive_days": 2,
                 "change_pct": -10.0}]
    async def calendar(_provider):
        return [date(2026, 9, 24), date(2026, 9, 28)]

    provider = type("Provider", (), {"get_limit_down_pool": staticmethod(pool)})()
    ctx = ToolContext(provider=provider)
    monkeypatch.setattr(trade_calendar, "trading_days", calendar)
    blocked = _run(_t_limit_down(ctx, date="2026-09-25"))
    assert "不是交易日" in blocked and calls == []
    allowed = _run(_t_limit_down(ctx, date="2026-09-24"))
    assert "连跌天" in allowed and "2" in allowed and calls == [date(2026, 9, 24)]


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
            return beijing_today().isoformat(), None

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


# ---------------------------------------------------------------- 板块资金流工具（P1-3）


def _patch_flow(monkeypatch, payload):
    """把 board_flow.get_board_fund_flow 换成受控桩（工具内部按名导入，故打模块属性）。"""
    from app.market import board_flow as bf

    async def fake(kind, range_key):
        return payload

    monkeypatch.setattr(bf, "get_board_fund_flow", fake)


def test_board_flow_tool_renders_rows(monkeypatch):
    _patch_flow(monkeypatch, {
        "available": True, "updated_at": "15:03:00", "degraded": [],
        "rows": [
            {"name": "绿色电力", "main_net_yi": 32.33, "change_pct": 0.19, "streak": 4, "rank_delta": 1},
            {"name": "某板", "main_net_yi": None, "change_pct": None},
        ],
    })
    out = _run(run_tool(ToolCall("board_flow", {}), _ctx(), cache=None))
    assert "绿色电力" in out and "32.33 亿" in out
    assert "连续 4 日净流入" in out and "榜位↑1" in out
    assert "--" in out  # 缺失如实显示，不冒充 0
    assert "不构成买卖建议" in out


def test_board_flow_tool_rejects_bad_params(monkeypatch):
    _patch_flow(monkeypatch, {"available": True, "rows": [{"name": "x", "main_net_yi": 1}]})
    assert "kind 只支持" in _run(run_tool(ToolCall("board_flow", {"kind": "bogus"}), _ctx(), cache=None))
    assert "range 只支持" in _run(run_tool(ToolCall("board_flow", {"range": "1y"}), _ctx(), cache=None))


def test_board_flow_tool_unavailable_is_honest(monkeypatch):
    """取不到就如实说"暂不可用"，绝不编造数值。"""
    _patch_flow(monkeypatch, {"available": False, "rows": []})
    assert "暂不可用" in _run(run_tool(ToolCall("board_flow", {}), _ctx(), cache=None))


def test_board_flow_tool_surfaces_degraded_note(monkeypatch):
    """降级口径必须随结论一起给出（数据源纪律：延迟口径不能伪装实时）。"""
    _patch_flow(monkeypatch, {
        "available": True, "updated_at": "15:03:00",
        "rows": [{"name": "甲板", "main_net_yi": 1.0, "change_pct": 0.5}],
        "degraded": ["主域不可达，使用延迟口径（push2delay）"],
    })
    out = _run(run_tool(ToolCall("board_flow", {}), _ctx(), cache=None))
    assert "延迟口径" in out


def test_board_flow_registered_in_specs_and_manifest():
    assert "board_flow" in TOOL_SPECS
    assert "board_flow" in tool_manifest()
    # 参数说明不能含 `|`（那是工具调用分段符，写进去会被解析成无 `=` 的段而丢弃）
    assert "|" not in TOOL_SPECS["board_flow"].params


# ---------------------------------------------------------------- 大宗商品工具（P1-33）


def _patch_commodity(monkeypatch, payload):
    """把 commodity_chain.collect 换成受控桩（工具内部按名导入，故打模块属性）。"""
    from app.market import commodity_chain as cm

    async def fake(asof=None):
        return payload

    monkeypatch.setattr(cm, "collect", fake)


_CM_ROW = {
    "code": "RB0", "label": "螺纹钢", "sw_name": "钢铁",
    "date": "2026-09-10", "change": 1.23, "dead_zone": 0.68,
    "zone": "涨破死区", "bias": 1, "mid_signal": True,
    "mid_verified": True, "mid_edge": 0.381, "mid_t": 3.389, "mid_n": 4214,
}


def test_commodity_tool_renders_mid_term_signal(monkeypatch):
    _patch_commodity(monkeypatch, {
        "rows": [_CM_ROW], "mid_signals": [_CM_ROW], "as_of": "2026-09-11",
        "timing_note": "实测以同日共振为主，商品不具备领先性。",
        "disclaimer": "以上为规则层偏向研判，不构成买卖建议。",
    })
    out = _run(run_tool(ToolCall("commodity", {}), _ctx(), cache=None))
    assert "螺纹钢" in out and "+1.23%" in out and "涨破死区" in out
    assert "0.381pp" in out and "t=3.389" in out and "n=4214" in out
    assert "不构成买卖建议" in out


def test_commodity_tool_always_carries_timing_note(monkeypatch):
    """红线 3 守卫：助手必须拿到「无领先性」这句话，否则会把共振转述成预测。"""
    _patch_commodity(monkeypatch, {
        "rows": [{**_CM_ROW, "mid_signal": False, "mid_verified": False,
                  "mid_edge": None, "mid_t": None, "mid_n": 0}],
        "mid_signals": [], "as_of": "2026-09-11",
        "timing_note": "实测时点结构：以同日共振为主，隔夜口径基本失效。",
        "disclaimer": "不构成买卖建议。",
    })
    out = _run(run_tool(ToolCall("commodity", {}), _ctx(), cache=None))
    assert "时点结构" in out
    assert "未通过实证" in out  # 未验证的链必须显式说明不给板块含义


def test_commodity_tool_keyword_filter_and_miss(monkeypatch):
    other = {**_CM_ROW, "code": "SC0", "label": "原油", "sw_name": "石油石化"}
    _patch_commodity(monkeypatch, {
        "rows": [_CM_ROW, other], "mid_signals": [], "as_of": "2026-09-11",
        "timing_note": "t", "disclaimer": "d",
    })
    hit = _run(run_tool(ToolCall("commodity", {"keyword": "原油"}), _ctx(), cache=None))
    assert "原油" in hit and "螺纹钢" not in hit
    miss = _run(run_tool(ToolCall("commodity", {"keyword": "不存在品"}), _ctx(), cache=None))
    assert "未找到匹配" in miss


def test_commodity_tool_renders_skip_reason(monkeypatch):
    _patch_commodity(monkeypatch, {
        "rows": [{**_CM_ROW, "skip_reason": "源不可得", "change": None, "zone": "unknown"}],
        "mid_signals": [], "as_of": "2026-09-11",
        "timing_note": "t", "disclaimer": "d",
    })
    out = _run(run_tool(ToolCall("commodity", {}), _ctx(), cache=None))
    assert "源不可得" in out


def test_commodity_tool_unavailable_is_honest(monkeypatch):
    _patch_commodity(monkeypatch, None)
    assert "暂不可用" in _run(run_tool(ToolCall("commodity", {}), _ctx(), cache=None))


def test_commodity_tool_failure_is_honest(monkeypatch):
    from app.market import commodity_chain as cm

    async def boom(asof=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(cm, "collect", boom)
    out = _run(run_tool(ToolCall("commodity", {}), _ctx(), cache=None))
    assert "取数失败" in out and "不要编造" in out


def test_commodity_registered_in_specs_and_manifest():
    assert "commodity" in TOOL_SPECS
    assert "commodity" in tool_manifest()
    assert "|" not in TOOL_SPECS["commodity"].params


# ---------------------------------------------------------------- 气候相位工具（P1-32）


def _patch_climate(monkeypatch, payload):
    """把 climate.collect 换成受控桩（工具内部按名导入，故打模块属性）。"""
    from app.market import climate as cl

    async def fake(asof=None):
        return payload

    monkeypatch.setattr(cl, "collect", fake)


_CL_PAYLOAD = {
    "state": "el_nino",
    "alert": None,
    "strength": "strong",
    "consecutive": 5,
    "peak_abs": 1.8,
    "threshold": 0.5,
    "persist_seasons": 5,
    "latest": {"season": "JJA", "year": 2026, "anom": 1.8, "end_date": "2026-08-31"},
    "series": [{"season": "AMJ", "year": 2026, "anom": 0.95},
               {"season": "MJJ", "year": 2026, "anom": 1.39},
               {"season": "JJA", "year": 2026, "anom": 1.80}],
    "candidate_links": [
        {"target": "磷化工", "strength": 2}, {"target": "化肥", "strength": 2},
    ],
    "as_of": "2026-09-11",
    "timing_note": "ONI 是三月滑动平均且季末后发布，天然滞后，不构成领先指标。",
    "empirical_verdict": "**人工传导链未获数据支持**……基础化工方向与表相反。",
    "disclaimer": "以上为气候相位与候选传导链陈述，不构成买卖建议。",
}


def test_climate_tool_renders_phase_alert_and_series(monkeypatch):
    _patch_climate(monkeypatch, {**_CL_PAYLOAD, "state": "neutral", "alert": "el_nino",
                                 "consecutive": 3})
    out = _run(run_tool(ToolCall("climate", {}), _ctx(), cache=None))
    assert "中性" in out
    assert "预警态" in out and "未满 5 季" in out
    assert "JJA" in out and "+1.80" in out
    assert "不构成买卖建议" in out


def test_climate_tool_marks_candidate_links_as_unverified(monkeypatch):
    _patch_climate(monkeypatch, _CL_PAYLOAD)
    out = _run(run_tool(ToolCall("climate", {}), _ctx(), cache=None))
    assert "磷化工" in out
    assert "未获数据支持" in out or "未获支持" in out


def test_climate_tool_always_carries_both_disclaimers(monkeypatch):
    """红线 3 守卫：少了任一段，助手都可能把气候相位转述成买卖结论。"""
    _patch_climate(monkeypatch, _CL_PAYLOAD)
    out = _run(run_tool(ToolCall("climate", {}), _ctx(), cache=None))
    assert "时点结构" in out and "不构成领先指标" in out
    assert "实证判读" in out and "未获" in out


def test_climate_tool_unjudged_is_honest(monkeypatch):
    _patch_climate(monkeypatch, {**_CL_PAYLOAD, "state": None, "alert": None,
                                 "unjudged_reason": "ONI 数据滞后（最新季末距今 200 天 > 120）"})
    out = _run(run_tool(ToolCall("climate", {}), _ctx(), cache=None))
    assert "未判定" in out and "滞后" in out
    assert "磷化工" not in out


def test_climate_tool_unavailable_is_honest(monkeypatch):
    _patch_climate(monkeypatch, None)
    assert "暂不可用" in _run(run_tool(ToolCall("climate", {}), _ctx(), cache=None))


def test_climate_tool_failure_is_honest(monkeypatch):
    from app.market import climate as cl

    async def boom(asof=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(cl, "collect", boom)
    out = _run(run_tool(ToolCall("climate", {}), _ctx(), cache=None))
    assert "取数失败" in out and "不要编造" in out


def test_climate_registered_in_specs_and_manifest():
    assert "climate" in TOOL_SPECS
    assert "climate" in tool_manifest()
    assert "|" not in TOOL_SPECS["climate"].params


# ---------------------------------------------------------------- P2-5 传导链检索

def _chain_out(**kw):
    return _run(run_tool(ToolCall("chain", kw), _ctx(), cache=None))


def test_chain_registered_in_specs_and_manifest():
    from app.assistant.tools import TOOL_LABELS

    assert "chain" in TOOL_SPECS
    assert "chain" in tool_manifest()
    assert "chain" in TOOL_LABELS
    assert "|" not in TOOL_SPECS["chain"].params


def test_chain_returns_targets_for_static_chain():
    out = _chain_out(keyword="厄尔尼诺")
    assert "磷化工" in out and "化肥" in out
    assert "方向 +1" in out


def test_chain_result_must_carry_unvalidated_caveat():
    """红线守卫：不带这句，模型会把"厄尔尼诺→化肥"当选股依据讲给用户。

    （与 climate 工具的 both_disclaimers 同类：口径必须随结论一起走。）
    """
    out = _chain_out(keyword="厄尔尼诺")
    assert "人工维护的映射索引" in out
    assert "不是已验证的行情规律" in out
    assert "未经数据验证" in out
    assert "不构成买卖建议" in out


def test_chain_dynamic_direction_is_not_fabricated():
    """非农/利率的方向取决于事件正文，本处无从判定 ⇒ 必须说"不固定"，不许填默认值。

    这是本工具最危险的一处：给个看着像结论的方向，模型就会照讲。
    """
    out = _chain_out(keyword="非农")
    assert "方向不固定" in out
    # 不得出现任何具体方向数字
    assert "方向 +1" not in out and "方向 -1" not in out
    # 动态链不预置目标板块，如实说明
    assert "动态确定" in out


def test_chain_bidirectional_containment():
    """关键词可带上下文（"美国非农数据"）也可只给触发词本身。"""
    assert "非农" in _chain_out(keyword="美国非农数据")
    assert "厄尔尼诺" in _chain_out(keyword="厄尔尼诺")
    # 反向：给出的是链名也能命中（"农业链"）
    assert "农业链" in _chain_out(keyword="农业链")


def test_chain_unknown_keyword_gives_examples():
    """未命中要给触发词示例帮模型换个说法，而不是只说"没找到"。"""
    out = _chain_out(keyword="天顶星")
    assert "未找到" in out
    assert "厄尔尼诺" in out and "非农" in out


def test_chain_missing_keyword_is_param_error():
    out = _chain_out()
    assert "参数缺失" in out
    assert "厄尔尼诺" in out


def test_chain_group_table_is_single_source():
    """正向 match_chains 与反向 find_chains 必须同源（CHAIN_GROUPS），不各留一份词表。

    若有人再抄一份触发词表，正向命中的链会与反向检索的链不一致——用户看到
    "新闻里有这条链"却检索不到，且两者都不会报错。
    """
    from app.events.chains import CHAIN_GROUPS, chain_keywords

    forward_keys = [k for g in CHAIN_GROUPS for k in g["keys"]]
    assert set(forward_keys) == set(chain_keywords())
    for g in CHAIN_GROUPS:
        assert g["keys"], f"链 {g['id']} 没有触发词，正向永远命中不了"
        assert g["direction_note"], f"链 {g['id']} 缺方向口径"
