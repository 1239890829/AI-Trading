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


# ---------------------------------------------------------------- 买点质量与失败归因


def _entry(**kw):
    from app.picks.engine import review_entry_quality

    defaults = dict(
        buy_range={"low": 9.7, "high": 10.3},
        day_open=10.0,
        day_high=11.0,
        day_low=9.5,
        day_close=10.8,
    )
    defaults.update(kw)
    return review_entry_quality(**defaults)


def test_entry_quality_filled_at_open_when_inside_range():
    """开盘在买入区间内 → 成本按开盘价，介入成功。"""
    e = _entry()
    assert e["filled"] is True
    assert e["entry_cost"] == 10.0
    assert e["entry_pnl_pct"] == 8.0
    assert e["open_pnl_pct"] == 8.0
    assert e["advantage_pct"] == 0.0


def test_entry_quality_cost_capped_at_range_high():
    """高开越过区间上沿 → 按纪律只按上沿计价，不美化收益。

    本例：高开 11.0 后回落收 10.5。追开盘亏 4.55%，按上沿 10.3 计入则盈 1.94%
    ——差额正是"不追高"的价值，advantage 为正。
    """
    e = _entry(day_open=11.0, day_close=10.5)
    assert e["entry_cost"] == 10.3
    assert e["open_pnl_pct"] == -4.55
    assert e["entry_pnl_pct"] == 1.94
    assert e["advantage_pct"] > 0  # 纪律介入优于追高


def test_entry_quality_not_filled_is_missed_not_failure():
    """全天高于买入区间上沿 → 未介入，属踏空而非选股失误。"""
    e = _entry(day_open=11.0, day_high=11.5, day_low=10.5, day_close=11.2)
    assert e["filled"] is False
    assert "未介入" in e["basis"]
    from app.picks.engine import classify_failure

    cat, note = classify_failure(excess_pct=5.0, entry=e)
    assert cat == "missed"
    assert "踏空" in note


def test_entry_quality_unknown_without_buy_range():
    """空仓闸门撤除买入范围 / 行情缺失 → 不可评（不硬凑一个结论）。"""
    e = _entry(buy_range=None)
    assert e["filled"] is None
    assert "不可评" in e["basis"]


def test_classify_failure_separates_entry_problem_from_logic_failure():
    """按买点介入明显优于追高 → 归「买点不对」，不是选股逻辑失效。"""
    from app.picks.engine import classify_failure

    # 冲高回落：盘中冲到 11.5，收盘跌回 9.6（低于开盘 10.5）
    e = _entry(
        buy_range={"low": 9.7, "high": 10.3},
        day_open=10.5,
        day_high=11.5,
        day_low=9.4,
        day_close=9.6,
    )
    cat, note = classify_failure(excess_pct=-4.0, entry=e)
    assert cat == "entry_bad"
    assert "买点" in note


def test_classify_failure_sentiment_misread_when_phase_weak():
    """持有期市场相位退潮 → 归情绪误判（个股再强也难逆势）。"""
    from app.picks.engine import classify_failure

    # 开盘落在买入区间内（成本=开盘，advantage=0 → 不会被归为买点问题），
    # 且最低价在区间内才算介入；此时走坏只可能是情绪或逻辑问题
    e = _entry(day_open=10.0, day_high=10.2, day_low=9.5, day_close=9.2)
    assert e["filled"] is True
    assert e["advantage_pct"] == 0.0
    cat, note = classify_failure(excess_pct=-5.0, entry=e, market_phase="退潮")
    assert cat == "sentiment_misread"
    assert "退潮" in note


def test_classify_failure_falls_back_to_logic_failed():
    from app.picks.engine import classify_failure

    e = _entry(day_open=10.0, day_high=10.2, day_low=9.0, day_close=9.2)
    cat, _ = classify_failure(excess_pct=-5.0, entry=e, market_phase="发酵")
    assert cat == "logic_failed"


def test_classify_failure_good_and_flat():
    from app.picks.engine import classify_failure

    e = _entry()
    assert classify_failure(excess_pct=3.0, entry=e)[0] == "gone_well"
    assert classify_failure(excess_pct=0.5, entry=e)[0] == "gone_well"


def test_entry_quality_distinguishes_gate_day_from_missing_data():
    """闸门日"没给买点"与"数据缺失"必须区分——前者是设计，后者是缺陷。"""
    e = _entry(buy_range=None, observation_only=True)
    assert e["filled"] is None
    assert "空仓闸门日" in e["basis"]
    assert "不适用买点评析" in e["basis"]
    # 同样无买入范围但非闸门日 → 归为数据缺失
    assert "无买入范围或行情缺失" in _entry(buy_range=None)["basis"]
