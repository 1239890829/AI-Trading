/** 技术分析引擎（纯函数，可解释输出）。
 * 精选常用指标：趋势(MA排列)、MACD、KDJ、RSI、形态(双响炮/早晨之星/黄昏之星)。
 * 结论 = 多因子共振计数，附每项依据；禁止输出确定性买卖结论，仅给偏向与依据。 */

export interface Bar {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
  change_pct?: number | null;
}

export interface Signal {
  name: string;
  bias: "bull" | "bear" | "neutral";
  detail: string;
}

export interface TechConclusion {
  bias: "bull" | "bear" | "neutral";
  bullCount: number;
  bearCount: number;
  signals: Signal[];
  summary: string;
}

export function calcMA(closes: number[], n: number): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i];
    if (i >= n) sum -= closes[i - n];
    out.push(i >= n - 1 ? +(sum / n).toFixed(3) : null);
  }
  return out;
}

export function calcEMA(values: number[], n: number): number[] {
  const k = 2 / (n + 1);
  const out: number[] = [values[0]];
  for (let i = 1; i < values.length; i++) out.push(values[i] * k + out[i - 1] * (1 - k));
  return out;
}

function calcRSI(closes: number[], n = 14): number | null {
  if (closes.length < n + 1) return null;
  let gain = 0;
  let loss = 0;
  for (let i = closes.length - n; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff > 0) gain += diff;
    else loss -= diff;
  }
  if (loss === 0) return 100;
  const rs = gain / n / (loss / n);
  return +(100 - 100 / (1 + rs)).toFixed(1);
}

function calcKDJ(bars: Bar[]): { k: number; d: number; j: number } | null {
  if (bars.length < 9) return null;
  let k = 50;
  let d = 50;
  for (let i = 8; i < bars.length; i++) {
    const window = bars.slice(i - 8, i + 1);
    const hn = Math.max(...window.map((b) => b.high));
    const ln = Math.min(...window.map((b) => b.low));
    const rsv = hn === ln ? 50 : ((bars[i].close - ln) / (hn - ln)) * 100;
    k = (2 / 3) * k + (1 / 3) * rsv;
    d = (2 / 3) * d + (1 / 3) * k;
  }
  return { k: +k.toFixed(1), d: +d.toFixed(1), j: +(3 * k - 2 * d).toFixed(1) };
}

/** 主入口：多因子技术评估（样本=全部传入 K 线，结论看最近 3 日） */
export function analyze(bars: Bar[]): TechConclusion | null {
  if (bars.length < 30) return null;
  const closes = bars.map((b) => b.close);
  const last = bars[bars.length - 1];
  const signals: Signal[] = [];

  // 1) 趋势：MA 排列
  const ma5 = calcMA(closes, 5);
  const ma10 = calcMA(closes, 10);
  const ma20 = calcMA(closes, 20);
  const ma30 = calcMA(closes, 30);
  const l5 = ma5[ma5.length - 1];
  const l10 = ma10[ma10.length - 1];
  const l20 = ma20[ma20.length - 1];
  const l30 = ma30[ma30.length - 1];
  // 提升到函数级：后续 KDJ/RSI 的防飞刀衰减需要读取空头排列状态
  const bullish = l5 != null && l10 != null && l20 != null && l30 != null && l5 > l10 && l10 > l20 && l20 > l30;
  const bearish = l5 != null && l10 != null && l20 != null && l30 != null && l5 < l10 && l10 < l20 && l20 < l30;
  if (l5 != null && l10 != null && l20 != null && l30 != null) {
    signals.push(
      bullish
        ? { name: "MA排列", bias: "bull", detail: `MA5>${l5}>MA10>${l10}>MA20>${l20}>MA30>${l30} 多头排列` }
        : bearish
          ? { name: "MA排列", bias: "bear", detail: `MA5 ${l5} < MA10 ${l10} < MA20 ${l20} 空头排列` }
          : { name: "MA排列", bias: "neutral", detail: `均线纠缠：MA5 ${l5} / MA10 ${l10} / MA20 ${l20} / MA30 ${l30}` }
    );
    // 站上/跌破 MA20
    signals.push(
      last.close > (l20 ?? 0)
        ? { name: "MA20位置", bias: "bull", detail: `收盘 ${last.close} 站上 MA20 ${l20}` }
        : { name: "MA20位置", bias: "bear", detail: `收盘 ${last.close} 跌破 MA20 ${l20}` }
    );
  }

  // 2) MACD
  const ema12 = calcEMA(closes, 12);
  const ema26 = calcEMA(closes, 26);
  const dif = closes.map((_, i) => ema12[i] - ema26[i]);
  const dea = calcEMA(dif, 9);
  const n = dif.length - 1;
  const crossedUp = dif[n - 1] <= dea[n - 1] && dif[n] > dea[n];
  const crossedDown = dif[n - 1] >= dea[n - 1] && dif[n] < dea[n];
    // 零轴位置参与判定（与后端 tech_score 对齐）：DIF<0 时的 DIF>DEA 只是
    // 死叉后的常态反弹，不计 bull
    signals.push(
      crossedUp
        ? { name: "MACD", bias: "bull", detail: `DIF ${dif[n].toFixed(2)} 金叉 DEA ${dea[n].toFixed(2)}（近1日）` }
        : crossedDown
          ? { name: "MACD", bias: "bear", detail: `DIF ${dif[n].toFixed(2)} 死叉 DEA ${dea[n].toFixed(2)}（近1日）` }
          : dif[n] > dea[n] && dif[n] > 0
            ? { name: "MACD", bias: "bull", detail: `DIF ${dif[n].toFixed(2)} > DEA ${dea[n].toFixed(2)}（零轴上方多头运行）` }
            : dif[n] > dea[n]
              ? { name: "MACD", bias: "neutral", detail: `DIF ${dif[n].toFixed(2)} > DEA ${dea[n].toFixed(2)}（零轴下方，反弹存疑）` }
              : { name: "MACD", bias: "bear", detail: `DIF ${dif[n].toFixed(2)} < DEA ${dea[n].toFixed(2)}（空头运行）` }
    );

  // 3) KDJ + 4) RSI：防飞刀衰减（与后端 tech_score 口径对齐）——空头排列（bearish）时
  //    "超卖"不构成 bull 依据：下降趋势中超卖可以更超卖（低吸接飞刀是回测主因亏损形态）
  const kdj = calcKDJ(bars);
  if (kdj) {
    let kdjSignal: Signal =
      kdj.j < 20
        ? { name: "KDJ", bias: "bull", detail: `J ${kdj.j} 超卖区（<20），存在修复需求` }
        : kdj.j > 80
          ? { name: "KDJ", bias: "bear", detail: `J ${kdj.j} 超买区（>80），注意回撤` }
          : kdj.k > kdj.d
            ? { name: "KDJ", bias: "bull", detail: `K ${kdj.k} > D ${kdj.d}，多头运行` }
            : { name: "KDJ", bias: "bear", detail: `K ${kdj.k} < D ${kdj.d}，空头运行` };
    if (bearish && kdjSignal.bias === "bull") {
      kdjSignal = { ...kdjSignal, bias: "neutral", detail: kdjSignal.detail + "；空头排列下超卖依据衰减" };
    }
    signals.push(kdjSignal);
  }

  // 4) RSI（防飞刀衰减同 KDJ）
  const rsi = calcRSI(closes);
  if (rsi != null) {
    let rsiSignal: Signal =
      rsi < 30
        ? { name: "RSI14", bias: "bull", detail: `RSI ${rsi} 超卖（<30）` }
        : rsi > 70
          ? { name: "RSI14", bias: "bear", detail: `RSI ${rsi} 超买（>70）` }
          : { name: "RSI14", bias: "neutral", detail: `RSI ${rsi} 中性区间` };
    if (bearish && rsiSignal.bias === "bull") {
      rsiSignal = { ...rsiSignal, bias: "neutral", detail: rsiSignal.detail + "；空头排列下超卖依据衰减" };
    }
    signals.push(rsiSignal);
  }

  // 5) 形态（最近 3 日）
  const A = bars[bars.length - 3];
  const B = bars[bars.length - 2];
  const C = bars[bars.length - 1];
  const body = (b: Bar) => Math.abs(b.close - b.open);
  const isBull = (b: Bar) => b.close > b.open;
  const chgOf = (b: Bar) => b.change_pct ?? ((b.close - b.open) / b.open) * 100;

  // 双响炮：大阳 + 小实体回调/横盘 + 大阳
  if (isBull(A) && chgOf(A) >= 5 && body(B) / Math.max(B.open, 0.01) < 0.025 && isBull(C) && chgOf(C) >= 5) {
    signals.push({ name: "形态·双响炮", bias: "bull", detail: `大阳(${chgOf(A).toFixed(1)}%)-小实体-大阳(${chgOf(C).toFixed(1)}%)，两阳夹一阴结构` });
  }
  // 早晨之星：阴 - 小实体低点 - 阳收复
  if (
    !isBull(A) && body(A) / Math.max(A.open, 0.01) > 0.02 &&
    body(B) / Math.max(B.open, 0.01) < 0.012 &&
    isBull(C) && C.close > (A.open + A.close) / 2
  ) {
    signals.push({ name: "形态·早晨之星", bias: "bull", detail: "阴线-星线-阳线收复过半，见底反转结构" });
  }
  // 黄昏之星：阳 - 小实体高点 - 阴跌破
  if (
    isBull(A) && body(A) / Math.max(A.open, 0.01) > 0.02 &&
    body(B) / Math.max(B.open, 0.01) < 0.012 &&
    !isBull(C) && C.close < (A.open + A.close) / 2
  ) {
    signals.push({ name: "形态·黄昏之星", bias: "bear", detail: "阳线-星线-阴线跌破过半，见顶反转结构" });
  }

  const bullCount = signals.filter((s) => s.bias === "bull").length;
  const bearCount = signals.filter((s) => s.bias === "bear").length;
  const bias: TechConclusion["bias"] = bullCount >= bearCount + 2 ? "bull" : bearCount >= bullCount + 2 ? "bear" : "neutral";
  const summary =
    bias === "bull"
      ? `技术面偏多：${bullCount} 多 / ${bearCount} 空，多因子共振向上`
      : bias === "bear"
        ? `技术面偏空：${bullCount} 多 / ${bearCount} 空，共振向下`
        : `技术面中性：${bullCount} 多 / ${bearCount} 空，多空分歧`;
  return { bias, bullCount, bearCount, signals, summary };
}
