"""个股资金流（stock_flow）+ watcher 大单异动状态机单测——零网络。"""
from __future__ import annotations

import asyncio

from app.market import stock_flow
from app.picks import watcher as watcher_mod
from app.picks.watcher import FLOW_SURGE_YI, IntradayWatcher


# ---- secid 映射 ----

def test_stock_secid_mapping():
    assert stock_flow.stock_secid("600519") == "1.600519"
    assert stock_flow.stock_secid("000001") == "0.000001"
    assert stock_flow.stock_secid("300750") == "0.300750"
    # 北交所无个股资金流数据 → None（显式无数据，不冒充）
    assert stock_flow.stock_secid("830799") is None
    assert stock_flow.stock_secid("920001") is None
    assert stock_flow.stock_secid("430047") is None
    assert stock_flow.stock_secid("60051") is None
    assert stock_flow.stock_secid("") is None


# ---- 响应解析 ----

def test_parse_stock_flow_rows():
    rows = [
        {"f12": "600540", "f14": "新赛股份", "f62": "45000000", "f66": "20000000",
         "f72": "25000000", "f78": "-10000000", "f84": "-5000000", "f184": "12.5",
         "f124": 1788830000},
        {"f12": "000001", "f14": "平安银行", "f62": "-", "f66": "-", "f72": "-",
         "f78": "-", "f84": "-", "f184": "-", "f124": None},
    ]
    items = stock_flow.parse_stock_flow(rows)
    sh = items["600540"]
    assert sh["main"] == 0.45  # 亿
    assert sh["main_pct"] == 12.5
    assert sh["available"] is True
    assert sh["as_of"] is not None
    pz = items["000001"]
    assert pz["main"] is None and pz["available"] is False  # 全 None 不冒充 available


# ---- 降级路径 ----

class _FailClient:
    def __init__(self):
        self.calls = 0

    async def get(self, *a, **kw):
        self.calls += 1
        raise RuntimeError("boom")


def test_get_stock_flow_all_failed(monkeypatch):
    monkeypatch.setattr(stock_flow, "_CACHE", stock_flow.TTLCache("t-stock-flow-test", ttl=30.0, maxsize=2))
    fail = _FailClient()
    monkeypatch.setattr(stock_flow, "_http", lambda: fail)
    out = asyncio.run(stock_flow.get_stock_flow(["600519", "830799"]))
    assert out["items"] == {}
    assert out["degraded"] and "不可用" in out["degraded"][0]
    assert out["no_data"] == ["830799"]  # 北交所显式列缺
    assert fail.calls >= 3  # 重试语义保留


class _OkResp:
    def raise_for_status(self):
        return None

    def json(self):
        return {"data": {"diff": [
            {"f12": "600519", "f14": "贵州茅台", "f62": "100000000", "f66": "0",
             "f72": "0", "f78": "0", "f84": "0", "f184": "5.0", "f124": 1},
        ]}}


class _OkClient:
    async def get(self, *a, **kw):
        return _OkResp()


def test_get_stock_flow_success_and_cache(monkeypatch):
    cache = stock_flow.TTLCache("t-stock-flow-ok", ttl=30.0, maxsize=2)
    monkeypatch.setattr(stock_flow, "_CACHE", cache)
    client = _OkClient()
    monkeypatch.setattr(stock_flow, "_http", lambda: client)
    out = asyncio.run(stock_flow.get_stock_flow(["600519"]))
    assert out["items"]["600519"]["main"] == 1.0
    assert out["degraded"] == []
    # 命中缓存：换一个会抛异常的客户端，结果不变
    monkeypatch.setattr(stock_flow, "_http", lambda: _FailClient())
    out2 = asyncio.run(stock_flow.get_stock_flow(["600519"]))
    assert out2["items"]["600519"]["main"] == 1.0


# ---- watcher 大单异动状态机 ----

def _flow(main):
    return {"available": True, "main": main, "name": "X"}


def test_flow_surge_first_break_then_dedup():
    w = IntradayWatcher([])
    assert w._step_flows({"600540": _flow(FLOW_SURGE_YI - 0.01)}) == []  # 未破线不报
    alerts = w._step_flows({"600540": _flow(FLOW_SURGE_YI + 0.1)})
    assert len(alerts) == 1 and alerts[0]["key"] == "flow-surge-600540"
    assert alerts[0]["kind"] == "flow_surge"
    assert alerts[0]["meta"]["threshold"] == FLOW_SURGE_YI
    # 首破语义：继续流入不再报
    assert w._step_flows({"600540": _flow(FLOW_SURGE_YI + 0.5)}) == []
    assert w.state()["flow_alerted"] == ["600540"]


def test_flow_surge_ignores_unavailable_and_negative():
    w = IntradayWatcher([])
    # available=False 的票直接跳过（无数据无从跟踪，三态：缺席≠0）
    assert w._step_flows({"600540": {"available": False, "main": None}}) == []
    assert w._step_flows({"000001": _flow(-0.9)}) == []  # 流出侧不报（炸板/跌停池覆盖）
    assert w.state()["flow_tracked"] == 1  # 只有可判定的 000001 进入跟踪


# ---- P1-16 源头收紧：阈值配置化 + 每拍上限 ----

def test_flow_surge_threshold_from_settings(monkeypatch):
    """阈值走 settings（运维可调），非正/非法回退默认——不静默改口径。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "picks_flow_surge_yi", 2.5, raising=False)
    assert watcher_mod.flow_surge_yi() == 2.5
    w = IntradayWatcher([])
    assert w._step_flows({"600540": _flow(2.4)}) == []       # 未破新阈值
    got = w._step_flows({"600540": _flow(2.6)})
    assert got and got[0]["meta"]["threshold"] == 2.5

    monkeypatch.setattr(settings, "picks_flow_surge_yi", 0, raising=False)
    assert watcher_mod.flow_surge_yi() == FLOW_SURGE_YI
    monkeypatch.setattr(settings, "picks_flow_surge_yi", "abc", raising=False)
    assert watcher_mod.flow_surge_yi() == FLOW_SURGE_YI


def test_flow_surge_per_beat_cap_queues_rest():
    """每拍按净额取 Top N；未入选的不置 alerted，下一拍仍有机会（是排队不是丢弃）。"""
    w = IntradayWatcher([])
    cap = watcher_mod.FLOW_ALERT_PER_BEAT
    flows = {f"6000{i:02d}": _flow(1.0 + i * 0.1) for i in range(cap + 3)}
    first = w._step_flows(flows)
    assert len(first) == cap
    # 降序取 Top：最大净额的先出
    assert first[0]["meta"]["trigger_value"] == round(1.0 + (cap + 2) * 0.1, 3)
    assert len(w.state()["flow_alerted"]) == cap
    # 下一拍把剩余的补出来（同一批 flows 再次传入）
    rest = w._step_flows(flows)
    assert len(rest) == 3
    assert len(w.state()["flow_alerted"]) == cap + 3
    # 第三拍全部已报 → 无新增
    assert w._step_flows(flows) == []
