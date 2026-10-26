"""Missing evidence is not proof that strategy thresholds are wrong."""
from __future__ import annotations

import pytest

from app.review.config import MethodologyConfig
from app.review.methodology import build_meta_insights
from app.review.schemas import DataGap, DimensionResult, MarketSnapshot, ReviewData, TradingSnapshot


def _insight(dimension: DimensionResult):
    data = ReviewData(trade_date="20261026", market=MarketSnapshot(trade_date="20261026"),
                      trading=TradingSnapshot(trade_date="20261026"))
    before = dimension.model_dump()
    result = build_meta_insights(data, [dimension], MethodologyConfig())
    assert dimension.model_dump() == before
    return next(item for item in result if item.dimension == dimension.key)


@pytest.mark.parametrize("status,with_gap", [("degraded", False), ("degraded", True), ("ok", True)])
def test_missing_coverage_is_not_success_or_evidence_for_a_threshold_change(status, with_gap):
    gaps = [DataGap(field="sector_boards", source="ths", reason="该日只有部分板块有来源日期",
                    impact="market.主线", severity="warn")] if with_gap else []
    insight = _insight(DimensionResult(key="market", title="市场", status=status,
                                      findings=["行业榜仅部分覆盖"], gaps=gaps))
    assert insight.effectiveness == "unknown"
    assert "资料或覆盖不足" in insight.observation
    assert "不据此调整策略或阈值" in insight.suggestion
    assert "采集成功" not in insight.observation
    assert "阈值过高" not in insight.suggestion
    if with_gap:
        assert "sector_boards" in insight.evidence
        assert "只有部分板块" in insight.evidence


def test_a_partial_dimension_with_a_judgement_is_not_marked_as_validated_effectiveness():
    insight = _insight(DimensionResult(key="market", title="市场", status="degraded",
                                      findings=["来源日期仅部分可核"], judgements=["限于已核样本观察"],
                                      gaps=[DataGap(field="breadth", source="ths", reason="有一条源日期未知",
                                                    impact="market.宽度", severity="warn")]))
    assert insight.effectiveness == "unknown"
    assert "1 条判断" in insight.observation
    assert "不据此调整策略或阈值" in insight.suggestion


def test_a_complete_dimension_without_judgements_can_be_a_legitimate_no_action():
    insight = _insight(DimensionResult(key="trades", title="操作", status="ok",
                                      findings=["今日无新建委托"], judgements=[]))
    assert insight.effectiveness == "unknown"
    assert "无判断" in insight.observation
    assert "合法" in insight.suggestion
    assert "阈值过高" not in insight.suggestion
    assert "指标选取不当" not in insight.suggestion


def test_a_blocked_dimension_does_not_suggest_disabling_the_dimension_due_to_missing_data():
    insight = _insight(DimensionResult(key="market", title="市场", status="blocked",
                                      evidence={"blocked_reason": "交易日历不可核"}))
    assert insight.effectiveness == "ineffective"
    assert insight.evidence == "交易日历不可核"
    assert "先核实数据来源" in insight.suggestion
    assert "应关闭" not in insight.suggestion


def test_a_complete_dimension_with_a_judgement_preserves_the_existing_output_classification():
    insight = _insight(DimensionResult(key="trades", title="操作", status="ok",
                                      findings=["委托按原规则拒绝"], judgements=["未发现绕过硬门"] ))
    assert insight.effectiveness == "effective"
    assert insight.evidence == "未发现绕过硬门"
    assert insight.suggestion == ""
