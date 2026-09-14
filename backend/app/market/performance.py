"""绩效指标模块（28 项）——freqtrade 调研采纳项（第 2/6 组）的统一实现。

单一口径来源：日线引擎（app/market/backtest.py）、Walk-Forward（walkforward.py）
共用本模块，禁止各处自算出现口径漂移。全部纯 Python（math），无新依赖。

指标清单（28 项）：
  曲线类（14）：total_return / annual_return / volatility_annual /
    downside_deviation_annual / max_drawdown / max_drawdown_days / sharpe /
    sortino / calmar / ulcer_index / ulcer_pi / recovery_factor /
    var_95_daily / cvar_95_daily
  交易类（11）：n_trades / win_rate / profit_factor / expectancy_pct /
    expectancy_ratio / avg_win_pct / avg_loss_pct / best_trade_pct /
    worst_trade_pct / max_win_streak / max_loss_streak
  其他（3）：avg_holding_bars / sqn / p_value

口径说明（与既有引擎保持一致，改动须过 golden test）：
- 年化基数 244 交易日；波动/下行偏差为总体口径（ddof=0）；
- sharpe/sortino 无风险利率取 0；sortino 的下行偏差沿用引擎「负收益分量 RMS」口径；
- ulcer_index 用百分比回撤的 RMS（标准定义）；ulcer_pi = 年化收益% / UI（rf=0）；
- var_95_daily 为日收益经验分位（升序 5% 位，负值=损失）；cvar_95_daily 为
  最差 5% 样本均值；
- profit_factor = 总盈利/总亏损（总额口径，与 avg 口径的 profit_loss_ratio 不同），
  分母为 0 且有盈利时返回 999.0（JSON 安全上限，非 inf）；
- pnl>=0 计入胜（与引擎既有口径一致）；
- sqn = √N × mean(R)/sd(R)（每笔收益率口径，N<2 或 sd=0 → 0.0）；
- p_value 为单样本 t 检验 H0: mean(R)=0 的双边 p（正态近似 erfc，小样本偏乐观，
  已在 notes 提示）；t 无定义（sd=0）时：mean>0 → 0.0（显著为正），否则 1.0；
- avg_holding_bars 用 bar 数（交易日），不解析 ts 字符串。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

PERIODS_PER_YEAR = 244
# profit_factor 的 JSON 安全上限（分母为 0 且有盈利时使用；999 与 inf 区分得开）。
PROFIT_FACTOR_CAP = 999.0


@dataclass
class TradePair:
    """一次同向配对（buy 后最近一次 ok sell）。"""

    entry_i: int  # buy bar 序号（引擎 bars 内 index）
    exit_i: int  # sell bar 序号
    entry_ts: str
    exit_ts: str
    cost: float  # 买入金额含费
    proceeds: float  # 卖出金额扣费
    pnl: float  # proceeds - cost
    pnl_pct: float  # pnl / cost
    reason: str = ""  # 卖出腿 reason（delisted 显式清算入对；synthetic 假设清算不入对，R14）


def pair_trades_ts(trades: list, ts_index: dict[str, int]) -> list[TradePair]:
    """引擎 Trade 列表 → 同向配对（buy 后最近一次 ok sell）。

    与引擎原内联配对逻辑一致：buy 记 open_cost（价×量+费），下一个 ok sell
    生成配对；未配对的 sell 忽略。entry_i/exit_i 由 ts 在 ts_index
    （ts→bar 序号，由引擎的 equity_ts 建）中的位置回推。

    ⚠️ 调用方负责**先剔除 `synthetic=True` 的假设清算产物**（R14）：本函数不看该字段，
    传进来的都会被配对。它是「成交统计的唯一入口」，过滤必须在入口之前完成。
    """
    pairs: list[TradePair] = []
    open_cost = 0.0
    open_i = -1
    open_ts = ""
    for t in trades:
        if not t.ok:
            continue
        if t.side == "buy":
            open_cost = t.price * t.qty + t.fee
            open_i = ts_index.get(t.fill_ts, -1)
            open_ts = t.fill_ts
        else:
            if open_cost > 0:
                proceeds = t.price * t.qty - t.fee
                pairs.append(
                    TradePair(
                        entry_i=open_i,
                        exit_i=ts_index.get(t.fill_ts, -1),
                        entry_ts=open_ts,
                        exit_ts=t.fill_ts,
                        cost=open_cost,
                        proceeds=proceeds,
                        pnl=proceeds - open_cost,
                        pnl_pct=proceeds / open_cost - 1.0 if open_cost > 0 else 0.0,
                        reason=t.reason,
                    )
                )
            open_cost = 0.0
            open_i = -1
    return pairs


def _max_drawdown_block(equity: list[float]) -> tuple[float, int]:
    """返回 (最大回撤, 回撤持续 bar 数)——与引擎原口径逐行一致。"""
    peak = equity[0] if equity else 0.0
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
    return mdd, mdd_days


def _pctl(sorted_vals: list[float], q: float) -> float:
    """经验分位（升序输入，线性索引取下界——保守偏损失方向）。"""
    if not sorted_vals:
        return 0.0
    idx = min(int(q * len(sorted_vals)), len(sorted_vals) - 1)
    return sorted_vals[idx]


def compute_performance(
    equity: list[float],
    pairs: list[TradePair] | None = None,
    *,
    periods_per_year: int = PERIODS_PER_YEAR,
) -> dict:
    """由净值曲线与交易配对计算全部指标（返回 round 后的 dict）。

    equity 须含起点（bar0 的期初净值）；n<2 时返回全 0 兜底（调用方保证
    不会拿去展示为"正常"，由调用方自行判断样本量）。
    """
    pairs = pairs or []
    n = len(equity)
    out: dict[str, float | int] = {
        "total_return": 0.0, "annual_return": 0.0,
        "volatility_annual": 0.0, "downside_deviation_annual": 0.0,
        "max_drawdown": 0.0, "max_drawdown_days": 0,
        "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0,
        "ulcer_index": 0.0, "ulcer_pi": 0.0, "recovery_factor": 0.0,
        "var_95_daily": 0.0, "cvar_95_daily": 0.0,
        "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "expectancy_pct": 0.0, "expectancy_ratio": 0.0,
        "profit_loss_ratio": 0.0,
        "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
        "best_trade_pct": 0.0, "worst_trade_pct": 0.0,
        "max_win_streak": 0, "max_loss_streak": 0,
        "avg_holding_bars": 0.0, "sqn": 0.0, "p_value": 1.0,
    }
    if n < 2 or equity[0] <= 0:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in out.items()}

    ann = math.sqrt(periods_per_year)

    # ---- 曲线类 ----
    total = equity[-1] / equity[0] - 1.0
    years = max(n / periods_per_year, 1e-9)
    annual = (equity[-1] / equity[0]) ** (1 / years) - 1.0 if equity[0] > 0 else 0.0

    daily = [equity[i] / equity[i - 1] - 1.0 for i in range(1, n) if equity[i - 1] > 0]
    mean = sum(daily) / len(daily) if daily else 0.0
    var = sum((r - mean) ** 2 for r in daily) / len(daily) if daily else 0.0
    sd = math.sqrt(var)
    downside = [r for r in daily if r < 0]
    dsd = math.sqrt(sum(r**2 for r in downside) / len(downside)) if downside else 0.0

    mdd, mdd_days = _max_drawdown_block(equity)

    # ulcer：百分比回撤的 RMS（peak 用运行最大值）
    peak = equity[0]
    dd2 = []
    for v in equity:
        if v >= peak:
            peak = v
        dd_pct = (1 - v / peak) * 100.0 if peak > 0 else 0.0
        dd2.append(dd_pct * dd_pct)
    ulcer = math.sqrt(sum(dd2) / len(dd2)) if dd2 else 0.0

    sorted_daily = sorted(daily)
    tail_n = max(1, math.ceil(0.05 * len(sorted_daily))) if sorted_daily else 0
    var95 = _pctl(sorted_daily, 0.05) if sorted_daily else 0.0
    cvar95 = sum(sorted_daily[:tail_n]) / tail_n if sorted_daily else 0.0

    out["total_return"] = total
    out["annual_return"] = annual
    out["volatility_annual"] = sd * ann
    out["downside_deviation_annual"] = dsd * ann
    out["max_drawdown"] = mdd
    out["max_drawdown_days"] = mdd_days
    out["sharpe"] = mean / sd * ann if sd > 0 else 0.0
    out["sortino"] = mean / dsd * ann if dsd > 0 else 0.0
    out["calmar"] = annual / mdd if mdd > 0 else 0.0
    out["ulcer_index"] = ulcer
    out["ulcer_pi"] = (annual * 100.0) / ulcer if ulcer > 0 else 0.0
    out["recovery_factor"] = (total * 100.0) / (mdd * 100.0) if mdd > 0 else 0.0
    out["var_95_daily"] = var95
    out["cvar_95_daily"] = cvar95

    # ---- 交易类 ----
    if pairs:
        pnls = [p.pnl_pct for p in pairs]
        wins = [x for x in pnls if x >= 0]
        losses = [x for x in pnls if x < 0]
        win_sum, loss_sum = sum(wins), sum(losses)
        out["n_trades"] = len(pairs)
        out["win_rate"] = len(wins) / len(pairs)
        out["profit_factor"] = (
            (win_sum / -loss_sum) if loss_sum < 0
            else (PROFIT_FACTOR_CAP if win_sum > 0 else 0.0)
        )
        out["expectancy_pct"] = (sum(pnls) / len(pnls)) * 100.0
        out["expectancy_ratio"] = (
            (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
            if wins and losses else 0.0
        )
        out["avg_win_pct"] = (sum(wins) / len(wins)) * 100.0 if wins else 0.0
        out["avg_loss_pct"] = (sum(losses) / len(losses)) * 100.0 if losses else 0.0
        out["best_trade_pct"] = max(pnls) * 100.0
        out["worst_trade_pct"] = min(pnls) * 100.0

        # 盈亏比（金额口径，引擎 profit_loss_ratio 原语义：均笔盈利额/均笔亏损额）
        win_amts = [p.pnl for p in pairs if p.pnl >= 0]
        loss_amts = [p.pnl for p in pairs if p.pnl < 0]
        out["profit_loss_ratio"] = (
            (sum(win_amts) / len(win_amts)) / abs(sum(loss_amts) / len(loss_amts))
            if win_amts and loss_amts else 0.0
        )

        cur_win = cur_loss = max_win = max_loss = 0
        for x in pnls:
            if x >= 0:
                cur_win += 1
                cur_loss = 0
                max_win = max(max_win, cur_win)
            else:
                cur_loss += 1
                cur_win = 0
                max_loss = max(max_loss, cur_loss)
        out["max_win_streak"] = max_win
        out["max_loss_streak"] = max_loss

        holding = [p.exit_i - p.entry_i for p in pairs if p.exit_i > p.entry_i >= 0]
        out["avg_holding_bars"] = sum(holding) / len(holding) if holding else 0.0

        # ---- 统计检验 ----
        if len(pnls) >= 2:
            m_r = sum(pnls) / len(pnls)
            # 全等快速路径：浮点方差对"值全相同"会残留 ~1e-36 噪声，
            # 把 sd_r 当非零会把 SQN/p_value 抬成天文数字（单测实锤）
            sd_r = (
                0.0 if max(pnls) == min(pnls)
                else math.sqrt(sum((x - m_r) ** 2 for x in pnls) / len(pnls))
            )
            if sd_r > 0:
                t_stat = m_r / (sd_r / math.sqrt(len(pnls)))
                # 正态近似的双边 p（小样本偏乐观，notes 提示）
                out["p_value"] = math.erfc(abs(t_stat) / math.sqrt(2))
                out["sqn"] = math.sqrt(len(pnls)) * m_r / sd_r
            else:
                out["p_value"] = 0.0 if m_r > 0 else 1.0
                out["sqn"] = 0.0

    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in out.items()}


# ---------------------------------------------------------------- 目标函数


def objective_value(metrics: dict, name: str) -> float:
    """Walk-Forward / 参数选择的优化目标取值。

    freqtrade HyperOptLoss 的对应物：max_drawdown 目标即
    MaxDrawDownHyperOptLoss（最小化最大回撤，而非最大化收益）。
    未知目标名抛 ValueError（不猜）。
    """
    if name not in _OBJECTIVES:
        raise ValueError(f"未知优化目标：{name}（可用：{', '.join(_OBJECTIVES)}）")
    return float(metrics.get(name, 0.0))


_OBJECTIVES = {
    # name → (方向, 说明)：方向决定"选最大"还是"选最小"
    "annual_return": ("max", "年化收益最大化"),
    "sharpe": ("max", "夏普最大化"),
    "calmar": ("max", "卡玛最大化"),
    "max_drawdown": ("min", "最大回撤最小化（MaxDrawDownHyperOptLoss 口径）"),
    "profit_factor": ("max", "盈亏总额比最大化"),
}


def objective_direction(name: str) -> str:
    if name not in _OBJECTIVES:
        raise ValueError(f"未知优化目标：{name}（可用：{', '.join(_OBJECTIVES)}）")
    return _OBJECTIVES[name][0]
