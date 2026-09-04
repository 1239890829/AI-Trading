"""东财跌停池（getTopicDTPool）解析测试。

字段缩放是本实现的核心风险点（同 test_eastmoney_zbpool.py 的教训）：
DTPool 的 p 为 **×1000**（集泰股份 p=7210 ↔ 收盘 7.21、传智教育 p=9720 ↔ 9.72，
2026-09-04 实测样本并与 zdp=-10% 交叉验证），与 ZTPool 的 ×100 不同——
缩放错了不会报错，只会静默产出错误价格，必须有单测锁死。

另锁死实测行为：**不带 date 参数接口返回 rc:102 data:null**（与 ZT/ZB 池不同），
因此请求必须显式携带 date。
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.data_providers.eastmoney import EastmoneyProvider
from app.market.normalizer import normalize_limit_down

_D = date(2026, 9, 4)

# 2026-09-04 实测样本（截取 2 条）
_RAW = {
    "data": {
        "tc": 9,
        "qdate": 20260904,
        "pool": [
            {
                "c": "002909", "m": 0, "n": "集泰股份",
                "p": 7210, "zdp": -9.987516403198243,
                "amount": 1135015040, "ltsz": 2742881431.43, "tshare": 2811900000.0,
                "pe": -102.87, "hs": 37.8386, "fund": 1333049,
                "lbt": 145418, "fba": 105587563, "days": 1, "oc": 11, "hybk": "化学制品",
            },
            {
                "c": "003032", "m": 0, "n": "传智教育",
                "p": 9720, "zdp": -10.0,
                "amount": 952070688, "ltsz": 2765161229.76, "tshare": 3911789738.88,
                "pe": 56.82, "hs": 32.551, "fund": 1634894,
                "lbt": 145351, "fba": 634034628, "days": 2, "oc": 1, "hybk": "教育",
            },
        ],
    }
}


def _provider_with_payload(monkeypatch: pytest.MonkeyPatch) -> EastmoneyProvider:
    p = EastmoneyProvider()

    async def _fake_get_json(url: str, params: dict) -> dict:
        assert "getTopicDTPool" in url
        assert params["date"] == "20260904", "DTPool 不带 date 返回 rc:102 data:null，date 必带"
        return _RAW

    monkeypatch.setattr(p, "_get_json", _fake_get_json)
    return p


def test_parse_scales_price_by_1000(monkeypatch: pytest.MonkeyPatch):
    p = _provider_with_payload(monkeypatch)
    rows = asyncio.run(p.get_limit_down_pool(_D))
    assert len(rows) == 2
    r = rows[0]
    assert r.symbol == "002909"
    assert r.price == 7.21, "DTPool 的 p 是 ×1000（ZTPool 是 ×100，不可混用）"
    assert rows[1].price == 9.72
    assert r.change_pct == pytest.approx(-9.987516, abs=1e-4)
    assert r.consecutive_days == 1
    assert rows[1].consecutive_days == 2
    assert r.open_count == 11
    assert r.seal_amount == pytest.approx(105587563, abs=1)
    assert r.turnover_rate == pytest.approx(37.8386, abs=1e-3)
    assert r.industry_board == "化学制品"
    assert r.source == "eastmoney"
    assert r.trade_date == _D


def test_normalize_skips_rows_without_symbol():
    assert normalize_limit_down({"n": "无名"}, _D) is None


def test_normalize_missing_optional_fields_do_not_crash():
    r = normalize_limit_down({"c": "600000"}, _D)
    assert r is not None
    assert r.price is None and r.name is None and r.consecutive_days == 0
