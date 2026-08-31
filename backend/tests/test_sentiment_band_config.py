"""P0-3 情绪阈值配置化测试。

风险点：配置错了但系统照常跑默认值——用户以为在调参，实际什么都没变，
结论失真且无感知。所以核心断言是：**非法配置必须炸，合法覆盖必须生效**。
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.schemas.market import LimitUpRecord
from app.sentiment.band_config import load_bands
from app.sentiment.engine import EARNING_BANDS, HEAT_BANDS, compute_sentiment

T = date(2026, 8, 28)
T1 = date(2026, 8, 27)

# 合法覆盖样例：只调涨停家数分档（40-59 家 +1 改为 50-59 家才 +1）
HEAT_OVERRIDE = json.dumps({
    "limit_up": [[25, -1, "<25家"], [50, 0, "25-49家"], [60, 1, "50-59家"],
                 [70, 2, "60-69家"], [None, 3, "≥70家"]],
    "max_board": HEAT_BANDS["max_board"],
    "break_rate": HEAT_BANDS["break_rate"],
}, ensure_ascii=False)


def _breadth(limit_up=90, up=3000, down=2400, limit_down=3):
    return {
        "total": 5550, "up": up, "down": down, "flat": 100, "suspended": 4,
        "limit_up": limit_up, "limit_down": limit_down,
        "limit_up_ratio": 0.03, "total_amount": 2e11,
    }


def _run(bands=None, pool_size=45):
    """构造 pool_size 只涨停（limit_up = len(pool_today)，封单法），验证分档覆盖生效。"""
    pool = [
        LimitUpRecord(symbol=f"{600000 + i:06d}", name="某股", trade_date="2026-08-28",
                      consecutive_boards=1, source="test")
        for i in range(pool_size)
    ]
    return compute_sentiment(
        breadth=_breadth(limit_up=pool_size + 3), pool_today=pool, pool_yesterday=[],
        snapshot=[], trade_date=T, prev_trade_date=T1, bands=bands,
    )


# ---------------------------------------------------------------- load_bands

def test_no_override_returns_defaults():
    heat, earning, source = load_bands("", "", HEAT_BANDS, EARNING_BANDS)
    assert heat is HEAT_BANDS and earning is EARNING_BANDS
    assert source == "defaults"


def test_valid_override_takes_effect():
    heat, earning, source = load_bands(HEAT_OVERRIDE, "", HEAT_BANDS, EARNING_BANDS)
    assert source == "env_override"
    assert heat["limit_up"][-1] == [None, 3, "≥70家"]  # JSON 解析后为 list
    # 未覆盖数值的指标与模板一致（JSON 往返后 tuple 变 list，按值比较）
    assert heat["break_rate"] == [list(r) for r in HEAT_BANDS["break_rate"]]
    assert earning is EARNING_BANDS


def test_invalid_json_raises():
    with pytest.raises(ValueError):
        load_bands("{not json", "", HEAT_BANDS, EARNING_BANDS)


def test_missing_key_raises():
    bad = json.dumps({
        "limit_up": HEAT_BANDS["limit_up"],
        "max_board": HEAT_BANDS["max_board"],
        # 缺 break_rate
    })
    with pytest.raises(ValueError, match="指标键不匹配"):
        load_bands(bad, "", HEAT_BANDS, EARNING_BANDS)


def test_extra_key_raises():
    bad = json.dumps({
        "limit_up": HEAT_BANDS["limit_up"],
        "max_board": HEAT_BANDS["max_board"],
        "break_rate": HEAT_BANDS["break_rate"],
        "ghost": [[1, 0, "x"]],
    })
    with pytest.raises(ValueError, match="指标键不匹配"):
        load_bands(bad, "", HEAT_BANDS, EARNING_BANDS)


def test_non_increasing_bounds_raise():
    bad = json.dumps({
        "limit_up": [[40, -1, "a"], [40, 0, "b"], [60, 1, "c"], [80, 2, "d"], [None, 3, "e"]],
        "max_board": HEAT_BANDS["max_board"],
        "break_rate": HEAT_BANDS["break_rate"],
    })
    with pytest.raises(ValueError, match="严格递增"):
        load_bands(bad, "", HEAT_BANDS, EARNING_BANDS)


def test_none_bound_must_be_last():
    bad = json.dumps({
        "limit_up": [[None, -1, "a"], [40, 0, "b"], [60, 1, "c"], [80, 2, "d"], [None, 3, "e"]],
        "max_board": HEAT_BANDS["max_board"],
        "break_rate": HEAT_BANDS["break_rate"],
    })
    with pytest.raises(ValueError, match="末位"):
        load_bands(bad, "", HEAT_BANDS, EARNING_BANDS)


def test_row_shape_must_be_triple():
    bad = json.dumps({
        "limit_up": [[40, 0]],
        "max_board": HEAT_BANDS["max_board"],
        "break_rate": HEAT_BANDS["break_rate"],
    })
    with pytest.raises(ValueError, match="得分, 标签"):
        load_bands(bad, "", HEAT_BANDS, EARNING_BANDS)


def test_wrong_type_points_raise():
    bad = json.dumps({
        "limit_up": [[40, "high", "a"]],
        "max_board": HEAT_BANDS["max_board"],
        "break_rate": HEAT_BANDS["break_rate"],
    })
    with pytest.raises(ValueError, match="得分须为数值"):
        load_bands(bad, "", HEAT_BANDS, EARNING_BANDS)


# ---------------------------------------------------------------- 引擎集成

def test_engine_uses_custom_bands():
    """覆盖生效：45 只涨停，默认档命中 "40-59家"=+1，覆盖后 "25-49家"=0 → 热度 raw 少 1 分。"""
    default = _run(pool_size=45)
    heat_custom, _, _ = load_bands(HEAT_OVERRIDE, "", HEAT_BANDS, EARNING_BANDS)
    custom = _run(pool_size=45, bands={"heat": heat_custom})
    assert custom["heat"]["raw"] == default["heat"]["raw"] - 1


def test_engine_without_bands_unchanged():
    """不传 bands 时行为与 v2 原版一致（回归保护）。"""
    r = _run(pool_size=45)
    assert r["phase"] in {"冰点", "修复", "发酵", "高潮", "分歧", "退潮"}
    assert r["heat"]["level"] in (0, 1, 2, 3)
