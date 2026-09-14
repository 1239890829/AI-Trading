"""日线回测引擎防泄露测试（docs/backtest-rules.md §4——先于引擎合入）。

§4 八条对照：
  1 未来 bar 不可访问 ✅ test_future_bar_raises / test_negative_index_is_within_visible_window
                     （R13：负索引是**同一守卫**的漏口——`view[-1]` 曾返回末根=未来）
  2 未来财报不可见   ✅ test_future_fundamentals_invisible（轻量 FundamentalsView）
  3 退市股不被剔除   ✅ TestEndOfSample::test_explicit_delisting_event_settles_at_that_bar
                     （R14：原用例把「窗口结束」当退市断言，测的其实是错口径——
                     现改为**显式退市事件**验，窗口结束另立三例）
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

    def test_negative_index_is_within_visible_window(self):
        """🔴 R13：`view[-1]` 必须返回**当前 bar**，不是全序列最后一根（未来）。

        原实现把用户给的负索引直接丢给完整 `_bars` ⇒ `view[-1]` == 最后一根 bar。
        这是最常用的写法（`view[-1]["close"]` = 现价），比正索引越界更危险：
        正索引守卫全绿，而策略实际上拿到了整段未来。
        """
        bars = _up_bars(120)
        view = BarView(bars, 10)
        assert len(view) == 11

        assert view[-1]["ts"] == bars[10]["ts"], "view[-1] 必须是当前 bar（as_of），不是末根"
        assert view[-1]["ts"] != bars[-1]["ts"], "夹具前提：末根 bar 必须与 as_of 不同"
        assert view[-len(view)]["ts"] == bars[0]["ts"], "view[-len] 是最早可见 bar"

        # 越过可见起点的负索引必须抛，而不是回绕到完整序列的历史里
        with pytest.raises(FutureDataError):
            _ = view[-len(view) - 1]
        with pytest.raises(FutureDataError):
            _ = view[-999]

    def test_negative_index_does_not_leak_future_close(self):
        """绝对结果断言：负索引取到的**收盘价**必须等于 as_of 当根的价。"""
        bars = _up_bars(120)
        for i in (0, 1, 5, 59, 118):
            view = BarView(bars, i)
            assert view[-1]["close"] == bars[i]["close"], f"as_of={i} 时 view[-1] 泄漏了未来收盘"
            if i >= 1:
                assert view[-2]["close"] == bars[i - 1]["close"]

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


# ---------------------------------------------------------------- §4-3 退市与窗口结束（R14）

def _cash_from_trades(report) -> float:
    """由成交流重建现金（只算真实成交；synthetic 也算，因为它确实改了现金）。"""
    cash = report.config["initial_cash"]
    for t in report.trades:
        if not t.ok:
            continue
        cash += (t.price * t.qty - t.fee) if t.side == "sell" else -(t.price * t.qty + t.fee)
    return cash


class TestEndOfSample:
    """R14 · `backtest.py` 原实现把「回测窗口结束」冒充「退市」并强平。

    原缺陷三连：① 伪造 `ok=True` 的 `reason="delisted"` 卖单污染成交/胜率；
    ② 该强平发生在末根**收盘**，若末根开盘刚买入，则同一 bar 完成买卖 ⇒ **绕过 T+1**；
    ③ 完全不看末根是否跌停 ⇒ 跌停也能卖。
    本节按验收标准分四态验：仍在市但窗口结束 / 末日新买 / 末日跌停 / 显式退市事件。
    """

    def test_window_end_does_not_fake_delisting(self):
        """① 仍在市但窗口结束：**不得**出现任何 delisted 成交；持仓按末价估值。

        夹具用**平价**序列（单次买入、无整手抖动）：上行序列的近满仓抖动会插入
        小额卖出，把「末段是否多出一笔假成交」这个信号搅浑。
        """
        bars = _mk_bars([10.0] * 120)
        report = run_backtest(bars, _always(1.0))

        assert not [t for t in report.trades if t.reason == "delisted"], \
            "窗口结束不得冒充退市"
        assert not [t for t in report.trades if t.synthetic], "默认路径不得产生合成成交"

        op = report.open_position
        assert op is not None and op.qty > 0, "满仓持有到末根 → 应有未平仓持仓"
        assert math.isclose(op.mark_price, bars[-1]["close"], rel_tol=1e-9)
        assert math.isclose(op.mark_value, op.mark_price * op.qty, rel_tol=1e-9)
        assert math.isclose(op.unrealized_pnl, op.mark_value - op.cost, rel_tol=1e-9)
        assert op.liquidated_by == ""

        # 权益/未实现/真实成交三者分离：期末权益 = 已实现现金 + 未平仓市值
        assert math.isclose(
            report.equity[-1], _cash_from_trades(report) + op.mark_value, rel_tol=1e-9
        ), "期末权益必须 = 已实现现金 + 未平仓市值（浮动盈亏已含在内）"
        assert any("估值" in n and "未强制平仓" in n for n in report.notes), \
            "notes 必须说明末段持仓是估值而非成交"

    def test_last_bar_buy_is_not_sold_same_bar(self):
        """② 末日新买：信号在倒数第二根收盘产生 → 末根开盘买入，随后窗口结束。

        原实现在末根收盘强平，**同一 bar 完成买卖 ⇒ 绕过 T+1**；
        新实现必须仍是持仓（不产生任何卖单）。
        """
        bars = _up_bars(120)
        n = len(bars)
        report = run_backtest(bars, lambda v: 1.0 if len(v) == n - 1 else 0.0)

        last_ts = bars[-1]["ts"]
        assert not [t for t in report.trades if t.ok and t.side == "sell"], \
            "末根买入后不得在同 bar 卖出（T+1）"
        assert not [t for t in report.trades if t.fill_ts == last_ts and t.side == "sell"], \
            "末根不得有任何卖单记录"

        op = report.open_position
        assert op is not None and op.qty > 0
        assert op.entry_ts == last_ts, "夹具前提：建仓发生在末根"
        # 建仓确有其事（不是空仓误判）
        assert [t for t in report.trades if t.ok and t.side == "buy" and t.fill_ts == last_ts]

    def test_last_bar_limit_down_does_not_force_sell(self):
        """③ 末日跌停：末根一字跌停仍持仓 → 不得强平（原实现照卖不误）。"""
        bars = _up_bars(120)
        prev = bars[-2]["close"]
        bars[-1]["open"] = bars[-1]["high"] = bars[-1]["low"] = bars[-1]["close"] = round(prev * 0.90, 4)

        report = run_backtest(bars, _always(1.0))
        last_ts = bars[-1]["ts"]
        assert not [t for t in report.trades if t.ok and t.side == "sell" and t.fill_ts == last_ts], \
            "末根一字跌停不得成交卖出"
        assert not [t for t in report.trades if t.reason == "delisted"]
        op = report.open_position
        assert op is not None and op.qty > 0
        assert math.isclose(op.mark_price, bars[-1]["close"], rel_tol=1e-9)

    def test_explicit_delisting_event_settles_at_that_bar(self):
        """④ 显式退市事件：`delisted=True` 的 bar 上按该 bar 收盘清算，计为**真实**成交。"""
        bars = _up_bars(120)
        bars[-1]["delisted"] = True
        report = run_backtest(bars, _always(1.0))

        dl = [t for t in report.trades if t.reason == "delisted"]
        assert len(dl) == 1, "显式退市事件必须产生且仅产生一笔清算成交"
        assert dl[0].ok is True and dl[0].synthetic is False
        assert dl[0].fill_ts == bars[-1]["ts"]
        assert math.isclose(dl[0].price, bars[-1]["close"], rel_tol=1e-9)
        assert report.open_position is None, "退市清算后不得再有未平仓持仓"
        assert math.isclose(report.equity[-1], _cash_from_trades(report), rel_tol=1e-9)
        assert any("显式退市事件" in n for n in report.notes)

    def test_delisting_midstream_halts_further_trading(self):
        """退市后不再有撮合与信号（后续 bar 只推进净值）。"""
        bars = _up_bars(120)
        at = bars[80]["ts"]
        bars[80]["delisted"] = True
        report = run_backtest(bars, _always(1.0))

        assert len(report.equity_ts) == len(bars), "净值序列仍覆盖全部 bar"
        after = [t for t in report.trades if t.ok and t.fill_ts > at]
        assert not after, f"退市后不得再有成交：{after}"
        assert report.open_position is None
        assert math.isclose(report.equity[-1], _cash_from_trades(report), rel_tol=1e-9)

    def test_assumed_liquidation_is_synthetic_and_excluded_from_stats(self):
        """可选假设清算：标 synthetic、**不计真实成交/胜率**。

        判据两向：① 绝对断言——配对里不得出现 assumed_liquidation（KB-ENG-65 形态㈡：
        只做 A/B 等价对照钉不住「两路共用的判据本身失效」）；
        ② 对照断言——与默认路径的成交统计**逐项相等**（两路成交列表不同，统计却应一致）。

        ⚠️ 夹具必须让合成卖单**有配对可污染**（注入验证 I2 暴露的守卫盲区）：
        `_up_bars(120) + 满仓` 的末段真实成交恰是「卖」，合成卖单落在 `open_cost==0`
        之后 ⇒ `pair_trades_ts` 不生成配对 ⇒「统计相等」与「配对无 synthetic」
        **两条断言双双空转**，摘掉过滤仍全绿。改用「**末日才买入**」夹具：
        合成卖单紧跟一笔未配对买入 ⇒ 过滤一被摘掉就立刻多出一笔配对（且带 synthetic 缘由）。
        """
        bars = _up_bars(120)
        n = len(bars)
        buy_at_last = lambda v: 1.0 if len(v) == n - 1 else 0.0  # noqa: E731
        base = run_backtest(bars, buy_at_last)
        liq = run_backtest(bars, buy_at_last, liquidate_at_end=True)

        # 夹具前提钉死（前提不成立时下面的断言会空转，必须显式失败）
        assert all(t.side == "buy" for t in base.trades if t.ok), "夹具前提：默认路径零卖出"
        assert base.extra_metrics["n_trades"] == 0, "夹具前提：默认路径无已配对成交"
        assert base.trades[-1].side == "buy", "夹具前提：末笔真实成交必须是买入（否则合成卖单无对可配）"

        last = liq.trades[-1]
        assert last.reason == "assumed_liquidation" and last.synthetic is True
        assert last.ok is True, "假设清算确实改了现金，故 ok=True；靠 synthetic 与统计隔离"
        assert liq.pairs == [], "假设清算不得进配对（否则会污染胜率/盈亏比）"

        for key in ("n_trades", "win_rate", "profit_loss_ratio", "profit_factor", "sqn"):
            assert liq.extra_metrics[key] == base.extra_metrics[key], \
                f"{key} 不应被假设清算影响"
        assert liq.open_position is not None
        assert liq.open_position.liquidated_by == "assumed_liquidation"
        assert liq.open_position.unrealized_pnl == base.open_position.unrealized_pnl
        assert any("假设清算" in n and "不计入成交" in n for n in liq.notes)

    def test_open_position_reconciles_across_multiple_buys(self):
        """成本为**加权平均**（含买入费用）：多次建仓后未实现盈亏口径自洽。

        夹具刻意用**平价**序列 + 末根才加仓：引擎按「总权益 × 目标比例」定股数，
        上行序列或临近满仓时整手截断会让目标股数比持仓少 100 股 ⇒ **产生小额卖出**，
        成本被摊掉，本用例前提就不成立。增量放在**末根开盘**可确保全程只买不卖。
        """
        bars = _mk_bars([10.0] * 160)
        last_k = len(bars)

        def step_in(view: BarView) -> float:
            k = len(view)
            if k <= 61:
                return 0.0
            return 1.0 if k >= last_k - 1 else 0.5

        report = run_backtest(bars, step_in)
        buys = [t for t in report.trades if t.ok and t.side == "buy"]
        assert len(buys) >= 2, "夹具前提：至少两次买入（否则测不到加权平均）"
        assert not [t for t in report.trades if t.ok and t.side == "sell"], \
            "夹具前提：全程只买不卖（有卖出则成本会被摊掉，本断言失效）"

        op = report.open_position
        assert op is not None
        expected_cost = sum(t.price * t.qty + t.fee for t in buys)
        assert math.isclose(op.cost, expected_cost, rel_tol=1e-9), \
            "未平仓成本应等于全部买入金额含费之和"
        assert math.isclose(op.mark_value, sum(t.qty for t in buys) * bars[-1]["close"], rel_tol=1e-9)
        assert op.entry_ts == buys[0].fill_ts, "建仓时间应取**首笔**买入的成交 bar"
        assert op.entry_price == buys[0].price

    def test_partial_sell_keeps_share_cost_basis(self):
        """部分卖出后**每股成本不变**：成本按卖出比例摊掉，不是整笔留着。

        注入验证（I4）会摘掉摊薄那一行：届时成本仍是全部买入额、而股数已减半
        ⇒ 每股成本翻倍 ⇒ 本断言精确变红。这是「守卫覆盖面」意义上的钉子——
        否则「卖出不摊成本」这个语义没有任何用例约束。
        """
        bars = _mk_bars([10.0] * 160)

        def step_down(view: BarView) -> float:
            k = len(view)
            if k <= 61:
                return 0.0
            return 0.25 if k >= 140 else 0.5

        report = run_backtest(bars, step_down)
        buys = [t for t in report.trades if t.ok and t.side == "buy"]
        sells = [t for t in report.trades if t.ok and t.side == "sell"]
        assert len(buys) == 1 and len(sells) == 1, \
            f"夹具前提：恰一次买入 + 一次部分卖出，实为 {len(buys)} 买 / {len(sells)} 卖"
        op = report.open_position
        assert op is not None and op.qty > 0, "夹具前提：卖出后仍有持仓"
        assert op.qty < buys[0].qty, "夹具前提：必须是**部分**卖出（否则摊薄与清零不可区分）"

        # 单买 + 单卖 ⇒ 成本按剩余股数比例摊薄：cost / qty 与「买入金额含费 / 买入股数」一致
        per_share_in = (buys[0].price * buys[0].qty + buys[0].fee) / buys[0].qty
        assert math.isclose(op.cost / op.qty, per_share_in, rel_tol=1e-9), (
            f"部分卖出后每股成本应保持 {per_share_in}（与卖出比例无关），"
            f"实为 {op.cost / op.qty}——若成本未被摊薄会翻倍"
        )
        assert math.isclose(op.qty, buys[0].qty - sells[0].qty)


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
        # R14：不再有 delisted 强平；用 synthetic 排除「假设清算」（本用例默认路径无此产物）
        sells = [t for t in report.trades if t.ok and t.side == "sell" and not t.synthetic]
        assert buys and sells
        assert report.open_position is None, "夹具前提：末段已清仓，无未平仓持仓"
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
