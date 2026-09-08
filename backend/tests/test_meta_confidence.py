"""meta 置信层（规则版）测试：三档判定 + 每条降档规则显式留痕。"""

from __future__ import annotations

from app.picks.meta_confidence import classify_confidence

FULL = {k: 60 for k in ("sentiment", "news", "tech", "fundamental", "capital", "echelon")}


class TestTiers:
    def test_strong_all_conditions_met(self):
        out = classify_confidence(score=80, sub_scores=FULL, phase="发酵")
        assert out["tier"] == "strong"
        assert out["label"] == "强执行"
        assert out["reasons"] == ["综合分、相位、筹码、红线检查全部通过"]

    def test_executable_mid_score_neutral_phase(self):
        out = classify_confidence(score=65, sub_scores=FULL, phase="分歧")
        assert out["tier"] == "executable"
        assert out["label"] == "可执行"

    def test_observe_low_score(self):
        out = classify_confidence(score=50, sub_scores=FULL, phase="发酵")
        assert out["tier"] == "observe"
        assert any("可执行线" in r for r in out["reasons"])


class TestCaps:
    def test_veto_caps_at_observe(self):
        out = classify_confidence(score=90, sub_scores=FULL, phase="发酵", veto_count=1)
        assert out["tier"] == "observe"
        assert any("一票否决" in r for r in out["reasons"])

    def test_halt_penalty_caps_at_observe(self):
        out = classify_confidence(score=90, sub_scores=FULL, phase="发酵", halt_penalty=6.0)
        assert out["tier"] == "observe"
        assert any("异动风险" in r for r in out["reasons"])

    def test_adverse_phase_caps_at_observe(self):
        for ph in ("冰点", "退潮"):
            out = classify_confidence(score=90, sub_scores=FULL, phase=ph)
            assert out["tier"] == "observe", ph
            assert any(ph in r for r in out["reasons"])

    def test_chip_distribution_warning_caps_at_observe(self):
        out = classify_confidence(
            score=90, sub_scores=FULL, phase="发酵", chip_signal="distribution_warning"
        )
        assert out["tier"] == "observe"
        assert any("派发警示" in r for r in out["reasons"])

    def test_launch_watch_does_not_cap(self):
        """启动观察是机会信号不是风险信号：不压档。"""
        out = classify_confidence(
            score=80, sub_scores=FULL, phase="发酵", chip_signal="launch_watch"
        )
        assert out["tier"] == "strong"

    def test_missing_dim_caps_at_executable(self):
        sub = dict(FULL)
        sub.pop("fundamental")
        out = classify_confidence(score=90, sub_scores=sub, phase="发酵")
        assert out["tier"] == "executable"
        assert any("维度不全" in r for r in out["reasons"])

    def test_phase_none_not_strong(self):
        """相位缺失：不因相位降档到观察，但也不给强执行（诚实降级）。"""
        out = classify_confidence(score=90, sub_scores=FULL, phase=None)
        assert out["tier"] == "executable"
        assert any("相位" in r for r in out["reasons"])


class TestPrecedence:
    def test_drift_beats_strong_score(self):
        """多条件同时命中：红线 + 低分 → 仍是 observe（最高档兜底语义）。"""
        out = classify_confidence(
            score=30, sub_scores=FULL, phase="退潮", veto_count=1, halt_penalty=5.0
        )
        assert out["tier"] == "observe"
        assert len(out["reasons"]) >= 3  # 每条命中原因都留痕
