"use client";

import {useMemo, useRef, useState, type CSSProperties} from "react";
import {useIncremental} from "@/hooks/use-incremental";
import {useResource} from "@/hooks/use-resource";
import {getIndexMembers} from "@/lib/api";
import {dateTimeTextBJ, pctColor, pctText, sourceLabel} from "@/lib/format";
import {ControlHint} from "@/components/ui/control-hint";
import {IncrementalSentinel} from "@/components/ui/incremental-sentinel";
import styles from "./index-members-panel.module.css";

const finitePct = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) ? value : null;
const clockText = (value: string | null | undefined) => value ? dateTimeTextBJ(value, {seconds: true}) : "未提供";
function Icon({kind}: {kind: "search" | "info" | "up" | "down"}) {
  return <svg aria-hidden="true" width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    {kind === "search" ? <><circle cx="8.5" cy="8.5" r="5.5"/><path d="m13 13 4 4"/></>
      : kind === "info" ? <><circle cx="10" cy="10" r="7"/><path d="M10 9v5m0-8h.01"/></>
        : <path d={kind === "up" ? "M10 16V4m-4 4 4-4 4 4" : "M10 4v12m-4-4 4 4 4-4"}/>}
  </svg>;
}

/** Read the server's existing memory snapshot; never subscribe the member universe. */
export function IndexMembersPanel({symbol, active, onSelect}: {symbol: string; active: boolean; onSelect: (symbol: string) => void}) {
  const resource = useResource(() => getIndexMembers(symbol), {key: symbol, enabled: active, intervalMs: 60_000, marketHours: false});
  const [query, setQuery] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const [direction, setDirection] = useState<"desc" | "asc">("desc");
  const data = resource.data;
  const rows = useMemo(() => {
    const term = query.trim().toLowerCase();
    return (data?.items ?? []).map((row, order) => ({row, order})).sort((a, b) => {
      const left = finitePct(a.row.change_pct), right = finitePct(b.row.change_pct);
      if (left === null) return right === null ? a.order - b.order : 1;
      if (right === null) return -1;
      return (direction === "desc" ? right - left : left - right) || a.order - b.order;
    }).map(({row}) => row).filter(row => !term || `${row.name}${row.symbol}`.toLowerCase().includes(term));
  }, [data, query, direction]);
  const {shown, visible, sentinelRef} = useIncremental(rows, {pageSize: 30, step: 30, active, resetKey: JSON.stringify([symbol, query, direction])});
  const known = data?.items.filter(row => finitePct(row.change_pct) !== null).length ?? 0;
  const total = data?.items.length ?? 0;
  const maxAbs = Math.max(0, ...(data?.items.map(row => Math.abs(finitePct(row.change_pct) ?? 0)) ?? []));
  const snapshot = data?.quote_snapshot;
  const partial = known < total;
  const quoteState = resource.error ? "上次列表" : snapshot?.quality === "high" ? ""
    : snapshot?.quality === "stale" ? "旧观察" : snapshot?.quality === "medium" ? "延迟观察"
      : snapshot?.quality === "low" || snapshot?.quality === "invalid" ? "质量异常" : "行情未知";
  const observation = snapshot?.source_clock ? `源 ${clockText(snapshot.source_clock).slice(-8)}`
    : snapshot?.observed_at ? `观察 ${clockText(snapshot.observed_at).slice(-8)}` : "行情时间未知";
  const evidence = `涨跌幅已知 ${known} / ${total}。全部返回成员排序，未知置后；下拉继续显示，搜索和排序后从首批开始。`
    + `已存行情观察，非全体实时保证；${snapshot?.observed_at ? `观察于 ${clockText(snapshot.observed_at)} · ` : ""}`
    + `${snapshot?.source_clock ? `源时刻 ${clockText(snapshot.source_clock)}` : "源时刻未提供"} · 质量 ${snapshot?.quality ?? "unknown"}。`
    + `成员关系：同花顺 · ${data ? dateTimeTextBJ(data.snapshot_at) : "未读取"}。`
    + `横线表示相对零点的真实涨跌幅，共用全返回成员±${pctText(maxAbs).replace(/^\+/, "")}尺度，不是评分。`;
  return <section className={`${styles.panel} index-members`} aria-label="指数成分股">
    <header className={styles.toolbar}>
      <div className={styles.heading}>
        <strong className={styles.name}>{data?.name ?? "指数成分"}</strong>
        <span className={styles.count}>{data ? `${total} 只` : resource.error ? "暂不可用" : "读取中"}</span>
        {data && <ControlHint content={evidence} inactive={!active}><button type="button" className={styles.coverage} data-partial={partial || undefined} aria-label="查看数据来源与排序口径">
          <span>已知 {known}/{total}</span><Icon kind="info"/>
        </button></ControlHint>}
      </div>
      <div className={styles.controls}>
        <label className={styles.search}><Icon kind="search"/><input aria-label="查找指数成分股" type="search" value={query} placeholder="名称 / 代码" onChange={event => {setQuery(event.target.value); if (listRef.current) listRef.current.scrollTop = 0;}}/></label>
        <div role="group" aria-label="成分股排序" className={styles.sort}>
          {(["desc", "asc"] as const).map(value => <button key={value} type="button" aria-label={`涨跌幅${value === "desc" ? "降序" : "升序"}`} aria-pressed={direction === value} onClick={() => {setDirection(value); if (listRef.current) listRef.current.scrollTop = 0;}}>
            <Icon kind={value === "desc" ? "down" : "up"}/><span>{value === "desc" ? "降" : "升"}</span>
          </button>)}
        </div>
      </div>
      {resource.error != null && <div role="status" className={styles.error}><span>成分股读取失败{data ? "，保留上次列表" : ""}</span><button type="button" onClick={resource.refresh} disabled={resource.pending}>重试</button></div>}
    </header>
    <div ref={listRef} className={`${styles.list} data-scroll`} aria-label="指数成分股列表" tabIndex={0}>
      {!data && !resource.error && <p className={styles.empty}>正在读取成分股…</p>}
      {data && !rows.length && <p className={styles.empty}>{query.trim() ? "没有匹配的成分股" : "暂无成分股"}</p>}
      {shown.map((row, order) => {
        const pct = finitePct(row.change_pct);
        const width = pct === null || maxAbs === 0 ? 0 : Math.abs(pct) / maxAbs * 50;
        const range = {"--change-width": `${width}%`, "--change-start": `${pct !== null && pct < 0 ? 50 - width : 50}%`} as CSSProperties;
        const quoteEvidence = `${sourceLabel(row.quote_source)} · 源时刻 ${clockText(row.source_clock)} · 质量 ${row.quote_quality ?? "unknown"}${row.quote_observed_at ? ` · 观察 ${clockText(row.quote_observed_at)}` : ""}`;
        return <button key={row.symbol} type="button" onClick={() => onSelect(row.symbol)} className={styles.row} title={`查看${row.name}盘口`} aria-description={quoteEvidence}>
          <span className={styles.rank}>{order + 1}</span>
          <span className={styles.identity}><span className={styles.memberName}>{row.name}</span><span className={styles.symbol}>{row.symbol}</span></span>
          <span className={`${styles.change} ${pctColor(pct)}`} title={quoteEvidence}>
            <span>{pctText(pct)}</span>
            {pct !== null && <span aria-hidden="true" className={styles.scale} style={range}><i/><b/></span>}
          </span>
        </button>;
      })}
      <IncrementalSentinel sentinelRef={sentinelRef} visible={visible} total={rows.length} unit="只成分股" testId="index-members-sentinel"/>
    </div>
    {data && <footer className={styles.footer}>
      <span className={styles.source} title={evidence} data-warning={!!quoteState || undefined}>{quoteState && `${quoteState} · `}{sourceLabel(snapshot?.source)} · {observation}</span>
      <span className={styles.loaded} aria-label="已显示成分股数量" aria-live="polite">已显示 {shown.length} / {rows.length}</span>
    </footer>}
  </section>;
}
