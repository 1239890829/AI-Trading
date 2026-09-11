"""情绪引擎 v2 测试。

测试哲学（沿用 vibe-astock）：**重点在那些"错了也看不出来"的地方**——
界面照常渲染、数字看着合理、但结论是错的。

最典型的就是 2026-08-29 那次事故：线上输出「高潮 / 温度 89.2 / 置信度高」，
依据"翻红率 100%、再涨停率 100%"。数字完全自洽，只是口径错了。
下面 `test_*_unreliable*` 一组用例专门锁死这类错误。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.schemas.market import LimitUpRecord
from app.sentiment.engine import (
    compute_sentiment,
    decide_phase,
    earning_axis,
    heat_axis,
    prev_zt_performance,
    promotion_rates,
    self_check,
)

T = date(2026, 8, 28)
T1 = date(2026, 8, 27)


def _rec(symbol: str, boards: int) -> LimitUpRecord:
    return LimitUpRecord(
        symbol=symbol, name="某股", trade_date="2026-08-28",
        consecutive_boards=boards, source="test",
    )


def _row(symbol: str, pct: float, name: str = "某股") -> dict:
    return {"symbol": symbol, "name": name, "price": 10.0, "change_pct": pct, "amount": 1e8}


def _breadth(limit_up=90, up=3000, down=2400, limit_down=3):
    return {
        "total": 5550, "up": up, "down": down, "flat": 100, "suspended": 4,
        "limit_up": limit_up, "limit_down": limit_down,
        "limit_up_ratio": 0.03, "total_amount": 2e11,
    }


def _run(**kw):
    base = dict(
        breadth=_breadth(), pool_today=[], pool_yesterday=[], snapshot=[],
        trade_date=T, prev_trade_date=T1,
    )
    base.update(kw)
    return compute_sentiment(**base)


# ---------------------------------------------------------------- 分轴

def test_heat_axis_monotonic_in_limit_up():
    """涨停家数越多热度越高。"""
    a = heat_axis(20, 3, 0.3)
    b = heat_axis(50, 3, 0.3)
    c = heat_axis(85, 3, 0.3)
    assert a["raw"] < b["raw"] < c["raw"]


def test_heat_axis_high_break_rate_lowers_score():
    """炸板率高说明封板不牢，热度应当被扣减。"""
    assert heat_axis(60, 5, 0.45)["raw"] < heat_axis(60, 5, 0.15)["raw"]


def test_earning_axis_promotion_is_most_negative_when_broken():
    """1进2 崩到 15% 以下必须给出强负分——它是接力的命门。"""
    low = earning_axis(0.10, 1.0, 0.6, 3)
    high = earning_axis(0.45, 1.0, 0.6, 3)
    assert low["raw"] < 0 < high["raw"]
    assert "1进2晋级率" in low["basis"][0]


def test_earning_axis_missing_values_do_not_punish():
    """缺项不应被当成坏消息（否则快照缺失会把一切判成退潮）。"""
    missing = earning_axis(None, None, None, None)
    assert missing["raw"] == 0, "缺失值应记 0 分，既不奖励也不惩罚"


def test_phase_matrix_heat_cannot_override_earning():
    """核心回归：热度拉满但赚钱效应偏弱 → 只能是分歧，不能是高潮。

    上一轮事故的结构性根因就是让热度指标（家数/高度）压过赚钱效应。
    """
    assert decide_phase(3, 1)[0] == "分歧"
    assert decide_phase(3, 3)[0] == "高潮"
    assert decide_phase(2, 1)[0] == "分歧"


# ---------------------------------------------------------------- 晋级率

def test_promotion_rates_basic():
    y = [_rec("600001", 1), _rec("600002", 1), _rec("600003", 2), _rec("600004", 5)]
    t = [_rec("600001", 2), _rec("600003", 3), _rec("600004", 6)]
    p = promotion_rates(t, y)
    assert p["promo_1to2"] == pytest.approx(0.5)   # 1/2 晋级
    assert p["promo_2to3"] == pytest.approx(1.0)   # 1/1
    assert p["high_survival"] == pytest.approx(1.0)
    assert p["pool_survival"] == pytest.approx(0.75)  # 3/4 仍涨停


def test_promotion_rates_empty_base_returns_none():
    """昨日无 1 板股时，1进2 应为 None 而不是 0——0 会被当成"接力全灭"。"""
    y = [_rec("600003", 3)]
    p = promotion_rates([_rec("600003", 4)], y)
    assert p["promo_1to2"] is None
    assert p["promo_1to2_base"] == 0


def test_promotion_not_inflated_by_new_first_boards():
    """今日新首板不应被算进晋级分子。"""
    y = [_rec("600001", 1)]
    t = [_rec("600001", 1), _rec("600009", 1)]  # 600001 未晋级，600009 是新首板
    assert promotion_rates(t, y)["promo_1to2"] == pytest.approx(0.0)


# ---------------------------------------------------------------- 自指 / 哨兵

def test_self_check_flags_100pct_re_limit():
    """再涨停率 100% 在真实市场不可能出现 → 必须被标记为不可信。"""
    issues = self_check({"re_limit_rate": 1.0, "red_rate": 0.6, "median_pct": 1.0}, T, T1)
    assert any("再涨停率" in i for i in issues)


def test_self_check_flags_100pct_red_rate():
    issues = self_check({"re_limit_rate": 0.2, "red_rate": 1.0, "median_pct": 1.0}, T, T1)
    assert any("翻红率" in i for i in issues)


def test_self_check_flags_non_increasing_dates():
    """T ≤ T-1 说明日期锚定失败，是自指计算的直接前兆。"""
    issues = self_check({"re_limit_rate": 0.2, "red_rate": 0.6, "median_pct": 1.0},
                        date(2026, 8, 28), date(2026, 8, 28))
    assert any("日期未严格递增" in i for i in issues)


def test_self_check_clean_when_normal():
    assert self_check({"re_limit_rate": 0.23, "red_rate": 0.62, "median_pct": 1.97}, T, T1) == []


def test_self_referential_data_marks_result_unreliable():
    """端到端：复刻 8/29 事故（同一份池既当今日又当昨日）。

    旧引擎会输出「高潮 / 置信度高」；新引擎必须把它标成不可信 + 低置信度。
    """
    pool = [_rec(f"6000{i:02d}", 3) for i in range(20)]
    snap = [_row(r.symbol, 10.0) for r in pool]  # 涨停股查自己涨停当天的收盘价
    s = _run(breadth=_breadth(limit_up=89), pool_today=pool, pool_yesterday=pool,
             snapshot=snap, trade_date=T, prev_trade_date=T)
    assert s["phase_unreliable"] is True
    assert s["confidence"] == "低"
    assert s["self_check"], "必须列出具体的自指证据"


# ---------------------------------------------------------------- 真实场景回归

def _build_0828_case():
    """按 docs/sentiment.md「历史误判案例库」§2.3 的实测口径构造 2026-08-28 场景。

    已知真值：涨停 82（封单法）· 最高板 7 · 炸板率 16.3%
            1进2 16.4%(10/61) · 昨涨停中位 +1.97% · 翻红率 62.3% · 跌停 4
    人工复核结论：**分歧**（高位分歧），不是高潮。
    """
    # 昨日池：61 首板 + 8 二板 + 8 高标(含 1 只 6 板)
    y1 = [_rec(f"6001{i:03d}", 1) for i in range(61)]
    y2 = [_rec(f"6002{i:03d}", 2) for i in range(8)]
    y3 = [_rec("6003000", 6)] + [_rec(f"6003{i:03d}", 3) for i in range(1, 8)]
    pool_y = y1 + y2 + y3

    # 今日池：10 只首板晋级(16.4%) + 3 只二板晋级 + 5 只高标存活(含 7 板) + 64 只新首板
    t_promoted = [_rec(y1[i].symbol, 2) for i in range(10)]
    t_y2 = [_rec(y2[i].symbol, 3) for i in range(3)]
    t_y3 = [_rec("6003000", 7)] + [_rec(y3[i].symbol, 4) for i in range(1, 5)]
    t_new = [_rec(f"0004{i:03d}", 1) for i in range(64)]
    pool_t = t_promoted + t_y2 + t_y3 + t_new

    # 快照：77 个值，中位 1.97（第 39 个）、翻红 48/77=62.3%
    pcts = [-3.0] * 29 + [1.0] * 9 + [1.97] + [5.0] * 38
    snap = [_row(r.symbol, pcts[i]) for i, r in enumerate(pool_y)]

    assert len(pool_t) == 82, len(pool_t)
    return pool_t, pool_y, snap


def test_0828_real_case_is_divergence_not_climax():
    """最重要的一条：用真实数据锁死「分歧」结论，防止热度指标再次压过赚钱效应。"""
    pool_t, pool_y, snap = _build_0828_case()
    s = _run(
        breadth=_breadth(limit_up=89, up=3013, down=2389, limit_down=4),
        pool_today=pool_t, pool_yesterday=pool_y, snapshot=snap,
        break_count=16, max_board_prev=6,
    )
    assert s["prev_perf"]["median_pct"] == pytest.approx(1.97)
    assert s["prev_perf"]["red_rate"] == pytest.approx(0.623, abs=0.01)
    assert s["promotion"]["promo_1to2"] == pytest.approx(0.164, abs=0.01)
    assert s["heat"]["level"] == 3, "热度确已到达高位"
    assert s["earning"]["level"] == 1, "但赚钱效应只是偏弱"
    assert s["phase"] == "分歧"
    assert s["phase"] != "高潮", "热度再高，赚钱效应跟不上就不能判高潮"


def test_0828_not_retreat_because_height_still_expanding():
    """8/28 不是退潮：最高板仍在拓展（6→7）、炸板率仅 16%、高标存活 62.5%。"""
    pool_t, pool_y, snap = _build_0828_case()
    s = _run(
        breadth=_breadth(limit_up=89, up=3013, down=2389, limit_down=4),
        pool_today=pool_t, pool_yesterday=pool_y, snapshot=snap,
        break_count=16, max_board_prev=6,
    )
    assert s["phase"] != "退潮"
    assert s["promotion"]["high_survival"] == pytest.approx(0.625, abs=0.01)


def test_stratified_returns_expose_divergence():
    """分层收益：高标中位远高于首板 → 资金抱团、首板一日游，分化的直证。"""
    pool_t, pool_y, snap = _build_0828_case()
    perf = prev_zt_performance(pool_y, snap)
    assert set(perf["by_board"]) == {"首板", "2板", "≥3板"}
    assert perf["by_board"]["首板"]["count"] == 61
    assert perf["by_board"]["≥3板"]["count"] == 8


def test_skew_positive_when_mean_exceeds_median():
    """均值-中位背离度：正值说明少数大涨拉高均值，多数人体感更差。"""
    pool_y = [_rec(f"6001{i:03d}", 1) for i in range(10)]
    snap = [_row(r.symbol, 1.0) for r in pool_y]
    snap[0]["change_pct"] = 20.0
    perf = prev_zt_performance(pool_y, snap)
    assert perf["skew_pct"] > 0
    assert perf["median_pct"] < perf["avg_pct"]


# ---------------------------------------------------------------- 各阶段

def test_ice_phase():
    pool_y = [_rec(f"6001{i:03d}", 1) for i in range(12)]
    snap = [_row(r.symbol, -4.0) for r in pool_y]
    s = _run(breadth=_breadth(limit_up=12, up=800, down=4500, limit_down=12),
             pool_today=[], pool_yesterday=pool_y, snapshot=snap, break_count=10)
    assert s["phase"] == "冰点"
    assert s["temperature"] < 45


def test_climax_requires_both_heat_and_earning():
    """高潮必须热度高**且**赚钱效应强，两者缺一不可。"""
    pool_y = [_rec(f"6001{i:03d}", 1) for i in range(40)]
    # 24 只晋级 = 60%，远超 40% 升温线；另有一只 7 板空间龙把高度打上去
    pool_t = (
        [_rec(pool_y[i].symbol, 2) for i in range(24)]
        + [_rec("600900", 7)]
        + [_rec(f"0005{i:03d}", 1) for i in range(60)]
    )
    snap = [_row(r.symbol, 6.0) for r in pool_y]  # 中位 6% > 4%
    s = _run(breadth=_breadth(limit_up=95, limit_down=2),
             pool_today=pool_t, pool_yesterday=pool_y, snapshot=snap, break_count=8)
    assert s["heat"]["level"] == 3
    assert s["earning"]["level"] == 3
    assert s["phase"] == "高潮"


def test_retreat_when_height_breaks_and_earning_collapses():
    pool_y = [_rec(f"6003{i:03d}", 3) for i in range(30)]
    snap = [_row(r.symbol, -4.5) for r in pool_y]
    pool_t = [_rec("600200", 3)] + [_rec(f"0006{i:03d}", 1) for i in range(45)]
    s = _run(breadth=_breadth(limit_up=62, limit_down=15),
             pool_today=pool_t, pool_yesterday=pool_y, snapshot=snap,
             break_count=20, max_board_prev=6)
    assert s["phase"] == "退潮"


def test_ice_vs_retreat_boundary():
    """冰点与退潮都表现为亏钱，区别在还有没有热度残留。

    最高板被打到 ≤2 且涨停 <25 家 = 跌无可跌（冰点）；
    仍有 3 板以上或涨停 ≥25 家 = 正在杀跌（退潮）。
    这两者混在一起会让"高位杀跌首日"被误报成"已经见底"。
    """
    pool_y = [_rec(f"6003{i:03d}", 3) for i in range(20)]
    snap = [_row(r.symbol, -4.0) for r in pool_y]
    bottom = _run(breadth=_breadth(limit_up=15, limit_down=12), pool_today=[],
                  pool_yesterday=pool_y, snapshot=snap, break_count=8)
    falling = _run(breadth=_breadth(limit_up=45, limit_down=12),
                   pool_today=[_rec("600200", 3)] + [_rec(f"0007{i:03d}", 1) for i in range(44)],
                   pool_yesterday=pool_y, snapshot=snap, break_count=20)
    assert bottom["phase"] == "冰点"
    assert falling["phase"] == "退潮"


def test_limit_down_pushes_toward_retreat():
    """跌停家数是退潮的核心信号，v1 完全缺失它。"""
    pool_y = [_rec(f"6001{i:03d}", 1) for i in range(20)]
    snap = [_row(r.symbol, 1.0) for r in pool_y]
    mild = _run(breadth=_breadth(limit_up=60, limit_down=2), pool_yesterday=pool_y,
                snapshot=snap, pool_today=[_rec(pool_y[i].symbol, 2) for i in range(6)])
    harsh = _run(breadth=_breadth(limit_up=60, limit_down=14), pool_yesterday=pool_y,
                 snapshot=snap, pool_today=[_rec(pool_y[i].symbol, 2) for i in range(6)])
    assert harsh["earning"]["raw"] < mild["earning"]["raw"]


# ---------------------------------------------------------------- 输出契约

def test_output_contract():
    s = _run(pool_today=[_rec("600001", 2)], pool_yesterday=[_rec("600002", 1)],
             snapshot=[_row("600002", 3.0)])
    for key in ("phase", "phase_unreliable", "temperature", "confidence", "phase_basis",
                "heat", "earning", "promotion", "prev_perf", "ladder", "indicators",
                "self_check", "misjudge_caveats", "switch_conditions", "verify_next",
                "trade_date", "prev_trade_date", "judged_at"):
        assert key in s, f"缺少输出字段 {key}"
    assert s["phase"] in {"冰点", "修复", "发酵", "高潮", "分歧", "退潮"}
    assert s["verify_next"], "每条判断必须带次日可验证条件"


def test_missing_snapshot_degrades_gracefully():
    """全市场快照缺失时置信度必须降级，而不是照常给出高置信度结论。"""
    s = _run(breadth={}, pool_today=[_rec("600001", 2)],
             pool_yesterday=[_rec("600002", 1)], snapshot=[])
    assert s["confidence"] == "低"
    assert any("快照缺失" in c for c in s["misjudge_caveats"])


def test_indicator_names_updated():
    s = _run(pool_today=[_rec("600001", 2)], pool_yesterday=[_rec("600002", 1)],
             snapshot=[_row("600002", 3.0)])
    names = {i["name"] for i in s["indicators"]}
    assert {"1进2 晋级率", "昨日涨停今日中位", "跌停家数"} <= names
