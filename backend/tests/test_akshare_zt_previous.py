"""akshare_ext.zt_pool_previous 与 /ext/akshare/zt-previous 路由测试。"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.akshare_ext import AkshareExtError, AkshareExtService


def test_zt_pool_previous_parses_rows(monkeypatch):
    import pandas as pd

    svc = AkshareExtService()
    df = pd.DataFrame([
        {"序号": "1", "代码": "002059", "名称": "云南旅游", "涨跌幅": -1.9,
         "最新价": "5.67", "昨日封板时间": "09:31:00", "昨日连板数": 2},
        {"序号": "2", "代码": 605, "名称": "零前导", "涨跌幅": 10.0},  # int 代码 → zfill
    ])
    monkeypatch.setattr(
        "akshare.stock_zt_pool_previous_em", lambda date: df, raising=False
    )

    import asyncio
    rows = asyncio.run(svc.zt_pool_previous(date(2026, 9, 7)))
    assert rows[0]["symbol"] == "002059"
    assert rows[0]["change_pct"] == -1.9
    assert rows[0]["prev_boards"] == 2
    assert rows[1]["symbol"] == "000605"  # 前导零还原


def test_zt_pool_previous_error_kind(monkeypatch):
    svc = AkshareExtService()
    monkeypatch.setattr(
        "akshare.stock_zt_pool_previous_em",
        lambda date: (_ for _ in ()).throw(RuntimeError("boom")),
        raising=False,
    )
    import asyncio

    with pytest.raises(AkshareExtError) as ei:
        asyncio.run(svc.zt_pool_previous(date(2026, 9, 7)))
    assert ei.value.kind == "source_error"
    assert "boom" in ei.value.detail


def test_zt_previous_route_degrades_explicitly(monkeypatch):
    """akshare 失败 → 路由返回 available=False + kind，绝不静默空数据。"""
    from fastapi.testclient import TestClient

    import app.api.routes.ext_data as ext
    from app.main import app

    class _Boom:
        async def zt_pool_previous(self, _d):
            raise AkshareExtError("source_error", "boom")

    app.dependency_overrides[ext.get_akshare_ext] = lambda: _Boom()
    try:
        client = TestClient(app)
        r = client.get("/api/ext/akshare/zt-previous", params={"date": "2026-09-07"})
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["available"] is False
        assert body["kind"] == "source_error"
    finally:
        app.dependency_overrides.pop(ext.get_akshare_ext, None)


def test_zt_previous_route_summary(monkeypatch):
    from fastapi.testclient import TestClient

    import app.api.routes.ext_data as ext
    from app.main import app

    class _Ok:
        async def zt_pool_previous(self, _d):
            return [
                {"change_pct": -1.9, "symbol": "002059"},
                {"change_pct": 5.0, "symbol": "000605"},
                {"change_pct": "bad", "symbol": "000001"},  # 非数值被排除
            ]

    app.dependency_overrides[ext.get_akshare_ext] = lambda: _Ok()
    try:
        client = TestClient(app)
        r = client.get("/api/ext/akshare/zt-previous", params={"date": "2026-09-07"})
        body = r.json()["data"]
        assert body["available"] is True
        assert body["summary"]["count"] == 2
        assert body["summary"]["red_ratio"] == 0.5
    finally:
        app.dependency_overrides.pop(ext.get_akshare_ext, None)
