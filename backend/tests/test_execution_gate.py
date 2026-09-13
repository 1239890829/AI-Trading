"""执行闸门（P0-A）测试：三态判定 + 组合采集 + 缺数据三态纪律。"""
import sys

sys.path.insert(0, ".")

import json
from types import SimpleNamespace

import pytest

from app.picks.execution_gate import (
    STATE_ANOMALY,
    STATE_BLOCKED,
    STATE_NORMAL,
    STATE_OBSERVE,
    STATE_UNKNOWN,
    classify_execution,
    collect_execution_gate,
)


def test_classify_five_states():
    assert classify_execution(9.9)["state"] == STATE_BLOCKED
    assert classify_execution(9.5)["state"] == STATE_BLOCKED  # 边界含
    assert classify_execution(6.0)["state"] == STATE_OBSERVE
    assert classify_execution(0.5)["state"] == STATE_NORMAL
    assert classify_execution(-4.9)["state"] == STATE_NORMAL
    assert classify_execution(-5.0)["state"] == STATE_ANOMALY
    assert classify_execution(None)["state"] == STATE_UNKNOWN  # 缺失 ≠ 平开
    # reason 都非空（可解释性）
    for g in (9.9, 6.0, 0.5, -5.0, None):
        assert classify_execution(g)["reason"]


def _seed_combo(items: list[dict], date: str = "2026-09-03"):
    from app.core.db import get_engine, get_session_factory
    from app.models.daily_pick import DailyPickSet
    from app.models.watchlist import Base

    Base.metadata.create_all(get_engine())
    sf = get_session_factory()
    with sf() as db:
        db.query(DailyPickSet).delete()
        db.add(DailyPickSet(
            date=date,
            items=json.dumps(items, ensure_ascii=False),
            meta="{}",
        ))
        db.commit()
    return sf


def _hub(auction_rows, exc: Exception | None = None):
    async def get_auction_snapshot(symbols, stage="final"):
        if exc:
            raise exc
        return auction_rows

    return SimpleNamespace(provider=SimpleNamespace(get_auction_snapshot=get_auction_snapshot))


def test_collect_gate_three_states_and_summary():
    sf = _seed_combo([
        {"symbol": "600001", "name": "甲", "score": 80},
        {"symbol": "600002", "name": "乙", "score": 75},
        {"symbol": "600003", "name": "丙", "score": 70},
        {"symbol": "600004", "name": "丁", "score": 65},
    ])
    hub = _hub([
        {"symbol": "600001", "auction_pct": 9.97, "data_status": "final"},
        {"symbol": "600002", "auction_pct": 6.2, "data_status": "final"},
        {"symbol": "600003", "auction_pct": 1.0, "data_status": "final"},
        # 600004 竞价快照缺行 → unknown
    ])
    out = collect_exec_sync(hub, sf)
    by_sym = {i["symbol"]: i for i in out["items"]}
    assert by_sym["600001"]["state"] == STATE_BLOCKED
    assert by_sym["600002"]["state"] == STATE_OBSERVE
    assert by_sym["600003"]["state"] == STATE_NORMAL
    assert by_sym["600004"]["state"] == STATE_UNKNOWN
    s = out["summary"]
    assert (s["blocked"], s["observe"], s["normal"], s["unknown"]) == (1, 1, 1, 1)
    assert s["executable"] == 1
    assert out["caveats"] == []


def test_collect_gate_data_status_not_final_is_unknown():
    """data_status 非 ready/final：字段有值也不可信 → unknown（不冒充）。"""
    sf = _seed_combo([{"symbol": "600001", "name": "甲"}])
    hub = _hub([{"symbol": "600001", "auction_pct": 0.5, "data_status": "loading"}])
    out = collect_exec_sync(hub, sf)
    assert out["items"][0]["state"] == STATE_UNKNOWN


def test_collect_gate_snapshot_failure_degrades_with_caveats():
    """竞价快照拉取整体失败：全 unknown + caveats 显式降级，绝不抛出。"""
    sf = _seed_combo([{"symbol": "600001", "name": "甲"}])
    hub = _hub([], exc=RuntimeError("ths 超时"))
    out = collect_exec_sync(hub, sf)
    assert out["items"][0]["state"] == STATE_UNKNOWN
    assert any("竞价快照" in c for c in out["caveats"])


def test_collect_gate_no_combo():
    from app.core.db import get_engine, get_session_factory
    from app.models.daily_pick import DailyPickSet
    from app.models.watchlist import Base

    Base.metadata.create_all(get_engine())
    sf = get_session_factory()
    with sf() as db:
        db.query(DailyPickSet).delete()
        db.commit()
    out = collect_exec_sync(_hub([]), sf)
    assert out["pick_date"] is None
    assert out["summary"] is None


def collect_exec_sync(hub, sf, **kw):
    import asyncio

    return asyncio.run(collect_execution_gate(hub, sf, **kw))


# ---------------------------------------------------------------- 路由


def test_route_execution_gate(monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import get_hub
    from app.api.routes import picks as picks_route

    _seed_combo([
        {"symbol": "600001", "name": "甲", "score": 80},
        {"symbol": "600002", "name": "乙", "score": 75},
    ])
    hub = _hub([
        {"symbol": "600001", "auction_pct": 9.97, "data_status": "final"},
        {"symbol": "600002", "auction_pct": 2.0, "data_status": "final"},
    ])

    app = FastAPI()
    app.include_router(picks_route.router, prefix="/api")
    app.dependency_overrides[get_hub] = lambda: hub
    app.state.hub = hub  # 端点内 cache_on 需要挂 state；get_hub 已被 override，但 state.hub 兜底
    with TestClient(app) as client:
        body = client.get("/api/picks/execution-gate").json()
        d = body["data"]
        assert d["pick_date"] == "2026-09-03"
        states = {i["symbol"]: i["state"] for i in d["items"]}
        assert states == {"600001": STATE_BLOCKED, "600002": STATE_NORMAL}
        assert d["summary"]["executable"] == 1
        # 缓存命中同结果
        body2 = client.get("/api/picks/execution-gate").json()
        assert body2["data"]["summary"] == d["summary"]


# ---------------------------------------------------------------- 条件化审计 A1（§6.25）


def test_thresholds_for_board_regimes():
    """按板块制度映射：主板与旧固定值逐字一致（历史行为不变），20/30cm 比例映射。"""
    assert vb_thresholds("600519") == (9.5, 5.0)
    assert vb_thresholds("300750") == (19.0, 10.0)
    assert vb_thresholds("688111") == (19.0, 10.0)
    assert vb_thresholds("920821") == (28.5, 15.0)


def vb_thresholds(sym):
    from app.picks.execution_gate import thresholds_for

    return thresholds_for(sym)


def test_classify_20cm_halfway_gap_is_not_blocked():
    """20cm 股竞价 +10%（半程高开，实测零期望）不得被判 blocked；主板同值仍 blocked。"""
    from app.picks.execution_gate import classify_execution

    # 创业板 +10%：用映射后阈值判定 → observe（非 blocked）
    v20 = classify_execution(10.0, block_ge=19.0, observe_ge=10.0)
    assert v20["state"] == "observe"
    # 主板 +10%：原阈值语义不变 → blocked（一字买不进）
    v10 = classify_execution(10.0, block_ge=9.5, observe_ge=5.0)
    assert v10["state"] == "blocked"
