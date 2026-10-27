"use client";

import { FilterMenu } from "@/components/ui/filter-menu";

import { useExitPresence } from "@/hooks/use-exit-presence";
import { ModalShell } from "@/components/ui/modal-shell";
import { useResource } from "@/hooks/use-polling-fetch";
import { Panel } from "@/components/panel";
import { StockLink, useStockRowNav } from "@/components/stock-link";
import { Skeleton } from "@/components/ui/loading";
import { IncrementalSentinel } from "@/components/ui/incremental-sentinel";
import { useIncremental } from "@/hooks/use-incremental";
import { dateTimeTextBJ, fmt, pctColor, pctText } from "@/lib/format";
import { CardHead, CardShell } from "@/components/picks/card-shell";
import { MasonryColumns } from "@/components/masonry-columns";
import { useMemo, useRef, useState } from "react";
import { useSurfaceScope } from "@/components/inspection/surface-scope";
import {
  getBoardFlowMembers,
  getBoardFlowMinute,
  getBoardFundFlow,
  type BoardFlowKind,
  type BoardFlowRange,
  type BoardFlowRow,
  type BoardFundFlowPayload,
  type BoardFlowMembersPayload,
} from "@/lib/api";
import { FlowIntradayChart, signedFmt } from "@/components/market/flow-intraday-chart";
import { ReadingRow, readingRowClasses } from "@/components/ui/reading-row";

/**
 * 板块资金流（L2 主视图 + 下钻抽屉，docs/summary/architecture-design.md §2）。
 *
 * 性能契约：后端一次翻页返回全量板块行，本组件排序/筛选纯内存（切换不触发请求）；
 * 轮询统一 30s；下钻抽屉数据独立 60s 轮询、关闭即停（usePollingFetch 卸载即清）。
 *
 * 口径：净流入=主力净额（东财官方板块口径，亿元）；主力占成交=净额/成交额×100（f184）；
 * 连续流入列 null=未沉淀（区别于 0=今日净流出）；排名Δ 正=上升（vs 昨日落盘榜位）。
 * 颜色纪律：净流入红 / 净流出绿（A 股惯例）。
 */

const KIND_CHIPS: { key: BoardFlowKind; label: string }[] = [
  { key: "concept", label: "概念" },
  { key: "industry", label: "行业" },
];

const RANGE_CHIPS: { key: BoardFlowRange; label: string }[] = [
  { key: "website", label: "官网全集" },
  { key: "intraday", label: "今日" },
  { key: "5d", label: "5日" },
  { key: "10d", label: "10日" },
  { key: "20d", label: "20日存档" },
  { key: "latest_saved", label: "最近单日" },
  { key: "changes", label: "异动统计" },
];

type SortKey = "main" | "chg" | "ratio" | "streak";

const SORT_CHIPS: { key: SortKey; label: string; title: string }[] = [
  { key: "main", label: "净流入", title: "按当前区间主力净额降序（默认）" },
  { key: "chg", label: "涨跌幅", title: "按板块涨跌幅降序" },
  { key: "ratio", label: "主力占成交", title: "按主力净额/成交额占比降序（仅今日口径）" },
  { key: "streak", label: "连续流入", title: "按连续净流入天数降序（仅沉淀板块可判）" },
];

const CHIP = "ui-filter flow-chip rounded px-1.5 py-0.5 text-[10px] transition-colors";
const CHIP_ON = "ui-filter-selected";
const CHIP_OFF = "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-400";

/** 瀑布流分页尺寸（滚动增量逻辑统一在 hooks/use-incremental，此处只留规格）。 */
const PAGE_SIZE = 30;
const PAGE_STEP = 30;
function rangeVal(r: BoardFlowRow, range: BoardFlowRange): number | null {
  if (range === "5d") return r.main_net_5d_yi;
  if (range === "10d") return r.main_net_10d_yi;
  if (range === "20d") return r.main_net_20d_yi ?? null;
  return r.main_net_yi;
}

/** 板块日度主力净额 diverging 柱（近 20 bar，HTML 条非 SVG——定高纪律）。 */
export function BoardDailyBars({ bars }: { bars: { date: string; main_yi: number | null }[] }) {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const last = bars.slice(-20);
  if (last.length === 0) return null;
  const selected = last.find(bar => bar.date === selectedDate) ?? last[last.length - 1];
  const maxAbs = Math.max(1, ...last.map((b) => Math.abs(b.main_yi ?? 0)));
  const tickCount = Math.min(5, last.length);
  const tickSlots = new Set(Array.from({length: tickCount}, (_, i) => Math.round(i * (last.length - 1) / Math.max(1, tickCount - 1))));
  return (
    <div className="fund-history-chart" data-testid="board-daily-bars">
      <p className="fund-history-detail">{selected.date} · 主力 {signedFmt(selected.main_yi)}</p>
      <div className="fund-history-plot">
      {last.map((b, index) => {
        const v = b.main_yi;
        const h = v == null || v === 0 ? 0 : Math.max(2, (Math.abs(v) / maxAbs) * 46);
        return (
          <button type="button" key={b.date} className="fund-history-column" aria-label={`${b.date}｜主力 ${signedFmt(v)}`} aria-pressed={selected.date === b.date} onMouseEnter={() => setSelectedDate(b.date)} onFocus={() => setSelectedDate(b.date)} onClick={() => setSelectedDate(b.date)}
            title={`${b.date}｜主力 ${signedFmt(v)}`}>
            <span className="fund-history-axis" />
            {v != null && v !== 0 && <i className={v > 0 ? "fund-history-bar bg-up/80" : "fund-history-bar bg-down/80"} data-sign={v > 0 ? "positive" : "negative"} style={{height: `${h}%`}} />}
            <span className="fund-history-tick">{tickSlots.has(index) ? b.date.slice(5) : ""}</span>
          </button>
        );
      })}
      </div>
    </div>
  );
}

/** 板块下钻抽屉：分钟五档累计（延迟口径）+ 日度主力净额柱 + 成员个股资金排行 Top20。 */
function LegacyBoardFlowDrawer({ row, onClose, open, asOf }: { row: BoardFlowRow; onClose: () => void; open: boolean; asOf?: string }) {
  const stockNav = useStockRowNav();
  const minuteResult = useResource(() => getBoardFlowMinute(row.board_code), { key: row.board_code, intervalMs: 60_000, enabled: open });
  const memberResult = useResource(() => getBoardFlowMembers(row.board_code), { key: row.board_code, intervalMs: 60_000, enabled: open });
  const minute = minuteResult.data;
  const members = memberResult.data;
  const pending = minuteResult.pending || memberResult.pending;

  const items = minute?.items ?? [];
  const bars = minute?.daily_bars ?? [];
  const memRows = members?.rows ?? [];

  return (
    <ModalShell open={open} presentation="drawer" onClose={onClose} label={`板块资金 ${row.name}`} testid="board-flow-drawer"
      header={<div><p className="text-sm font-semibold">{row.name}</p><p className="text-xs text-zinc-600 dark:text-zinc-400">{row.board_code} · {asOf ? `存档榜位 #${row.rank}（截至 ${asOf}）` : `榜位 #${row.rank}`} · 东财 f62口径，非成分股相加</p></div>}>
        {!!(minuteResult.error || memberResult.error) && <p role="alert" className="text-xs text-amber-800 dark:text-amber-300">部分资金来源读取失败；保留值仅作上次结果参考。<button onClick={() => { if (minuteResult.error) minuteResult.refresh(); if (memberResult.error) memberResult.refresh(); }}>重试</button></p>}
        <div className="space-y-4 px-4 py-3">
          {/* ① 板块分钟五档累计（延迟 ~15min 口径） */}
          <section>
            <p className="mb-1 text-[10px] text-zinc-600 dark:text-zinc-400">
              分钟资金累计（五档；本次独立读取）· 东财延迟约 15 分钟口径 · 悬停看明细
              {minute?.degraded.length ? <span className="ml-1 text-amber-800 dark:text-amber-500">⚠ {minute.degraded.join("；")}</span> : null}
            </p>
            {pending && items.length === 0 ? (
              <Skeleton className="h-40 w-full" />
            ) : items.length > 0 ? (
              <FlowIntradayChart items={items} />
            ) : (
              <p className="py-6 text-center text-xs text-zinc-600 dark:text-zinc-400">
                分钟资金流暂不可用{minute?.reason ? `（${minute.reason}）` : ""}，60s 后自动重试。
              </p>
            )}
          </section>

          {/* ② 日度主力净额柱（落盘沉淀板块才有，零外呼） */}
          <section>
            <p className="mb-1 text-[10px] text-zinc-600 dark:text-zinc-400">日度主力净额（近 {Math.min(20, bars.length) || 20} 条存档；上红流入 / 下绿流出）</p>
            {bars.length > 0 ? (
              <BoardDailyBars bars={bars} />
            ) : (
              <p className="py-4 text-center text-xs text-zinc-600 dark:text-zinc-400">该板块日度历史尚未沉淀（每日收盘后自动累积 Top 板块）</p>
            )}
          </section>

          {/* ③ 成员个股资金排行 Top20 */}
          <section>
            <p className="mb-1 text-[10px] text-zinc-600 dark:text-zinc-400">
              成员个股资金排行 Top20（本次独立读取，东财成员口径）
              {members?.degraded.length ? <span className="ml-1 text-amber-800 dark:text-amber-500">⚠ {members.degraded.join("；")}</span> : null}
            </p>
            {pending && memRows.length === 0 ? (
              <div className="space-y-1.5">
                {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-6 w-full" />)}
              </div>
            ) : memRows.length > 0 ? (
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-zinc-200 text-left text-[10px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-800">
                    <th className="py-1 font-normal">代码</th>
                    <th className="py-1 font-normal">名称</th>
                    <th className="py-1 text-right font-normal">涨跌幅</th>
                    <th className="py-1 text-right font-normal">主力净额</th>
                    <th className="py-1 text-right font-normal">占成交</th>
                  </tr>
                </thead>
                <tbody>
                  {memRows.map((m) => (
                    <tr key={m.symbol} onClick={stockNav(m.symbol)} className={`${readingRowClasses.row} border-b border-zinc-100 last:border-0 dark:border-zinc-800/60`}>
                      <td className="py-1 font-mono text-zinc-600 dark:text-zinc-400">
                        <StockLink symbol={m.symbol} className="font-mono text-zinc-600 dark:text-zinc-400">{m.symbol}</StockLink>
                      </td>
                      <td className="max-w-[8em] truncate py-1">
                        <StockLink symbol={m.symbol}>{m.name}</StockLink>
                      </td>
                      <td className={`py-1 text-right font-mono ${pctColor(m.change_pct)}`}>{pctText(m.change_pct)}</td>
                      <td className={`py-1 text-right font-mono ${m.main_net_yi == null ? "text-zinc-600 dark:text-zinc-400" : m.main_net_yi >= 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down"}`}>{signedFmt(m.main_net_yi)}</td>
                      <td className="py-1 text-right font-mono text-zinc-600 dark:text-zinc-400">{m.main_net_ratio != null ? `${m.main_net_ratio.toFixed(2)}%` : "--"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="py-4 text-center text-xs text-zinc-600 dark:text-zinc-400">成员排行暂不可用{members?.reason ? `（${members.reason}）` : ""}</p>
            )}
          </section>
        </div>
    </ModalShell>
  );
}

class BoardIdentityError extends Error {}
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const named = (value: unknown): value is string => typeof value === "string" && !!value.trim();
const sourceClock = (value: unknown): value is string => named(value) && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) && Number.isFinite(Date.parse(value));
const clockText = (value: string | null | undefined) => sourceClock(value) ? `${dateTimeTextBJ(value)}（北京）` : "未提供";

function checkedFlow(data: BoardFundFlowPayload, kind: BoardFlowKind, range: BoardFlowRange) {
  if (!data || data.kind!==kind || data.range!==range || typeof data.available!=="boolean" || !Array.isArray(data.rows)
    || !Array.isArray(data.degraded) || data.degraded.some(message=>typeof message!=="string")
    || data.rows.some(row=>!row || !/^BK\d{3,4}$/.test(row.board_code) || !named(row.name) || row.kind!==kind)
    || new Set(data.rows.map(row=>row.board_code)).size!==data.rows.length) throw new BoardIdentityError("板块返回身份或结构不符");
  if (range==="website" && (data.source!=="eastmoney_official_funds" || data.available && (
    data.source_as_of!==null || data.as_of!==null || data.quality!=="stale" || data.observation_state!=="clock_unknown"
    || data.coverage_state!=="source_complete" || !sourceClock(data.received_at)
    || !Number.isSafeInteger(data.source_total) || data.source_total!==data.rows.length || data.source_returned!==data.source_total || data.directory_total!==data.source_total
    || !data.coverage || typeof data.coverage!=="object" || data.coverage.complete!==true || data.coverage.total!==data.source_total || data.coverage.returned!==data.source_total
    || data.rows.some(row=>[row.main_net_yi,row.change_pct].some(value=>value!==null && !finite(value)))
  ))) throw new BoardIdentityError("官网板块来源或完整覆盖不符");
  return data;
}

function checkedWebsiteMembers(data: BoardFlowMembersPayload, code: string, kind: BoardFlowKind) {
  if (!data || data.source!=="eastmoney_official_funds" || data.board_code!==code || data.kind!==kind
    || typeof data.available!=="boolean" || data.source_as_of!==null || data.as_of!==null
    || !Array.isArray(data.rows) || !Array.isArray(data.degraded) || data.degraded.some(message=>typeof message!=="string")
    || data.rows.some(row=>!row || !/^\d{6}$/.test(row.symbol) || !named(row.name)
      || [row.price,row.change_pct,row.main_net_yi,row.main_net_ratio,row.super_net_yi,row.big_net_yi].some(value=>value!==null && !finite(value))
      || !(row.quote_source_as_of===null || sourceClock(row.quote_source_as_of)))
    || new Set(data.rows.map(row=>row.symbol)).size!==data.rows.length
    || !data.coverage || !Number.isSafeInteger(data.coverage.returned) || data.coverage.returned!==data.rows.length || typeof data.coverage.complete!=="boolean"
    || data.available && (!named(data.name) || data.coverage.complete!==true || data.coverage.total!==data.rows.length
      || data.source_total!==data.rows.length || data.source_returned!==data.rows.length || data.coverage_state!=="source_complete"
      || !Number.isSafeInteger(data.directory_total) || (data.directory_total as number)<1 || !sourceClock(data.received_at)
      || data.quality!=="stale" || data.observation_state!=="clock_unknown")
    || !(data.quote_source_as_of===null || sourceClock(data.quote_source_as_of))) throw new BoardIdentityError("官网成员身份或完整覆盖不符");
  const latest = data.rows.flatMap(row=>sourceClock(row.quote_source_as_of)?[row.quote_source_as_of]:[]).sort((a,b)=>Date.parse(b)-Date.parse(a))[0]??null;
  if (data.quote_source_as_of!==latest) throw new BoardIdentityError("官网成员报价钟不一致");
  return data;
}

/** Same market drawer and scroll owner; the website view reads complete source members. */
function WebsiteBoardFlowDrawer({row,kind,open,onClose}:{row:BoardFlowRow;kind:BoardFlowKind;open:boolean;onClose:()=>void}) {
  const stockNav = useStockRowNav();
  const scope = JSON.stringify(["website",kind,row.board_code,row.name]);
  const freshRead = useRef<string|null>(null);
  const resource = useResource(async signal=>{
    const fresh = freshRead.current===scope; freshRead.current=null;
    return checkedWebsiteMembers(await getBoardFlowMembers(row.board_code,{signal,view:"website",kind,...(fresh?{fresh:true}:{})}),row.board_code,kind);
  },{key:scope,intervalMs:null,enabled:open,abortable:true});
  const data = resource.error instanceof BoardIdentityError ? undefined : resource.data;
  const [filters,setFilters] = useState({scope,query:"",ascending:false});
  if (filters.scope!==scope) setFilters({scope,query:"",ascending:false});
  const members = useMemo(()=>data?.available ? data.rows : [],[data]);
  const shownMembers = useMemo(()=>{
    const query = filters.query.trim().toLocaleLowerCase();
    return members.filter(member=>!query || `${member.symbol} ${member.name}`.toLocaleLowerCase().includes(query)).sort((a,b)=>{
      if (a.change_pct===null) return b.change_pct===null ? a.symbol.localeCompare(b.symbol):1;
      if (b.change_pct===null) return -1;
      return (filters.ascending?1:-1)*(a.change_pct-b.change_pct) || a.symbol.localeCompare(b.symbol);
    });
  },[members,filters.query,filters.ascending]);
  const incremental = useIncremental(shownMembers,{resetKey:JSON.stringify([scope,filters.query,filters.ascending]),active:open});
  const refresh = ()=>{freshRead.current=scope;resource.refresh();};
  const missing = members.filter(member=>member.price===null || member.change_pct===null).length;
  return <ModalShell open={open} presentation="drawer" onClose={onClose} label={`板块资金 ${row.name}`} testid="board-flow-drawer"
    header={<div><p className="text-sm font-semibold">{row.name}</p><p className="text-xs text-zinc-600 dark:text-zinc-400">{row.board_code} · 东财官网正式{kind==="industry"?"行业":"概念"}</p></div>}>
    <div className="space-y-3 px-4 py-3">
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-zinc-600 dark:text-zinc-400" data-testid="website-member-facts">
        <span>完整成员</span><span>{data?.available?`${resource.error?"上次覆盖":"覆盖"} ${data.coverage!.returned}/${data.coverage!.total} · 已读全`:"覆盖待核"}</span>
        {data?.available && data.name!==row.name && <span className="col-span-2" role="status">成员来源名称：{data.name}；代码保持 {row.board_code}。</span>}
        <span className="text-amber-800 dark:text-amber-300">资金源钟未提供</span><span>接收 {clockText(data?.received_at)}</span>
        <span className="col-span-2">最新报价钟 {clockText(data?.quote_source_as_of)} · 各股时点分别保留</span>
        {data?.available && <span className="col-span-2">缺报价 {missing}/{members.length}只 · 缺数保留成员</span>}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 text-xs">查找成员<input type="search" value={filters.query} placeholder="代码 / 名称" onChange={event=>setFilters(current=>({...current,query:event.target.value}))}
          className="mt-1 min-h-8 w-full min-w-0 rounded border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"/></label>
        <button className="quiet-action" aria-label={`涨跌幅${filters.ascending?"升序":"降序"}，点击切换`} onClick={()=>setFilters(current=>({...current,ascending:!current.ascending}))}>涨跌幅{filters.ascending?"从低到高":"从高到低"}</button>
        <button className="quiet-action" disabled={resource.pending} onClick={refresh}>{resource.pending?"读取中…":"重读成员"}</button>
      </div>
      {resource.error!=null && <p role="alert" className="text-xs text-amber-800 dark:text-amber-300">{resource.error instanceof BoardIdentityError?"成员返回身份或覆盖不符，未展示该返回。":data?"成员刷新失败，保留上次读取；当前状态未核。":"成员读取失败，未确认当前成员。"}<button className="quiet-action ml-2" disabled={resource.pending} onClick={refresh}>重试成员读取</button></p>}
      {data && !data.available && <p role="alert" className="text-xs text-amber-800 dark:text-amber-300">成员来源暂不可用：{data.reason??"来源未提供原因"}<button className="quiet-action ml-2" disabled={resource.pending} onClick={refresh}>重试成员读取</button></p>}
      {resource.pending && !data && <Skeleton className="h-24 w-full"/>}
      {data?.available && <>
        {filters.query && <p className="text-[11px] text-zinc-600 dark:text-zinc-400">匹配 {shownMembers.length} / 已读 {members.length} 只</p>}
        <table className="w-full table-fixed text-xs" aria-label="完整成员资金">
          <thead><tr className="border-b border-zinc-200 text-left text-[10px] text-zinc-600 dark:border-zinc-800 dark:text-zinc-400"><th className="w-14 py-1 font-normal">代码</th><th className="py-1 font-normal">名称 / 价格</th><th className="w-14 py-1 text-right font-normal">涨跌幅</th><th className="w-16 py-1 text-right font-normal">主力净额</th><th className="w-20 py-1 text-right font-normal">报价钟</th></tr></thead>
          <tbody>{incremental.shown.map(member=><tr key={member.symbol} onClick={stockNav(member.symbol)} className={`${readingRowClasses.row} border-b border-zinc-100 dark:border-zinc-800/60`}>
            <td className="py-2 font-mono text-[11px]"><StockLink symbol={member.symbol}>{member.symbol}</StockLink></td>
            <td className="py-2"><StockLink symbol={member.symbol} className="block truncate">{member.name}</StockLink><span className="block font-mono text-[10px] text-zinc-600 dark:text-zinc-400">{fmt(member.price)}</span></td>
            <td className={`py-2 text-right font-mono tabular-nums ${pctColor(member.change_pct)}`}>{pctText(member.change_pct)}</td>
            <td className={`py-2 text-right font-mono tabular-nums ${pctColor(member.main_net_yi)}`}>{member.main_net_yi===null?"未提供":signedFmt(member.main_net_yi)}</td>
            <td className="break-words py-2 text-right font-mono text-[9px] text-zinc-600 dark:text-zinc-400">{clockText(member.quote_source_as_of)}</td>
          </tr>)}</tbody>
        </table>
        {shownMembers.length===0 && <p role="status" className="py-3 text-xs text-zinc-600 dark:text-zinc-400">{members.length?"当前搜索无匹配，可清除关键词。":"本次来源返回有效空成员集。"}</p>}
        <IncrementalSentinel sentinelRef={incremental.sentinelRef} visible={Math.min(incremental.visible,shownMembers.length)} total={shownMembers.length} unit="只成员" testId="board-members-sentinel" onLoadMore={incremental.loadMore}/>
      </>}
      <details className="text-[11px] text-zinc-600 dark:text-zinc-400"><summary className="cursor-pointer">口径与来源详情{data?.degraded.length?` · ${data.degraded.length}项记录`:""}</summary>
        <p>金额为亿元，f62来源元÷1亿。资金源日期未知；报价钟不是资金时刻或成员生效时刻。东财BK身份与同花顺TI、新浪分类分别保留。</p>
        {data?.degraded.map((message,index)=><p key={index}>{message}</p>)}
      </details>
    </div>
  </ModalShell>;
}

/** 板块卡片（瀑布流单元，视觉参照每日精选 PickCard：圆角卡 + 徽标 + mono 数值）。 */
function BoardCard({
  row,
  range,
  onClick,
}: {
  row: BoardFlowRow;
  range: BoardFlowRange;
  onClick: () => void;
}) {
  const v = rangeVal(row, range);
  const rangeLabel = range === "website" ? "官网主力净额" : range === "changes" ? "异动统计主力净额" : range === "latest_saved" ? "原日主力净额" : range === "intraday" ? "净流入" : `${range}净流入`;
  return (
    <ReadingRow data-testid={`board-row-${row.board_code}`}>
      <CardShell flow className="board-flow-card cursor-pointer transition-colors hover:border-zinc-300 dark:hover:border-zinc-700">
      <div className="board-flow-heading flex items-baseline justify-between gap-2">
        <div className="board-flow-identity flex min-w-0 items-center gap-1.5">
          <button type="button" data-reading-primary aria-label={`查看 ${row.name} 资金详情`} onClick={onClick} className={`${readingRowClasses.primary} board-flow-name text-sm font-semibold`}>{row.name}</button>
          <span className="shrink-0 font-mono text-[9px] text-zinc-600 dark:text-zinc-400">{row.board_code.replace(/^BK/, "")}</span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {row.rank_delta != null && row.rank_delta !== 0 && (
            <span
              className={`rounded px-1 py-0.5 text-[9px] font-medium ${
                row.rank_delta > 0 ? "bg-rose-500/10 text-rose-700 dark:text-rose-300" : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
              }`}
              title={`今日榜位 vs 昨日（正=上升）`}
            >
              {row.rank_delta > 0 ? `↑${row.rank_delta}` : `↓${-row.rank_delta}`}
            </span>
          )}
          {row.streak != null && row.streak >= 3 && (
            <span className="rounded bg-amber-500/10 px-1 py-0.5 text-[9px] text-amber-800 dark:text-amber-300" title="连续净流入天数">
              {row.streak}天连
            </span>
          )}
        </div>
      </div>

      <div className="board-flow-metrics mt-1.5 flex items-end justify-between gap-3">
        <div>
          <div className={`font-mono text-lg font-semibold leading-tight tabular-nums ${pctColor(row.change_pct)}`}>{pctText(row.change_pct)}</div>
          <div className="text-[9px] text-zinc-600 dark:text-zinc-400">涨跌幅</div>
        </div>
        <div className="text-right">
          <div className={`font-mono text-lg font-semibold leading-tight tabular-nums ${v == null ? "text-zinc-600 dark:text-zinc-400" : v >= 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down"}`}>
            {v == null ? "--" : signedFmt(v)}
          </div>
          <div className="text-[9px] text-zinc-600 dark:text-zinc-400">{rangeLabel}(亿)</div>
        </div>
      </div>

      <div className="mt-2 space-y-0.5 border-t border-zinc-100 pt-1.5 text-[10px] text-zinc-600 dark:text-zinc-400 dark:border-zinc-800/60">
        <div className="flex justify-between">
          <span>主力占成交</span>
          <span className="font-mono tabular-nums">{row.main_net_ratio != null ? `${row.main_net_ratio.toFixed(2)}%` : "--"}</span>
        </div>
        <div className="flex justify-between">
          <span>连续流入</span>
          <span className={`font-mono tabular-nums ${row.streak == null ? "" : row.streak >= 3 ? "font-semibold text-up-ink dark:text-up" : ""}`}>
            {row.streak == null ? "—" : `${row.streak}天`}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="shrink-0">龙头</span>
          <span className="truncate text-right" title={row.leader_symbol ? `${row.leader_name} ${row.leader_symbol}` : undefined}>
            {row.leader_name ?? "—"}
          </span>
        </div>
      </div>
      </CardShell>
    </ReadingRow>
  );
}

export function BoardFlowPanel() {
  const surface = useSurfaceScope();
  const params = surface.searchParams;
  const requestedKind = params.get("board_kind");
  const requestedRange = params.get("board_range");
  const selectedCode = params.get("board_code") ?? "";
  const kind: BoardFlowKind = requestedKind === "industry" ? "industry" : "concept";
  const range = RANGE_CHIPS.find(item=>item.key===requestedRange)?.key ?? "website";
  const paused = params.has("board_notice") || !!requestedKind && !["concept","industry"].includes(requestedKind)
    || !!requestedRange && !RANGE_CHIPS.some(item=>item.key===requestedRange) || !!selectedCode && !/^BK\d{3,4}$/.test(selectedCode);
  const website = range === "website";
  const scope = JSON.stringify([kind,range,paused]);
  const freshRead = useRef<string|null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("main");
  const [filters, setFilters] = useState<Set<string>>(new Set());
  const [query,setQuery] = useState("");
  const limited = ["website","changes","latest_saved"].includes(range);
  const preferenceScope = `${kind}/${range}`;
  const [seenPreferenceScope,setSeenPreferenceScope] = useState(preferenceScope);
  if (seenPreferenceScope!==preferenceScope) {
    setSeenPreferenceScope(preferenceScope);
    if (limited) {
      setFilters(current=>new Set([...current].filter(key=>key==="inflow")));
      if (sortKey!=="main"&&sortKey!=="chg") setSortKey("main");
    } else if (range==="20d") {
      setFilters(current=>new Set([...current].filter(key=>key!=="ratio5"&&key!=="rising")));
      if (sortKey==="chg"||sortKey==="ratio") setSortKey("main");
    }
  }
  const resource = useResource(async signal=>{
    const fresh=freshRead.current===scope;freshRead.current=null;
    return checkedFlow(await getBoardFundFlow(kind,range,{signal,...(fresh?{fresh:true}:{})}),kind,range);
  }, {key:scope,intervalMs:30_000,enabled:!paused,abortable:true});
  const mismatch = resource.error instanceof BoardIdentityError;
  const payload = mismatch || paused ? undefined : resource.data;
  const pending = resource.pending;
  const sourceRows = useMemo(()=>payload?.available ? payload.rows : [],[payload]);
  const drawer = useMemo(()=>{
    const row=sourceRows.find(item=>item.board_code===selectedCode);
    return row ? {row,scope,kind,range,asOf:payload?.historical?payload.as_of??"未知":undefined} : null;
  },[sourceRows,selectedCode,scope,kind,range,payload]);
  const drawerPresence = useExitPresence(drawer);

  const rows = useMemo(() => {
    const term=query.trim().toLocaleLowerCase();
    const filtered = sourceRows.filter(r=>!term || `${r.board_code} ${r.name}`.toLocaleLowerCase().includes(term)).filter(r=>{
      if (filters.has("inflow") && !((rangeVal(r,range)??-Infinity)>0)) return false;
      if (filters.has("ratio5") && !((r.main_net_ratio??-Infinity)>5)) return false;
      if (filters.has("streak3") && !((r.streak??-Infinity)>=3)) return false;
      if (filters.has("rising") && !((r.rank_delta??-Infinity)>0)) return false;
      return true;
    });
    const val = (r:BoardFlowRow) => sortKey==="chg"?r.change_pct:sortKey==="ratio"?r.main_net_ratio:sortKey==="streak"?r.streak:rangeVal(r,range);
    return [...filtered].sort((a,b)=>{
      const va=val(a),vb=val(b);
      if (va==null) return vb==null?a.board_code.localeCompare(b.board_code):1;
      if (vb==null) return -1;
      return vb-va || a.board_code.localeCompare(b.board_code);
    });
  },[sourceRows,filters,sortKey,range,query]);
  const incremental = useIncremental(rows,{pageSize:PAGE_SIZE,step:PAGE_STEP,resetKey:JSON.stringify([kind,range,query,sortKey,[...filters]]),active:!paused});
  const navigate=(changes:Record<string,string|null>)=>{
    const next=new URLSearchParams(params);
    for(const [key,value] of Object.entries(changes)) value===null?next.delete(key):next.set(key,value);
    surface.replaceSearch(next);
  };
  function changeRange(next:BoardFlowRange) {
    navigate({board_range:next,board_code:null});
  }
  function toggleFilter(key:string) {setFilters(current=>{const next=new Set(current);next.has(key)?next.delete(key):next.add(key);return next;});}
  const refresh=()=>{freshRead.current=scope;resource.refresh();};
  const sortOptions = limited ? SORT_CHIPS.filter(item=>item.key==="main"||item.key==="chg") : range==="20d"?SORT_CHIPS.filter(item=>item.key==="main"||item.key==="streak"):SORT_CHIPS;

  return <Panel title="板块资金流" className="min-h-0 shrink-0 overflow-hidden" bodyClassName="overflow-hidden"
    extra={<span className="text-[10px] text-zinc-600 dark:text-zinc-400">{website?"东财官网全集":range==="changes"?"东财异动统计":payload?.historical?`存档截至 ${dateTimeTextBJ(payload.as_of,{seconds:true})}`:payload?.updated_at?`更新 ${dateTimeTextBJ(payload.updated_at,{seconds:true})}`:"--"}
      {payload?.total_boards!=null?` · 共 ${payload.total_boards} 个板块`:""}{typeof payload?.coverage==="number"?` · 沉淀 ${payload.coverage} 板块`:""}</span>}>
    <div className="flex h-full min-h-0 flex-col px-4 py-2.5">
      {paused ? <div role="alert" className="space-y-2 py-4 text-xs text-amber-800 dark:text-amber-300">
        <p>旧资金方向已并入本页。原来源或代码不能直接转换为东财BK，未请求板块资金。</p>
        <p>原来源 {params.get("legacy_source")??"未记录"} · 原代码 {params.get("legacy_code")??(selectedCode||"未记录")} · 原类别 {params.get("legacy_kind")??requestedKind??"未记录"} · 原范围 {params.get("legacy_range")??requestedRange??"未记录"}</p>
        <button className="quiet-action" onClick={()=>navigate({board_notice:null,legacy_source:null,legacy_code:null,legacy_range:null,legacy_kind:null,board_code:null,board_range:"website",board_kind:kind})}>查看东财官网板块</button>
      </div> : <>
        {resource.error!=null && <p role="alert" className="text-xs text-amber-800 dark:text-amber-300">{mismatch?"板块返回身份或覆盖不符，已停止展示。":`板块资金读取失败。${payload?"以下为同范围上次结果。":""}`}<button className="quiet-action ml-2" disabled={pending} onClick={refresh}>重试读取</button></p>}
        <div className="board-flow-controls flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5">
          <FilterMenu label="板块" value={kind} options={KIND_CHIPS} onChange={next=>navigate({board_kind:next,board_code:null})}/>
          <FilterMenu label="区间" value={range} options={RANGE_CHIPS} onChange={changeRange}/>
          <FilterMenu label="排序" value={sortKey} options={sortOptions} onChange={setSortKey}/>
          <div className="flow-control-group" title="多选叠加筛选">
            <button aria-pressed={filters.has("inflow")} onClick={()=>toggleFilter("inflow")} className={`${CHIP} ${filters.has("inflow")?CHIP_ON:CHIP_OFF}`}>净流入&gt;0</button>
            <button disabled={range==="20d"||limited} aria-pressed={filters.has("ratio5")} onClick={()=>toggleFilter("ratio5")} className={`${CHIP} ${filters.has("ratio5")?CHIP_ON:CHIP_OFF}`}>主力占比&gt;5%</button>
            <button disabled={limited} aria-pressed={filters.has("streak3")} onClick={()=>toggleFilter("streak3")} className={`${CHIP} ${filters.has("streak3")?CHIP_ON:CHIP_OFF}`}>连续流入≥3</button>
            <button disabled={range==="20d"||limited} aria-pressed={filters.has("rising")} onClick={()=>toggleFilter("rising")} className={`${CHIP} ${filters.has("rising")?CHIP_ON:CHIP_OFF}`}>排名上升</button>
          </div>
          <label className="text-[11px]">查找板块<input type="search" value={query} placeholder="名称 / BK代码" onChange={event=>setQuery(event.target.value)} className="ml-1 min-h-7 w-32 rounded border border-zinc-300 bg-transparent px-1.5 dark:border-zinc-700"/></label>
          <button className="quiet-action" disabled={pending} onClick={refresh}>{pending?"读取中…":"刷新板块"}</button>
        </div>
        {website && <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-zinc-600 dark:text-zinc-400">
          <span>{kind==="industry"?"行业":"概念"} · {payload?.available?`${resource.error?"上次覆盖":"覆盖"} ${payload.source_returned}/${payload.source_total} · 本次来源范围已读全`:"覆盖待核"}</span>
          <span className="text-amber-800 dark:text-amber-300">资金源钟未提供 · 即时状态未知</span><span>接收 {clockText(payload?.received_at)}</span>
          <span>最新板块报价钟 {clockText(payload?.quote_source_as_of)}</span>
          <span>东财正式BK身份；下钻同源完整成员。金额为亿元，源元÷1亿；概念可重叠，不跨板块相加。</span>
        </div>}
        {range==="changes" && <p className="mt-1.5 text-[10px] text-zinc-600 dark:text-zinc-400">独立异动统计 · 来源批次 {clockText(payload?.source_as_of)} · 原批 {payload?.source_returned??"—"}/{payload?.source_total??"—"} · 正式目录匹配 {sourceRows.length}/{payload?.directory_total??"—"} · 逐行时点未核，不作为原f62恢复。</p>}
        {range==="latest_saved" && <p className="mt-1.5 text-[10px] text-zinc-600 dark:text-zinc-400">最近已存单日Top50样本 · 原日 {payload?.as_of??"未提供"} · 不能替代即时资金或全部目录。</p>}
        {!!payload?.degraded.length && (website?<details className="mt-1.5 shrink-0 text-[10px] text-amber-800 dark:text-amber-300"><summary className="cursor-pointer">来源详情 · {payload.degraded.length}项记录</summary><p>{payload.degraded.join("；")}</p></details>:<p className="mt-1.5 shrink-0 text-[10px] text-amber-800 dark:text-amber-500">⚠ {payload.degraded.join("；")}</p>)}
        {pending&&!payload?<div className="mt-2 space-y-1.5">{Array.from({length:8},(_,index)=><Skeleton key={index} className="h-6 w-full"/>)}</div>
          :payload&&!payload.available?<div className="board-flow-unavailable py-8 text-center text-sm text-zinc-600 dark:text-zinc-400"><p>板块资金流暂不可用（{payload.reason??"数据源异常"}），下一拍自动重试。</p>
            {range!=="20d"&&<button className="quiet-action" onClick={()=>changeRange("20d")}>查看本地 20 日存档</button>}</div>
          :!payload?<p className="py-8 text-center text-sm text-zinc-600 dark:text-zinc-400">板块资金流加载失败（后端不可达或版本未更新），下一拍自动重试。</p>
          :<div className="board-flow-list mt-2 min-h-0 flex-1 overflow-y-auto" data-testid="board-flow-table" role="region" aria-label="板块资金列表" tabIndex={0}>
            {selectedCode&&!drawer&&<p role="status" className="py-2 text-xs text-amber-800 dark:text-amber-300">所选 {selectedCode} 不在本次来源类别范围，未按名称替换。</p>}
            {incremental.shown.length===0?<p className="py-10 text-center text-xs text-zinc-600 dark:text-zinc-400">当前筛选无匹配板块</p>:<MasonryColumns gap="gap-3">{incremental.shown.map(row=><BoardCard key={row.board_code} row={row} range={range} onClick={()=>navigate({board_code:row.board_code})}/>)}</MasonryColumns>}
            <IncrementalSentinel sentinelRef={incremental.sentinelRef} visible={Math.min(incremental.visible,rows.length)} total={rows.length} unit="个板块" testId="board-flow-sentinel" onLoadMore={incremental.loadMore}/>
            <p className="mt-1 text-[10px] text-zinc-600 dark:text-zinc-400">{website?"点卡查看同源完整成员；资金源日期未知，报价钟分别保留。":"点卡看板块下钻（分钟累计 + 日度柱 + 成员排行，独立读取）。净流入为东财官方板块口径（非成分股相加）；连续流入/20日区间来自收盘落盘沉淀。"}</p>
          </div>}
      </>}
    </div>
    {drawerPresence.value && !paused && (drawerPresence.value.range==="website"?<WebsiteBoardFlowDrawer key={`${drawerPresence.value.scope}/${drawerPresence.value.row.board_code}`} row={drawerPresence.value.row} kind={drawerPresence.value.kind} open={drawerPresence.active} onClose={()=>navigate({board_code:null})}/>
      :<LegacyBoardFlowDrawer key={`${drawerPresence.value.scope}/${drawerPresence.value.row.board_code}`} row={drawerPresence.value.row} asOf={drawerPresence.value.asOf} open={drawerPresence.active} onClose={()=>navigate({board_code:null})}/>)}
  </Panel>;
}
