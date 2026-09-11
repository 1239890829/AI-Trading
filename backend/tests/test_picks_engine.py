"""每日精选引擎测试。

核心契约：
- 综合分 = 五维加权，全程带 basis（可解释纪律）
- 换股门槛：新候选分差不足不换（组合稳定性的机制保证）
- 买入范围 = 现价±3% 与技术位收敛，且显式声明"不构成买卖建议"
- 复盘归类枚举完整
"""

from __future__ import annotations

import pytest

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


def test_fundamental_profit_quality_adjusts():
    # 净利同比 >20% +5；<0 −8（增收不增利在 basis 里点名）
    s, basis = score_fundamental(20.0, 30.0, profit_yoy=25.0)
    assert s == 90.0
    assert "净利同比 +25.0%" in basis
    s2, basis2 = score_fundamental(20.0, 30.0, profit_yoy=-10.0)
    assert s2 == 77.0
    assert "增收不增利" in basis2
    # 营收也负时表述改为"利润下滑"，不冤枉
    _, basis3 = score_fundamental(20.0, -5.0, profit_yoy=-10.0)
    assert "利润下滑" in basis3 and "增收不增利" not in basis3


def test_fundamental_roe_and_gross_margin():
    # ROE：≥15 +8 / 8-15 +4 / <0 −8；毛利率：≥40 +6 / 20-40 +3（只加分不扣分）
    s, _ = score_fundamental(20.0, None, roe=18.0, gross_margin=45.0)
    assert s == 84.0
    s2, _ = score_fundamental(20.0, None, roe=10.0, gross_margin=25.0)
    assert s2 == 77.0
    s3, basis3 = score_fundamental(20.0, None, roe=-5.0)
    assert s3 == 62.0
    assert "亏损" in basis3
    # 低毛利率不扣分（行业属性差异大）
    assert score_fundamental(20.0, None, gross_margin=5.0)[0] == 70.0


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


# ---------------------------------------------------------------- 入选门槛（2026-09-10）
# 用户要求：「盘前不一定是要五个，不要硬凑五个，但也不要过多，按实际情况来选」。
# 判据：名额由质量（MIN_PICK_SCORE 绝对线）决定，不由常量（MAX_PICKS 容量）决定。


def test_min_pick_score_anchored_at_neutral():
    """门槛锚在六维中性线 50——各维兜底分都是 50，故 ≥50 ⇔ 多维证据整体净正面。"""
    from app.picks.engine import MIN_PICK_SCORE

    assert MIN_PICK_SCORE == 50.0


def test_entry_floor_does_not_pad_to_max_picks():
    """够格者不足时名单就该更短：7 只候选只有 2 只过线 → 输出 2 只（不拿低分凑满 5）。"""
    from app.picks.engine import apply_replacement_threshold

    ranked = [
        {"symbol": "A", "score": 62.0},
        {"symbol": "B", "score": 51.0},
        {"symbol": "C", "score": 49.9},
        {"symbol": "D", "score": 45.0},
        {"symbol": "E", "score": 41.0},
    ]
    kept, _ = apply_replacement_threshold([], ranked, 15.0, 5)
    assert [k["symbol"] for k in kept] == ["A", "B"]


def test_entry_floor_drops_carryover_below_floor():
    """跌破门槛的昨日成员出列——门槛语义是「在组合里就必须够格」，
    昨天在列不是豁免理由（否则今日名单退化成昨日名单的惯性延续）。"""
    from app.picks.engine import apply_replacement_threshold

    prev = ["A", "B", "C"]
    ranked = [
        {"symbol": "A", "score": 58.0},
        {"symbol": "B", "score": 49.9},   # 跌破门槛 → 出列
        {"symbol": "C", "score": 44.0},   # 跌破门槛 → 出列
    ]
    kept, replaced = apply_replacement_threshold(prev, ranked, 15.0, 5)
    assert [k["symbol"] for k in kept] == ["A"]
    assert replaced == []  # 出列不是「换股」（无换入方），不进 replaced


def test_entry_floor_yields_empty_combo_when_pool_all_below():
    """候选池整体不够格 → 组合为空（宁缺毋滥），不塞一只 49 分的来「凑名单」。

    弱市里空名单本身是结论——空仓闸门管"今天要不要出手"，入选门槛管"单只够不够格"，
    粒度不同、同源逻辑（gate.py：没有赚钱效应的市场里任何精选都是硬凑）。
    """
    from app.picks.engine import apply_replacement_threshold

    ranked = [
        {"symbol": "LOW1", "score": 49.0},
        {"symbol": "LOW2", "score": 48.0},
    ]
    kept, replaced = apply_replacement_threshold(["OLD"], ranked, 1.0, 1)
    # OLD 掉出候选池 → 无留任；两个候选都不过线 → 组合为空而不是塞一只 49 分的
    assert kept == []
    assert replaced == []


def test_entry_floor_all_qualified_still_capped_by_max_picks():
    """门槛与容量各管一头：过线者多于容量时，仍按分数取前 max_picks。"""
    from app.picks.engine import apply_replacement_threshold

    ranked = [{"symbol": s, "score": 90.0 - i} for i, s in enumerate(["A", "B", "C", "D", "E", "F"])]
    kept, _ = apply_replacement_threshold([], ranked, 15.0, 5)
    assert [k["symbol"] for k in kept] == ["A", "B", "C", "D", "E"]


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


def test_combo_stays_full_when_prev_members_drop_out():
    """回归（2026-08-31 跨日回放实测发现的缺陷）：
    昨日成员掉出候选池后，组合要能**补位**而不是让门槛把空位锁死。

    原实现把换股门槛也套在补位上：留下的高分成员把门槛抬高，新候选永远
    够不着 → 6 天回放里组合从 5 只缩到 1 只。门槛只该约束"替换"，不该阻止"填空"。
    """
    from app.picks.engine import apply_replacement_threshold

    prev = ["A", "B", "C", "D", "E"]
    ranked = [
        {"symbol": "A", "score": 90.0},
        {"symbol": "N1", "score": 75.0},
        {"symbol": "N2", "score": 74.0},
        {"symbol": "N3", "score": 73.0},
        {"symbol": "N4", "score": 72.0},
        {"symbol": "N5", "score": 71.0},
    ]
    # 不设每日换股上限时：空位全部补上（A + N1..N4），且无人被踢
    kept, replaced = apply_replacement_threshold(prev, ranked, 15.0, 5, max_swaps=None)
    assert [k["symbol"] for k in kept] == ["A", "N1", "N2", "N3", "N4"]
    assert replaced == []


def test_daily_swap_cap_limits_turnover():
    """每日换股上限：极端情况（昨日成员几乎全消失）也只换入 2 只。

    取舍：稳定性优先于"每天都满员"——成员消失后渐进补位（分几天补满），
    好过一天之内把组合全换成陌生的票。
    """
    from app.picks.engine import MAX_SWAPS_PER_DAY, apply_replacement_threshold

    assert MAX_SWAPS_PER_DAY == 2
    prev = ["A", "B", "C", "D", "E"]
    ranked = [
        {"symbol": "A", "score": 90.0},
        {"symbol": "N1", "score": 75.0},
        {"symbol": "N2", "score": 74.0},
        {"symbol": "N3", "score": 73.0},
    ]
    kept, _ = apply_replacement_threshold(prev, ranked, 15.0, 5)
    assert [k["symbol"] for k in kept] == ["A", "N1", "N2"]


def test_swap_cap_does_not_apply_to_first_build():
    """首次建仓（无昨日组合）不受换股上限约束——否则第一天只能选出 2 只。"""
    from app.picks.engine import apply_replacement_threshold

    ranked = [{"symbol": s, "score": 90.0 - i} for i, s in enumerate(["A", "B", "C", "D", "E"])]
    kept, _ = apply_replacement_threshold([], ranked, 15.0, 5)
    assert len(kept) == 5


# ---------------------------------------------------------------- 选股 2.0 打分增强（2026-09-02）


def test_score_sentiment_promo_percentile_adjustment():
    """晋级率分位 ±10 修正：历史低位压分、高位加分；缺失时不修正。"""
    from app.picks.engine import score_sentiment

    base, _ = score_sentiment("发酵", None)
    low, b_low = score_sentiment("发酵", None, promo_percentile=10.0)
    high, b_high = score_sentiment("发酵", None, promo_percentile=90.0)
    assert low == pytest.approx(base - 8.0)  # (10-50)*0.2 = -8
    assert high == pytest.approx(base + 8.0)
    assert "晋级率历史分位" in b_low
    none_score, b_none = score_sentiment("发酵", None, promo_percentile=None)
    assert none_score == pytest.approx(base)
    assert "晋级率" not in b_none


def test_score_sentiment_promo_clamped():
    """退潮 30×0.7=21 + 修正 −8 → 13；不越界即可。"""
    from app.picks.engine import score_sentiment

    s, _ = score_sentiment("退潮", None, promo_percentile=0.0)
    assert 0 <= s <= 100


def test_event_weight_tier_and_certainty():
    """tier1 官方落地 = 1.0；tier5 传闻 = 0.06；缺失按中性档不冒充已判。"""
    from app.picks.engine import CERTAINTY_WEIGHTS, TIER_WEIGHTS, event_weight

    assert event_weight(1, "done") == pytest.approx(1.0)
    assert event_weight(5, "rumor") == pytest.approx(0.06)  # 0.2 × 0.3
    assert event_weight(None, None) == pytest.approx(TIER_WEIGHTS[3] * CERTAINTY_WEIGHTS["done"])
    # 未知档位逐项回落中性档（0.6 × 0.6 = 0.36），不抛异常也不给满分
    assert event_weight(9, "nonsense") == pytest.approx(0.36)


def test_news_weighted_strength_separates_official_from_rumor():
    """同样 3 点强度：官方来源得分显著高于传闻来源（同向利好）。"""
    from app.picks.engine import score_news

    s_official, _ = score_news(3, 0, "政策落地", "利好")   # tier1 done：3×1.0
    s_rumor, _ = score_news(3 * 0.04, 0, "群里传的", "利好")  # tier5 rumor：3×0.04
    assert s_official > s_rumor + 10


def test_news_negative_dominates_positive():
    from app.picks.engine import score_news

    s, _ = score_news(2, 3, "暴雷", "利空")
    assert s < 50


# ---------------------------------------------------------------- 运行时参数覆盖（P1-15，2026-09-10）
# 目的：控制台改参数**免重启**生效，且覆盖层为空时行为与改动前完全一致。


def test_engine_reads_runtime_overrides(monkeypatch):
    """覆盖层生效：三个阈值都按覆盖值走（默认值参数在**调用时**解析，不是签名绑定）。"""
    import app.core.runtime_params as rp
    from app.picks.engine import apply_replacement_threshold

    rp.clear()
    try:
        # 默认：门槛 50 → 49 分候选进不来、只有 1 只
        ranked = [{"symbol": "A", "score": 70.0}, {"symbol": "B", "score": 49.0}]
        kept, _ = apply_replacement_threshold([], ranked)
        assert [k["symbol"] for k in kept] == ["A"]

        # 覆盖入选门槛 40 → 49 分也能进
        rp.set_overrides({"picks_min_pick_score": 40.0})
        kept, _ = apply_replacement_threshold([], ranked)
        assert [k["symbol"] for k in kept] == ["A", "B"]

        # 覆盖换股上限 0 → 昨日成员一个都不换（新候选再高也不换）
        rp.set_overrides({"picks_max_swaps_per_day": 0})
        prev = ["OLD"]
        ranked2 = [{"symbol": "NEW", "score": 99.0}, {"symbol": "OLD", "score": 60.0}]
        kept, _ = apply_replacement_threshold(prev, ranked2)
        assert [k["symbol"] for k in kept] == ["OLD"]
    finally:
        rp.clear()
    # 清空后 = 代码常量
    kept, _ = apply_replacement_threshold([], [{"symbol": "B", "score": 49.0}])
    assert kept == []


def test_max_swaps_none_still_means_unlimited_under_override():
    """哨兵回归：覆盖层启用时，显式 `max_swaps=None` 仍表示**不限换股**。

    `None` 本来就有语义（回放对照组/首次建仓用），不能拿它兼职表示"未传参"
    ——否则覆盖层一启用就会把"不限"悄悄变成"上限"（静默改变回放结论）。
    """
    import app.core.runtime_params as rp
    from app.picks.engine import apply_replacement_threshold

    rp.clear()
    try:
        rp.set_overrides({"picks_max_swaps_per_day": 0})
        ranked = [{"symbol": s, "score": 90.0 - i} for i, s in enumerate(["A", "B", "C"])]
        kept, _ = apply_replacement_threshold(["OLD"], ranked, max_swaps=None)
        assert [k["symbol"] for k in kept] == ["A", "B", "C"]  # 不限 → 补满
    finally:
        rp.clear()


def test_effective_limits_report_overrides():
    """meta 里的阈值必须是**生效值**，否则调参后复盘看到的仍是旧数（口径漂移）。"""
    import app.core.runtime_params as rp
    from app.picks.engine import (
        MAX_SWAPS_PER_DAY, MIN_PICK_SCORE, REPLACE_THRESHOLD, effective_limits,
    )

    rp.clear()
    base = effective_limits()
    assert base == {
        "replace_threshold": REPLACE_THRESHOLD,
        "max_swaps_per_day": MAX_SWAPS_PER_DAY,
        "min_pick_score": MIN_PICK_SCORE,
    }
    try:
        rp.set_overrides({"picks_replace_threshold": 8.0, "picks_max_swaps_per_day": 3})
        eff = effective_limits()
        assert eff["replace_threshold"] == 8.0 and eff["max_swaps_per_day"] == 3
        assert eff["min_pick_score"] == MIN_PICK_SCORE  # 未覆盖的项回落常量
    finally:
        rp.clear()
