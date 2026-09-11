"""三倍量战法：信号识别与买卖计划（用户 2026-09-10 提出，2026-09-10 落地为纯函数）。

## 它做什么

识别**标志性三倍量K线**（当日成交量 ≥ 前 N 日均量 × 3），并据此给出两类买点计划：

- **左侧（低吸）**：价格回踩至三倍量K线最低价附近，且**当日收盘未跌破**该最低价 → 尾盘低吸；
  **止损 = 三倍量K线最低价**（收盘口径）
- **右侧（突破）**：①当日突破三倍量K线**最高价**（上影线端点）→ 当日介入；
  ②突破后次日回踩 → 低吸；**止损 = 突破当天K线最低价**（收盘口径）

## ⚠️ 未验证声明（项目纪律，勿删）

本模块**只做信号识别，不宣称任何有效性**。有效性结论只能来自
`app/picks/backtest.py` 在本市场真实历史样本上的统计。
[[KB-DEC-018]]：方法论 / 经验 / 思路 ≠ 已验证结论——**「社区很流行」不是证据**。

## 两个必须先讲清的口径分歧（本模块的选择与理由）

1. **「三倍量」的分母**：本模块取**前 N 日均量（不含当日）**。
   社区另有一派用「昨日量」（`V/REF(V,1) ≥ 3`）——**不采用**：昨日可能是极缩量日，
   用它作分母会把「温和放量」误判成「三倍量」，噪声显著更大。
2. **「近期均量」的窗口 N**：默认 **5 日**（社区主流，贴近"近期"语义）。
   20 日更严格、信号更稀；N 是**可调参数**，最终取值应由回测决定，**不得拍脑袋固定**
   （见 `VOL_LOOKBACK` 注释）。

## 三态纪律

量能或价格缺失 → 该判定项为 `unknown`（判不出来），**绝不当成满足或不满足**；
K 线不足 N+1 根 → 不给信号（而非按不足的样本硬算均量）。
"""
from __future__ import annotations

#: 三倍量倍数（核心基准）。社区常见 2/3/4 倍，本模块取 3 倍（用户口径）。
VOL_MULTIPLE = 3.0
#: 「近期均量」窗口（不含当日）。5 = 社区主流；20 更严格但信号更稀。
#: ⚠️ 取值应由回测决定——`scripts/backtest_picks.py` 同族通道可网格扫描。
VOL_LOOKBACK = 5
#: 左侧买点的「附近」容差（%）：回踩到最低价 +X% 以内即视为触及
TOUCH_TOLERANCE_PCT = 1.0
#: 右侧突破的「有效突破」确认幅度（%）：等于最高价也算突破，留 0 即严格相等
BREAKOUT_TOLERANCE_PCT = 0.0


def avg_volume(vols_prev: list[float] | None, lookback: int = VOL_LOOKBACK) -> float | None:
    """前 `lookback` 日均量（不含当日）。

    样本不足或存在非正数（停牌/脏数据）→ None（三态：判不出来，**不按剩余样本硬算**，
    否则停牌复牌后的票会被算出一个虚低的基线，凭空"制造"三倍量）。
    """
    if not vols_prev or len(vols_prev) < lookback:
        return None
    window = vols_prev[-lookback:]
    if any(v is None or v <= 0 for v in window):
        return None
    return sum(window) / lookback


def is_triple_volume(
    *,
    vol_today: float | None,
    vols_prev: list[float] | None,
    multiple: float = VOL_MULTIPLE,
    lookback: int = VOL_LOOKBACK,
) -> dict:
    """是否为三倍量K线。返回 {is_triple, ratio, baseline, detail}（判不出时 is_triple=None）。"""
    base = avg_volume(vols_prev, lookback)
    if vol_today is None or base is None or vol_today <= 0:
        return {"is_triple": None, "ratio": None, "baseline": base,
                "detail": f"量能数据不足（今日量或前 {lookback} 日均量缺失）"}
    ratio = round(vol_today / base, 2)
    hit = ratio >= multiple
    return {"is_triple": hit, "ratio": ratio, "baseline": round(base, 2),
            "detail": f"今日量 / 前 {lookback} 日均量 = {ratio}（门槛 {multiple}）"}


def plan_left(
    *,
    price: float | None,
    triple_low: float | None,
    triple_close: float | None,
    holding: bool = False,
) -> dict:
    """左侧（低吸）买点计划。

    规则（用户口径）：回踩至三倍量K线最低价附近 → 关注；**当日收盘未跌破**该最低价 → 尾盘低吸；
    止损 = 三倍量K线最低价（**收盘口径**：盘中短暂刺破不算破位）。

    :param holding: 是否已持有（区分"新建仓"与"持仓中复核止损"两种语义）
    """
    ok, reasons = True, []
    for name, cond in (
        (f"现价回踩至最低价附近（≤+{TOUCH_TOLERANCE_PCT}%）",
         None if (price is None or triple_low is None) else price <= triple_low * (1 + TOUCH_TOLERANCE_PCT / 100)),
        ("收盘未跌破三倍量K线最低价",
         None if (triple_close is None or triple_low is None) else triple_close >= triple_low),
    ):
        if cond is None:
            ok = False
            reasons.append(f"{name}：判不出来")
        elif not cond:
            ok = False
            reasons.append(f"{name}：不满足")
    # 买区：最低价 ~ 最低价+容差（低吸区间贴着支撑，不追）
    buy_range = None if triple_low is None else [round(triple_low, 2), round(triple_low * (1 + TOUCH_TOLERANCE_PCT / 100), 2)]
    return {
        "mode": "左侧低吸", "ready": ok,
        "buy_range": buy_range if not holding else None,
        "stop": round(triple_low, 2) if triple_low is not None else None,
        "unmet": reasons,
        "note": "买区=三倍量K线最低价 +1% 以内；止损=该最低价（**收盘价跌破才离场**，盘中刺破不算）",
    }


def plan_right(
    *,
    price: float | None,
    triple_high: float | None,
    breakout_low: float | None = None,
) -> dict:
    """右侧（突破）买点计划。

    规则（用户口径）：①当日突破三倍量K线**最高价**（上影线端点）→ 当日介入；
    ②突破后次日回踩 → 低吸。止损 = **突破当天K线最低价**（收盘口径）。

    :param breakout_low: 突破当天的K线最低价；缺省时退化为「三倍量K线最低价」并把该退化写进 note
    """
    ok, reasons = True, []
    thr = None if triple_high is None else triple_high * (1 + BREAKOUT_TOLERANCE_PCT / 100)
    for name, cond in (
        (f"现价突破三倍量K线最高价 {triple_high}",
         None if (price is None or thr is None) else price >= thr),
    ):
        if cond is None:
            ok = False
            reasons.append(f"{name}：判不出来")
        elif not cond:
            ok = False
            reasons.append(f"{name}：不满足")
    stop_src = breakout_low if breakout_low is not None else triple_high
    note = "买点=突破最高价当日介入；止损=突破当天K线最低价（收盘口径）"
    if breakout_low is None and triple_high is not None:
        note += "；⚠️ 突破当天最低价缺失 → 止损退化用三倍量K线最低价，须人工复核"
    return {
        "mode": "右侧突破", "ready": ok,
        "buy_range": None if triple_high is None else [round(triple_high, 2), round(triple_high * 1.015, 2)],
        "stop": round(stop_src * 0.99, 2) if stop_src is not None else None,
        "unmet": reasons,
        "note": note,
    }


def is_stopped_out(*, close: float | None, stop: float | None) -> bool | None:
    """止损判定——**只看收盘价**（用户口径的关键一条）。

    盘中短暂下探刺破止损线**不算破位**（可继续持有）；收盘价跌破才算。
    缺任一输入 → None（判不出来，不臆造"安全"）。
    """
    if close is None or stop is None:
        return None
    return close < stop
