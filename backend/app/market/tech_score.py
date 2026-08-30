"""全市场选股器·技术评分卡（纯函数，可解释输出）。

口径与前端 lib/technical-analysis.ts 的 analyze() 严格一致（MA 排列/MACD/KDJ/RSI/形态），
在此之上叠加权重评分与流动性维度，输出 0-100 分 + 等级 + 逐维依据。

红线 3 合规：只给技术面偏向 + 依据 + 失效条件，禁止确定性买卖结论；
summary/evidence 一律描述"多因子共振状态"，不输出"建议买入/卖出"。
"""
from __future__ import annotations

from typing import Literal

Bar = dict  # {ts, open, high, low, close, volume}

# 评分维度权重（和=1）。版本化：改权重必须递增 SCORER_VERSION。
SCORER_VERSION = "v1"
_WEIGHTS = {
    "trend": 0.25,
    "macd": 0.20,
    "kdj": 0.15,
    "rsi": 0.10,
    "volume": 0.15,
    "liquidity": 0.15,
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


def score_stock(
    bars: list[Bar],
    *,
    amount_rank_pct: float | None = None,
    turnover_rank_pct: float | None = None,
) -> dict | None:
    """日K（升序、QFQ）+ 截面流动性分位 → 评分卡。

    :param amount_rank_pct: 候选池内成交额分位 0-1（None 则流动性维度取中性 0.5）
    :param turnover_rank_pct: 候选池内换手率分位 0-1
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
    vols = [b.get("volume") or 0 for b in bars]
    v5 = sum(vols[-6:-1]) / 5 if len(vols) >= 6 and vols[-6:-1] and sum(vols[-6:-1]) > 0 else None
    if v5:
        ratio = vols[-1] / v5
        day_up = len(closes) >= 2 and closes[-1] > closes[-2]
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
    }
