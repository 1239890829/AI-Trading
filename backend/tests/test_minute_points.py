"""腾讯分钟分时解析测试（docs/archive/minute-chart-plan.md 模块 0）。

重点覆盖"错了也看不出来"的地方：
- 非交易日请求时 ts 必须落在响应自带的真实交易日，而不是 datetime.now()；
- **第 3 列是累计量（手）而非分钟量**（2026-08-30 交叉验证确认）——
  旧实现把它当分钟量，量能柱画成"递增的累计柱"，均价线算出荒谬值；
- 均价线 = 累计成交额 / 累计量（股），量纲先做"手→股"换算。
"""

import asyncio
import json
from pathlib import Path
import sys
from datetime import date

import httpx
import pytest

sys.path.insert(0, ".")

from app.data_providers.tencent import TencentProvider, build_minute_points, to_tencent_symbol


def test_build_minute_points_uses_official_trade_date_not_now():
    """周六请求拿到周五数据：ts 必须是 20260828（响应 date），而非本地"今天"。"""
    rows = ["0930 1289.00 81 10440900.00", "0931 1293.46 434 55993745.00"]
    pts = build_minute_points(rows, "20260828", date(2026, 8, 30))
    assert len(pts) == 2
    # 北京时间 09:30 → UTC 01:30
    assert pts[0]["ts"].startswith("2026-08-28T01:30")
    assert pts[1]["ts"].startswith("2026-08-28T01:31")


def test_third_column_is_cumulative_volume_not_per_minute():
    """核心回归：第 3 列是累计量。分钟量 = 本行累计 − 上行累计。

    茅台 8/28 实拍数据交叉验证：0931 行累计 434 手，与 0930 行 81 手差分
    353 手 × ~1292 元 ≈ 4560 万，与累计额差分（55993745−10440900）吻合——
    若 434 是分钟量则金额对不上。
    """
    rows = ["0930 1289.00 81 10440900.00", "0931 1293.46 434 55993745.00"]
    pts = build_minute_points(rows, "20260828", date(2026, 8, 28))
    assert pts[0]["cum_volume"] == 8100        # 手→股
    assert pts[1]["cum_volume"] == 43400
    assert pts[0]["volume"] == 8100            # 首点分钟量 = 累计量
    assert pts[1]["volume"] == 35300           # 差分
    # 均价 = 累计额 / 累计量（股）
    assert pts[0]["avg"] == round(10440900 / 8100, 3)
    assert pts[1]["avg"] == round(55993745 / 43400, 3)


def test_build_minute_points_avg_stays_inside_price_envelope():
    """均价线是量的加权平均，必须落在当日价格包络内——越界即量纲错误。"""
    rows = [
        "0930 10.00 100 100000.00",
        "0931 11.00 200 210000.00",
        "0932 10.50 260 262500.00",
    ]
    pts = build_minute_points(rows, "20260828", date(2026, 8, 28))
    lo = min(p["price"] for p in pts)
    hi = max(p["price"] for p in pts)
    for p in pts:
        assert lo <= p["avg"] <= hi


def test_build_minute_points_skips_bad_rows():
    rows = ["garbage", "0930 0 100 1000.00", "0932 10.50 50 52500.00"]
    pts = build_minute_points(rows, "20260828", date(2026, 8, 28))
    assert len(pts) == 1
    assert pts[0]["price"] == 10.5
    assert pts[0]["avg"] == 10.5  # 首个有效点：avg=price


def test_build_minute_points_falls_back_when_date_missing():
    rows = ["0930 10.00 100 100000.00"]
    pts = build_minute_points(rows, "", date(2026, 8, 28))
    assert pts[0]["ts"].startswith("2026-08-28T01:30")
    pts2 = build_minute_points(rows, "bad-date", date(2026, 8, 28))
    assert pts2[0]["ts"].startswith("2026-08-28T01:30")


@pytest.fixture
def current_minute_payload():
    return json.loads((Path(__file__).parent / "fixtures/tencent_minute_current.json").read_text())


def _run_provider_minute(payload, symbol="600127"):
    async def read():
        provider = TencentProvider()
        await provider._client.aclose()
        calls = []

        def transport(request):
            calls.append(request.url)
            assert request.url.path == "/appstock/app/minute/query"
            assert request.url.params["code"] == to_tencent_symbol(symbol)
            return httpx.Response(200, json=payload)

        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        try:
            result = await provider.get_minute_line(symbol)
            assert len(calls) == 1  # Evidence comes from this payload, never another quote request.
            return result
        finally:
            await provider.aclose()

    return asyncio.run(read())


def test_same_payload_quote_proves_only_current_tail_interval_end(current_minute_payload):
    node = current_minute_payload["data"]["sh600127"]
    original = build_minute_points(node["data"]["data"], node["data"]["date"], date(2026, 10, 12), "600127")
    points = _run_provider_minute(current_minute_payload)
    assert points[:-1] == original[:-1]
    assert {key: value for key, value in points[-1].items() if key not in {"observation_ts", "time_label"}} == original[-1]
    assert points[-1]["ts"] == "2026-10-12T05:48:00+00:00"
    assert points[-1]["observation_ts"] == "2026-10-12T05:47:01+00:00"
    assert points[-1]["time_label"] == "interval_end"


@pytest.mark.parametrize(("clock", "label"), [
    ("093022", "0931"), ("130022", "1301"),
    ("112922", "1130"), ("145922", "1500"),
])
def test_proven_forming_opening_and_closing_minutes_keep_source_label(current_minute_payload, clock, label):
    node = current_minute_payload["data"]["sh600127"]
    node["data"]["data"] = [f"{label} 15.40 3352158 5327559491"]
    node["qt"]["sh600127"][30] = "20261012" + clock
    point = _run_provider_minute(current_minute_payload)[0]
    assert point["time_label"] == "interval_end"
    assert point["observation_ts"].endswith(f"{int(clock[:2])-8:02}:{clock[2:4]}:{clock[4:]}+00:00")
    assert point["ts"].endswith(f"{int(label[:2])-8:02}:{label[2:]}:00+00:00")


@pytest.mark.parametrize(("symbol", "expected_volume"), [("sh600127", 335215800), ("688981", 3352158)])
def test_observation_identity_supports_prefixed_stock_and_star_share_units(current_minute_payload, symbol, expected_volume):
    code = to_tencent_symbol(symbol)
    node = current_minute_payload["data"].pop("sh600127")
    fields = node["qt"].pop("sh600127")
    fields[2] = code[2:]
    node["qt"][code] = fields
    current_minute_payload["data"][code] = node
    point = _run_provider_minute(current_minute_payload, symbol)[-1]
    assert point["time_label"] == "interval_end"
    assert point["cum_volume"] == expected_volume


@pytest.mark.parametrize("case", [
    "missing_qt", "wrong_qt_key", "short_qt", "invalid_clock", "invalid_clock_date",
    "numeric_clock", "cross_day", "missing_trade_date", "wrong_identity", "wrong_price",
    "wrong_volume", "missing_volume", "nonfinite_price", "nonfinite_volume", "two_minute_gap",
])
def test_unproven_tail_keeps_prior_minute_contract(current_minute_payload, case):
    node = current_minute_payload["data"]["sh600127"]
    qt = node["qt"]["sh600127"]
    if case == "missing_qt":
        node.pop("qt")
    elif case == "wrong_qt_key":
        node["qt"] = {"sh600519": qt}
    elif case == "short_qt":
        node["qt"]["sh600127"] = qt[:30]
    elif case == "missing_trade_date":
        node["data"].pop("date")
    elif case == "two_minute_gap":
        qt[30] = "20261012134601"
    else:
        index, value = {
            "invalid_clock": (30, "invalid"), "invalid_clock_date": (30, "20261312134701"),
            "numeric_clock": (30, 20261012134701), "cross_day": (30, "20261009134701"),
            "wrong_identity": (2, "600519"), "wrong_price": (3, "15.41"),
            "wrong_volume": (36, "3352157"), "missing_volume": (36, ""),
            "nonfinite_price": (3, "nan"), "nonfinite_volume": (36, "inf"),
        }[case]
        qt[index] = value
    points = _run_provider_minute(current_minute_payload)
    assert all("observation_ts" not in point and "time_label" not in point for point in points)
    assert points[-1]["price"] == 15.4


@pytest.mark.parametrize(("clock", "label"), [
    ("093000", "0930"), ("130000", "1300"), ("113000", "1130"), ("150000", "1500"),
    ("092922", "0930"), ("125922", "1300"), ("113022", "1131"), ("150022", "1501"),
])
def test_sentinel_and_noncontinuous_source_clock_never_gain_end_label(current_minute_payload, clock, label):
    node = current_minute_payload["data"]["sh600127"]
    node["data"]["data"] = [f"{label} 15.40 3352158 5327559491"]
    node["qt"]["sh600127"][30] = "20261012" + clock
    point = _run_provider_minute(current_minute_payload)[0]
    assert "observation_ts" not in point and "time_label" not in point
    assert point["ts"].endswith(f"{int(label[:2])-8:02}:{label[2:]}:00+00:00")


def test_minute_endpoint_schema_retains_proven_source_observation(current_minute_payload, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI

    from app.api.deps import get_hub
    from app.api.routes.market_quotes import router
    from app.market import minute_backfill
    from app.schemas.envelope import MinuteLinePayload

    monkeypatch.setattr(minute_backfill, "load_vr_baseline", lambda _symbol: None)

    async def read():
        provider = TencentProvider()
        await provider._client.aclose()
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=current_minute_payload)))
        hub = SimpleNamespace(provider=provider, last_success_refresh=None, is_stale=lambda: False)
        api = FastAPI()
        api.include_router(router, prefix="/api")
        api.dependency_overrides[get_hub] = lambda: hub
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://isolated") as client:
                response = await client.get("/api/minute-line/600127")
            assert response.status_code == 200
            data = response.json()["data"]
            last = data["points"][-1]
            assert last["observation_ts"] == "2026-10-12T05:47:01+00:00"
            assert last["time_label"] == "interval_end"
            model = MinuteLinePayload.model_validate(data)
            assert model.points[-1].observation_ts == last["observation_ts"]
            assert all(point.observation_ts is None and point.time_label is None for point in model.points[:-1])
        finally:
            await provider.aclose()

    asyncio.run(read())
