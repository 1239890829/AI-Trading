"""B4 天梯交叉验证测试：provider 解析（真实 fixture）/ 纯函数 / 对照逻辑 / 路由缓存。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.sentiment.ladder_check import MIN_BASE, TOLERANCE, ladder_rates, run_ladder_check


# ---------------------------------------------------------------- provider 解析（fixture 取自 2026-08-31 实抓）


def _real_ladder_payload() -> dict:
    """实测要点：seal_nextday 是布尔（文档写 string）；梯队无 4 只上限；sign_level 恒 0。"""
    return {
        "timestamp": 1788150000000,
        "window": {"length": 30, "date_list": ["2026-08-31", "2026-08-28"]},
        "item": [
            {
                "date": "2026-08-31",
                "boards": {"two_board": [
                    {"thscode": "603559.SH", "ticker": "603559", "name": "中通国脉",
                     "board_num": 2, "seal_nextday": None, "sign_level": 0},
                ]},
            },
            {
                "date": "2026-08-28",
                "boards": {
                    "two_board": [
                        {"thscode": "600540.SH", "ticker": "600540", "name": "新赛股份",
                         "board_num": 2, "seal_nextday": True, "sign_level": 0},
                        {"thscode": "600654.SH", "ticker": "600654", "name": "中安科",
                         "board_num": 2, "seal_nextday": False, "sign_level": 0},
                    ],
                    "three_board": [
                        {"thscode": "0000000.SZ", "ticker": "x", "name": "坏代码",
                         "board_num": 3, "seal_nextday": True, "sign_level": 0},
                    ],
                },
            },
        ],
    }


def test_ths_limit_up_ladder_parses_real_payload(monkeypatch: pytest.MonkeyPatch):
    from app.data_providers.ths import ThsFuyaoProvider

    async def fake_get(self, path, params=None):
        return _real_ladder_payload()

    monkeypatch.setattr(ThsFuyaoProvider, "_get", fake_get)
    rows = asyncio.run(ThsFuyaoProvider(api_key="k").get_limit_up_ladder())

    assert len(rows) == 3, "坏代码剔除后 2+1 行"
    r = next(x for x in rows if x["symbol"] == "600540")
    assert r["seal_nextday"] is True and r["tier"] == "two_board" and r["board_num"] == 2
    latest = [x for x in rows if x["date"] == "2026-08-31"]
    assert latest[0]["seal_nextday"] is None, "最近交易日无次日参考"


def test_ths_limit_up_ladder_empty_raises(monkeypatch: pytest.MonkeyPatch):
    from app.data_providers.eastmoney import ProviderError
    from app.data_providers.ths import ThsFuyaoProvider

    async def fake_get(self, path, params=None):
        return {"item": []}

    monkeypatch.setattr(ThsFuyaoProvider, "_get", fake_get)
    with pytest.raises(ProviderError):
        asyncio.run(ThsFuyaoProvider(api_key="k").get_limit_up_ladder())


# ---------------------------------------------------------------- 纯函数


def test_ladder_rates_counts_and_skips_null():
    rows = [
        {"date": "2026-08-27", "tier": "two_board", "board_num": 2, "symbol": "a", "seal_nextday": True},
        {"date": "2026-08-27", "tier": "two_board", "board_num": 2, "symbol": "b", "seal_nextday": False},
        {"date": "2026-08-27", "tier": "three_board", "board_num": 3, "symbol": "c", "seal_nextday": True},
        {"date": "2026-08-27", "tier": "seven_over", "board_num": 7, "symbol": "d", "seal_nextday": False},
        {"date": "2026-08-28", "tier": "two_board", "board_num": 2, "symbol": "a", "seal_nextday": None},
    ]
    rates = ladder_rates(rows)
    assert rates["2026-08-27"]["p23"] == (1, 2), "two_board 档 1/2"
    assert rates["2026-08-27"]["high"] == (1, 2), "≥3板合并：three_board 命中 + seven_over 未封"
    assert "2026-08-28" not in rates, "全 null 日不计入"


# ---------------------------------------------------------------- 对照逻辑


def _pool(stocks: list[tuple[str, int]]):
    return [SimpleNamespace(symbol=s, consecutive_boards=b) for s, b in stocks]


def _fake_ths(ladder_rows, pools_by_date):
    class ThsFuyaoProvider:
        name = "ths"

        async def get_limit_up_ladder(self):
            return ladder_rows

        async def get_limit_up_pool(self, trade_date):
            return pools_by_date[trade_date.isoformat()]

    return ThsFuyaoProvider()


def _scenario(stocks_27, next_27, ladder_rows):
    """27 日池 stocks_27=[(sym, boards)]，28 日池 next_27；ladder_rows 描述 27 日天梯。"""
    pools = {
        "2026-08-27": _pool(stocks_27),
        "2026-08-28": _pool(next_27),
    }

    class _Hub:
        name = "fake"
        provider = _fake_ths(ladder_rows, pools)

    return _Hub()


def _anchor_row():
    """28 日锚行：让 27 日在 rates 日期序列里有"次日"可比（真实窗口 28 日同样有 seal 数据）。"""
    return {"date": "2026-08-28", "tier": "two_board", "board_num": 2, "symbol": "2b0", "seal_nextday": True}


def test_run_ladder_check_match_when_pools_agree_with_ladder():
    # 27 日：5 只二板（3 只晋级）+ 5 只三板（2 只存活）+ 2 只首板（不参与高位口径）
    stocks_27 = [(f"2b{i}", 2) for i in range(5)] + [(f"3b{i}", 3) for i in range(5)] + [("1b0", 1), ("1b1", 1)]
    next_27 = [("2b0", 3), ("2b1", 3), ("2b2", 3)] + [(f"3b{i}", 4) for i in range(2)] + [("1b0", 2)]
    ladder_rows = [
        {"date": "2026-08-27", "tier": "two_board", "board_num": 2, "symbol": f"2b{i}",
         "seal_nextday": i < 3}
        for i in range(5)
    ] + [
        {"date": "2026-08-27", "tier": "three_board", "board_num": 3, "symbol": f"3b{i}",
         "seal_nextday": i < 2}
        for i in range(5)
    ] + [_anchor_row()]

    async def main():
        return await run_ladder_check(_scenario(stocks_27, next_27, ladder_rows))

    r = asyncio.run(main())
    assert r["drifted"] == []
    day = r["checks"][-1]
    assert day["p23"]["ours"] == day["p23"]["ths"] == 0.6, "两边都应是 3/5"
    assert day["p23"]["verdict"] == "match"
    assert day["high"]["ours"] == day["high"]["ths"] == 0.4, "高位存活 2/5"
    assert day["high"]["verdict"] == "match"


def test_run_ladder_check_flags_drift_when_pipeline_broken():
    # 天梯说 3/5 晋级，两日池拼接却说 5/5 —— 拼接逻辑有问题必须被抓出来
    stocks_27 = [(f"2b{i}", 2) for i in range(5)]
    next_27 = [(f"2b{i}", 3) for i in range(5)]
    ladder_rows = [
        {"date": "2026-08-27", "tier": "two_board", "board_num": 2, "symbol": f"2b{i}",
         "seal_nextday": i < 3}
        for i in range(5)
    ] + [_anchor_row()]

    async def main():
        return await run_ladder_check(_scenario(stocks_27, next_27, ladder_rows))

    r = asyncio.run(main())
    day = r["checks"][-1]
    assert day["p23"]["verdict"] == "drift"
    assert day["p23"]["delta"] == round(1.0 - 0.6, 3)
    assert r["drifted"] == ["2026-08-27"]


def test_run_ladder_check_insufficient_on_small_sample():
    # 基数 1 < MIN_BASE：不判 drift（小样本一票 20%）
    stocks_27 = [("2b0", 2)]
    next_27 = [("2b0", 3)]
    ladder_rows = [
        {"date": "2026-08-27", "tier": "two_board", "board_num": 2, "symbol": "2b0", "seal_nextday": True},
        _anchor_row(),
    ]

    async def main():
        return await run_ladder_check(_scenario(stocks_27, next_27, ladder_rows))

    r = asyncio.run(main())
    assert r["checks"][-1]["p23"]["verdict"] == "insufficient"


def test_run_ladder_check_requires_ths_in_chain():
    class _Hub:
        name = "fake"
        provider = SimpleNamespace(name="mock")  # 无 providers、类型名不匹配

    async def main():
        return await run_ladder_check(_Hub())

    with pytest.raises(RuntimeError):
        asyncio.run(main())


def test_tolerance_constants_sane():
    assert 0 < TOLERANCE <= 0.2 and MIN_BASE >= 3


def test_ladder_check_route_and_cache(monkeypatch: pytest.MonkeyPatch):
    """路由：200 + 逐日 verdict；10 分钟缓存命中时 provider 只打一次。"""
    calls = {"n": 0}

    class ThsFuyaoProvider:
        name = "ths"

        async def get_limit_up_ladder(self):
            calls["n"] += 1
            return [
                {"date": "2026-08-27", "tier": "two_board", "board_num": 2, "symbol": f"2b{i}",
                 "name": f"股{i}", "seal_nextday": i < 3}
                for i in range(5)
            ] + [_anchor_row()]

        async def get_limit_up_pool(self, trade_date):
            if trade_date.isoformat() == "2026-08-27":
                return _pool([(f"2b{i}", 2) for i in range(5)])
            return _pool([("2b0", 3), ("2b1", 3), ("2b2", 3)])

    class _Hub:
        name = "fake"
        last_success_refresh = None

        def is_stale(self):
            return False

        provider = ThsFuyaoProvider()

    from fastapi import FastAPI

    from app.api.routes import market as market_route

    a = FastAPI()
    a.include_router(market_route.router, prefix="/api")
    a.dependency_overrides[market_route.get_hub] = lambda: _Hub()
    from app.core.errors import register_error_handlers

    register_error_handlers(a)
    from fastapi.testclient import TestClient

    with TestClient(a) as client:
        r1 = client.get("/api/market/ladder-check")
        client.get("/api/market/ladder-check")

    assert r1.status_code == 200
    data = r1.json()["data"]
    assert data["checks"] and data["checks"][-1]["p23"]["verdict"] == "match"
    assert calls["n"] == 1, "第二次请求命中缓存，天梯只拉一次"
