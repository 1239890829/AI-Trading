from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.schemas.market import Quality, Quote
from app.services import quote_enrich

from app.services.quote_enrich import enrich_quote, fill_limit_prices, fill_valuation


NOW = datetime.fromisoformat("2026-10-29T10:00:00+08:00")


@pytest.fixture(autouse=True)
def frozen_supplement_clock(monkeypatch):
    monkeypatch.setattr(quote_enrich, "beijing_now", lambda: NOW, raising=False)
    monkeypatch.setattr("app.data_quality.validator.utcnow", lambda: NOW)


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
    q = _quote()
    tq = _tencent_full()
    out = _run(fill_limit_prices(_chain(tq), q))
    assert out.limit_up_price == 1421.53
    assert out.limit_down_price == 1163.07


def test_keeps_existing_values_and_fills_only_missing():
    q = _quote(price=90.0, limit_up_price=100.0)
    tq = _tencent_full(price=90.0, high=95.0, low=89.0, prev_close=90.0, limit_up_price=999.0, limit_down_price=80.0)
    out = _run(fill_limit_prices(_chain(tq), q))
    assert out.limit_up_price == 100.0  # 已有的不动
    assert out.limit_down_price == 80.0  # 缺的才补


def test_no_tencent_in_chain_is_noop():
    q = _quote()
    provider = SimpleNamespace(name="ths", providers=[SimpleNamespace(name="ths")])
    out = _run(fill_limit_prices(provider, q))
    assert out.limit_up_price is None  # 补不上就补不上，绝不臆造限价


def test_provider_is_tencent_itself_is_noop():
    """主源就是 tencent 时不能再找自己（无限自补）。"""
    q = _quote()
    provider = SimpleNamespace(name="tencent", providers=[])
    out = _run(fill_limit_prices(provider, q))
    assert out is q


def test_upstream_failure_keeps_original():
    """tencent 拉取抛错时保持原样，异常必须被吞掉（补价是尽力而为）。"""

    async def _boom(symbol: str):
        raise RuntimeError("upstream down")

    q = _quote()
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
        market="SH",
        source="ths",
        data_timestamp=NOW,
        received_at=NOW,
        price=1330.0,
        high=1340.0,
        low=1300.0,
        prev_close=1320.0,
        limit_up_price=None,
        limit_down_price=None,
        pe_ttm=None,
        pb=None,
        total_mktcap_yi=None,
        float_mktcap_yi=None,
    )
    base.update(over)
    return Quote(**base)


def _tencent_full(**over):
    q = _quote(source="tencent", limit_up_price=1421.53,
               limit_down_price=1163.07, pe_ttm=53.66, pb=9.1,
               total_mktcap_yi=305.69, float_mktcap_yi=305.69)
    # Preserve malformed provider rows for the supplement admission tests;
    # Quote construction itself already normalizes ordinary non-finite inputs.
    return q.model_copy(update=over)


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


@pytest.mark.parametrize("change", [
    {"symbol": "000001"}, {"market": "SZ"}, {"market": None},
    {"symbol": "sz600519"}, {"source": "eastmoney"},
    {"quality": Quality.invalid}, {"quality": Quality.stale},
    {"quality": Quality.low}, {"quality": Quality.medium},
    {"data_timestamp": None}, {"data_timestamp": NOW.replace(tzinfo=None)},
    {"data_timestamp": NOW + timedelta(seconds=1)},
    {"data_timestamp": NOW - timedelta(days=1)},
    {"received_at": NOW.replace(tzinfo=None)}, {"received_at": None},
    {"received_at": NOW + timedelta(seconds=1)},
    {"price": -1.0}, {"price": None}, {"volume": -1.0},
    {"quality_reasons": ["time_regress"]},
])
def test_foreign_or_untrusted_supplement_never_changes_primary(change):
    q = _quote()
    before = q.model_dump()
    out = _run(enrich_quote(_chain(_tencent_full(**change)), q))
    assert out is q
    assert q.model_dump() == before


@pytest.mark.parametrize("change", [
    {"market": None}, {"symbol": "sz600519"},
    {"data_timestamp": None}, {"data_timestamp": NOW.replace(tzinfo=None)},
    {"data_timestamp": NOW + timedelta(seconds=1)},
    {"data_timestamp": NOW - timedelta(days=1)},
])
def test_unknown_or_different_primary_day_is_not_borrowed(change):
    q = _quote(**change)
    before = q.model_dump()
    _run(enrich_quote(_chain(_tencent_full()), q))
    assert q.model_dump() == before


def test_same_security_prefixed_identity_is_accepted_without_rewriting_primary():
    q = _quote(symbol="sh600519")
    before = (q.symbol, q.market, q.source, q.data_timestamp, q.received_at, q.quality)
    _run(enrich_quote(_chain(_tencent_full()), q))
    assert q.limit_up_price == 1421.53 and q.pe_ttm == 53.66
    assert (q.symbol, q.market, q.source, q.data_timestamp, q.received_at, q.quality) == before


@pytest.mark.parametrize("change", [
    {"limit_up_price": float("nan")}, {"limit_up_price": float("inf")},
    {"limit_up_price": 0.0}, {"limit_up_price": -1.0},
    {"limit_up_price": True},
])
def test_invalid_limit_value_is_not_copied(change):
    q = _quote()
    _run(fill_limit_prices(_chain(_tencent_full(**change)), q))
    assert q.limit_up_price is None
    assert q.limit_down_price == 1163.07


@pytest.mark.parametrize("limits", [(1200.0, 1300.0), (1200.0, 1100.0), (1500.0, 1400.0)])
def test_inconsistent_limit_pair_is_not_used_but_valid_valuation_survives(limits):
    q = _quote()
    _run(enrich_quote(_chain(_tencent_full(limit_up_price=limits[0], limit_down_price=limits[1])), q))
    assert q.limit_up_price is None and q.limit_down_price is None
    assert q.pe_ttm == 53.66


@pytest.mark.parametrize("field", ["pe_ttm", "pb", "total_mktcap_yi", "float_mktcap_yi"])
@pytest.mark.parametrize("bad", [0.0, float("nan"), float("inf"), True])
def test_bad_valuation_field_stays_unknown_without_discarding_other_fields(field, bad):
    q = _quote()
    _run(fill_valuation(_chain(_tencent_full(**{field: bad})), q))
    assert getattr(q, field) is None
    assert q.limit_up_price is None  # valuation-only consumer never gains limits
    other = "pb" if field != "pb" else "pe_ttm"
    assert getattr(q, other) is not None


def test_negative_ratios_are_valid_facts_but_negative_capitalisation_is_not():
    q = _quote()
    _run(fill_valuation(_chain(_tencent_full(pe_ttm=-2.3, pb=-0.8,
                                         total_mktcap_yi=-1.0, float_mktcap_yi=-1.0)), q))
    assert q.pe_ttm == -2.3 and q.pb == -0.8
    assert q.total_mktcap_yi is None and q.float_mktcap_yi is None


@pytest.mark.parametrize("which", ["primary", "supplement"])
def test_day_limits_can_be_used_without_laundering_old_valuation(which):
    q = _quote(data_timestamp=NOW - timedelta(seconds=61) if which == "primary" else NOW)
    tq = _tencent_full(data_timestamp=NOW - timedelta(seconds=61) if which == "supplement" else NOW)
    _run(enrich_quote(_chain(tq), q))
    assert q.limit_up_price == 1421.53 and q.limit_down_price == 1163.07
    assert q.pe_ttm is None and q.pb is None and q.total_mktcap_yi is None


def test_valuation_at_existing_sixty_second_boundary_is_accepted():
    q = _quote()
    _run(fill_valuation(_chain(_tencent_full(data_timestamp=NOW - timedelta(seconds=60))), q))
    assert q.pe_ttm == 53.66


def test_two_matching_old_days_do_not_make_current_day_limits_valid():
    old = NOW - timedelta(days=1)
    q = _quote(data_timestamp=old)
    _run(enrich_quote(_chain(_tencent_full(data_timestamp=old)), q))
    assert q.limit_prices_state() == "unavailable" and q.pe_ttm is None


def test_supplement_validation_preserves_the_original_source_row():
    q, tq = _quote(), _tencent_full()
    before = tq.model_dump()
    _run(enrich_quote(_chain(tq), q))
    assert q.pe_ttm == 53.66 and tq.model_dump() == before


def test_source_limits_cannot_conflict_with_a_primary_limit_already_present():
    q = _quote(limit_up_price=1400.0)
    tq = _tencent_full(price=1450.0, high=1480.0, low=1420.0,
                       limit_up_price=1500.0, limit_down_price=1420.0)
    _run(fill_limit_prices(_chain(tq), q))
    assert q.limit_up_price == 1400.0 and q.limit_down_price is None


@pytest.mark.parametrize("price", [1500.0, 1100.0])
def test_source_limits_cannot_enclose_donor_but_exclude_primary_price(price):
    q = _quote(price=price)
    _run(fill_limit_prices(_chain(_tencent_full()), q))
    assert q.limit_up_price is None and q.limit_down_price is None
    assert q.field_sources == {}


def test_rejected_supplement_keeps_real_paper_limit_interception():
    from app.paper.engine import limit_block_reason

    q = _quote()
    _run(fill_limit_prices(_chain(_tencent_full(symbol="000001")), q))
    assert "缺涨停价" in limit_block_reason(q, "buy", q.price)


def test_only_copied_fields_get_their_own_origin_without_mutating_shared_metadata():
    from app.schemas.market import QuoteFieldSource

    cached = _quote(pb=1.2, field_sources={"pb": QuoteFieldSource(source="ths",
                    data_timestamp=NOW, received_at=NOW)})
    q = cached.model_copy()  # same shallow copy used by the REST route
    tq = _tencent_full()
    _run(enrich_quote(_chain(tq), q))
    assert q.field_sources["pe_ttm"].source == "tencent"
    assert q.field_sources["pe_ttm"].data_timestamp == NOW
    assert q.field_sources["pe_ttm"].received_at == NOW
    assert q.field_sources["pb"].source == "ths" and q.pb == 1.2
    assert set(cached.field_sources) == {"pb"}
    assert q.field_sources["pb"] is not cached.field_sources["pb"]
    assert tq.field_sources == {}
    assert q.source == "ths" and q.data_timestamp == NOW and q.received_at == NOW


def test_same_day_closed_valuation_uses_source_clock_difference_not_wall_age(monkeypatch):
    monkeypatch.setattr(quote_enrich, "beijing_now", lambda: NOW.replace(hour=18))
    q = _quote()
    _run(fill_valuation(_chain(_tencent_full()), q))
    assert q.pe_ttm == 53.66 and q.field_sources["pe_ttm"].source == "tencent"


@pytest.mark.parametrize("hour,minute,accepted", [(9, 26, True), (10, 0, False), (12, 0, True), (16, 0, True)])
def test_unestablished_high_low_follows_the_existing_validation_session(monkeypatch, hour, minute, accepted):
    stamp = NOW.replace(hour=hour, minute=minute)
    monkeypatch.setattr(quote_enrich, "beijing_now", lambda: stamp)
    monkeypatch.setattr("app.data_quality.validator.utcnow", lambda: stamp)
    q = _quote(data_timestamp=stamp, received_at=stamp)
    tq = _tencent_full(data_timestamp=stamp, received_at=stamp, high=0, low=0)
    original = tq.model_dump()
    _run(fill_limit_prices(_chain(tq), q))
    assert (q.limit_prices_state() == "ready") is accepted
    assert tq.model_dump() == original


@pytest.mark.parametrize("skew,accepted", [(300, True), (301, False)])
def test_source_and_receipt_clock_cannot_exceed_existing_skew(skew, accepted):
    q = _quote()
    tq = _tencent_full(received_at=NOW - timedelta(seconds=skew))
    _run(enrich_quote(_chain(tq), q))
    assert (q.limit_prices_state() == "ready") is accepted
    assert (q.pe_ttm is not None) is accepted
