"""R4 前后端指标黄金样本对照——涨跌停幅度（后端侧）。

共享样本：tests/golden/price_limit_golden.json（前端 vitest
apps/web/lib/price-limit-golden.test.ts 消费同一文件）。
- be 字段 = 后端 price_rules.limit_pct 期望值；null 表示该样本后端不适用（跳过）；
- divergent 样本是设计内已知分歧（halt_risk ST 决策后统一），照常断言各自行为；
- 口径变更时必须更新样本并双端同批提交——本测试红 = 两端漂移或样本过期。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.market.price_rules import limit_pct, limit_up_distance, limit_up_distance_text

GOLDEN = Path(__file__).resolve().parent / "golden" / "price_limit_golden.json"


def _trusted(**values):
    return {"quality": "high", "source": "tencent", "data_timestamp": datetime.now(timezone.utc).isoformat(),
            **values}


def _cases() -> list[dict]:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases = data.get("cases")
    assert isinstance(cases, list) and cases, "黄金样本结构异常：缺 cases 数组"
    return cases


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["symbol"] or "(empty)")
def test_price_limit_backend_matches_golden(case: dict) -> None:
    be = case.get("be")
    if be is None:
        pytest.skip(f"后端不适用（{case.get('note') or '无指数/无涨跌停概念'}）")
    assert limit_pct(case["symbol"], case.get("name")) == be, (
        f"涨跌停口径漂移：{case['symbol']}({case.get('name')}) 期望 {be}。"
        f"若是合法口径变更，更新 golden 样本并与前端同批提交"
        f"{('；分歧样本：' + case['divergent']) if case.get('divergent') else ''}"
    )


def test_golden_covers_key_segments() -> None:
    """样本覆盖面守卫：关键代码段缺失即样本本身不完整（防删样本偷懒）。

    2026-09-11 补 302132 / 889123 / 870804：302 与 88 两段此前**无样本**
    ⇒ 双端已实际漂移（后端判 10 / 前端判 20、30）却永不报警。段样本是
    「漂移会被发现」的唯一机制，删样本等于关掉报警器。
    """
    syms = {c["symbol"] for c in _cases()}
    for required in ("600519", "300750", "688981", "920075", "300999", "sh000001",
                     "302132", "889123", "870804"):
        assert required in syms, f"黄金样本缺关键段：{required}"
    divergents = [c for c in _cases() if c.get("divergent")]
    assert divergents, "分歧样本必须显式留档（删掉=隐藏口径分歧）"


@pytest.mark.parametrize("symbol,name,upper,expected", [
    ("600519", "普通主板", 11.0, 10.0),
    ("600073", "ST源限价", 10.5, 5.0),
    ("300750", "创业板", 12.0, 20.0),
    ("688981", "科创板", 12.0, 20.0),
    ("920075", "北交所", 13.0, 30.0),
])
def test_actual_limit_distance_uses_source_price_not_board_inference(symbol, name, upper, expected):
    # ST 的源限价特意给 5% 档：展示不得用当前代码段规则覆盖源提供的实际价格。
    got = limit_up_distance(_trusted(symbol=symbol, name=name, price=10.0, limit_up_price=upper))
    assert got["limit_up_gap_pct"] == expected
    assert got["limit_up_price"] == upper and got["limit_up_gap_state"] == "ready"


def test_actual_limit_distance_respects_source_tick_price_and_half_up_display():
    # 源实际涨停价11.06已完成到分取整，不能用未取整的11.055或9.7%判定线替代。
    got = limit_up_distance(_trusted(symbol="600519", price=10.8, prev_close=10.05, limit_up_price=11.06))
    assert got["limit_up_gap_pct"] == 2.41
    assert limit_up_distance_text(got) == "距实际涨停价还需上涨 2.41%"
    assert limit_up_distance(_trusted(price=200.0, limit_up_price=200.01))["limit_up_gap_pct"] == 0.01
    assert limit_up_distance(_trusted(price=11.06, limit_up_price=11.06))["limit_up_gap_pct"] == 0.0


@pytest.mark.parametrize("price,upper", [
    (None, 11.0), (10.0, None), (0, 11.0), (-1, 11.0), (10.0, 0),
    (float("nan"), 11.0), (10.0, float("inf")), (True, 11.0), (10.0, "bad"),
    (11.01, 11.0), (1e-300, 1e300),
])
def test_actual_limit_distance_invalid_or_missing_prices_stay_unknown(price, upper):
    got = limit_up_distance(_trusted(symbol="600519", price=price, limit_up_price=upper,
                                     prev_close=10.0, change_pct=7.2))
    assert got["limit_up_gap_pct"] is None and got["limit_up_gap_state"] == "unknown"
    assert limit_up_distance_text(got) == "距实际涨停价待核对"


@pytest.mark.parametrize("identity", [
    {"symbol": "000001", "market": "SH"}, {"symbol": "399001", "market": "SZ"},
    {"symbol": "885123"}, {"symbol": "600519", "security_type": "index"},
    {"symbol": "600519", "name": "N新股"}, {"symbol": "300001", "name": "C新股"},
    {"symbol": "600519", "quality": "stale"}, {"symbol": "600519", "quality": "invalid"},
])
def test_actual_limit_distance_does_not_claim_limit_for_index_special_or_bad_quote(identity):
    got = limit_up_distance(_trusted(**identity, price=10.0, limit_up_price=11.0))
    assert got["limit_up_gap_pct"] is None


@pytest.mark.parametrize("failure", [
    "missing_quality", "medium", "missing_source", "mock", "missing_time", "time_only",
    "naive_time", "previous_day", "future", "stale",
])
def test_snapshot_actual_distance_requires_source_quality_and_current_source_time(failure):
    now = datetime(2026, 10, 9, 2, 30, tzinfo=timezone.utc)
    row = _trusted(price=10.0, limit_up_price=11.0, data_timestamp=now.isoformat())
    if failure == "missing_quality": row.pop("quality")
    elif failure == "medium": row["quality"] = "medium"
    elif failure == "missing_source": row.pop("source")
    elif failure == "mock": row["source"] = "mock_fallback"
    elif failure == "missing_time": row.pop("data_timestamp")
    elif failure == "time_only": row["data_timestamp"] = "10:30:00"
    elif failure == "naive_time": row["data_timestamp"] = "2026-10-09T10:30:00"
    elif failure == "previous_day": row["data_timestamp"] = (now - timedelta(days=1)).isoformat()
    elif failure == "future": row["data_timestamp"] = (now + timedelta(seconds=1)).isoformat()
    elif failure == "stale": row["data_timestamp"] = (now - timedelta(seconds=121)).isoformat()
    assert limit_up_distance(row, now=now)["limit_up_gap_state"] == "unknown"


def test_snapshot_fallback_aware_ticktime_and_fresh_boundary_are_accepted():
    now = datetime(2026, 10, 9, 2, 30, tzinfo=timezone.utc)
    row = {"symbol": "600519", "price": 10.0, "limit_up_price": 11.0,
           "quality": "high", "source": "tencent_fallback",
           "ticktime": (now - timedelta(seconds=120)).isoformat()}
    assert limit_up_distance(row, now=now)["limit_up_gap_pct"] == 10.0
