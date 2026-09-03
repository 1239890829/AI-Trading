"""tech_score 单元测试：七维权重和、形态判定口径（同前端）、防飞刀衰减。

2026-09-04 随 pattern 维（v2）新增。形态口径与前端 technical-analysis.ts 一致，
唯一差异：后端 Bar 无 change_pct，实体涨幅代替（见 tech_score 模块 docstring）。
"""

import sys

sys.path.insert(0, ".")

from app.market.tech_score import _WEIGHTS, SCORER_VERSION, patterns, score_stock


def _bar(o, c, v=1000.0, h=None, l=None):
    return {"ts": "", "open": o, "close": c, "high": h or max(o, c), "low": l or min(o, c), "volume": v}


def _up_trend_bars(n=60, start=10.0):
    """温和上行序列：MA 多头排列、无形态尾部（每日 +0.1）。"""
    return [_bar(round(start + 0.1 * i, 3), round(start + 0.1 * (i + 1), 3)) for i in range(n)]


def test_weights_sum_to_one_and_version_bumped():
    assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9
    assert set(_WEIGHTS) == {"trend", "macd", "kdj", "rsi", "volume", "liquidity", "pattern"}
    assert SCORER_VERSION == "v2"  # 改权重必须递增版本（tech_score 头注释约定）


# ---------------------------------------------------------------- patterns 纯判定

def _cannon_bars():
    """显式构造双响炮（实体涨幅口径）：A 大阳 +5.5%、B 小实体 0.09%、C 大阳 +5.75%。"""
    return [
        _bar(10.00, 10.55),            # A：(10.55-10.00)/10.00 = 5.5% ≥ 5%
        _bar(10.60, 10.61),            # B：body/open = 0.01/10.60 ≈ 0.09% < 2.5%
        _bar(10.61, 11.22),            # C：(11.22-10.61)/10.61 = 5.75% ≥ 5%
    ]


def test_patterns_cannon_and_stars():
    # 双响炮
    pat = patterns(_cannon_bars())
    assert pat is not None and pat["bias"] == "bull" and "双响炮" in pat["name"]

    # 早晨之星：阴线(实体>2%) - 星线(<1.2%) - 阳线收复 A 实体中点
    morning = [
        _bar(10.00, 9.70),   # A 阴 -3%
        _bar(9.65, 9.66),    # B 星线 body/open ≈ 0.1%
        _bar(9.60, 9.90),    # C 阳，收复中点 (10.00+9.70)/2=9.85 → 9.90 > 9.85
    ]
    pat = patterns(morning)
    assert pat is not None and pat["bias"] == "bull" and "早晨之星" in pat["name"]

    # 黄昏之星：阳线(>2%) - 星线 - 阴线跌破 A 实体中点
    evening = [
        _bar(10.00, 10.30),  # A 阳 +3%
        _bar(10.31, 10.30),  # B 星线
        _bar(10.28, 10.05),  # C 阴，中点 10.15 → 10.05 < 10.15
    ]
    pat = patterns(evening)
    assert pat is not None and pat["bias"] == "bear" and "黄昏之星" in pat["name"]

    # 无形态：平稳序列
    assert patterns([_bar(10.0, 10.02)] * 3) is None


def test_patterns_insufficient_bars_returns_none():
    assert patterns([_bar(10, 11)]) is None
    assert patterns([]) is None


# ---------------------------------------------------------------- score_stock 集成

def _score_with_tail(tail_bars, n_hist=70):
    """70 根平稳上行历史 + 指定尾部 3 根 → 评分卡。"""
    hist = _up_trend_bars(n_hist)
    return score_stock(hist + tail_bars)


def test_score_stock_pattern_dim_neutral_when_no_pattern():
    rep = _score_with_tail([_bar(20.0, 20.1), _bar(20.1, 20.2), _bar(20.2, 20.3)])
    assert rep is not None
    assert rep["dimensions"]["pattern"] == 0.5
    assert any(s["name"] == "形态" and s["bias"] == "neutral" for s in rep["signals"])


def test_score_stock_morning_star_bull_in_uptrend_scores_full():
    """上行趋势中出现早晨之星：pattern=1.0 且信号入卡。"""
    # A 阴 body/open = 0.45/20.00 = 2.25% > 2%；B 星线 0.05%；C 收复中点 19.775
    tail = [_bar(20.00, 19.55), _bar(19.50, 19.51), _bar(19.45, 19.90)]
    rep = _score_with_tail(tail)
    assert rep["dimensions"]["pattern"] == 1.0
    sig = next(s for s in rep["signals"] if s["name"] == "形态·早晨之星")
    assert sig["bias"] == "bull"


def test_score_stock_bull_pattern_decays_in_downtrend():
    """空头排列（长下行）+ 早晨之星：防飞刀衰减 → 0.3 neutral。"""
    # 长阴下行 70 根构造 bearish MA 排列（尾部 close ≈ 19.50 与 tail 衔接）
    hist = [_bar(30.0 - 0.15 * i, 30.0 - 0.15 * (i + 1)) for i in range(70)]
    tail = [_bar(19.50, 19.10), _bar(19.05, 19.06), _bar(19.00, 19.40)]
    rep = score_stock(hist + tail)
    assert rep is not None
    assert rep["dimensions"]["pattern"] == 0.3
    sig = next(s for s in rep["signals"] if s["name"] == "形态·早晨之星")
    assert sig["bias"] == "neutral" and "衰减" in sig["detail"]


def test_score_stock_evening_star_bear_zero():
    # A 阳 body/open = 0.45/20.00 = 2.25% > 2%；B 星线；C 跌破中点 20.225
    tail = [_bar(20.00, 20.45), _bar(20.46, 20.45), _bar(20.43, 19.98)]
    rep = _score_with_tail(tail)
    assert rep["dimensions"]["pattern"] == 0.0
    sig = next(s for s in rep["signals"] if s["name"] == "形态·黄昏之星")
    assert sig["bias"] == "bear"


def test_score_stock_short_sample_returns_none():
    assert score_stock(_up_trend_bars(30)) is None  # <60 根：次新/长停牌过滤
