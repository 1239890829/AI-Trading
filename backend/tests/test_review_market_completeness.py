"""The report consumes available saved facts without turning missing fields into zero."""
from app.review.analyzers import RulesAnalyzer
from app.review.config import MethodologyConfig
from app.review.schemas import MarketSnapshot, ReviewData, TradingSnapshot
from app.review.provenance import evidence
from datetime import date


def test_daily_comparison_does_not_invent_limit_counts_or_live_phase():
    data = ReviewData(trade_date="20261026", trading=TradingSnapshot(trade_date="20261026"),
        market=MarketSnapshot(trade_date="20261026", breadth={"up": 1, "down": 0,
            "basis": "复权日线对照，非官方实时涨跌幅"}))
    dim = RulesAnalyzer()._market(data, MethodologyConfig())
    assert "涨 1 / 跌 0，涨停 未记录 / 跌停 未记录" in dim.findings
    assert any("复权日线对照" in row for row in dim.findings)
    assert not any("市场处于" in row for row in dim.judgements)


def test_only_bound_board_sample_enters_market_summary():
    day = date(2026, 10, 26)
    market = MarketSnapshot(trade_date="20261026", contract_version="date_bound_v1",
        sector_boards={"industry": [{"name": "原正式名称", "board_code": "881101.TI", "change_pct": 0}], "concept": []})
    data = ReviewData(trade_date="20261026", market=market, trading=TradingSnapshot(trade_date="20261026"))
    assert "sector_boards" not in RulesAnalyzer()._market(data, MethodologyConfig()).evidence
    market.provenance["sector_boards"] = evidence(day, state="partial", source="ths_official_kline", source_day=day)
    dim = RulesAnalyzer()._market(data, MethodologyConfig())
    assert dim.evidence["sector_boards"]["industry"][0]["board_code"] == "881101.TI"
    assert any("行业已核强弱样本 1 个" in row for row in dim.findings)
    market.provenance["sector_boards"].source_trade_date = "2026-10-23"
    assert "sector_boards" not in RulesAnalyzer()._market(data, MethodologyConfig()).evidence
