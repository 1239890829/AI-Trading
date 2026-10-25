"use client";

import { useId, useState } from "react";
import type { ReviewMetricHistoryPoint, ReviewMetricHistoryReference } from "@/lib/api";
import { dateTimeTextBJ, fmt } from "@/lib/format";

const METRICS = [
  ["seal_rate", "封板率"], ["promo_1to2", "1进2晋级"],
  ["promo_2to3", "2进3晋级"], ["max_board", "连板高度"],
] as const;
type Metric = typeof METRICS[number][0];
const DAY_MS = 86400000;
const number = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : null;
const count = (value: unknown) => {const n = number(value); return n !== null && Number.isSafeInteger(n) && n >= 0 ? n : null;};
const isoDay = (value: unknown): value is string => typeof value === "string"
  && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(`${value}T00:00:00Z`))
  && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
const compactDay = (value: string) => value.replaceAll("-", "");
const dayNumber = (value: string) => Date.parse(`${value}T00:00:00Z`) / DAY_MS;
const metricValue = (point: ReviewMetricHistoryPoint | undefined, metric: Metric) => {
  const n = number(point?.[metric]);
  if (metric === "seal_rate") {
    const base = count(point?.seal_base), up = count(point?.limit_up), broken = count(point?.n_break), breakRate = number(point?.break_rate);
    if (base === null || base <= 0 || up === null || broken === null || up + broken !== base
      || n === null || breakRate === null || Math.abs(breakRate - broken / base) > 0.000051
      || Math.abs(n - up / base) > 1e-9) return null;
  }
  return n !== null && (metric === "max_board" ? Number.isInteger(n) && n >= 0 : n >= 0 && n <= 1) ? n : null;
};
const metricText = (value: number | null, metric: Metric) => value === null ? "未记录" : metric === "max_board" ? `${value}板` : `${fmt(value * 100, 1)}%`;

/** Validate the separate reference scope; the original report's date is not its evidence. */
function validReference(ref: ReviewMetricHistoryReference, tradeDate: string) {
  if (ref.contract_version !== "metric_history_reference_v1" || ref.role !== "read_time_aggregate_reference"
    || ref.source !== "sentiment.metric_history" || !isoDay(ref.requested_trade_date)
    || compactDay(ref.requested_trade_date) !== compactDay(tradeDate)
    || !isoDay(ref.window_start) || !isoDay(ref.window_end) || ref.window_end > ref.requested_trade_date
    || dayNumber(ref.window_end) - dayNumber(ref.window_start) !== 29
    || !["partial", "unavailable", "error"].includes(ref.state) || !Array.isArray(ref.points) || ref.points.length > 30) return false;
  let previous = "";
  return ref.points.every(point => {
    if (!point || typeof point !== "object" || !isoDay(point.date) || point.date < ref.window_start
      || point.date > ref.window_end || point.date <= previous) return false;
    previous = point.date;
    return true;
  });
}

export function ReviewMetricTrends({ reference, tradeDate }: {reference?: ReviewMetricHistoryReference; tradeDate: string}) {
  const id = useId();
  const [metric, setMetric] = useState<Metric>("seal_rate");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  if (!reference) return null;
  if (!validReference(reference, tradeDate)) return <p role="alert" className="market-review-unavailable">历史参考身份或结构不符，未使用该返回。</p>;
  const points = reference.points;
  const selected = selectedDay ?? points.at(-1)?.date ?? reference.window_end;
  const selectedIndex = Math.max(0, Math.min(29, dayNumber(selected) - dayNumber(reference.window_start)));
  const current = points.find(point => point.date === selected);
  const value = metricValue(current, metric);
  const label = METRICS.find(([key]) => key === metric)![1];
  const dayAt = (index: number) => new Date((dayNumber(reference.window_start) + index) * DAY_MS).toISOString().slice(0, 10);
  const maximum = metric === "max_board" ? Math.max(1, ...points.map(point => metricValue(point, metric) ?? 0)) : 1;
  const x = (index: number) => 32 + index / 29 * 536;
  const y = (n: number) => 88 - n / maximum * 68;
  const rows = Array.from({length: 30}, (_, index) => ({index, point: points.find(point => point.date === dayAt(index))}));
  let drawing = false;
  const path = rows.map(({index, point}) => {
    const n = metricValue(point, metric);
    if (n === null) {drawing = false; return "";}
    const command = `${drawing ? "L" : "M"}${x(index)},${y(n)}`;
    drawing = true;
    return command;
  }).join(" ");
  const effective = points.filter(point => metricValue(point, metric) !== null).length;
  const missing = 30 - points.length;
  const positiveBase = count(current?.seal_base);
  const sealBase = positiveBase !== null && positiveBase > 0 ? positiveBase : null;
  const localClock = typeof reference.dataset_updated_at === "string" && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(reference.dataset_updated_at)
    && Number.isFinite(Date.parse(reference.dataset_updated_at)) ? dateTimeTextBJ(reference.dataset_updated_at) : "未记录或不可核";

  return <section aria-label="历史情绪汇总参考" className="mt-4">
    <div className="market-review-section-title"><h5>历史情绪趋势</h5><span>读取时追加 · 采集参考</span></div>
    <p className="market-review-caption">{reference.window_start} — {reference.window_end} · 已存{points.length}日 / 未记录{missing}个自然日（含休市与缺档）</p>
    {points.length ? <>
      <div className="market-review-controls"><div role="group" aria-label="历史趋势指标">{METRICS.map(([key, title]) => <button key={key} type="button" aria-pressed={metric === key} onClick={() => setMetric(key)}>{title}</button>)}</div><span className="text-xs text-[var(--ui-muted)]">本窗有效 {effective}日</span></div>
      <div className="relative"><span className="absolute left-0 top-1 text-[10px] text-[var(--ui-muted)]">{metric === "max_board" ? `${maximum}板` : "100%"}</span><span className="absolute bottom-2 left-0 text-[10px] text-[var(--ui-muted)]">{metric === "max_board" ? "0板" : "0%"}</span>
      <svg viewBox="0 0 600 100" preserveAspectRatio="none" className="h-24 w-full" role="img" aria-label={`${label}历史采集趋势，有效${effective}日，空缺不连线`}>
        <line x1="32" x2="568" y1="20" y2="20" stroke="var(--ui-line)"/>
        <line x1="32" x2="568" y1="88" y2="88" stroke="var(--ui-line)"/>
        <path d={path} fill="none" stroke="var(--ui-accent)" strokeWidth="1.5"/>
        {rows.map(({index, point}) => {const n = metricValue(point, metric); return n === null ? null : <circle key={index} cx={x(index)} cy={y(n)} r={point?.date === selected ? 3.8 : 2} fill="var(--ui-accent)"/>;})}
        <line x1={x(selectedIndex)} x2={x(selectedIndex)} y1="14" y2="94" stroke="var(--ui-muted)" strokeDasharray="2 3"/>
      </svg>
      </div><div className="flex justify-between text-[10px] text-[var(--ui-muted)]"><span>{reference.window_start.slice(5)}</span><span>{reference.window_end.slice(5)}</span></div>
      <label htmlFor={id} className="market-review-caption">查看日期 · {selected} · {label} {metricText(value, metric)}</label>
      <input id={id} aria-label="查看历史日期" type="range" min={0} max={29} step={1} value={selectedIndex}
        className="w-full accent-[var(--ui-accent)]" aria-valuetext={selected}
        onChange={event => setSelectedDay(dayAt(Number(event.target.value)))}/>
      <dl className="market-review-facts">{METRICS.map(([key, title]) => <div key={key}><dt>{title}</dt><dd>{metricText(metricValue(current, key), key)}</dd></div>)}</dl>
      <p className="market-review-caption">{current ? `涨停 ${count(current.limit_up) ?? "未记录"}家 · 炸板 ${count(current.n_break) ?? "未记录"}家 · 封板分母 ${sealBase ?? "未记录或为零"}家。晋级基数未保存。` : "该自然日未记录，不补成0，也不推断是否交易日。"}</p>
    </> : <p className="market-review-unavailable">{reference.reason || "本地历史汇总暂不可用，未触发回补。"}</p>}
    <details className="market-review-source"><summary>口径与缺口</summary><p>读取时追加的历史汇总参考，不代表原报告当时已见；不改写原判断。</p><p>封板率＝涨停家数 ÷（涨停＋炸板）；仅分母有效且与已存炸板率一致时展示。1进2、2进3为原库已存比例，基数未保存，不是上涨概率或策略胜率。</p><dl><div><dt>本地文件更新</dt><dd>{localClock}；不是逐日源钟</dd></div><div><dt>原始证据</dt><dd>逐日源钟与原始池哈希未保存；历史完整性未核</dd></div></dl><p>30个自然日内，未记录日期可能是休市或缺档；不把自然日空缺当作交易日缺失。{count(reference.invalid_rows) ? `读取时另跳过${reference.invalid_rows}条日期或结构无效记录。` : ""}</p></details>
  </section>;
}
