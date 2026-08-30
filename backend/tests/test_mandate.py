from __future__ import annotations

from pathlib import Path

import pytest

from app.market.backtest import BacktestConfig, STRATEGY_REGISTRY
from app.market.mandate import load_mandate, list_mandates, resolve_backtest_request

REAL = "ma_cross_600519"  # 仓库自带的示例 mandate


def test_builtin_example_loads():
    m = load_mandate(REAL)
    assert m.symbol == "600519"
    assert m.strategy_id == "ma_cross"
    assert m.bars == 300
    assert m.config.initial_cash == 1_000_000.0
    assert m.config.commission_rate == 0.00025


def test_list_mandates_contains_example():
    items = list_mandates()
    assert any(i.get("file") == f"{REAL}.yaml" and i.get("valid") for i in items)


def test_missing_mandate_raises():
    with pytest.raises(ValueError, match="不存在"):
        load_mandate("no_such_mandate")


def test_path_traversal_rejected():
    with pytest.raises(ValueError, match="非法"):
        load_mandate("../secrets")


def test_unknown_strategy_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.market import mandate as M

    monkeypatch.setattr(M, "MANDATE_DIR", tmp_path)
    (tmp_path / "bad.yaml").write_text("symbol: '600519'\nstrategy:\n  id: nope\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不在注册表"):
        load_mandate("bad")


def test_unknown_top_level_field_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """未知字段必须显式报错，不能静默忽略（项目纪律：不猜）。"""
    from app.market import mandate as M

    monkeypatch.setattr(M, "MANDATE_DIR", tmp_path)
    (tmp_path / "x.yaml").write_text("symbol: '600519'\nfoo: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="未知字段"):
        load_mandate("x")


def test_unknown_cost_field_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.market import mandate as M

    monkeypatch.setattr(M, "MANDATE_DIR", tmp_path)
    (tmp_path / "c.yaml").write_text(
        "symbol: '600519'\nstrategy:\n  id: ma_cross\ncosts:\n  commission: 1\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="未知字段"):
        load_mandate("c")


def test_resolution_layers_request_overrides_mandate():
    """分层：代码默认 < mandate < 请求显式字段，且 applied 可解释。"""
    r = resolve_backtest_request(
        symbol="000001", strategy_id=None, params=None, bars=None, mandate_name=REAL
    )
    # 请求给了 symbol → 覆盖 mandate 的 600519
    assert r.symbol == "000001"
    # 其余取 mandate
    assert r.strategy_id == "ma_cross"
    assert r.bars == 300
    assert r.config == load_mandate(REAL).config
    assert any(a.startswith("symbol←请求") for a in r.applied)
    assert any(a.startswith("bars←mandate") for a in r.applied)


def test_resolution_without_mandate_uses_registry_defaults():
    r = resolve_backtest_request(
        symbol="600519", strategy_id="ma_breakout", params=None, bars=None, mandate_name=None
    )
    assert r.bars == 500
    assert r.params == STRATEGY_REGISTRY["ma_breakout"]["params"]
    assert isinstance(r.config, BacktestConfig)
    assert r.mandate is None


def test_resolution_requires_symbol():
    with pytest.raises(ValueError, match="symbol 缺失"):
        resolve_backtest_request(
            symbol=None, strategy_id=None, params=None, bars=None, mandate_name=None
        )


def test_empty_symbol_not_padded_into_fake_code():
    """空串会被 zfill 填成 000000 混过校验——必须有独立判空。"""
    with pytest.raises(ValueError, match="symbol 缺失"):
        resolve_backtest_request(
            symbol="  ", strategy_id="ma_cross", params=None, bars=None, mandate_name=None
        )


def test_resolution_rejects_unknown_param_key():
    with pytest.raises(ValueError, match="未知键"):
        resolve_backtest_request(
            symbol="600519", strategy_id="ma_cross", params={"foo": 1}, bars=None, mandate_name=None
        )


def test_load_accepts_file_suffix_form():
    """列表端点展示 file，调用方容易误传；加载器对 .yaml 后缀做显式归一。"""
    m1 = load_mandate(REAL)
    m2 = load_mandate(f"{REAL}.yaml")
    assert (m1.symbol, m1.strategy_id, m1.bars) == (m2.symbol, m2.strategy_id, m2.bars)


def test_list_mandates_returns_id_and_params():
    items = list_mandates()
    row = next(i for i in items if i.get("id") == REAL)
    assert row["valid"] is True
    assert row["params"].get("fast") == 5
    assert row["params"].get("slow") == 20
