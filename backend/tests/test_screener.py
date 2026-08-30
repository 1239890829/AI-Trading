"""选股器测试：tech_score 纯函数 + 截面过滤 + API 集成（TDX 打桩，离线运行）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.market.tech_score import score_stock
from app.services.screener_service import ScreenerService, filter_universe


# ---------------------------------------------------------------- 合成日K

def _trend_bars(direction: str, n: int = 120) -> list[dict]:
    """构造趋势清晰的合成日K：up=稳步上行+温和放量；down=阴跌缩量。"""
    bars: list[dict] = []
    price = 10.0
    for i in range(n):
        if direction == "up":
            chg = 0.012 + (i % 5) * 0.002  # ~1.2%-2% 震荡上行
            vol = 1_000_000 * (1 + (i % 5) * 0.3)  # 温和波动，不爆量
        else:
            chg = -0.011 - (i % 5) * 0.001
            vol = 1_000_000 * max(0.15, 1 - i * 0.008)  # 持续缩量不封底
        open_ = price
        close = round(price * (1 + chg), 3)
        high = max(open_, close) * 1.005
        low = min(open_, close) * 0.995
        bars.append({
            "ts": f"2026-{(i // 21) + 1:02d}-{(i % 21) + 1:02d}",
            "open": round(open_, 3), "high": round(high, 3),
            "low": round(low, 3), "close": close, "volume": float(vol),
        })
        price = close
    return bars


class TestScoreStock:
    def test_uptrend_scores_high_and_bull(self):
        r = score_stock(_trend_bars("up"), amount_rank_pct=0.8, turnover_rank_pct=0.8)
        assert r is not None
        assert r["grade"] in ("A", "B")
        assert r["bias"] == "bull"
        assert r["score"] >= 60
        names = {s["name"] for s in r["signals"]}
        assert {"MA排列", "MACD", "KDJ", "RSI14", "量价", "流动性"} <= names
        assert all(0 <= s["score"] <= 1 for s in r["signals"])
        # 红线 3：文案不得出现确定性建议
        assert "建议" not in r["summary"]

    def test_downtrend_scores_low_and_bear(self):
        r = score_stock(_trend_bars("down"), amount_rank_pct=0.2, turnover_rank_pct=0.2)
        assert r is not None
        assert r["bias"] == "bear"
        assert r["score"] <= 45
        assert r["grade"] in ("C", "D")

    def test_insufficient_bars_returns_none(self):
        assert score_stock(_trend_bars("up", n=59)) is None

    def test_liquidity_none_defaults_neutral(self):
        up = score_stock(_trend_bars("up"))
        up_with = score_stock(_trend_bars("up"), amount_rank_pct=0.9, turnover_rank_pct=0.9)
        assert up is not None and up_with is not None
        # 分位更高 → 流动性维度分更高 → 总分不低于默认
        assert up_with["score"] >= up["score"]

    def test_weights_sum_to_one(self):
        from app.market.tech_score import _WEIGHTS

        assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9


class TestFilterUniverse:
    def _row(self, sym: str, **kw) -> dict:
        base = {
            "symbol": sym, "name": f"股票{sym}", "price": 10.0,
            "change_pct": 3.0, "amount": 2.0e8, "turnover_rate": 5.0,
        }
        base.update(kw)
        return base

    def test_filters_st_bj_and_bands(self):
        rows = [
            self._row("600000"),
            self._row("600001", name="ST 某某"),
            self._row("920001"),  # 北交所
            self._row("600002", change_pct=10.5),  # 超涨幅带
            self._row("600003", amount=0.5e8),  # 成交额不足
            self._row("600004", turnover_rate=1.0),  # 换手不足
            self._row("600005", price=0),  # 停牌
        ]
        pool = filter_universe(
            rows, change_low=-2, change_high=9.5, min_amount_yi=1.0,
            min_turnover=2.0, exclude_st=True, exclude_bj=True, pool_limit=150,
        )
        assert [r["symbol"] for r in pool] == ["600000"]

    def test_amount_sort_desc_and_pool_limit(self):
        rows = [self._row(f"60000{i}", amount=(i + 1) * 1e8) for i in range(10)]
        pool = filter_universe(
            rows, change_low=-21, change_high=21, min_amount_yi=0,
            min_turnover=0, exclude_st=False, exclude_bj=False, pool_limit=3,
        )
        assert [r["symbol"] for r in pool] == ["600009", "600008", "600007"]


class TestApi:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        import polars as pl

        # 1) 伪造快照 parquet：3 只候选
        day = tmp_path / "snapshots" / "20260828"
        day.mkdir(parents=True)
        pl.DataFrame([
            {"symbol": "600100", "name": "甲股", "market": "SH", "price": 12.0,
             "change_pct": 3.0, "amount": 3.0e8, "turnover_rate": 6.0,
             "nmc": 50e4, "ticktime": "15:00:00"},
            {"symbol": "600200", "name": "乙股", "market": "SH", "price": 8.0,
             "change_pct": 1.0, "amount": 2.0e8, "turnover_rate": 4.0,
             "nmc": 30e4, "ticktime": "15:00:00"},
            {"symbol": "600300", "name": "ST 丙", "market": "SH", "price": 5.0,
             "change_pct": 2.0, "amount": 9.0e8, "turnover_rate": 9.0,
             "nmc": 10e4, "ticktime": "15:00:00"},
        ]).write_parquet(day / "150000.parquet")

        # 2) 打桩 TDX 日K：600100 上升趋势，600200 下降趋势
        import app.services.screener_service as svc_mod

        def fake_bars(symbol: str, count: int = 250):
            if symbol == "600100":
                return _trend_bars("up")
            if symbol == "600200":
                return _trend_bars("down")
            return None

        monkeypatch.setattr(svc_mod, "_tdx_daily_bars", fake_bars)

        from fastapi import FastAPI

        from app.api.routes import screener as route
        from app.core.errors import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)
        app.include_router(route.router, prefix="/api")
        app.state.screener_service = ScreenerService(parquet_dir=tmp_path)
        return TestClient(app)

    def test_end_to_end_ranking_and_payload(self, client):
        r = client.get("/api/screener")
        assert r.status_code == 200
        body = r.json()
        data = body["data"]
        assert data["scanned"] == 3
        assert data["filtered"] == 2  # ST 丙被排除
        assert data["scored"] == 2
        assert data["failed"] == 0
        assert data["snapshot_time"] == "15:00:00"
        items = data["items"]
        assert len(items) == 2
        # 上升趋势股排在下降趋势股前面
        assert items[0]["symbol"] == "600100"
        assert items[0]["score"] > items[1]["score"]
        assert items[0]["grade"] in ("A", "B")
        assert items[1]["bias"] == "bear"
        assert data["disclaimers"]
        assert body["meta"] == {}

    def test_cached_flag_on_second_hit(self, client):
        client.get("/api/screener")
        r2 = client.get("/api/screener")
        assert r2.json()["data"]["cached"] is True

    def test_bad_range_rejected(self, client):
        r = client.get("/api/screener", params={"change_low": 5, "change_high": -5})
        assert r.status_code == 400
        assert r.json()["code"] == "validation_error"

    def test_snapshot_unavailable_502(self, tmp_path, monkeypatch):
        from fastapi import FastAPI

        from app.api.routes import screener as route
        from app.core.errors import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)
        app.include_router(route.router, prefix="/api")
        app.state.screener_service = ScreenerService(parquet_dir=tmp_path)  # 无快照
        client = TestClient(app)
        r = client.get("/api/screener")
        assert r.status_code == 502
        assert r.json()["code"] == "snapshot_unavailable"
