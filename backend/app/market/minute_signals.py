"""做 T 分时信号引擎（docs/archive/minute-chart-plan.md 模块 4 的引擎核心）。

红线与设计约束
--------------
- **只输出偏向 + 依据 + 失效条件**（AGENTS 红线 3）：不输出确定性买卖结论。
- **as_of 严格推进**：所有 running 统计量只用 ≤ 当前 bar 的数据；信号触发时刻 =
  确认完成时刻（指标 1 的"3 分钟不创新低"确认发生在第 3 根 bar 上，
  signal_price 取该 bar 价格——不存在用未来数据"回看"发信号）。
- 后端唯一实现（口径唯一原则），前端只渲染。
- 缺失输入显式降级（degraded 列表），绝不臆造。

五个指标（权重来自方案，未经历史校准——上线前必须跑模块 4.3 回测）：
1. 均价线偏离 0.30：低吸=偏离 ≤ -1.5% 且随后 3 分钟不创新低；高抛=偏离 ≥ +2%。
2. 量价背离 0.25：创新高但量 < 5 分钟均量×0.6（顶背离）；创新低但量 ≤ 均量×0.4（底背离）。
3. 分时量能突变 0.20：单分钟量 > 已过均量×3，方向按价格涨跌（强化项，单独不触发）。
4. 开盘 30 分钟突破 0.15：放量突破高点=强势（抑制高抛）；跌破低点=偏空。
   量比条件缺昨日量时降级为"分钟量 ≥ 已过均量×1.2"并标注。
5. 换手率进度 0.10：需流通股本与 5 日均额，本轮无输入——恒降级，权重不参与归一。

组合：score = Σ(命中权重×方向) / Σ(可计算权重)，归一到 [-1, +1]；
|score| ≥ 0.5 出偏向（同方向 30 根 bar 冷却防刷屏），0.3~0.5 记观察。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.bjtime import BJ_OFFSET  # S2-8 时区收敛
WEIGHTS = {"avg_dev": 0.30, "vol_div": 0.25, "surge": 0.20, "breakout": 0.15, "turnover": 0.10}
ACTIVE_KEYS = ("avg_dev", "vol_div", "surge", "breakout")  # turnover 本轮恒降级
COOLDOWN_BARS = 30
CONFIRM_BARS = 3
OPEN_WINDOW = 30

TH_AVG_DEV_LOW = -1.5   # 低吸：偏离下限 %
TH_AVG_DEV_HIGH = 2.0   # 高抛：偏离上限 %
TH_DIV_VOL_LOW = 0.6    # 顶背离：量 < 均量×0.6
TH_DIV_VOL_CRASH = 0.4  # 底背离：量 ≤ 均量×0.4
TH_SURGE = 3.0          # 突变：量 > 均量×3
TH_BREAK_LB = 1.2       # 突破确认：量比 ≥ 1.2


class IndicatorHit(BaseModel):
    key: str
    name: str
    weight: float
    direction: int = Field(..., description="-1 低吸方向 / +1 高抛方向")
    trigger_value: float
    threshold: float
    evidence: str


class MinuteSignal(BaseModel):
    ts: str = Field(..., description="触发时刻（= 确认完成的那根 bar，UTC ISO）")
    signal_price: float
    bias: str = Field(..., description="低吸偏向 | 高抛偏向")
    score: float = Field(..., description="-1~+1，负=低吸方向")
    confidence: str = "medium"
    triggered: list[IndicatorHit]
    invalidate_condition: str
    basis: str = ""


def _dev(price: float, avg: float | None) -> float | None:
    if avg is None or avg <= 0:
        return None
    return (price - avg) / avg * 100


def compute_minute_signals(
    points: list[dict],
    *,
    yesterday_vol: float | None = None,
    daily_vol_pct: float | None = None,
    cooldown_bars: int = COOLDOWN_BARS,
) -> dict:
    """流式扫描分时序列，产出做 T 偏向信号。

    :param points: /api/minute-line 的 points（需 price/avg/volume/cum_volume）
    :param yesterday_vol: 昨日全天量（股），量比口径用；缺省时指标 4 降级
    :param daily_vol_pct: 近 5 日日波动率 %，偏离阈值自适应用；缺省用固定阈值
    :return: {"signals": [...], "observed": int, "degraded": [...], "basis": {...}}
    """
    degraded: list[str] = []
    if yesterday_vol is None:
        degraded.append("breakout: 缺昨日量，量比条件降级为 分钟量≥已过均量×1.2")
    degraded.append("turnover: 缺流通股本与 5 日均额，指标 5 未启用（权重已从归一中剔除）")
    if daily_vol_pct is None:
        degraded.append("avg_dev: 缺近 5 日波动率，偏离阈值未做自适应")

    # 自适应阈值（方案 4.1 指标 1 的公式具体化）：以 1.5% 日振幅为校准基准线性
    # 缩放，再双向 clamp——低波动蓝筹（茅台 0.77%）阈值收浅至 -0.5 防噪音，
    # 高波动妖股（4.5%+）最深 -2.5 防极端。区间端点未经回测校准，随模块 4.3 调。
    if daily_vol_pct:
        th_low = max(-2.5, min(TH_AVG_DEV_LOW * (daily_vol_pct / 1.5), -0.5))
        th_high = min(3.0, max(TH_AVG_DEV_HIGH * (daily_vol_pct / 1.5), 1.0))
    else:
        th_low = TH_AVG_DEV_LOW
        th_high = TH_AVG_DEV_HIGH

    signals: list[MinuteSignal] = []
    observed = 0
    last_emit: dict[int, int] = {}  # direction -> bar idx
    active: list[tuple[int, IndicatorHit]] = []  # 活动窗口：5 根 bar 内的命中参与共振
    ACTIVE_WINDOW = 5

    run_max = run_min = None          # 不含当前 bar 的运行极值（用于"创新高/新低"判定）
    trough_price = trough_dev = None  # 指标 1 低吸的待确认谷底
    trough_idx = -1
    broke_high = broke_low = False
    open30_high = open30_low = None

    for i, p in enumerate(points):
        price = p.get("price")
        if not price:
            continue
        avg = p.get("avg")
        vol = p.get("volume")
        cum_vol = p.get("cum_volume")
        ts = p["ts"]
        prev_price = points[i - 1]["price"] if i > 0 else None
        hits: list[IndicatorHit] = []

        # ---- 指标 1：均价线偏离（谷底确认流）----
        dev = _dev(price, avg)
        if dev is not None:
            if trough_price is not None:
                if price < trough_price:  # 创新低 → 谷底下移，重新计确认
                    trough_price, trough_idx = price, i
                    trough_dev = min(trough_dev or dev, dev)
                elif i - trough_idx >= CONFIRM_BARS:
                    hits.append(IndicatorHit(
                        key="avg_dev", name="均价线偏离", weight=WEIGHTS["avg_dev"], direction=-1,
                        trigger_value=trough_dev or dev, threshold=th_low,
                        evidence=f"偏离均价 {trough_dev:.2f}% 后 {CONFIRM_BARS} 分钟未创新低",
                    ))
                    trough_price = trough_dev = None  # 消费掉，防逐 bar 重复
            elif dev <= th_low:
                trough_price, trough_dev, trough_idx = price, dev, i
            if dev >= th_high:
                hits.append(IndicatorHit(
                    key="avg_dev", name="均价线偏离", weight=WEIGHTS["avg_dev"], direction=1,
                    trigger_value=dev, threshold=th_high,
                    evidence=f"偏离均价 +{dev:.2f}%（≥ {th_high:.1f}% 高抛带）",
                ))

        # ---- 指标 2：量价背离（对"不含当前 bar"的运行极值判定新高/新低）----
        if run_max is not None and vol is not None:
            vol_recent = [q["volume"] for q in points[max(0, i - 5):i] if q.get("volume")]
            mean5 = sum(vol_recent) / len(vol_recent) if vol_recent else None
            if mean5:
                if price > run_max and vol < mean5 * TH_DIV_VOL_LOW:
                    hits.append(IndicatorHit(
                        key="vol_div", name="量价背离", weight=WEIGHTS["vol_div"], direction=1,
                        trigger_value=vol / mean5, threshold=TH_DIV_VOL_LOW,
                        evidence=f"价格创新高但分钟量仅为 5 分钟均量 {vol / mean5:.0%}（顶背离）",
                    ))
                elif price < run_min and vol <= mean5 * TH_DIV_VOL_CRASH:
                    hits.append(IndicatorHit(
                        key="vol_div", name="量价背离", weight=WEIGHTS["vol_div"], direction=-1,
                        trigger_value=vol / mean5, threshold=TH_DIV_VOL_CRASH,
                        evidence=f"价格创新低但量缩至 5 分钟均量 {vol / mean5:.0%}（底背离）",
                    ))

        # ---- 指标 3：量能突变（≥10 根 bar 后才有稳定均量）----
        if i >= 10 and vol is not None:
            hist = [q["volume"] for q in points[:i] if q.get("volume")]
            mean_all = sum(hist) / len(hist)
            if mean_all and vol > mean_all * TH_SURGE:
                direction = -1 if (prev_price is None or price > prev_price) else 1
                hits.append(IndicatorHit(
                    key="surge", name="量能突变", weight=WEIGHTS["surge"], direction=direction,
                    trigger_value=vol / mean_all, threshold=TH_SURGE,
                    evidence=f"单分钟量为已过均量 {vol / mean_all:.1f} 倍，方向{'向上' if direction < 0 else '向下'}",
                ))

        # ---- 指标 4：开盘 30 分钟突破 ----
        if i == OPEN_WINDOW - 1:
            head = [q["price"] for q in points[:OPEN_WINDOW] if q.get("price")]
            open30_high, open30_low = (max(head), min(head)) if head else (None, None)
        if open30_high is not None and i >= OPEN_WINDOW and vol is not None:
            if not broke_high and price > open30_high:
                hist = [q["volume"] for q in points[:i] if q.get("volume")]
                mean_all = sum(hist) / len(hist) if hist else None
                lb = (cum_vol / (yesterday_vol * _elapsed_frac(ts))) if (yesterday_vol and cum_vol) else None
                confirmed = lb is not None and lb >= TH_BREAK_LB
                if confirmed or (lb is None and mean_all and vol >= mean_all * 1.2):
                    broke_high = True
                    hits.append(IndicatorHit(
                        key="breakout", name="开盘突破", weight=WEIGHTS["breakout"], direction=-1,
                        trigger_value=price, threshold=open30_high,
                        evidence=f"放量突破开盘 30 分钟高点 {open30_high:.2f}"
                                 + (f"（量比 {lb:.2f}）" if lb is not None else "（降级口径：分钟量≥均量×1.2）"),
                    ))
            if not broke_low and price < open30_low:
                broke_low = True
                hits.append(IndicatorHit(
                    key="breakout", name="开盘突破", weight=WEIGHTS["breakout"], direction=1,
                    trigger_value=price, threshold=open30_low,
                    evidence=f"跌破开盘 30 分钟低点 {open30_low:.2f}",
                ))

        # ---- 组合：5 bar 活动窗口共振 ----
        # 指标确认常常跨 2~3 根 bar（谷底确认 vs 底背离差 3 根），
        # 逐 bar 聚合会让共振永远达不到阈值——窗口内的命中都算"在场证据"。
        # 例外：surge 是**同 bar 脉冲确认**（方案"强化项，不单独触发"），
        # 不进跨 bar 窗口——否则"放量下杀后缩量企稳"这类教科书低吸
        # 会被早前的下杀脉冲在窗口内持续抵消，永远不出信号。
        run_max = price if run_max is None else max(run_max, price)
        run_min = price if run_min is None else min(run_min, price)
        for h in hits:
            if h.key != "surge":
                active.append((i, h))
        active[:] = [(bi, h) for bi, h in active if i - bi <= ACTIVE_WINDOW]
        combined = list(active) + [(i, h) for h in hits if h.key == "surge"]
        if not combined:
            continue
        total_w = sum(WEIGHTS[k] for k in ACTIVE_KEYS)
        score = sum(h.weight * h.direction for _, h in combined) / total_w
        if abs(score) >= 0.5:
            direction = -1 if score < 0 else 1
            if i - last_emit.get(direction, -10**9) >= cooldown_bars:
                contributing = [h for _, h in combined]
                dominant = max(contributing, key=lambda h: abs(h.weight * h.direction))
                last_emit[direction] = i
                invalidates = {
                    "avg_dev": "跌破均价幅度超 3% 且放量（出货行情，低吸作废）",
                    "vol_div": "随后 5 分钟放量（≥均量×1.5）同向突破，背离失效",
                    "surge": "下一根量能回落至均量以下（脉冲无延续）",
                    "breakout": "价格收回开盘 30 分钟区间内（假突破）",
                }
                inv = "；".join(sorted({invalidates[h.key] for h in contributing}))
                signals.append(MinuteSignal(
                    ts=ts, signal_price=price,
                    bias="低吸偏向" if score < 0 else "高抛偏向",
                    score=round(score, 3),
                    confidence="high" if abs(score) >= 0.7 else "medium",
                    triggered=contributing, invalidate_condition=inv,
                    basis=f"主导指标 {dominant.name}（贡献 {dominant.weight * dominant.direction:+.2f}）",
                ))
                active.clear()  # 消费掉，防止同一批证据逐 bar 重复触发
        elif abs(score) >= 0.3:
            observed += 1

    return {
        "signals": [s.model_dump() for s in signals],
        "observed": observed,
        "degraded": degraded,
        "basis": {
            "weights": WEIGHTS, "active_keys": list(ACTIVE_KEYS),
            "cooldown_bars": cooldown_bars, "confirm_bars": CONFIRM_BARS,
            "thresholds": {"avg_dev_low": th_low, "avg_dev_high": th_high},
            "note": "阈值未经历史校准（方案模块 4.3：40 日样本内调参 + 20 日样本外验证后方可收紧）",
        },
    }


def _elapsed_frac(ts: str) -> float:
    """已开市分钟占比（近似量比分母用），clamp [1/240, 1]。"""
    hhmm = (datetime.fromisoformat(ts) + BJ_OFFSET).strftime("%H:%M")
    mins = int(hhmm[:2]) * 60 + int(hhmm[3:5])
    am = min(max(mins - 570, 0), 120)
    pm = min(max(mins - 780, 0), 120)
    return min(max((am + pm) / 240, 1 / 240), 1.0)
