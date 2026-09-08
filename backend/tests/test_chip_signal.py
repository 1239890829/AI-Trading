"""派发/吸筹规则化测试（chip_signal）：形态语义合成 + 三态降级。"""

from __future__ import annotations

from app.picks.chip_signal import evaluate_chip_signal, chip_basis_text


def _bar(close: float, volume: float, spread: float = 0.05) -> dict:
    return {
        "open": close, "high": close * (1 + spread), "low": close * (1 - spread),
        "close": close, "volume": volume,
    }


def _chip(available: bool = True, profit=0.5, conc=0.5, peak=10.0, as_of="2026-09-07") -> dict:
    return {
        "available": available,
        "approx": True,
        "as_of": as_of,
        "profit_ratio": profit,
        "concentration": conc,
        "main_peak": {"price": peak, "width_low": peak * 0.95, "width_high": peak * 1.05},
        "support": 9.0,
        "resistance": 11.0,
    }


def _distribution_bars() -> list[dict]:
    """80 根 10→20 上升（量 100）+ 15 根 20 横盘（量 100）+ 5 根放量（量 200）：
    close 处窗口 ~91% 分位、3 日滞涨、量能比 200/125=1.6。"""
    bars = [_bar(round(10 + i * 10 / 79, 3), 100) for i in range(80)]
    bars += [_bar(20.0, 100) for _ in range(15)]
    bars += [_bar(20.0, 200) for _ in range(5)]
    return bars


def _launch_bars() -> list[dict]:
    """100 根 10~11 区间震荡 + 近期缩量回踩 10：close 处窗口 0 分位。"""
    bars = [_bar(10.0 + (i % 2) * 1.0, 100) for i in range(95)]
    bars += [_bar(10.0, 50) for _ in range(5)]
    return bars


class TestDistributionWarning:
    def test_high_dense_profitable_stalling(self):
        sig = evaluate_chip_signal(
            _chip(profit=0.92, conc=0.31, peak=20.5), _distribution_bars()
        )
        assert sig["available"] is True
        assert sig["signal"] == "distribution_warning"
        assert sig["label"] == "派发警示"
        assert sig["metrics"]["price_pos"] >= 0.75
        assert sig["metrics"]["vol_ratio_5_20"] >= 1.3
        assert sig["reasons"]

    def test_conditions_not_met_signal_is_none_not_healthy(self):
        """获利盘低 → 未触发：signal=None 是「未触发」，绝不冒充「形态健康」。"""
        sig = evaluate_chip_signal(_chip(profit=0.39, conc=0.55, peak=8.6), _distribution_bars())
        assert sig["available"] is True
        assert sig["signal"] is None
        assert sig["label"] is None


class TestLaunchWatch:
    def test_low_dense_pullback_on_peak_with_shrinking_volume(self):
        sig = evaluate_chip_signal(_chip(profit=0.50, conc=0.35, peak=10.0), _launch_bars())
        assert sig["signal"] == "launch_watch"
        assert sig["label"] == "启动观察"
        assert sig["metrics"]["price_pos"] <= 0.45
        assert sig["metrics"]["vol_ratio_5_20"] <= 0.70


class TestDegradation:
    def test_chip_unavailable_explicit(self):
        sig = evaluate_chip_signal({"available": False, "reason": "无日K覆盖"}, _distribution_bars())
        assert sig["available"] is False
        assert sig["signal"] is None
        assert "无日K覆盖" in sig["reason"]

    def test_none_chip(self):
        assert evaluate_chip_signal(None, _distribution_bars())["available"] is False

    def test_missing_volume_blocks_rules(self):
        """bars < 20 根：量能比 None → 两条规则都不触发（不臆造量能结论）。"""
        sig = evaluate_chip_signal(_chip(profit=0.92, conc=0.31), _distribution_bars()[:10])
        assert sig["available"] is True
        assert sig["signal"] is None
        assert sig["metrics"]["vol_ratio_5_20"] is None


class TestBasisText:
    def test_unavailable_basis_honest(self):
        text = chip_basis_text({"available": False, "reason": "marketdb 不存在"}, None)
        assert "筹码数据缺失" in text and "marketdb 不存在" in text

    def test_signal_basis_contains_metrics_and_caveat(self):
        sig = evaluate_chip_signal(
            _chip(profit=0.92, conc=0.31, peak=20.5), _distribution_bars()
        )
        text = chip_basis_text(sig, _chip(profit=0.92, conc=0.31, peak=20.5))
        assert "派发警示" in text and "CYQ 近似口径" in text
