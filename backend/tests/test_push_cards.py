"""飞书推送卡片构建（app/picks/push_cards.py）单测——零网络。

2026-09-08 修复：判定字段 `level="unknown"` 是 truthy，`or "—"` 兜底失效，
内部三态字面量被直接打进飞书卡片（14:40 盘中确认卡实测 3 处「辨识度 unknown」）。
本文件锚定「任何对外文案都不含内部字面量 unknown」。
"""
from __future__ import annotations

import json
from datetime import datetime

from app.picks.push_cards import build_buy_point_card, sentiment_pairs, tri_text


def test_tri_text_three_states():
    assert tri_text(None) == "--"        # 缺数据
    assert tri_text("") == "--"
    assert tri_text("  ") == "--"
    assert tri_text("unknown") == "未判定"   # 有判定但判不出 ≠ 低、≠ 空
    assert tri_text("Unknown") == "未判定"
    assert tri_text("高") == "高"
    assert tri_text(0) == "0"            # 数值 0 是有效值，不冒充缺失
    n = float("nan")
    assert tri_text(n) == "--"


def test_sentiment_pairs_never_leaks_none_or_unknown():
    """情绪栅格：缺失/三态一律走 tri_text，绝不出现 None/unknown 字面量。"""
    pairs = sentiment_pairs({"temperature": None, "phase": "unknown", "indicators": []}, {})
    body = json.dumps(pairs, ensure_ascii=False)
    assert "None" not in body and "unknown" not in body
    assert "未判定" in body and "--" in body


def test_buy_point_card_has_no_internal_literals():
    """买点卡：个股块三态字段（题材阶段/置信档）不外泄 unknown/None。"""
    hit = {
        "item": {
            "name": "测试股", "symbol": "600000", "score": 80.0,
            "echelon_role": None, "theme": "题材", "theme_stage": "unknown",
            "stop_loss": {"pct": 5, "price": 10.0},
            "invalidations": ["破线"], "bases": {"echelon": "龙头"},
        },
        "price": 10.5, "chg": 2.1, "tier_label": tri_text("unknown"),
        "low": 10.2, "high": 10.8,
    }
    card = build_buy_point_card([hit], None, None, {"stand_aside": False}, show=datetime(2026, 9, 8))
    body = json.dumps(card, ensure_ascii=False)
    assert "unknown" not in body
    assert "None" not in body
    assert "未判定" in body  # 判不出显式呈现为「未判定」


def test_buy_point_card_gate_day_copy_emphasizes_follow_not_buy():
    """闸门期：文案必须写明「不给买入范围 + 须经影子持仓验证」（可跟 ≠ 可买）。"""
    hit = {
        "item": {"name": "闸门票", "symbol": "600000", "score": 80.0,
                 "stop_loss": {"pct": 5, "price": 10.0}, "invalidations": ["破线"]},
        "price": 10.5, "chg": 2.1, "tier_label": "可执行", "low": 10.2, "high": 10.8,
    }
    card = build_buy_point_card([hit], None, None,
                                {"stand_aside": True, "level": "strong"}, show=datetime(2026, 9, 8))
    body = json.dumps(card, ensure_ascii=False)
    assert "不给买入范围" in body and "影子持仓" in body
