"""akshare 扩展数据服务与路由测试（零网络：注入假 akshare 模块）。

项目未配置 anyio/asyncio pytest 插件（惯例全同步测试）——异步服务方法用
``asyncio.run`` 包装。依赖覆盖键必须是路由实际引用的 ``app.api.deps.get_hub``。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import date

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_hub
from app.api.routes import ext_data as ext_data_route
from app.services.akshare_ext import AkshareExtError, AkshareExtService, get_akshare_ext


# ---------- 假 akshare 模块 ----------

def _fake_akshare(up_rows=None, dt_rows=None, boom=False):
    mod = types.ModuleType("akshare")
    mod.__version__ = "9.9.9-fake"
    calls = {"zt": 0, "dt": 0}

    def _df(rows):
        return pd.DataFrame(rows) if rows is not None else pd.DataFrame()

    def zt(date: str):  # noqa: A002 —— 对齐 akshare 签名
        calls["zt"] += 1
        if boom:
            raise ConnectionError("push2ex gone")
        return _df(up_rows)

    def dt(date: str):  # noqa: A002
        calls["dt"] += 1
        return _df(dt_rows)

    mod.stock_zt_pool_em = zt
    mod.stock_zt_pool_dtgc_em = dt
    mod.macro_china_cpi = lambda: pd.DataFrame([{"月份": "2026-08", "全国-当月同比": 0.3}])
    mod.stock_margin_account_info = lambda: pd.DataFrame([{"日期": "2026-09-04", "融资余额": 1.0}])
    mod._calls = calls
    return mod


UP_ROWS = [
    # 代码 故意给 int（模拟 pandas 解析丢前导零）+ NaN 最新价
    {"代码": 600519, "名称": "贵州茅台", "最新价": float("nan"), "涨跌幅": 10.01, "连板数": 3, "涨停统计": "3天3板"},
    {"代码": 2539, "名称": "新亚强", "最新价": 22.1, "涨跌幅": 9.98, "连板数": None, "涨停统计": "1天1板"},
]
DT_ROWS = [{"代码": 301010, "名称": "晶雪节能", "最新价": 9.1, "涨跌幅": -10.0, "连续跌停天数": 2, "开板次数": 1}]


@pytest.fixture()
def fake_ak(monkeypatch):
    mod = _fake_akshare(up_rows=UP_ROWS, dt_rows=DT_ROWS)
    monkeypatch.setitem(sys.modules, "akshare", mod)
    return mod


@pytest.fixture()
def blocked_ak(monkeypatch):
    monkeypatch.setitem(sys.modules, "akshare", None)  # import akshare → ImportError
    return None


def _run(coro):
    return asyncio.run(coro)


# ---------- 服务层 ----------

def test_status_not_installed_is_explicit(blocked_ak):
    st = AkshareExtService().status()  # status 是同步方法（仅本地导入探测）
    assert st["available"] is False
    assert st["kind"] == "not_installed"


def test_pool_normalizes_code_and_nan(fake_ak):
    up = _run(AkshareExtService().limit_up_pool(date(2026, 9, 4)))
    syms = [r["symbol"] for r in up]
    assert syms == ["600519", "002539"]  # int→zfill(6) 恢复前导零
    assert up[0]["price"] is None  # NaN → None
    assert up[0]["consecutive_boards"] == 3


def test_pool_cached(fake_ak):
    svc = AkshareExtService()
    _run(svc.limit_up_pool(date(2026, 9, 4)))
    _run(svc.limit_up_pool(date(2026, 9, 4)))
    assert fake_ak._calls["zt"] == 1  # 第二次走缓存


def test_pool_error_not_cached_then_recovers(fake_ak, monkeypatch):
    monkeypatch.setitem(sys.modules, "akshare", _fake_akshare(up_rows=UP_ROWS, boom=True))
    svc = AkshareExtService()
    with pytest.raises(AkshareExtError) as ei:
        _run(svc.limit_up_pool(date(2026, 9, 4)))
    assert ei.value.kind == "source_error"
    # 异常不缓存：数据源恢复后重试成功（真实世界 = 同一模块内网络恢复；测试等价替换持有引用）
    svc._mod = fake_ak
    up = _run(svc.limit_up_pool(date(2026, 9, 4)))
    assert len(up) == 2


def test_limit_down_pool_fields(fake_ak):
    dn = _run(AkshareExtService().limit_down_pool(date(2026, 9, 4)))
    assert dn[0]["symbol"] == "301010"
    assert dn[0]["consecutive_days"] == 2


# ---------- 路由层 ----------

class _Rec:
    def __init__(self, symbol, name, reason=None):
        self._d = {"symbol": symbol, "name": name, "reason": reason}

    def model_dump(self, mode="json"):
        return dict(self._d)


class _FakeProvider:
    async def get_limit_up_pool(self, trade_date):
        return [_Rec("600519", "贵州茅台"), _Rec("000001", "平安银行")]

    async def get_limit_down_pool(self, trade_date):
        return [_Rec("301010", "晶雪节能")]


class _FakeHub:
    provider = _FakeProvider()


def _make_client(fake_ak_mod) -> TestClient:
    app = FastAPI()
    app.include_router(ext_data_route.router)
    app.dependency_overrides[get_hub] = lambda: _FakeHub()

    svc = AkshareExtService()
    if fake_ak_mod is not None:
        svc._mod = fake_ak_mod
        svc._import_tried = True
    app.dependency_overrides[get_akshare_ext] = lambda: svc
    return TestClient(app)


def test_route_crosscheck_counts_and_diff(fake_ak):
    client = _make_client(fake_ak)
    resp = client.get("/ext/akshare/pool-crosscheck", params={"date": "2026-09-04"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["composite"]["up_count"] == 2
    assert data["akshare_counts"]["up_count"] == 2
    # 000001 只在 composite；2539(002539) 只在 akshare
    only_c = [d["symbol"] for d in data["up_diff"]["only_in_composite"]]
    only_a = [d["symbol"] for d in data["up_diff"]["only_in_akshare"]]
    assert only_c == ["000001"]
    assert only_a == ["002539"]
    assert data["down_diff"]["only_in_composite"] == []


def test_route_crosscheck_akshare_down_is_explicit(blocked_ak):
    client = _make_client(None)
    resp = client.get("/ext/akshare/pool-crosscheck", params={"date": "2026-09-04"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["akshare"]["available"] is False
    assert data["akshare"]["kind"] == "not_installed"
    assert data["composite"]["up_count"] == 2  # composite 侧照常
    assert "note" in data  # 显式降级说明，绝不静默装作"两源一致"


def test_route_crosscheck_bad_date():
    client = _make_client(None)
    resp = client.get("/ext/akshare/pool-crosscheck", params={"date": "2026/09/04"})
    assert resp.status_code == 422


def test_route_status(fake_ak):
    client = _make_client(fake_ak)
    resp = client.get("/ext/akshare/status")
    assert resp.status_code == 200
    assert resp.json()["data"]["available"] is True
    assert resp.json()["data"]["version"] == "9.9.9-fake"


def test_no_proxy_guard_set():
    """模块导入即 NO_PROXY setdefault '*'（不覆盖显式配置）——防 macOS 死系统代理。"""
    import app.services.akshare_ext as ext_mod

    assert ext_mod._service is not None  # 导入副作用：模块级单例就绪
    assert os.environ.get("NO_PROXY") is not None
