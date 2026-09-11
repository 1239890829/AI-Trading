from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.quote_enrich import enrich_quote, fill_limit_prices


def _chain(tencent_quote):
    """构造 ths(主) + tencent(备) 的最小 provider 链桩。"""
    async def _tq(symbol: str):
        return tencent_quote

    return SimpleNamespace(
        name="ths",
        providers=[
            SimpleNamespace(name="ths"),
            SimpleNamespace(name="tencent", get_quote=_tq),
        ],
    )


def _counting_chain(tencent_quote):
    """同 `_chain`，但记录上游被调用了几次（P1-4 回归位）。"""
    calls = {"n": 0}

    async def _tq(symbol: str):
        calls["n"] += 1
        return tencent_quote

    provider = SimpleNamespace(
        name="ths",
        providers=[
            SimpleNamespace(name="ths"),
            SimpleNamespace(name="tencent", get_quote=_tq),
        ],
    )
    return provider, calls


def _run(coro):
    return asyncio.run(coro)


def test_enriches_missing_limit_prices_from_tencent():
    q = SimpleNamespace(symbol="600519", limit_up_price=None, limit_down_price=None)
    tq = SimpleNamespace(symbol="600519", limit_up_price=1421.53, limit_down_price=1163.07)
    out = _run(fill_limit_prices(_chain(tq), q))
    assert out.limit_up_price == 1421.53
    assert out.limit_down_price == 1163.07


def test_keeps_existing_values_and_fills_only_missing():
    q = SimpleNamespace(symbol="600519", limit_up_price=100.0, limit_down_price=None)
    tq = SimpleNamespace(symbol="600519", limit_up_price=999.0, limit_down_price=80.0)
    out = _run(fill_limit_prices(_chain(tq), q))
    assert out.limit_up_price == 100.0  # 已有的不动
    assert out.limit_down_price == 80.0  # 缺的才补


def test_no_tencent_in_chain_is_noop():
    q = SimpleNamespace(symbol="600519", limit_up_price=None, limit_down_price=None)
    provider = SimpleNamespace(name="ths", providers=[SimpleNamespace(name="ths")])
    out = _run(fill_limit_prices(provider, q))
    assert out.limit_up_price is None  # 补不上就补不上，绝不臆造限价


def test_provider_is_tencent_itself_is_noop():
    """主源就是 tencent 时不能再找自己（无限自补）。"""
    q = SimpleNamespace(symbol="600519", limit_up_price=None, limit_down_price=None)
    provider = SimpleNamespace(name="tencent", providers=[])
    out = _run(fill_limit_prices(provider, q))
    assert out is q


def test_upstream_failure_keeps_original():
    """tencent 拉取抛错时保持原样，异常必须被吞掉（补价是尽力而为）。"""

    async def _boom(symbol: str):
        raise RuntimeError("upstream down")

    q = SimpleNamespace(symbol="600519", limit_up_price=None, limit_down_price=None)
    provider = SimpleNamespace(
        name="ths", providers=[SimpleNamespace(name="tencent", get_quote=_boom)]
    )
    out = _run(fill_limit_prices(provider, q))
    assert out.limit_up_price is None


def test_none_quote_passthrough():
    assert _run(fill_limit_prices(None, None)) is None


# --------------------------------------------------------- P1-4 enrich_quote

def _quote(**over):
    """完整字段的 quote 桩（链首 ths 口径：限价与估值全缺）。"""
    base = dict(
        symbol="600519",
        limit_up_price=None,
        limit_down_price=None,
        pe_ttm=None,
        pb=None,
        total_mktcap_yi=None,
        float_mktcap_yi=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _tencent_full():
    return SimpleNamespace(
        symbol="600519",
        limit_up_price=1421.53,
        limit_down_price=1163.07,
        pe_ttm=53.66,
        pb=9.1,
        total_mktcap_yi=305.69,
        float_mktcap_yi=305.69,
    )


def test_enrich_quote_fills_both_groups_in_one_upstream_call():
    """P1-4 核心契约：限价 + 估值都缺时**只发一次**上游请求。"""
    provider, calls = _counting_chain(_tencent_full())
    q = _quote()
    out = _run(enrich_quote(provider, q))

    assert calls["n"] == 1, "两个补全函数串联时这里是 2（P1-4 的根因）"
    assert out.limit_up_price == 1421.53
    assert out.limit_down_price == 1163.07
    assert out.pe_ttm == 53.66
    assert out.pb == 9.1
    assert out.total_mktcap_yi == 305.69
    assert out.float_mktcap_yi == 305.69


def test_enrich_quote_skips_upstream_when_nothing_missing():
    provider, calls = _counting_chain(_tencent_full())
    q = _quote(
        limit_up_price=1.0, limit_down_price=1.0, pe_ttm=1.0, pb=1.0, total_mktcap_yi=1.0
    )
    out = _run(enrich_quote(provider, q))

    assert calls["n"] == 0, "无缺失字段时不该有任何上游请求"
    assert out.pe_ttm == 1.0  # 已有的不动


def test_enrich_quote_fills_only_missing_group():
    """只有限价缺、估值齐 ⇒ 仍只补限价（不覆盖已有估值）。"""
    provider, calls = _counting_chain(_tencent_full())
    q = _quote(pe_ttm=7.7, pb=1.2, total_mktcap_yi=100.0)
    out = _run(enrich_quote(provider, q))

    assert calls["n"] == 1
    assert out.limit_up_price == 1421.53
    assert out.pe_ttm == 7.7  # 未被上游覆盖
    assert out.pb == 1.2


def test_enrich_quote_no_tencent_is_noop():
    q = _quote()
    provider = SimpleNamespace(name="ths", providers=[SimpleNamespace(name="ths")])
    out = _run(enrich_quote(provider, q))
    assert out.limit_up_price is None and out.pe_ttm is None


def test_enrich_quote_upstream_failure_is_swallowed():
    async def _boom(symbol: str):
        raise RuntimeError("upstream down")

    q = _quote()
    provider = SimpleNamespace(name="ths", providers=[SimpleNamespace(name="tencent", get_quote=_boom)])
    out = _run(enrich_quote(provider, q))
    assert out.limit_up_price is None and out.pe_ttm is None


def test_enrich_quote_none_passthrough():
    assert _run(enrich_quote(None, None)) is None
