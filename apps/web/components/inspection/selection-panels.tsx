"use client";

import { useState } from "react";
import { useResource } from "@/hooks/use-resource";
import { ApiError, getIntradayOpportunities, getIntradayTop, getMorningBriefToday, getPickReviews, getTodayPicks, getWatcherState, type DailyPickItem } from "@/lib/api";
import { DailyReviews } from "@/components/hunting/pick-sections";
import { PickCard, fromDailyPick, fromIntradayStock } from "@/components/picks/pick-card";
import { PickDetailModal } from "@/components/picks/pick-detail-modal";
import { OpportunityEvidencePanel } from "@/components/hunting/opportunity-evidence-panel";
import { WatchLedgerPanel } from "@/components/hunting/watch-ledger-panel";
import { LeaderResearchPanel } from "@/components/hunting/leader-research-panel";
import { ReviewTab } from "@/components/research/review-tab";
import { AlertItem, ClimateBlock, DailyPlanBlock, DirectionCard, EnvStrip, MacroCalendar, MarketOverviewStrip, OpportunitySection, OvernightBiasBlock, WatcherPanel } from "@/components/hunting/intraday-sections";
import { PostMarketEnhance } from "@/components/hunting/post-market-enhance";
import { bjToday } from "@/lib/market-hours";
import type { InspectionRequest } from "./inspection-context";

/** An original message's date/version is never substituted by today's result. */
export function SelectionReviewPanel({ request }: { request: Extract<InspectionRequest, {kind: "selection-review"}> }) {
  const date = request.date.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3");
  const result = useResource(() => getPickReviews(request.date, request.version), {
    key: `${request.date}/${request.version}`, intervalMs: null, marketHours: false,
  });
  const records = result.data?.filter(row => row.date === date && row.selection_version === request.version);
  const mismatch = result.data && records?.length !== result.data.length;
  return <div className="space-y-3">
    <p className="text-xs text-zinc-600 dark:text-zinc-400">原入选日 {request.date} · 版本 <span className="break-all">{request.version}</span></p>
    <p className="text-xs text-zinc-600 dark:text-zinc-400">仅看此版本的价格观察；加入参考价不等于成交价，不构成买卖建议。</p>
    {!!result.error && <p role="alert">原版本复盘读取失败，当前结果未知。<button className="quiet-action" onClick={result.refresh}>重试</button></p>}
    {mismatch && <p role="alert">响应含其他日期或版本，已排除，不能代替原记录。</p>}
    {result.pending && !records ? <p role="status">读取原版本复盘…</p> : records && (records.length ? <DailyReviews reviews={records} /> : <p role="status">此版本暂无复盘记录，未替换成当前版本。</p>)}
  </div>;
}

export function SelectionPreviewPanel({ request }: { request: Extract<InspectionRequest, {kind: "selection-preview"}> }) {
  if (request.view === "evidence" || request.view === "research") return <DatedSelectionPreview key={`${request.view}:${request.date ?? "today"}`} request={request} />;
  if (request.view === "tracking") return <WatchLedgerPanel date={request.date} readOnly />;
  if (request.view === "review") return <CurrentReviews />;
  const date = request.date?.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3");
  if (date && date !== bjToday()) return <p role="status">此入口只提供当日条件，未以今天的数据代替 {date}。历史记录请核对原版本复盘或时点证据。</p>;
  if (request.section === "brief" || request.section === "reminders") return <BriefPreview section={request.section} />;
  if (request.section === "watcher") return <WatcherPreview />;
  if (request.section === "opportunity" || request.section === "overview") return <OpportunityPreview overview={request.section === "overview"} />;
  if (request.section === "postmarket") return <PostMarketEnhance initiallyOpen />;
  return <CurrentSelection theme={request.theme} dailyOnly={request.section === "daily"} intradayOnly={request.section === "candidates"} />;
}

function DatedSelectionPreview({request}: {request: Extract<InspectionRequest, {kind: "selection-preview"}>}) {
  const [date, setDate] = useState(request.date?.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3"));
  return request.view === "evidence" ? <OpportunityEvidencePanel date={date} onDateChange={setDate} /> : <LeaderResearchPanel date={date} onDateChange={setDate} />;
}

function BriefPreview({section}: {section: "brief" | "reminders"}) {
  const result = useResource(async () => {
    try { return await getMorningBriefToday(); }
    catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
  }, {intervalMs: 60_000});
  const brief = result.data;
  if (result.error) return <p role="alert">简报读取失败，不能确认该范围。<button className="quiet-action" onClick={result.refresh}>重试</button></p>;
  if (result.pending && brief === undefined) return <p role="status">读取{section === "brief" ? "盘前简报" : "盘中提醒"}…</p>;
  if (!brief) return <p role="status">今日尚无盘前简报，未启动生成或监测。</p>;
  if (brief.brief_date !== bjToday()) return <p role="status">收到 {brief.brief_date} 的简报，不能代替今日记录。</p>;
  if (section === "reminders") return <div className="space-y-3"><p className="text-xs text-zinc-400">简报日 {brief.brief_date} · 原归档提醒，仅供核对，不重复推送。</p>{brief.alerts.length ? brief.alerts.map((alert, index) => <AlertItem key={alert.key || `${alert.symbol}:${index}`} a={alert} />) : <p>今日简报暂无已归档提醒。</p>}</div>;
  return <div className="space-y-4"><p className="text-xs text-zinc-400">简报日 {brief.brief_date}</p><EnvStrip brief={brief} /><MacroCalendar brief={brief} /><OvernightBiasBlock bias={brief.overnight_bias} /><ClimateBlock climate={brief.climate} /><DailyPlanBlock plan={brief.daily_plan} /><div className="grid gap-3 lg:grid-cols-2">{brief.directions.map(direction => <DirectionCard key={direction.direction} d={direction} />)}</div></div>;
}

function WatcherPreview() {
  const result = useResource(getWatcherState, {intervalMs: 60_000});
  return <div className="space-y-3">{result.error ? <p role="alert">监测状态读取失败。<button className="quiet-action" onClick={result.refresh}>重试</button></p> : <WatcherPanel watcher={result.data ?? null} pending={result.pending} />}</div>;
}

function OpportunityPreview({overview}: {overview: boolean}) {
  const result = useResource(getIntradayOpportunities, {intervalMs: 60_000});
  const [expanded, setExpanded] = useState<string | null>(null);
  if (result.error) return <p role="alert">题材条件读取失败。<button className="quiet-action" onClick={result.refresh}>重试</button></p>;
  if (!result.data) return <p role="status">读取题材条件…</p>;
  if (result.data.trade_date !== bjToday()) return <p role="status">题材数据日 {result.data.trade_date ?? "未知"}，不能作为今日条件。请重试核对来源。</p>;
  return overview ? <MarketOverviewStrip opps={result.data} /> : <OpportunitySection opps={result.data} showLedger={false} expanded={expanded} onToggle={theme => setExpanded(previous => previous === theme ? null : theme)} />;
}

function CurrentReviews() {
  const result = useResource(() => getPickReviews(), {intervalMs: null, marketHours: false});
  return <div className="space-y-3"><p className="text-xs text-zinc-600 dark:text-zinc-400">当前可读复盘，逐条标原日期与版本；不是某条历史消息的原版复盘。</p>
    {!!result.error && <p role="alert">复盘读取失败。<button onClick={result.refresh}>重试</button></p>}
    {result.pending && !result.data ? <p role="status">读取复盘…</p> : result.data && (result.data.length ? <DailyReviews reviews={result.data} /> : <p>尚无可读复盘。</p>)}
  </div>;
}

function CurrentSelection({ theme, dailyOnly = false, intradayOnly = false }: { theme?: string; dailyOnly?: boolean; intradayOnly?: boolean }) {
  // Independent owners: a slow/failed daily generation must not block intraday cards.
  const daily = useResource(getTodayPicks, {intervalMs: 60_000, enabled: !intradayOnly});
  const intraday = useResource(getIntradayTop, {intervalMs: 60_000, enabled: !dailyOnly});
  const [detail, setDetail] = useState<DailyPickItem | null>(null);
  const items = daily.data?.date === bjToday() && !daily.data.stale ? daily.data.items : [];
  const top = intraday.data?.trade_date === bjToday() ? intraday.data.items : [];
  const dailyBySymbol = new Map(items.map(item => [item.symbol, item]));
  const references = intraday.data?.trade_date === bjToday() ? intraday.data.reference_items ?? [] : [];
  const all = new Set<string>();
  const cards = [...top.map(fromIntradayStock), ...items.filter(item => !top.some(row => row.symbol === item.symbol)).map(fromDailyPick)]
    .filter(card => (!theme || card.theme === theme || card.themeSources?.some(source => source.theme === theme)) && !all.has(card.symbol) && !!all.add(card.symbol));
  return <div className="space-y-3">
    <p className="text-xs text-zinc-600 dark:text-zinc-400">当前入选条件旁览{theme ? ` · ${theme}` : ""}。首次加入记录用于复盘；观察或已封板不代表可执行，不构成买卖建议。</p>
    {(daily.data?.stale || (daily.data?.date && daily.data.date !== bjToday())) && <p role="status">每日组合日 {daily.data?.date ?? "未知"}，未作为今日候选展示。</p>}
    {intraday.data && intraday.data.trade_date !== bjToday() && <p role="status">盘中数据日 {intraday.data.trade_date ?? "未知"}，当前候选无法确认，未当作有效空集。</p>}
    {[['每日组合', daily], ['盘中候选', intraday]] .map(([label, resource]) => {
      const r = resource as typeof daily | typeof intraday;
      return r.error ? <p role="alert" key={String(label)}>{String(label)}读取失败，此来源不能确认。<button className="quiet-action" onClick={r.refresh}>重试</button></p> : null;
    })}
    {cards.length ? <div className="grid gap-3 xl:grid-cols-2">{cards.map(card => <div key={card.symbol} className="min-w-0"><PickCard item={card} />{card.origin === "intraday" && dailyBySymbol.has(card.symbol) && <button className="quiet-action mt-2" onClick={() => setDetail(dailyBySymbol.get(card.symbol)!)}>每日原生成依据</button>}</div>)}</div> : (!intradayOnly && daily.pending) || (!dailyOnly && intraday.pending) ? <p role="status">读取当前候选…</p> : <p>当前范围没有可展示候选；失败或过期的来源未当成有效空集。</p>}
    {!dailyOnly && references.length > 0 && <details><summary className="cursor-pointer py-2 text-xs">已封板 / 仅参考 · {references.length} 只</summary><p className="mb-3 text-xs text-zinc-400">保留原首次加入事实，当前不可参与或状态待核，不代表可成交。</p><div className="grid gap-3 xl:grid-cols-2">{references.map(stock => <PickCard key={stock.symbol} item={fromIntradayStock(stock)} />)}</div></details>}
    <PickDetailModal target={detail ? {kind: "pick", item: detail} : null} onClose={() => setDetail(null)} />
  </div>;
}

export function ReviewReportPanel({ request }: { request: Extract<InspectionRequest, {kind: "review-report"}> }) {
  return <ReviewTab focusDate={request.date} />;
}
