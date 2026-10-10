"""Attribution originals keep their source/date through every view projection."""
from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from app.api.routes import theme_catalog
from app.picks.intraday_opportunity import assemble, top_watch_stocks
from app.picks.relay_rank import compute_relay_rank
from app.schemas.market import Kline, LimitUpRecord
from app.services import theme_service

DAY = date(2026, 10, 9)
REASON = "收购预案+传媒业务+其他数据源标签"


def record(reason=REASON, source="ths"):
    return LimitUpRecord(symbol="600825", name="新华传媒", trade_date=DAY, reason=reason,
                         consecutive_boards=4, seal_amount=1e8, first_seal_time="09:35:00", source=source)


@pytest.mark.parametrize("reason,source", [(REASON, "ths"), (None, "eastmoney")])
def test_theme_to_intraday_reference_keeps_original_identity(reason, source):
    card = theme_service._build_card(
        theme="传媒", members=[record(reason, source)], stock_themes={"600825": ["传媒"]},
        enhance={}, board_index={}, prev_boards_map={}, prev_theme_stats={}, market_max_boards=4,
        active_days=1, daily_counts=[], market_break_rate=None, snapshot_map={}, prev_theme_symbols=set(),
    )
    rung = card["ladder"][0]
    assert (rung["reason"], rung["source"], rung["trade_date"]) == (reason, source, DAY.isoformat())
    board = {"trade_date": DAY.isoformat(), "themes": [card], "summary": {}, "caveats": []}
    payload = assemble(board, [], False)
    stock = payload["themes"][0]["stocks"][0]
    assert (stock["reason"], stock["reason_source"], stock["reason_date"]) == (reason, source, DAY.isoformat())
    reference = top_watch_stocks(payload)["reference_items"][0]
    assert (reference["reason"], reference["reason_source"], reference["reason_date"]) == (reason, source, DAY.isoformat())


def test_relay_rank_keeps_pool_cause_source_and_date_without_changing_factor_values():
    from datetime import datetime, timezone

    class Provider:
        async def get_kline(self, symbol, timeframe):
            return [Kline(symbol=symbol, timeframe=timeframe, ts=datetime(2026, 10, day, tzinfo=timezone.utc),
                          open=8, high=10, low=7, close=10, source="fixture") for day in [8, 9]]

    rows = asyncio.run(compute_relay_rank(Provider(), DAY, pool=[record()]))
    assert rows[0]["reason"] == REASON
    assert rows[0]["reason_source"] == "ths"
    assert rows[0]["reason_date"] == "2026-10-09"
    assert rows[0]["kmid2"] == 0.6667


def test_concept_detail_keeps_reason_identity_without_replacing_with_membership(monkeypatch):
    async def today(_hub):
        return DAY

    async def pool(_day):
        return [record()]

    async def enhancement(*_args):
        return {}

    monkeypatch.setattr(theme_catalog, "default_trade_date", today)
    monkeypatch.setattr(theme_service, "_em_enhancement_map", enhancement)
    svc = SimpleNamespace(get_members=lambda code: [SimpleNamespace(symbol="600825", name="新华传媒")],
                          get_catalog=lambda limit: [SimpleNamespace(code="A", name="成员概念不是涨停原因")])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(snapshot_service=SimpleNamespace(
        snapshot=[{"symbol":"600825", "price":10, "change_pct":10}]))))
    hub = SimpleNamespace(provider=SimpleNamespace(get_limit_up_pool=pool))
    data = asyncio.run(theme_catalog.theme_catalog_detail("A", request, False, svc, hub))["data"]
    member = data["members"][0]
    assert member["reason"] == REASON
    assert member["reason_source"] == "ths"
    assert member["reason_date"] == "2026-10-09"
    assert data["name"] == "成员概念不是涨停原因"
