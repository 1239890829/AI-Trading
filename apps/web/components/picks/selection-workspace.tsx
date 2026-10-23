"use client";

import {useRef, useState} from "react";
import {CardEntryRow} from "./card-entries";
import {PickSelectionEvidence, selectionReading, selectionScoreItems, SUB_LABELS, tradabilityLabel, type DailySelectionEvidence, type TradingCard} from "./pick-card";
import {SelectionPriceComparison} from "./selection-price-comparison";
import {StockLink} from "@/components/stock-link";
import {dateTimeTextBJ, fmt, sourceLabel} from "@/lib/format";
import styles from "./selection-workspace.module.css";

type Column = {key: string; label: string; unit: string};
const METRICS: Column[] = [
  {key: "change_pct", label: "当日涨幅", unit: "%"}, {key: "amount", label: "成交额", unit: "元"},
  {key: "theme_limit_ups", label: "题材涨停", unit: "家"}, {key: "theme_share", label: "题材占比", unit: "%"},
  {key: "limit_up_gap_pct", label: "距涨停价", unit: "%"},
];
const SCORES: Column[] = SUB_LABELS.map(([key, label]) => ({key, label: label === "基本" ? "基本面" : label, unit: "分"}));
export type SelectionWorkspaceGroup = {
  key: "intraday" | "picks"; title: string; description: string; items: TradingCard[]; total?: number; readError?: boolean; contextKey?: string; readState?: "pending" | "unavailable" | "ready";
};
type Cell = {value: number | null; measured: boolean; state: string; label: string; basis: string; unit: string};
const numberText = (value: number, unit: string) => unit === "元" && Math.abs(value) >= 100000000 ? `${(value / 100000000).toFixed(2)}亿`
  : unit === "元" && Math.abs(value) >= 10000 ? `${(value / 10000).toFixed(0)}万`
    : `${new Intl.NumberFormat("zh-CN", {maximumFractionDigits: 2}).format(value)}${unit}`;
const identity = (group: SelectionWorkspaceGroup, item: TradingCard) => JSON.stringify([group.key, group.contextKey, item.origin, item.symbol, item.selectionEntry?.selected_at, item.selectionEntry?.source_version]);

function cellFor(item: TradingCard, column: Column): Cell {
  if (item.origin === "picks") {
    const score = selectionScoreItems(item)?.find(score => score.key === column.key);
    const measured = !!score && score.value !== null && ["success", "valid_empty", "partial"].includes(score.state);
    return {value: score?.value ?? null, measured, state: score?.state ?? "unknown", unit: "分",
      label: score?.state === "missing" || score?.state === "error" ? "缺项" : !measured ? "待核" : score?.state === "partial" ? "部分" : score?.state === "valid_empty" ? "有效空集" : "原规则分",
      basis: score?.basis ?? "原记录未保存此分项依据，不补算分数。"};
  }
  const metric = item.selectionMetrics?.items.find(metric => metric.key === column.key);
  return {value: metric?.value ?? null, measured: metric?.state === "ready" && metric.value !== null, state: metric?.state ?? "unknown", unit: metric?.unit ?? column.unit,
    label: metric?.state === "ready" && metric.value !== null ? "原测量" : metric?.state === "missing" ? "缺项" : "待核",
    basis: metric?.basis || "原记录未保存本项测量依据，不以其他时点补填。"};
}

/** One selected owner reads saved evidence; the matrix itself makes no requests. */
export function SelectionWorkspace({groups, dailyEvidence, onShowDailyEvidence, positionLabels = {}}: {
  groups: SelectionWorkspaceGroup[];
  dailyEvidence?: Map<string, DailySelectionEvidence>;
  onShowDailyEvidence?: (item: DailySelectionEvidence["item"]) => void;
  positionLabels?: Record<string, "sim" | "real">;
}) {
  const available = groups.flatMap(group => group.items.filter(item => !item.referenceOnly).map(item => ({group, item, id: identity(group, item)})));
  const [chosen, setChosen] = useState<{id: string; column: string} | null>(null);
  const stageRef = useRef<HTMLElement>(null);
  const active = available.find(entry => entry.id === chosen?.id) ?? available[0];
  if (active && (!chosen || chosen.id !== active.id)) setChosen({id: active.id, column: "reason"});
  const columns = active?.item.origin === "picks" ? SCORES : METRICS;
  const column = columns.find(column => column.key === chosen?.column) ?? null;
  const cell = active && column ? cellFor(active.item, column) : null;
  const selectedMetric = active?.item.selectionMetrics?.items.find(item => item.key === column?.key);
  const choose = (group: SelectionWorkspaceGroup, item: TradingCard, field = "reason") => setChosen({id: identity(group, item), column: field});
  const peers = active && column ? active.group.items.filter(item => !item.referenceOnly).map(item => ({item, cell: cellFor(item, column)})) : [];
  const measured = peers.filter(value => value.cell.measured).map(value => value.cell.value!);
  const min = active?.item.origin === "picks" ? 0 : Math.min(0, ...measured);
  const max = active?.item.origin === "picks" ? 100 : Math.max(0, ...measured);
  const reading = active ? selectionReading(active.item) : null;
  return <section className={styles.workspace} aria-label="入选依据对照工作区">
    <div className={styles.matrixPane}>
      <header className={styles.intro}><h2>入选依据对照</h2><p>点选原值，同时核对股票与依据。</p></header>
      {active && <div className={styles.currentChoice} role="status" aria-label="当前核对反馈" aria-live="polite"><span>当前核对 <strong>{active.item.name || active.item.symbol}</strong> · {column?.label || "原选择"}{cell ? ` · ${cell.measured ? numberText(cell.value!, cell.unit) : cell.label}` : ""}</span><button type="button" aria-label={`定位${active.item.name || active.item.symbol}的所选依据`} onClick={() => stageRef.current?.scrollIntoView({block: "start", behavior: typeof window.matchMedia === "function" && !window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "smooth" : "instant"})}>看依据 ↓</button></div>}
      <div className={`${styles.matrices} data-scroll`} tabIndex={0} role="region" aria-label="多股票原分项对照矩阵">
        {groups.map(group => {
          const items = group.items.filter(item => !item.referenceOnly);
          const fields = group.key === "picks" ? SCORES : METRICS;
          return <section key={group.key} id={group.key === "picks" ? "sec-daily" : "sec-candidates"} className={styles.group}>
            <header className={styles.groupHeading}><div><h3>{group.title}</h3><span>{group.description}</span></div><strong>{group.readState === "pending" ? "读取中" : group.readState === "unavailable" ? "未确认" : <>{group.total ?? items.length} 只{group.total !== undefined && group.total > items.length && <small>{items.length} 行对照 · 同股不重复</small>}</>}</strong></header>
            {group.readError && <p role="alert" className={styles.warning}>上次读取结果 · 当前行情与参与条件待核。</p>}
            {!items.length ? <p className={styles.empty}>{group.readState === "pending" ? "正在读取这组原名单…" : group.readState === "unavailable" ? "原名单读取失败，不能确认是否有入选项。" : group.total ? "本组入选已在盘中候选展示；每日原依据随所选股核对。" : "本组没有可展示的入选记录。参考集合不进入这张矩阵。"}</p> : <table className={styles.matrix} aria-label={`${group.title}原值对照`}>
              <thead><tr><th scope="col" className={styles.stockColumn}>股票 / 原名单顺序</th>{fields.map(field => <th scope="col" key={field.key} data-column-active={active?.group.key === group.key && column?.key === field.key || undefined}>{field.label}<small>{group.key === "picks" ? "原规则分 / 100" : field.unit}</small></th>)}</tr></thead>
              <tbody>{items.map((item, index) => <tr key={identity(group, item)} data-selected={active?.id === identity(group, item) || undefined}>
                <th scope="row"><button type="button" onClick={() => choose(group, item)} aria-pressed={active?.id === identity(group, item)} aria-label={`核对${item.name || item.symbol}入选依据`} className={styles.stockChoice}><span className={styles.order}>{index + 1}</span><span><strong>{item.name || item.symbol}</strong><small>{item.symbol} · {item.theme || "题材未记录"}</small></span></button></th>
                {fields.map(field => {
                  const value = cellFor(item, field);
                  const selected = active?.id === identity(group, item) && column?.key === field.key;
                  return <td key={field.key} data-column-active={active?.group.key === group.key && column?.key === field.key || undefined}><button type="button" className={styles.valueChoice} data-selected={selected || undefined} data-state={value.measured ? value.state : "unknown"} aria-pressed={selected} aria-label={`${item.name || item.symbol}，${field.label}，${value.measured ? numberText(value.value!, value.unit) : value.label}`} onClick={() => choose(group, item, field.key)}>
                    {group.key === "picks" && value.measured && <span aria-hidden="true" className={styles.scoreWash} style={{opacity: .03 + value.value! / 100 * .18}}/>}
                    <strong>{value.measured ? numberText(value.value!, value.unit) : value.label}</strong><small>{value.measured ? value.label === "原测量" ? "" : value.label : value.value !== null ? `原存 ${value.value}` : "未记录原值"}</small>
                  </button></td>;
                })}
              </tr>)}</tbody>
            </table>}
          </section>;
        })}
      </div>
      <p className={styles.matrixNote}>每日分数与盘中测量分开核对；缺项不按零计，颜色不是上涨概率。</p>
    </div>
    {active && reading ? <section ref={stageRef} className={`${styles.stage} data-scroll`} aria-label="所选股原地证据舞台">
      <header className={styles.stageHeading}><h2>{active.item.name || active.item.symbol}<small>{active.item.symbol}</small></h2><StockLink symbol={active.item.symbol} preferModal>查看行情 ↗</StockLink></header>
      <div className={styles.identityLine}><span title={active.item.tradability?.basis}>{active.group.readError ? "上次名单 · 当前待核" : tradabilityLabel(active.item.tradability)}</span>{positionLabels[active.item.symbol] && <span>{positionLabels[active.item.symbol] === "real" ? "有手工持仓记录" : "有模拟持仓记录"}</span>}<span>{active.item.origin === "picks" ? "每日持久记录" : active.item.tier !== null ? `T${active.item.tier} 跟踪档` : "盘中动态记录"}</span></div>
      <section className={styles.reason} aria-label="原选择简述"><p>{reading.why}</p><small><strong>关键限制</strong> {reading.limit}</small></section>
      <SelectionPriceComparison item={active.item} readError={active.group.readError}/>
      {column && cell ? <section className={styles.fieldEvidence} aria-label="所选分项原依据">
        <header><div><span>{column.label}</span><strong>{cell.measured ? numberText(cell.value!, cell.unit) : cell.label}</strong></div><small>{cell.label}{!cell.measured && cell.value !== null ? ` · 原存 ${cell.value} 为待核或计算占位` : ""}</small></header>
        <p tabIndex={0} className={styles.basis}>{cell.basis}</p>
        {active.item.origin === "intraday" && <div className={styles.thresholds} aria-label="本项原门槛">{selectedMetric?.thresholds.map(threshold => <span key={threshold.key}>{threshold.label}<strong>{threshold.operator === ">=" ? "≥" : "<"}{numberText(threshold.value, cell.unit)}</strong></span>)}</div>}
        <details className={styles.fieldSource}><summary>核对本项来源与时刻</summary>{active.item.origin === "intraday" ? <dl><div><dt>来源</dt><dd>{sourceLabel(selectedMetric?.source)}</dd></div><div><dt>源时刻</dt><dd>{selectedMetric?.as_of ? dateTimeTextBJ(selectedMetric.as_of, {seconds: true}) : "未提供"}</dd></div><div><dt>接收时刻</dt><dd>{selectedMetric?.received_at ? dateTimeTextBJ(selectedMetric.received_at, {seconds: true}) : "未提供"}</dd></div><div><dt>快照版本</dt><dd>{active.item.selectionMetrics?.snapshot_version || "未记录"}</dd></div></dl> : <p>原读取状态：{cell.state} · 依据时刻：{active.item.dimensionEvidence?.[column.key]?.source_as_of || "未记录"} · 入选版本：{active.item.selectionEntry?.source_version || "未记录"}</p>}</details>
        <div className={styles.dotPlot} aria-label={`${column.label}同组原值对照`}>{measured.length ? <div className={styles.dotScale}><span>{numberText(min, cell.unit)}</span><span>{numberText(max, cell.unit)}</span></div> : <p className={styles.matrixNote}>没有可核对的原值，不绘制刻度。</p>}{peers.map(peer => <button type="button" className={styles.dotRow} key={identity(active.group, peer.item)} data-measured={peer.cell.measured} aria-pressed={active.id === identity(active.group, peer.item)} aria-label={`改看${peer.item.name || peer.item.symbol}的${column.label}`} onClick={() => choose(active.group, peer.item, column.key)}><span>{peer.item.name || peer.item.symbol}</span><span className={styles.dotLane}>{peer.cell.measured && <i style={{left: `${max === min ? 50 : (peer.cell.value! - min) / (max - min) * 100}%`}}/>}</span><strong>{peer.cell.measured ? numberText(peer.cell.value!, peer.cell.unit) : peer.cell.label}</strong></button>)}</div>
      </section> : null}
      <div className={styles.actions}><CardEntryRow symbol={active.item.symbol} name={active.item.name} theme={active.item.theme}/>{active.item.stopLoss && <span title={active.item.stopLoss.basis}>止损参考 ¥{fmt(active.item.stopLoss.price)}（-{active.item.stopLoss.pct}%）</span>}{dailyEvidence?.has(active.item.symbol) && onShowDailyEvidence && <button type="button" onClick={() => onShowDailyEvidence(dailyEvidence.get(active.item.symbol)!.item)}>每日原依据</button>}</div>
      <div key={active.id} className={styles.owner} data-evidence-owner={active.item.symbol}><PickSelectionEvidence item={active.item} presentation="workspace" dailyEvidence={dailyEvidence?.get(active.item.symbol)} onShowDailyEvidence={onShowDailyEvidence}/></div>
      <p className={styles.matrixNote}>入选、观察变化与成交分别核对。不构成买卖建议。</p>
    </section> : <p className={styles.empty}>没有真实入选项，暂不读取个股研究。</p>}
  </section>;
}
