"""相位→风格路由表 + 空仓闸门三态（审查报告 §4.1/§4.2，P1-2）测试。"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.picks.gate import FOLLOW_MIN_BOARDS, FOLLOW_ROLES, apply_gate_to_picks, evaluate_stand_aside
from app.picks.style_router import (
    DIMS,
    OFFSET_MAX,
    apply_style_offsets,
    route_style,
    style_note,
)
from app.picks.meta_confidence import classify_confidence

FULL = {k: 60 for k in DIMS}


# ---------------------------------------------------------------- 相位→风格路由


class TestRouteStyle:
    @pytest.mark.parametrize("phase,style", [
        ("发酵", "题材进攻"),
        ("高潮", "题材进攻"),
        ("冰点", "趋势防守"),
        ("退潮", "趋势防守"),
        ("分歧", "防守微调"),
        ("修复", "均衡"),
    ])
    def test_six_phases_route(self, phase, style):
        out = route_style(phase)
        assert out["routed"] is True
        assert out["style"] == style
        assert set(out["offsets"]) <= set(DIMS)
        assert all(abs(v) <= OFFSET_MAX for v in out["offsets"].values())

    def test_offense_direction(self):
        """发酵/高潮：题材情绪↑ 趋势/基本面↓（审查报告 §4.1 原文口径）。"""
        off = route_style("发酵")["offsets"]
        assert off["echelon"] > 0 and off["sentiment"] > 0
        assert off["tech"] < 0 and off["fundamental"] < 0

    def test_defense_direction(self):
        """冰点/退潮：趋势/基本面↑ 题材情绪↓。"""
        off = route_style("冰点")["offsets"]
        assert off["tech"] > 0 and off["fundamental"] > 0
        assert off["sentiment"] < 0 and off["echelon"] < 0

    def test_repair_balanced(self):
        assert route_style("修复")["offsets"] == {}

    @pytest.mark.parametrize("bad", [None, "", "未知相位"])
    def test_missing_or_unknown_phase_not_routed(self, bad):
        out = route_style(bad)
        assert out["routed"] is False
        assert out["offsets"] == {}

    def test_config_override_merges(self, monkeypatch):
        monkeypatch.setattr(
            settings, "picks_style_offsets_json",
            '{"发酵": {"tech": 0.02}}', raising=False,
        )
        off = route_style("发酵")["offsets"]
        assert off["tech"] == 0.02          # 覆盖默认 -0.05
        assert off["echelon"] == 0.06       # 未覆盖维度保留默认

    def test_config_invalid_raises(self, monkeypatch):
        for bad in ('{"发酵": {"nope": 0.01}}', '{"发酵": {"tech": 0.5}}', "not-json"):
            monkeypatch.setattr(settings, "picks_style_offsets_json", bad, raising=False)
            with pytest.raises((ValueError, json.JSONDecodeError)):
                route_style("发酵")


# ---------------------------------------------------------------- 权重合成


class TestApplyStyleOffsets:
    def test_sums_to_one_and_keeps_dims(self):
        base = {"sentiment": 0.18, "news": 0.22, "tech": 0.20, "fundamental": 0.15,
                "capital": 0.12, "echelon": 0.13}
        out = apply_style_offsets(base, route_style("发酵")["offsets"])
        assert set(out) == set(DIMS)
        assert sum(out.values()) == pytest.approx(1.0, abs=1e-3)
        assert out["echelon"] > base["echelon"]
        assert out["tech"] < base["tech"]

    def test_clamp_prevents_silencing(self):
        # 极端偏移也不允许任何维度被压到下限之下 / 冲破上限
        out = apply_style_offsets({d: 1 / 6 for d in DIMS}, {d: 0.06 for d in DIMS})
        assert all(0.02 <= v <= 0.45 for v in out.values())
        assert sum(out.values()) == pytest.approx(1.0, abs=1e-3)

    def test_empty_offsets_is_renorm_identity(self):
        base = {"sentiment": 0.2, "news": 0.2, "tech": 0.2, "fundamental": 0.2,
                "capital": 0.1, "echelon": 0.1}
        assert apply_style_offsets(base, {}) == pytest.approx(base, abs=1e-3)


def test_style_note_only_when_offsets():
    assert style_note(route_style("修复")) is None
    assert style_note(route_style(None)) is None
    note = style_note(route_style("发酵"))
    assert note and "题材进攻" in note and "echelon" in note


def test_meta_confidence_appends_style_note():
    plain = classify_confidence(score=80, sub_scores=FULL, phase="发酵")
    with_note = classify_confidence(score=80, sub_scores=FULL, phase="发酵",
                                    style_note="当日风格「题材进攻」")
    assert with_note["tier"] == plain["tier"] == "strong"
    # 注：唯一理由是"全过"文案时，追加 note 后该文案被替换（note 成为唯一留痕）——
    # 档位不受影响，这是设计行为；此处锚定 note 恒在末位且档位不变。
    assert with_note["reasons"][-1] == "当日风格「题材进攻」"
    # 有多条降档理由时，note 只追加、原理由保留
    capped = classify_confidence(score=80, sub_scores=FULL, phase="冰点", veto_count=1,
                                 style_note="当日风格「趋势防守」")
    assert capped["reasons"][-1] == "当日风格「趋势防守」"
    assert len(capped["reasons"]) >= 3


# ---------------------------------------------------------------- gate 三态


def _pick(**kw) -> dict:
    base = {"symbol": "600000", "name": "测试", "score": 70.0}
    base.update(kw)
    return base


def _gate() -> dict:
    return evaluate_stand_aside(phase="退潮")


class TestGateTriState:
    def test_non_gate_day_untouched(self):
        picks = [_pick()]
        assert apply_gate_to_picks(picks, {"stand_aside": False}) == picks

    def test_followable_full_criteria(self):
        picks = [_pick(boards=3, echelon_role="龙头", theme="存储芯片")]
        out = apply_gate_to_picks(picks, _gate())
        it = out[0]
        # 纪律不破：可跟仍不给买入范围、observation_only 仍 True（下游语义不变）
        assert it["follow_state"] == "followable"
        assert it["observation_only"] is True
        assert "buy_range" not in it
        assert "影子持仓" in it["follow_reasons"][-1]

    def test_first_board_not_followable(self):
        out = apply_gate_to_picks([_pick(boards=1, echelon_role="龙头", theme="x")], _gate())
        assert out[0]["follow_state"] == "observe"

    def test_follower_role_not_followable(self):
        out = apply_gate_to_picks([_pick(boards=3, echelon_role="跟风", theme="x")], _gate())
        assert out[0]["follow_state"] == "observe"

    def test_no_theme_not_followable(self):
        out = apply_gate_to_picks([_pick(boards=3, echelon_role="龙头", theme=None)], _gate())
        assert out[0]["follow_state"] == "observe"

    def test_veto_blocks_even_leader(self):
        picks = [_pick(boards=5, echelon_role="空间板", theme="x", vetoes=["R1 停牌前兆"])]
        out = apply_gate_to_picks(picks, _gate())
        assert out[0]["follow_state"] == "blocked"

    def test_halt_penalty_blocks(self):
        picks = [_pick(boards=3, echelon_role="龙头", theme="x",
                       halt_risk={"penalty": 6.0})]
        out = apply_gate_to_picks(picks, _gate())
        assert out[0]["follow_state"] == "blocked"

    def test_legacy_shape_defaults_observe(self):
        """旧数据形态（无 boards/role/theme 字段）→ 仅观察，不误升级。"""
        out = apply_gate_to_picks([_pick()], _gate())
        assert out[0]["follow_state"] == "observe"
        assert out[0]["observation_only"] is True

    def test_non_limit_up_leader_role_needs_boards(self):
        """领涨（非涨停角色）无 boards 数据 → 不满足连板判据 → 观察。"""
        out = apply_gate_to_picks([_pick(boards=None, echelon_role="领涨", theme="x")], _gate())
        assert out[0]["follow_state"] == "observe"

    def test_constants_match_echelon_vocabulary(self):
        assert FOLLOW_ROLES <= {"空间板", "龙头", "反包", "中军", "领涨", "补涨",
                                "首板", "同步", "跟风", "滞涨", "断板"}
        assert FOLLOW_MIN_BOARDS == 2
