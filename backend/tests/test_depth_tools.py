"""助手工具批量登记的三个深度行情工具（P2-28①）：orderbook / trades / auction。

三者都是 `ctx.provider.get_xxx(symbol)` 直连，一起测。**本文件锁住三条纪律**：

1. **盘口不得暗示是 L2**——我们只有 L1 五档，没有十档、没有逐笔委托队列；
2. **取不到如实说取不到**，不编造档位/成交；
3. **竞价取不到必须说明"是源覆盖问题，不是没有竞价数据"**——
   `get_auction_snapshot` 仅 ths 一源实现，取不到是**常态**。若工具只回"没有数据"，
   模型会照着告诉用户"该股没有竞价数据"，那是把能力缺口说成了事实（最危险的一条）。
"""
from __future__ import annotations

import asyncio

from app.assistant.tools import ToolCall, ToolContext, run_tool

SYMS = {"600519"}


class _Prov:
    """可定制返回的 provider 桩。"""

    def __init__(self, *, ob=None, trades=None, auction=None, raise_auction=False):
        self._ob = ob
        self._trades = trades
        self._auction = auction
        self._raise = raise_auction

    async def get_order_book(self, symbol):
        return self._ob

    async def get_trades(self, symbol):
        return self._trades or []

    async def get_auction_snapshot(self, symbols, stage="final"):
        if self._raise:
            raise RuntimeError("ths 超时")
        return self._auction or []


def _ctx(prov) -> ToolContext:
    return ToolContext(provider=prov, known_symbols=SYMS)


def _run(name: str, args: dict, ctx: ToolContext) -> str:
    return asyncio.run(run_tool(ToolCall(name, args), ctx, cache=None))


# ---------------------------------------------------------------- 盘口


def test_orderbook_renders_five_levels():
    ob = {
        "asks": [{"price": 10.02, "volume": 100}, {"price": 10.03, "volume": 200}],
        "bids": [{"price": 10.01, "volume": 300}],
    }
    out = _run("orderbook", {"symbols": "600519"}, _ctx(_Prov(ob=ob)))
    assert "600519" in out
    assert "10.02" in out and "10.01" in out


def test_orderbook_never_implies_l2():
    """我们只有 L1 五档——输出里出现 L2/十档 会误导。"""
    ob = {"asks": [{"price": 1, "volume": 1}], "bids": []}
    out = _run("orderbook", {"symbols": "600519"}, _ctx(_Prov(ob=ob)))
    assert "L2" not in out and "十档" not in out


def test_orderbook_empty_says_unavailable():
    out = _run("orderbook", {"symbols": "600519"}, _ctx(_Prov(ob=None)))
    assert "取不到" in out


def test_orderbook_blank_levels_says_blank():
    out = _run("orderbook", {"symbols": "600519"}, _ctx(_Prov(ob={"bids": [], "asks": []})))
    assert "为空" in out


def test_orderbook_bad_symbol_rejected():
    out = _run("orderbook", {"symbols": "not_a_code"}, _ctx(_Prov(ob=None)))
    assert "参数不合法" in out


# ---------------------------------------------------------------- 逐笔


def test_trades_renders_rows():
    p = _Prov(trades=[{"time": "14:30:01", "price": 10.5, "volume": 100, "side": "buy"}])
    out = _run("trades", {"symbols": "600519"}, _ctx(p))
    assert "600519" in out and "10.5" in out


def test_trades_empty_says_unavailable():
    out = _run("trades", {"symbols": "600519"}, _ctx(_Prov(trades=[])))
    assert "取不到" in out


# ---------------------------------------------------------------- 集合竞价（最关键）


def test_auction_renders_rows():
    p = _Prov(auction=[{"symbol": "600519", "name": "贵州茅台", "price": 10.1,
                        "change_pct": 1.2, "volume": 500}])
    out = _run("auction", {"symbols": "600519"}, _ctx(p))
    assert "贵州茅台" in out and "10.1" in out


def test_auction_empty_explains_it_is_a_source_gap():
    """**最关键的一条**：取不到时必须说清是源覆盖问题，不是"该股没有竞价数据"。"""
    out = _run("auction", {"symbols": "600519"}, _ctx(_Prov(auction=[])))
    assert "取不到" in out
    assert "一源" in out or "仅 ths" in out or "数据源" in out
    assert "不代表" in out or "不是" in out, "必须否定'该股没有竞价数据'的读法"


def test_auction_failure_explains_source_gap():
    out = _run("auction", {"symbols": "600519"}, _ctx(_Prov(raise_auction=True)))
    assert "失败" in out
    assert "一源" in out or "仅 ths" in out


def test_all_three_registered_with_labels():
    import app.assistant.tools as T

    specs = getattr(T, "TOOLS", None) or getattr(T, "TOOL_SPECS", {})
    for k in ("orderbook", "trades", "auction"):
        assert k in specs, f"{k} 未登记"
        assert T.tool_label(k)
