"""全市场选股器·技术评分卡（纯函数，可解释输出）。

口径与前端 lib/technical-analysis.ts 的 analyze() 严格一致（MA 排列/MACD/KDJ/RSI/形态），
在此之上叠加权重评分与流动性维度，输出 0-100 分 + 等级 + 逐维依据。

八维：trend / macd / kdj / rsi / volume / liquidity / pattern / rps。
- v2（2026-09-04）补齐形态维——此前前后端形态不对齐，docstring 撒谎，已修正。
- v3（2026-09-04）新增 rps 维（RPS50/RPS120 全市场涨幅分位，来源
  docs/github-repo-audit-financial-api-sequoia-x.md 采纳项）——既有维度全是
  「个股自身 vs 自身历史」，rps 补上「个股 vs 全市场」横截面（强者恒强）。
  数据底座 = 本地 DuckDB marketdb（app/picks/rps.py）；仓未建/未覆盖 → 0.5
  中性（无证据≠负面），不臆造分位。

形态口径差异说明：前端 chgOf 优先取 change_pct（相对昨收涨跌幅）；后端 Bar 无该
字段，统一用实体涨幅 (close-open)/open —— 阈值 5% 语义近似。一字板实体为 0，
两种口径都判不出双响炮，属已知盲区。

红线 3 合规：只给技术面偏向 + 依据 + 失效条件，禁止确定性买卖结论；
summary/evidence 一律描述"多因子共振状态"，不输出"建议买入/卖出"。
"""
from __future__ import annotations

from typing import Literal

from app.market.volume_state import describe as describe_volume
from app.market.volume_state import volume_state as classify_volume

Bar = dict  # {ts, open, high, low, close, volume}

# 评分维度权重（和=1）。版本化：改权重必须递增 SCORER_VERSION。
# v3（2026-09-04）：新增 rps 0.10，其余七维等比缩放（trend .22→.20 等）。
SCORER_VERSION = "v3"
_WEIGHTS = {
    "trend": 0.20,
    "macd": 0.16,
    "kdj": 0.12,
    "rsi": 0.08,
    "volume": 0.12,
    "liquidity": 0.12,
    "pattern": 0.10,
    "rps": 0.10,
}


def sma(closes: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    s = 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(values: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi14(closes: list[float]) -> float | None:
    n = 14
    if len(closes) < n + 1:
        return None
    gain = 0.0
    loss = 0.0
    for i in range(len(closes) - n, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gain += diff
        else:
            loss -= diff
    if loss == 0:
        return 100.0
    rs = gain / n / (loss / n)
    return round(100 - 100 / (1 + rs), 1)


def kdj(bars: list[Bar]) -> dict | None:
    if len(bars) < 9:
        return None
    k = 50.0
    d = 50.0
    for i in range(8, len(bars)):
        window = bars[i - 8 : i + 1]
        hn = max(b["high"] for b in window)
        ln = min(b["low"] for b in window)
        rsv = 50.0 if hn == ln else (bars[i]["close"] - ln) / (hn - ln) * 100
        k = (2 / 3) * k + (1 / 3) * rsv
        d = (2 / 3) * d + (1 / 3) * k
    return {"k": round(k, 1), "d": round(d, 1), "j": round(3 * k - 2 * d, 1)}


def _sig(name: str, bias: Literal["bull", "bear", "neutral"], score: float, detail: str) -> dict:
    return {"name": name, "bias": bias, "score": round(score, 3), "detail": detail}


def patterns(bars: list[Bar]) -> dict | None:
    """最近 3 日（A/B/C）K 线形态判定，口径同前端 technical-analysis.ts。

    三个形态：双响炮（两阳夹一阴回调）、早晨之星（见底反转）、黄昏之星（见顶反转）。
    差异：前端 chgOf 优先取 change_pct（相对昨收），后端 Bar 无该字段，
    统一用实体涨幅 (close-open)/open —— 阈值语义近似（见模块 docstring）。
    :return: None = 近 3 日无形态；命中返回 {name, bias, detail}
    """
    if len(bars) < 3:
        return None
    a, b, c = bars[-3], bars[-2], bars[-1]
    body = lambda x: abs(x["close"] - x["open"])  # noqa: E731
    is_bull = lambda x: x["close"] > x["open"]  # noqa: E731
    chg = lambda x: (x["close"] - x["open"]) / max(x["open"], 0.01) * 100  # noqa: E731

    # 双响炮：大阳(≥5%) + 小实体回调/横盘(<2.5%) + 大阳(≥5%)
    if (
        is_bull(a) and chg(a) >= 5
        and body(b) / max(b["open"], 0.01) < 0.025
        and is_bull(c) and chg(c) >= 5
    ):
        return {
            "name": "形态·双响炮", "bias": "bull",
            "detail": f"大阳({chg(a):.1f}%)-小实体-大阳({chg(c):.1f}%)，两阳夹一阴结构",
        }
    # 早晨之星：阴线(body>2%) - 星线(<1.2%) - 阳线收复 A 实体中点
    if (
        not is_bull(a) and body(a) / max(a["open"], 0.01) > 0.02
        and body(b) / max(b["open"], 0.01) < 0.012
        and is_bull(c) and c["close"] > (a["open"] + a["close"]) / 2
    ):
        return {"name": "形态·早晨之星", "bias": "bull", "detail": "阴线-星线-阳线收复过半，见底反转结构"}
    # 黄昏之星：阳线(body>2%) - 星线(<1.2%) - 阴线跌破 A 实体中点
    if (
        is_bull(a) and body(a) / max(a["open"], 0.01) > 0.02
        and body(b) / max(b["open"], 0.01) < 0.012
        and not is_bull(c) and c["close"] < (a["open"] + a["close"]) / 2
    ):
        return {"name": "形态·黄昏之星", "bias": "bear", "detail": "阳线-星线-阴线跌破过半，见顶反转结构"}
    return None


def score_stock(
    bars: list[Bar],
    *,
    amount_rank_pct: float | None = None,
    turnover_rank_pct: float | None = None,
    rps: dict | None = None,
    rps_note: str | None = None,
) -> dict | None:
    """日K（升序、QFQ）+ 截面流动性分位 + 全市场 RPS → 评分卡。

    :param amount_rank_pct: 候选池内成交额分位 0-1（None 则流动性维度取中性 0.5）
    :param turnover_rank_pct: 候选池内换手率分位 0-1
    :param rps: {"rps50": 0-100, "rps120": 0-100} 全市场涨幅分位
                （app/picks/rps.py 截面；None/缺窗口 → rps 维取中性 0.5）
    :param rps_note: rps 缺失时的**具名原因**（调用方探 RpsService.freshness() 得到，
                     如「数据陈旧：滞后 6 个交易日」）。三态纪律：缺证据要能说清是
                     缺仓、陈旧还是窗口不足——缺省文案只兜底，不遮挡真实原因。
    :return: None = 样本不足（<60 根，视为次新/长停牌，调用方过滤）
    """
    if len(bars) < 60:
        return None
    closes = [b["close"] for b in bars]
    last = bars[-1]
    signals: list[dict] = []
    dim: dict[str, float] = {}

    # 1) 趋势：MA 排列（口径同前端：MA5/10/20/30）
    ma5, ma10, ma20, ma30 = (sma(closes, n)[-1] for n in (5, 10, 20, 30))
    if None in (ma5, ma10, ma20, ma30):
        return None
    bullish = ma5 > ma10 > ma20 > ma30
    bearish = ma5 < ma10 < ma20 < ma30
    above20 = last["close"] > ma20
    if bullish:
        trend = 1.0
    elif bearish:
        trend = 0.0
    elif above20 and ma5 > ma10:
        trend = 0.65
    elif not above20:
        trend = 0.2
    else:
        trend = 0.45
    dim["trend"] = trend
    if bullish:
        signals.append(_sig("MA排列", "bull", 1.0, f"MA5>{ma5:.2f}>MA10>{ma10:.2f}>MA20>{ma20:.2f}>MA30>{ma30:.2f} 多头排列"))
    elif bearish:
        signals.append(_sig("MA排列", "bear", 0.0, f"MA5 {ma5:.2f} < MA10 {ma10:.2f} < MA20 {ma20:.2f} < MA30 {ma30:.2f} 空头排列"))
    else:
        signals.append(_sig("MA排列", "neutral", 0.45, f"均线纠缠：MA5 {ma5:.2f} / MA10 {ma10:.2f} / MA20 {ma20:.2f} / MA30 {ma30:.2f}"))
    signals.append(_sig("MA20位置", "bull" if above20 else "bear", 1.0 if above20 else 0.0, f"收盘 {last['close']:.2f} {'站上' if above20 else '跌破'} MA20 {ma20:.2f}"))

    # 2) MACD（口径同前端：EMA12-26，DEA=EMA9(DIF)）；零轴位置参与判定——
    #    DIF<0 时的 DIF>DEA 只是死叉后的常态反弹，批量排名场景不得计 bull
    ema12, ema26 = ema(closes, 12), ema(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = ema(dif, 9)
    n = len(dif) - 1
    crossed_up = dif[n - 1] <= dea[n - 1] and dif[n] > dea[n]
    crossed_down = dif[n - 1] >= dea[n - 1] and dif[n] < dea[n]
    if crossed_up:
        macd_s, macd_bias = 1.0, "bull"
        macd_detail = f"DIF {dif[n]:.2f} 金叉 DEA {dea[n]:.2f}（近1日）"
    elif crossed_down:
        macd_s, macd_bias = 0.0, "bear"
        macd_detail = f"DIF {dif[n]:.2f} 死叉 DEA {dea[n]:.2f}（近1日）"
    elif dif[n] > dea[n] and dif[n] > 0:
        macd_s, macd_bias = 0.7, "bull"
        macd_detail = f"DIF {dif[n]:.2f} > DEA {dea[n]:.2f}（零轴上方多头运行）"
    elif dif[n] > dea[n]:
        macd_s, macd_bias = 0.45, "neutral"
        macd_detail = f"DIF {dif[n]:.2f} > DEA {dea[n]:.2f}（零轴下方，反弹存疑）"
    else:
        macd_s, macd_bias = 0.3, "bear"
        macd_detail = f"DIF {dif[n]:.2f} < DEA {dea[n]:.2f}（空头运行）"
    dim["macd"] = macd_s
    signals.append(_sig("MACD", macd_bias, macd_s, macd_detail))

    # 3) KDJ + 4) RSI：防飞刀衰减——空头排列（bearish）时"超卖"不构成 bull 依据
    #    （下降趋势中超卖可以更超卖；2 年做T回测 avg_dev 主因即"低吸接飞刀"）
    kd = kdj(bars)
    if kd:
        if kd["j"] < 20:
            kdj_s, kdj_bias, kdj_detail = 0.85, "bull", f"J {kd['j']} 超卖区（<20），存在修复需求"
        elif kd["j"] > 80:
            kdj_s, kdj_bias, kdj_detail = 0.15, "bear", f"J {kd['j']} 超买区（>80），注意回撤"
        elif kd["k"] > kd["d"]:
            kdj_s, kdj_bias, kdj_detail = 0.7, "bull", f"K {kd['k']} > D {kd['d']}，多头运行"
        else:
            kdj_s, kdj_bias, kdj_detail = 0.3, "bear", f"K {kd['k']} < D {kd['d']}，空头运行"
        if bearish and kdj_bias == "bull":
            kdj_s *= 0.3
            kdj_bias = "neutral"
            kdj_detail += "；空头排列下超卖依据衰减"
        dim["kdj"] = kdj_s
        signals.append(_sig("KDJ", kdj_bias, kdj_s, kdj_detail))

    # 4) RSI14（防飞刀衰减同 KDJ）
    r = rsi14(closes)
    if r is not None:
        if r < 30:
            r_s, r_bias, r_detail = 0.8, "bull", f"RSI {r} 超卖（<30）"
        elif r > 80:
            r_s, r_bias, r_detail = 0.1, "bear", f"RSI {r} 超买（>80）"
        elif 45 <= r <= 65:
            r_s, r_bias, r_detail = 1.0, "bull", f"RSI {r} 健康强势区间"
        else:
            r_s, r_bias, r_detail = 0.6, "neutral", f"RSI {r} 中性区间"
        if bearish and r_bias == "bull":
            r_s *= 0.3
            r_bias = "neutral"
            r_detail += "；空头排列下超卖依据衰减"
        dim["rsi"] = r_s
        signals.append(_sig("RSI14", r_bias, r_s, r_detail))

    # 5) 量价：当日量 / 前 5 日均量；放量方向必须结合当日涨跌——放量下杀不是"温和放量"
    #    量能语义（P1-35）：分档与打分**一律保持不变**（零回归），仅额外用统一语义层
    #    `volume_state` 产出六态 + 三层解读文案，收口"缩量/放量"的跨模块词汇。
    vol_state = "unknown"
    vols = [b.get("volume") or 0 for b in bars]
    v5 = sum(vols[-6:-1]) / 5 if len(vols) >= 6 and vols[-6:-1] and sum(vols[-6:-1]) > 0 else None
    if v5:
        ratio = vols[-1] / v5
        day_up = len(closes) >= 2 and closes[-1] > closes[-2]
        chg_pct = (
            (closes[-1] / closes[-2] - 1) * 100
            if len(closes) >= 2 and closes[-2] else None
        )
        vol_state = classify_volume(ratio, chg_pct, bands="tech")
        if ratio > 4.0:
            v_s, v_bias, v_detail = 0.35, "neutral", f"爆量（量比 {ratio:.2f}），警惕分歧"
        elif ratio < 0.6:
            v_s, v_bias, v_detail = 0.3, "bear", f"显著缩量（量比 {ratio:.2f}）"
        elif 1.0 <= ratio <= 2.5:
            if day_up:
                v_s, v_bias, v_detail = 1.0, "bull", f"温和放量上行（量比 {ratio:.2f}）"
            else:
                v_s, v_bias, v_detail = 0.3, "bear", f"放量下杀（量比 {ratio:.2f} 且当日收跌）"
        else:
            v_s, v_bias, v_detail = 0.6, "neutral", f"量能平稳（量比 {ratio:.2f}）"
        dim["volume"] = v_s
        signals.append(_sig("量价", v_bias, v_s, v_detail))

    # 6) 流动性：候选池内分位（成交额 0.6 + 换手 0.4）
    a_pct = 0.5 if amount_rank_pct is None else min(max(amount_rank_pct, 0.0), 1.0)
    t_pct = 0.5 if turnover_rank_pct is None else min(max(turnover_rank_pct, 0.0), 1.0)
    liq = a_pct * 0.6 + t_pct * 0.4
    dim["liquidity"] = liq
    signals.append(_sig(
        "流动性", "bull" if liq >= 0.6 else ("bear" if liq < 0.3 else "neutral"), liq,
        f"候选池内分位：成交额 {a_pct:.0%} / 换手 {t_pct:.0%}",
    ))

    # 7) 形态（v2 起，口径同前端）：无形态记中性 0.5（无证据≠负面，七维恒齐）；
    #    空头排列下的 bull 反转形态按"防飞刀"惯例衰减（同 KDJ/RSI 规则）
    pat = patterns(bars)
    if pat is None:
        dim["pattern"] = 0.5
        signals.append(_sig("形态", "neutral", 0.5, "近 3 日无形态特征"))
    elif pat["bias"] == "bull":
        p_s, p_bias, p_detail = 1.0, "bull", pat["detail"]
        if bearish:
            p_s, p_bias = 0.3, "neutral"
            p_detail += "；空头排列下反转形态依据衰减"
        dim["pattern"] = p_s
        signals.append(_sig(pat["name"], p_bias, p_s, p_detail))
    else:
        dim["pattern"] = 0.0
        signals.append(_sig(pat["name"], "bear", 0.0, pat["detail"]))

    # 8) RPS 相对强度（v3 起）：N 日涨幅全市场分位——唯一「个股 vs 全市场」维度。
    #    score = 可用窗口分位均值 / 100；两窗口全缺 → 0.5 中性（仓未建/次新，
    #    无证据≠负面）；单一窗口可用时只用该窗口（次新股 rps120 天然缺失是
    #    事实而非异常，单独提示即可）。
    rps50 = rps.get("rps50") if isinstance(rps, dict) else None
    rps120 = rps.get("rps120") if isinstance(rps, dict) else None
    known: list[int] = [v for v in (rps50, rps120) if isinstance(v, (int, float))]
    if known:
        r_s = sum(known) / len(known) / 100.0
        lo, hi = min(known), max(known)
        if r_s >= 0.85:
            rps_bias, rps_detail = "bull", f"RPS {lo}/{hi}，全市场最强梯队（≥85）"
        elif r_s >= 0.60:
            rps_bias, rps_detail = "bull", f"RPS {lo}/{hi}，强于市场多数（≥60）"
        elif r_s < 0.30:
            rps_bias, rps_detail = "bear", f"RPS {lo}/{hi}，弱于市场多数（<30）"
        else:
            rps_bias, rps_detail = "neutral", f"RPS {lo}/{hi}，市场中性带"
        if len(known) == 1:
            rps_detail += "（另一窗口样本不足，未计入）"
    else:
        r_s, rps_bias = 0.5, "neutral"
        rps_detail = (rps_note or "RPS 未覆盖（marketdb 仓未建/未回补/数据陈旧），中性处理")
    dim["rps"] = r_s
    signals.append(_sig("RPS相对强度", rps_bias, r_s, rps_detail))

    total = sum(_WEIGHTS[k] * v for k, v in dim.items()) * 100
    bull_count = sum(1 for s in signals if s["bias"] == "bull")
    bear_count = sum(1 for s in signals if s["bias"] == "bear")
    bias = "bull" if bull_count >= bear_count + 2 else ("bear" if bear_count >= bull_count + 2 else "neutral")
    grade = "A" if total >= 75 else ("B" if total >= 60 else ("C" if total >= 45 else "D"))
    summary = (
        f"技术面偏多：{bull_count} 多 / {bear_count} 空，共振向上"
        if bias == "bull"
        else f"技术面偏空：{bull_count} 多 / {bear_count} 空，共振向下"
        if bias == "bear"
        else f"技术面中性：{bull_count} 多 / {bear_count} 空，多空分歧"
    )
    fail_conditions = [f"收盘跌破 MA20（{ma20:.2f}）则趋势依据失效"]
    if kd and kd["j"] > 90:
        fail_conditions.append(f"KDJ J={kd['j']} 高位钝化，超买依据随时反转")
    return {
        "signals": signals,
        "dimensions": {k: round(v, 3) for k, v in dim.items()},
        "score": round(total, 1),
        "grade": grade,
        "bias": bias,
        "summary": summary,
        "fail_conditions": fail_conditions,
        "scorer_version": SCORER_VERSION,
        # P1-35 量能语义（**纯增量字段**，不影响上述任何数值口径）：
        # 六态枚举供个股/板块/大盘三层共用，避免各处自造"缩量/放量"词表。
        "volume_state": vol_state,
        "volume_note": describe_volume(vol_state),
    }
