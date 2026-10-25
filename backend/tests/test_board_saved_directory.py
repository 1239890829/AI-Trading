"""Read a bounded, dated local sample without reviving a live source or writing."""
import asyncio
from datetime import date, datetime
import json

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.deps import require_api_token
from app.api.routes.market_flow import router
from app.core import device_access as access
from app.core.bjtime import BJ_TZ
from app.core.config import settings
from app.market import board_flow as bf


def archive():
    return {"updated_at": "2026-10-23T17:02:57+08:00", "days": {
        "2026-10-22": {"boards": {
            "industry": [["BK0010", "旧行业", 4, 1, 2]],
            "concept": [["BK0020", "旧概念", 8, 2, 1]],
        }},
        "2026-10-23": {"boards": {
            "industry": [["BK0001", "正值", 1.5, 2.5, 3.5],
                         ["BK0002", "负值", -1.5, -2.5, -3.5],
                         ["BK0003", "合法零", 0, 0, 0]],
            "concept": [["BK0099", "概念", None, None, None]],
        }},
    }}


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "_DAILY_STORE", tmp_path / "daily.json")
    monkeypatch.setattr(bf, "_mem_memo", {"daily": (None, {}), "dayk": (None, {})})
    clock = {"now": datetime(2026, 10, 26, 10, tzinfo=BJ_TZ)}
    monkeypatch.setattr(bf, "beijing_now", lambda: clock["now"])
    monkeypatch.setattr(bf.trade_calendar, "_load_persisted", lambda: [date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26)])

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("saved directory must not fetch or synchronize")

    def no_write(*_args, **_kwargs):
        raise AssertionError("saved directory must not write runtime data")

    monkeypatch.setattr(bf, "get_board_list", forbidden)
    monkeypatch.setattr(bf, "_clist_pages", forbidden)
    monkeypatch.setattr(bf.trade_calendar, "trading_days", forbidden)
    monkeypatch.setattr(bf, "_write_json_atomic", no_write)
    return clock


def write(data):
    bf._DAILY_STORE.write_text(json.dumps(data, ensure_ascii=False))


def read(kind="industry"):
    return asyncio.run(bf.get_board_fund_flow(kind, "latest_saved"))


def test_latest_saved_keeps_original_values_time_scope_and_source(local):
    original = archive()
    write(original)
    before = bf._DAILY_STORE.read_bytes()
    data = read()
    assert data["available"] and data["kind"] == "industry" and data["range"] == "latest_saved"
    assert data["historical"] and data["as_of"] == "2026-10-23"
    assert data["stored_at"] == original["updated_at"]
    assert data["received_at"] is None and data["source_as_of"] is None
    assert data["source"] == "eastmoney_local_daily_snapshot"
    assert data["quality"] == "stale" and data["coverage_state"] == "local_sample"
    assert data["total_boards"] is None and data["coverage"] == 3 and data["sample_limit"] == 50
    assert [row["main_net_yi"] for row in data["rows"]] == [1.5, -1.5, 0]
    assert [row["change_pct"] for row in data["rows"]] == [3.5, -3.5, 0]
    assert [row["rank"] for row in data["rows"]] == [1, 2, 3]
    data["rows"][0]["name"] = "consumer mutation"
    assert read()["rows"][0]["name"] == "正值"
    assert bf._DAILY_STORE.read_bytes() == before
    assert json.loads(before) == original


def test_missing_numeric_fields_stay_unknown_not_zero(local):
    write(archive())
    data = read("concept")
    assert data["available"] and data["coverage"] == 1
    row = data["rows"][0]
    assert row["change_pct"] is None and row["main_net_yi"] is None and row["main_net_ratio"] is None
    assert row["main_net_5d_yi"] is None and row["leader_symbol"] is None
    assert any("未保存数值" in note for note in data["degraded"])


def test_no_cross_kind_or_cross_day_merge(local):
    data = archive()
    del data["days"]["2026-10-23"]["boards"]["industry"]
    write(data)
    result = read()
    assert result["as_of"] == "2026-10-22"
    assert [row["board_code"] for row in result["rows"]] == ["BK0010"]
    assert all(row["kind"] == "industry" for row in result["rows"])


@pytest.mark.parametrize("stamp", ["2026-10-27", "2026-10-26", "2026-10-24", "20261025", "not-a-date"])
def test_future_unfinished_nontrading_and_invalid_dates_are_excluded(local, stamp):
    data = archive()
    data["days"][stamp] = {"boards": {"industry": [["BK1234", "不可用时点", 10, 10, 10]]}}
    write(data)
    result = read()
    assert result["as_of"] == "2026-10-23"
    assert "BK1234" not in {row["board_code"] for row in result["rows"]}
    assert any("排除" in note for note in result["degraded"])


def test_today_is_readable_only_after_completed_snapshot_window(local):
    local["now"] = datetime(2026, 10, 26, 15, 5, tzinfo=BJ_TZ)
    data = archive()
    data["updated_at"] = "2026-10-26T15:05:00+08:00"
    data["days"]["2026-10-26"] = {"boards": {"industry": [["BK1234", "已完成", 10, 10, 10]]}}
    write(data)
    assert read()["as_of"] == "2026-10-26"


@pytest.mark.parametrize("bad_row", [
    ["600001", "坏代码", 1, 2, 3], ["BK1234", "", 1, 2, 3],
    ["BK1234", "缺字段", 1, 2], ["BK1234", "数值文本", "3", 2, 3],
    ["BK1234", "非有限数", float("nan"), 2, 3], ["BK1234", "无穷数", float("inf"), 2, 3],
    ["BK1234", "布尔非数字", True, 2, 3],
    ["BK1234", "错类记录", 1, 2, 3, "concept"], None,
])
def test_bad_latest_record_is_unavailable_not_hidden_by_older_snapshot(local, bad_row):
    data = archive()
    data["days"]["2026-10-23"]["boards"]["industry"] = [bad_row]
    write(data)
    result = read()
    assert result["available"] is False and result["rows"] == [] and result["reason"]
    assert result["kind"] == "industry" and result["range"] == "latest_saved"
    assert result["total_boards"] is None


@pytest.mark.parametrize("bad", [None, [], {"bad": []}, [["BK1234", "重复", 1, 2, 3]] * 2,
                                   [[f"BK{i:04d}", "越界样本", 1, 2, 3] for i in range(51)]])
def test_missing_bad_duplicate_or_oversized_sample_is_not_valid_directory(local, bad):
    data = {"days": {"2026-10-23": {"boards": {"industry": bad}}}}
    write(data)
    result = read()
    assert result["available"] is False and result["rows"] == [] and result["reason"]


@pytest.mark.parametrize("data", [{}, [], {"days": []}, {"days": {"2026-10-23": []}}])
def test_invalid_store_structure_is_an_explicit_unavailable(local, data):
    write(data)
    result = read()
    assert result["available"] is False and result["rows"] == [] and result["reason"]


def test_missing_or_corrupt_file_and_calendar_never_trigger_source_repair(local, monkeypatch):
    assert read()["available"] is False
    bf._DAILY_STORE.write_text("not JSON")
    assert read()["available"] is False
    write(archive())
    monkeypatch.setattr(bf.trade_calendar, "_load_persisted", lambda: None)
    assert read()["available"] is False


@pytest.mark.parametrize("stored", [None, "bad-clock", "2026-10-23T17:00:00", "2100-01-01T10:00:00+08:00"])
def test_bad_save_clock_is_unknown_not_replaced_by_read_time(local, stored):
    data = archive()
    data["updated_at"] = stored
    write(data)
    result = read()
    assert result["available"] and result["as_of"] == "2026-10-23"
    assert result["stored_at"] is None and result["source_as_of"] is None


def test_existing_paired_route_exposes_explicit_saved_range_without_new_privilege(local, tmp_path, monkeypatch):
    write(archive())
    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "devices.sqlite3")
    monkeypatch.setattr(settings, "api_token", "fixture-server")
    store = access.get_device_store()
    origin = "http://saved-directory.test:3000"
    admin, _ = store.bootstrap(store.bootstrap_code(), "测试管理设备", origin)
    token, row = store.start("普通设备", origin)
    store.manage(admin, origin, row["id"], "approve")
    assert store.authorize(token, origin)["maintenance"] is False
    app = FastAPI()
    app.include_router(router, prefix="/api", dependencies=[Depends(require_api_token)])
    headers = {"X-API-Token": "fixture-server", access.BROWSER_HEADER: "1",
               access.SESSION_HEADER: token, access.ORIGIN_HEADER: origin}
    with TestClient(app) as probe:
        url = "/api/market/board-fund-flow?kind=industry&range=latest_saved"
        assert probe.get(url).status_code == 401
        response = probe.get(url, headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["as_of"] == "2026-10-23"
        assert probe.get(url.replace("industry", "unknown"), headers=headers).status_code == 422
        assert probe.get(url.replace("latest_saved", "unknown"), headers=headers).status_code == 422
