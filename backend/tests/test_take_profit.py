"""止盈档位（P0-1）单元测试——零网络、零 DB，纯函数与口径锚定。

设计要点（勿擅改，改了先改 `exit_engine` 模块 docstring）：
- 触发口径 = 当日涨跌幅（相对昨收，直取快照 `change_pct`）
- 封板不触发（与「连板持有」纪律同源，避免过早止盈错失连板趋势）
- 持仓 / 未持仓文案分流（未持仓不给卖出指令，守「不输出确定性买卖结论」红线）
- 阈值 env `ASHARE_TAKE_PROFIT_PCT` 可调，非法值回退默认并记日志
"""
from __future__ import annotations

import pytest

from app.picks import exit_engine as ee


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ASHARE_TAKE_PROFIT_PCT", raising=False)


# ---------------------------------------------------------------- 阈值读取


def test_default_threshold_is_three_pct():
    assert ee.take_profit_pct() == pytest.approx(3.0)


def test_env_override(monkeypatch):
    monkeypatch.setenv("ASHARE_TAKE_PROFIT_PCT", "5.5")
    assert ee.take_profit_pct() == pytest.approx(5.5)


@pytest.mark.parametrize("bad", ["abc", "", "  ", "0", "-2"])
def test_env_invalid_falls_back_to_default(monkeypatch, bad):
    """非法/非正阈值绝不静默改口径，一律回退默认。"""
    monkeypatch.setenv("ASHARE_TAKE_PROFIT_PCT", bad)
    assert ee.take_profit_pct() == pytest.approx(3.0)


# ---------------------------------------------------------------- 触发口径


def test_below_threshold_does_not_fire():
    assert ee.take_profit_rule(2.99, sealed=False, held=True) is None


def test_boundary_fires_inclusive():
    """口径是「≥ 阈值」，边界值必须触发（否则 3% 这个整数会被漏掉）。"""
    rule = ee.take_profit_rule(3.0, sealed=False, held=True)
    assert rule is not None and rule["action"] == "take_profit"


def test_above_threshold_fires():
    rule = ee.take_profit_rule(7.2, sealed=False, held=True)
    assert rule is not None
    assert rule["pct"] == pytest.approx(7.2)
    assert rule["held"] is True


def test_sealed_never_fires():
    """封板日绝不提示止盈——避免过早卖飞连板趋势。"""
    assert ee.take_profit_rule(10.0, sealed=True, held=True) is None
    assert ee.take_profit_rule(20.0, sealed=True, held=True) is None


def test_missing_pct_is_unknown_not_zero():
    """缺数据显式 unknown（None）——绝不按 0% 处理成「未冲高」。"""
    assert ee.take_profit_rule(None, sealed=False, held=True) is None


def test_negative_pct_does_not_fire():
    assert ee.take_profit_rule(-5.0, sealed=False, held=True) is None


# ---------------------------------------------------------------- 文案分流


def test_held_text_suggests_halving():
    rule = ee.take_profit_rule(4.0, sealed=False, held=True)
    assert "减半仓" in rule["reason"]


def test_unheld_text_gives_no_sell_instruction():
    """未持仓只记录，不得出现买卖指令（红线）。"""
    rule = ee.take_profit_rule(4.0, sealed=False, held=False)
    assert rule["held"] is False
    assert "减半仓" not in rule["reason"]
    assert "不作为买入依据" in rule["reason"]


def test_threshold_echoed_in_reason_for_auditability():
    rule = ee.take_profit_rule(4.5, sealed=False, held=True)
    assert "3.0%" in rule["reason"]


def test_pool_cap_constant_is_sane():
    assert 1 <= ee.TAKE_PROFIT_POOL_CAP <= 20
