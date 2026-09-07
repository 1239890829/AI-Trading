"""异动原因接入（A1）与封单字段（A2）测试——零网络，假响应。

覆盖：provider 解析/标签过滤/空集语义/北交所后缀/分批、composite 允许空语义
（空集不打熔断——变异验证点）、路由 422/200 结构。
"""
from __future__ import annotations

import asyncio
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_hub
from app.api.routes import market as market_route
from app.data_providers.composite import CompositeProvider
from app.data_providers.eastmoney import ProviderError
from app.data_providers.ths import ThsFuyaoProvider, to_thscode
from app.schemas.market import AnomalyRecord


def _run(coro):
    return asyncio.run(coro)


def _rec(code="600519", tag="涨停", analysis="公司披露业绩预告") -> AnomalyRecord:
    return AnomalyRecord(
        symbol=code, name="贵州茅台", tag=tag, analysis=analysis,
        keywords=["业绩", "预增"], source="ths",
    )


def _provider(monkeypatch, responder):
    """ThsFuyaoProvider + _get 桩；返回 (provider, calls)。"""
    p = ThsFuyaoProvider(api_key="test-key")
    calls: list[tuple[str, dict]] = []

    async def fake_get(path, params):
        calls.append((path, dict(params)))
        return responder(path, params)

    monkeypatch.setattr(p, "fake_get", fake_get, raising=False)
    monkeypatch.setattr(p, "_get", fake_get)
    return p, calls


def _item(code="600519", tag="涨停", analysis="公司披露业绩预告"):
    suffix = ".BJ" if code.startswith(("4", "8", "920")) else (".SH" if code.startswith("6") else ".SZ")
    return {
        "thscode": f"{code}{suffix}",
        "stock_name": "贵州茅台",
        "tag_name": tag,
        "analysis_content": analysis,
        "keyword_list": ["业绩", "预增"],
    }


# ---------- provider 层 ----------

def test_anomaly_list_parses_fields(monkeypatch):
    p, _ = _provider(monkeypatch, lambda path, params: {"item": [_item()]})
    rows = _run(p.get_anomaly_list(None))
    assert len(rows) == 1
    r = rows[0]
    assert r.symbol == "600519"
    assert r.tag == "涨停"
    assert r.analysis == "公司披露业绩预告"
    assert r.keywords == ["业绩", "预增"]
    assert r.source == "ths"


def test_anomaly_list_filters_and_dedupes_tags(monkeypatch):
    p, calls = _provider(monkeypatch, lambda path, params: {"item": []})
    _run(p.get_anomaly_list(["LIMIT_UP", "limit_up", "垃圾标签", " SHARP_RISE "]))
    sent = calls[0][1].get("tag_codes")
    assert sent == "LIMIT_UP,SHARP_RISE"  # 大小写归一 + 去重 + 非法剔除


def test_anomaly_list_empty_is_ok_not_error(monkeypatch):
    """空集=当日无记录（today-only 合法语义），绝不 raise。"""
    p, _ = _provider(monkeypatch, lambda path, params: {"item": []})
    assert _run(p.get_anomaly_list(None)) == []


def test_anomaly_stock_bj_suffix(monkeypatch):
    """北交所 920xxx 后缀必须 .BJ（to_thscode 已全局修复，本测试防回归）。"""
    p, calls = _provider(monkeypatch, lambda path, params: {"item": [_item("920075")]})
    rows = _run(p.get_anomaly_stock(["920075"]))
    assert rows[0].symbol == "920075"
    assert calls[0][1]["thscodes"] == "920075.BJ"


def test_anomaly_stock_batches_over_50(monkeypatch):
    """官方单批 50 上限：55 个代码必须分 2 批。"""
    seen: list[str] = []

    def responder(path, params):
        seen.append(params["thscodes"])
        return {"item": []}

    p, _ = _provider(monkeypatch, responder)
    symbols = [f"{600000 + i}" for i in range(55)]
    assert _run(p.get_anomaly_stock(symbols)) == []
    assert len(seen) == 2
    assert len(seen[0].split(",")) == 50 and len(seen[1].split(",")) == 5


def test_to_thscode_bj_aware_rules():
    """全局 to_thscode 北交所感知规则（2026-09-07 修复 920→.SH 错标）。"""
    assert to_thscode("600519") == "600519.SH"
    assert to_thscode("510300") == "510300.SH"   # 沪市基金/ETF
    assert to_thscode("900901") == "900901.SH"   # 沪 B
    assert to_thscode("000001") == "000001.SZ"
    assert to_thscode("300750") == "300750.SZ"
    assert to_thscode("920075") == "920075.BJ"
    assert to_thscode("830799") == "830799.BJ"
    assert to_thscode("430047") == "430047.BJ"
    assert to_thscode("889999") == "889999.BJ"   # 88 段北交所


# ---------- composite 层：允许空语义（变异验证点） ----------

class _EmptyThs:
    name = "ths"
    realtime = False

    async def get_anomaly_list(self, tag_codes):
        return []


def test_composite_anomaly_empty_never_breaks():
    """空集连续调用不打熔断（走 _call 的话第 4 次会被 FAILURE_THRESHOLD 熔断抛错）。"""
    chain = CompositeProvider([_EmptyThs()])
    for _ in range(6):  # 6 次 > 阈值 3，若计失败必炸
        assert _run(chain.get_anomaly_list(None)) == []


class _BoomThs:
    name = "ths"
    realtime = False

    async def get_anomaly_list(self, tag_codes):
        raise RuntimeError("upstream down")


def test_composite_anomaly_error_raises():
    chain = CompositeProvider([_BoomThs()])
    try:
        _run(chain.get_anomaly_list(None))
        raise AssertionError("should raise")
    except ProviderError as exc:
        assert "all providers failed" in str(exc)


# ---------- A2：涨停池 max_seal_money ----------

def test_limit_up_pool_parses_max_seal_money(monkeypatch):
    payload = {"item": [{
        "thscode": "600519.SH", "name": "贵州茅台", "last_price": 10.0,
        "price_change_ratio_pct": 10.01, "limit_up_time": "09:31:00",
        "seal_money": 5.0e8, "max_seal_money": 9.0e8,
        "turnover_rate": 1.2, "continue_day_cnt": 3, "continue_day_text": "3天3板",
        "limit_up_reason": "业绩预增",
    }]}

    async def fake_get(path, params):
        return payload

    p = ThsFuyaoProvider(api_key="test-key")
    monkeypatch.setattr(p, "_get", fake_get)
    rows = _run(p.get_limit_up_pool(date(2026, 9, 7)))
    assert rows[0].max_seal_money == 9.0e8
    assert rows[0].seal_amount == 5.0e8
    assert rows[0].first_seal_time == "09:31:00"


# ---------- 路由层 ----------

class _FakeProvider:
    name = "fake"

    def __init__(self, rows):
        self._rows = rows

    async def get_anomaly_list(self, tag_codes):
        return list(self._rows)

    async def get_anomaly_stock(self, symbols):
        return list(self._rows)


class _FakeHub:
    def __init__(self, rows):
        self.provider = _FakeProvider(rows)
        self.last_success_refresh = None

    def is_stale(self):
        return False


def _client(rows) -> TestClient:
    app = FastAPI()
    app.include_router(market_route.router)
    app.dependency_overrides[get_hub] = lambda: _FakeHub(rows)
    return TestClient(app)


def test_route_anomalies_ok():
    c = _client([_rec()])
    resp = c.get("/market/anomalies")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["records"][0]["symbol"] == "600519"
    assert data["note"] is None


def test_route_anomalies_empty_has_explicit_note():
    """空集必须带显式 note——绝不静默。"""
    c = _client([])
    data = c.get("/market/anomalies").json()["data"]
    assert data["records"] == []
    assert "无匹配异动" in data["note"]


def test_route_anomalies_rejects_bad_tag():
    c = _client([_rec()])
    assert c.get("/market/anomalies", params={"tags": "垃圾"}).status_code == 422


def test_route_anomalies_stock_validates_batch_size():
    c = _client([_rec()])
    assert c.get("/market/anomalies/stock", params={"symbols": ""}).status_code == 422
    assert c.get("/market/anomalies/stock", params={"symbols": ",".join(f"{600000 + i}" for i in range(51))}).status_code == 422
    ok = c.get("/market/anomalies/stock", params={"symbols": "600519"})
    assert ok.status_code == 200 and ok.json()["data"]["records"][0]["symbol"] == "600519"
