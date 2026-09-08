"""applied 半自动写回提示（review/writeback）单测。"""
from __future__ import annotations

from app.review.writeback import PARAM_TARGETS, build_param_diff


def test_registry_covers_registered_params_only():
    assert "PENALTY_Y1" in PARAM_TARGETS
    assert "FLOW_SURGE_YI" in PARAM_TARGETS
    # 每个登记项都指向真实文件（防手滑写错展示路径）
    for _, file_name in PARAM_TARGETS.values():
        assert file_name.startswith("app/")


def test_extract_suggestion_and_runtime_current():
    text = "建议把 PENALTY_Y1 6.0 → 8.0，观察一个月后再评估"
    diffs = build_param_diff(text)
    assert len(diffs) == 1
    d = diffs[0]
    assert d["param"] == "PENALTY_Y1"
    assert d["suggested"] == 8.0
    assert d["current_in_text"] == 6.0
    # 运行时值可读（halt_risk 模块常量=6.0）且与文本一致 → resolved 且不 stale
    assert d["resolved"] is True
    assert d["stale"] is False
    assert d["current"] == 6.0


def test_stale_flag_when_text_outdated():
    text = "RED_DEV_10D 50.0 → 60.0"  # 模块实际是 80.0 → 文本里的当前值过期
    diffs = build_param_diff(text)
    assert diffs[0]["stale"] is True


def test_unknown_param_skipped_and_no_match_empty():
    assert build_param_diff("SOME_UNKNOWN_PARAM 1.0 → 2.0") == []
    assert build_param_diff("没有参数调整意图的普通说明") == []
    assert build_param_diff("") == []
