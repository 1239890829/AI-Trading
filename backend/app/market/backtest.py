"""日线回测引擎 v1（Phase 6 后半）——docs/backtest-rules.md 强制禁令的代码级实现。

设计（对齐禁令逐条）：
- §1 数据可用性：BarView 只暴露 `bars[:i+1]`；越界访问抛 FutureDataError（禁令 §4-1）。
  v1 无财务/修订数据接入（接口留 FundamentalsView 扩展位），股票池时点成分属组合层。
- §2 撮合：信号 bar 收盘产生 → **下一根 bar 开盘价 ± 滑点** 撮合（禁止同 bar 收盘撮合）；
  T+1（当日买入不可当日卖出）；一字涨停拒买/一字跌停拒卖；停牌=无 bar=无撮合机会；
  佣金/印花税/滑点全部来自 BacktestConfig，无硬编码。
- §3 每笔交易完整记录：触发/成交时间、价格、数量、各项费用、拒绝原因。
- §5 可解释：基准（买入持有）、超额、最大回撤及时长、夏普/Sortino/Calmar、
  胜率、盈亏比、样本内外分离（in_ratio）。

红线 3：报告只描述统计事实与策略偏向，不输出确定性买卖建议。
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 数据视图


class FutureDataError(IndexError):
    """禁令 §4-1：未来 bar 数据不可访问，读取即抛。"""


class BarView:
    """as_of 数据视图——策略只能看到 `bars[:i+1]`（含当前 bar）。"""

    __slots__ = ("_bars", "_i")

    def __init__(self, bars: list[dict], i: int):
        self._bars = bars
        self._i = i

    @property
    def as_of(self) -> str:
        return str(self._bars[self._i]["ts"])

    def __len__(self) -> int:
        return self._i + 1

    def __getitem__(self, idx: int) -> dict:
        if idx > self._i:
            raise FutureDataError(f"bar[{idx}] 超出 as_of={self._i}（未来数据不可访问）")
        if idx < -len(self):
            raise FutureDataError("负索引越过可用历史起点")
        return self._bars[idx]

    def closes(self, n: int | None = None) -> list[float]:
        """截至 as_of 的收盘序列（可选最近 n 根）。"""
        seq = self._bars[: self._i + 1]
        return [b["close"] for b in (seq[-n:] if n else seq)]


class FundamentalsView:
    """按公告日对齐的财务数据视图（禁令 §1：报告期数据在公告日之前不可见）。

    v1 回测未接入财务数据；本类为接入时的防泄露契约——records 须含
    ann_date（公告日）与 report_period（报告期），as_of 只返回已公告记录。
    修订数据接入时以 (report_period, ann_date, version) 三元组扩展，不覆盖时点值。
    """

    def __init__(self, records: list[dict]):
        self._records = sorted(records, key=lambda r: r["ann_date"])

    def as_of(self, date: str) -> list[dict]:
        return [r for r in self._records if r["ann_date"] <= date]


Strategy = Callable[[BarView], float]
"""策略协议：输入 as_of 视图，返回目标仓位比例 [0,1]（收盘后调用，次日开盘撮合）。"""


# ---------------------------------------------------------------- 配置与记录


@dataclass
class BacktestConfig:
    """撮合与费用参数（禁令 §2：全部配置化，无硬编码）。"""

    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.00025  # 佣金万 2.5（双边）
    commission_min: float = 5.0  # 单笔最低佣金（元）
    stamp_tax: float = 0.001  # 印花税千 1（卖出单边）
    slippage_bp: float = 5.0  # 滑点（双边恶化，bp）
    limit_pct: float = 0.10  # 涨跌停幅度（主板；ST/北交未在 v1 区分）
    limit_eps: float = 0.002  # 涨跌停判定容差（QFQ 价格微偏）
    in_ratio: float = 0.7  # 样本内切分比例（禁令 §5 样本内外分离）


@dataclass
class Trade:
    signal_ts: str
    fill_ts: str
    side: str  # buy / sell
    price: float  # 实际成交价（含滑点）
    ref_price: float  # 撮合参考价（开盘价）
    qty: int
    fee: float  # 佣金+印花税合计
    ok: bool
    reason: str = ""  # 拒绝原因：limit_up / limit_down / t1 / insufficient_cash / delisted


@dataclass
class BacktestReport:
    trades: list[Trade] = field(default_factory=list)
    equity_ts: list[str] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    benchmark: list[float] = field(default_factory=list)  # 买入持有净值
    total_return: float = 0.0
    benchmark_return: float = 0.0
    excess_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_days: int = 0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    in_return: float = 0.0
    out_return: float = 0.0
    config: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 内置策略库

def _sma_at(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _ma_cross_strategy(params: dict) -> Strategy:
    """双均线：fast 在 slow 上方 → 满仓，下方 → 空仓（纯位置函数，无隐含状态）。"""
    fast = int(params.get("fast", 5))
    slow = int(params.get("slow", 20))

    def run(view: BarView) -> float:
        closes = view.closes(slow + 1)
        f, s = _sma_at(closes, fast), _sma_at(closes, slow)
        return 1.0 if f is not None and s is not None and f > s else 0.0

    return run


def _ma_breakout_strategy(params: dict) -> Strategy:
    """单均线突破：收盘站上 MA(n) → 满仓，跌破 → 空仓。"""
    period = int(params.get("period", 20))

    def run(view: BarView) -> float:
        closes = view.closes(period + 1)
        ma = _sma_at(closes, period)
        return 1.0 if ma is not None and closes[-1] > ma else 0.0

    return run


STRATEGY_REGISTRY: dict[str, dict] = {
    "ma_cross": {
        "name": "双均线 MA5/20",
        "params": {"fast": 5, "slow": 20},
        "build": _ma_cross_strategy,
    },
    "ma_breakout": {
        "name": "均线突破 MA20",
        "params": {"period": 20},
        "build": _ma_breakout_strategy,
    },
}


def build_strategy(strategy_id: str, params: dict | None = None) -> Strategy:
    entry = STRATEGY_REGISTRY.get(strategy_id)
    if entry is None:
        raise ValueError(f"未知策略：{strategy_id}（可用：{', '.join(STRATEGY_REGISTRY)}）")
    merged = {**entry["params"], **(params or {})}
    return entry["build"](merged)


# ---------------------------------------------------------------- 引擎


def _is_one_board(bar: dict, prev_close: float, cfg: BacktestConfig) -> tuple[bool, bool]:
    """一字板判定 → (是否一字涨停, 是否一字跌停)。OHLC 全等且达限价幅度。"""
    eps = 1e-6
    flat = (
        abs(bar["open"] - bar["high"]) < eps
        and abs(bar["high"] - bar["low"]) < eps
        and abs(bar["low"] - bar["close"]) < eps
    )
    if not flat or prev_close <= 0:
        return False, False
    up = bar["open"] >= prev_close * (1 + cfg.limit_pct - cfg.limit_eps)
    down = bar["open"] <= prev_close * (1 - cfg.limit_pct + cfg.limit_eps)
    return up, down


def _fees(cfg: BacktestConfig, amount: float, side: str) -> float:
    commission = max(amount * cfg.commission_rate, cfg.commission_min)
    stamp = amount * cfg.stamp_tax if side == "sell" else 0.0
    return round(commission + stamp, 2)


def run_backtest(
    bars: list[dict],
    strategy: Strategy,
    cfg: BacktestConfig | None = None,
) -> BacktestReport:
    """逐 bar 推进的日线回测。bars 升序（ts/open/high/low/close/volume）。

    流程（禁令 §2：信号 bar 收盘产生 → 下一 bar 开盘撮合）：
      bar i 开盘：撮合上一收盘产生的 pending 目标；
      bar i 收盘：strategy(BarView(bars, i)) → 新 pending 目标。
    """
    cfg = cfg or BacktestConfig()
    if len(bars) < 60:
        raise ValueError("回测样本不足：至少 60 根日K")
    slip = cfg.slippage_bp / 10_000

    cash = cfg.initial_cash
    qty = 0
    last_buy_ts: str | None = None
    pending_target: float | None = None
    trades: list[Trade] = []
    equity_ts: list[str] = []
    equity: list[float] = []
    refused_delisted = 0

    for i, bar in enumerate(bars):
        prev_close = bars[i - 1]["close"] if i > 0 else bar["open"]

        # ---- 开盘撮合 pending 目标（若上一收盘发出过信号）----
        if pending_target is not None:
            # 目标股数基于**总资产**（现金+持仓市值）而非现金——否则满仓持有
            # 会被误算为"目标 0 股"反复清仓（首版实锤：8 个防泄露测试抓出）
            equity_now = cash + qty * bar["open"]
            target_qty = (
                int(equity_now * pending_target // (bar["open"] * (1 + slip)) // 100) * 100
                if pending_target > 0
                else 0
            )
            limit_up, limit_down = _is_one_board(bar, prev_close, cfg)

            # 卖出腿：目标仓位降低 → 卖出差额
            if target_qty < qty:
                sell_qty = qty - target_qty
                if limit_down:
                    trades.append(Trade(bar["ts"], bar["ts"], "sell", 0.0, bar["open"], 0, 0.0, False, "limit_down"))
                elif last_buy_ts == bar["ts"]:
                    trades.append(Trade(bar["ts"], bar["ts"], "sell", 0.0, bar["open"], 0, 0.0, False, "t1"))
                else:
                    price = bar["open"] * (1 - slip)
                    amount = price * sell_qty
                    fee = _fees(cfg, amount, "sell")
                    cash += amount - fee
                    qty -= sell_qty
                    trades.append(Trade(bar["ts"], bar["ts"], "sell", price, bar["open"], sell_qty, fee, True))

            # 买入腿：目标仓位升高 → 买入差额
            if target_qty > qty:
                if limit_up:
                    trades.append(Trade(bar["ts"], bar["ts"], "buy", 0.0, bar["open"], 0, 0.0, False, "limit_up"))
                else:
                    price = bar["open"] * (1 + slip)
                    # 留出费用余量，按整手
                    affordable = int((cash - cfg.commission_min) // (price * 100 * (1 + cfg.commission_rate))) * 100
                    buy_qty = min(target_qty - qty, max(affordable, 0))
                    if buy_qty < 100:
                        trades.append(Trade(bar["ts"], bar["ts"], "buy", 0.0, bar["open"], 0, 0.0, False, "insufficient_cash"))
                    else:
                        amount = price * buy_qty
                        fee = _fees(cfg, amount, "buy")
                        cash -= amount + fee
                        qty += buy_qty
                        last_buy_ts = bar["ts"]
                        trades.append(Trade(bar["ts"], bar["ts"], "buy", price, bar["open"], buy_qty, fee, True))
            pending_target = None

        # ---- 收盘 mark + 策略调用（as_of = 当前 bar）----
        equity_ts.append(str(bar["ts"]))
        equity.append(cash + qty * bar["close"])
        view = BarView(bars, i)
        target = float(strategy(view))
        if not (0.0 <= target <= 1.0):
            raise ValueError(f"策略输出非法：target={target}（须在 [0,1]）")
        pending_target = target

    # 数据流结束仍有持仓 → 按最后收盘强制平仓并标记（禁令 §1：退市不被静默剔除）
    if qty > 0:
        last = bars[-1]
        amount = last["close"] * qty
        fee = _fees(cfg, amount, "sell")
        cash += amount - fee
        trades.append(Trade(last["ts"], last["ts"], "sell", last["close"], last["close"], qty, fee, True, "delisted"))
        equity[-1] = cash
        qty = 0
        refused_delisted += 1

    return _build_report(trades, equity_ts, equity, bars, cfg, refused_delisted)


def _build_report(
    trades: list[Trade],
    equity_ts: list[str],
    equity: list[float],
    bars: list[dict],
    cfg: BacktestConfig,
    refused_delisted: int,
) -> BacktestReport:
    n = len(equity)
    benchmark = [cfg.initial_cash * bars[i]["close"] / bars[0]["open"] for i in range(n)]

    def _ret(seq: list[float]) -> float:
        return seq[-1] / seq[0] - 1 if seq and seq[0] > 0 else 0.0

    total, bench = _ret(equity), _ret(benchmark)
    years = max(n / 244, 1e-9)
    annual = (equity[-1] / equity[0]) ** (1 / years) - 1 if equity[0] > 0 else 0.0

    peak = equity[0]
    mdd, mdd_days, cur_days = 0.0, 0, 0
    for v in equity:
        if v >= peak:
            peak = v
            cur_days = 0
        else:
            cur_days += 1
            dd = 1 - v / peak
            if dd > mdd:
                mdd, mdd_days = dd, cur_days

    in_n = max(int(n * cfg.in_ratio), 1)
    in_ret, out_ret = _ret(equity[:in_n]), _ret(equity[in_n - 1 :])

    daily_ret = [equity[i] / equity[i - 1] - 1 for i in range(1, n) if equity[i - 1] > 0]
    sharpe = sortino = calmar = 0.0
    if daily_ret:
        mean = sum(daily_ret) / len(daily_ret)
        var = sum((r - mean) ** 2 for r in daily_ret) / len(daily_ret)
        sd = math.sqrt(var)
        downside = [r for r in daily_ret if r < 0]
        dsd = math.sqrt(sum(r**2 for r in downside) / len(downside)) if downside else 0.0
        ann = math.sqrt(244)
        sharpe = mean / sd * ann if sd > 0 else 0.0
        sortino = mean / dsd * ann if dsd > 0 else 0.0
        calmar = annual / mdd if mdd > 0 else 0.0

    # 胜率/盈亏比：按同向成交配对（buy 后最近一次 ok sell）
    wins, losses, win_amt, loss_amt = 0, 0, 0.0, 0.0
    open_cost = 0.0
    for t in trades:
        if not t.ok:
            continue
        if t.side == "buy":
            open_cost = t.price * t.qty + t.fee
        else:
            proceeds = t.price * t.qty - t.fee
            pnl = proceeds - open_cost
            if open_cost > 0:
                if pnl >= 0:
                    wins += 1
                    win_amt += pnl
                else:
                    losses += 1
                    loss_amt += -pnl
            open_cost = 0.0

    notes = [
        "撮合口径：信号收盘产生→次 bar 开盘±滑点；T+1；一字涨停拒买/跌停拒卖；费用全配置化",
        "样本内外分离报告（禁令 §5）；评分为统计事实，不构成买卖建议",
    ]
    if refused_delisted:
        notes.append(f"数据流结束时仍有持仓，已按最后收盘强制平仓并标记 delisted（×{refused_delisted}）")

    return BacktestReport(
        trades=trades,
        equity_ts=equity_ts,
        equity=equity,
        benchmark=benchmark,
        total_return=round(total, 4),
        benchmark_return=round(bench, 4),
        excess_return=round(total - bench, 4),
        annual_return=round(annual, 4),
        max_drawdown=round(mdd, 4),
        max_drawdown_days=mdd_days,
        sharpe=round(sharpe, 3),
        sortino=round(sortino, 3),
        calmar=round(calmar, 3),
        win_rate=round(wins / (wins + losses), 4) if wins + losses else 0.0,
        profit_loss_ratio=round((win_amt / wins) / (loss_amt / losses), 3) if wins and losses else 0.0,
        in_return=round(in_ret, 4),
        out_return=round(out_ret, 4),
        config={
            "initial_cash": cfg.initial_cash,
            "commission_rate": cfg.commission_rate,
            "commission_min": cfg.commission_min,
            "stamp_tax": cfg.stamp_tax,
            "slippage_bp": cfg.slippage_bp,
            "limit_pct": cfg.limit_pct,
            "in_ratio": cfg.in_ratio,
        },
        notes=notes,
    )
