"""量能语义层单测（P1-35）。

锁三类必然再发生的失效：
1. **方向搞反**——`vma20` 这类「基准量/当期量」指标与「量比」方向相反，
   直接混用会把缩量读成放量（本模块存在的主要理由）。
2. **判不出被贴标签**——缺数据/非正比值必须给 `unknown`，不可当「量能平稳」。
3. **词表漂移**——状态到中文的映射必须单点，`unknown` 不得被视为可读信号。
"""
from __future__ import annotations

import pytest

from app.market.volume_state import (
    BANDS,
    EXPAND_LINE,
    FLAT_EPS_PCT,
    describe,
    is_actionable,
    label,
    ratio_from_inverse,
    volume_state,
    volume_state_from_inverse,
)


# ---------------------------------------------------------------- 六态覆盖

def test_six_states_covered_by_ratio_and_direction():
    """六态 = 量比分区 × 价格方向；涨跌同量比必落到不同状态。"""
    # 爆量：方向意义被稀释（涨跌都是分歧）
    assert volume_state(4.5, +3.0) == "spike"
    assert volume_state(4.5, -3.0) == "spike"
    # 放量区（> 1.0）
    assert volume_state(1.8, +2.0) == "surge_up"
    assert volume_state(1.8, -2.0) == "surge_down"
    # 缩量区（< 0.6）
    assert volume_state(0.4, +2.0) == "shrink_up"
    assert volume_state(0.4, -2.0) == "shrink_down"
    # 中间区
    assert volume_state(0.9, +2.0) == "normal"
    assert volume_state(0.9, -2.0) == "normal"
    assert volume_state(1.0, +2.0) == "normal"  # 边界：正好 1.0 不算放量


def test_flat_price_is_normal_not_directional():
    """价格死区内（|chg| < FLAT_EPS_PCT）不计方向——±0.01% 不能被读成涨/跌。"""
    assert volume_state(2.0, 0.0) == "normal"
    assert volume_state(2.0, FLAT_EPS_PCT / 2) == "normal"
    assert volume_state(0.3, -FLAT_EPS_PCT / 2) == "normal"
    assert volume_state(2.0, FLAT_EPS_PCT) == "surge_up"  # 达到死区即计方向


def test_spike_takes_precedence_over_direction():
    """爆量优先级最高：即使价格下跌，也先报「分歧」而非「放量下杀」。"""
    assert volume_state(9.9, -8.0) == "spike"


# ---------------------------------------------------------------- 三态纪律

def test_unknown_instead_of_fabricated_normal():
    """缺数据 / 非法输入一律 unknown——**绝不臆造「量能平稳」**。"""
    assert volume_state(None, 1.0) == "unknown"
    assert volume_state(1.0, None) == "unknown"
    assert volume_state(None, None) == "unknown"
    assert volume_state(0.0, 1.0) == "unknown"      # 基准量为 0：比值无意义
    assert volume_state(-1.0, 1.0) == "unknown"
    assert volume_state("1.5", 1.0) == "unknown"    # 字符串不隐式转换
    assert volume_state(1.0, "0.5") == "unknown"


def test_unknown_not_actionable():
    assert is_actionable("unknown") is False
    for s in ("spike", "surge_up", "surge_down", "shrink_up", "shrink_down", "normal"):
        assert is_actionable(s) is True


# ---------------------------------------------------------------- 方向（核心防错）

def test_vma20_inverse_direction_is_not_reversed():
    """`vma20 = 20 日均量 / 今日量`，其 > 1 表示**缩量**（今日量小于均量）。

    直接把它当「量比」塞进 volume_state 会把缩量读成放量 —— 方向完全相反。
    """
    # vma20 = 2.0 ⇒ 今日量只有均量的一半 ⇒ 缩量
    assert ratio_from_inverse(2.0) == pytest.approx(0.5)
    assert volume_state_from_inverse(2.0, +1.0) == "shrink_up"
    assert volume_state_from_inverse(2.0, -1.0) == "shrink_down"
    # 反例锁定：同一数值若当量比用（忘记取倒数），结论落在相反的放量区
    assert volume_state(2.0, +1.0) == "surge_up"
    assert volume_state_from_inverse(2.0, +1.0) != volume_state(2.0, +1.0)
    # vma20 = 0.5 ⇒ 今日量是均量两倍 ⇒ 放量
    assert volume_state_from_inverse(0.5, +1.0) == "surge_up"


def test_ratio_from_inverse_handles_bad_input():
    assert ratio_from_inverse(None) is None
    assert ratio_from_inverse(0.0) is None
    assert ratio_from_inverse(-2.0) is None
    assert ratio_from_inverse("2.0") is None
    # 组合入口对坏输入给 unknown（不是抛异常、也不是猜一个）
    assert volume_state_from_inverse(None, 1.0) == "unknown"
    assert volume_state_from_inverse(0.0, 1.0) == "unknown"


# ---------------------------------------------------------------- 阈值分档

def test_bands_shrink_threshold_actually_applies():
    """不同分档的缩量线不同（场景不同，阈值差异是合理的——故显式登记而非拉平）。"""
    assert BANDS["tech"].shrink == 0.6
    assert BANDS["intraday"].shrink == 0.8
    # ratio=0.7：tech 档不算缩量（normal），intraday 档算缩量
    assert volume_state(0.7, -1.0, bands="tech") == "normal"
    assert volume_state(0.7, -1.0, bands="intraday") == "shrink_down"
    # 传 VolumeBands 实例等价于传 key
    assert volume_state(0.7, -1.0, bands=BANDS["intraday"]) == "shrink_down"


def test_unknown_bands_key_fails_fast():
    """拼错档名必须抛错——静默回退到默认档会掩盖调用方 bug。"""
    with pytest.raises(ValueError, match="unknown volume bands"):
        volume_state(1.0, 1.0, bands="nope")


def test_expand_line_is_one():
    assert EXPAND_LINE == 1.0
    assert volume_state(1.01, +1.0) == "surge_up"
    assert volume_state(0.99, +1.0) == "normal"


# ---------------------------------------------------------------- 文案单点

def test_describe_is_context_specific():
    """同一状态在个股/板块/大盘的解读必须不同（否则「三层共用」只是句空话）。"""
    stock = describe("shrink_up", context="stock")
    board = describe("shrink_up", context="board")
    index = describe("shrink_up", context="index")
    assert len({stock, board, index}) == 3
    assert "承接不足" in stock
    assert "分歧" in board
    assert "假突破" in index


def test_label_and_describe_cover_all_states():
    states = ("spike", "surge_up", "surge_down", "shrink_up", "shrink_down", "normal", "unknown")
    for s in states:
        assert label(s) and label(s) != s          # 有中文标签
        assert describe(s, context="stock")        # 三层都有文案
        assert describe(s, context="board")
        assert describe(s, context="index")
    assert label("unknown") == "量能未判定"


def test_unknown_state_never_reads_as_healthy():
    """unknown 的文案必须暴露「未判定」，不得与 normal 的文案混同。"""
    assert describe("unknown") != describe("normal")
    assert "未判定" in describe("unknown")
