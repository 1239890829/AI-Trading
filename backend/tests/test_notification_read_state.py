"""通知已读状态持久化测试（2026-09-12 缺陷修复）。

回归背景：已读状态此前**只存浏览器 localStorage**（按 origin 命名空间），
换源／换 profile／清站点数据 → 徽标回到 65（= 通知总数）。本文件钉死三件事：
① 合并**单调**（水位取大、id 取并集，任何一侧都不会把已读变回未读）；
② 服务端能真正落库并读回；
③ 接口三态诚实——DB 正常时往返一致，非法载荷 422，不静默吞。
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------- 纯合并函数
def test_merge_is_monotonic_on_both_directions():
    """水位/清除水位取大——**两个方向**都不回退（本地领先与服务端领先都被覆盖）。"""
    from app.services.notification_read_state import merge_read_state

    # 本地领先：服务端存量大也不该把本地水位拉回去
    out = merge_read_state(
        {"seen_before": 100, "read_ids": [], "clear_before": 50},
        {"seen_before": 900, "read_ids": [], "clear_before": 10},
    )
    assert out["seen_before"] == 900
    assert out["clear_before"] == 50  # 各自取大，不互相牵连

    # 服务端领先：提交旧值不会把已读水位打回去（这正是「已读变未读」的成因）
    out2 = merge_read_state(
        {"seen_before": 900, "read_ids": [], "clear_before": 50},
        {"seen_before": 100, "read_ids": [], "clear_before": 0},
    )
    assert out2["seen_before"] == 900
    assert out2["clear_before"] == 50


def test_merge_read_ids_is_union_and_stable():
    from app.services.notification_read_state import merge_read_state

    out = merge_read_state(
        {"seen_before": 0, "read_ids": ["a", "b"], "clear_before": 0},
        {"seen_before": 0, "read_ids": ["b", "c"], "clear_before": 0},
    )
    assert out["read_ids"] == ["a", "b", "c"]  # 并集且去重，顺序稳定


def test_merge_caps_ids_keeping_newest():
    """上界只保留尾部（较新登记的一批），防止异常客户端灌爆单行 JSON。"""
    from app.services.notification_read_state import READ_IDS_MAX, merge_read_state

    stored = [f"old-{i}" for i in range(READ_IDS_MAX)]
    out = merge_read_state(
        {"seen_before": 0, "read_ids": stored, "clear_before": 0},
        {"seen_before": 0, "read_ids": ["newest"], "clear_before": 0},
    )
    assert len(out["read_ids"]) == READ_IDS_MAX
    assert out["read_ids"][-1] == "newest"
    assert "old-0" not in out["read_ids"]  # 最旧的被挤出


def test_merge_tolerates_missing_and_dirty_fields():
    from app.services.notification_read_state import merge_read_state

    out = merge_read_state({}, {})
    assert out == {"seen_before": 0, "read_ids": [], "clear_before": 0}
    out2 = merge_read_state({"seen_before": None, "read_ids": None}, {"seen_before": 5})
    assert out2["seen_before"] == 5
    assert out2["read_ids"] == []


# ---------------------------------------------------------------- 服务层（独立库，不干扰共享夹具）
@pytest.fixture()
def isolated_sf(tmp_path):
    """独立 SQLite 文件库 + 建表 → 不触碰模块级共享内存库（避免用例间串状态）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.notification import NotificationReadState
    from app.models.watchlist import Base

    engine = create_engine(f"sqlite:///{tmp_path/'state.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    # 建表依赖上面那次 import（模型把表注册进 metadata）。显式断言表名：
    # 既自检「表真的建出来了」，也避免 pyflakes 把它当成未使用导入（本文件是门禁项）。
    assert NotificationReadState.__tablename__ in Base.metadata.tables
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_load_state_defaults_to_empty_not_error(isolated_sf):
    from app.services.notification_read_state import load_state

    state = load_state(isolated_sf)
    assert state == {"seen_before": 0, "read_ids": [], "clear_before": 0, "updated_at": None}


def test_save_then_load_roundtrip(isolated_sf):
    from app.services.notification_read_state import load_state, save_state

    saved = save_state({"seen_before": 1789179822129, "read_ids": ["alert-1"], "clear_before": 0}, isolated_sf)
    assert saved["seen_before"] == 1789179822129
    assert saved["updated_at"] is not None

    again = load_state(isolated_sf)
    assert again["seen_before"] == 1789179822129
    assert again["read_ids"] == ["alert-1"]


def test_save_with_stale_value_does_not_regress(isolated_sf):
    """提交旧值不改动已读水位——「重启后变未读」的直接防线。"""
    from app.services.notification_read_state import load_state, save_state

    save_state({"seen_before": 5000, "read_ids": ["x"], "clear_before": 3000}, isolated_sf)
    out = save_state({"seen_before": 1, "read_ids": [], "clear_before": 0}, isolated_sf)
    assert out["seen_before"] == 5000
    assert out["clear_before"] == 3000
    assert out["read_ids"] == ["x"]
    assert load_state(isolated_sf)["seen_before"] == 5000


def test_save_handles_dirty_row_without_crashing(isolated_sf):
    """库内 read_ids 是坏 JSON 时读取退回空表，后续写入能自愈。"""
    from app.services.notification_read_state import load_state, save_state

    with isolated_sf() as db:
        from app.models.notification import NotificationReadState

        db.add(NotificationReadState(id=1, seen_before=7, read_ids="{not json", clear_before=0))
        db.commit()

    assert load_state(isolated_sf)["read_ids"] == []
    out = save_state({"seen_before": 9, "read_ids": ["ok"], "clear_before": 0}, isolated_sf)
    assert out["seen_before"] == 9
    assert out["read_ids"] == ["ok"]
    assert json.loads(json.dumps(out))["read_ids"] == ["ok"]  # 可序列化（进 Envelope 的前提）


# ---------------------------------------------------------------- 接口
def test_get_read_state_shape(client):
    r = client.get("/api/notifications/read-state")
    assert r.status_code == 200
    d = r.json()["data"]
    assert set(d) == {"seen_before", "read_ids", "clear_before", "updated_at"}
    assert isinstance(d["seen_before"], int) and d["seen_before"] >= 0
    assert isinstance(d["read_ids"], list)
    assert isinstance(d["clear_before"], int) and d["clear_before"] >= 0


def test_put_read_state_roundtrip_and_monotonic(client):
    """往返一致 + 旧值不回退。断言用「不小于」：本模块共享库，不假设起点为空。"""
    sentinel = 9_999_999_999_999  # 远大于任何真实 epoch ms，保证单调断言跨用例成立
    r = client.put(
        "/api/notifications/read-state",
        json={"seen_before": sentinel, "read_ids": ["alert-9001"], "clear_before": sentinel},
    )
    assert r.status_code == 200
    assert r.json()["data"]["seen_before"] == sentinel
    assert "alert-9001" in r.json()["data"]["read_ids"]

    back = client.get("/api/notifications/read-state").json()["data"]
    assert back["seen_before"] == sentinel
    assert back["clear_before"] == sentinel

    # 提交更旧的状态：服务端必须保持单调，吐回的还是已有的大值
    stale = client.put(
        "/api/notifications/read-state",
        json={"seen_before": 1, "read_ids": [], "clear_before": 1},
    )
    assert stale.json()["data"]["seen_before"] == sentinel
    assert stale.json()["data"]["clear_before"] == sentinel
    assert "alert-9001" in stale.json()["data"]["read_ids"]


@pytest.mark.parametrize(
    "payload",
    [
        {"seen_before": -1},
        {"clear_before": -5},
        {"seen_before": "not-a-number"},
        {"read_ids": "not-a-list"},
    ],
)
def test_put_read_state_rejects_invalid_payload(client, payload):
    """非法载荷显式 422，不静默钳制（钳制会把客户端 bug 藏起来）。"""
    r = client.put("/api/notifications/read-state", json=payload)
    assert r.status_code == 422
