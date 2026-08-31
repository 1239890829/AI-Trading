"""每日精选引擎测试。

核心契约：
- 综合分 = 五维加权，全程带 basis（可解释纪律）
- 换股门槛：新候选分差不足不换（组合稳定性的机制保证）
- 买入范围 = 现价±3% 与技术位收敛，且显式声明"不构成买卖建议"
- 复盘归类枚举完整
"""

from __future__ import annotations

from app.picks.engine import (
    REPLACE_THRESHOLD,
    build_buy_range,
    classify_review,
    score_fundamental,
    score_news,
    score_sentiment,
    apply_replacement_threshold,
    synthesize,
)


def test_news_score_bull_bear_and_neutral():
    assert score_news(1, 0, None, None)[0] == 68.0
    assert score_news(0, 2, None, None)[0] == 0.0  # 50-50=0
    assert score_news(0, 0, None, None) == (50.0, "无活跃事件命中，消息面中性")
    score, basis = score_news(2, 0, "长鑫 LPDDR6 量产", "利好")
    assert score == 86.0 and "长鑫" in basis


def test_news_bear_weight_heavier_than_bull():
    """利空单条扣分（25）大于利好单条加分（18）——突发利空反身性更强。"""
    s_bull = 50 + 18
    s_bear = 50 - 25
    assert score_news(1, 0, None, None)[0] == s_bull
    assert score_news(0, 1, None, None)[0] == s_bear


def test_fundamental_pe_bands():
    assert score_fundamental(20.0, None) == (70.0, "PE 20.0（≤30 合理带）")
    assert score_fundamental(45.0, None)[0] == 55.0
    assert score_fundamental(80.0, None)[0] == 35.0
    assert score_fundamental(None, None)[0] == 50.0


def test_fundamental_revenue_growth_adjusts():
    assert score_fundamental(20.0, 30.0)[0] == 85.0
    assert score_fundamental(20.0, -5.0)[0] == 60.0


def test_sentiment_phase_and_theme_ratio():
    s, basis = score_sentiment("发酵", 0.7)
    assert s == round(90 * 0.7 + 6.0, 1)
    assert "发酵" in basis
    assert score_sentiment(None, None)[0] == 50.0


def test_synthesize_weighted_sum_with_veto():
    sub = {"sentiment": 80, "news": 60, "tech": 70, "fundamental": 50, "capital": 60}
    w = {"sentiment": 0.2, "news": 0.25, "tech": 0.25, "fundamental": 0.15, "capital": 0.15}
    expected = round(0.2 * 80 + 0.25 * 60 + 0.25 * 70 + 0.15 * 50 + 0.15 * 60, 1)
    score, reasons = synthesize(sub, w)
    assert score == expected and reasons == []
    score2, reasons2 = synthesize(sub, w, vetoes=["风控强空"])
    assert abs(score2 - expected * 0.4) < 0.05
    assert "一票否决" in reasons2[0]


def test_replacement_threshold_keeps_stable():
    """昨日成员（60 分）保留：新候选 70 分 < 60+15=75，不够强不换。"""
    ranked = [
        {"symbol": "NEW1", "score": 78},
        {"symbol": "OLD1", "score": 60},
        {"symbol": "NEW2", "score": 70},
    ]
    kept, replaced = apply_replacement_threshold(["OLD1"], ranked, max_picks=2)
    assert [k["symbol"] for k in kept] == ["NEW1", "OLD1"]  # NEW1 够强进；NEW2 差 10 分不换
    assert replaced == []


def test_replacement_threshold_allows_strong_replacement():
    """新候选 80 分 > 60+15：替换发生且记录 delta。"""
    ranked = [
        {"symbol": "NEW1", "score": 80},
        {"symbol": "OLD1", "score": 60},
    ]
    kept, replaced = apply_replacement_threshold(["OLD1"], ranked, max_picks=1)
    assert [k["symbol"] for k in kept] == ["NEW1"]
    assert replaced == [{"out": "OLD1", "in": "NEW1", "delta": 20.0}]


def test_replacement_threshold_default_is_15():
    assert REPLACE_THRESHOLD == 15.0


def test_buy_range_converges_with_technical_levels():
    r = build_buy_range(price=100.0, support=98.5, resistance=104.0)
    assert r["low"] == 98.5 and r["high"] == 103.0  # max(97, 98.5), min(103, 104)
    assert "不构成买卖建议" in r["basis"]


def test_buy_range_falls_back_to_plain_band_on_disjoint():
    r = build_buy_range(price=100.0, support=105.0, resistance=106.0)  # 支撑高于 ±3% 上界 → 无交集回退
    assert r["low"] == 97.0 and r["high"] == 103.0


def test_classify_review_categories():
    assert classify_review(3.0)[0] == "good"
    assert classify_review(-3.0)[0] == "bad"
    assert classify_review(0.5)[0] == "flat"
    assert classify_review(-3.0, "board_receding")[1].startswith("板块退潮")


def test_classify_review_reason_categories_complete():
    from app.picks.engine import REASON_CATEGORIES

    for cat in ("event_expired", "board_receding", "market_drag", "data_issue", "news_gap", "logic_failed", "gone_well"):
        assert cat in REASON_CATEGORIES
