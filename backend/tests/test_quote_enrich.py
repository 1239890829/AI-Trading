from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.quote_enrich import fill_limit_prices


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
