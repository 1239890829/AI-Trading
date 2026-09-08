"""派发/吸筹规则化（strategy-evolution-plan 方向 2「主力行为识别」P0.5/P1 首项）。

筹码形态（chip_service，CYQ 近似口径）× 量价组合 → 两条可解释规则：
- **派发警示**（distribution_warning）：高位 + 筹码高度密集 + 获利盘 ≥ 85% +
  放量滞涨——「上峰不移下跌不止」的前兆形态。警示而非否决：不进权重、
  不一票否决，先在每日精选理由中留痕，滚动验证胜率后再议进权重；
- **启动观察**（launch_watch）：低位单峰密集 + 缩量回踩主峰——吸筹完成的
  经典形态，作为观察候选信号。

三态纪律：
- chip unavailable（缺仓/查询失败/样本不足）→ available=False + reason 显式降级；
- 条件不满足 → signal=None（「未触发」不是错误，也不是「形态健康」）；
- approx=True 恒透传——获利盘绝对值受流通盘代理假设影响，形态才稳健
  （chip_service 模块 docstring 的口径声明）。

量价口径（provider 日 K，bars: {ts, open, high, low, close, volume}）：
- 放量/缩量：近 5 日均量 / 近 20 日均量（VOL_EXPAND_RATIO / VOL_SHRINK_RATIO）；
- 滞涨：近 3 日累计涨幅 ≤ STALL_CHG_PCT；
- 高位/低位：现价在窗口 [low, high] 区间的分位（PRICE_POS_*）。
"""

from __future__ import annotations

# —— 规则常数（集中成表，供复盘 action_items 调参与测试对照）——
DISTRIBUTE_PROFIT = 0.85     # 获利盘 ≥ 85%
DISTRIBUTE_CONC = 0.45       # 集中度 ≤ 0.45（(p95-p5)/(p95+p5)，越小越密集）
DISTRIBUTE_POS = 0.75        # 现价处于窗口上 1/4（高位）
VOL_EXPAND_RATIO = 1.3       # 近5日均量/近20日均量 ≥ 1.3 → 放量
STALL_CHG_PCT = 1.5          # 近 3 日累计涨幅 ≤ 1.5% → 滞涨（%）

LAUNCH_CONC = 0.40           # 单峰密集
LAUNCH_PROFIT_LO = 0.30      # 获利盘 30%~70%：低位吸筹后的常见区间
LAUNCH_PROFIT_HI = 0.70
LAUNCH_POS = 0.45            # 现价处于窗口下半区（低位）
PEAK_NEAR_PCT = 0.03         # 现价距主峰 ±3% 内 → 「回踩主峰」
VOL_SHRINK_RATIO = 0.70      # 近5日均量/近20日均量 ≤ 0.7 → 缩量


def _vol_ratio(vols: list[float]) -> float | None:
    if len(vols) < 20:
        return None
    v20 = sum(vols[-20:]) / 20
    if v20 <= 0:
        return None
    return sum(vols[-5:]) / 5 / v20


def _chg3_pct(closes: list[float]) -> float | None:
    if len(closes) < 4 or closes[-4] <= 0:
        return None
    return (closes[-1] / closes[-4] - 1) * 100


def _price_pos(closes: list[float], highs: list[float], lows: list[float]) -> float | None:
    if not closes:
        return None
    lo, hi = min(lows), max(highs)
    if hi <= lo:
        return None
    return (closes[-1] - lo) / (hi - lo)


def evaluate_chip_signal(chip: dict | None, bars: list[dict]) -> dict:
    """筹码分布 + 日 K → 派发/吸筹信号评估。

    :param chip: ChipService.distribution() 输出（含 available/profit_ratio/
                 concentration/main_peak/as_of）。
    :param bars: provider 日 K（升序，250 根内）。
    :returns: {available, signal, label, reasons, metrics, approx, as_of}
              signal ∈ distribution_warning | launch_watch | None。
    """
    if not chip or not chip.get("available"):
        return {
            "available": False,
            "signal": None,
            "label": None,
            "reasons": [],
            "metrics": None,
            "reason": (chip or {}).get("reason") or "筹码分布不可用",
        }

    closes = [float(b["close"]) for b in bars if b.get("close")]
    vols = [float(b["volume"]) for b in bars if b.get("volume")]
    highs = [float(b["high"]) for b in bars if b.get("high")]
    lows = [float(b["low"]) for b in bars if b.get("low")]

    profit = chip.get("profit_ratio")
    conc = chip.get("concentration")
    peak = (chip.get("main_peak") or {}).get("price")
    pos = _price_pos(closes, highs, lows)
    vr = _vol_ratio(vols)
    chg3 = _chg3_pct(closes)

    metrics = {
        "profit_ratio": profit,
        "concentration": conc,
        "main_peak": peak,
        "last_close": round(closes[-1], 3) if closes else None,
        "price_pos": round(pos, 3) if pos is not None else None,
        "vol_ratio_5_20": round(vr, 3) if vr is not None else None,
        "chg3_pct": round(chg3, 2) if chg3 is not None else None,
    }

    reasons: list[str] = []
    signal: str | None = None
    label: str | None = None

    # —— 派发警示：高位密集 + 高获利盘 + 放量滞涨 ——
    if (
        profit is not None and profit >= DISTRIBUTE_PROFIT
        and conc is not None and conc <= DISTRIBUTE_CONC
        and pos is not None and pos >= DISTRIBUTE_POS
        and vr is not None and vr >= VOL_EXPAND_RATIO
        and chg3 is not None and chg3 <= STALL_CHG_PCT
    ):
        signal, label = "distribution_warning", "派发警示"
        reasons.append(
            f"获利盘 {profit:.0%} ≥ {DISTRIBUTE_PROFIT:.0%} 且集中度 {conc:.2f}（高位密集）"
        )
        reasons.append(
            f"价位于窗口 {pos:.0%} 分位，近5日均量为20日 {vr:.1f} 倍而 3 日仅 {'+' if chg3 >= 0 else ''}{chg3:.1f}%（放量滞涨）"
        )

    # —— 启动观察：低位单峰密集 + 缩量回踩主峰 ——
    elif (
        conc is not None and conc <= LAUNCH_CONC
        and profit is not None and LAUNCH_PROFIT_LO <= profit <= LAUNCH_PROFIT_HI
        and pos is not None and pos <= LAUNCH_POS
        and peak is not None and closes
        and abs(closes[-1] / peak - 1) <= PEAK_NEAR_PCT
        and vr is not None and vr <= VOL_SHRINK_RATIO
    ):
        signal, label = "launch_watch", "启动观察"
        reasons.append(
            f"集中度 {conc:.2f}（单峰密集）且价位于窗口 {pos:.0%} 分位（低位）"
        )
        reasons.append(
            f"现价距主峰 {peak} {(closes[-1] / peak - 1) * 100:+.1f}%（回踩主峰），"
            f"近5日均量为20日 {vr:.1f} 倍（缩量）"
        )

    return {
        "available": True,
        "signal": signal,
        "label": label,
        "reasons": reasons,
        "metrics": metrics,
        "approx": bool(chip.get("approx")),
        "as_of": chip.get("as_of"),
    }


def chip_basis_text(sig: dict, chip: dict | None) -> str:
    """信号 → 每日精选「筹码」维度依据文本（available 与否都要有诚实表述）。"""
    if not sig.get("available"):
        return f"筹码数据缺失：{sig.get('reason')}（不臆造形态结论）"
    m = sig.get("metrics") or {}
    peak = m.get("main_peak")
    peak_note = ""
    if peak is not None:
        close = m.get("last_close")
        pos_word = ""
        if close:
            pos_word = (
                "（现价下方，支撑）" if peak < close
                else "（现价上方，压力）" if peak > close
                else "（贴现价）"
            )
        peak_note = f"，主峰 {peak}{pos_word}"
    head = f"筹码[{sig['label']}]：" if sig.get("signal") else "筹码形态中性："
    body = (
        f"获利盘 {_pct_cn(m.get('profit_ratio'))}"
        f"、集中度 {m.get('concentration') if m.get('concentration') is not None else '--'}"
        f"{peak_note}"
    )
    if m.get("vol_ratio_5_20") is not None:
        body += f"，量能比(5/20日) {m['vol_ratio_5_20']}"
    return head + body + f"（CYQ 近似口径，as_of {sig.get('as_of')}）"


def _pct_cn(v) -> str:
    return "--" if v is None else f"{v:.0%}"
