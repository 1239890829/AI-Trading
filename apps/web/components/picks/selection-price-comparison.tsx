"use client";

import {useState} from "react";
import type {TradingCard} from "./pick-card";
import {dateTimeTextBJ, fmt, pctColor, pctText, sourceLabel} from "@/lib/format";
import styles from "./selection-price-comparison.module.css";

const positive = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
export type SelectionPriceItem = Pick<TradingCard, "symbol" | "name" | "origin" | "selectionEntry" | "execution" | "price">;
function timestamp(value?: string | null): number | null {
  if (!value) return null;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match) return null;
  const local = new Date(`${match[1]}Z`), instant = Date.parse(value);
  return Number.isFinite(instant) && Number.isFinite(local.getTime()) && local.toISOString().slice(0, 19) === match[1] ? instant : null;
}

/** Two recorded observations, never an invented price path or a simulated fill. */
export function SelectionPriceComparison({item, readError = false}: {item: SelectionPriceItem; readError?: boolean}) {
  return <PricePair key={`${item.symbol}:${item.origin}:${item.selectionEntry?.source_version ?? ""}`} item={item} readError={readError}/>;
}

function PricePair({item, readError}: {item: SelectionPriceItem; readError: boolean}) {
  const [selected, setSelected] = useState<0 | 1>(0);
  const entry = item.selectionEntry, snapshot = item.execution?.executable_snapshot;
  const first = positive(entry?.reference_price), observed = positive(snapshot?.price);
  const firstAt = timestamp(entry?.quote_as_of), selectedAt = timestamp(entry?.selected_at), observedAt = timestamp(snapshot?.as_of);
  const bound = item.origin === "picks" && !readError && entry?.state === "recorded" && !!entry.source_version && first !== null && firstAt !== null
    && selectedAt !== null && firstAt <= selectedAt && snapshot?.state === "ready" && observed !== null && observedAt !== null && observedAt >= selectedAt;
  const percentage = bound ? (observed! / first! - 1) * 100 : NaN;
  const comparable = bound && Number.isFinite(percentage);
  const delta = comparable ? percentage : null;
  const currentPrice = item.origin === "intraday" ? positive(item.price) : observed;
  const currentLabel = item.origin === "intraday" ? "本次名单价" : "执行复核记录";
  const currentState = readError ? "读取失败，当前状态待核" : item.origin === "intraday" ? "报价时点未提供" : snapshot?.state === "ready" && observedAt !== null ? "按原执行快照展示" : "快照或报价时点未就绪";
  const firstState = entry?.state === "recorded" && firstAt !== null ? "原入选报价" : "原记录有缺项";
  const values = [first, comparable ? observed : currentPrice];
  const labels = ["加入参考价", currentLabel];
  const times = [entry?.quote_as_of, item.origin === "intraday" ? null : snapshot?.as_of];
  const notes = [firstState, currentState];
  return <section className={styles.root} aria-label={`${item.name || item.symbol}的两次价格记录`}>
    <div className={styles.heading}><h3>加入后，价格如何变化</h3><span>观察记录，非成交收益</span></div>
    <div className={styles.pair} data-comparable={comparable}>
      {([0, 1] as const).map(index => <button key={index} type="button" aria-label={`核对${labels[index]}`} aria-pressed={selected === index}
        onClick={() => setSelected(index)} onFocus={() => setSelected(index)} className={styles.observation}>
        <span>{labels[index]}</span><strong>{values[index] === null ? "未提供" : `¥${fmt(values[index])}`}</strong><small>{notes[index]}</small>
      </button>)}
      <div className={styles.difference} aria-label="价格观察差值">
        {comparable ? <><strong className={pctColor(delta)}>{pctText(delta)}</strong><span>两次观察价差</span></> : <><strong>暂不比较</strong><span>{readError ? "本次读取失败" : item.origin === "intraday" ? "名单价缺报价时点" : "缺可对照的完整快照"}</span></>}
      </div>
    </div>
    {comparable && <div className={styles.ruler} aria-label="两次真实价格记录的时序对照">
      <span>加入</span><div className={styles.axis}><span className={styles.connector}/>
        <button type="button" aria-label="价格刻度：加入记录" aria-pressed={selected === 0} onFocus={() => setSelected(0)} onClick={() => setSelected(0)} className={styles.first}/>
        <button type="button" aria-label="价格刻度：执行复核" aria-pressed={selected === 1} onFocus={() => setSelected(1)} onClick={() => setSelected(1)} className={styles.last}/>
      </div><span>复核</span>
    </div>}
    <div className={styles.context} aria-live="polite">
      <strong>{labels[selected]}</strong><span>{timestamp(times[selected]) === null ? "报价时刻未提供" : dateTimeTextBJ(times[selected], {seconds: true})}</span>
      <span>{selected === 0 ? entry?.source === "daily_generation" ? "每日生成记录" : entry?.source === "intraday_rank_archive" ? "盘中加入记录" : "来源未提供" : item.origin === "intraday" ? "名单展示，不据此验证报价新鲜" : sourceLabel(snapshot?.source)}</span>
      {selected === 0 && entry?.selected_at && <span>入选记录：{dateTimeTextBJ(entry.selected_at, {seconds: true})}</span>}
      {selected === 0 && entry?.source_version && <span title={entry.source_version}>版本 {entry.source_version.slice(0, 12)}</span>}
    </div>
  </section>;
}
