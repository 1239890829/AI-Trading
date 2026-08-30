"""东财炸板池备源（数据源方案 B5）解析测试。

字段缩放是本实现的核心风险点：ZBPool 的 p 为 ×1000（已用 600103 青山纸业
2026-08-28 收盘 3.85 与 TDX 日K交叉验证），与 ZTPool 的 ×100 不同——
缩放错了不会报错，只会静默产出错误价格，所以必须有单测锁死。
"""
from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from app.data_providers.eastmoney import EastmoneyProvider

_D = date(2026, 8, 28)

_RAW = {
    "data": {
        "tc": 16,
        "qdate": 20260828,
        "pool": [
            {
                "c": "600103", "m": 1, "n": "青山纸业",
                "p": 3850, "ztp": 4200, "zdp": 0.7853402,
                "amount": 2970707696, "ltsz": 8517466830.95,
                "hs": 33.795787, "fbt": 92501, "zbc": 1,
                "zttj": {"days": 4, "ct": 3}, "hybk": "造纸",
            }
        ],
    }
}


def _provider_with_payload(monkeypatch: pytest.MonkeyPatch) -> EastmoneyProvider:
    p = EastmoneyProvider()

    async def _fake_get_json(url: str, params: dict) -> dict:
        assert "getTopicZBPool" in url
        assert params["date"] == "20260828"
        return _RAW

    monkeypatch.setattr(p, "_get_json", _fake_get_json)
    return p


def test_parse_scales_price_by_1000(monkeypatch: pytest.MonkeyPatch):
    p = _provider_with_payload(monkeypatch)
    rows = asyncio.run(p.get_limit_break_pool(_D))
    assert len(rows) == 1
    r = rows[0]
    assert r.symbol == "600103"
    assert r.price == 3.85, "ZBPool 的 p 是 ×1000（ZTPool 是 ×100，不可混用）"
    assert r.change_pct == pytest.approx(0.7853402, abs=1e-6)
    assert r.break_count == 1
    assert r.turnover_rate == pytest.approx(33.795787, abs=1e-4)
    assert r.first_seal_time == "09:25:01"
    assert r.industry_board == "造纸"
    assert r.source == "eastmoney"


def test_skips_rows_without_symbol(monkeypatch: pytest.MonkeyPatch):
    p = _provider_with_payload(monkeypatch)
    raw = {"data": {"tc": 2, "pool": [{"c": "", "n": "无代码"}, _RAW["data"]["pool"][0]]}}

    async def _fake(url: str, params: dict) -> dict:
        return raw

    monkeypatch.setattr(p, "_get_json", _fake)
    rows = asyncio.run(p.get_limit_break_pool(_D))
    assert [r.symbol for r in rows] == ["600103"]


def test_missing_optional_fields_do_not_crash(monkeypatch: pytest.MonkeyPatch):
    p = _provider_with_payload(monkeypatch)
    raw = {"data": {"tc": 1, "pool": [{"c": "600103"}]}}

    async def _fake(url: str, params: dict) -> dict:
        return raw

    monkeypatch.setattr(p, "_get_json", _fake)
    rows = asyncio.run(p.get_limit_break_pool(_D))
    assert len(rows) == 1
    r = rows[0]
    assert r.price is None and r.name is None and r.break_count is None


def test_chain_has_two_limit_break_sources():
    """B5 的目标：消除炸板率单点——链上必须有 ≥2 个 provider 实现该方法。"""
    from app.data_providers.eastmoney import EastmoneyProvider as EM
    from app.data_providers.ths import ThsFuyaoProvider

    assert hasattr(ThsFuyaoProvider, "get_limit_break_pool")
    assert hasattr(EM, "get_limit_break_pool")


def test_chain_falls_back_to_eastmoney_when_ths_fails(monkeypatch: pytest.MonkeyPatch):
    """B5 的验收：主源炸板池挂掉时链自动落到东财，而不是整条失败。"""
    from app.data_providers.composite import CompositeProvider

    async def _ths_fail(trade_date):
        raise RuntimeError("ths down")

    async def _em_ok(trade_date):
        p = _provider_with_payload(monkeypatch)
        return await p.get_limit_break_pool(trade_date)

    ths = SimpleNamespace(name="ths", get_limit_break_pool=_ths_fail)
    em = SimpleNamespace(name="eastmoney", get_limit_break_pool=_em_ok)
    chain = CompositeProvider([ths, em])

    rows = asyncio.run(chain.get_limit_break_pool(_D))
    assert [r.symbol for r in rows] == ["600103"], "主源失败必须落到东财备源"
    # 注：switch_log 只在「切换」时记录（首次选中不记），故此处断言结果而非日志
