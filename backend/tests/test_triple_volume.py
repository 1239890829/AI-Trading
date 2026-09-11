"""三倍量战法信号识别的回归测试（用户 2026-09-10 提出的策略）。

本组测试锁的是**规则语义**，不是有效性——有效性属回测范畴（见模块 docstring 的未验证声明）。
重点覆盖三处最容易写错的地方：①基线样本不足时不得"硬算"；②止损是**收盘口径**且为严格小于；
③突破当天最低价缺失时的退化必须显式告警（不能静默用一个错的止损位）。
"""
from __future__ import annotations

from app.picks.triple_volume import (
    VOL_LOOKBACK,
    avg_volume,
    is_stopped_out,
    is_triple_volume,
    plan_left,
    plan_right,
)


# ---------------------------------------------------------------- 基线

def test_avg_volume_needs_full_window():
    """样本不足 → None：**不按剩余样本硬算**（否则停牌复牌票会被算出虚低基线，凭空造出三倍量）。"""
    assert avg_volume([100.0, 100.0] * 2) is None
    assert avg_volume(None) is None
    assert avg_volume([]) is None
    assert avg_volume([100.0] * VOL_LOOKBACK) == 100.0


def test_avg_volume_rejects_non_positive_window():
    """窗口内出现 0/负（停牌、脏数据）→ 整窗作废，不按剩余有效值平均。"""
    assert avg_volume([100.0, 0.0, 100.0, 100.0, 100.0]) is None
    assert avg_volume([100.0, -5.0, 100.0, 100.0, 100.0]) is None


# ---------------------------------------------------------------- 三倍量识别

def test_triple_volume_hit_and_miss():
    base = [100.0] * VOL_LOOKBACK
    hit = is_triple_volume(vol_today=300.0, vols_prev=base)
    assert hit["is_triple"] is True and hit["ratio"] == 3.0
    # 严格 >= 门槛：299 不算，300 算
    assert is_triple_volume(vol_today=299.0, vols_prev=base)["is_triple"] is False
    assert is_triple_volume(vol_today=300.0, vols_prev=base)["is_triple"] is True


def test_triple_volume_unknown_when_data_missing():
    """量能缺失 → None（判不出来），绝不冒充 False（那会把 unknown 混进"确认没有三倍量"）。"""
    assert is_triple_volume(vol_today=None, vols_prev=[100.0] * VOL_LOOKBACK)["is_triple"] is None
    assert is_triple_volume(vol_today=300.0, vols_prev=None)["is_triple"] is None
    # K 线不足
    assert is_triple_volume(vol_today=300.0, vols_prev=[100.0] * 3)["is_triple"] is None


# ---------------------------------------------------------------- 左侧低吸

def test_plan_left_ready_when_touch_and_hold():
    p = plan_left(price=10.05, triple_low=10.0, triple_close=10.4)
    assert p["ready"] is True
    assert p["buy_range"] == [10.0, 10.1]      # 最低价 ~ +1%
    assert p["stop"] == 10.0                    # 止损=三倍量K线最低价


def test_plan_left_rejects_when_close_broke_low():
    """收盘跌破最低价 → 不成立（这正是用户规则的"收盘价未跌破"条件）。"""
    p = plan_left(price=9.90, triple_low=10.0, triple_close=9.95)
    assert p["ready"] is False
    assert any("收盘未跌破" in r for r in p["unmet"])


def test_plan_left_unknown_when_price_missing():
    p = plan_left(price=None, triple_low=10.0, triple_close=10.4)
    assert p["ready"] is False
    assert any("判不出来" in r for r in p["unmet"])


def test_plan_left_holding_gives_no_buy_range():
    """已持有时只复核止损，不再给买区（避免"已持有还给买点"的语义错配）。"""
    p = plan_left(price=10.0, triple_low=10.0, triple_close=10.4, holding=True)
    assert p["buy_range"] is None and p["stop"] == 10.0


# ---------------------------------------------------------------- 右侧突破

def test_plan_right_ready_on_breakout_of_triple_high():
    p = plan_right(price=11.0, triple_high=10.8, breakout_low=10.3)
    assert p["ready"] is True
    assert p["buy_range"] == [10.8, round(10.8 * 1.015, 2)]   # 突破价 ~ +1.5%（不手算，交给表达式）
    assert p["stop"] == round(10.3 * 0.99, 2)   # 止损=**突破当天**最低价下方 1%


def test_plan_right_not_ready_below_high():
    p = plan_right(price=10.5, triple_high=10.8, breakout_low=10.3)
    assert p["ready"] is False and any("不满足" in r for r in p["unmet"])


def test_plan_right_warns_when_breakout_low_missing():
    """突破当天最低价缺失 → 退化为三倍量最低价，且**必须显式告警**（不静默换口径）。"""
    p = plan_right(price=11.0, triple_high=10.8, breakout_low=None)
    assert p["ready"] is True
    assert "人工复核" in p["note"]


# ---------------------------------------------------------------- 止损（收盘口径）

def test_stop_is_close_based_and_strict():
    """止损=收盘口径 + 严格小于：等于止损价**不算破位**（用户规则原话「收盘价跌破」）。"""
    assert is_stopped_out(close=9.99, stop=10.0) is True
    assert is_stopped_out(close=10.0, stop=10.0) is False   # 等于不破
    assert is_stopped_out(close=10.01, stop=10.0) is False
    # 数据缺失 → 判不出来，不臆造"安全"
    assert is_stopped_out(close=None, stop=10.0) is None
    assert is_stopped_out(close=10.0, stop=None) is None
