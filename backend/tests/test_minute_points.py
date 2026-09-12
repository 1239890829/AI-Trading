"""腾讯分钟分时解析测试（docs/archive/minute-chart-plan.md 模块 0）。

重点覆盖"错了也看不出来"的地方：
- 非交易日请求时 ts 必须落在响应自带的真实交易日，而不是 datetime.now()；
- **第 3 列是累计量（手）而非分钟量**（2026-08-30 交叉验证确认）——
  旧实现把它当分钟量，量能柱画成"递增的累计柱"，均价线算出荒谬值；
- 均价线 = 累计成交额 / 累计量（股），量纲先做"手→股"换算。
"""

import sys
from datetime import date

sys.path.insert(0, ".")

from app.data_providers.tencent import build_minute_points


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
