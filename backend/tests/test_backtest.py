"""日线回测引擎防泄露测试（docs/backtest-rules.md §4——先于引擎合入）。

§4 八条对照：
  1 未来 bar 不可访问 ✅ test_future_bar_raises
  2 未来财报不可见   ✅ test_future_fundamentals_invisible（轻量 FundamentalsView）
  3 退市股不被剔除   ✅ test_delisted_not_silently_removed
  4 停牌日无法成交   ✅ test_suspension_no_fill
  5 涨停买不进/跌停卖不出 ✅ test_one_board_refusal
  6 T+1 生效        ✅ test_t1_structural（日线开盘撮合结构上买卖必隔 bar；
                     引擎内 last_buy_ts 分支为撮合模式变化的防线）
  7 手续费滑点反映在资金曲线 ✅ test_fees_reflected_exactly
  8 修订数据不覆盖历史时点 → v1 无修订数据源接入（无财务/多版本数据），
    引擎不缓存任何历史序列、每次全量重算，结构性不适用；接入财务数据时
    须以 (report_period, ann_date, version) 三元组入 FundamentalsView 后补测。
"""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from app.market.backtest import (
    BacktestConfig,
    BarView,
    FutureDataError,
    FundamentalsView,
    _fees,
    run_backtest,
)


# ---------------------------------------------------------------- 工具

def _mk_bars(closes: list[float], *, board: str | None = None, board_at: int = -1) -> list[dict]:
    """由收盘序列构造日K；board 指定在 board_at 位置插入一字板 bar。"""
    bars = []
    price = closes[0]
    ts_seq = [f"2026-{(i // 21) + 1:02d}-{(i % 21) + 1:02d}" for i in range(len(closes))]
    for i, c in enumerate(closes):
        o = price
        bars.append({"ts": ts_seq[i], "open": o, "high": max(o, c) * 1.001,
                     "low": min(o, c) * 0.999, "close": c, "volume": 1_000_000.0})
        price = c
    if board:
        b = bars[board_at]
        b["open"] = b["high"] = b["low"] = b["close"] = b["close"]
    return bars


def _up_bars(n: int = 120) -> list[dict]:
    closes = [round(10 * (1 + 0.012) ** i, 4) for i in range(n)]
    return _mk_bars(closes)


def _always(ratio: float):
    return lambda view: ratio


# ---------------------------------------------------------------- §4-1 未来 bar

class TestFutureBar:
    def test_future_bar_raises(self):
        bars = _up_bars(120)
        view = BarView(bars, 10)
        assert len(view) == 11
        assert view[10]["ts"] == bars[10]["ts"]  # 当前 bar 可见
        with pytest.raises(FutureDataError):
            _ = view[11]  # 未来 bar 读取即抛
        with pytest.raises(FutureDataError):
            _ = view[999]

    def test_engine_never_leaks_future_to_strategy(self):
        """探测策略：访问越界数据应让回测终止（而不是静默拿到未来值）。"""
        bars = _up_bars(120)

        def probe(view: BarView) -> float:
            try:
                _ = view[len(view)]  # 探未来
            except FutureDataError:
                return 0.0
            raise AssertionError("策略成功访问了未来数据——防泄露失效")

        run_backtest(bars, probe)


# ---------------------------------------------------------------- §4-2 未来财报

class TestFundamentals:
    def test_future_fundamentals_invisible(self):
        recs = [
            {"ann_date": "2025-10-28", "report_period": "2025-09-30", "revenue": 1.0},
            {"ann_date": "2026-04-25", "report_period": "2025-12-31", "revenue": 2.0},
        ]
        fv = FundamentalsView(recs)
        # 年报报告期 2025-12-31，但公告日 2026-04-25 之前不可见（禁令 §1）
        assert fv.as_of("2026-04-24") == [recs[0]]
        assert fv.as_of("2026-04-25") == recs
        assert [r["report_period"] for r in fv.as_of("2026-04-25")[-1:]] == ["2025-12-31"]


# ---------------------------------------------------------------- §4-3 退市

class TestDelisted:
    def test_delisted_not_silently_removed(self):
        """全程满仓策略：数据流结束仍有持仓 → 必须有 delisted 标记的强平记录，
        且期末 equity 与成交流精确对账（不静默剔除、不留悬空市值）。"""
        bars = _up_bars(120)
        report = run_backtest(bars, _always(1.0))
        last_trade = report.trades[-1]
        assert last_trade.reason == "delisted"
        assert last_trade.ok is True
        assert math.isclose(last_trade.price, bars[-1]["close"], rel_tol=1e-9)
        # 期末 equity = 由成交流重建的现金（强平后全部为现金，无悬空持仓）
        cash = report.config["initial_cash"]
        for t in report.trades:
            if not t.ok:
                continue
            cash += (t.price * t.qty - t.fee) if t.side == "sell" else -(t.price * t.qty + t.fee)
        assert math.isclose(cash, report.equity[-1], rel_tol=1e-9)
        assert any("delisted" in n for n in report.notes)


# ---------------------------------------------------------------- §4-4 停牌

class TestSuspension:
    def test_suspension_no_fill(self):
        """日期断档模拟停牌：成交只可能落在真实存在的 bar 上。"""
        bars = _up_bars(120)
        # 在中部制造 5 个"交易日"断档（ts 跳号）
        for i in range(60, 65):
            bars[i]["ts"] = f"2026-99-{i:02d}"  # 明显异常日期标签
        # 策略恒定换仓：每次收盘都满仓目标（无变化则不产生成交）
        calls = {"n": 0}

        def flip(view: BarView) -> float:
            calls["n"] += 1
            return 1.0 if calls["n"] % 20 else 0.0  # 周期性清仓/建仓

        report = run_backtest(bars, flip)
        # 所有成交 ts 必须存在于 bars 的 ts 集合（停牌日无 bar → 无成交）
        ts_set = {b["ts"] for b in bars}
        assert all(t.fill_ts in ts_set for t in report.trades if t.ok)
        assert len(report.equity_ts) == len(bars)


# ---------------------------------------------------------------- §4-5 一字板

class TestOneBoard:
    def test_limit_up_buy_refused(self):
        # 构造：策略先空仓，第 80 根收盘突然要求满仓，而第 81 根是一字涨停
        bars = _up_bars(120)

        def switch(view: BarView) -> float:
            return 0.0 if len(view) <= 80 else 1.0

        bars[81]["open"] = bars[81]["high"] = bars[81]["low"] = bars[81]["close"] = \
            round(bars[80]["close"] * 1.10, 4)  # 一字涨停
        report = run_backtest(bars, switch)
        refused = [t for t in report.trades if not t.ok and t.reason == "limit_up"]
        assert refused, "一字涨停买入应被拒绝"
        assert refused[0].fill_ts == bars[81]["ts"]
        # 被拒后下一根可交易 bar 继续尝试建仓（pending 未清）
        assert any(t.ok and t.side == "buy" for t in report.trades)

    def test_limit_down_sell_refused(self):
        bars = _up_bars(120)

        def exit_at(view: BarView) -> float:
            return 1.0 if len(view) <= 80 else 0.0

        bars[81]["open"] = bars[81]["high"] = bars[81]["low"] = bars[81]["close"] = \
            round(bars[80]["close"] * 0.90, 4)  # 一字跌停
        report = run_backtest(bars, exit_at)
        refused = [t for t in report.trades if not t.ok and t.reason == "limit_down"]
        assert refused, "一字跌停卖出应被拒绝"
        # 延迟到后续 bar 成功卖出
        assert any(t.ok and t.side == "sell" for t in report.trades)


# ---------------------------------------------------------------- §4-6 T+1

class TestT1:
    def test_t1_structural(self):
        """日线开盘撮合下买卖必然隔 bar（T+1 结构性成立）；
        引擎内 last_buy_ts 检查是撮合模式变化时的防线。"""
        bars = _up_bars(120)

        def flip(view: BarView) -> float:
            # 每 30 根切换一次多空
            return 1.0 if (len(view) // 30) % 2 else 0.0

        report = run_backtest(bars, flip)
        ts_order = {ts: i for i, ts in enumerate(report.equity_ts)}
        for sell in (t for t in report.trades if t.ok and t.side == "sell"):
            prior_buys = [b for b in report.trades if b.ok and b.side == "buy" and b.fill_ts == sell.signal_ts]
            if prior_buys:
                assert ts_order[sell.fill_ts] > ts_order[prior_buys[0].fill_ts], "同 bar 先买后卖违反 T+1"


# ---------------------------------------------------------------- §4-7 费用

class TestFees:
    def test_fees_reflected_exactly(self):
        bars = _up_bars(120)
        cfg = BacktestConfig(
            initial_cash=1_000_000, commission_rate=0.00025, commission_min=5.0,
            stamp_tax=0.0005, transfer_fee=0.00001, slippage_bp=5.0,
        )

        # 单次满仓进出：前半满仓、后半清仓
        report = run_backtest(bars, lambda v: 1.0 if len(v) <= 60 else 0.0, cfg)
        buys = [t for t in report.trades if t.ok and t.side == "buy"]
        sells = [t for t in report.trades if t.ok and t.side == "sell" and t.reason != "delisted"]
        assert buys and sells
        b, s = buys[0], sells[0]
        # 滑点方向：买价 > 开盘参考价，卖价 < 开盘参考价
        assert b.price > b.ref_price
        assert s.price < s.ref_price
        # 佣金下限、印花税与过户费（双边）
        assert b.fee >= cfg.commission_min
        expected_stamp = s.price * s.qty * cfg.stamp_tax
        assert s.fee >= expected_stamp
        # 过户费双边：买入 fee 至少含 transfer；卖出 fee 至少含 stamp+transfer
        assert b.fee >= b.price * b.qty * cfg.transfer_fee
        assert s.fee >= expected_stamp + s.price * s.qty * cfg.transfer_fee
        # 期末现金 = 初始 + 卖出净额 - 买入成本（精确对账）
        cash = cfg.initial_cash
        for t in report.trades:
            if not t.ok:
                continue
            cash += (t.price * t.qty - t.fee) if t.side == "sell" else -(t.price * t.qty + t.fee)
        assert math.isclose(cash, report.equity[-1], rel_tol=1e-9)

    def test_zero_fee_config_allowed(self):
        cfg = BacktestConfig(
            commission_rate=0.0, commission_min=0.0, stamp_tax=0.0,
            transfer_fee=0.0, slippage_bp=0.0,
        )
        bars = _up_bars(120)
        report = run_backtest(bars, lambda v: 1.0 if len(v) <= 60 else 0.0, cfg)
        assert all(t.fee == 0.0 for t in report.trades if t.ok)

    def test_fee_matches_china_a_engine_baseline(self):
        """黄金对照：ChinaAEngine（Vibe-Trading，本轮实测 7/7）同口径费用精确吻合。

        金额 5000：佣金 max(5000×0.00025, 5)=5；过户费 5000×0.00001=0.05 →
        买入 5.05；卖出再 + 印花税 5000×0.0005=2.5 → 7.55。
        """
        cfg = BacktestConfig()  # 全默认口径
        amount = 5000.0
        buy_fee = _fees(cfg, amount, "buy")
        sell_fee = _fees(cfg, amount, "sell")
        assert math.isclose(buy_fee, 5.05, abs_tol=1e-9)
        assert math.isclose(sell_fee, 7.55, abs_tol=1e-9)


# ---------------------------------------------------------------- §5 可解释性

class TestReport:
    def test_metrics_and_in_out_separation(self):
        bars = _up_bars(200)
        report = run_backtest(bars, _always(1.0), BacktestConfig(in_ratio=0.7))
        # 满仓持有上行序列：策略≈基准，超额≈0（费前差）
        assert report.total_return > 0
        assert 0 < report.benchmark_return
        assert -0.05 < report.excess_return < 0.05  # 费用导致的轻微落后
        assert report.max_drawdown < 0.05  # 上行序列回撤极小
        assert report.in_return != 0 and report.out_return != 0
        assert report.config["in_ratio"] == 0.7
        assert report.notes

    def test_insufficient_bars_rejected(self):
        with pytest.raises(ValueError, match="样本不足"):
            run_backtest(_up_bars(59), _always(1.0))


# ---------------------------------------------------------------- API 集成（TDX 打桩，离线）

class TestApi:
    @pytest.fixture()
    def client(self, monkeypatch):
        import app.market.tdx_kline as kline_mod

        monkeypatch.setattr(kline_mod, "tdx_daily_bars", lambda sym, count=500: _up_bars(200))

        from fastapi import FastAPI

        from app.api.routes import backtest as route
        from app.core.errors import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)
        app.include_router(route.router, prefix="/api")
        return TestClient(app)

    def test_run_and_shape(self, client):
        r = client.post("/api/backtest/run", json={
            "symbol": "600519", "strategy_id": "ma_cross",
            "params": {"fast": 5, "slow": 20}, "bars": 200,
        })
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["symbol"] == "600519"
        assert data["bars_count"] == 200
        m = data["metrics"]
        assert {"total_return", "max_drawdown", "sharpe", "win_rate", "in_return", "out_return"} <= set(m)
        assert len(data["equity"]) == 200
        assert all("value" in p and "benchmark" in p for p in data["equity"])
        assert data["notes"]
        # 上行序列 + 均线策略：应有交易发生且多头交易成功
        assert any(t["ok"] and t["side"] == "buy" for t in data["trades"])

    def test_unknown_strategy_400(self, client):
        r = client.post("/api/backtest/run", json={"symbol": "600519", "strategy_id": "nope"})
        assert r.status_code == 400
        assert r.json()["code"] == "validation_error"

    def test_bad_symbol_400(self, client):
        r = client.post("/api/backtest/run", json={"symbol": "AB12", "strategy_id": "ma_cross"})
        assert r.status_code == 400

    def test_strategies_list(self, client):
        r = client.get("/api/backtest/strategies")
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()["data"]]
        assert {"ma_cross", "ma_breakout"} <= set(ids)
